# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Canvas kit in headless Chromium: renders, no console errors, no network."""

from __future__ import annotations

import asyncio
import html as html_lib

import pytest

from navin.artifacts.canvas import build_canvas
from navin.artifacts.canvas.templates import TEMPLATES, build_template
from tests.test_canvas import CHART, SAMPLES


def _chromium_ready() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False

    async def probe() -> bool:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True)
            except Exception:  # noqa: BLE001
                return False
            await browser.close()
            return True

    return asyncio.run(probe())


pytestmark = pytest.mark.skipif(not _chromium_ready(), reason="Chromium (Playwright) missing")

KITCHEN_SINK = [
    {"type": "stats", "items": [{"label": "Total", "value": 1234.5, "unit": "ms", "delta": -12, "delta_unit": "%", "tone": "good"},
                                {"label": "Verdict", "value": "WARN", "tone": "warn"}]},
    {"type": "section", "title": "Charts", "description": "Both chart kinds", "blocks": [
        {**CHART, "series": [{"name": "Before", "values": [3, 5], "role": "baseline"}, {"name": "After", "values": [2, 4]}]},
        {**CHART, "orientation": "horizontal", "title": "Horizontal"},
        {"type": "line_chart", "title": "Line", "x_label": "Day", "y_label": "Tokens", "unit": "tokens",
         "caption": "Source: test", "x": ["10-01", "10-02", "10-03"],
         "series": [{"name": "Prompt", "values": [10, None, 30]}, {"name": "Completion", "values": [1, 2, 3]}]},
    ]},
    {"type": "table", "title": "Rows", "columns": [{"key": "name", "label": "Name"}, {"key": "v", "label": "Value", "unit": "ms"},
                                                   {"key": "d", "label": "Change", "format": "delta_pct", "better": "lower"},
                                                   {"key": "s", "label": "State"}],
     "rows": [{"name": "b", "v": 2, "d": -5, "s": {"badge": "pass"}}, {"name": "a", "v": 10, "d": 3, "s": {"badge": "fail", "tone": "bad"}},
              {"name": "c", "v": 5, "d": 0, "s": {"text": "n/a"}}]},
    {"type": "timeline", "items": [{"date": "2026-10-01", "label": "Start", "milestone": True},
                                   {"date": "2026-10-05", "end": "2026-10-09", "label": "Build", "status": "in_progress", "critical": True}]},
    {"type": "findings", "items": [{"severity": "low", "title": "Nit", "file": "a.py", "line": 3},
                                   {"severity": "critical", "title": "Leak", "location": "0:12.00", "evidence": "x = 1", "fix": "rotate", "status": "open"}]},
    {"type": "diff", "title": "Config", "before": {"label": "Before", "text": "a\nb\nc"}, "after": {"label": "After", "text": "a\nB\nc\nd"}},
    {"type": "badges", "items": [{"label": "draft", "tone": "warn"}, {"label": "main"}]},
    {"type": "text", "text": "First paragraph.\n\nSecond <b>not bold</b>."},
]


async def _render(html: str, *, iframe: bool = False, dark: bool = False, then=None):
    from playwright.async_api import async_playwright

    errors: list[str] = []
    requests: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 900, "height": 900},
                                          color_scheme="dark" if dark else "light")
            page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: requests.append(r.url))
            if iframe:
                await page.set_content(
                    '<iframe sandbox="allow-scripts" style="width:880px;height:860px;border:0" '
                    f'srcdoc="{html_lib.escape(html, quote=True)}"></iframe>'
                )
                frame = page.frames[1]
            else:
                await page.set_content(html)
                frame = page.main_frame
            await frame.wait_for_selector("#navin-canvas[data-rendered='true']", timeout=5000)
            info = await frame.evaluate("""() => {
              const q = (s) => Array.from(document.querySelectorAll(s));
              return {
                blocks: q('[data-block]').map(n => n.dataset.block),
                svgs: q('svg.nv-chart').length,
                axisLabels: q('svg .nv-axis-label').map(n => n.textContent),
                figTitles: q('figure .nv-block-title').map(n => n.textContent),
                captions: q('figcaption').map(n => n.textContent),
                legends: q('.nv-legend').length,
                bold: q('#navin-canvas b').length,
                bg: getComputedStyle(document.body).backgroundColor,
                shadows: q('*').filter(n => { const s = getComputedStyle(n); return s.boxShadow !== 'none' || s.backgroundImage !== 'none'; }).length,
              };
            }""")
            if then is not None:
                await then(frame)
            return info, errors, requests
        finally:
            await browser.close()


def _run(coro):
    return asyncio.run(coro)


def test_kit_renders_every_block_without_errors_or_network():
    async def interact(frame):
        # Sortable table: click Value, rows reorder descending then ascending.
        header = frame.locator("th[data-key='v'] button")
        await header.click()
        assert await frame.locator(".nv-table tbody tr td").first.text_content() == "a"
        await header.click()
        assert await frame.locator(".nv-table tbody tr td").first.text_content() == "b"
        assert await frame.locator("th[data-key='v']").get_attribute("aria-sort") == "ascending"
        # Theme message from the host updates tokens live.
        await frame.evaluate("""() => window.postMessage({type: 'navin:canvas-theme', mode: 'dark',
            tokens: {bg: 'rgb(20, 20, 20)', fg: 'rgb(245, 245, 245)', accent: 'url(x)'}}, '*')""")
        await frame.wait_for_function("getComputedStyle(document.body).backgroundColor === 'rgb(20, 20, 20)'")
        accent = await frame.evaluate("document.documentElement.style.getPropertyValue('--nv-accent')")
        assert accent == ""  # unsafe values are dropped

    async def go():
        info, errors, requests = await _render(build_canvas("Kitchen sink", KITCHEN_SINK), then=interact)
        assert errors == []
        assert [u for u in requests if not u.startswith(("about:", "data:"))] == []
        assert set(info["blocks"]) >= {"stats", "section", "bar_chart", "line_chart", "table", "timeline",
                                       "findings", "diff", "badges", "text"}
        assert info["svgs"] == 3
        assert "Y (ms)" in info["axisLabels"] and "X" in info["axisLabels"] and "Tokens" in info["axisLabels"]
        assert info["legends"] == 2  # multi-series bar + line, none for the single series
        assert info["captions"] == ["Source: test"] * 3
        assert info["bold"] == 0  # data is text, never markup
        assert info["shadows"] == 0

    _run(go())


def test_kit_runs_in_the_panel_sandbox():
    async def go():
        info, errors, requests = await _render(
            build_canvas("Sandboxed", KITCHEN_SINK), iframe=True, dark=True,
        )
        assert errors == []
        assert [u for u in requests if not u.startswith(("about:", "data:"))] == []
        assert info["svgs"] == 3
        assert info["bg"] == "rgb(20, 20, 20)"  # dark kit default (hsl 0 0% 8%)

    _run(go())


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_templates_render_labelled_charts(name):
    title, blocks = build_template(name, SAMPLES[name])

    async def go():
        info, errors, requests = await _render(build_canvas(title, blocks))
        assert errors == []
        assert [u for u in requests if not u.startswith(("about:", "data:"))] == []
        assert info["svgs"] >= 1
        assert all(t.strip() for t in info["figTitles"])
        assert len(info["captions"]) == info["svgs"] and all(c.strip() for c in info["captions"])
        # Every chart has a value-axis label (unit appended unless the label is the unit).
        assert len([label for label in info["axisLabels"] if label.strip()]) >= info["svgs"]

    _run(go())
