# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Motion engine: deterministic renderer, template library, storyboard."""

from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

from navin.montage.motion import catalog, renderer, storyboard
from navin.montage.motion.brand import BrandKit, brand_kit_from_marketing_store, load_brand_kit
from navin.montage.motion.server import MotionServer
from navin.montage.motion.vendor import PACKAGE_ASSETS, TEMPLATES_DIR, missing_files

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _chromium_ready() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    from navin.browser_runtime import installed_chromium

    if installed_chromium():
        return True

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


CHROMIUM = _chromium_ready()
needs_render = pytest.mark.skipif(
    not (CHROMIUM and FFMPEG and FFPROBE), reason="Chromium (Playwright) or ffmpeg/ffprobe missing"
)

BRAND = {
    "name": "Acme",
    "colors": {"primary": "#6366F1", "secondary": "#22D3EE", "accent": "#F472B6", "background": "#0A0B14", "text": "#F5F7FF"},
    "fonts": {"heading": "Inter", "body": "Inter"},
    "tagline": "Ship faster",
    "url": "acme.com",
}


# ------------------------------------------------------------------ pure


def test_vendored_runtime_is_complete_and_licensed() -> None:
    assert missing_files(PACKAGE_ASSETS) == []
    for name in ("LICENSE-gsap.txt", "LICENSE-three.txt", "fonts/LICENSE-inter-OFL.txt",
                 "time-controller.js", "navin-motion.js", "navin-motion.css"):
        assert (PACKAGE_ASSETS / name).is_file(), name


def test_catalog_lists_ten_templates_with_files_and_schemas() -> None:
    expected = {
        "hero_intro", "kinetic_title", "feature_callout", "device_mockup_3d", "stats_counter",
        "split_compare", "lower_third", "cta_end_card", "logo_sting", "gradient_stage",
    }
    assert set(catalog.TEMPLATES) == expected
    for info in catalog.TEMPLATES.values():
        html = info.path.read_text(encoding="utf-8")
        assert "NM.mount(" in html, info.name
        assert info.props_schema()["type"] == "object"
        # No CDN or remote resource anywhere in a template.
        assert not re.search(r"(src|href)=[\"']https?://", html), info.name
        assert chr(0x2014) not in html and chr(0x2013) not in html
    assert set(catalog.TEMPLATES["feature_callout"].video_layers) == {"media"}


def test_split_ranges_cover_every_frame_once() -> None:
    ranges = renderer.split_ranges(101, 4)
    assert ranges[0][0] == 0 and ranges[-1][1] == 101
    assert sum(b - a for a, b in ranges) == 101
    assert all(ranges[i][1] == ranges[i + 1][0] for i in range(len(ranges) - 1))
    assert renderer.split_ranges(3, 8) == [(0, 1), (1, 2), (2, 3)]


def test_h264_encoder_is_high_quality_cfr() -> None:
    argv = renderer.encoder_args(renderer.RenderSettings(), "ffmpeg", Path("out.mp4"))
    joined = " ".join(argv)
    for flag in ("-crf 16", "-preset slow", "-pix_fmt yuv420p", "-f image2pipe", "-r 30", "-colorspace bt709"):
        assert flag in joined
    prores = renderer.encoder_args(renderer.RenderSettings(codec="prores4444"), "ffmpeg", Path("o.mov"))
    assert "prores_ks" in prores and "yuva444p10le" in prores
    assert renderer.RenderSettings(codec="webm_alpha").capture_format == "png"


def test_render_settings_reject_bad_values() -> None:
    with pytest.raises(renderer.MotionRenderError):
        renderer.RenderSettings(codec="gif").validate()
    with pytest.raises(renderer.MotionRenderError):
        renderer.RenderSettings(width=1921).validate()


def test_brand_kit_contract_and_css(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n")
    font = tmp_path / "Inter-Bold.ttf"
    font.write_bytes(b"0")
    kit = load_brand_kit({**BRAND, "logo_path": "logo.png", "font_files": ["Inter-Bold.ttf"],
                          "colors": {"primary": "abc", "text": "not-a-color"}}, base_dir=tmp_path)
    assert kit.logo_path == str(logo.resolve())
    assert kit.colors["primary"] == "#AABBCC"
    assert kit.colors["text"] == "#F5F7FF"  # invalid color falls back
    assert kit.font_faces() == [{"path": str(font.resolve()), "family": "Inter", "weight": "700", "style": "normal"}]
    assert "--c-primary: #AABBCC;" in kit.css_variables()
    path = tmp_path / "brand.json"
    path.write_text(json.dumps(BRAND), encoding="utf-8")
    assert load_brand_kit(str(path)).name == "Acme"
    assert load_brand_kit(None, use_marketing_store=False) == BrandKit()


def test_brand_kit_from_marketing_store_is_read_only() -> None:
    class Store:
        def load_brand(self):
            return {"company": "Acme", "colors": ["#0B0B10", "#FF5500", "#00AAFF", "#FAFAFA"],
                    "fonts": ["Sora", "Inter"], "site": "https://acme.com/", "logo": ""}

        def load_harvest(self):
            return {"colors": [], "fonts": []}

        def load_product(self):
            return {"one_liner": "Ship faster"}

        def save_brand(self, *_a):  # pragma: no cover - must never be called
            raise AssertionError("store must stay read only")

    kit = brand_kit_from_marketing_store(Store())
    assert kit is not None
    assert kit.name == "Acme" and kit.url == "acme.com" and kit.tagline == "Ship faster"
    assert kit.colors["primary"] == "#FF5500" and kit.colors["background"] == "#0B0B10"
    assert kit.fonts == {"heading": "Sora", "body": "Inter"}


def _board(**extra):
    raw = {
        "version": 1,
        "format": "9:16",
        "fps": 30,
        "brand": BRAND,
        "scenes": [
            {"template": "gradient_stage", "duration": 2},
            {"template": "kinetic_title", "duration": 3, "props": {"lines": ["Hi"]}, "transition": "fade"},
            {"type": "footage", "path": "clip.mp4", "duration": 2, "transition": "whip"},
            {"template": "cta_end_card", "duration": 3, "transition": "cut"},
        ],
    }
    raw.update(extra)
    return raw


def test_storyboard_contract_and_duration() -> None:
    board = storyboard.parse_storyboard(_board(), use_marketing_store=False)
    assert (board.width, board.height) == (1080, 1920)
    assert [s.transition for s in board.scenes] == ["cut", "fade", "whip", "cut"]
    assert board.total_duration == pytest.approx(10 - 0.5 - 0.3)
    argv = storyboard.build_join_args(board, ["a.mp4", "b.mp4", "c.mp4", "d.mp4"], "out.mp4", "ffmpeg")
    graph = argv[argv.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=0.500000:offset=1.500000" in graph
    assert "xfade=transition=smoothleft:duration=0.300000:offset=4.200000" in graph
    assert "concat=n=2" in graph


@pytest.mark.parametrize(
    "patch,message",
    [
        ({"format": "4:3"}, "format"),
        ({"version": 2}, "version"),
        ({"scenes": [{"template": "nope", "duration": 2}]}, "unknown"),
        ({"scenes": [{"template": "gradient_stage", "duration": 1},
                     {"template": "gradient_stage", "duration": 1, "transition": "fade",
                      "transition_duration": 0.9}]}, "transition_duration"),
        ({"scenes": [{"type": "footage", "duration": 2}]}, "path"),
    ],
)
def test_storyboard_rejects_contract_violations(patch, message) -> None:
    with pytest.raises(storyboard.StoryboardError, match=message):
        storyboard.parse_storyboard(_board(**patch), use_marketing_store=False)


def test_server_serves_registered_files_with_ranges_only(tmp_path: Path) -> None:
    media = tmp_path / "clip.bin"
    media.write_bytes(bytes(range(256)) * 4)
    secret = tmp_path / "secret.txt"
    secret.write_text("no")
    with MotionServer(PACKAGE_ASSETS, TEMPLATES_DIR) as server:
        url = server.register_file(media)
        req = urllib.request.Request(url, headers={"Range": "bytes=10-19"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 206
            assert resp.read() == bytes(range(10, 20))
        with urllib.request.urlopen(server.base_url + "/assets/gsap.min.js") as resp:
            assert resp.status == 200
        for bad in ("/assets/../vendor.py", "/media/x/secret.txt", "/../" + str(secret)):
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(server.base_url + bad)


# ------------------------------------------------------------------ render


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    if not FFMPEG:
        pytest.skip("ffmpeg missing")
    root = tmp_path_factory.mktemp("motion-media")
    rgb = root / "rgb.mp4"
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=red:size=320x180:rate=30:duration=1",
         "-f", "lavfi", "-i", "color=lime:size=320x180:rate=30:duration=1",
         "-f", "lavfi", "-i", "color=blue:size=320x180:rate=30:duration=1",
         "-filter_complex", "[0][1][2]concat=n=3:v=1[v]", "-map", "[v]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(rgb)],
        check=True,
    )
    shot = root / "shot.png"
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=640x400", "-frames:v", "1", str(shot)],
        check=True,
    )
    return {"rgb": rgb, "shot": shot, "root": root}


def _props(name: str, media: dict[str, Path]) -> dict:
    return {
        "hero_intro": {"headline": "Meet Acme", "subheadline": "Ship faster"},
        "kinetic_title": {"lines": ["Stop guessing.", "Start shipping."], "emphasis": ["shipping."]},
        "feature_callout": {"media": str(media["rgb"]), "bbox": [0.2, 0.2, 0.4, 0.4], "label": "Export", "title": "Fast"},
        "device_mockup_3d": {"media": str(media["rgb"]), "device": "laptop", "headline": "One screen"},
        "stats_counter": {"title": "Proof", "stats": [{"value": 98, "suffix": "%", "label": "uptime"}]},
        "split_compare": {"before": str(media["shot"]), "after": str(media["rgb"])},
        "lower_third": {"name": "Sarah Chen", "role": "CEO"},
        "cta_end_card": {"headline": "Start today", "button": "Try it"},
        "logo_sting": {"tagline": "Ship faster"},
        "gradient_stage": {"title": "Hello"},
    }[name]


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-count_frames", "-show_entries",
         "stream=width,height,nb_read_frames,codec_name,pix_fmt:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


@needs_render
@pytest.mark.parametrize("name", sorted(catalog.TEMPLATES))
def test_each_template_renders_one_second_without_js_errors(name: str, media, tmp_path: Path) -> None:
    out = tmp_path / f"{name}.mp4"
    result = asyncio.run(
        renderer.render_motion(
            output=out,
            template=name,
            props=_props(name, media),
            brand=BRAND,
            settings=renderer.RenderSettings(width=320, height=180, fps=10, duration=1.0, workers=1, preset="ultrafast"),
        )
    )
    assert result["ok"], result
    assert result["console_errors"] == []
    info = _probe(out)
    stream = info["streams"][0]
    assert (stream["width"], stream["height"], stream["codec_name"], stream["pix_fmt"]) == (320, 180, "h264", "yuv420p")
    assert int(stream["nb_read_frames"]) == 10


@needs_render
def test_render_is_deterministic_and_parallel_split_matches(tmp_path: Path) -> None:
    def run(workers: int, out: str) -> dict:
        return asyncio.run(
            renderer.render_motion(
                output=tmp_path / out,
                template="hero_intro",
                props={"headline": "Meet Acme"},
                brand=BRAND,
                settings=renderer.RenderSettings(width=320, height=180, fps=10, duration=1.5, workers=workers, preset="ultrafast"),
                collect_hashes=True,
            )
        )

    first, second, split = run(1, "a.mp4"), run(1, "b.mp4"), run(3, "c.mp4")
    assert first["ok"] and second["ok"] and split["ok"], (first, second, split)
    assert len(first["frame_hashes"]) == 15
    assert first["frame_hashes"] == second["frame_hashes"]
    assert split["workers"] == 3
    assert split["frame_hashes"] == first["frame_hashes"]
    assert len(set(first["frame_hashes"])) > 5  # it actually animates
    assert int(_probe(tmp_path / "c.mp4")["streams"][0]["nb_read_frames"]) == 15


def _dominant(png: bytes, x: float, y: float) -> str:
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGB")
    r, g, b = image.getpixel((int(image.width * x), int(image.height * y)))
    return max((("red", r), ("green", g), ("blue", b)), key=lambda kv: kv[1])[0]


@needs_render
@pytest.mark.parametrize(
    "template,props,point",
    [
        ("feature_callout", {"zoom": 1, "bbox": [0.4, 0.4, 0.2, 0.2], "zoom_out": False}, (0.5, 0.58)),
        ("device_mockup_3d", {"device": "phone", "camera": "push"}, (0.5, 0.5)),
    ],
)
def test_video_layers_seek_frame_accurately(template: str, props: dict, point, media) -> None:
    frames, errors = asyncio.run(
        renderer.capture_stills(
            [0.5, 1.5, 2.5, 1.5],
            template=template,
            props={**props, "media": str(media["rgb"])},
            brand=BRAND,
            settings=renderer.RenderSettings(width=480, height=270, fps=30, duration=3.0, image_format="png"),
        )
    )
    assert errors == []
    colors = [_dominant(f, *point) for f in frames]
    assert colors == ["red", "green", "blue", "green"]


@needs_render
def test_storyboard_concat_duration_is_exact(media, tmp_path: Path) -> None:
    raw = {
        "version": 1,
        "format": "16:9",
        "size": [320, 180],
        "fps": 30,
        "brand": BRAND,
        "scenes": [
            {"template": "gradient_stage", "duration": 1.0},
            {"template": "kinetic_title", "duration": 1.0, "props": {"lines": ["Go"]}, "transition": "fade", "transition_duration": 0.3},
            {"type": "footage", "path": str(media["rgb"]), "duration": 1.5, "start": 0.5, "transition": "cut"},
        ],
    }
    out = tmp_path / "story.mp4"
    result = asyncio.run(storyboard.render_storyboard(raw, out, use_marketing_store=False))
    assert result["ok"], result
    assert result["duration_s"] == pytest.approx(3.2)
    info = _probe(out)
    assert float(info["format"]["duration"]) == pytest.approx(3.2, abs=0.04)
    assert int(info["streams"][0]["nb_read_frames"]) == 96


# ------------------------------------------------------------------ tool


def _tool():
    from navin.agent.tools.montage import MontageTool

    return MontageTool.__new__(MontageTool)


def test_montage_tool_lists_motion_templates() -> None:
    tool = _tool()
    payload = json.loads(asyncio.run(tool.execute(action="motion_templates")))
    names = {row["name"] for row in payload["templates"]}
    assert "device_mockup_3d" in names and len(names) == 10
    assert payload["storyboard_contract"]["version"] == 1
    assert tool.call_concurrency_safe({"action": "motion_templates"})


def test_montage_tool_motion_render_needs_input(tmp_path: Path) -> None:
    from unittest.mock import patch

    tool = _tool()
    with patch.object(tool, "_root_or_error", return_value=(tmp_path, "")):
        result = json.loads(asyncio.run(tool.execute(action="motion_render")))
        assert result["ok"] is False and "storyboard" in result["error"]
        escaped = json.loads(
            asyncio.run(tool.execute(action="motion_render", template="gradient_stage", output="../x.mp4"))
        )
        assert escaped["ok"] is False and "workspace" in escaped["error"]


@needs_render
def test_montage_tool_renders_a_template_into_the_workspace(tmp_path: Path) -> None:
    from unittest.mock import patch

    tool = _tool()
    with patch.object(tool, "_root_or_error", return_value=(tmp_path, "")):
        raw = asyncio.run(
            tool.execute(
                action="motion_render",
                template="lower_third",
                props=json.dumps({"name": "Sarah Chen", "role": "CEO"}),
                brand=json.dumps(BRAND),
                width=320,
                height=180,
                fps=10,
                duration=1.0,
                codec="webm_alpha",
            )
        )
    result = json.loads(raw)
    assert result["ok"], result
    assert result["output"].startswith("marketing/montage/exports/lower_third-")
    assert result["output"].endswith(".webm") and result["alpha"] is True
    assert (tmp_path / result["output"]).is_file()
