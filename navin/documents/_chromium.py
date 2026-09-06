"""Headless Chromium plumbing shared by the HTML document converters.

The converters need the *rendered* page, not the source: only a browser knows
that a class resolves to 22px semibold #6D28D9. Chromium is driven through its
command line rather than Playwright, which is not installed everywhere Navin
runs, and the measurements travel back through ``--dump-dom``: an injected
script writes its JSON result into the DOM, and the serialized page carries it
out.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any, Sequence

RESULT_ELEMENT_ID = "__navin_html_measure__"

_CHROMIUM_ENV_VARS = ("NAVIN_CHROMIUM", "NAVIN_CHROME_PATH", "CHROME_PATH")
_CHROMIUM_COMMANDS = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "msedge",
)
# Windows installers put the browser under Program Files and never on PATH, so
# every name above misses and document conversion fails on a machine that
# visibly has Chrome. Edge is listed because it ships with the OS.
_WINDOWS_CHROMIUM_RELATIVE = (
    r"Google\Chrome\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
    r"Chromium\Application\chrome.exe",
)
_WINDOWS_CHROMIUM_BASE_VARS = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
_MAC_CHROMIUM_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
_BASE_FLAGS = (
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    "--allow-file-access-from-files",
    "--virtual-time-budget=8000",
)
_DUMP_RE = re.compile(
    rf'<script[^>]*id="{RESULT_ELEMENT_ID}"[^>]*>(.*?)</script>',
    re.DOTALL,
)


class ConversionError(RuntimeError):
    """Raised when a document cannot be produced."""


def _windows_install_candidates() -> list[str]:
    """Where the Windows installers actually put the browsers.

    Joined as plain strings: this has to be callable from a POSIX host, where
    ``Path`` would apply the wrong separator rules to a drive-letter path.
    """
    found: list[str] = []
    for var in _WINDOWS_CHROMIUM_BASE_VARS:
        base = (os.environ.get(var) or "").rstrip("\\/")
        if not base:
            continue
        found.extend(f"{base}\\{relative}" for relative in _WINDOWS_CHROMIUM_RELATIVE)
    return found


def find_chromium(explicit: str | None = None) -> str:
    """Locate a Chromium binary, including the Playwright cache."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(os.environ[var] for var in _CHROMIUM_ENV_VARS if os.environ.get(var))
    for command in _CHROMIUM_COMMANDS:
        found = shutil.which(command)
        if found:
            candidates.append(found)
    if os.name == "nt":
        candidates.extend(_windows_install_candidates())
    else:
        candidates.extend(_MAC_CHROMIUM_APPS)
    # PLAYWRIGHT_BROWSERS_PATH is often set to a sandbox-local cache that was
    # never populated, so the default cache stays in the search list too.
    raw_caches = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH")]
    # A frozen build hands its children a scrubbed environment, and without HOME
    # or USERPROFILE ``Path.home()`` raises rather than returning anything, which
    # would abort the search before the installed browsers are even considered.
    with suppress(RuntimeError, OSError):
        home = Path.home()
        raw_caches += [
            str(home / ".cache/ms-playwright"),
            str(home / "Library/Caches/ms-playwright"),
        ]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        raw_caches.append(str(Path(local_appdata) / "ms-playwright"))
    caches = [Path(raw) for raw in raw_caches if raw]
    for cache in caches:
        if not cache.is_dir():
            continue
        builds = sorted(
            (path for path in cache.glob("chromium-*") if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        )
        for build in builds:
            for relative in (
                # Recent Playwright builds use chrome-linux64; older use chrome-linux.
                "chrome-linux64/chrome",
                "chrome-linux/chrome",
                "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                "chrome-win64/chrome.exe",
                "chrome-win/chrome.exe",
            ):
                binary = build / relative
                if binary.is_file():
                    candidates.append(str(binary))
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise ConversionError(
        "No Chromium binary found. Install one, or point NAVIN_CHROMIUM at it."
    )


def _no_window_kwargs() -> dict[str, Any]:
    """Keep Chromium from flashing a console on Windows.

    Imported lazily: these modules also run from user workspaces where the
    full Navin dependency set is not installed.
    """
    try:
        from navin.utils.proc import no_window_kwargs
    except Exception:
        flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flag} if flag else {}
    return no_window_kwargs()


_PROBE_HTML = (
    "<html><body><script>window.addEventListener('load',()=>{"
    "const h=document.createElement('script');h.type='application/json';"
    f"h.id='{RESULT_ELEMENT_ID}';"
    "h.textContent=JSON.stringify({w:window.innerWidth,h:window.innerHeight});"
    "document.body.appendChild(h);});</script></body></html>"
)
_chrome_padding: dict[str, tuple[int, int]] = {}


def chrome_padding(chromium: str, workdir: Path, timeout: int) -> tuple[int, int]:
    """Pixels ``--window-size`` spends on window furniture instead of the page.

    The new headless mode simulates a real window, so a 1920x1080 window gives
    a viewport around 1920x993. Asking for a 1080px-tall slide then renders and
    captures only its first 993px, which shifts the decor against the elements
    measured in page coordinates. Measured once per binary, then compensated.
    """
    cached = _chrome_padding.get(chromium)
    if cached is not None:
        return cached
    padding = (0, 0)
    try:
        probe = workdir / "__h2x_probe.html"
        probe.write_text(_PROBE_HTML, encoding="utf-8")
        flags = [*_BASE_FLAGS, "--window-size=1000,1000"]
        result = subprocess.run(
            [chromium, *flags, "--dump-dom", probe.resolve().as_uri()],
            capture_output=True,
            timeout=timeout,
            check=False,
            **_no_window_kwargs(),
        )
        payload = read_measurements(result.stdout.decode("utf-8", errors="ignore"), probe)
        width, height = int(payload.get("w") or 0), int(payload.get("h") or 0)
        if 0 < width <= 1000 and 0 < height <= 1000:
            padding = (1000 - width, 1000 - height)
    except (OSError, ValueError, ConversionError, subprocess.SubprocessError):
        padding = (0, 0)
    _chrome_padding[chromium] = padding
    return padding


def run_chromium(
    chromium: str,
    args: Sequence[str],
    timeout: int,
    viewport: tuple[int, int] | None = None,
    padding: tuple[int, int] = (0, 0),
) -> subprocess.CompletedProcess:
    """Run one headless Chromium command.

    ``viewport`` is the page area wanted; window furniture is added on top.
    """
    flags = list(_BASE_FLAGS)
    if viewport:
        flags.append(
            f"--window-size={viewport[0] + padding[0]},{viewport[1] + padding[1]}"
        )
    try:
        return subprocess.run(
            [chromium, *flags, *args],
            capture_output=True,
            timeout=timeout,
            check=False,
            **_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - environment dependent
        raise ConversionError(f"Chromium timed out after {timeout}s") from exc


_FRAME_CSS = """<style id="__navin_frame__">
  html, body {{
    width: {width}px !important;
    height: {height}px !important;
    min-height: 0 !important;
    max-height: {height}px !important;
    overflow: hidden !important;
  }}
</style>"""

# Entry animations make the page a moving target: an element mid-fade (or
# still waiting on a stagger delay, opacity 0) is skipped by the measurement
# pass but fully painted by the later screenshot pass, so its text ends up
# baked into the backdrop instead of editable - or twice, once per layer.
# Freezing every animation at its resting state makes both Chromium runs see
# the same, final page.
_FREEZE_CSS = """<style id="__navin_freeze__">
  *, *::before, *::after {
    animation: none !important;
    transition: none !important;
  }
</style>"""


def instrument(
    source: Path,
    script: str,
    workdir: Path,
    prefix: str = "measure",
    frame: tuple[int, int] | None = None,
) -> Path:
    """Write a copy of the page with the measuring script appended.

    A ``<base>`` tag keeps relative assets (``../../uploads/bg.png``) resolving
    against the original folder, since the copy no longer sits beside them.

    ``frame`` pins the document to an exact pixel size. Slides need it: a page
    one pixel taller than the frame makes Chromium capture the whole document
    and squeeze it back into the requested image, which shifts the backdrop
    against the text measured in unsqueezed coordinates.
    """
    html = source.read_text(encoding="utf-8", errors="ignore")
    base_tag = f'<base href="{source.resolve().parent.as_uri()}/">'
    head = html.lower().find("<head")
    if head != -1:
        insert_at = html.find(">", head) + 1
        html = html[:insert_at] + base_tag + html[insert_at:]
    else:
        html = base_tag + html
    # Last in the document, so it wins the cascade against the page styles.
    injection = _FREEZE_CSS
    injection += _FRAME_CSS.format(width=frame[0], height=frame[1]) if frame else ""
    injection += (
        "<script>window.addEventListener('load', () => {"
        "const run = () => {" + script + "};"
        "(document.fonts ? document.fonts.ready.then(run) : Promise.resolve().then(run));"
        "});</script>"
    )
    if "</body>" in html:
        html = html.replace("</body>", f"{injection}</body>", 1)
    else:
        html = f"{html}{injection}"
    target = workdir / f"__h2x_{prefix}_{source.stem}.html"
    target.write_text(html, encoding="utf-8")
    return target


def read_measurements(dump: str, source: Path) -> dict[str, Any]:
    """Pull the injected JSON back out of a serialized DOM."""
    match = _DUMP_RE.search(dump)
    if not match:
        raise ConversionError(f"{source.name}: the measuring script produced no output")
    raw = match.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConversionError(f"{source.name}: unreadable measurements ({exc})") from exc


def measure(
    source: Path,
    script: str,
    *,
    chromium: str,
    workdir: Path,
    timeout: int,
    viewport: tuple[int, int],
    prefix: str = "measure",
    screenshot: Path | None = None,
    frame: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Measure a page, optionally capturing the neutralized result."""
    page = instrument(source, script, workdir, prefix=prefix, frame=frame)
    url = page.resolve().as_uri()
    padding = chrome_padding(chromium, workdir, timeout)
    dump = run_chromium(chromium, ["--dump-dom", url], timeout, viewport, padding)
    payload = read_measurements(dump.stdout.decode("utf-8", errors="ignore"), source)
    if screenshot is not None:
        run_chromium(chromium, [f"--screenshot={screenshot}", url], timeout, viewport, padding)
        _crop(screenshot, viewport)
    return payload


def _crop(image_path: Path, size: tuple[int, int]) -> None:
    """Trim the window furniture off a capture, keeping the page area."""
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            if image.size == size:
                return
            cropped = image.crop((0, 0, min(size[0], image.width), min(size[1], image.height)))
            cropped.load()
        cropped.save(image_path)
    except Exception:  # pragma: no cover - a raw capture is better than none
        return
