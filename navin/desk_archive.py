# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive desk results while retaining Career configuration and reusable talent."""

from __future__ import annotations

import base64
import io
import os
import re
import shutil
import time
import uuid
import zipfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from navin.career.store import _atomic_write, _read_json


def list_archives(root: Path) -> list[dict[str, Any]]:
    folder = root / "archives"
    if not folder.is_dir():
        return []
    return [data for path in sorted(folder.glob("*/manifest.json"), reverse=True)
            if isinstance(data := _read_json(path, {}), dict) and data.get("id")]


def download_archive(root: Path, archive_id: str) -> dict[str, str]:
    if not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{8}", archive_id):
        raise ValueError("Invalid archive.")
    folder = root / "archives" / archive_id
    if not (folder / "manifest.json").is_file():
        raise ValueError("Archive not found.")
    files = [path for path in folder.rglob("*") if path.is_file() and not path.is_symlink()
             and folder.resolve() in path.resolve().parents]
    if sum(path.stat().st_size for path in files) > 64 * 1024 * 1024:
        raise ValueError("Archive exceeds 64 MB. Open its local folder.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(folder))
    return {"name": f"archive-{archive_id}.zip", "data_b64": base64.b64encode(output.getvalue()).decode()}


def archive_reset(store: Any, *, module: str, confirmed: bool) -> dict[str, Any]:
    if module == "career":
        from navin.career.errors import CareerError as Error
    else:
        from navin.tenders.errors import TenderError as Error
    if not confirmed:
        raise Error("Confirm archiving before starting over.", status=400)
    try:
        with ExitStack() as locks:
            locks.enter_context(FileLock(str(store.root / "desk.lock"), timeout=0))
            locks.enter_context(FileLock(str(store.root / "prospecting.lock"), timeout=0))
            locks.enter_context(FileLock(str(store.root / "mail.lock"), timeout=0))
            # A full archive is a separate directory, never subject to offer retention.
            archive_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
            target = store.root / "archives" / archive_id
            target.mkdir(parents=True)
            preserved = []
            retained_state = {}
            retained_loop = {}
            retained_files = {"profile.json", "cv.md", "dossier.md", "employers.json", "files"} if module == "career" else set()
            if module == "career":
                from navin.career.prospecting import _state

                state = _state(store)
                preserved = state["candidates"]
                retained_state = {key: state[key] for key in ("criteria", "candidates", "health", "checks")}
                retained_loop = {**store.load_loop(), "enabled": False, "phase": "paused", "next_due": 0.0,
                                 "last_result": "Results cleared. Scheduled searches are paused."}
            entries = [p for p in store.root.iterdir() if p.name not in {"archives", "secrets.json", "talent-files"}
                       and not p.name.endswith(".lock")]
            moved = []
            try:
                talent_files = store.root / "talent-files"
                if module == "career" and talent_files.is_dir():
                    archived_talents = target / "talent-files"
                    archived_talents.mkdir()
                    for path in talent_files.iterdir():
                        if path.is_file() and not path.is_symlink():
                            shutil.copy2(path, archived_talents / path.name)
                for path in entries:
                    if path.name in retained_files:
                        if path.is_dir():
                            shutil.copytree(path, target / path.name, symlinks=True)
                        else:
                            shutil.copy2(path, target / path.name, follow_symlinks=False)
                    else:
                        os.replace(path, target / path.name)
                        moved.append(path.name)
                if module == "career":
                    _atomic_write(store.root / "prospecting.json", retained_state)
                    _atomic_write(store.loop_path, retained_loop)
                manifest = {"id": archive_id, "module": module, "at": time.time(),
                            "files": [path.name for path in entries], "retained_candidates": len(preserved),
                            "retained_configuration": module == "career", "path": str(target)}
                _atomic_write(target / "manifest.json", manifest)
                return manifest
            except Exception:
                # Roll the move back if the archive cannot be finalized.
                for name in reversed(moved):
                    os.replace(target / name, store.root / name)
                raise
    except Timeout:
        raise Error("An operation is in progress. Wait until it finishes before resetting.", status=409) from None
