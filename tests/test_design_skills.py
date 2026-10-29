"""Checks for the Navin design skills pack (no network, under 20 s).

Covers algorithmic-art, brand-guidelines, canvas-design, theme-factory and
slack-gif-creator: frontmatter, lint, forbidden characters and words, and the
bundled scripts actually running.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "navin" / "skills"
DESIGN_SKILLS = ["algorithmic-art", "brand-guidelines", "canvas-design", "theme-factory", "slack-gif-creator"]
TEXT_SUFFIXES = {".md", ".txt", ".py", ".js", ".html", ".css", ".json", ".sh"}
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Never leave bytecode inside the packaged skills (test_no_pycache guards it).
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@pytest.fixture(scope="module")
def validate_skill():
    return _load_module(SKILLS / "skill-development" / "scripts" / "validate_skill.py", "navin_validate_skill")


@pytest.fixture(scope="module")
def gif_core():
    """Load the slack-gif-creator ``core`` package without leaving bytecode in the skill."""
    base = SKILLS / "slack-gif-creator"
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(base))
    try:
        core = importlib.import_module("core")
        for name in ("easing", "frame_composer", "gif_builder", "validators"):
            setattr(core, name, importlib.import_module(f"core.{name}"))
    finally:
        sys.path.remove(str(base))
        sys.dont_write_bytecode = previous
    return core


def _text_files(folder: Path):
    for path in folder.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def _frontmatter(folder: Path) -> dict:
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    assert match, f"{folder.name}: SKILL.md has no frontmatter"
    meta: dict = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


# -- every design skill ----------------------------------------------------


@pytest.mark.parametrize("name", DESIGN_SKILLS)
def test_frontmatter(name):
    folder = SKILLS / name
    meta = _frontmatter(folder)
    assert meta["name"] == name
    assert 150 <= len(meta["description"]) <= 600, len(meta["description"])
    metadata = json.loads(meta["metadata"])
    assert metadata["navin"]["category"] == "design"
    assert metadata["navin"]["emoji"]


@pytest.mark.parametrize("name", DESIGN_SKILLS)
def test_no_license_notice_or_attribution_file(name):
    """The skills are original Navin work: no NOTICE.txt and no Apache-2.0 mention anywhere."""
    folder = SKILLS / name
    assert not (folder / "NOTICE.txt").exists()
    bad = [p for p in _text_files(folder) if re.search(r"apache\s*(license)?\s*2", p.read_text(encoding="utf-8", errors="replace"), re.IGNORECASE)]
    assert not bad, bad


@pytest.mark.parametrize("name", DESIGN_SKILLS)
def test_no_long_dashes(name):
    bad = [p for p in _text_files(SKILLS / name) if re.search("[\u2013\u2014]", p.read_text(encoding="utf-8", errors="replace"))]
    assert not bad, bad


@pytest.mark.parametrize("name", DESIGN_SKILLS)
def test_no_upstream_names(name):
    bad = [
        p
        for p in _text_files(SKILLS / name)
        if re.search(r"claude|anthropic", p.read_text(encoding="utf-8", errors="replace"), re.IGNORECASE)
    ]
    assert not bad, bad


@pytest.mark.parametrize("name", DESIGN_SKILLS)
def test_validate_skill_passes(name, validate_skill):
    result = validate_skill.lint(SKILLS / name)
    assert result["passed"], result["critical"]
    assert result["critical"] == []


@pytest.mark.parametrize("name", DESIGN_SKILLS)
def test_no_bytecode_shipped(name):
    """Bytecode must never be versioned or packaged with a skill.

    Local tooling (linters, importlib loaders) may compile the scripts and leave a
    __pycache__ on disk; what matters is that git ignores it and that the wheel
    package-data globs cannot pick it up (they list source suffixes only).
    """
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", str(SKILLS / name)],
        capture_output=True, text=True, cwd=ROOT, timeout=30,
    )
    assert tracked.returncode == 0, tracked.stderr
    shipped = [line for line in tracked.stdout.splitlines() if "__pycache__" in line or line.endswith(".pyc")]
    assert shipped == [], f"bytecode would be committed for {name}: {shipped}"
    package_data = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"navin/skills/**/*.pyc"' not in package_data and "__pycache__" not in package_data


# -- theme-factory -----------------------------------------------------------


def test_theme_files_parse_and_showcase_is_current(tmp_path):
    base = SKILLS / "theme-factory"
    themes = sorted((base / "themes").glob("*.md"))
    assert len(themes) == 10
    for theme in themes:
        text = theme.read_text(encoding="utf-8")
        assert len(HEX_RE.findall(text)) >= 5, theme.name
    out = tmp_path / "showcase.html"
    proc = _run([str(base / "scripts" / "build_showcase.py"), "--out", str(out)])
    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes() == (base / "assets" / "theme-showcase.html").read_bytes()


# -- canvas-design -----------------------------------------------------------


def test_make_canvas_spec_example_renders_png(tmp_path):
    script = SKILLS / "canvas-design" / "scripts" / "make_canvas.py"
    proc = _run([str(script), "--spec-example"])
    assert proc.returncode == 0, proc.stderr
    spec = json.loads(proc.stdout)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    out = tmp_path / "canvas.png"
    proc = _run([str(script), str(spec_path), str(out)])
    assert proc.returncode == 0, proc.stderr
    from PIL import Image

    with Image.open(out) as img:
        assert img.size == (spec["width"], spec["height"])


def test_fetch_fonts_list_prints_manifest():
    script = SKILLS / "canvas-design" / "scripts" / "fetch_fonts.py"
    proc = _run([str(script), "--list", "--json"])
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads(proc.stdout)
    assert manifest
    text = _run([str(script), "--list"]).stdout
    assert "Inter" in text


# -- brand-guidelines --------------------------------------------------------


def test_brand_tokens_css_and_json_agree():
    assets = SKILLS / "brand-guidelines" / "assets"
    css = {h.lower() for h in HEX_RE.findall((assets / "navin-tokens.css").read_text(encoding="utf-8"))}
    data = (assets / "navin-tokens.json").read_text(encoding="utf-8")
    json.loads(data)
    js = {h.lower() for h in HEX_RE.findall(data)}
    assert css == js, {"css_only": css - js, "json_only": js - css}


# -- algorithmic-art ---------------------------------------------------------


def test_algorithmic_art_templates_only_use_p5_cdn():
    hosts = set()
    for path in (SKILLS / "algorithmic-art" / "templates").iterdir():
        hosts.update(re.findall(r"https?://([^/\"' ]+)", path.read_text(encoding="utf-8")))
    hosts.discard("www.w3.org")  # SVG/XHTML namespaces, not a download
    assert hosts == {"cdnjs.cloudflare.com"}, hosts
    assert "p5" in (SKILLS / "algorithmic-art" / "templates" / "viewer.html").read_text(encoding="utf-8")


# -- slack-gif-creator -------------------------------------------------------


def test_easing_endpoints(gif_core):
    easing = gif_core.easing
    assert len(easing.EASINGS) >= 9
    for name, fn in easing.EASINGS.items():
        assert abs(fn(0.0)) < 1e-6, name
        assert abs(fn(1.0) - 1.0) < 1e-6, name
        assert easing.ease(name, 0.5) == fn(0.5)
    with pytest.raises(KeyError):
        easing.ease("nope", 0.5)


def _build_gif(gif_core, path: Path, size: int, frames: int = 8) -> dict:
    builder = gif_core.gif_builder.GifBuilder(size, size)
    for i in range(frames):
        frame = gif_core.frame_composer.FrameComposer(size, size, "#17171a")
        frame.circle((size / 2, size / 2), size * 0.1 + i * 2, fill="#c6e88a")
        frame.text("GO", size=max(10, size // 4), color="#17171a")
        builder.add_frame(frame.to_image())
    return builder.set_duration(80).loop(0).quantize(32).save(path)


def test_gif_builder_and_validator(gif_core, tmp_path):
    info = _build_gif(gif_core, tmp_path / "big.gif", 400)
    assert set(info) >= {"path", "bytes", "frames", "width", "height", "duration_ms"}
    assert info["width"] == 400 and info["bytes"] > 0
    ok_msg = gif_core.validators.validate_gif(info["path"], "message")
    assert ok_msg["ok"], ok_msg["issues"]
    bad_emoji = gif_core.validators.validate_gif(info["path"], "emoji")
    assert not bad_emoji["ok"]
    assert any("exceeds 128x128" in issue for issue in bad_emoji["issues"])
    small = _build_gif(gif_core, tmp_path / "small.gif", 128)
    assert gif_core.validators.validate_gif(small["path"], "emoji")["ok"]
    assert gif_core.frame_composer.hex_to_rgb("#c6e88a") == (198, 232, 138)


def test_validators_cli_exit_codes(gif_core, tmp_path):
    info = _build_gif(gif_core, tmp_path / "big.gif", 400)
    script = SKILLS / "slack-gif-creator" / "core" / "validators.py"
    proc = _run([str(script), info["path"], "--target", "message"])
    assert proc.returncode == 0 and json.loads(proc.stdout)["ok"]
    proc = _run([str(script), info["path"], "--target", "emoji"])
    assert proc.returncode == 1 and not json.loads(proc.stdout)["ok"]


def test_bounce_text_example_emoji(tmp_path):
    script = SKILLS / "slack-gif-creator" / "examples" / "bounce_text.py"
    out = tmp_path / "go.gif"
    proc = _run([str(script), "--text", "GO", "--out", str(out), "--target", "emoji"])
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] and report["issues"] == []
    assert report["width"] == 128 and report["height"] == 128
    assert report["bytes"] <= 64 * 1024
    assert out.is_file()


# ---------------------------------------------------------------- routing


ROUTING_PROMPTS = [
    ("algorithmic-art", "Génère une oeuvre d'art génératif avec p5.js, flow field et seed"),
    ("algorithmic-art", "Create generative art with a seeded algorithm in p5.js"),
    ("brand-guidelines", "Applique la charte graphique Navin : couleurs et typographie officielles"),
    ("brand-guidelines", "Which brand colors and fonts should this slide deck use"),
    ("canvas-design", "Crée une affiche visuelle, un poster PNG avec une composition design"),
    ("canvas-design", "Design a poster canvas artwork exported as PNG and PDF"),
    ("theme-factory", "Applique un thème de couleurs à ce document, propose une palette"),
    ("theme-factory", "Pick a visual theme palette for my slides"),
    ("slack-gif-creator", "Crée un GIF animé pour Slack avec un texte qui rebondit"),
    ("slack-gif-creator", "Make an animated gif emoji for slack"),
]


@pytest.fixture(scope="module")
def skill_router():
    from navin.agent.skills import SkillsLoader
    from navin.agent.skills_graph import build_skills_graph
    from navin.agent.tools.skill_catalog import SkillCatalogTool

    loader = SkillsLoader(ROOT)
    return loader, build_skills_graph(loader), SkillCatalogTool(workspace=ROOT)


@pytest.mark.parametrize(("skill", "prompt"), ROUTING_PROMPTS, ids=[f"{s}-{i}" for i, (s, _p) in enumerate(ROUTING_PROMPTS)])
def test_design_requests_route_to_their_skill(skill_router, skill: str, prompt: str):
    """French and English design requests reach the re-coded skill on every
    path the agent uses: keyword find, graph ranking, per-turn suggestion."""
    loader, graph, tool = skill_router
    found = [row["name"] for row in loader.search_skills(prompt, limit=5)]
    ranked = [row["name"] for row in graph.suggest(prompt, limit=5)]
    suggested = [row["name"] for row in tool._suggest_for_request(prompt)]
    assert skill in found[:3], f"find: {found}"
    assert skill in ranked[:3], f"graph: {ranked}"
    assert skill in suggested, f"auto-suggest: {suggested}"
