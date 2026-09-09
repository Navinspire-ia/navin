# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Readable beliefs: ten lines at most in ``.navin/BELIEFS.md`` (S3.4).

A belief is what the active head is confident about and that is *not*
"this works": ``exec npm test -> error (87%, 15 calls)``. The file is a
summary for humans, not the brain: the advisor reads the head, and the
prompt never reads this file while ``advise`` is off.

The rendered block sits between two markers so a human can keep notes above
or below; ``discard_belief`` records a key in ``beliefs-ignored.json`` so it
never comes back, without retraining anything.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.world_model import checkpoints as ck
from navin.world_model.model import CountModel
from navin.world_model.paths import beliefs_file, beliefs_ignored_path
from navin.world_model.trajectory import now_stamp

MAX_BELIEFS = 10
MIN_SUPPORT = 4
MIN_CONFIDENCE = 0.7

BEGIN = "<!-- navin:world-model:begin -->"
END = "<!-- navin:world-model:end -->"
_KEY_RE = re.compile(r"<!--\s*key:(.+?)\s*-->")


@dataclass(frozen=True, slots=True)
class Belief:
    key: str
    cls: str
    confidence: float
    support: int

    @property
    def text(self) -> str:
        tool, _, rest = self.key.partition("|")
        label = " ".join(part for part in rest.split("|") if part) or tool
        subject = f"{tool} {label}".strip() if label != tool else tool
        return f"`{subject}` -> {self.cls} ({round(self.confidence * 100)}%, {self.support} calls)"

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "cls": self.cls, "confidence": round(self.confidence, 3), "support": self.support, "text": self.text}


def derive_beliefs(model: CountModel, *, ignored: set[str] | None = None, limit: int = MAX_BELIEFS) -> list[Belief]:
    """Confident, non-trivial, well-supported keys of the head, best first."""
    ignored = ignored or set()
    beliefs: list[Belief] = []
    for key in model.keys():
        if key in ignored:
            continue
        _counts, support = model.key_support(key)
        if support < MIN_SUPPORT:
            continue
        prediction = model.predict_key(key)
        if prediction.cls == "ok" or prediction.confidence < MIN_CONFIDENCE:
            continue
        beliefs.append(Belief(key=key, cls=prediction.cls, confidence=prediction.confidence, support=support))
    beliefs.sort(key=lambda b: (b.confidence * min(b.support, 50), b.support), reverse=True)
    return beliefs[: max(0, limit)]


def read_ignored(workspace: Path | str) -> set[str]:
    try:
        with open(beliefs_ignored_path(workspace), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return set()
    keys = data.get("keys") if isinstance(data, dict) else None
    return {str(k) for k in keys} if isinstance(keys, list) else set()


def _write_ignored(workspace: Path | str, keys: set[str]) -> None:
    path = beliefs_ignored_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"keys": sorted(keys)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def current_beliefs(workspace: Path | str, *, model: CountModel | None = None) -> list[Belief]:
    """Beliefs of the active head (or the given one), minus what a human threw away."""
    if model is None:
        active = ck.active_checkpoint(workspace)
        if active is None:
            return []
        model = active.model
    return derive_beliefs(model, ignored=read_ignored(workspace))


def render_block(beliefs: list[Belief]) -> str:
    lines = [BEGIN, f"<!-- generated {now_stamp()}; edit or delete lines freely, the model is not retrained -->"]
    if beliefs:
        lines.extend(f"- {b.text} <!-- key:{b.key} -->" for b in beliefs)
    else:
        lines.append("- (no confident belief yet)")
    lines.append(END)
    return "\n".join(lines)


def render_beliefs(workspace: Path | str, *, model: CountModel | None = None) -> Path:
    """Write the managed block into ``.navin/BELIEFS.md``, keeping human text around it."""
    path = beliefs_file(workspace)
    beliefs = current_beliefs(workspace, model=model)
    block = render_block(beliefs)
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    if BEGIN in existing and END in existing:
        head, _, tail = existing.partition(BEGIN)
        _, _, tail = tail.partition(END)
        text = f"{head}{block}{tail}"
    else:
        intro = existing.rstrip() + "\n\n" if existing.strip() else "# Beliefs\n\nWhat the world model expects from this project's tools. Readable summary only.\n\n"
        text = f"{intro}{block}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_beliefs_file(workspace: Path | str) -> str | None:
    try:
        return beliefs_file(workspace).read_text(encoding="utf-8")
    except OSError:
        return None


def discard_belief(workspace: Path | str, key: str) -> list[Belief]:
    """A human threw one belief away: remember the key, re-render, no retrain."""
    ignored = read_ignored(workspace)
    ignored.add(key)
    _write_ignored(workspace, ignored)
    if beliefs_file(workspace).exists() or ck.active_checkpoint(workspace) is not None:
        render_beliefs(workspace)
    return current_beliefs(workspace)


def restore_beliefs(workspace: Path | str) -> list[Belief]:
    _write_ignored(workspace, set())
    if beliefs_file(workspace).exists():
        render_beliefs(workspace)
    return current_beliefs(workspace)


def keys_in_file(text: str) -> list[str]:
    return _KEY_RE.findall(text or "")


__all__ = [
    "BEGIN",
    "END",
    "Belief",
    "current_beliefs",
    "derive_beliefs",
    "discard_belief",
    "keys_in_file",
    "read_beliefs_file",
    "read_ignored",
    "render_beliefs",
    "restore_beliefs",
]
