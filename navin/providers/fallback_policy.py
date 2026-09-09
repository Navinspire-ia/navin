# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""When to retry the chosen model, and when to give up and switch models.

The rule is the one the operator asked for: a model that blocks, for whatever
reason, is asked again at most twice, then the turn moves to the next model of
the list. A blocked step is never the end of the turn while another configured
model can take it.

Two decisions live here, kept apart on purpose:

- :func:`should_switch_model` - is this an error at all? Every error response
  is a reason to try the next model: rate limits and outages of course, but
  also an expired key, a slug the provider no longer serves, a refusal or a
  request shape one vendor rejects and another accepts. The switch is
  announced to the user (see :func:`model_switch_notice`), so nothing is
  hidden; what changes is that the turn keeps going.
- :func:`is_worth_retrying` - is it worth waiting and asking the same model
  again before switching? Only for availability failures that clear in
  seconds (busy, 5xx, dropped connection, empty body, an unclassified
  glitch). A definitive refusal (4xx, bad key, quota, content filter) and a
  timeout (a full request window already spent) switch at once.

Identifier fields (``error_kind``, ``error_type``, ``error_code``) are provider
enums, so substring matching on them is safe and intended. Free-form text is
matched only against specific phrases: tokens like ``empty``, ``balance`` and
``connection`` appear in ordinary prose.
"""

from __future__ import annotations

import random
import re
from typing import Any, Callable

from navin.providers.user_facing_errors import content_allows_model_switch

#: Failure kinds that mean "the endpoint is busy or unreachable right now".
TRANSIENT_KINDS: frozenset[str] = frozenset(
    {"timeout", "connection", "server_error", "rate_limit", "overloaded"}
)

#: Failure kinds that are definitive for this model: asking it again returns the
#: same answer, so the only way forward is the next model of the list.
BLOCKING_KINDS: frozenset[str] = frozenset(
    {
        "authentication",
        "auth",
        "permission",
        "content_filter",
        "refusal",
        "context_length",
        "invalid_request",
    }
)

#: Substrings allowed against provider enum fields only.
STRUCTURED_TOKENS: tuple[str, ...] = (
    "rate_limit",
    "too_many_requests",
    "overloaded",
    "server_error",
    "timeout",
    "connection",
    "empty",
    "insufficient_quota",
    "quota_exceeded",
    "quota_exhausted",
    "billing_hard_limit",
    "insufficient_balance",
)

#: Phrases allowed against free-form error text. Each one has to be specific
#: enough that it cannot show up in an unrelated message.
TEXT_PHRASES: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "too_many_requests",
    "overloaded",
    "server error",
    "server_error",
    "temporarily unavailable",
    "service unavailable",
    "timed out",
    "timeout",
    "connection error",
    "connection reset",
    "connection refused",
    "empty choices",
    "empty response",
    "no choices returned",
    "insufficient quota",
    "insufficient_quota",
    "quota exceeded",
    "quota_exceeded",
    "quota exhausted",
    "insufficient balance",
    "insufficient_balance",
    "out of credits",
    "billing_hard_limit",
)

_BLOCKING_STATUSES = frozenset({400, 401, 403, 404, 422})
_SWITCHING_STATUSES = frozenset({408, 409, 429})

#: Statuses that mean "wait, then ask again" rather than "this model is done".
RETRYABLE_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})

# Word boundaries matter here: without them "timeoutish" matched "timeout" and a
# healthy model was swapped out over a word in a sentence. Underscores are allowed
# on either side so provider identifiers like rate_limit_exceeded still match.
_TEXT_PATTERN = re.compile(
    "(?<![a-z0-9])(?:"
    + "|".join(re.escape(phrase) for phrase in TEXT_PHRASES)
    + ")(?![a-z0-9])",
    re.IGNORECASE,
)


def _fields(response: Any) -> tuple[str, str, str]:
    return (
        str(getattr(response, "error_kind", "") or "").lower(),
        str(getattr(response, "error_type", "") or "").lower(),
        str(getattr(response, "error_code", "") or "").lower(),
    )


def text_indicates_availability_problem(text: str | None) -> bool:
    """Match only specific phrases, never bare words, against free-form text."""
    return bool(text) and _TEXT_PATTERN.search(text or "") is not None


#: Substrings allowed against provider enum fields only.
_EXHAUSTED_MARKERS = ("quota", "balance", "credit", "billing", "payment_required")
#: Phrases allowed against free-form text. "balance" alone matched "load
#: balancer" in a 502 body, which is the opposite of an exhausted account.
_EXHAUSTED_TEXT_RE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"quota|insufficient[ _]balance|credit balance|balance is too low|"
    r"insufficient[ _]credits?|out of credits?|payment required|"
    r"billing[ _]hard[ _]limit|billing"
    r")(?![a-z0-9])",
    re.IGNORECASE,
)


def is_exhausted_account(response: Any) -> bool:
    """True for quota / balance / billing refusals, which do not clear in seconds."""
    if getattr(response, "error_status_code", None) == 402:
        return True
    kind, error_type, code = _fields(response)
    if any(
        marker in value
        for value in (kind, error_type, code)
        for marker in _EXHAUSTED_MARKERS
    ):
        return True
    text = str(getattr(response, "content", "") or "")
    return _EXHAUSTED_TEXT_RE.search(text) is not None


def is_blocking_failure(response: Any) -> bool:
    """True when this model's answer is definitive: a retry returns the same error.

    Malformed request for this vendor, bad or expired key, unknown slug,
    content filter, exhausted account. None of these clear by waiting, so the
    chosen model is not asked again; the next model of the list is.
    """
    if getattr(response, "error_should_retry", None) is False:
        return True
    if getattr(response, "error_status_code", None) in _BLOCKING_STATUSES:
        return True
    if is_exhausted_account(response):
        return True
    kind, error_type, code = _fields(response)
    if kind in BLOCKING_KINDS:
        return True
    return any(
        token in value
        for value in (kind, error_type, code)
        for token in BLOCKING_KINDS
    )


def is_timeout_failure(response: Any) -> bool:
    """True when the model spent a whole request or idle window saying nothing."""
    kind, _error_type, _code = _fields(response)
    if kind == "timeout":
        return True
    return getattr(response, "error_status_code", None) == 408


def should_switch_model(response: Any) -> bool:
    """True when this failure justifies serving the turn from another model.

    Every error qualifies. The primary was already retried when a retry made
    sense (see :func:`is_worth_retrying`); whatever is left is a model that
    blocks, and the operator's rule is that the next model of the list takes
    the step rather than the turn dying here.
    """
    return getattr(response, "finish_reason", None) == "error"


def is_availability_failure(response: Any) -> bool:
    """True when the error says the endpoint is busy or unreachable right now."""
    if getattr(response, "error_should_retry", None) is True:
        return True
    status = getattr(response, "error_status_code", None)
    if status is not None and (status in _SWITCHING_STATUSES or 500 <= status <= 599):
        return True
    kind, error_type, code = _fields(response)
    if kind in TRANSIENT_KINDS:
        return True
    if any(
        token in value
        for value in (kind, error_type, code)
        for token in STRUCTURED_TOKENS
    ):
        return True
    content = getattr(response, "content", None)
    if content_allows_model_switch(content):
        return True
    return text_indicates_availability_problem(content)


def is_worth_retrying(response: Any) -> bool:
    """True when the same model deserves another attempt after a short wait.

    Busy endpoints, 5xx, dropped connections and empty bodies clear on their
    own within seconds. An error the provider did not classify at all (no
    status, no kind) is treated the same way: a parse glitch or a torn stream
    is far more common there than a request the model will refuse forever.

    Definitive refusals (:func:`is_blocking_failure`) and timeouts are not
    retried: the first cannot change, the second has already cost a full
    request window, and both go straight to the next model.
    """
    if getattr(response, "finish_reason", None) != "error":
        return False
    if is_blocking_failure(response):
        return False
    if is_timeout_failure(response):
        return False
    status = getattr(response, "error_status_code", None)
    if status is not None:
        return status in RETRYABLE_STATUSES
    kind, error_type, code = _fields(response)
    if is_availability_failure(response):
        return True
    # Unclassified: nothing structured, no known phrase. One short wait is
    # cheap and usually enough for a torn stream or a malformed body.
    return not any((kind, error_type, code))


#: Longest wait before asking the chosen model again when another model could
#: take the step instead. Past this, switching is the better trade.
STICKY_MAX_DELAY_S = 10.0

#: How long the chosen model is skipped after it said "come back later" or ran
#: out of credit. Bounded so a stray hint cannot park the model for an hour.
CIRCUIT_MAX_COOLDOWN_S = 60.0


def sticky_retry_budget(response: Any, budget: int) -> int:
    """How many times the chosen model is asked again after *response*.

    ``budget`` (two by default) for failures that clear on their own; zero for
    a definitive refusal or a timeout, where the next model is the only useful
    move, and zero when the provider itself named a wait longer than a sticky
    retry (a ``Retry-After`` of 30s is not honoured by asking again in 10s).
    Never negative.
    """
    if budget <= 0:
        return 0
    if not is_worth_retrying(response):
        return 0
    if circuit_cooldown_s(response) is not None:
        return 0
    return budget


def circuit_cooldown_s(response: Any) -> float | None:
    """Seconds to skip the chosen model after this failure, or ``None`` for the default.

    A ``Retry-After`` longer than a sticky wait means the provider itself said
    the model is not available before then: asking it again on every step
    until the failure counter trips only burns the wait. An exhausted account
    does not recover within a turn either.
    """
    if is_exhausted_account(response):
        return CIRCUIT_MAX_COOLDOWN_S
    hinted = getattr(response, "error_retry_after_s", None) or getattr(
        response, "retry_after", None
    )
    if hinted:
        try:
            seconds = float(hinted)
        except (TypeError, ValueError):
            return None
        if seconds > STICKY_MAX_DELAY_S:
            return min(seconds, CIRCUIT_MAX_COOLDOWN_S)
    return None


#: How far past a computed delay a retry may be pushed, as a fraction of it.
JITTER_RATIO = 0.25


def apply_jitter(delay: float, *, rand: Callable[[], float] | None = None) -> float:
    """Spread a retry delay so clients that failed together do not return together.

    Every caller that took the same rate limit computes the same delay from the
    same rules, and a fleet sharing one provider account takes them at the same
    moment. Without a spread they all come back in the same instant and rebuild
    the spike that caused the limit.

    The spread only ever pushes a retry later, never earlier. When the delay
    came from a provider's ``Retry-After``, asking again before the window it
    named just earns another refusal.
    """
    if delay <= 0:
        return 0.0
    roll = (rand or random.random)()
    return round(delay * (1.0 + JITTER_RATIO * roll), 3)


def retry_delay(
    attempt: int,
    response: Any = None,
    base: float = 1.5,
    *,
    max_delay: float | None = None,
) -> float:
    """Seconds to wait before retrying the primary on *attempt* (1-based).

    A provider-supplied ``retry_after`` wins: guessing shorter only earns another
    rate limit. The value is capped so a stray large hint cannot stall a turn:
    30s for a hint, 20s for the backoff, each before jitter widens it by up to
    :data:`JITTER_RATIO`. Both paths are jittered, the hinted one above all - a
    hint every client receives at once is what synchronises a fleet.

    ``max_delay`` is a hard ceiling applied after jitter. The failover chain
    passes :data:`STICKY_MAX_DELAY_S`: when another model can take the step,
    waiting longer than that for the chosen one is the worse trade.
    """
    hinted = getattr(response, "error_retry_after_s", None) or getattr(
        response, "retry_after", None
    )
    delay: float | None = None
    if hinted:
        try:
            delay = apply_jitter(max(0.0, min(float(hinted), 30.0)))
        except (TypeError, ValueError):
            delay = None
    if delay is None:
        delay = apply_jitter(round(min(base * (2 ** max(0, attempt - 1)), 20.0), 3))
    if max_delay is not None:
        delay = min(delay, max(0.0, float(max_delay)))
    return delay


def model_switch_notice(primary_model: str, served_model: str) -> str:
    """Message shown when a turn was answered by a model the user did not pick."""
    return (
        f"{primary_model} could not answer this step, so it came from "
        f"{served_model} instead."
    )
