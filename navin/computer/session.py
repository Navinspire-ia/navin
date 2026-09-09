"""One desktop session per agent conversation: backend, scaling, live view, audit.

The model never sees the physical screen. It sees a screenshot scaled to fit
``max_width x max_height`` (1366x768 by default, the size grounding models are
trained on) and answers in that image's pixel coordinates. :meth:`to_screen`
turns those back into screen pixels, and :meth:`to_model` does the reverse for
window rectangles and accessibility nodes, so every coordinate the model ever
reads or writes lives in the same space.
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
import os
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

from loguru import logger

from navin.computer.base import (
    ComputerBackend,
    ComputerError,
    ScreenInfo,
    Screenshot,
    UIElement,
    WindowInfo,
)
from navin.computer.safety import AuditLog, TakeoverMonitor


class DesktopAccess:
    """A physical display has one cursor, even across different conversations."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.owner: str | None = None
        self.user_session: str | None = None


_DESKTOP_ACCESS: WeakValueDictionary[tuple[int, str], DesktopAccess] = WeakValueDictionary()


def _desktop_access(config: Any) -> DesktopAccess:
    from navin.computer.detect import detect_platform

    display = getattr(config, "display", None)
    choice = detect_platform(preferred=str(getattr(config, "backend", "auto")), display=display)
    address = display or os.environ.get("DISPLAY", "") if choice.name == "x11" else ""
    # X11 screens on the same server share the pointer and keyboard.
    address = str(address).split(".", 1)[0] if str(address).startswith(":") else str(address)
    key = (id(asyncio.get_running_loop()), f"{choice.name}:{address}")
    access = _DESKTOP_ACCESS.get(key)
    if access is None:
        access = DesktopAccess()
        _DESKTOP_ACCESS[key] = access
    return access


class ComputerSession:
    def __init__(
        self,
        config: Any,
        *,
        backend_factory: Callable[[], ComputerBackend],
        session_key: str = "default",
    ) -> None:
        self.config = config
        self.session_key = session_key
        self.id = uuid.uuid4().hex[:12]
        self.lock = asyncio.Lock()
        self.access = _desktop_access(config)
        self.closed = False
        self._factory = backend_factory
        self._backend: ComputerBackend | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=200)
        # Last full screenshot and the size the model saw it at.
        self.last_shot: Screenshot | None = None
        self.model_size: tuple[int, int] | None = None
        self.last_scaled_png: bytes | None = None
        self.live_shot: Screenshot | None = None
        # Accessibility refs from the last snapshot, in model coordinates.
        self.elements: list[UIElement] = []
        self.takeover = TakeoverMonitor(
            threshold_px=int(getattr(config, "user_takeover_px", 48) or 0),
            failsafe_corner=bool(getattr(config, "failsafe_corner", True)),
        )
        self._turn_id: str | None = None
        self._turn_actions = 0
        self.audit = AuditLog(
            directory=None, keep_screenshots=bool(getattr(config, "audit_screenshots", True))
        )
        # Live view: mirror of the desktop streamed to the Dev workbench.
        self._live_bus: Any = None
        self._live_chat_id: str | None = None
        self._live_started = False
        self._live_last_frame = 0.0
        self._preview_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------ lifecycle

    def backend(self) -> ComputerBackend:
        if self.closed:
            raise ComputerError("this desktop session was closed; take a new screenshot")
        if self._backend is None:
            self._backend = self._factory()
        return self._backend

    async def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Keep the display locked until a cancelled native call really stops."""
        worker = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                await worker
            finally:
                raise

    async def close(self) -> None:
        self.closed = True
        if self.access.owner == self.id:
            self.access.owner = None
        if self.access.user_session == self.id:
            self.access.user_session = None
        if self._preview_task is not None:
            self._preview_task.cancel()
            self._preview_task = None
        if self._live_started:
            self._live_started = False
            self._emit_live("exit")
        backend, self._backend = self._backend, None
        if backend is not None:
            await self.run(backend.close)
        self.last_shot = None
        self.live_shot = None
        self.last_scaled_png = None
        self.model_size = None
        self.elements = []

    @property
    def user_control(self) -> bool:
        return self.access.user_session is not None

    def check_action(self) -> None:
        from navin.computer.policy import stop_reason

        if self.closed:
            raise ComputerError("this desktop session was closed")
        if self.user_control:
            raise ComputerError(
                "the user has control of the live desktop. Wait until they return control "
                "using the live view; action=resume cannot release their control."
            )
        if stopped := stop_reason():
            raise ComputerError(f"computer use is stopped ({stopped})")
        if self.access.owner not in {None, self.id}:
            raise ComputerError(
                "this desktop is being used by another conversation. Close its desktop "
                "session before controlling the same display here."
            )

    async def check_screen_readable(self) -> None:
        from navin.computer.policy import AppPolicy, stop_reason

        if stopped := stop_reason():
            raise ComputerError(f"computer use is stopped ({stopped})")
        policy = AppPolicy.from_config(self.config)
        if not policy.protected:
            return
        try:
            active = await self.run(self.backend().active_window)
        except ComputerError:
            active = None
        verdict = policy.evaluate("screenshot", mutating=False, active=active)
        if not verdict.allowed:
            raise ComputerError(verdict.reason)

    def set_user_control(self, active: bool) -> None:
        self.access.user_session = self.id if active else None
        self.elements = []
        if not active:
            self.takeover.resume()
        if active and self._live_bus is not None and self._preview_task is None:
            self._preview_task = asyncio.create_task(self._preview_during_takeover())

    async def _preview_during_takeover(self) -> None:
        try:
            while self.user_control and not self.closed and self.config.live_view:
                await asyncio.sleep(0.5)
                if self.lock.locked() or self.access.lock.locked():
                    continue
                async with self.lock, self.access.lock:
                    if self.closed or not self.user_control:
                        break
                    try:
                        await self.check_screen_readable()
                        shot = await self.run(self.backend().screenshot)
                        await self._emit_frame(shot.png, shot)
                    except Exception as exc:
                        logger.debug("computer live preview paused: {}", exc)
                        continue
        finally:
            self._preview_task = None

    # --------------------------------------------------------------- budget

    def begin_action(self, turn_id: str | None) -> str | None:
        """Count one action against the per-turn budget; returns a refusal or None."""
        if turn_id != self._turn_id:
            self._turn_id = turn_id
            self._turn_actions = 0
        self._turn_actions += 1
        limit = int(getattr(self.config, "max_actions_per_turn", 0) or 0)
        if limit and self._turn_actions > limit:
            return (
                f"Error: {limit} computer actions in this turn already. Stop, report what was "
                "done and what remains, and let the user decide whether to continue."
            )
        return None

    # -------------------------------------------------------------- mapping

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Model (screenshot) pixel -> screen pixel."""
        shot, size = self.last_shot, self.model_size
        if shot is None or size is None:
            raise ComputerError("take a screenshot first so coordinates have a reference image")
        mw, mh = size
        if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x < mw and 0 <= y < mh):
            raise ComputerError(f"coordinate must be inside the {mw}x{mh} screenshot")
        sx = shot.left + x * shot.width / max(1, mw)
        sy = shot.top + y * shot.height / max(1, mh)
        return min(shot.left + shot.width - 1, round(sx)), min(shot.top + shot.height - 1, round(sy))

    def to_model(self, sx: float, sy: float) -> tuple[int, int]:
        shot, size = self.last_shot, self.model_size
        if shot is None or size is None:
            return int(round(sx)), int(round(sy))
        mw, mh = size
        return (
            int(round((sx - shot.left) * mw / max(1, shot.width))),
            int(round((sy - shot.top) * mh / max(1, shot.height))),
        )

    def scale_note(self) -> str:
        shot, size = self.last_shot, self.model_size
        if shot is None or size is None:
            return ""
        if (shot.width, shot.height) == size:
            return f"screen {shot.width}x{shot.height}"
        return (
            f"screenshot {size[0]}x{size[1]} = screen {shot.width}x{shot.height}; "
            "use screenshot coordinates"
        )

    # ------------------------------------------------------------ capture

    async def capture(self) -> tuple[bytes, tuple[int, int]]:
        """Take a screenshot, scale it for the model, remember the mapping."""
        backend = self.backend()
        await self.check_screen_readable()
        shot: Screenshot = await self.run(backend.screenshot)
        max_w = int(getattr(self.config, "screenshot_max_width", 1366) or 1366)
        max_h = int(getattr(self.config, "screenshot_max_height", 768) or 768)
        scaled, size = await self.run(_scale_png, shot.png, shot.width, shot.height, max_w, max_h)
        self.last_shot = shot
        self.model_size = size
        self.last_scaled_png = scaled
        await self._emit_frame(scaled, shot)
        return scaled, size

    def save_screenshot(self, png: bytes, tag: str = "shot") -> Path | None:
        directory = self._artifact_dir()
        if directory is None:
            return None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{tag}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png"
            path.write_bytes(png)
            return path
        except OSError:
            return None

    def _artifact_dir(self) -> Path | None:
        try:
            from navin.utils.artifacts import artifact_directory

            root = artifact_directory(
                str(getattr(self.config, "screenshot_dir", "computer") or "computer")
            )
        except Exception:  # noqa: BLE001
            return None
        return root / self.id

    def enable_audit(self) -> None:
        if not bool(getattr(self.config, "audit_log", True)):
            self.audit.directory = None
            return
        self.audit.keep_screenshots = bool(getattr(self.config, "audit_screenshots", True))
        if self.audit.directory is None:
            self.audit.directory = self._artifact_dir()
            if self.audit.directory is not None:
                self.audit.write_meta(
                    {
                        "session": self.id,
                        "chat": self.session_key,
                        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "ask": str(getattr(self.config, "ask", "")),
                        "session_mode": str(getattr(self.config, "session_mode", "shared")),
                        "backend": str(getattr(self.config, "backend", "auto")),
                        "display": getattr(self.config, "display", None),
                    }
                )

    # ------------------------------------------------------------ helpers

    def window_in_model_coords(self, w: WindowInfo) -> dict[str, Any]:
        x, y = self.to_model(w.left, w.top)
        x2, y2 = self.to_model(w.left + w.width, w.top + w.height)
        out = w.as_dict()
        out.update({"x": x, "y": y, "w": max(0, x2 - x), "h": max(0, y2 - y)})
        return out

    def element_in_model_coords(self, el: UIElement) -> dict[str, Any]:
        x, y = self.to_model(el.left, el.top)
        x2, y2 = self.to_model(el.left + el.width, el.top + el.height)
        cx, cy = self.to_model(*el.center)
        out: dict[str, Any] = {
            "ref": el.ref,
            "role": el.role,
            "name": el.name,
            "center": [cx, cy],
            "box": [x, y, max(0, x2 - x), max(0, y2 - y)],
        }
        if el.value:
            out["value"] = el.value
        if not el.enabled:
            out["disabled"] = True
        if el.focused:
            out["focused"] = True
        return out

    def record(self, action: str, kwargs: dict[str, Any], result: str) -> None:
        self.history.append(
            {
                "t": round(time.time(), 3),
                "action": action,
                "args": {k: v for k, v in kwargs.items() if k != "action" and v is not None},
                "result": result[:200],
            }
        )

    # ----------------------------------------------------------- live view

    def set_live_target(self, bus: Any) -> None:
        if bus is None or not bool(getattr(self.config, "live_view", True)):
            if self._live_started:
                self._emit_live("exit")
                self._live_started = False
            self._live_bus = None
            self._live_chat_id = None
            return
        from navin.agent.tools.context import current_request_context

        ctx = current_request_context()
        if ctx is None or ctx.channel != "websocket" or not ctx.chat_id:
            return
        self._live_bus = bus
        self._live_chat_id = ctx.chat_id

    def emit_live_action(self, line: str) -> None:
        if line:
            self._emit_live("action", action=line[:300])

    async def _emit_frame(self, png: bytes, shot: Screenshot) -> None:
        if self._live_bus is None or not self._live_chat_id:
            return
        now = time.monotonic()
        min_ms = int(getattr(self.config, "live_view_min_frame_ms", 250) or 0)
        if now - self._live_last_frame < min_ms / 1000:
            return
        self._live_last_frame = now
        try:
            quality = int(getattr(self.config, "live_view_quality", 55) or 55)
            max_w = int(getattr(self.config, "live_view_max_width", 1152) or 1152)
            jpeg, (w, h) = await self.run(_png_to_jpeg, png, max_w, quality)
        except Exception as exc:  # noqa: BLE001
            logger.debug("computer live view: encode failed: {}", exc)
            return
        self.live_shot = shot
        if not self._live_started:
            self._live_started = True
            self._emit_live("start")
        self._emit_live("frame", data=base64.b64encode(jpeg).decode("ascii"), width=w, height=h)

    def _emit_live(
        self,
        phase: str,
        *,
        action: str | None = None,
        data: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        if self._live_bus is None or not self._live_chat_id:
            return
        try:
            from navin.bus.outbound_events import AgentBrowserEvent, outbound_message_for_event

            self._live_bus.outbound.put_nowait(
                outbound_message_for_event(
                    channel="websocket",
                    chat_id=self._live_chat_id,
                    event=AgentBrowserEvent(
                        browser_id=f"desktop-{self.id}",
                        phase=phase,
                        url="desktop://" + (self._backend.label if self._backend else "screen"),
                        title="Desktop",
                        action=action,
                        data=data,
                        width=width,
                        height=height,
                        user_control=self.user_control,
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - the live view must never break the tool
            logger.debug("computer live view: emit failed: {}", exc)

    def screen_info(self) -> ScreenInfo:
        return self.backend().screen()


def _scale_png(
    png: bytes, width: int, height: int, max_w: int, max_h: int
) -> tuple[bytes, tuple[int, int]]:
    scale = min(1.0, max_w / max(1, width), max_h / max(1, height))
    if scale >= 1.0:
        return png, (width, height)
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        image.load()
        size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        resized = image.convert("RGB").resize(size, Image.LANCZOS)
    buffer = io.BytesIO()
    resized.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue(), size


def _png_to_jpeg(png: bytes, max_w: int, quality: int) -> tuple[bytes, tuple[int, int]]:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        image.load()
        w, h = image.size
        if w > max_w:
            nh = max(1, int(round(h * max_w / w)))
            image = image.resize((max_w, nh), Image.BILINEAR)
            w, h = image.size
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue(), (w, h)


def crop_zoom(
    png: bytes, box: tuple[int, int, int, int], *, max_w: int, max_h: int
) -> tuple[bytes, tuple[int, int]]:
    """Crop ``box`` (x0, y0, x1, y1) out of *png* and upscale it to fit the model box."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        image.load()
        w, h = image.size
        x0, y0, x1, y1 = box
        x0, x1 = sorted((max(0, min(w, x0)), max(0, min(w, x1))))
        y0, y1 = sorted((max(0, min(h, y0)), max(0, min(h, y1))))
        if x1 - x0 < 4 or y1 - y0 < 4:
            raise ComputerError("zoom region is too small or outside the screenshot")
        region = image.crop((x0, y0, x1, y1))
        rw, rh = region.size
        scale = min(max_w / rw, max_h / rh)
        if scale > 1.0:
            region = region.resize((int(rw * scale), int(rh * scale)), Image.LANCZOS)
        buffer = io.BytesIO()
        region.convert("RGB").save(buffer, format="PNG", optimize=False)
        return buffer.getvalue(), region.size
