import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "navin"
spec = importlib.util.spec_from_file_location("bsm", ROOT / "scripts" / "build_skill_marketplace.py")
bsm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bsm)


def test_frontmatter_parses_quotes_and_metadata():
    fm = bsm.frontmatter('---\nname: git\ndescription: "Use for git"\nmetadata: {"navin":{"category":"devops"}}\n---\nbody')
    assert fm["name"] == "git"
    assert fm["description"] == "Use for git"
    assert fm["metadata"]["navin"]["category"] == "devops"


def test_frontmatter_missing_or_bad_metadata():
    assert bsm.frontmatter("no header") == {}
    fm = bsm.frontmatter("---\nname: x\nmetadata: {broken\n---\n")
    assert fm == {"name": "x"}


def _fake_pkg(tmp_path: Path, plugin: dict) -> Path:
    pkg = tmp_path / "navin"
    (pkg / "skills" / "a").mkdir(parents=True)
    (pkg / "skills" / "a" / "SKILL.md").write_text('---\nname: a\ndescription: "do a"\nmetadata: {"navin":{"category":"dev","requires":{"bins":["git"]}}}\n---\n')
    (pkg / "skills" / "b").mkdir()
    (pkg / "skills" / "b" / "SKILL.md").write_text("---\ndescription: do b\n---\n")
    (pkg / "builtin_commands").mkdir()
    (pkg / "builtin_commands" / "go.md").write_text('---\ndescription: Go now\nargument-hint: "<x>"\n---\nGo.\n')
    (pkg / "builtin_agents").mkdir()
    (pkg / "builtin_agents" / "helper.md").write_text(
        "---\nname: helper\ndescription: Helps.\n  <example>\n  user: hi\n  </example>\n---\nBody.\n"
    )
    (pkg / "builtin_plugins" / "kit").mkdir(parents=True)
    (pkg / "builtin_plugins" / "kit" / "plugin.json").write_text(json.dumps(plugin))
    return pkg


def test_build_lists_plugins_with_steps_and_skills(tmp_path):
    pkg = _fake_pkg(tmp_path, {
        "name": "kit", "description": "Kit", "commands": ["go"], "agents": ["helper"], "skills": ["a"],
        "steps": [{"step": "go", "use": "/go fast"}, {"step": "help", "use": "agent:helper"}, {"step": "a", "use": "skill:a"}],
    })
    data = bsm.build_manifest(pkg)
    assert data["skills"][0] == {"name": "a", "description": "do a", "skill": "skills/a/SKILL.md",
                                 "category": "dev", "requires": {"bins": ["git"]}}
    assert data["skills"][1]["name"] == "b" and data["skills"][1]["category"] == "general"
    plugin = data["plugins"][0]
    assert plugin["commands"] == [{"name": "/go", "description": "Go now", "file": "builtin_commands/go.md", "argument_hint": "<x>"}]
    assert plugin["agents"] == [{"name": "helper", "description": "Helps.", "file": "builtin_agents/helper.md"}]
    assert plugin["skills"] == [{"name": "a", "skill": "skills/a/SKILL.md"}]
    assert [s["use"] for s in plugin["steps"]] == ["/go fast", "agent:helper", "skill:a"]


@pytest.mark.parametrize("bad", [
    {"commands": ["missing"]},
    {"agents": ["ghost"]},
    {"skills": ["nope"]},
    {"steps": [{"step": "x", "use": "agent:helper"}]},
    {"commands": ["go"], "steps": [{"step": "x", "use": "go"}]},
])
def test_build_rejects_dangling_references(tmp_path, bad):
    pkg = _fake_pkg(tmp_path, {"name": "kit", **bad})
    with pytest.raises(SystemExit):
        bsm.build_manifest(pkg)


def test_check_mode_flags_a_stale_catalog(tmp_path, monkeypatch):
    out = tmp_path / "marketplace.json"
    monkeypatch.setattr(bsm, "OUT", out)
    assert bsm.main(["--check"]) == 1
    assert bsm.main([]) == 0
    assert bsm.main(["--check"]) == 0
    out.write_text("{}\n")
    assert bsm.main(["--check"]) == 1


def test_shipped_catalog_is_current_and_consistent():
    text = (PKG / "marketplace.json").read_text(encoding="utf-8")
    assert text == bsm.render(bsm.build_manifest(PKG)), "run python scripts/build_skill_marketplace.py"
    assert not (ROOT / ".claude-plugin").exists()
    assert not re.search("claude", text, re.I)
    data = json.loads(text)
    assert len(data["skills"]) == len(list((PKG / "skills").glob("*/SKILL.md")))
    for entry in data["skills"]:
        assert entry["description"] and (PKG / entry["skill"]).is_file()
    plugins = {p["name"]: p for p in data["plugins"]}
    assert {
        "feature-dev", "commit-commands", "code-review", "issue-triage", "plugin-dev",
        "pr-review-toolkit", "frontend-design", "hookify", "security-guidance",
        "ralph-loop", "agent-sdk-dev", "model-migration", "explanatory-output-style", "learning-output-style",
    } <= set(plugins)
    assert [c["name"] for c in plugins["security-guidance"]["commands"]] == ["/security-review"]
    assert [c["name"] for c in plugins["hookify"]["commands"]] == [
        "/hookify", "/hookify-list", "/hookify-configure", "/hookify-help",
    ]
    assert [a["name"] for a in plugins["hookify"]["agents"]] == ["conversation-analyzer"]
    assert any(e["name"] == "hookify-rules" for e in data["skills"])
    design_steps = {s["use"] for s in plugins["frontend-design"]["steps"]}
    assert "skill:frontend-design" in design_steps
    assert any(e["name"] == "frontend-design" for e in data["skills"])
    assert [c["name"] for c in plugins["commit-commands"]["commands"]] == ["/commit", "/commit-push-pr", "/clean_gone"]
    assert [a["name"] for a in plugins["feature-dev"]["agents"]] == ["code-explorer", "code-architect", "code-reviewer"]
    assert [c["name"] for c in plugins["pr-review-toolkit"]["commands"]] == ["/review-pr"]
    assert [a["name"] for a in plugins["pr-review-toolkit"]["agents"]] == [
        "code-reviewer", "comment-analyzer", "pr-test-analyzer",
        "silent-failure-hunter", "type-design-analyzer", "code-simplifier",
    ]
    for plugin in data["plugins"]:
        assert plugin["steps"]
        for row in plugin["commands"] + plugin["agents"]:
            assert row["description"] and (PKG / row["file"]).is_file()


def test_prompt_summary_names_the_catalog_and_steps(tmp_path):
    from navin.agent.marketplace import MARKETPLACE_PATH, marketplace_summary

    summary = marketplace_summary()
    assert str(MARKETPLACE_PATH) in summary
    assert "start a new feature -> /feature-dev" in summary
    assert "understand the existing code -> agent:code-explorer" in summary
    assert marketplace_summary(tmp_path / "missing.json") == ""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert marketplace_summary(broken) == ""
