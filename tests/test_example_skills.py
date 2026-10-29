# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the dev/writing skills pack: webapp-testing, web-artifacts-builder,
doc-coauthoring, internal-comms, mcp-builder. No network, under 25 s."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "navin" / "skills"
PACK = ["webapp-testing", "web-artifacts-builder", "doc-coauthoring", "internal-comms", "mcp-builder"]
TEXT_SUFFIXES = {".md", ".py", ".sh", ".json", ".txt", ".ts", ".tsx", ".html"}
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

WEBAPP = SKILLS / "webapp-testing"
ARTIFACTS = SKILLS / "web-artifacts-builder"
MCP = SKILLS / "mcp-builder"


def _load_validator():
    path = SKILLS / "skill-development" / "scripts" / "validate_skill.py"
    spec = importlib.util.spec_from_file_location("validate_skill", path)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = False
    return module


def _text_files(folder: Path):
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts and "node_modules" not in path.parts:
            yield path


def _frontmatter(folder: Path) -> dict:
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    assert match, f"{folder.name}: missing frontmatter"
    meta: dict = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    # Never leave bytecode inside the packaged skills when their scripts run.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **kw.pop("env", {})}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 60), env=env, **kw)


# ---------------------------------------------------------------- frontmatter and hygiene


@pytest.mark.parametrize("name", PACK)
def test_frontmatter(name):
    folder = SKILLS / name
    meta = _frontmatter(folder)
    assert meta["name"] == name
    desc = meta["description"]
    assert 150 <= len(desc) <= 600, f"{name}: description is {len(desc)} chars"
    assert len(re.findall(r'"[^"]{3,80}"', desc)) >= 3
    assert not re.search(r"\byou\b", desc, re.IGNORECASE)
    navin = json.loads(meta["metadata"])["navin"]
    assert navin["category"] in ("development", "writing")
    assert navin.get("emoji")


@pytest.mark.parametrize("name", PACK)
def test_no_license_notice_or_attribution_file(name):
    """The skills are original Navin work: no NOTICE.txt and no Apache-2.0 mention anywhere."""
    folder = SKILLS / name
    assert not (folder / "NOTICE.txt").exists()
    bad = [
        str(p.relative_to(ROOT))
        for p in _text_files(folder)
        if re.search(r"apache\s*(license)?\s*2", p.read_text(encoding="utf-8", errors="replace"), re.IGNORECASE)
    ]
    assert bad == []


@pytest.mark.parametrize("name", PACK)
def test_no_long_dashes(name):
    bad = [str(p.relative_to(ROOT)) for p in _text_files(SKILLS / name) if re.search("[\u2013\u2014]", p.read_text(encoding="utf-8", errors="replace"))]
    assert bad == []


@pytest.mark.parametrize("name", PACK)
def test_no_upstream_vendor_names(name):
    bad = []
    for path in _text_files(SKILLS / name):
        if re.search(r"claude|anthropic", path.read_text(encoding="utf-8", errors="replace"), re.IGNORECASE):
            bad.append(str(path.relative_to(ROOT)))
    assert bad == []


@pytest.mark.parametrize("name", PACK)
def test_validate_skill_passes(name):
    result = _load_validator().lint(SKILLS / name)
    assert result["passed"], result["critical"]
    assert result["critical"] == []


@pytest.mark.parametrize("name", PACK)
def test_body_has_workflow(name):
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert "## Workflow" in text


def test_shell_scripts_parse():
    scripts = list(ARTIFACTS.glob("scripts/*.sh"))
    assert len(scripts) == 2
    for script in scripts:
        res = _run(["bash", "-n", str(script)])
        assert res.returncode == 0, res.stderr
        assert os.access(script, os.X_OK), f"{script.name} not executable"


# ---------------------------------------------------------------- webapp-testing


def test_with_server_propagates_exit_code_and_stops_server(tmp_path):
    port = _free_port()
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    inner = (
        "import urllib.request,sys;"
        f"b=urllib.request.urlopen('http://127.0.0.1:{port}/index.html',timeout=5).read();"
        "assert b'hi' in b;sys.exit(3)"
    )
    res = _run(
        [
            sys.executable,
            str(WEBAPP / "scripts" / "with_server.py"),
            "--server",
            f"{sys.executable} -m http.server {port} --bind 127.0.0.1",
            "--port",
            str(port),
            "--timeout",
            "15",
            "--",
            sys.executable,
            "-c",
            inner,
        ],
        cwd=tmp_path,
    )
    assert res.returncode == 3, res.stderr
    assert "port" in res.stderr and "ready" in res.stderr
    with socket.socket() as sock:
        sock.settimeout(1)
        assert sock.connect_ex(("127.0.0.1", port)) != 0, "server still listening after with_server exited"


def test_with_server_help_and_bad_args():
    script = str(WEBAPP / "scripts" / "with_server.py")
    assert _run([sys.executable, script, "--help"]).returncode == 0
    assert _run([sys.executable, script, "--server", "x", "--", "true"]).returncode == 2


def _chromium_available() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return False
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", Path.home() / ".cache" / "ms-playwright"))
    return any(cache.glob("chromium*")) if cache.is_dir() else False


needs_chromium = pytest.mark.skipif(not _chromium_available(), reason="playwright chromium not installed")

SAMPLE_HTML = """<!doctype html><html><head><title>Sample</title></head><body>
<h1>Sample form</h1>
<form onsubmit="event.preventDefault();document.getElementById('out').textContent='Thanks '+document.getElementById('name').value;console.error('boom')">
<input id="name" name="name" placeholder="Name"><input id="email" name="email" type="email">
<button type="submit">Send</button></form><a href="#top">Top</a><p id="out"></p>
</body></html>"""


@needs_chromium
def test_discover_elements_example(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(SAMPLE_HTML)
    res = _run([sys.executable, str(WEBAPP / "examples" / "discover_elements.py"), page.as_uri()], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["title"] == "Sample"
    assert any(b["text"] == "Send" for b in data["buttons"])
    assert {i.get("id") for i in data["inputs"]} == {"name", "email"}
    assert data["links"][0]["href"] == "#top"


@needs_chromium
def test_console_errors_example(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(SAMPLE_HTML)
    script = str(WEBAPP / "examples" / "console_errors.py")
    clean = _run([sys.executable, script, page.as_uri()], cwd=tmp_path)
    assert clean.returncode == 0, clean.stderr
    assert json.loads(clean.stdout)["ok"] is True
    noisy = _run([sys.executable, script, page.as_uri(), "--click", "button[type=submit]"], cwd=tmp_path)
    assert noisy.returncode == 1
    report = json.loads(noisy.stdout)
    assert any(c["level"] == "error" and "boom" in c["text"] for c in report["console"])


@needs_chromium
def test_static_page_example(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(SAMPLE_HTML)
    res = _run([sys.executable, str(WEBAPP / "examples" / "static_page.py"), page.as_uri(), "--out", str(tmp_path / "ev")], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    steps = {s["step"] for s in data["steps"]}
    assert {"load", "fill name", "fill email", "submit"} <= steps
    assert "Thanks Ada Lovelace" in data["steps"][-1]["body_excerpt"]
    assert (tmp_path / "ev" / "01-loaded.png").is_file()
    assert (tmp_path / "ev" / "02-submitted.png").is_file()


# ---------------------------------------------------------------- mcp-builder


def test_evaluate_validate_only(tmp_path):
    script = str(MCP / "scripts" / "evaluate.py")
    assert _run([sys.executable, script, "--help"]).returncode == 0
    good = _run([sys.executable, script, "--validate-only", str(MCP / "scripts" / "evaluation.example.json")])
    assert good.returncode == 0, good.stderr + good.stdout
    broken = tmp_path / "broken.json"
    broken.write_text('{"not": "closed"')
    assert _run([sys.executable, script, "--validate-only", str(broken)]).returncode != 0


def test_connections_help():
    assert _run([sys.executable, str(MCP / "scripts" / "connections.py"), "--help"]).returncode == 0


# ---------------------------------------------------------------- web-artifacts-builder


def test_init_artifact_dry_run(tmp_path):
    script = str(ARTIFACTS / "scripts" / "init-artifact.sh")
    res = _run(["bash", script, "demo", "--dry-run"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "Dry run" in res.stdout
    assert not (tmp_path / "demo" / "node_modules").exists()
    blob = "\n".join(p.read_text() for p in (tmp_path / "demo").rglob("*") if p.is_file())
    for needle in ("@mui/material", "@mui/icons-material", "@emotion/react", "@emotion/styled", "framer-motion", "vite-plugin-singlefile", "#c6e88a", "#17171a", "#f4f3ee", "#0e7490", "#7c3aed", "<Dialog", "<TextField", "<AppBar"):
        assert needle in blob, needle
    assert "tailwind" not in blob.lower()
    assert "@react-three/fiber" not in blob
    pkg = json.loads((tmp_path / "demo" / "package.json").read_text())
    assert pkg["name"] == "demo"
    # refuses to overwrite
    assert _run(["bash", script, "demo", "--dry-run"], cwd=tmp_path).returncode == 1


def test_init_artifact_three(tmp_path):
    script = str(ARTIFACTS / "scripts" / "init-artifact.sh")
    res = _run(["bash", script, "scene", "--three", "--dry-run"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    pkg = json.loads((tmp_path / "scene" / "package.json").read_text())
    assert "@react-three/fiber" in pkg["dependencies"]
    assert "@react-three/drei" in pkg["dependencies"]
    assert "three" in pkg["dependencies"]
    assert (tmp_path / "scene" / "src" / "Scene.tsx").is_file()
    assert 'import Scene from "./Scene"' in (tmp_path / "scene" / "src" / "App.tsx").read_text()


def test_init_artifact_help_and_errors(tmp_path):
    script = str(ARTIFACTS / "scripts" / "init-artifact.sh")
    assert _run(["bash", script, "--help"]).returncode == 0
    assert _run(["bash", script], cwd=tmp_path).returncode == 2
    assert _run(["bash", script, "x", "--bogus"], cwd=tmp_path).returncode == 2


def test_bundle_artifact_skip_build(tmp_path):
    script = str(ARTIFACTS / "scripts" / "bundle-artifact.sh")
    dist = tmp_path / "proj" / "dist"
    dist.mkdir(parents=True)
    index = dist / "index.html"

    index.write_text('<html><head><script src="/assets/x.js"></script></head><body></body></html>')
    bad = _run(["bash", script, str(tmp_path / "proj"), "--skip-build"])
    assert bad.returncode == 1
    assert "external script" in bad.stderr

    index.write_text('<html><head><link rel="stylesheet" href="/assets/x.css"></head><body></body></html>')
    assert _run(["bash", script, str(tmp_path / "proj"), "--skip-build"]).returncode == 1

    index.write_text('<html><head><style>body{margin:0}</style><script type="module">console.log(1)</script></head><body></body></html>')
    good = _run(["bash", script, str(tmp_path / "proj"), "--skip-build"])
    assert good.returncode == 0, good.stderr
    assert "self-contained" in good.stdout and "KB" in good.stdout

    (dist / "assets").mkdir()
    (dist / "assets" / "x.js").write_text("1")
    extra = _run(["bash", script, str(tmp_path / "proj"), "--skip-build"])
    assert extra.returncode == 1 and "not inlined" in extra.stderr
    shutil.rmtree(dist / "assets")

    (dist / "favicon.ico").write_bytes(b"\x00")
    assert _run(["bash", script, str(tmp_path / "proj"), "--skip-build"]).returncode == 0

    index.unlink()
    assert _run(["bash", script, str(tmp_path / "proj"), "--skip-build"]).returncode == 1


# ---------------------------------------------------------------- routing


ROUTING_PROMPTS = [
    ("doc-coauthoring", "Coécris ce document avec moi, on itère section par section"),
    ("doc-coauthoring", "Help me co-author this proposal document collaboratively"),
    ("internal-comms", "Rédige une note interne pour annoncer la réorganisation à l'équipe"),
    ("internal-comms", "Write an internal announcement memo for the team"),
    ("mcp-builder", "Construis un serveur MCP qui expose mon API comme outils"),
    ("mcp-builder", "Build an MCP server with tools and evaluate it"),
    ("webapp-testing", "Teste mon application web avec Playwright, lance le serveur de dev puis les tests"),
    ("webapp-testing", "Run end to end tests on the local web app with a headless browser"),
    ("web-artifacts-builder", "Crée une page web autonome en un seul fichier HTML avec React et MUI"),
    ("web-artifacts-builder", "Build a single-file web artifact with React and framer-motion"),
    ("skill-creator", "Crée un nouveau skill et évalue-le avec des cas de test"),
    ("skill-creator", "Write a new skill and run its evals benchmark"),
]


@pytest.fixture(scope="module")
def skill_router():
    from navin.agent.skills import SkillsLoader
    from navin.agent.skills_graph import build_skills_graph
    from navin.agent.tools.skill_catalog import SkillCatalogTool

    loader = SkillsLoader(ROOT)
    return loader, build_skills_graph(loader), SkillCatalogTool(workspace=ROOT)


@pytest.mark.parametrize(("skill", "prompt"), ROUTING_PROMPTS, ids=[f"{s}-{i}" for i, (s, _p) in enumerate(ROUTING_PROMPTS)])
def test_development_requests_route_to_their_skill(skill_router, skill: str, prompt: str):
    """French and English development requests reach the re-coded skill on
    every path the agent uses: keyword find, graph ranking, per-turn suggestion."""
    loader, graph, tool = skill_router
    found = [row["name"] for row in loader.search_skills(prompt, limit=5)]
    ranked = [row["name"] for row in graph.suggest(prompt, limit=5)]
    suggested = [row["name"] for row in tool._suggest_for_request(prompt)]
    assert skill in found[:3], f"find: {found}"
    assert skill in ranked[:3], f"graph: {ranked}"
    assert skill in suggested, f"auto-suggest: {suggested}"
