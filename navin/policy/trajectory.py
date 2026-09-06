"""One line per step of an eval episode: state, action, observation, reward.

The record is the whole memory of S4, so it is compact, secret-free and
honest about where its reward comes from::

    {
      "v": 1, "id": "7c1e...", "ts": "2026-09-05T02:10:41Z",
      "episode": "policy-r1-ab12cd34ef56|code-02|1f3a9c",
      "suite": "code", "case": "code-02", "split": "train", "step": 1,
      "intent": "fix,test", "prev": ["read_file:ok"], "s3": "ok",
      "action": "edit_file", "akey": "edit_file|.py", "obs": "changed",
      "reward": 1.0, "terminal": false, "source": "eval"
    }

* the **state** is ``intent`` (a few canonical verbs of the request, never
  the text), ``prev`` (the last three ``tool:class`` of the episode) and
  ``s3`` (what the project's world model predicted for that call, when it has
  an active head);
* the **action** is a tool name, or ``stop`` (final answer) / ``ask`` (the
  final answer was a question). ``akey`` is the S3 normalized key of the
  call (tool, program, extension): no values, no paths, no text;
* the **observation** is the S3 class of what the tool answered;
* the **reward** is the eval verdict of the whole episode (1.0 passed, 0.0
  failed), copied on every step. ``source`` is always ``eval``: a thumbs-up,
  a "merci" or any chat signal is not a reward and never reaches this file.
"""

from __future__ import annotations

import hashlib
import itertools
import re
import time
from dataclasses import dataclass, field
from typing import Any

from navin.world_model.trajectory import (
    CLASSES,
    HISTORY,
    classify_observation,
    normalize_call,
    now_stamp,
    scrub_secrets,
)

RECORD_VERSION = 1
SOURCE_EVAL = "eval"

STOP = "stop"
ASK = "ask"
SPECIAL_ACTIONS: tuple[str, ...] = (STOP, ASK)

# Tools the policy may propose but never execute or skip on its own (S4.4).
GUARDED_TOOLS = frozenset(
    {
        "write_file",
        "apply_patch",
        "edit_file",
        "create_file",
        "delete_file",
        "move_file",
        "rename_file",
        "manage_files",
        "exec",
        "shell",
        "git_commit",
        "git_push",
        "message",
        "notify",
        "send_message",
        "send_email",
        "payment",
        "desk",
    }
)

_POLICY_SALT = "navin-policy"
# Hosts, URLs and file names carry no intent ("example.test" is not a test).
_NOISE_RE = re.compile(r"https?://\S+|\S+\.[a-z0-9]\S*", re.IGNORECASE)

# Canonical intents: a word-bounded pattern on the lowered request and the
# intent it stands for. The result is a sorted, de-duplicated set of at most
# three; the request text itself is never stored.
_INTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bfix(es|ed|ing)?\b|\bbugs?\b|\brepair\w*|\bcorrig\w*|\brépar\w*|\bbroken\b|\bfail(s|ed|ing|ure)?\b", "fix"),
    (r"\badd(s|ed|ing)?\b|\bcreat\w*|\bajout\w*|\bcrée\w*|\bécri\w*|\bwrite\b|\bwriting\b|\bnew\b|\bnouveau\b|\bnouvelle\b", "add"),
    (r"\blist(s|ed|ing)?\b|\bshow\b|\bliste\w*|\baffich\w*|\bexport\w*", "list"),
    (r"\bfind\b|\bsearch\w*|\bwhere\b|\bcherch\w*|\btrouv\w*|\bgrep\b|\blocate\b", "find"),
    (r"\bread\b|\bexplain\w*|\bsummar\w*|\brésum\w*|\blis\b|\blire\b|\bwhat\b|\bwho\b|\bqui\b|\bquoi\b|\bque\b", "read"),
    (r"\brenam\w*|\bmove\b|\brenomm\w*|\bdéplac\w*", "rename"),
    (r"\bdelet\w*|\bremov\w*|\bsupprim\w*", "delete"),
    (r"\bcheck\w*|\bverif\w*|\bvérif\w*|\bwhether\b", "check"),
    (r"\btests?\b|\btesting\b", "test"),
    (r"\bfetch\w*|\bpages?\b|\bsite\b|\burl\b|\bopen\b|\bweb\b|\bprices?\b|\bprix\b|\bpricing\b", "fetch"),
    (r"\bschedul\w*|\bmeeting\b|\bcalendar\b|\brendez\b|\bagenda\b|\bplanifi\w*", "schedule"),
    (r"\bmail\b|\be-?mails?\b|\bsend\b|\benvoi\w*|\breminder\b|\brelance\w*", "mail"),
    (r"\bcontacts?\b|\bclients?\b|\bleads?\b|\bcustomers?\b|\bcompany\b", "crm"),
    (r"\binvoices?\b|\bfactur\w*|\bunpaid\b|\bimpay\w*", "invoice"),
    (r"\bcount\w*|\bhow many\b|\bcombien\b|\bnombre\b", "count"),
)
_INTENTS = tuple((re.compile(pattern, re.IGNORECASE), intent) for pattern, intent in _INTENT_PATTERNS)
_MAX_INTENTS = 3
UNKNOWN_INTENT = "other"


def intent_of(text: str | None) -> str:
    """Compact goal key of a request: ``"fix,test"``; never the text itself."""
    if not text:
        return UNKNOWN_INTENT
    lowered = _NOISE_RE.sub(" ", scrub_secrets(text)[:2000].lower())
    found = {intent for pattern, intent in _INTENTS if pattern.search(lowered)}
    if not found:
        return UNKNOWN_INTENT
    return ",".join(sorted(found)[:_MAX_INTENTS])


def action_key(tool_name: str, arguments: Any) -> str:
    """The S3 normalized key of a call (tool|program|sub or tool|.ext): safe to store."""
    return normalize_call(tool_name, arguments, salt=_POLICY_SALT).key


def final_action(final_content: str | None) -> str:
    """``ask`` when the final answer is a question, ``stop`` otherwise."""
    text = (final_content or "").strip()
    return ASK if text.endswith("?") else STOP


@dataclass(frozen=True, slots=True)
class StateKey:
    """What the head sees before choosing: intent, last calls, S3 class."""

    intent: str
    prev: tuple[str, ...] = ()
    s3: str | None = None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> StateKey:
        prev = record.get("prev")
        s3 = record.get("s3")
        return cls(
            intent=str(record.get("intent") or UNKNOWN_INTENT),
            prev=tuple(str(p) for p in prev)[-HISTORY:] if isinstance(prev, list) else (),
            s3=str(s3) if isinstance(s3, str) and s3 in CLASSES else None,
        )

    @property
    def last(self) -> str:
        return self.prev[-1] if self.prev else "start"


_SEQUENCE = itertools.count()


@dataclass(slots=True)
class Step:
    """One journal line, before serialization."""

    episode: str
    suite: str
    case: str
    split: str
    step: int
    intent: str
    prev: list[str]
    s3: str | None
    action: str
    akey: str
    obs: str
    reward: float
    terminal: bool = False
    ts: str = ""
    id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def finish(self, now: float | None = None) -> Step:
        if not self.ts:
            self.ts = now_stamp(now)
        if not self.id:
            raw = f"{self.episode}|{self.step}|{time.time_ns()}|{next(_SEQUENCE)}"
            self.id = hashlib.sha1(raw.encode()).hexdigest()[:16]
        return self

    def as_record(self) -> dict[str, Any]:
        return {
            "v": RECORD_VERSION,
            "id": self.id,
            "ts": self.ts,
            "episode": self.episode,
            "suite": self.suite,
            "case": self.case,
            "split": self.split,
            "step": self.step,
            "intent": self.intent,
            "prev": list(self.prev)[-HISTORY:],
            "s3": self.s3,
            "action": self.action,
            "akey": self.akey,
            "obs": self.obs,
            "reward": float(self.reward),
            "terminal": bool(self.terminal),
            "source": SOURCE_EVAL,
        }


def build_step(
    *,
    episode: str,
    suite: str,
    case: str,
    split: str,
    step: int,
    intent: str,
    prev: list[str] | None,
    s3: str | None,
    action: str,
    arguments: Any,
    result: Any,
    is_error: bool,
    reward: float,
    terminal: bool = False,
    read_only: bool | None = None,
    now: float | None = None,
) -> Step:
    """A step of an eval episode. Tool steps are classed from their result;
    ``stop`` / ``ask`` steps are ``ok``."""
    if action in SPECIAL_ACTIONS:
        akey, obs = action, "ok"
    else:
        akey = action_key(action, arguments)
        obs = classify_observation(action, result, is_error=is_error, read_only=read_only)
    return Step(
        episode=episode,
        suite=suite,
        case=case,
        split=split,
        step=step,
        intent=intent,
        prev=list(prev or [])[-HISTORY:],
        s3=s3 if isinstance(s3, str) and s3 in CLASSES else None,
        action=(action or "").strip()[:64] or "unknown",
        akey=akey[:120],
        obs=obs,
        reward=1.0 if reward >= 1.0 else 0.0,
        terminal=terminal,
    ).finish(now)


def record_is_valid(record: Any) -> bool:
    """A usable line: eval-sourced, classed, with a 0/1 reward and an action."""
    if not isinstance(record, dict):
        return False
    reward = record.get("reward")
    return (
        record.get("source") == SOURCE_EVAL
        and isinstance(record.get("action"), str)
        and isinstance(record.get("intent"), str)
        and isinstance(record.get("suite"), str)
        and record.get("split") in ("train", "heldout")
        and record.get("obs") in CLASSES
        and isinstance(reward, (int, float))
        and not isinstance(reward, bool)
        and float(reward) in (0.0, 1.0)
    )
