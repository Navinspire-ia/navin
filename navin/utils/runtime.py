# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Runtime-specific helper functions and constants."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from navin.utils.helpers import stringify_text_blocks

_MAX_REPEAT_EXTERNAL_LOOKUPS = 2

# Third same-target workspace violation in a turn escalates to "stop retrying".
_MAX_REPEAT_WORKSPACE_VIOLATIONS = 2

# With soft tool errors (fail_on_tool_error=False) the model self-heals by
# retrying; this bounds how often the *same exact call* may fail before the
# error message escalates to "change approach".
_MAX_IDENTICAL_TOOL_FAILURES = 2
# After this many identical failures, treat the call as a hard stop so a stuck
# model cannot burn the whole turn repeating the same broken arguments.
_HARD_STOP_IDENTICAL_TOOL_FAILURES = 5
# Successful read-only calls are also bounded. Context clearing blanks old
# results and tells the model it can re-run the tool; thinking models then
# spend the whole step budget re-reading the same file (35x observed).
_MAX_IDENTICAL_READONLY_CALLS = 2
_READONLY_SPIN_TOOLS = frozenset(
    {
        "read_file",
        "grep",
        "list_dir",
        "find_files",
        "code_index",
    }
)
# Any other action repeated verbatim: the third identical successful call
# (same tool, same arguments, same answer twice, no edit in between) is
# refused. Polling tools are exempt: asking a running process, a page or a
# teammate for news is not a loop, the answer changes on its own. So are the
# verification tools: the verify gates themselves ask for a re-run, and a
# flaky test can turn green with nothing edited.
_MAX_IDENTICAL_ACTIONS = 2
_ACTION_REPEAT_PREFIX = "action:"
_ACTION_DIGEST_PREFIX = "action:digest:"
_REPEAT_EXEMPT_TOOLS = frozenset(
    {
        "verify",
        "board",
        "lint",
        "test_run",
        "write_stdin",
        "list_exec_sessions",
        "browser",
        "computer",
        "mobile",
        "spawn",
        "team",
        "collab",
        "wait",
    }
)
# Reminders (no progress, exploration, scope) the model may ignore in a row
# before the turn has to answer without tools. No fixed call cap: a turn that
# acts on the reminder keeps all its steps.
IGNORED_NUDGES_BEFORE_FINAL = 2
_MUTATING_RESET_TOOLS = frozenset(
    {
        "apply_patch",
        "edit_file",
        "write_file",
        "manage_files",
    }
)

EMPTY_FINAL_RESPONSE_MESSAGE = (
    "The model returned an empty reply, even after automatic retries, so this "
    "turn has no final answer. Nothing done so far was lost; you "
    "can rephrase the request."
)

# Said in the user's language: the English sentence reached a French chat and
# read as a crash ("I completed the tool steps but couldn't produce a final
# answer" while no tool had even run).
_EMPTY_FINAL_RESPONSE_MESSAGES = {
    "French": (
        "Le modèle a renvoyé une réponse vide, même après les relances "
        "automatiques, donc ce tour n'a pas de réponse finale. Rien n'est "
        "perdu ; vous pouvez reformuler la demande."
    ),
    "Spanish": (
        "El modelo devolvió una respuesta vacía, incluso tras los reintentos "
        "automáticos, así que este turno no tiene respuesta final. No se ha "
        "perdido nada; puedes reformular la petición."
    ),
    "German": (
        "Das Modell hat auch nach den automatischen Wiederholungen eine leere "
        "Antwort geliefert, daher hat dieser Durchgang keine endgültige "
        "Antwort. Es ist nichts verloren; du kannst die Anfrage "
        "umformulieren."
    ),
    "Italian": (
        "Il modello ha restituito una risposta vuota, anche dopo i tentativi "
        "automatici, quindi questo turno non ha una risposta finale. Non è "
        "andato perso nulla; puoi riformulare la "
        "richiesta."
    ),
    "Portuguese": (
        "O modelo devolveu uma resposta vazia, mesmo após as novas tentativas "
        "automáticas, por isso este turno não tem resposta final. Nada se "
        "perdeu; pode reformular o pedido."
    ),
}


def empty_final_response_message(language: str | None = None) -> str:
    """The blank-reply notice in *language* (a reply_language name), else English."""
    return _EMPTY_FINAL_RESPONSE_MESSAGES.get(language or "", EMPTY_FINAL_RESPONSE_MESSAGE)


def session_empty_final_response_message(session_key: str | None) -> str:
    """The blank-reply notice in the language this chat was last written in."""
    if not session_key:
        return EMPTY_FINAL_RESPONSE_MESSAGE
    from navin.agent.reply_language import reply_language

    return empty_final_response_message(reply_language(session_key, None))

TOOL_FAILURE_STOP_MESSAGE = (
    "A step kept failing, so I stopped here. Nothing done so far was lost; "
    "you can try again or rephrase the request."
)

# The user sees one plain sentence; the raw exception stays in the logs.
_TOOL_FAILURE_STOP_MESSAGES = {
    "French": (
        "Une étape a échoué à plusieurs reprises, je me suis donc arrêté ici. "
        "Rien n'est perdu ; vous pouvez réessayer ou reformuler la demande."
    ),
    "Arabic": (
        "فشلت إحدى الخطوات عدة مرات، لذلك توقفت هنا. لم يضع أي شيء مما تم "
        "إنجازه؛ يمكنك المحاولة مرة أخرى أو إعادة صياغة الطلب."
    ),
}


def tool_failure_stop_message(language: str | None = None) -> str:
    """The failing-tool stop notice in *language* (a reply_language name), else English."""
    return _TOOL_FAILURE_STOP_MESSAGES.get(language or "", TOOL_FAILURE_STOP_MESSAGE)


def session_tool_failure_stop_message(session_key: str | None) -> str:
    """The failing-tool stop notice in the language this chat was last written in."""
    if not session_key:
        return TOOL_FAILURE_STOP_MESSAGE
    from navin.agent.reply_language import reply_language

    return tool_failure_stop_message(reply_language(session_key, None))


FINALIZATION_RETRY_PROMPT = (
    "Please provide your response to the user based on the conversation above."
)

# Second chance when the plain finalization prompt still came back blank:
# some models answer an empty turn with an empty turn unless told outright
# that blank output is the failure.
STRICT_FINALIZATION_RETRY_PROMPT = (
    "Your last replies were empty, so the user received nothing. Tools are "
    "disabled now. Write your answer to the user as plain text, in their "
    "language: what you found or did based on the tool results above, what "
    "was verified, and remarks; name a blocker in one line only if one really "
    "stops the work. An empty reply is not allowed."
)


def build_strict_finalization_retry_message() -> dict[str, str]:
    """The last-chance no-tools prompt after a blank finalization reply."""
    return {"role": "user", "content": STRICT_FINALIZATION_RETRY_PROMPT}


BUDGET_EXHAUSTED_FINALIZATION_PROMPT = (
    "The tool-call budget for this turn is exhausted. Based only on the "
    "conversation and tool results above, provide a concise final response to "
    "the user. Do not call or request tools. Do not claim the task is complete "
    "unless the evidence above clearly shows it is complete. Structure the "
    "reply as: (1) what was done - concrete files, commands, and board steps; "
    "(2) what remains; (3) the execution constraint preventing further work. "
    "Keep it scannable and specific - no vague 'I worked on it'."
)

LENGTH_RECOVERY_PROMPT = (
    "Output limit reached. Continue exactly where you left off "
    "- no recap, no apology. Break remaining work into smaller steps if needed."
)

SUSTAINED_GOAL_CONTINUE_PROMPT = (
    "You have an active sustained goal. Please continue working toward the "
    "objective using your tools, or call update_goal with action='complete' "
    "if the work is truly finished. If you are blocked (a missing credential, "
    "a decision only the user can take, the same error again), call "
    "update_goal with action='block' and say what you need instead of retrying."
)

DELIVERY_CONTINUE_PROMPT = (
    "You ended the turn without calling any tools, so no deliverable was "
    "created. This workflow requires tool execution. Continue now: follow the "
    "Active Skills, call the tools needed to produce the real workspace file "
    "(and any required images), and only then reply with the file path and a "
    "short outline. Do not describe what you plan to do - do it."
)

VERIFY_BEFORE_DONE_CONTINUE_PROMPT = (
    "You edited code but have not verified the current result yet. Finish the "
    "accepted development task: add or adapt meaningful tests for the requested "
    "behavior, including the reported bug and relevant failure cases. Use "
    "existing tests when they already cover the change; do not create boilerplate "
    "tests for a reversible text/style edit. Execute the relevant tests after "
    "the last code/test change, then run the appropriate lint/type/build checks "
    "with `verify action=check` or the project's equivalent. Reproduce the user "
    "flow in Preview when relevant. Fix failures, check the acceptance criteria, "
    "then report what changed and the actual results. A discovery call, a test "
    "still running or a result from before the last edit is not validation. "
    "Stay inside the request: do not install test tooling or browsers, do not "
    "edit tests of features you did not touch, and do not chase failures in "
    "files you did not change; name them in one line instead. "
    "Continue without asking the user to resume."
)

VERIFY_FAILED_CONTINUE_PROMPT = (
    "Verification failed (lint errors and/or failing tests). You may not claim "
    "the task is done while checks are red. Fix the reported cause and run "
    "`verify action=fix` (or targeted lint/test fixes), re-run "
    "`verify action=check`, and only then summarize with green evidence. "
    "Keep the patch minimal and in-scope. Keep repairing while making progress; "
    "do not remove assertions, skip failing tests or weaken acceptance criteria "
    "just to get green results. If an external blocker prevents a check, report "
    "the exact blocker and unfinished validation rather than claiming completion. "
    "A failure in code you did not change, or a missing tool or secret, is such "
    "a blocker: report it, do not repair other features or install tooling."
)

NO_PROGRESS_CONTINUE_PROMPT = (
    "Your recent calls only repeated blocked operations or bookkeeping that "
    "made no changes. Use the existing results and continue the unfinished "
    "implementation or validation. Do not repeat unchanged plan updates or "
    "blocked calls. If all work is complete, report the result in chat."
)

NO_PROGRESS_FINALIZATION_PROMPT = (
    "Repeated blocked tool calls or unchanged bookkeeping produced no new "
    "result even after a request to change approach. Explain the loop and remaining "
    "work using the tool evidence above. Do not claim completion or blame a "
    "time limit. Name any concrete input needed to resolve the blocker."
)

NO_PROGRESS_STOP_FALLBACK = (
    "The same blocked tool calls or unchanged bookkeeping kept repeating without a new result. "
    "The task is unfinished; completed work has been preserved."
)

# A build turn that read and searched, then ended on text without editing,
# running or delegating anything: the "I have read everything, next message I
# start writing" answer. The user had already said go.
NO_ACTION_CONTINUE_PROMPT = (
    "You ended the turn without changing any file or running anything: only "
    "reads and searches so far. The user already accepted this task, so do not "
    "end on a plan, a recap of what you read or a promise for the next message. "
    "Stop reading now and start the implementation in this turn: write the "
    "first edit with what you already know, then continue step by step and "
    "validate. Read again only a precise spot you are about to change. If the "
    "request was only a question, or a real blocker needs the user (a missing "
    "input or decision), answer that directly instead."
)

# A build turn that did part of the work and ended on a list of what is left.
REMAINING_WORK_CONTINUE_PROMPT = (
    "Your answer lists work of the accepted request that is still to do. Do not "
    "stop halfway: continue with the remaining steps now, in this turn, and "
    "validate them. Leave a step out only when it is genuinely blocked (a missing "
    "input, a decision only the user can take) or when the user said they will do "
    "it themselves; then say so in one line. When everything is done, give the "
    "final report: what was done, what was verified, and remarks if useful. The "
    "report never ends on a list of work still to do."
)

# Phrases a final answer uses to hand back unfinished work ("what remains",
# "next message I will..."). French and English: the languages this product
# is used in most. Matching only decides whether to send one reminder.
_REMAINING_WORK_MARKERS = re.compile(
    r"ce qui reste|reste [àa] (?:faire|[ée]crire|impl[ée]menter|traiter)"
    r"|il (?:me )?reste\b|[ée]tapes? restantes?|t[âa]ches? restantes?"
    r"|au prochain (?:message|tour)|prochaine [ée]tape|dans un prochain tour"
    r"|pas encore (?:[ée]t[ée] )?(?:modifi|[ée]crit|impl[ée]ment|fait|trait|lanc)"
    r"|aucun fichier n'a (?:encore )?[ée]t[ée] modifi"
    r"|what (?:still )?remains|remaining (?:work|steps|tasks)|still to do|left to do"
    # A "Remaining:" or "Reste :" label before a list of unfinished items.
    r"|\bremaining\s*:|\breste\s*:|\brestant(?:e|s|es)?\s*:"
    r"|next (?:message|turn)|not (?:yet )?(?:been )?(?:implemented|written|done|modified)"
    r"|no files? (?:has|have|was|were) (?:been )?(?:changed|modified|edited)"
    # Seen in real sessions ending mid-plan: "Pas commencé :", "Dis « continue »
    # et j'attaque D1", "je fais cette partie dans un tour dédié".
    r"|pas (?:encore )?commenc[ée]|not (?:yet )?started"
    r"|dis(?:-moi)? [«\"']?\s*continue|say [\"']?continue"
    r"|(?:dans|en) un tour d[ée]di[ée]|in a (?:separate|dedicated) turn"
    # A heading or label opening a line of the report: "**Non traité**",
    # "- Pas corrigé :", "Prochaines étapes", "Next steps:". Only at the start
    # of a line, so a remark like "le cas n'est pas traité par l'API" is not one.
    r"|(?m:^[\s*#>_-]*(?:non trait[ée]|pas (?:encore )?(?:corrig|trait|fait)"
    r"|not (?:yet )?(?:fixed|handled|addressed|done)\b|prochaines? [ée]tapes?\b"
    r"|next steps?\b|[àa] faire ensuite|to do next))",
    re.IGNORECASE,
)


# Words quoted in a remark (« reste à faire », "what remains", `next steps`)
# name the phrase, they do not hand work back.
_QUOTED = re.compile(
    r"«[^»\n]{0,200}»|\u201c[^\u201d\n]{0,200}\u201d|\"[^\"\n]{0,200}\"|`[^`\n]{0,200}`"
)
# "Dis « continue »" hands back with its quotes, so it is read before they go.
_SAY_CONTINUE = re.compile(
    r"dis(?:-moi)? [«\"']?\s*continue|say [\"']?continue", re.IGNORECASE,
)


# Work the answer hands to the user on purpose (they deploy, commit or decide):
# "La mise en ligne reste a faire de ton cote" pushed the agent to go on
# exploring, then to ask the user what they had already said they would do.
_USER_OWNED = re.compile(
    r"de (?:ton|votre) c[ôo]t[ée]|[àa] (?:toi|vous) de\b|c[ôo]t[ée] (?:client|utilisateur)"
    r"|on your (?:side|end)|up to you|you (?:need|have) to|your (?:call|decision)"
    r"|mise en ligne|mettre en ligne|d[ée]ploi|deploy|\bcommit|\bpush\b|\bmerge",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?\n])")


def lists_remaining_work(text: str | None) -> bool:
    """True when a final answer hands back unfinished work of the request.

    A sentence that leaves the step to the user (deploying, committing, a
    decision) is not unfinished work: the user said they would do it.
    """
    if not text:
        return False
    if _SAY_CONTINUE.search(text):
        return True
    for sentence in _SENTENCE_END.split(_QUOTED.sub(" ", text)):
        if _REMAINING_WORK_MARKERS.search(sentence) and not _USER_OWNED.search(sentence):
            return True
    return False


def is_harness_nudge(content: object) -> bool:
    """True for a continuation prompt Navin wrote, not something the user said."""
    if not isinstance(content, str):
        return False
    text = content.lstrip()
    if text.startswith("Wrap-up check:"):
        return True
    return any(
        text.startswith(prompt[:60])
        for prompt in (
            DELIVERY_CONTINUE_PROMPT,
            VERIFY_BEFORE_DONE_CONTINUE_PROMPT,
            VERIFY_FAILED_CONTINUE_PROMPT,
            NO_PROGRESS_CONTINUE_PROMPT,
            NO_ACTION_CONTINUE_PROMPT,
            REMAINING_WORK_CONTINUE_PROMPT,
            SUSTAINED_GOAL_CONTINUE_PROMPT,
        )
    )


def request_used_tools(messages: list[dict[str, Any]]) -> bool:
    """Whether tools already ran for the user's current request.

    Walks back past Navin's own continuation prompts to the user's real
    message: a continuation run starts with an empty tool list, yet the same
    request may have run a dozen tools a minute earlier.
    """
    for message in reversed(messages):
        role = message.get("role")
        if role == "tool" or (role == "assistant" and message.get("tool_calls")):
            return True
        if role == "user" and not is_harness_nudge(message.get("content")):
            return False
    return False


def build_wrap_up_message(look_steps: int) -> dict[str, str]:
    """Stop a turn that keeps reading after its work came back green."""
    return {
        "role": "user",
        "content": (
            f"Wrap-up check: your latest edit is validated (checks green) and the "
            f"last {look_steps} steps only read or searched. Do not re-read or "
            "re-check finished work. If a step of the request is still to do, make "
            "its edit now with what you already know; otherwise stop and give the "
            "final summary."
        ),
    }


def build_no_action_continue_message() -> dict[str, str]:
    """Push a build turn that only read into its first real edit."""
    return {"role": "user", "content": NO_ACTION_CONTINUE_PROMPT}


def build_remaining_work_continue_message() -> dict[str, str]:
    """Push a build turn that stopped halfway into its remaining steps."""
    return {"role": "user", "content": REMAINING_WORK_CONTINUE_PROMPT}


def build_delivery_continue_message(custom: str | None = None) -> dict[str, str]:
    """Prompt the model to resume when a delivery workflow produced no tools."""
    return {"role": "user", "content": custom or DELIVERY_CONTINUE_PROMPT}


def build_verify_before_done_message(custom: str | None = None) -> dict[str, str]:
    """Prompt the model to run verify/lint/tests before closing a Build turn."""
    return {"role": "user", "content": custom or VERIFY_BEFORE_DONE_CONTINUE_PROMPT}


def build_verify_failed_message(
    custom: str | None = None,
    *,
    last_summary: str | None = None,
) -> dict[str, str]:
    """Prompt the model to fix a red verify before closing a Build turn.

    ``last_summary`` is the recorded verify/lint/test output (already
    truncated). Without it the model only hears "verify is red" and
    re-guesses which tests failed.
    """
    body = custom or VERIFY_FAILED_CONTINUE_PROMPT
    detail = (last_summary or "").strip()
    if detail:
        body = f"{body}\n\nLast verification output:\n{detail[:800]}"
    return {"role": "user", "content": body}


def build_no_progress_continue_message(custom: str | None = None) -> dict[str, str]:
    """Request a different approach after repeated blocked calls."""
    return {"role": "user", "content": custom or NO_PROGRESS_CONTINUE_PROMPT}


def build_no_progress_finalization_message() -> dict[str, str]:
    """Explain an actual repeated-call blocker without inventing a time cap."""
    return {"role": "user", "content": NO_PROGRESS_FINALIZATION_PROMPT}


def build_ignored_nudge_reminder_message(ignored: int) -> dict[str, str]:
    """Follow-up after a reminder the model did not act on."""
    left = max(IGNORED_NUDGES_BEFORE_FINAL - ignored, 0)
    return {
        "role": "user",
        "content": (
            f"Reminder {ignored + 1}: the previous instruction was ignored and the "
            "last step only looked around again. Act on what you already found "
            "now (edit, run the check) or answer the user. "
            f"{left} more step like this and the turn must answer without tools."
        ),
    }


def build_ignored_nudges_finalization_message(ignored: int) -> dict[str, str]:
    """Final answer request once the reminders were ignored too many times."""
    return {
        "role": "user",
        "content": (
            f"You were reminded {ignored + 1} times to stop looking around and to act "
            "or answer, and kept going. Tools are off for the rest of this turn. "
            "Answer the user now, straight to the point, from what you already "
            "have: what was done, what you found, and what is still missing or "
            "which precise question remains. Do not announce further steps."
        ),
    }


def empty_tool_result_message(tool_name: str) -> str:
    """Short prompt-safe marker for tools that completed without visible output."""
    return f"({tool_name} completed with no output)"


def ensure_nonempty_tool_result(tool_name: str, content: Any) -> Any:
    """Replace semantically empty tool results with a short marker string."""
    if content is None:
        return empty_tool_result_message(tool_name)
    if isinstance(content, str) and not content.strip():
        return empty_tool_result_message(tool_name)
    if isinstance(content, list):
        if not content:
            return empty_tool_result_message(tool_name)
        text_payload = stringify_text_blocks(content)
        if text_payload is not None and not text_payload.strip():
            return empty_tool_result_message(tool_name)
    return content


def is_blank_text(content: str | None) -> bool:
    """True when *content* is missing or only whitespace."""
    return content is None or not content.strip()


def build_finalization_retry_message() -> dict[str, str]:
    """A short no-tools-allowed prompt for final answer recovery."""
    return {"role": "user", "content": FINALIZATION_RETRY_PROMPT}


def build_budget_exhausted_finalization_message() -> dict[str, str]:
    """Prompt the model for a no-tools final response after budget exhaustion."""
    return {"role": "user", "content": BUDGET_EXHAUSTED_FINALIZATION_PROMPT}


def build_length_recovery_message() -> dict[str, str]:
    """Prompt the model to continue after hitting output token limit."""
    return {"role": "user", "content": LENGTH_RECOVERY_PROMPT}


def build_goal_continue_message(custom: str | None = None) -> dict[str, str]:
    """Prompt the model to continue when a sustained goal is still active."""
    return {"role": "user", "content": custom or SUSTAINED_GOAL_CONTINUE_PROMPT}


def _tool_failure_signature(tool_name: str, arguments: Any) -> str:
    try:
        payload = json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(arguments)
    return f"{tool_name}:{payload}"


def repeated_tool_failure_hint(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Escalating hint once the same exact tool call has failed repeatedly.

    Soft tool errors keep the run alive so the model can self-correct, but an
    unbounded retry loop burns iterations on a call that will never succeed.
    Counting (tool, arguments) failures per turn keeps legitimate retries
    with adjusted arguments free, while the verbatim repeat gets told to stop.
    """
    signature = _tool_failure_signature(tool_name, arguments)
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count < _MAX_IDENTICAL_TOOL_FAILURES:
        return None
    logger.warning(
        "Tool {} failed {} times with identical arguments; escalating hint",
        tool_name,
        count,
    )
    if count >= _HARD_STOP_IDENTICAL_TOOL_FAILURES:
        return (
            f"\n\n[This exact {tool_name} call failed {count} times with identical "
            "arguments. Further identical calls are blocked for this turn only - "
            "change the arguments, use a different tool, or continue without it. "
            "The agent run is NOT stopped.]"
        )
    return (
        f"\n\n[This exact {tool_name} call has now failed {count} times. "
        "Do not repeat it verbatim: change the arguments, use a different "
        "tool, or report the blocker in your final answer.]"
    )


def repeated_tool_failure_is_hard_stop(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> bool:
    """True when the latest identical failure should abort further retries."""
    signature = _tool_failure_signature(tool_name, arguments)
    return seen_counts.get(signature, 0) >= _HARD_STOP_IDENTICAL_TOOL_FAILURES


def reset_tool_failure_count(tool_name: str, arguments: Any, seen_counts: dict[str, int]) -> None:
    """A successful retry ends the consecutive failure streak for this call."""
    seen_counts.pop(_tool_failure_signature(tool_name, arguments), None)


def reset_readonly_spin_counts(seen_counts: dict[str, int]) -> None:
    """Drop read-only signatures after a successful edit so a re-read is allowed."""
    stale = [
        key
        for key in seen_counts
        if key.split(":", 1)[0] in _READONLY_SPIN_TOOLS
        or key.startswith(_ACTION_REPEAT_PREFIX)
    ]
    for key in stale:
        del seen_counts[key]


def _counts_as_repeatable_action(tool_name: str) -> bool:
    return tool_name not in _READONLY_SPIN_TOOLS and tool_name not in _REPEAT_EXEMPT_TOOLS


def repeated_identical_action_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Refuse the third identical successful action since the last edit.

    Reads have their own, tighter budget above. This covers everything else
    (a command, a board call) run again verbatim after it already returned
    the same answer twice with nothing changed in between: the turn is
    spinning. A call whose answer moved (a log that grows) is not counted.
    """
    if not _counts_as_repeatable_action(tool_name):
        return None
    key = _ACTION_REPEAT_PREFIX + _tool_failure_signature(tool_name, arguments)
    if seen_counts.get(key, 0) < _MAX_IDENTICAL_ACTIONS:
        return None
    logger.warning(
        "Blocking identical {} call repeated a third time with no edit in between",
        tool_name,
    )
    return (
        f"Error: this exact {tool_name} call already ran {_MAX_IDENTICAL_ACTIONS} "
        "times this turn with identical arguments and the same answer, and "
        "nothing was edited since, so running it again will not help. Use what "
        "it returned, change the arguments, or answer the user now."
    )


def record_identical_action(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
    result: Any = None,
) -> None:
    """Count one successful run of ``tool_name`` with these exact arguments.

    The count only grows while the answer stays the same; a different
    answer starts it again at one.
    """
    if not _counts_as_repeatable_action(tool_name):
        return
    signature = _tool_failure_signature(tool_name, arguments)
    key = _ACTION_REPEAT_PREFIX + signature
    digest_key = _ACTION_DIGEST_PREFIX + signature
    digest = int(hashlib.sha1(str(result).encode("utf-8", "replace")).hexdigest()[:12], 16)
    if key in seen_counts and seen_counts.get(digest_key) == digest:
        seen_counts[key] += 1
    else:
        seen_counts[key] = 1
    seen_counts[digest_key] = digest


# Checks whose result depends only on the files. Re-running one with no edit
# since its last identical run repeats the same answer and burns a step: the
# "one more test run before I say done" loop at the end of every task.
_RERUN_EPOCH_KEY = "\x00rerun-check-epoch"
_RERUN_NEUTRAL_TOOLS = frozenset({
    "read_file", "grep", "find_files", "list_dir", "code_index", "metagraph",
    "web_search", "web_fetch", "skill", "board", "ask_user", "code_review",
    "improvement", "my", "team", "collab", "open_file_preview", "open_in_editor",
})
_RERUN_NEUTRAL_GIT = frozenset({
    "status", "diff", "log", "show", "blame", "branches", "stash_list", "add",
    "commit", "fetch", "push", "remote",
})
_CHECK_BINARIES = frozenset({
    "pytest", "vitest", "jest", "tsc", "ruff", "mypy", "pyright", "eslint",
    "flake8", "pylint", "actionlint", "shellcheck",
})
_CHECK_SCRIPTS = frozenset({"test", "lint", "build", "typecheck", "check", "type-check"})
_CHECK_SUBCOMMANDS = {
    "cargo": frozenset({"test", "build", "check", "clippy"}),
    "go": frozenset({"test", "build", "vet"}),
    "make": frozenset({"check", "test", "lint", "build"}),
}


def _is_check_command(command: str) -> bool:
    words = command.replace("&&", " ").replace("||", " ").replace("|", " ").replace(";", " ").split()
    for index, word in enumerate(words):
        name = word.rsplit("/", 1)[-1]
        following = words[index + 1] if index + 1 < len(words) else ""
        after = words[index + 2] if index + 2 < len(words) else ""
        if name in _CHECK_BINARIES:
            return True
        if name in {"npm", "pnpm", "yarn", "bun"}:
            if (after if following == "run" else following) in _CHECK_SCRIPTS:
                return True
        if following in _CHECK_SUBCOMMANDS.get(name, ()):
            return True
    return False


def _rerun_kind(tool_name: str, args: dict[str, Any]) -> str:
    """``check`` for a file-only check, ``neutral`` for a pure read, else ``edit``."""
    action = str(args.get("action") or "")
    if tool_name in _RERUN_NEUTRAL_TOOLS:
        return "neutral"
    if tool_name == "git":
        return "neutral" if action in _RERUN_NEUTRAL_GIT else "edit"
    if tool_name == "lsp":
        return "edit" if args.get("apply") else "neutral"
    if tool_name == "test_run":
        return "neutral" if action == "detect" else "check"
    if tool_name == "verify":
        if action in {"", "check"}:
            return "check"
        return "neutral" if action in {"snapshot", "snapshots"} else "edit"
    if tool_name == "lint":
        return "edit" if action == "fix" else "check"
    if tool_name == "project":
        if action in {"build", "test", "e2e", "lint", "typecheck"}:
            return "check"
        return "neutral" if action in {"detect", "status", "logs"} else "edit"
    if tool_name == "exec":
        if args.get("background") or args.get("yield_time_ms"):
            return "edit"
        command = args.get("command") or args.get("cmd") or ""
        return "check" if isinstance(command, str) and _is_check_command(command) else "edit"
    # Unknown tools may change files: never block the next check after them.
    return "edit"


def repeated_rerun_check_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Refuse an identical check when nothing that could change files ran since.

    Every call that may modify the workspace (edits, installs, other shell
    commands, unknown tools) starts a new epoch; a check (tests, lint, build,
    verify) is refused only when the same call already ran in the current
    epoch. Background runs are never refused.
    """
    if not isinstance(arguments, dict):
        return None
    kind = _rerun_kind(tool_name, arguments)
    if kind == "neutral":
        return None
    epoch = seen_counts.get(_RERUN_EPOCH_KEY, 0)
    if kind == "edit":
        seen_counts[_RERUN_EPOCH_KEY] = epoch + 1
        return None
    signature = "\x00rerun-check:" + _tool_failure_signature(tool_name, arguments)
    if seen_counts.get(signature) != epoch + 1:
        seen_counts[signature] = epoch + 1
        return None
    logger.info("Refusing identical {} check: no file changed since its last run", tool_name)
    return (
        "This exact check already ran in this turn and nothing that could change "
        "files ran since, so it would give the same result: it was not run again. "
        "Use its earlier output: fix what it reported, run a different targeted "
        "check, or give the final answer."
    )


def repeated_readonly_tool_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Block identical successful reads after a small per-turn budget.

    Failures already escalate via ``repeated_tool_failure_hint``. This catches
    the other loop: the same ``read_file`` / ``grep`` succeeding over and over
    because the previous result was blanked from context.
    """
    if tool_name not in _READONLY_SPIN_TOOLS:
        return None
    signature = _tool_failure_signature(tool_name, arguments)
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_IDENTICAL_READONLY_CALLS:
        return None
    logger.warning(
        "Blocking repeated identical {} call after {} successes this turn",
        tool_name,
        count,
    )
    return (
        f"Error: this exact {tool_name} call already ran "
        f"{_MAX_IDENTICAL_READONLY_CALLS} times this turn with identical "
        "arguments. The result is already in the conversation (or was "
        "cleared after you saw it). Do not repeat it verbatim. "
        + _readonly_spin_hint(tool_name, arguments)
    )


def _readonly_spin_hint(tool_name: str, arguments: Any) -> str:
    """Escape hatch named in the blocked-read message.

    ``read_file`` owns a ``force=true`` parameter built for exactly this case
    (compaction cleared the earlier content), so the error must name it.
    ``force=true`` calls carry their own budget; once that is spent too, the
    honest advice is to narrow the read or move on.
    """
    if tool_name != "read_file":
        return (
            "Change the path or pattern, edit the file, or answer from what "
            "you already have."
        )
    if isinstance(arguments, dict) and arguments.get("force"):
        return (
            "The force=true re-read budget is spent too: narrow the read with "
            "offset/limit, edit the file, or answer from what you already have."
        )
    return (
        "If compaction cleared the earlier content, pass force=true once to "
        "re-read the file with a fresh budget; otherwise change the arguments, "
        "edit the file, or answer from what you already have."
    )


def external_lookup_signature(tool_name: str, arguments: Any) -> str | None:
    """Stable signature for repeated external lookups we want to throttle."""
    if not isinstance(arguments, dict):
        return None
    if tool_name == "web_fetch":
        url = str(arguments.get("url") or "").strip()
        if url:
            return f"web_fetch:{url.lower()}"
    if tool_name == "web_search":
        query = str(arguments.get("query") or arguments.get("search_term") or "").strip()
        if query:
            return f"web_search:{query.lower()}"
    return None


def repeated_external_lookup_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Block repeated external lookups after a small retry budget."""
    signature = external_lookup_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_EXTERNAL_LOOKUPS:
        return None
    logger.warning(
        "Blocking repeated external lookup {} on attempt {}",
        signature[:160],
        count,
    )
    return (
        "Error: repeated external lookup blocked. "
        "Use the results you already have to answer, or try a meaningfully different source."
    )


# Workspace-boundary violations are soft errors, with per-target throttling.

_OUTSIDE_PATH_PATTERN = re.compile(r"(?:^|[\s|>'\"])((?:/[^\s\"'>;|<]+)|(?:~[^\s\"'>;|<]+))")


def workspace_violation_signature(
    tool_name: str,
    arguments: Any,
) -> str | None:
    """Return a stable cross-tool signature for the outside-workspace target."""
    if not isinstance(arguments, dict):
        return None
    for key in ("path", "file_path", "target", "source", "destination"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            return _normalize_violation_target(val.strip())

    if tool_name in {"exec", "shell"}:
        cmd = str(arguments.get("command") or "").strip()
        if cmd:
            match = _OUTSIDE_PATH_PATTERN.search(cmd)
            if match:
                return _normalize_violation_target(match.group(1))
        cwd = str(arguments.get("working_dir") or "").strip()
        if cwd:
            return _normalize_violation_target(cwd)

    return None


def _normalize_violation_target(raw: str) -> str:
    """Normalize *raw* path so that equivalent spellings collide on the same key."""
    try:
        normalized = Path(raw).expanduser().resolve().as_posix()
    except Exception:
        normalized = raw.replace("\\", "/")
    return f"violation:{normalized}".lower()


def repeated_workspace_violation_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Return an escalated error after repeated bypass attempts."""
    signature = workspace_violation_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_WORKSPACE_VIOLATIONS:
        return None
    logger.warning(
        "Escalating repeated workspace bypass attempt {} (attempt {})",
        signature[:160],
        count,
    )
    target = signature.split("violation:", 1)[1] if "violation:" in signature else signature
    return (
        "Error: refusing repeated workspace-bypass attempts.\n"
        f"You have tried to access '{target}' (or an equivalent path) "
        f"{count} times in this turn. This is a hard policy boundary -- "
        "switching tools, shell tricks, working_dir overrides, symlinks, "
        "or base64 piping will NOT change the answer. Stop retrying. "
        "If the user genuinely needs this resource, tell them you cannot "
        "access it and ask how they want to proceed (e.g. copy the file "
        "into the workspace, or disable restrict_to_workspace for this run)."
    )
