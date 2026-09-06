"""Path ownership checks for shared org projects (anti merge-conflict guard).

Zones are globs relative to the repo root (e.g. ``webui/**``, ``navin/agent/*``).
A path with no owners is allowed (greenfield). A path owned by others blocks
the actor unless a lead forces the change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class OwnershipZone:
    user_id: str
    path_glob: str
    label: str = ""


def normalize_rel_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.lstrip("/")
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    return cleaned


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    pattern = normalize_rel_path(glob)
    i = 0
    out: list[str] = ["^"]
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        ch = pattern[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch in ".[]{}()+-^$|\\":
            out.append("\\" + ch)
        else:
            out.append(ch)
        i += 1
    out.append("$")
    return re.compile("".join(out))


def path_matches_glob(file_path: str, glob: str) -> bool:
    path = normalize_rel_path(file_path)
    pattern = normalize_rel_path(glob)
    if not path or not pattern:
        return False
    if pattern in {"**", "**/*"}:
        return True
    return bool(_glob_to_regex(pattern).match(path))


def owners_for_path(zones: list[OwnershipZone], file_path: str) -> list[str]:
    owners: list[str] = []
    seen: set[str] = set()
    for zone in zones:
        if path_matches_glob(file_path, zone.path_glob) and zone.user_id not in seen:
            seen.add(zone.user_id)
            owners.append(zone.user_id)
    return owners


def files_outside_actor_zones(
    zones: list[OwnershipZone],
    actor_user_id: str,
    file_paths: list[str],
) -> list[str]:
    """Return normalized paths owned by someone else (not the actor)."""
    violations: list[str] = []
    for raw in file_paths:
        path = normalize_rel_path(raw)
        if not path:
            continue
        owners = owners_for_path(zones, path)
        if not owners:
            continue
        if actor_user_id not in owners:
            violations.append(path)
    return violations


def suggest_branch_name(ticket_id: str, slug: str) -> str:
    clean_ticket = re.sub(r"[^a-zA-Z0-9_-]+", "-", ticket_id.strip())[:40]
    clean_slug = re.sub(r"[^a-z0-9]+", "-", slug.strip().lower())[:40].strip("-")
    return f"feat/{clean_ticket}-{clean_slug}".rstrip("-")
