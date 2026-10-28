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


FAKE_POWERSHELL = r"""
import base64, sys
mode, png = sys.argv[1], base64.b64encode(bytes.fromhex(sys.argv[2])).decode()
if mode == "dead":
    sys.exit(0)
sys.stdout.write("READY\r\n")
sys.stdout.flush()
for line in sys.stdin:
    if mode == "hang":
        continue
    sys.stdout.write(("NONE" if mode == "none" else "PNG " + png) + "\r\n")
    sys.stdout.flush()
"""


def _fake_helper(mode: str, spawned: list[str]):
    import sys

    def argv_for(_powershell: str) -> list[str]:
        spawned.append(mode)
        return [sys.executable, "-c", FAKE_POWERSHELL, mode, PNG.hex()]

    return images.ClipboardHelper(argv_for=argv_for)


def _windows_clipboard(monkeypatch, helper) -> None:
    monkeypatch.setattr(images, "_HELPER", helper)
    monkeypatch.setattr(images.shutil, "which", lambda name: "/c/powershell.exe" if name == "powershell.exe" else None)


def test_one_powershell_serves_every_paste(tmp_path: Path, monkeypatch) -> None:
    # PowerShell plus Windows Forms took 1.2 s to start on every paste; the
    # helper starts once and the PNG comes back on its stdout.
    spawned: list[str] = []
    helper = _fake_helper("png", spawned)
    _windows_clipboard(monkeypatch, helper)
    try:
        first = images.save_clipboard_image(tmp_path)
        second = images.save_clipboard_image(tmp_path)
    finally:
        helper.close()
    assert first is not None and second is not None
    assert first.read_bytes() == PNG and second.read_bytes() == PNG
    assert spawned == ["png"]


def test_an_empty_clipboard_is_an_answer_not_a_failure(tmp_path: Path, monkeypatch) -> None:
    spawned: list[str] = []
    helper = _fake_helper("none", spawned)
    _windows_clipboard(monkeypatch, helper)
    calls: list[Path] = []
    monkeypatch.setattr(images, "_save_windows_clipboard_image_once", lambda target, ps: calls.append(target) or False)
    try:
        assert images.save_clipboard_image(tmp_path) is None
        assert images.save_clipboard_image(tmp_path) is None
    finally:
        helper.close()
    assert calls == [] and spawned == ["none"]
    assert list(tmp_path.iterdir()) == []


def test_a_helper_that_dies_or_hangs_falls_back_to_one_powershell(tmp_path: Path, monkeypatch) -> None:
    for mode in ("dead", "hang"):
        spawned: list[str] = []
        helper = _fake_helper(mode, spawned)
        helper.read_timeout_s = 0.5
        _windows_clipboard(monkeypatch, helper)

        def once(target: Path, powershell: str) -> bool:
            target.write_bytes(PNG)
            return True

        monkeypatch.setattr(images, "_save_windows_clipboard_image_once", once)
        try:
            saved = images.save_clipboard_image(tmp_path)
            # The broken helper was stopped; the next paste starts a fresh one.
            assert helper._proc is None
        finally:
            helper.close()
        assert saved is not None and saved.read_bytes() == PNG, mode


def test_a_png_already_on_the_clipboard_is_sent_before_any_re_encoding() -> None:
    # Screenshots and browser copies put a PNG on the clipboard: it goes as
    # is; the bitmap is encoded again only when there is no PNG.
    for script in (images._HELPER_SCRIPT, images._ONCE_SCRIPT):
        assert script.index("GetData('PNG')") < script.index("GetImage()")
        # PowerShell compares an array element by element: $null goes left.
        assert "-eq $null" not in script and "-ne $null" not in script


def test_the_one_shot_fallback_reads_the_png_from_stdout(tmp_path: Path, monkeypatch) -> None:
    # No wslpath and no write through \\wsl$: the PNG comes back base64.
    import base64
    import subprocess

    encoded = base64.b64encode(PNG)
    answers = {
        "ok": subprocess.CompletedProcess([], 0, encoded[:10] + b"\r\n" + encoded[10:], b""),
        "empty": subprocess.CompletedProcess([], 3, b"", b""),
        "garbage": subprocess.CompletedProcess([], 0, b"not base64 !!", b""),
        "not-png": subprocess.CompletedProcess([], 0, base64.b64encode(b"GIF89a...."), b""),
    }
    for name, answer in answers.items():
        target = tmp_path / f"{name}.png"
        seen: list[list[str]] = []
        monkeypatch.setattr(images, "_run", lambda cmd, timeout=8, answer=answer, seen=seen: seen.append(cmd) or answer)
        saved = images._save_windows_clipboard_image_once(target, "powershell.exe")
        assert saved is (name == "ok"), name
        assert target.exists() is (name == "ok"), name
        assert seen[0][-1] == images._ONCE_SCRIPT and "wslpath" not in seen[0]
    assert (tmp_path / "ok.png").read_bytes() == PNG
