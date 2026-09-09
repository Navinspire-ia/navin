# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Android preview session: Rust-accelerated with pure-Python fallback.

The native path uses ``navin_core.MobilePreviewSession`` (adb screencap loop,
logcat, input injection). Without the extension, the same contract is served
by subprocess calls so the WebUI and agent tool keep working.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

from navin.mobile.adb import adb_setup_help, resolve_adb_binary
from navin.utils.native import native
from navin.utils.proc import no_window_kwargs

_MAX_LOG_LINES = 400
_DEFAULT_FPS = 4.0


class PreviewError(Exception):
    pass


class PreviewSession(Protocol):
    def poll_frame(self, timeout_ms: int = 250, after_seq: int = 0) -> dict[str, Any]: ...
    def poll_logs(self, max_lines: int = 80) -> list[str]: ...
    def metrics(self) -> dict[str, Any]: ...
    def tap(self, x: int, y: int) -> None: ...
    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> None: ...
    def key(self, keycode: str) -> None: ...
    def text(self, value: str) -> None: ...
    def ui_dump(self) -> str: ...
    def device_serial(self) -> str | None: ...
    def is_alive(self) -> bool: ...
    def kill(self) -> None: ...


def open_preview(
    *,
    serial: str | None = None,
    fps: float = _DEFAULT_FPS,
) -> PreviewSession:
    """Open a preview session for the stack matching ``serial`` / host OS."""
    from navin.mobile.ios import is_ios_serial, open_ios_preview

    if is_ios_serial(serial):
        try:
            return open_ios_preview(serial=serial or "", fps=fps)
        except Exception as exc:
            raise PreviewError(str(exc)) from exc

    adb = resolve_adb_binary()
    if not adb:
        raise PreviewError(adb_setup_help())
    core = native()
    if core is not None and hasattr(core, "MobilePreviewSession"):
        try:
            return _NativePreview(
                core.MobilePreviewSession(serial=serial, fps=fps, adb=adb)
            )
        except Exception as exc:
            # Fall through to Python if adb/device checks fail the same way.
            if "no Android device" in str(exc) or "adb" in str(exc).lower():
                raise PreviewError(str(exc)) from exc
    return PythonPreviewSession(serial=serial, fps=fps, adb=adb)


@dataclass
class _NativePreview:
    _inner: Any

    def poll_frame(self, timeout_ms: int = 250, after_seq: int = 0) -> dict[str, Any]:
        raw = self._inner.poll_frame(timeout_ms=timeout_ms, after_seq=after_seq)
        return dict(raw)

    def poll_logs(self, max_lines: int = 80) -> list[str]:
        return list(self._inner.poll_logs(max_lines=max_lines))

    def metrics(self) -> dict[str, Any]:
        return dict(self._inner.metrics())

    def tap(self, x: int, y: int) -> None:
        try:
            self._inner.tap(int(x), int(y))
        except Exception as exc:
            raise PreviewError(_friendly_input_error(str(exc))) from exc

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> None:
        try:
            self._inner.swipe(int(x1), int(y1), int(x2), int(y2), int(duration_ms))
        except Exception as exc:
            raise PreviewError(_friendly_input_error(str(exc))) from exc

    def key(self, keycode: str) -> None:
        try:
            self._inner.key(_normalize_keyevent(keycode))
        except Exception as exc:
            raise PreviewError(_friendly_input_error(str(exc))) from exc

    def text(self, value: str) -> None:
        try:
            self._inner.text(str(value))
        except Exception as exc:
            raise PreviewError(_friendly_input_error(str(exc))) from exc

    def ui_dump(self) -> str:
        return str(self._inner.ui_dump())

    def device_serial(self) -> str | None:
        return self._inner.device_serial()

    def is_alive(self) -> bool:
        return bool(self._inner.is_alive())

    def kill(self) -> None:
        self._inner.kill()


class PythonPreviewSession:
    """Pure-Python adb screencap / input session."""

    def __init__(
        self,
        *,
        serial: str | None = None,
        fps: float = _DEFAULT_FPS,
        adb: str | None = None,
    ) -> None:
        self._adb = adb or resolve_adb_binary() or shutil.which("adb")
        if not self._adb:
            raise PreviewError(adb_setup_help())
        self._serial = (serial or "").strip() or None
        self._interval = 1.0 / max(0.5, min(float(fps or _DEFAULT_FPS), 20.0))
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._frame = b""
        self._width = 0
        self._height = 0
        self._seq = 0
        self._ts_ms = 0
        self._fps = 0.0
        self._mem_mb = 0.0
        self._cpu_pct = 0.0
        self._error: str | None = None
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._recent: deque[float] = deque()
        self._ensure_device()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="navin-py-mobile-capture", daemon=True
        )
        self._log_thread = threading.Thread(
            target=self._logcat_loop, name="navin-py-mobile-logcat", daemon=True
        )
        self._metrics_thread = threading.Thread(
            target=self._metrics_loop, name="navin-py-mobile-metrics", daemon=True
        )
        self._capture_thread.start()
        self._log_thread.start()
        self._metrics_thread.start()

    def _adb_base(self) -> list[str]:
        cmd = [self._adb]
        if self._serial:
            cmd.extend(["-s", self._serial])
        return cmd

    def _run(self, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess[bytes]:
        completed = subprocess.run(  # noqa: S603
            [*self._adb_base(), *args],
            capture_output=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
        return completed

    def _run_ok(self, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess[bytes]:
        completed = self._run(*args, timeout=timeout)
        if completed.returncode == 0:
            return completed
        err = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        out = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
        detail = err or out or f"exit {completed.returncode}"
        raise PreviewError(detail)

    def _ensure_device(self) -> None:
        try:
            completed = self._run("devices", "-l", timeout=12)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreviewError(f"adb devices failed: {exc}") from exc
        text = completed.stdout.decode("utf-8", errors="replace")
        ready = False
        for line in text.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                ready = True
                break
        if not ready:
            raise PreviewError(
                "no Android device/emulator online (adb devices). "
                "Start an AVD or plug a phone."
            )

    def _capture_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                completed = self._run("exec-out", "screencap", "-p", timeout=15)
                if completed.returncode != 0:
                    err = completed.stderr.decode("utf-8", errors="replace")
                    raise PreviewError(f"screencap failed: {err}")
                png = _normalize_png(completed.stdout)
                size = _png_size(png)
                if size is None:
                    raise PreviewError("screencap did not return a PNG")
                w, h = size
                now = time.monotonic()
                self._recent.append(now)
                while self._recent and now - self._recent[0] > 2.0:
                    self._recent.popleft()
                fps = (
                    (len(self._recent) - 1) / max(0.001, now - self._recent[0])
                    if len(self._recent) >= 2
                    else 0.0
                )
                with self._cond:
                    self._frame = png
                    self._width, self._height = w, h
                    self._seq += 1
                    self._ts_ms = int(time.time() * 1000)
                    self._fps = fps
                    self._error = None
                    self._cond.notify_all()
            except Exception as exc:
                detail = str(exc)
                fatal = is_device_gone_error(detail)
                with self._cond:
                    self._error = (
                        friendly_device_gone_message(detail) if fatal else detail
                    )
                    self._cond.notify_all()
                if fatal:
                    # Emulator closed / USB unplugged - stop the session so the
                    # UI can leave "running" and offer Start emulator again.
                    self._stop.set()
                    break
                self._stop.wait(0.8)
            elapsed = time.monotonic() - started
            if elapsed < self._interval:
                self._stop.wait(self._interval - elapsed)

    def _logcat_loop(self) -> None:
        try:
            self._run("logcat", "-c", timeout=8)
        except Exception:
            pass
        try:
            proc = subprocess.Popen(  # noqa: S603
                [*self._adb_base(), "logcat", "-v", "time"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                **no_window_kwargs(),
            )
        except OSError as exc:
            with self._cond:
                self._error = f"logcat spawn failed: {exc}"
            return
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                line = line.rstrip("\n")
                if line:
                    self._logs.append(line)
        finally:
            proc.kill()

    def _metrics_loop(self) -> None:
        while not self._stop.wait(2.0):
            try:
                mem = self._run("shell", "dumpsys", "meminfo", timeout=10)
                text = mem.stdout.decode("utf-8", errors="replace")
                parsed = _parse_mem_mb(text)
                if parsed is not None:
                    self._mem_mb = parsed
            except Exception:
                pass

    def poll_frame(self, timeout_ms: int = 250, after_seq: int = 0) -> dict[str, Any]:
        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        with self._cond:
            while self._seq <= after_seq and self._error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            return {
                "png": bytes(self._frame),
                "width": self._width,
                "height": self._height,
                "seq": self._seq,
                "ts_ms": self._ts_ms,
                "fps": self._fps,
                "mem_mb": self._mem_mb,
                "cpu_pct": self._cpu_pct,
                **({"error": self._error} if self._error else {}),
            }

    def poll_logs(self, max_lines: int = 80) -> list[str]:
        lines = list(self._logs)
        return lines[-max(1, max_lines) :]

    def metrics(self) -> dict[str, Any]:
        return {
            "fps": self._fps,
            "mem_mb": self._mem_mb,
            "cpu_pct": self._cpu_pct,
            "width": self._width,
            "height": self._height,
            "frame_seq": self._seq,
        }

    def tap(self, x: int, y: int) -> None:
        self._run_ok("shell", "input", "tap", str(int(x)), str(int(y)), timeout=8)

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> None:
        self._run_ok(
            "shell",
            "input",
            "swipe",
            str(int(x1)),
            str(int(y1)),
            str(int(x2)),
            str(int(y2)),
            str(max(1, int(duration_ms))),
            timeout=12,
        )

    def key(self, keycode: str) -> None:
        code = _normalize_keyevent(keycode)
        if not code:
            raise PreviewError("keycode required")
        try:
            self._run_ok("shell", "input", "keyevent", code, timeout=8)
        except PreviewError as exc:
            raise PreviewError(_friendly_input_error(str(exc))) from exc

    def text(self, value: str) -> None:
        escaped = str(value).replace(" ", "%s").replace("'", "\\'")
        try:
            self._run_ok("shell", "input", "text", escaped, timeout=12)
        except PreviewError as exc:
            raise PreviewError(_friendly_input_error(str(exc))) from exc

    def ui_dump(self) -> str:
        self._run(
            "shell", "uiautomator", "dump", "/sdcard/window_dump.xml", timeout=20
        )
        completed = self._run("shell", "cat", "/sdcard/window_dump.xml", timeout=12)
        text = completed.stdout.decode("utf-8", errors="replace")
        if not text.strip():
            raise PreviewError(
                "ui_dump empty - is a device unlocked and UIAutomator available?"
            )
        return text

    def device_serial(self) -> str | None:
        return self._serial

    def is_alive(self) -> bool:
        return not self._stop.is_set()

    def kill(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()


def frame_to_b64(frame: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe frame payload for WebSocket events."""
    png = frame.get("png") or b""
    if isinstance(png, memoryview):
        png = png.tobytes()
    return {
        "png_b64": base64.b64encode(bytes(png)).decode("ascii") if png else "",
        "width": int(frame.get("width") or 0),
        "height": int(frame.get("height") or 0),
        "seq": int(frame.get("seq") or 0),
        "ts_ms": int(frame.get("ts_ms") or 0),
        "fps": float(frame.get("fps") or 0),
        "mem_mb": float(frame.get("mem_mb") or 0),
        "cpu_pct": float(frame.get("cpu_pct") or 0),
        **(
            {"error": str(frame["error"])}
            if frame.get("error")
            else {}
        ),
    }


def _normalize_keyevent(keycode: str) -> str:
    """Map UI / agent aliases to stable adb keyevent tokens."""
    raw = str(keycode or "").strip()
    if not raw:
        return ""
    aliases = {
        "BACK": "KEYCODE_BACK",
        "HOME": "KEYCODE_HOME",
        "APP_SWITCH": "KEYCODE_APP_SWITCH",
        "RECENTS": "KEYCODE_APP_SWITCH",
        "POWER": "KEYCODE_POWER",
        "ENTER": "KEYCODE_ENTER",
        "VOLUME_UP": "KEYCODE_VOLUME_UP",
        "VOLUME_DOWN": "KEYCODE_VOLUME_DOWN",
    }
    upper = raw.upper()
    if upper in aliases:
        return aliases[upper]
    if upper.startswith("KEYCODE_"):
        return upper
    return raw


def is_device_gone_error(detail: str) -> bool:
    """True when the adb target disappeared (emulator closed / USB unplugged)."""
    lowered = (detail or "").lower()
    needles = (
        "not found",
        "device offline",
        "device unauthorized",
        "no devices/emulators found",
        "no android device",
        "error: closed",
        "connection reset",
        "listener 'emulator-",
    )
    if any(n in lowered for n in needles):
        # Prefer device-target failures over unrelated "file not found".
        if "device '" in lowered or "emulator-" in lowered:
            return True
        if "no devices" in lowered or "device offline" in lowered:
            return True
        if "device unauthorized" in lowered:
            return True
        if "screencap" in lowered and "not found" in lowered:
            return True
        if "exit status" in lowered and "not found" in lowered:
            return True
    return False


def friendly_device_gone_message(detail: str = "") -> str:
    """User-facing copy when the mirrored device disappears mid-preview."""
    base = (
        "Device disconnected (emulator closed or phone unplugged). "
        "Click Start emulator or plug the phone, then Start preview."
    )
    extra = (detail or "").strip()
    if not extra:
        return base
    return f"{base} ({extra[:180]})"


def _friendly_input_error(detail: str) -> str:
    text = (detail or "").strip() or "input command failed"
    if is_device_gone_error(text):
        return friendly_device_gone_message(text)
    lowered = text.lower()
    if "can't find service: input" in lowered or "service: input" in lowered:
        return (
            "Android input service is down on this emulator "
            "(often after a Pixel Launcher ANR on software GPU). "
            "Stop preview, reboot the AVD, then retry. "
            f"Detail: {text}"
        )
    return text


def _normalize_png(data: bytes) -> bytes:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data
    return data.replace(b"\r", b"")


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w = int.from_bytes(data[16:20], "big")
    h = int.from_bytes(data[20:24], "big")
    return w, h


def _parse_mem_mb(text: str) -> float | None:
    for line in text.splitlines():
        if "MemTotal" in line or "memtotal" in line.lower():
            parts = line.split()
            for token in parts:
                try:
                    return float(token) / 1024.0
                except ValueError:
                    continue
        if line.lstrip().startswith("TOTAL"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    value = float(parts[1])
                except ValueError:
                    continue
                return value / 1024.0 if value > 10_000 else value
    return None
