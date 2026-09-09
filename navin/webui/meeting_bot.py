# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Autonomous meeting bot: joins Zoom / Google Meet / Teams as a browser guest.

The bot drives a headless Chromium (Playwright) to the meeting's web client,
fills the guest name, asks to join, and waits in the lobby until the host
admits it. Once inside, an injected WebAudio graph taps every remote
audio/video element, mixes them to 16 kHz mono PCM, and ships 15-second WAV
segments back to Python where the configured STT provider transcribes them.
The WebUI polls ``bot_status`` and appends the segments to the meeting
transcript with the usual [mm:ss] timecodes.

No third-party bot service is involved: this is Navin's own browser session,
visible in the participant list under the configured display name.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import re
import struct
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from navin.webui.meeting_api import MeetingError

#: Safety net: a forgotten bot leaves the meeting on its own after 4 hours.
_MAX_SESSION_S = 4 * 3600
def _segment_ms() -> int:
    """Configured capture chunk, defaulting to five seconds."""
    try:
        return max(1_000, min(60_000, int(os.environ.get("NAVIN_MEETING_CHUNK_MS", "5000"))))
    except ValueError:
        return 5_000


_SEGMENT_MS = _segment_ms()

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

_INSTALL_HINT = (
    "playwright is not installed on the gateway host. Run: "
    "pip install playwright && playwright install chromium"
)


async def ensure_chromium() -> str | None:
    """Return a usable Chromium path, downloading Playwright's if needed.

    Reuses the montage installer so the bot behaves the same on Linux, macOS
    and Windows and inside a packaged build (``navin python -m playwright
    install`` vs the plain interpreter). Returns the browser path, or ``None``
    when nothing is available and the download failed.
    """
    from navin.agent.tools.browser import _installed_chromium

    existing = _installed_chromium()
    if existing:
        return existing
    try:
        from navin.montage.install import install_chromium

        result = await install_chromium()
    except Exception as exc:  # noqa: BLE001 - installer must never crash the bot
        logger.warning("meeting bot chromium install failed: {}", exc)
        return _installed_chromium()
    if result.get("ok"):
        return result.get("path") or _installed_chromium()
    return _installed_chromium()

# Injected once the join flow finished. Taps every audio/video element that
# carries a remote MediaStream, mixes to 16 kHz mono, and posts one WAV
# segment (base64) roughly every _SEGMENT_MS. Silent segments still advance
# the clock but are not posted, so lobby time costs no STT calls.
_CAPTURE_JS = """
() => {
  if (window.__navinBotStarted) return true;
  window.__navinBotStarted = true;
  const ctx = new AudioContext({ sampleRate: 16000 });
  const mix = ctx.createGain();
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const sink = ctx.createGain();
  sink.gain.value = 0;
  mix.connect(proc);
  proc.connect(sink);
  sink.connect(ctx.destination);
  let buffers = [];
  let samples = 0;
  let sources = 0;
  let clockMs = 0;
  proc.onaudioprocess = (e) => {
    buffers.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    samples += e.inputBuffer.length;
  };
  const attached = new WeakSet();
  const attach = () => {
    document.querySelectorAll("audio, video").forEach((el) => {
      const s = el.srcObject;
      if (!s || attached.has(s)) return;
      try {
        if (!s.getAudioTracks || !s.getAudioTracks().length) return;
        ctx.createMediaStreamSource(s).connect(mix);
        attached.add(s);
        sources += 1;
        if (window.__navinBotSources) window.__navinBotSources(sources);
      } catch (err) {}
    });
    if (ctx.state !== "running") ctx.resume().catch(() => {});
  };
  setInterval(attach, 1000);
  const flush = () => {
    if (!samples) return;
    const total = samples;
    const parts = buffers;
    buffers = [];
    samples = 0;
    const offsetMs = clockMs;
    const durationMs = Math.round((total / 16000) * 1000);
    clockMs += durationMs;
    const pcm = new Float32Array(total);
    let off = 0;
    for (const p of parts) { pcm.set(p, off); off += p.length; }
    let peak = 0;
    for (let i = 0; i < pcm.length; i += 16) {
      const v = Math.abs(pcm[i]);
      if (v > peak) peak = v;
    }
    if (peak < 0.004) return; // silence: skip the STT call
    const view = new DataView(new ArrayBuffer(44 + total * 2));
    const w = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
    w(0, "RIFF"); view.setUint32(4, 36 + total * 2, true); w(8, "WAVE");
    w(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true); view.setUint32(24, 16000, true);
    view.setUint32(28, 32000, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); w(36, "data"); view.setUint32(40, total * 2, true);
    for (let i = 0; i < total; i++) {
      const v = Math.max(-1, Math.min(1, pcm[i]));
      view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
    }
    const bytes = new Uint8Array(view.buffer);
    let bin = "";
    const STEP = 0x8000;
    for (let i = 0; i < bytes.length; i += STEP) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + STEP));
    }
    if (window.__navinBotSegment) window.__navinBotSegment(btoa(bin), offsetMs, durationMs);
  };
  setInterval(flush, %SEGMENT_MS%);
  return true;
}
""".replace("%SEGMENT_MS%", str(_SEGMENT_MS))


def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "meet.google.com" in host:
        return "meet"
    if "zoom.us" in host or re.search(r"\bzoom\b", host):
        return "zoom"
    if "teams.microsoft.com" in host or "teams.live.com" in host:
        return "teams"
    return "unknown"


def zoom_web_client_url(url: str) -> str:
    """Rewrite a Zoom invite link to the browser web client.

    ``https://us05web.zoom.us/j/123?pwd=x`` becomes
    ``https://us05web.zoom.us/wc/join/123?pwd=x`` - the page that works
    without the desktop app.
    """
    parsed = urlparse(url)
    m = re.search(r"/j/(\d+)", parsed.path)
    if not m:
        return url
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{parsed.netloc}/wc/join/{m.group(1)}{query}"


def _silent_wav_path() -> str:
    """A one-second silent WAV used as the bot's fake microphone.

    Chromium's default fake audio device emits a test tone that everyone in
    the meeting would hear; a silent capture file keeps the bot mute.
    """
    path = Path(tempfile.gettempdir()) / "navin-meetingbot-silence.wav"
    if not path.exists():
        rate = 16000
        pcm = b"\x00\x00" * rate
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16,
            1, 1, rate, rate * 2, 2, 16, b"data", len(pcm),
        )
        path.write_bytes(header + pcm)
    return str(path)


async def _click_first(page: Any, selectors: list[str], timeout_ms: int = 4000) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            await locator.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


async def _fill_first(page: Any, selectors: list[str], value: str, timeout_ms: int = 6000) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            await locator.fill(value, timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


class MeetingBot:
    """One autonomous browser session inside one meeting."""

    def __init__(self, bot_id: str, url: str, name: str, language: str = ""):
        self.bot_id = bot_id
        self.url = url.strip()
        self.name = name.strip() or "Navin AI"
        self.language = language.strip() or None
        self.platform = detect_platform(self.url)
        self.state = "starting"
        self.error: str | None = None
        self.sources = 0
        self.segments: list[dict[str, Any]] = []
        self.started_at = time.time()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stt_lock = asyncio.Lock()
        self._browser: Any = None
        self._pw: Any = None

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        self._persist()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._task, timeout=20)
        if self.state not in ("ended", "error"):
            self.state = "ended"
        self._persist()

    @property
    def active(self) -> bool:
        return self.state in ("starting", "joining", "waiting", "live")

    def status(self, cursor: int = 0) -> dict[str, Any]:
        cursor = max(0, min(cursor, len(self.segments)))
        return {
            "bot_id": self.bot_id,
            "platform": self.platform,
            "state": self.state,
            "error": self.error or "",
            "sources": self.sources,
            "elapsed_s": int(time.time() - self.started_at),
            "cursor": len(self.segments),
            "segments": self.segments[cursor:],
        }

    def _persist(self) -> None:
        """Persist status without storing invite URLs or credentials."""
        from navin.meetings.store import default_meeting_store

        default_meeting_store().save_bot_state(
            self.bot_id,
            {
                **self.status(),
                "name": self.name,
                "language": self.language or "",
                "started_at": self.started_at,
            },
        )

    # ---- browser session ----------------------------------------------

    async def _run(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.state = "error"
            self.error = _INSTALL_HINT
            self._persist()
            return
        try:
            # First run on a fresh install has no browser: fetch Playwright's
            # Chromium (or reuse a system Chrome/Edge) before launching.
            chromium_path = await ensure_chromium()
            self._pw = await async_playwright().start()
            launch_args = [
                "--autoplay-policy=no-user-gesture-required",
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                f"--use-file-for-fake-audio-capture={_silent_wav_path()}",
                "--disable-blink-features=AutomationControlled",
                # Headless Chromium in a container / CI often lacks a usable
                # sandbox; the montage and browser stacks run without it too.
                "--no-sandbox",
            ]
            try:
                self._browser = await self._pw.chromium.launch(
                    headless=True, args=launch_args
                )
            except Exception as exc:
                message = str(exc)
                if (
                    "Executable doesn't exist" not in message
                    and "playwright install" not in message
                ):
                    raise
                # Playwright has no browser of its own (packaged build, or the
                # user only has a system Chrome). Point it at whatever is here.
                if not chromium_path:
                    raise MeetingError(
                        "no Chromium is available on the gateway host and the "
                        "automatic download failed. Install Google Chrome, Edge "
                        "or Chromium, or run: playwright install chromium",
                        status=503,
                    ) from exc
                logger.info("meeting bot using system Chromium at {}", chromium_path)
                self._browser = await self._pw.chromium.launch(
                    headless=True, args=launch_args, executable_path=chromium_path
                )
            context = await self._browser.new_context(
                user_agent=_UA,
                viewport={"width": 1280, "height": 720},
                locale="fr-FR",
            )
            await context.grant_permissions(["microphone", "camera"])
            page = await context.new_page()
            await page.expose_function("__navinBotSegment", self._on_segment)
            await page.expose_function("__navinBotSources", self._on_sources)

            self.state = "joining"
            self._persist()
            if self.platform == "meet":
                await self._join_meet(page)
            elif self.platform == "zoom":
                await self._join_zoom(page)
            elif self.platform == "teams":
                await self._join_teams(page)
            else:
                raise MeetingError("unsupported meeting link", status=400)

            # The capture graph runs from the lobby on: the first remote
            # audio source flips the state to live (= the host admitted us).
            await page.evaluate(_CAPTURE_JS)
            self.state = "waiting"
            self._persist()
            await self._debug_screenshot(page, "joined")

            deadline = time.monotonic() + _MAX_SESSION_S
            while not self._stop_event.is_set() and time.monotonic() < deadline:
                if page.is_closed():
                    break
                await asyncio.sleep(1)
        except MeetingError as e:
            self.state = "error"
            self.error = e.message
        except Exception as e:  # noqa: BLE001 - joining foreign UIs fails in many ways
            self.state = "error"
            self.error = str(e)[:400]
            logger.warning("meeting bot {} failed: {}", self.bot_id, e)
        finally:
            if self.state != "error":
                self.state = "ended"
            with contextlib.suppress(Exception):
                if self._browser:
                    await self._browser.close()
            with contextlib.suppress(Exception):
                if self._pw:
                    await self._pw.stop()
            self._persist()

    async def _debug_screenshot(self, page: Any, tag: str) -> None:
        with contextlib.suppress(Exception):
            path = Path(tempfile.gettempdir()) / f"navin-meetingbot-{self.bot_id}-{tag}.png"
            await page.screenshot(path=str(path))

    # ---- join flows ----------------------------------------------------

    async def _join_meet(self, page: Any) -> None:
        await page.goto(self.url, wait_until="domcontentloaded", timeout=60_000)
        # Cookie consent interstitial (consent.google.com) on fresh profiles.
        await _click_first(page, [
            'button:has-text("Accept all")',
            'button:has-text("Tout accepter")',
            'button:has-text("Reject all")',
            'button:has-text("Tout refuser")',
        ], timeout_ms=3000)
        filled = await _fill_first(page, [
            'input[aria-label*="name" i]',
            'input[aria-label*="nom" i]',
            'input[placeholder*="name" i]',
            'input[placeholder*="nom" i]',
            'input[type="text"]',
        ], self.name, timeout_ms=20_000)
        if not filled:
            await self._debug_screenshot(page, "meet-noname")
            raise MeetingError(
                "Google Meet did not offer a guest name field. The meeting may "
                "require a signed-in Google account.", status=502,
            )
        # Join muted: toggle mic and camera off when the prejoin tiles are on.
        for label in ("microphone", "micro", "camera", "caméra"):
            await _click_first(
                page,
                [f'[role="button"][aria-label*="{label}" i][data-is-muted="false"]'],
                timeout_ms=1200,
            )
        joined = await _click_first(page, [
            'button:has-text("Ask to join")',
            'button:has-text("Demander à participer")',
            'button:has-text("Join now")',
            'button:has-text("Participer")',
            'button:has-text("Rejoindre")',
        ], timeout_ms=10_000)
        if not joined:
            await self._debug_screenshot(page, "meet-nojoin")
            raise MeetingError("could not find the Google Meet join button", status=502)

    async def _join_zoom(self, page: Any) -> None:
        await page.goto(zoom_web_client_url(self.url), wait_until="domcontentloaded", timeout=60_000)
        await _click_first(page, [
            'button:has-text("I Agree")',
            'button:has-text("Accept Cookies")',
            "#onetrust-accept-btn-handler",
        ], timeout_ms=3000)
        filled = await _fill_first(page, [
            "#input-for-name",
            'input[placeholder*="name" i]',
            'input[placeholder*="nom" i]',
        ], self.name, timeout_ms=25_000)
        if not filled:
            await self._debug_screenshot(page, "zoom-noname")
            raise MeetingError(
                "the Zoom web client did not load a guest name field", status=502,
            )
        joined = await _click_first(page, [
            'button:has-text("Join")',
            'button:has-text("Rejoindre")',
            ".preview-join-button",
        ], timeout_ms=10_000)
        if not joined:
            await self._debug_screenshot(page, "zoom-nojoin")
            raise MeetingError("could not find the Zoom join button", status=502)
        # Once inside (or admitted), Zoom asks for the audio mode.
        await _click_first(page, [
            'button:has-text("Join Audio by Computer")',
            'button:has-text("Rejoindre l\'audio par ordinateur")',
            'button:has-text("Computer Audio")',
        ], timeout_ms=30_000)

    async def _join_teams(self, page: Any) -> None:
        await page.goto(self.url, wait_until="domcontentloaded", timeout=60_000)
        await _click_first(page, [
            'button:has-text("Accept")',
            'button:has-text("Accepter")',
        ], timeout_ms=3000)
        # The launcher page pushes the desktop app first.
        await _click_first(page, [
            'button:has-text("Continue on this browser")',
            'button:has-text("Continuer dans ce navigateur")',
            'a:has-text("Join on the web")',
            'a:has-text("Participer sur le web")',
        ], timeout_ms=8000)
        filled = await _fill_first(page, [
            'input[data-tid="prejoin-display-name-input"]',
            'input[placeholder*="name" i]',
            'input[placeholder*="nom" i]',
        ], self.name, timeout_ms=30_000)
        if not filled:
            await self._debug_screenshot(page, "teams-noname")
            raise MeetingError(
                "Teams did not offer a guest name field; the link may require "
                "a signed-in account.", status=502,
            )
        joined = await _click_first(page, [
            'button[data-tid="prejoin-join-button"]',
            'button:has-text("Join now")',
            'button:has-text("Rejoindre maintenant")',
        ], timeout_ms=10_000)
        if not joined:
            await self._debug_screenshot(page, "teams-nojoin")
            raise MeetingError("could not find the Teams join button", status=502)

    # ---- audio segments -------------------------------------------------

    def _on_sources(self, count: int) -> None:
        self.sources = int(count)
        if self.state == "waiting":
            self.state = "live"
        self._persist()

    async def _on_segment(self, b64: str, offset_ms: float, duration_ms: float) -> None:
        """Transcribe one WAV segment, keeping the transcript in capture order."""
        try:
            from navin.audio.transcription import (
                resolve_transcription_config,
                transcribe_audio_data_url,
            )
            from navin.config.loader import load_config

            async with self._stt_lock:
                config = resolve_transcription_config(load_config())
                if not config.configured:
                    raise MeetingError("no transcription provider configured", status=503)
                text = await transcribe_audio_data_url(
                    f"data:audio/wav;base64,{b64}", config
                )
            text = (text or "").strip()
            if text:
                segment = {
                    "offset_ms": int(offset_ms),
                    "duration_ms": int(duration_ms),
                    "text": text,
                }
                self.segments.append(segment)
                from navin.meetings.store import default_meeting_store

                default_meeting_store().save_audio_segment(
                    self.bot_id,
                    base64.b64decode(b64),
                    metadata=segment,
                )
                self._persist()
        except Exception as e:  # noqa: BLE001 - one bad segment must not kill the bot
            logger.warning("meeting bot {} segment failed: {}", self.bot_id, e)


# ---- module-level registry ----------------------------------------------

_BOTS: dict[str, MeetingBot] = {}


async def bot_start_payload(
    *, bot_id: str, url: str, name: str, language: str = ""
) -> dict[str, Any]:
    if not bot_id:
        raise MeetingError("bot_id is required", status=400)
    if not url.strip():
        raise MeetingError("a meeting link is required", status=400)
    if detect_platform(url) == "unknown":
        raise MeetingError(
            "unsupported meeting link: expected a Zoom, Google Meet or "
            "Microsoft Teams URL", status=400,
        )
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise MeetingError(_INSTALL_HINT, status=503) from None

    existing = _BOTS.get(bot_id)
    if existing and existing.active:
        return existing.status(cursor=len(existing.segments))
    bot = MeetingBot(bot_id, url, name, language)
    _BOTS[bot_id] = bot
    bot.start()
    return bot.status()


def bot_status_payload(bot_id: str, cursor: int = 0) -> dict[str, Any]:
    bot = _BOTS.get(bot_id)
    if bot is None:
        from navin.meetings.store import default_meeting_store

        saved = default_meeting_store().load_bot_state(bot_id)
        if saved is None:
            raise MeetingError("no bot for this meeting", status=404)
        segments = saved.get("segments") if isinstance(saved.get("segments"), list) else []
        cursor = max(0, min(cursor, len(segments)))
        state = str(saved.get("state") or "ended")
        if state in ("starting", "joining", "waiting", "live"):
            state = "interrupted"
        return {**saved, "state": state, "cursor": len(segments), "segments": segments[cursor:]}
    return bot.status(cursor=cursor)


async def bot_stop_payload(bot_id: str) -> dict[str, Any]:
    bot = _BOTS.get(bot_id)
    if bot is None:
        payload = bot_status_payload(bot_id)
        payload["state"] = "ended"
        from navin.meetings.store import default_meeting_store

        default_meeting_store().save_bot_state(bot_id, payload)
        return payload
    await bot.stop()
    return bot.status(cursor=len(bot.segments))
