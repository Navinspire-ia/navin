"""Model-message governance for agent runner requests.

This module owns model-facing message shaping and tool-result content normalization.
It may return copied messages or persisted-result placeholders, but it must not
mutate an existing session history list in place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from navin.config.schema import ToolResultClearing
from navin.utils.helpers import (
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    find_legal_message_start,
    maybe_persist_tool_result,
    truncate_text,
)
from navin.utils.runtime import ensure_nonempty_tool_result

if TYPE_CHECKING:
    from navin.providers.base import LLMProvider

SNIP_SAFETY_BUFFER = 1024
# After a hard overflow pass, leave more headroom so the next tool turns do not
# immediately overflow again (better than sitting at 0.85 forever).
INFLIGHT_COMPACT_TARGET_RATIO = 0.70
# read_file is the recovery path for persisted results; exempting it prevents persist->read->persist loops.
TOOL_RESULT_OFFLOAD_EXEMPT_TOOLS = frozenset({"read_file"})
BACKFILL_CONTENT = "[Tool result unavailable - call was interrupted or lost]"
PLACEHOLDER_TEXTS = frozenset({
    "[Previous assistant message omitted.]",
})

# Paths the model actually touched in the dropped turns. Enough to re-open
# files after a snip; not a full parser of every tool payload.
_SNIP_PATH_RE = re.compile(
    r"(?:^|[\s\"'`=])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,8})",
)
_SNIP_BRIEF_MAX_FILES = 8
_SNIP_BRIEF_MAX_ERRORS = 3
_SNIP_BRIEF_MAX_CHARS = 900


def deterministic_snip_brief(dropped: list[dict[str, Any]]) -> str:
    """A mid-turn stand-in for the LLM handoff that cannot run here.

    The dropped turns still exist on disk; this only names what the model
    must re-read so it does not invent files, test results or a next step
    from a window that is gone.
    """
    files: list[str] = []
    seen_files: set[str] = set()
    errors: list[str] = []
    last_user = ""

    for message in dropped:
        role = str(message.get("role") or "")
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        if role == "user" and text.strip():
            last_user = text.strip().splitlines()[0][:160]
        name = str(message.get("name") or "")
        if role == "tool" and (
            str(message.get("status") or "").lower() == "error"
            or text.lower().startswith("error")
            or "fail" in text.lower()[:80]
        ):
            label = name or "tool"
            snippet = text.strip().replace("\n", " ")[:120]
            if snippet:
                errors.append(f"{label}: {snippet}")
        for match in _SNIP_PATH_RE.finditer(text):
            path = match.group(1)
            if path not in seen_files and len(files) < _SNIP_BRIEF_MAX_FILES:
                seen_files.add(path)
                files.append(path)

    lines = [
        "[Context notice] Older turns of this conversation were snipped "
        "mid-run to fit the context window. Re-read the files and the "
        "board before editing; do not rely on remembered content.",
    ]
    if last_user:
        lines.append(f"Last user ask still in scope: {last_user}")
    if files:
        lines.append("Files touched in the dropped turns: " + ", ".join(files))
    if errors:
        lines.append(
            "Recent tool failures: " + " | ".join(errors[:_SNIP_BRIEF_MAX_ERRORS])
        )
    brief = "\n".join(lines)
    if len(brief) > _SNIP_BRIEF_MAX_CHARS:
        return brief[:_SNIP_BRIEF_MAX_CHARS].rstrip() + "…"
    return brief


def _tool_call_name_is_valid(tool_call: Any) -> bool:
    """Whether a persisted OpenAI-style tool_call carries a usable name.

    Mirrors ``ToolCallRequest.has_valid_name`` for the dict shape stored in
    message history: a degenerate call with ``name=None`` / ``""`` cannot be
    executed and is rejected by upstream APIs if replayed.
    """
    if not isinstance(tool_call, dict):
        return False
    fn = tool_call.get("function")
    name = fn.get("name") if isinstance(fn, dict) else tool_call.get("name")
    return isinstance(name, str) and bool(name)


@dataclass(slots=True)
class ContextGovernanceConfig:
    provider: LLMProvider
    model: str
    tools: Any
    workspace: Path | None
    session_key: str | None
    max_tool_result_chars: int
    context_window_tokens: int | None = None
    context_block_limit: int | None = None
    max_tokens: int | None = None
    inflight_start_index: int = 0
    clearing: ToolResultClearing = field(default_factory=ToolResultClearing)


# Identity-keyed memo of full-prompt estimates: (messages_object, (tokens, source)).
# Holding the object reference keeps it alive, so an id can never be recycled
# into a false hit while the memo is in scope.
_EstimateMemo = list[tuple[list[dict[str, Any]], tuple[int, str]]]


class ContextGovernor:
    """Prepare model-copy messages while preserving persisted history."""

    def prepare_for_model(
        self,
        config: ContextGovernanceConfig,
        messages: list[dict[str, Any]],
        compacted_tool_call_ids: set[str],
    ) -> list[dict[str, Any]]:
        updated = self.strip_placeholder_assistant_messages(messages)
        updated = self.strip_malformed_tool_calls(updated)
        updated = self.drop_orphan_tool_results(updated)
        updated = self.backfill_missing_tool_results(updated)
        updated = self.clear_stale_turn_tool_results(config, updated)
        updated = self.apply_tool_result_budget(config, updated)
        # In the common under-budget case, compact and snip estimate the very
        # same list; the memo halves the tokenizer work of every iteration.
        memo: _EstimateMemo = []
        updated = self.compact_inflight_overflow(
            config, updated, compacted_tool_call_ids, _estimate_memo=memo
        )
        updated = self.snip_history(config, updated, _estimate_memo=memo)
        updated = self.drop_orphan_tool_results(updated)
        return self.backfill_missing_tool_results(updated)

    @staticmethod
    def _estimate_with_memo(
        config: ContextGovernanceConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        memo: _EstimateMemo | None,
    ) -> tuple[int, str]:
        if memo is not None:
            for obj, cached in memo:
                if obj is messages:
                    return cached
        result = estimate_prompt_tokens_chain(
            config.provider,
            config.model,
            messages,
            tools,
        )
        if memo is not None:
            memo.append((messages, result))
        return result

    @staticmethod
    def input_budget(config: ContextGovernanceConfig) -> int:
        if not config.context_window_tokens:
            return 0

        provider_max_tokens = getattr(
            getattr(config.provider, "generation", None),
            "max_tokens",
            4096,
        )
        max_output = config.max_tokens if isinstance(config.max_tokens, int) else (
            provider_max_tokens if isinstance(provider_max_tokens, int) else 4096
        )
        budget = config.context_block_limit or (
            config.context_window_tokens - max_output - SNIP_SAFETY_BUFFER
        )
        return budget if budget > 0 else 0

    @staticmethod
    def normalize_tool_result(
        config: ContextGovernanceConfig,
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> Any:
        result = ensure_nonempty_tool_result(tool_name, result)
        if tool_name in TOOL_RESULT_OFFLOAD_EXEMPT_TOOLS:
            return result
        try:
            content = maybe_persist_tool_result(
                config.workspace,
                config.session_key,
                tool_call_id,
                result,
                max_chars=config.max_tool_result_chars,
            )
        except Exception:
            logger.exception(
                "Tool result persist failed for {} in {}; using raw result",
                tool_call_id,
                config.session_key or "default",
            )
            content = result
        if isinstance(content, str) and len(content) > config.max_tool_result_chars:
            return truncate_text(content, config.max_tool_result_chars)
        return content

    @staticmethod
    def strip_placeholder_assistant_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Remove assistant messages that are compaction placeholders.

        Messages like ``[Previous assistant message omitted.]`` carry no useful
        context for the model and can cause it to repeatedly attempt tool calls
        that previously failed, producing malformed responses in a loop.
        Consecutive same-role messages that result from removal are handled
        downstream by the provider's merge-consecutive logic. Only the
        model-facing copy is repaired; the persisted transcript is untouched
        (a copy is returned, or the same list object when nothing changes).
        """
        updated: list[dict[str, Any]] | None = None
        for idx, msg in enumerate(messages):
            if msg.get("role") != "assistant":
                if updated is not None:
                    updated.append(msg)
                continue
            content = msg.get("content", "")
            text = content if isinstance(content, str) else ""
            is_placeholder = text.strip() in PLACEHOLDER_TEXTS
            has_tool_calls = bool(msg.get("tool_calls"))
            if is_placeholder and not has_tool_calls:
                if updated is None:
                    updated = list(messages[:idx])
                logger.debug(
                    "Stripping placeholder assistant message from history: {!r}",
                    text[:60],
                )
                continue
            if updated is not None:
                updated.append(msg)
        if updated is None:
            return messages
        return updated

    @staticmethod
    def strip_malformed_tool_calls(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Drop persisted assistant tool_calls whose name is missing/non-string.

        A degenerate tool call (``name=None`` or ``""``) that slipped into the
        saved history before this guard existed gets replayed on every turn and
        makes upstream APIs reject the whole request
        (``messages.content.N.tool_use.name: Input should be a valid string``),
        permanently wedging the session. Removing the bad call here lets the
        existing orphan-result cleanup drop its now-dangling tool result, so a
        polluted session self-heals on its next turn. The persisted transcript
        is left untouched; only the model-facing copy is repaired (a copy is
        returned, or the same list object when nothing changes).
        """
        updated: list[dict[str, Any]] | None = None
        for idx, msg in enumerate(messages):
            if msg.get("role") != "assistant":
                if updated is not None:
                    updated.append(msg)
                continue
            calls = msg.get("tool_calls")
            if not calls:
                if updated is not None:
                    updated.append(msg)
                continue
            kept = [tc for tc in calls if _tool_call_name_is_valid(tc)]
            if len(kept) == len(calls):
                if updated is not None:
                    updated.append(msg)
                continue
            if updated is None:
                updated = [dict(m) for m in messages[:idx]]
            logger.warning(
                "Stripping {} malformed tool_call(s) with missing/non-string "
                "name from assistant history before request",
                len(calls) - len(kept),
            )
            repaired = dict(msg)
            if kept:
                repaired["tool_calls"] = kept
            else:
                repaired.pop("tool_calls", None)
            # An assistant turn with neither content nor any valid tool call is
            # itself invalid upstream; drop it entirely in that case.
            has_content = bool(repaired.get("content"))
            if not kept and not has_content:
                continue
            updated.append(repaired)

        if updated is None:
            return messages
        return updated

    @staticmethod
    def drop_orphan_tool_results(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Drop tool results that have no matching assistant tool_call earlier in history."""
        declared: set[str] = set()
        updated: list[dict[str, Any]] | None = None
        for idx, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        declared.add(str(tc["id"]))
            if role == "tool":
                tid = msg.get("tool_call_id")
                if tid and str(tid) not in declared:
                    if updated is None:
                        updated = [dict(m) for m in messages[:idx]]
                    continue
            if updated is not None:
                updated.append(dict(msg))

        if updated is None:
            return messages
        return updated

    @staticmethod
    def backfill_missing_tool_results(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Insert synthetic error results for assistant tool_calls with missing tool outputs."""
        declared: list[tuple[int, str, str]] = []
        fulfilled: set[str] = set()
        for idx, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        name = ""
                        func = tc.get("function")
                        if isinstance(func, dict):
                            name = func.get("name", "")
                        declared.append((idx, str(tc["id"]), name))
            elif role == "tool":
                tid = msg.get("tool_call_id")
                if tid:
                    fulfilled.add(str(tid))

        missing = [(ai, cid, name) for ai, cid, name in declared if cid not in fulfilled]
        if not missing:
            return messages

        updated = list(messages)
        offset = 0
        for assistant_idx, call_id, name in missing:
            insert_at = assistant_idx + 1 + offset
            while insert_at < len(updated) and updated[insert_at].get("role") == "tool":
                insert_at += 1
            updated.insert(insert_at, {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": BACKFILL_CONTENT,
            })
            offset += 1
        return updated

    def apply_tool_result_budget(
        self,
        config: ContextGovernanceConfig,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        updated = messages
        for idx, message in enumerate(messages):
            if message.get("role") != "tool":
                continue
            normalized = self.normalize_tool_result(
                config,
                str(message.get("tool_call_id") or f"tool_{idx}"),
                str(message.get("name") or "tool"),
                message.get("content"),
            )
            if normalized != message.get("content"):
                if updated is messages:
                    updated = [dict(m) for m in messages]
                updated[idx]["content"] = normalized
        return updated

    @staticmethod
    def _stale_summary_for(message: dict[str, Any]) -> str:
        name = message.get("name", "tool")
        return (
            f"[Prior {name} result from an earlier turn was cleared from the "
            f"replayed context; the call completed. Re-run {name} if its "
            "output is needed again.]"
        )

    def clear_stale_turn_tool_results(
        self,
        config: ContextGovernanceConfig,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Blank bulky tool results from turns older than the last N user turns.

        This runs on every request, independent of context pressure: replayed
        tool dumps from finished turns are the main source of dead input
        tokens, and the model can always re-run the tool. The rule is
        deterministic and monotone - once a result ages past the boundary it
        renders as the same stub on every later turn, so the prompt prefix in
        front of it never changes again and the provider prompt cache stays
        warm there. Each new turn only re-caches the region of the turn that
        just aged out.
        """
        policy = config.clearing
        stale_turns = int(getattr(policy, "stale_after_user_turns", 0) or 0)
        if stale_turns <= 0:
            return messages
        user_indexes = [
            i for i, m in enumerate(messages) if m.get("role") == "user"
        ]
        # Keep the current turn plus the last `stale_turns` completed turns.
        if len(user_indexes) <= stale_turns:
            return messages
        cutoff = user_indexes[-(stale_turns + 1)]

        excluded = frozenset(policy.exclude_tools)
        updated: list[dict[str, Any]] | None = None
        for idx, msg in enumerate(messages):
            if idx >= cutoff:
                break
            if msg.get("role") != "tool" or msg.get("name") in excluded:
                continue
            content = msg.get("content")
            if not isinstance(content, str) or len(content) < policy.min_chars:
                continue
            summary = self._stale_summary_for(msg)
            if content == summary:
                continue
            if updated is None:
                updated = [dict(m) for m in messages]
            updated[idx]["content"] = summary
        if updated is None:
            return messages
        logger.debug(
            "Cleared stale prior-turn tool results for {} (cutoff idx {})",
            config.session_key or "default",
            cutoff,
        )
        return updated

    def compact_inflight_overflow(
        self,
        config: ContextGovernanceConfig,
        messages: list[dict[str, Any]],
        compacted_tool_call_ids: set[str],
        *,
        _estimate_memo: _EstimateMemo | None = None,
    ) -> list[dict[str, Any]]:
        """Compact in-flight tool results on soft pressure or hard overflow."""
        budget = self.input_budget(config)
        if budget <= 0:
            return messages

        tools = config.tools.get_definitions()
        updated = self._apply_recorded_compactions(messages, compacted_tool_call_ids)
        estimate, source = self._estimate_with_memo(
            config, updated, tools, _estimate_memo
        )
        soft_ratio = float(getattr(config.clearing, "soft_clear_ratio", 0.0) or 0.0)
        soft_limit = int(budget * soft_ratio) if soft_ratio > 0 else 0
        hard_overflow = estimate > budget
        if not hard_overflow and (soft_limit <= 0 or estimate <= soft_limit):
            return updated

        target = (
            int(budget * INFLIGHT_COMPACT_TARGET_RATIO)
            if hard_overflow
            else soft_limit
        )
        candidates = self._inflight_compaction_candidates(
            config,
            updated,
            compacted_tool_call_ids,
            allow_recent_fallback=hard_overflow,
        )
        if not candidates:
            return updated

        # The loop below mutates ``updated`` in place, so any estimate the
        # memo holds for that object is about to describe stale contents.
        if _estimate_memo is not None:
            _estimate_memo[:] = [
                entry for entry in _estimate_memo if entry[0] is not updated
            ]

        # Stopping the moment the target is met leaves the prompt sitting just under
        # it, so the next turn overflows again and rewrites the prefix again, and the
        # provider's cache is cold on every single turn of a long run. clear_at_least
        # buys several turns of headroom for the one break already being paid for.
        start_estimate = estimate
        floor = config.clearing.clear_at_least
        for candidate_idx, (idx, tool_call_id) in enumerate(candidates):
            is_newest_candidate = candidate_idx == len(candidates) - 1
            if is_newest_candidate and estimate <= budget and hard_overflow:
                break
            if is_newest_candidate and not hard_overflow:
                # Soft clear never touches the newest reclaimable result.
                break
            if tool_call_id in compacted_tool_call_ids:
                continue
            if updated is messages:
                updated = [dict(m) for m in messages]
            compacted_tool_call_ids.add(tool_call_id)
            if source == "tiktoken":
                # Only this one message changed, so re-encoding the whole
                # prompt (150k tokens, once per blanked result, every step)
                # is replaced by the difference on the message itself.
                before = estimate_message_tokens(updated[idx])
                self._compact_tool_result_at(updated, idx)
                estimate = max(0, estimate - before + estimate_message_tokens(updated[idx]))
            else:
                # A provider counter has its own scale; keep asking it.
                self._compact_tool_result_at(updated, idx)
                estimate, source = estimate_prompt_tokens_chain(
                    config.provider,
                    config.model,
                    updated,
                    tools,
                )
            if estimate <= target and (
                floor <= 0 or start_estimate - estimate >= floor or not hard_overflow
            ):
                break

        logger.debug(
            "In-flight context compaction for {}: prompt={} budget={} target={} "
            "freed={} via {}, hard={}, ids={}",
            config.session_key or "default",
            estimate,
            budget,
            target,
            start_estimate - estimate,
            source,
            hard_overflow,
            len(compacted_tool_call_ids),
        )
        if _estimate_memo is not None:
            _estimate_memo.append((updated, (estimate, source)))
        return updated

    def snip_history(
        self,
        config: ContextGovernanceConfig,
        messages: list[dict[str, Any]],
        *,
        _estimate_memo: _EstimateMemo | None = None,
    ) -> list[dict[str, Any]]:
        if not messages or not config.context_window_tokens:
            return messages

        budget = self.input_budget(config)
        if budget <= 0:
            return messages

        tools = config.tools.get_definitions()
        estimate, _ = self._estimate_with_memo(
            config, messages, tools, _estimate_memo
        )
        if estimate <= budget:
            return messages

        system_messages = [dict(msg) for msg in messages if msg.get("role") == "system"]
        non_system = [dict(msg) for msg in messages if msg.get("role") != "system"]
        if not non_system:
            return messages

        system_tokens = sum(estimate_message_tokens(msg) for msg in system_messages)
        fixed_tokens, _ = estimate_prompt_tokens_chain(
            config.provider,
            config.model,
            system_messages,
            tools,
        )
        remaining_budget = max(0, budget - max(system_tokens, fixed_tokens))
        kept: list[dict[str, Any]] = []
        kept_tokens = 0
        for message in reversed(non_system):
            msg_tokens = estimate_message_tokens(message)
            if kept and kept_tokens + msg_tokens > remaining_budget:
                break
            kept.append(message)
            kept_tokens += msg_tokens
        kept.reverse()

        tail = self._legal_history_tail(kept, non_system)
        if len(tail) < len(non_system):
            # No LLM handoff mid-turn (that would stall the request). A
            # deterministic brief of what just dropped is still cheap and
            # stops the model from inventing files, tests and decisions that
            # lived only in the snipped turns.
            dropped = non_system[: len(non_system) - len(tail)]
            system_messages.append({
                "role": "system",
                "content": deterministic_snip_brief(dropped),
            })
        return system_messages + tail

    @staticmethod
    def _summary_for(message: dict[str, Any]) -> str:
        name = message.get("name", "tool")
        return f"[Prior {name} result compacted to fit context; the tool call already completed.]"

    def _legal_history_tail(
        self,
        kept: list[dict[str, Any]],
        non_system: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        fallback = kept if kept else (non_system[-1:] if non_system else [])
        kept = self._user_tail(kept) or self._user_tail(non_system, last=True) or fallback

        start = find_legal_message_start(kept)
        return kept[start:] if start else kept

    @staticmethod
    def _user_tail(messages: list[dict[str, Any]], *, last: bool = False) -> list[dict[str, Any]]:
        indexes = range(len(messages) - 1, -1, -1) if last else range(len(messages))
        for idx in indexes:
            if messages[idx].get("role") == "user":
                return messages[idx:]
        return []

    def _apply_recorded_compactions(
        self,
        messages: list[dict[str, Any]],
        compacted_tool_call_ids: set[str],
    ) -> list[dict[str, Any]]:
        if not compacted_tool_call_ids:
            return messages
        updated = messages
        for idx, msg in enumerate(messages):
            if msg.get("role") != "tool":
                continue
            tool_call_id = msg.get("tool_call_id")
            if not tool_call_id or str(tool_call_id) not in compacted_tool_call_ids:
                continue
            summary = self._summary_for(msg)
            if msg.get("content") == summary:
                continue
            if updated is messages:
                updated = [dict(m) for m in messages]
            updated[idx]["content"] = summary
        return updated

    def _inflight_compaction_candidates(
        self,
        config: ContextGovernanceConfig,
        messages: list[dict[str, Any]],
        compacted_tool_call_ids: set[str],
        *,
        allow_recent_fallback: bool = True,
    ) -> list[tuple[int, str]]:
        """Blankable tool results, stalest first.

        Eligibility is a denylist rather than a roster of known-safe tools: a
        roster silently protects everything it has not heard of, which meant every
        MCP tool result stayed in the window no matter how large, and every tool
        added since had to be remembered here to be reclaimable.
        """
        policy = config.clearing
        excluded = frozenset(policy.exclude_tools)
        compactable: list[tuple[int, str]] = []
        for idx, msg in enumerate(messages):
            if idx < config.inflight_start_index:
                continue
            if msg.get("role") != "tool" or msg.get("name") in excluded:
                continue
            tool_call_id = msg.get("tool_call_id")
            if not tool_call_id or str(tool_call_id) in compacted_tool_call_ids:
                continue
            content = msg.get("content")
            if not isinstance(content, str) or len(content) < policy.min_chars:
                continue
            compactable.append((idx, str(tool_call_id)))

        if not compactable:
            return []
        primary_count = max(0, len(compactable) - policy.keep_recent)
        primary = compactable[:primary_count]
        if not allow_recent_fallback:
            return primary
        # Hard overflow beats the keep-recent preference. Return recent results
        # after stale ones so the newest result is naturally last.
        fallback = compactable[primary_count:]
        return primary + fallback

    def _compact_tool_result_at(self, messages: list[dict[str, Any]], idx: int) -> None:
        messages[idx]["content"] = self._summary_for(messages[idx])
