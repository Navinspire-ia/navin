from __future__ import annotations

import json
import multiprocessing
import time
import zipfile
from pathlib import Path

import pytest

from navin.notes import store
from navin.utils import atomic_io


def _writer(root: str, note_id: str, base: str, body: str, results: object) -> None:
    try:
        store.update_note(Path(root), note_id, markdown=body, base_updated=base)
        results.put("ok")
    except store.NoteConflictError:
        results.put("conflict")


def test_existing_vault_migrates_to_atomic_manifest(tmp_path: Path) -> None:
    (tmp_path / "legacy.md").write_text("# legacy", encoding="utf-8")
    note = store.list_notes(tmp_path)["notes"][0]
    assert store.get_note(tmp_path, note["id"])["note"]["path"] == "legacy.md"
    manifest = json.loads((tmp_path / store.MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["notes"][note["id"]] == "legacy.md"


def test_simulated_crash_keeps_note_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = store.create_note(tmp_path, title="Crash safe", markdown="before")
    path = tmp_path / created["note"]["path"]
    original = path.read_bytes()
    real_replace = atomic_io.os.replace

    def crash_on_note(source: object, destination: object) -> None:
        if Path(destination) == path:
            raise OSError("simulated crash before commit")
        real_replace(source, destination)

    monkeypatch.setattr(atomic_io.os, "replace", crash_on_note)
    with pytest.raises(OSError, match="simulated crash"):
        store.update_note(tmp_path, created["note"]["id"], tags=["changed"])
    assert path.read_bytes() == original
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_two_writers_produce_one_conflict(tmp_path: Path) -> None:
    created = store.create_note(tmp_path, title="Concurrent", markdown="v1")["note"]
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    writers = [
        context.Process(
            target=_writer,
            args=(str(tmp_path), created["id"], created["updated"], f"writer-{index}", results),
        )
        for index in range(2)
    ]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(15)
        assert writer.exitcode == 0
    assert sorted(results.get(timeout=2) for _ in writers) == ["conflict", "ok"]


def test_search_indexes_tags_aliases_and_external_edits(tmp_path: Path) -> None:
    created = store.create_note(tmp_path, title="Alpha", markdown="initial")
    path = tmp_path / created["note"]["path"]
    meta, body = store.parse_note_text(path.read_text(encoding="utf-8"))
    meta["tags"] = ["roadmap"]
    meta["aliases"] = ["launch-plan"]
    path.write_text(store.serialize_note(meta, body), encoding="utf-8")
    assert store.search_notes(tmp_path, "launch-plan")["results"][0]["id"] == created["note"]["id"]
    path.write_text(store.serialize_note(meta, "external needle"), encoding="utf-8")
    hit = store.search_notes(tmp_path, "needle")["results"][0]
    assert hit["matches"][0]["line"] == 1
    assert hit["snippet"] == "external needle"


def test_import_zip_is_sandboxed_and_handles_conflicts(tmp_path: Path) -> None:
    archive = tmp_path / "vault.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Team/Decision.md", "# Keep this")
        bundle.writestr("../escape.md", "unsafe")
    root = tmp_path / "notes"
    first = store.import_vault(root, archive)
    second = store.import_vault(root, archive, conflict="skip")
    assert first["imported"] == 1
    assert second["skipped"] == 1
    assert not (tmp_path / "escape.md").exists()
    assert store.list_notes(root, folder=r"Team")["total"] == 1


def test_windows_style_folder_path_stays_inside_root(tmp_path: Path) -> None:
    created = store.create_note(tmp_path, title="Portable", folder=r"one\two")
    assert created["note"]["path"].startswith("one/two/")
    assert (tmp_path / "one" / "two").is_dir()


def test_rebuild_and_search_five_thousand_notes(tmp_path: Path) -> None:
    timestamp = "2026-01-01T00:00:00.000000Z"
    for index in range(5_000):
        meta = {
            "id": f"id{index:010d}",
            "title": f"Record {index}",
            "tags": ["bulk"],
            "created": timestamp,
            "updated": timestamp,
        }
        (tmp_path / f"record-{index}.md").write_text(
            store.serialize_note(meta, f"body token-{index}"), encoding="utf-8"
        )
    started = time.monotonic()
    stats = store.rebuild_search_index(tmp_path)
    results = store.search_notes(tmp_path, "token-4321")
    assert stats["total"] == 5_000
    assert results["results"][0]["id"] == "id0000004321"
    assert time.monotonic() - started < 30
