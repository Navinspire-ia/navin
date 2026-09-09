# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""S5.1 / S5.2 - the secret suites: loaded from outside the repository, frozen
by hash, checked for leaks before every campaign.

Layout of a suites folder (never inside a project, never inside a git
checkout)::

    <suites_dir>/code.jsonl        one item per line
    <suites_dir>/browser.jsonl
    <suites_dir>/business.jsonl
    <suites_dir>/plan.jsonl
    <suites_dir>/lock.json         hashes, authors, attester, junior baseline

An item has the shape of an S4 battery case without the script (the real
model decides what to call)::

    {"id": "code-017", "prompt": "...", "workspace": {"path": "content"},
     "expect": {"files_contain": {...}, "final_contains": [...]}, "seed": 17}

The lock is written once by a human (``freeze``) and records who wrote the
items and who reads the score, so that the organisational split (authors,
S4 trainers, score readers) is on paper. Any later change to an item file
is caught by the hash: the campaign is void.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.policy.battery import EXPECT_KEYS
from navin.transfer.paths import default_suites_dir, lock_path
from navin.transfer.protocol import FAMILY_IDS, PROTOCOL_VERSION
from navin.transfer.settings import read_settings
from navin.workspace_layout import navin_dir

HUMAN = "human"
_MAX_ITEMS_PER_FAMILY = 500
_MAX_FIXTURE_BYTES = 512 * 1024
_MAX_SCAN_FILES = 4000
_MAX_SCAN_BYTES = 2 * 1024 * 1024


class SuitesError(ValueError):
    """The suites folder is unusable (missing, malformed, misplaced)."""


class SuitesNotFrozenError(SuitesError):
    """No lock yet: a human has to freeze before any campaign."""


class SuitesTamperedError(RuntimeError):
    """An item file changed since the lock: the campaign is void."""


@dataclass(frozen=True, slots=True)
class TransferItem:
    id: str
    family: str
    prompt: str
    workspace: dict[str, str]
    expect: dict[str, Any]
    seed: int
    composer_mode: str = "agent"

    @property
    def fingerprint(self) -> str:
        """Short hash of the request text: what a leak scan looks for."""
        return prompt_fingerprint(self.prompt)


@dataclass(frozen=True, slots=True)
class SuitesLock:
    protocol: str
    frozen_at: str
    actor: str
    families: dict[str, dict[str, Any]]
    authors: tuple[str, ...]
    attester: str
    trainers: tuple[str, ...] = ()
    junior_baseline: dict[str, float] = field(default_factory=dict)

    @property
    def version(self) -> str:
        joined = "|".join(f"{fam}:{self.families[fam].get('sha256', '')}" for fam in sorted(self.families))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "frozen_at": self.frozen_at,
            "actor": self.actor,
            "families": self.families,
            "authors": list(self.authors),
            "attester": self.attester,
            "trainers": list(self.trainers),
            "junior_baseline": dict(self.junior_baseline),
        }


def prompt_fingerprint(prompt: str) -> str:
    normalized = re.sub(r"\s+", " ", (prompt or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def suites_dir_for(workspace: Path | str) -> Path:
    """The configured suites folder, or the machine default outside every project."""
    configured = read_settings(workspace).suites_dir
    return Path(configured).expanduser() if configured else default_suites_dir()


def s4_training_leak(workspace: Path | str, prompts: Iterable[str]) -> list[str]:
    """Secret item ids whose request text is in the S4 battery: training on
    them is forbidden (S5.0 "S4 refuses to prepare S5"). Empty when the
    transfer flag is off or no suites exist."""
    if not read_settings(workspace).enabled:
        return []
    try:
        suites = load_suites(suites_dir_for(workspace))
    except SuitesError:
        return []
    fingerprints = {prompt_fingerprint(p) for p in prompts}
    return [item.id for items in suites.values() for item in items if item.fingerprint in fingerprints]


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------


def _inside_git_checkout(path: Path) -> Path | None:
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            return parent
    return None


def check_outside(suites_dir: Path | str, workspace: Path | str | None) -> None:
    """Refuse a suites folder inside the project, inside any git checkout, or
    inside the project's ``.navin`` tree (where Dream and recall index)."""
    suites = Path(suites_dir).expanduser().resolve()
    if workspace is not None:
        root = Path(workspace).expanduser().resolve()
        if suites == root or root in suites.parents:
            raise SuitesError(f"secret suites must live outside the project: {suites} is under {root}")
    checkout = _inside_git_checkout(suites)
    if checkout is not None:
        raise SuitesError(f"secret suites must live outside any git checkout: {suites} is under {checkout}")


# --------------------------------------------------------------------------
# Items
# --------------------------------------------------------------------------


def family_file(suites_dir: Path | str, family: str) -> Path:
    return Path(suites_dir) / f"{family}.jsonl"


def _item(family: str, data: Any, index: int) -> TransferItem:
    where = f"{family}[{index}]"
    if not isinstance(data, dict):
        raise SuitesError(f"{where}: an item must be an object")
    item_id = str(data.get("id") or "").strip()
    prompt = str(data.get("prompt") or "").strip()
    if not item_id or not prompt:
        raise SuitesError(f"{where}: id and prompt are required")
    workspace = data.get("workspace") or {}
    if not isinstance(workspace, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in workspace.items()):
        raise SuitesError(f"{where}: workspace must map paths to text")
    if sum(len(v) for v in workspace.values()) > _MAX_FIXTURE_BYTES:
        raise SuitesError(f"{where}: fixture too large")
    for rel in workspace:
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise SuitesError(f"{where}: fixture path escapes the sandbox: {rel}")
    expect = data.get("expect") or {}
    if not isinstance(expect, dict) or not expect:
        raise SuitesError(f"{where}: expect is required (what decides pass / fail)")
    unknown = set(expect) - EXPECT_KEYS
    if unknown:
        raise SuitesError(f"{where}: unknown expect keys {sorted(unknown)}")
    seed = data.get("seed", index)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SuitesError(f"{where}: seed must be an integer")
    mode = str(data.get("composer_mode") or "agent").strip().lower() or "agent"
    return TransferItem(id=item_id, family=family, prompt=prompt, workspace=dict(workspace), expect=dict(expect), seed=seed, composer_mode=mode)


def _family_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_family(suites_dir: Path | str, family: str) -> list[TransferItem]:
    path = family_file(suites_dir, family)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items: list[TransferItem] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SuitesError(f"{family}[{index}]: unreadable JSON") from exc
        item = _item(family, data, index)
        if item.id in seen:
            raise SuitesError(f"{family}: duplicate item id {item.id}")
        seen.add(item.id)
        items.append(item)
        if len(items) > _MAX_ITEMS_PER_FAMILY:
            raise SuitesError(f"{family}: too many items")
    return items


def load_suites(suites_dir: Path | str) -> dict[str, list[TransferItem]]:
    """Every family file present, parsed and validated. Missing files are empty families."""
    return {family: load_family(suites_dir, family) for family in FAMILY_IDS}


def describe_suites(suites_dir: Path | str) -> dict[str, Any]:
    """Counts only: the panel and the CLI never see an item."""
    out: dict[str, Any] = {"dir": str(suites_dir), "families": {}, "error": None}
    try:
        for family, items in load_suites(suites_dir).items():
            out["families"][family] = len(items)
    except SuitesError as exc:
        out["error"] = str(exc)
    return out


# --------------------------------------------------------------------------
# Lock
# --------------------------------------------------------------------------


def read_lock(suites_dir: Path | str) -> SuitesLock | None:
    path = lock_path(suites_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    except json.JSONDecodeError as exc:
        raise SuitesTamperedError("lock unreadable") from exc
    if not isinstance(data, dict) or not isinstance(data.get("families"), dict):
        raise SuitesTamperedError("lock malformed")
    baseline = data.get("junior_baseline") or {}
    return SuitesLock(
        protocol=str(data.get("protocol") or ""),
        frozen_at=str(data.get("frozen_at") or ""),
        actor=str(data.get("actor") or ""),
        families={str(k): dict(v) for k, v in data["families"].items() if isinstance(v, dict)},
        authors=tuple(str(a) for a in (data.get("authors") or [])),
        attester=str(data.get("attester") or ""),
        trainers=tuple(str(t) for t in (data.get("trainers") or [])),
        junior_baseline={str(k): float(v) for k, v in baseline.items() if isinstance(v, (int, float)) and not isinstance(v, bool)},
    )


def freeze(
    suites_dir: Path | str,
    *,
    actor: str,
    authors: Iterable[str],
    attester: str,
    trainers: Iterable[str] = (),
    junior_baseline: dict[str, float] | None = None,
    workspace: Path | str | None = None,
    now: float | None = None,
) -> SuitesLock:
    """A human freezes the suites: hashes per family plus the organisational split.

    Refused when the actor is not a human, when no author is named, or when
    the attester (who reads the score) or an S4 trainer is also an author.
    """
    if actor != HUMAN:
        raise SuitesError("freezing the secret suites needs a human")
    suites_dir = Path(suites_dir).expanduser()
    check_outside(suites_dir, workspace)
    authors_t = tuple(a.strip() for a in authors if a and a.strip())
    trainers_t = tuple(t.strip() for t in trainers if t and t.strip())
    attester = (attester or "").strip()
    if not authors_t:
        raise SuitesError("name at least one item author")
    if not attester:
        raise SuitesError("name the attester who reads the score")
    if attester in authors_t:
        raise SuitesError("the attester must not be an item author (organisational split)")
    overlap = set(authors_t) & set(trainers_t)
    if overlap:
        raise SuitesError(f"an S4 trainer must not write items: {sorted(overlap)}")
    suites = load_suites(suites_dir)
    families: dict[str, dict[str, Any]] = {}
    for family, items in suites.items():
        path = family_file(suites_dir, family)
        if not items:
            continue
        families[family] = {"sha256": _family_sha(path), "items": len(items)}
    if not families:
        raise SuitesError(f"no items found in {suites_dir}")
    lock = SuitesLock(
        protocol=PROTOCOL_VERSION,
        frozen_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now)),
        actor=actor,
        families=families,
        authors=authors_t,
        attester=attester,
        trainers=trainers_t,
        junior_baseline={k: float(v) for k, v in (junior_baseline or {}).items() if k in FAMILY_IDS},
    )
    path = lock_path(suites_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(lock.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return lock


def verify(suites_dir: Path | str) -> SuitesLock:
    """The lock, after re-hashing every frozen family. Raises when anything moved."""
    suites_dir = Path(suites_dir).expanduser()
    lock = read_lock(suites_dir)
    if lock is None:
        raise SuitesNotFrozenError("secret suites are not frozen yet (navin agi transfer freeze)")
    if lock.protocol != PROTOCOL_VERSION:
        raise SuitesTamperedError(f"lock was frozen under protocol {lock.protocol}, this is {PROTOCOL_VERSION}: freeze again")
    for family, entry in lock.families.items():
        path = family_file(suites_dir, family)
        if not path.is_file():
            raise SuitesTamperedError(f"{family}.jsonl disappeared since the lock")
        if _family_sha(path) != entry.get("sha256"):
            raise SuitesTamperedError(f"{family}.jsonl changed since the lock: the campaign is void; freeze a new version and say why")
    for family in FAMILY_IDS:
        if family not in lock.families and family_file(suites_dir, family).is_file():
            raise SuitesTamperedError(f"{family}.jsonl appeared after the lock: freeze again")
    return lock


# --------------------------------------------------------------------------
# Leak scan
# --------------------------------------------------------------------------


def _scan_roots(workspace: Path) -> list[Path]:
    roots = [
        navin_dir(workspace) / "policy" / "cases.jsonl",
        navin_dir(workspace) / "skills",
        navin_dir(workspace) / "skills-evolve",
        navin_dir(workspace) / "cognition",
        navin_dir(workspace) / "memory",
        navin_dir(workspace) / "world",
        navin_dir(Path.home()) / "skills",
    ]
    return roots


def _iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    count = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            count += 1
            if count > _MAX_SCAN_FILES:
                return
            yield Path(dirpath) / name


def contamination(workspace: Path | str, items: Iterable[TransferItem]) -> list[dict[str, str]]:
    """Where a secret item leaked: an id or a request text found in the S4
    battery cases, a skill, an episode, a memory file or the world model
    folder. Empty means clean."""
    workspace = Path(workspace)
    needles: dict[str, str] = {}
    for item in items:
        needles[item.id] = item.id
        head = re.sub(r"\s+", " ", item.prompt.strip().lower())[:80]
        if len(head) >= 24:
            needles[head] = item.id
    if not needles:
        return []
    hits: list[dict[str, str]] = []
    for root in _scan_roots(workspace):
        for path in _iter_files(root):
            try:
                if path.stat().st_size > _MAX_SCAN_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lowered = re.sub(r"\s+", " ", text.lower())
            for needle, item_id in needles.items():
                if needle in lowered or needle in text:
                    hits.append({"item": item_id, "path": str(path)})
                    break
    return hits
