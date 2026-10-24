from pathlib import Path

from navin.tui import images
from navin.tui.images import extract_image_paths, image_token

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def _png(path: Path) -> Path:
    path.write_bytes(PNG)
    return path


def test_token_and_plain_paths_are_attached(tmp_path: Path) -> None:
    a = _png(tmp_path / "a.png")
    b = _png(tmp_path / "shot b.jpg")
    text = f"{image_token(a)} compare avec '{b}' et ./a.png."
    assert extract_image_paths(text, tmp_path) == [str(a.resolve()), str(b.resolve())]


def test_missing_and_non_image_files_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x")
    assert extract_image_paths("voir notes.txt et absent.png", tmp_path) == []


def test_relative_path_resolves_from_workspace(tmp_path: Path) -> None:
    (tmp_path / "shots").mkdir()
    img = _png(tmp_path / "shots" / "ui.webp")
    assert extract_image_paths("analyse shots/ui.webp", tmp_path) == [str(img.resolve())]


def test_save_clipboard_image_none_without_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(images, "_save_windows_clipboard_image", lambda target: False)
    monkeypatch.setattr(images, "_save_unix_clipboard_image", lambda target: False)
    assert images.save_clipboard_image(tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_save_clipboard_image_returns_png(tmp_path: Path, monkeypatch) -> None:
    def fake(target: Path) -> bool:
        target.write_bytes(PNG)
        return True

    monkeypatch.setattr(images.shutil, "which", lambda name: None)
    monkeypatch.setattr(images.sys, "platform", "linux")
    monkeypatch.setattr(images, "_save_unix_clipboard_image", fake)
    saved = images.save_clipboard_image(tmp_path)
    assert saved is not None and saved.read_bytes() == PNG
