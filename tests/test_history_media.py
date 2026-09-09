# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Images of earlier turns must reach a vision model again, within a budget."""

from __future__ import annotations

from pathlib import Path

from navin.agent.history_media import (
    REPLAY_MAX_IMAGES,
    image_block_for_path,
    replay_history_images,
)
from navin.session.manager import Session

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _png(tmp_path: Path, name: str) -> str:
    target = tmp_path / name
    target.write_bytes(PNG)
    return str(target)


def _user(text: str, *paths: str) -> dict:
    entry = {"role": "user", "content": text}
    if paths:
        entry["_meta"] = {"media": list(paths)}
    return entry


class TestSessionHistoryMediaRefs:
    def test_media_paths_travel_only_when_asked(self):
        session = Session(key="s")
        session.add_message("user", "look", media=["/tmp/shot.png"])
        session.add_message("assistant", "I see a button")

        plain = session.get_history()
        assert "_meta" not in plain[0]
        # The textual breadcrumb still tells a text-only model something was there.
        assert "[image: /tmp/shot.png]" in plain[0]["content"]

        with_refs = session.get_history(with_media_refs=True)
        assert with_refs[0]["_meta"] == {"media": ["/tmp/shot.png"]}
        assert "_meta" not in with_refs[1]

    def test_public_history_never_carries_refs(self):
        session = Session(key="s")
        session.add_message("user", "look", media=["/tmp/shot.png"])
        history = session.get_history(include_runtime_context=False)
        assert all("_meta" not in entry for entry in history)


class TestImageBlockForPath:
    def test_reads_a_real_image(self, tmp_path: Path):
        block = image_block_for_path(_png(tmp_path, "a.png"))
        assert block is not None
        assert block["type"] == "image_url"
        assert block["image_url"]["url"].startswith("data:image/png;base64,")
        assert block["_meta"]["path"].endswith("a.png")

    def test_missing_or_non_image_files_are_skipped(self, tmp_path: Path):
        assert image_block_for_path(str(tmp_path / "gone.png")) is None
        notes = tmp_path / "notes.md"
        notes.write_text("# hi", encoding="utf-8")
        assert image_block_for_path(str(notes)) is None


class TestReplayHistoryImages:
    def test_vision_model_gets_the_image_back(self, tmp_path: Path):
        shot = _png(tmp_path, "shot.png")
        history = [_user("what is wrong here?", shot), {"role": "assistant", "content": "A typo"}]

        out = replay_history_images(history, model="gpt-4o")

        content = out[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "image_url"
        assert content[-1] == {"type": "text", "text": "what is wrong here?"}
        # The input list is left alone: session data must not be rewritten.
        assert history[0]["content"] == "what is wrong here?"
        assert out[1] is history[1]

    def test_text_only_model_keeps_the_breadcrumbs(self, tmp_path: Path):
        shot = _png(tmp_path, "shot.png")
        history = [_user("[image: shot]", shot)]
        out = replay_history_images(history, model="deepseek-chat")
        assert out[0]["content"] == "[image: shot]"

    def test_budget_keeps_the_most_recent_images(self, tmp_path: Path):
        paths = [_png(tmp_path, f"s{i}.png") for i in range(REPLAY_MAX_IMAGES + 3)]
        history = []
        for index, path in enumerate(paths):
            history.append(_user(f"turn {index}", path))
            history.append({"role": "assistant", "content": "ok"})

        out = replay_history_images(history, model="gpt-4o")

        replayed = [
            entry for entry in out if isinstance(entry.get("content"), list)
        ]
        assert len(replayed) == REPLAY_MAX_IMAGES
        # Oldest turns stay text; the newest ones carry their image.
        assert isinstance(out[0]["content"], str)
        assert isinstance(out[-2]["content"], list)

    def test_budget_within_one_turn_prefers_the_last_attachments(self, tmp_path: Path):
        paths = [_png(tmp_path, f"m{i}.png") for i in range(3)]
        history = [_user("three shots", *paths)]
        out = replay_history_images(history, model="gpt-4o", max_images=2)
        blocks = [b for b in out[0]["content"] if b["type"] == "image_url"]
        assert [b["_meta"]["path"] for b in blocks] == paths[1:]

    def test_lookback_is_bounded_by_user_turns(self, tmp_path: Path):
        shot = _png(tmp_path, "old.png")
        history = [_user("old screenshot", shot)]
        for index in range(6):
            history.append({"role": "assistant", "content": f"a{index}"})
            history.append(_user(f"follow-up {index}"))
        out = replay_history_images(history, model="gpt-4o", max_user_turns=3)
        assert out[0]["content"] == "old screenshot"

    def test_deleted_files_and_entries_without_media_pass_through(self, tmp_path: Path):
        history = [
            _user("gone", str(tmp_path / "missing.png")),
            {"role": "assistant", "content": "?"},
            _user("plain text"),
        ]
        out = replay_history_images(history, model="gpt-4o")
        assert out == history

    def test_empty_history_and_zero_budget_are_noops(self):
        assert replay_history_images([], model="gpt-4o") == []
        history = [_user("x", "/tmp/a.png")]
        assert replay_history_images(history, model="gpt-4o", max_images=0) is history
