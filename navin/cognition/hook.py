"""Per-turn hook that journals a finished turn when the project opted in.

``create_episode_journal_hook`` is a turn hook factory (see
``navin.agent.turn_hooks``). It is called once per turn and answers ``None``
whenever the journal must stay silent: no project path, ephemeral turn
(titles, summaries, compaction), the gateway heartbeat, or the flag off.
That path is one ``os.stat`` and no allocation beyond the call itself.

When it does return a hook, the hook inherits every no-op from
``AgentHook`` and only implements ``after_run`` / ``on_error``, where it
hands a compact record to the background writer and returns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.agent.hook import AgentHook, AgentRunHookContext, AgentTurnHookContext
from navin.agent.tools.context import current_request_context
from navin.cognition.episodes import append_episode, build_episode, last_user_text
from navin.cognition.settings import cognition_enabled

_HEARTBEAT_KEY = "heartbeat"


def _is_heartbeat(context: AgentTurnHookContext) -> bool:
    if (context.session_key or "").strip().lower() == _HEARTBEAT_KEY:
        return True
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    return bool(metadata.get(_HEARTBEAT_KEY))


class EpisodeJournalHook(AgentHook):
    """Write one episode when the run ends. Never raises into the runner."""

    __slots__ = ("_workspace", "_channel", "_chat_id", "_session_key", "_metadata", "_turn_id")

    def __init__(
        self,
        *,
        workspace: Path,
        channel: str,
        chat_id: str,
        session_key: str | None,
        metadata: dict[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> None:
        super().__init__()
        self._workspace = workspace
        self._channel = channel
        self._chat_id = chat_id
        self._session_key = session_key
        self._metadata = metadata or {}
        self._turn_id = turn_id

    @staticmethod
    def _user_text(context: AgentRunHookContext) -> str:
        # The request snapshot is still bound while after_run executes and
        # carries the user's words before any injection; the message list is
        # the fallback for callers that run the runner without one.
        request = current_request_context()
        original = request.original_user_text if request is not None else None
        if isinstance(original, str) and original.strip():
            return original
        return last_user_text(context.messages)

    def _journal(self, context: AgentRunHookContext) -> None:
        record = build_episode(
            channel=self._channel,
            chat_id=self._chat_id,
            session_key=self._session_key,
            turn_id=self._turn_id,
            metadata=self._metadata,
            user_text=self._user_text(context),
            reply=context.final_content,
            tools_used=context.tools_used,
            stop_reason=context.stop_reason,
            error=context.error,
        )
        if record is not None:
            append_episode(self._workspace, record)

    async def after_run(self, context: AgentRunHookContext) -> None:
        # The runner calls after_run on every non-raising run, including the
        # ones that ended with context.error set; on_error runs first then.
        self._journal(context)

    async def on_error(self, context: AgentRunHookContext) -> None:
        # Only the raising path reaches here without after_run following.
        if context.exception is not None:
            self._journal(context)


def create_episode_journal_hook(context: AgentTurnHookContext) -> AgentHook | None:
    """Turn hook factory: the journal hook, or ``None`` when it must stay off."""
    workspace = context.workspace
    if workspace is None or context.ephemeral or _is_heartbeat(context):
        return None
    if not cognition_enabled(workspace, "episodes"):
        return None
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    turn_id = metadata.get("turn_id")
    return EpisodeJournalHook(
        workspace=Path(workspace),
        channel=context.channel,
        chat_id=context.chat_id,
        session_key=context.session_key,
        metadata=metadata,
        turn_id=turn_id if isinstance(turn_id, str) else None,
    )
