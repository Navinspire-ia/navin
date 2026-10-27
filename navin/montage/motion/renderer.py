# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Node-free deterministic motion renderer (Playwright Chromium + ffmpeg).

A composition is an HTML page that implements the seek contract
(``window.__navinSeek(t)``, ``window.__navinDuration``, ``window.__navinReady``;
see ``assets/navin-motion.js``). For every output frame the renderer:

1. calls ``__navinSeek(frame / fps)``; the injected time controller
   (``assets/time-controller.js``) has frozen ``performance.now``, ``Date``,
   ``requestAnimationFrame`` and ``Math.random``, so the page only moves when
   told to and every frame is a pure function of its timestamp;
2. captures the viewport with CDP ``Page.captureScreenshot`` at the exact
   target size (device scale factor 1);
3. pipes the image to ffmpeg (``image2pipe``) which encodes H.264 High
   (crf 16, preset slow, yuv420p, BT.709, CFR) or, for overlays, ProRes 4444 /
   VP9 WebM with alpha.

Long renders split the frame range across N pages (one browser context and
one ffmpeg each) and join the segments with the concat demuxer (stream copy).
Nothing but the loopback server is reachable from the page: every other
request is aborted, so a template can never depend on a CDN at render time.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.montage.motion.brand import BrandKit, load_brand_kit
from navin.montage.motion.media import (
    PathResolver,
    brand_payload,
    default_resolver,
    prepare_props,
)
from navin.montage.motion.server import MotionServer
from navin.montage.motion.vendor import TEMPLATES_DIR, assets_dir
from navin.utils.proc import no_window_kwargs

FORMATS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4k": (3840, 2160),
}

CODECS: dict[str, dict[str, Any]] = {
    "h264": {"suffix": ".mp4", "alpha": False},
    "prores4444": {"suffix": ".mov", "alpha": True},
    "webm_alpha": {"suffix": ".webm", "alpha": True},
}

DEFAULT_CRF = 16
DEFAULT_PRESET = "slow"
_READY_TIMEOUT_MS = 90_000
_SEEK_TIMEOUT_S = 60.0
_MAX_SIDE = 3840
_MIN_SIDE = 16

ProgressFn = Callable[["MotionProgress"], None]


class MotionRenderError(RuntimeError):
    """A composition failed to load, seek or encode."""

    def __init__(self, message: str, *, console_errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.console_errors = list(console_errors or [])


@dataclass(slots=True)
class MotionProgress:
    phase: str
    frames_done: int = 0
    total_frames: int = 0

    @property
    def fraction(self) -> float | None:
        if not self.total_frames:
            return None
        return max(0.0, min(1.0, self.frames_done / self.total_frames))

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "frames_done": self.frames_done,
            "total_frames": self.total_frames,
            "fraction": None if self.fraction is None else round(self.fraction, 4),
        }


@dataclass(slots=True)
class RenderSettings:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    duration: float | None = None
    codec: str = "h264"
    crf: int = DEFAULT_CRF
    preset: str = DEFAULT_PRESET
    workers: int | None = None
    image_format: str | None = None
    transparent: bool | None = None
    seed: int = 1337
    software_gl: bool = False
    # WebGL internal resolution factor (1 = full). Drafts/previews use 0.5.
    gl_scale: float = 1.0

    def validate(self) -> None:
        if self.codec not in CODECS:
            raise MotionRenderError(f"unknown codec {self.codec!r}; use one of {', '.join(CODECS)}")
        for side in (self.width, self.height):
            if not _MIN_SIDE <= int(side) <= _MAX_SIDE:
                raise MotionRenderError(f"width/height must be between {_MIN_SIDE} and {_MAX_SIDE}")
        if self.codec == "h264" and (self.width % 2 or self.height % 2):
            raise MotionRenderError("H.264 needs even width and height")
        if not 1 <= int(self.fps) <= 120:
            raise MotionRenderError("fps must be between 1 and 120")
        if self.duration is not None and not 0 < float(self.duration) <= 600:
            raise MotionRenderError("duration must be between 0 and 600 seconds")
        if not 0 <= int(self.crf) <= 51:
            raise MotionRenderError("crf must be between 0 and 51")

    @property
    def alpha(self) -> bool:
        if self.transparent is not None:
            return bool(self.transparent) and CODECS[self.codec]["alpha"]
        return bool(CODECS[self.codec]["alpha"])

    @property
    def capture_format(self) -> str:
        if self.alpha:
            return "png"
        fmt = (self.image_format or os.environ.get("NAVIN_MOTION_IMAGE_FORMAT") or "jpeg").lower()
        return "png" if fmt == "png" else "jpeg"


@dataclass(slots=True)
class _Composition:
    url: str
    init_script: str
    console_errors: list[str] = field(default_factory=list)


def chromium_args(software_gl: bool = False) -> list[str]:
    args = [
        "--hide-scrollbars",
        "--mute-audio",
        "--autoplay-policy=no-user-gesture-required",
        "--font-render-hinting=none",
        "--disable-lcd-text",
        "--force-color-profile=srgb",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--run-all-compositor-stages-before-draw",
        "--ignore-gpu-blocklist",
        "--enable-webgl",
    ]
    # WebGL falls back to SwiftShader when the host has no usable GPU (CI,
    # WSL, servers); a real GPU is used when present. Frames are a pure
    # function of time either way, so a render is repeatable on one machine.
    args.append("--enable-unsafe-swiftshader")
    if software_gl or os.environ.get("NAVIN_MOTION_SOFTWARE_GL") == "1":
        # Bit-identical pixels across machines, at roughly 3x the capture cost.
        args.append("--use-angle=swiftshader")
    return args


async def launch_browser(pw: Any, *, software_gl: bool = False) -> Any:
    """Playwright's Chromium, else the browser Navin prepares on demand."""
    args = chromium_args(software_gl)
    try:
        return await pw.chromium.launch(headless=True, args=args)
    except Exception as first:  # noqa: BLE001 - fall back to the managed browser
        try:
            from navin.browser_runtime import ensure_chromium

            executable = await ensure_chromium()
        except Exception as exc:  # noqa: BLE001
            raise MotionRenderError(
                "No Chromium is available to render motion templates. "
                "Install it from Montage setup and retry."
            ) from exc
        try:
            return await pw.chromium.launch(headless=True, args=args, executable_path=executable)
        except Exception as exc:  # noqa: BLE001
            raise MotionRenderError(f"Chromium could not start: {exc}") from first


def encoder_args(settings: RenderSettings, ffmpeg: str, output: Path) -> list[str]:
    fmt = settings.capture_format
    head = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "image2pipe",
        "-framerate",
        str(settings.fps),
        "-c:v",
        "png" if fmt == "png" else "mjpeg",
        "-i",
        "-",
    ]
    color_tags = [
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
    ]
    if settings.codec == "h264":
        vf = (
            "scale=in_color_matrix=bt601:out_color_matrix=bt709:out_range=tv"
            ":flags=accurate_rnd+full_chroma_int,format=yuv420p"
        )
        tail = [
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            settings.preset,
            "-crf",
            str(settings.crf),
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            *color_tags,
            "-r",
            str(settings.fps),
            "-movflags",
            "+faststart",
        ]
    elif settings.codec == "prores4444":
        tail = [
            "-vf",
            "scale=out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int,format=yuva444p10le",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-vendor",
            "apl0",
            "-pix_fmt",
            "yuva444p10le",
            *color_tags,
            "-r",
            str(settings.fps),
        ]
    else:  # webm_alpha
        tail = [
            "-vf",
            "format=yuva420p",
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuva420p",
            "-crf",
            str(max(settings.crf, 15)),
            "-b:v",
            "0",
            "-row-mt",
            "1",
            "-deadline",
            "good",
            "-cpu-used",
            "2",
            "-auto-alt-ref",
            "0",
            *color_tags,
            "-r",
            str(settings.fps),
        ]
    return [*head, *tail, str(output)]


def _time_controller_js(assets: Path) -> str:
    return (assets / "time-controller.js").read_text(encoding="utf-8")


def _init_script(
    assets: Path,
    *,
    settings: RenderSettings,
    duration: float | None,
    props: dict[str, Any],
    brand: dict[str, Any],
    assets_url: str,
) -> str:
    render_cfg = {
        "seed": int(settings.seed),
        "canvas": {
            "width": int(settings.width),
            "height": int(settings.height),
            "fps": int(settings.fps),
            "duration": float(duration) if duration else 0,
            "transparent": settings.alpha,
            "gl_scale": float(settings.gl_scale),
        },
    }
    return (
        f"window.__NAVIN_RENDER__ = {json.dumps(render_cfg)};\n"
        f"window.__NAVIN_PROPS__ = {json.dumps(props, ensure_ascii=False)};\n"
        f"window.__NAVIN_BRAND__ = {json.dumps(brand, ensure_ascii=False)};\n"
        f"window.__NAVIN_ASSETS__ = {json.dumps(assets_url)};\n"
        + _time_controller_js(assets)
    )


def default_workers(total_frames: int, settings: RenderSettings) -> int:
    if settings.workers:
        # An explicit request is honoured (tests, power users), one frame minimum each.
        return max(1, min(int(settings.workers), 8, total_frames))
    else:
        env = os.environ.get("NAVIN_MOTION_WORKERS")
        if env and env.isdigit():
            wanted = int(env)
        else:
            cpus = os.cpu_count() or 2
            wanted = max(1, min(4, cpus // 2))
            if settings.width * settings.height > 1920 * 1080:
                wanted = min(wanted, 2)
    # A worker pays a page load; keep at least ~1.5 s of frames per worker.
    per_worker = max(1, int(settings.fps * 1.5))
    return max(1, min(wanted, 8, total_frames // per_worker or 1))


def split_ranges(total: int, parts: int) -> list[tuple[int, int]]:
    parts = max(1, min(parts, total or 1))
    size, extra = divmod(total, parts)
    ranges: list[tuple[int, int]] = []
    start = 0
    for index in range(parts):
        end = start + size + (1 if index < extra else 0)
        if end > start:
            ranges.append((start, end))
        start = end
    return ranges


class _Encoder:
    """One ffmpeg process fed with images on stdin."""

    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.proc: asyncio.subprocess.Process | None = None
        self._stderr = bytearray()
        self._reader: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            **no_window_kwargs(),
        )
        self._reader = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while True:
            chunk = await self.proc.stderr.read(4096)
            if not chunk:
                return
            self._stderr += chunk
            if len(self._stderr) > 64_000:
                del self._stderr[:-32_000]

    async def write(self, data: bytes) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        try:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            await self._finish_quietly()
            raise MotionRenderError(f"ffmpeg stopped accepting frames: {self.error_text()}") from exc

    async def close(self) -> None:
        assert self.proc is not None
        if self.proc.stdin is not None and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
            try:
                await self.proc.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        code = await self.proc.wait()
        if self._reader is not None:
            await self._reader
        if code != 0:
            raise MotionRenderError(f"ffmpeg failed ({code}): {self.error_text()}")

    async def kill(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            await self._finish_quietly()

    async def _finish_quietly(self) -> None:
        if self.proc is None:
            return
        try:
            await asyncio.wait_for(self.proc.wait(), 10)
        except TimeoutError:
            pass
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._reader, 5)
            except TimeoutError:
                pass

    def error_text(self) -> str:
        text = self._stderr.decode("utf-8", "replace").strip()
        return "\n".join(text.splitlines()[-8:]) or "no details"


class MotionSession:
    """Browser + loopback server shared by every page of one render job."""

    def __init__(
        self,
        settings: RenderSettings,
        *,
        resolve: PathResolver | None = None,
        base_dir: Path | None = None,
        ffmpeg: str | None = None,
    ) -> None:
        self.settings = settings
        self.resolve = resolve or default_resolver(base_dir)
        self.ffmpeg = ffmpeg
        self.assets: Path | None = None
        self.server: MotionServer | None = None
        self._pw: Any = None
        self._browser: Any = None
        self._contexts: list[Any] = []

    async def __aenter__(self) -> MotionSession:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise MotionRenderError("Playwright is not installed; motion rendering needs it.") from exc
        if self.ffmpeg is None:
            from navin.montage.detect import find_ffmpeg

            self.ffmpeg = await asyncio.to_thread(find_ffmpeg)
        self.assets = await asyncio.to_thread(assets_dir)
        self.server = MotionServer(self.assets, TEMPLATES_DIR).start()
        try:
            self._pw = await async_playwright().start()
            self._browser = await launch_browser(self._pw, software_gl=self.settings.software_gl)
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        for context in self._contexts:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass
        self._contexts.clear()
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None
        if self.server is not None:
            self.server.stop()
            self.server = None

    async def prepare(
        self,
        *,
        template: str | None,
        html_path: Path | None,
        props: dict[str, Any] | None,
        brand: BrandKit,
        duration: float | None,
    ) -> _Composition:
        assert self.server is not None and self.assets is not None
        if template:
            from navin.montage.motion.catalog import get_template

            info = get_template(template)
            if info is None:
                raise MotionRenderError(f"unknown motion template {template!r}")
            url = self.server.template_url(info.name)
        elif html_path is not None:
            html = Path(html_path).resolve()
            if not html.is_file() or html.suffix.lower() not in {".html", ".htm"}:
                raise MotionRenderError(f"composition HTML not found: {html_path}")
            url = f"{self.server.register_dir(html.parent)}/{html.name}"
        else:
            raise MotionRenderError("pass a template name or a composition HTML path")
        prepared, _used = await prepare_props(
            props or {},
            server=self.server,
            resolve=self.resolve,
            fps=self.settings.fps,
            max_dim=max(self.settings.width, self.settings.height),
            ffmpeg=self.ffmpeg,
        )
        script = _init_script(
            self.assets,
            settings=self.settings,
            duration=duration,
            props=prepared,
            brand=brand_payload(brand, self.server),
            assets_url=self.server.base_url + "/assets/",
        )
        return _Composition(url=url, init_script=script)

    async def open_page(self, comp: _Composition) -> tuple[Any, Any]:
        """A loaded page that reached ``__navinReady`` plus its CDP session."""
        assert self.server is not None and self._browser is not None
        settings = self.settings
        context = await self._browser.new_context(
            viewport={"width": settings.width, "height": settings.height},
            device_scale_factor=1,
            color_scheme="dark",
            reduced_motion="no-preference",
            service_workers="block",
        )
        self._contexts.append(context)
        base = self.server.base_url

        async def block(route: Any) -> None:
            comp.console_errors.append(f"blocked network request: {route.request.url[:200]}")
            await route.abort()

        await context.route(lambda url: not url.startswith(base + "/"), block)
        await context.add_init_script(script=comp.init_script)
        page = await context.new_page()

        def on_console(msg: Any) -> None:
            if msg.type == "error":
                comp.console_errors.append(msg.text[:2000])

        page.on("console", on_console)
        page.on("pageerror", lambda exc: comp.console_errors.append(f"page error: {exc}"[:2000]))
        cdp = await context.new_cdp_session(page)
        if settings.alpha:
            await cdp.send(
                "Emulation.setDefaultBackgroundColorOverride",
                {"color": {"r": 0, "g": 0, "b": 0, "a": 0}},
            )
        await page.goto(comp.url, wait_until="load", timeout=_READY_TIMEOUT_MS)
        try:
            await page.wait_for_function(
                "() => window.__navinReady === true || !!window.__navinError",
                timeout=_READY_TIMEOUT_MS,
                polling=50,
            )
        except Exception as exc:  # noqa: BLE001 - playwright timeout
            raise MotionRenderError(
                "the composition never became ready (window.__navinReady)",
                console_errors=comp.console_errors,
            ) from exc
        error = await page.evaluate("() => window.__navinError || null")
        if error:
            raise MotionRenderError(
                f"the composition failed to set up: {str(error)[:1500]}",
                console_errors=comp.console_errors,
            )
        has_seek = await page.evaluate("() => typeof window.__navinSeek === 'function'")
        if not has_seek:
            raise MotionRenderError(
                "the composition does not implement window.__navinSeek(t)",
                console_errors=comp.console_errors,
            )
        return page, cdp

    async def capture(self, page: Any, cdp: Any, t: float) -> bytes:
        """Seek to *t* seconds and return the encoded frame."""
        try:
            await asyncio.wait_for(
                page.evaluate("t => window.__navinSeek(t)", t), timeout=_SEEK_TIMEOUT_S
            )
        except TimeoutError as exc:
            raise MotionRenderError(f"seeking to {t:.3f}s timed out") from exc
        fmt = self.settings.capture_format
        params: dict[str, Any] = {
            "format": fmt,
            "clip": {
                "x": 0,
                "y": 0,
                "width": self.settings.width,
                "height": self.settings.height,
                "scale": 1,
            },
            "fromSurface": True,
            "captureBeyondViewport": False,
        }
        if fmt == "jpeg":
            params["quality"] = 95
        else:
            params["optimizeForSpeed"] = True
        result = await cdp.send("Page.captureScreenshot", params)
        return base64.b64decode(result["data"])


async def _concat(ffmpeg: str, segments: list[Path], output: Path, workdir: Path) -> None:
    listing = workdir / "segments.txt"
    await asyncio.to_thread(
        listing.write_text, "".join(f"file '{seg.name}'\n" for seg in segments), encoding="utf-8",
    )
    argv = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        listing.name,
        "-c",
        "copy",
    ]
    if output.suffix.lower() in {".mp4", ".mov"}:
        argv += ["-movflags", "+faststart"]
    argv.append(str(output))
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(workdir),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        **no_window_kwargs(),
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise MotionRenderError(
            "joining the rendered segments failed: "
            + (err or b"").decode("utf-8", "replace").strip()[-800:]
        )


async def render_motion(
    *,
    output: str | Path,
    template: str | None = None,
    html_path: str | Path | None = None,
    props: dict[str, Any] | None = None,
    brand: Any = None,
    settings: RenderSettings | None = None,
    resolve: PathResolver | None = None,
    base_dir: Path | None = None,
    on_progress: ProgressFn | None = None,
    cancel: asyncio.Event | None = None,
    collect_hashes: bool = False,
    ffmpeg: str | None = None,
) -> dict[str, Any]:
    """Render a template (or custom HTML composition) to a video file.

    Returns a JSON-ready dict: ``ok``, ``output``, ``duration_s``, ``frames``,
    ``width``/``height``/``fps``/``codec``, ``workers``, ``console_errors`` and
    optionally ``frame_hashes`` (sha256 of every captured image, in order).
    Raises nothing for render failures: ``ok`` is ``False`` with ``error``.
    """
    settings = settings or RenderSettings()
    started = time.monotonic()
    out_path = Path(output).expanduser()
    if out_path.suffix.lower() != CODECS.get(settings.codec, {}).get("suffix", out_path.suffix):
        out_path = out_path.with_suffix(CODECS.get(settings.codec, {"suffix": ".mp4"})["suffix"])
    errors: list[str] = []

    def report(progress: MotionProgress) -> None:
        if on_progress is None:
            return
        try:
            on_progress(progress)
        except Exception:  # noqa: BLE001 - a UI callback must never break a render
            pass

    try:
        settings.validate()
        kit = brand if isinstance(brand, BrandKit) else load_brand_kit(brand, base_dir=base_dir)
        report(MotionProgress("preparing"))
        async with MotionSession(settings, resolve=resolve, base_dir=base_dir, ffmpeg=ffmpeg) as session:
            if not session.ffmpeg:
                raise MotionRenderError(
                    "ffmpeg is not installed. Run montage(action=setup, package=ffmpeg) first."
                )
            comp = await session.prepare(
                template=template,
                html_path=Path(html_path) if html_path else None,
                props=props,
                brand=kit,
                duration=settings.duration,
            )
            errors = comp.console_errors
            first_page, first_cdp = await session.open_page(comp)
            duration = settings.duration or float(
                await first_page.evaluate("() => Number(window.__navinDuration) || 0") or 0
            )
            if not duration or duration <= 0:
                raise MotionRenderError("the composition did not declare a duration")
            total = max(1, round(duration * settings.fps))
            ranges = split_ranges(total, default_workers(total, settings))
            report(MotionProgress("rendering", 0, total))

            pages: list[tuple[Any, Any]] = [(first_page, first_cdp)]
            if len(ranges) > 1:
                pages += await asyncio.gather(*(session.open_page(comp) for _ in ranges[1:]))

            out_path.parent.mkdir(parents=True, exist_ok=True)
            workdir = Path(tempfile.mkdtemp(prefix=".navin-motion-", dir=str(out_path.parent)))
            hashes: list[list[str]] = [[] for _ in ranges]
            done = [0]
            step = max(1, total // 200)
            suffix = CODECS[settings.codec]["suffix"]
            segments = [workdir / f"seg{index:03d}{suffix}" for index in range(len(ranges))]

            async def run_worker(index: int) -> None:
                page, cdp = pages[index]
                start, end = ranges[index]
                encoder = _Encoder(encoder_args(settings, session.ffmpeg or "ffmpeg", segments[index]))
                await encoder.start()
                try:
                    for frame in range(start, end):
                        if cancel is not None and cancel.is_set():
                            raise asyncio.CancelledError
                        data = await session.capture(page, cdp, frame / settings.fps)
                        if collect_hashes:
                            hashes[index].append(hashlib.sha256(data).hexdigest())
                        await encoder.write(data)
                        done[0] += 1
                        if done[0] % step == 0 or done[0] == total:
                            report(MotionProgress("rendering", done[0], total))
                    await encoder.close()
                except BaseException:
                    await encoder.kill()
                    raise

            try:
                await asyncio.gather(*(run_worker(i) for i in range(len(ranges))))
                report(MotionProgress("encoding", total, total))
                staging = out_path.with_name(f".{out_path.stem}.part{suffix}")
                if len(segments) == 1:
                    os.replace(segments[0], staging)
                else:
                    await _concat(session.ffmpeg or "ffmpeg", segments, staging, workdir)
                os.replace(staging, out_path)
            finally:
                await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)
        report(MotionProgress("done", total, total))
        result: dict[str, Any] = {
            "ok": True,
            "output": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "template": template,
            "duration_s": round(total / settings.fps, 3),
            "frames": total,
            "width": settings.width,
            "height": settings.height,
            "fps": settings.fps,
            "codec": settings.codec,
            "alpha": settings.alpha,
            "workers": len(ranges),
            "elapsed_s": round(time.monotonic() - started, 2),
            "console_errors": errors[:20],
        }
        if collect_hashes:
            result["frame_hashes"] = [h for chunk in hashes for h in chunk]
        return result
    except asyncio.CancelledError:
        if cancel is not None and cancel.is_set():
            return {"ok": False, "cancelled": True, "error": "render cancelled"}
        raise
    except MotionRenderError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "template": template,
            "console_errors": (exc.console_errors or errors)[:20],
        }
    except Exception as exc:  # noqa: BLE001 - surface as a tool result, not a crash
        from navin.montage.motion.brand import BrandKitError
        from navin.montage.motion.media import MotionMediaError

        if isinstance(exc, (BrandKitError, MotionMediaError)):
            return {"ok": False, "error": str(exc), "template": template}
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "template": template,
            "console_errors": errors[:20],
        }


async def capture_stills(
    times: list[float],
    *,
    template: str | None = None,
    html_path: str | Path | None = None,
    props: dict[str, Any] | None = None,
    brand: Any = None,
    settings: RenderSettings | None = None,
    resolve: PathResolver | None = None,
    base_dir: Path | None = None,
) -> tuple[list[bytes], list[str]]:
    """PNG/JPEG frames at *times* (seconds) plus the page's console errors.

    Used for thumbnails, previews and tests. Raises :class:`MotionRenderError`.
    """
    settings = settings or RenderSettings(image_format="png")
    settings.validate()
    kit = brand if isinstance(brand, BrandKit) else load_brand_kit(brand, base_dir=base_dir)
    async with MotionSession(settings, resolve=resolve, base_dir=base_dir) as session:
        comp = await session.prepare(
            template=template,
            html_path=Path(html_path) if html_path else None,
            props=props,
            brand=kit,
            duration=settings.duration,
        )
        page, cdp = await session.open_page(comp)
        frames = [await session.capture(page, cdp, float(t)) for t in times]
        return frames, list(comp.console_errors)


__all__ = [
    "CODECS",
    "FORMATS",
    "MotionProgress",
    "MotionRenderError",
    "MotionSession",
    "RenderSettings",
    "capture_stills",
    "chromium_args",
    "encoder_args",
    "render_motion",
    "split_ranges",
]
