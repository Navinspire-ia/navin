import asyncio
from typing import Any

from navin.agent import error_registry
from navin.agent.constraint_guard import check_call, parse_constraints
from navin.agent.error_registry import find_errors, load_errors, record_error
from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.recall import RecallTool
from navin.agent.tools.registry import ToolRegistry
from navin.cognition.settings import write_settings

MEM = """# Memory
## Constraints
- never write path: secrets/*
- forbid command: rm -rf
- interdit commande: git push --force
## Other
- never write path: other/*
"""


def test_registry_roundtrip(tmp_path):
    assert record_error(tmp_path, "ModuleNotFoundError yaml", "pyyaml missing", "pip install pyyaml")
    assert not record_error(tmp_path, "modulenotfounderror YAML", "x", "y")
    hits = find_errors(tmp_path, "got ModuleNotFoundError yaml on import")
    assert hits and hits[0]["fix"] == "pip install pyyaml"
    assert find_errors(tmp_path, "unrelated") == []


def test_parse_constraints_only_section():
    rules = parse_constraints(MEM)
    assert [(r.kind, r.value) for r in rules] == [
        ("path", "secrets/*"),
        ("command", "rm -rf"),
        ("command", "git push --force"),
    ]


def test_guard(tmp_path):
    (tmp_path / "MEMORY.md").write_text(MEM, encoding="utf-8")
    assert check_call(tmp_path, "write_file", {"path": "secrets/a.txt"})
    assert check_call(tmp_path, "write_file", {"path": "src/a.txt"}) is None
    assert check_call(tmp_path, "exec", {"command": "sudo rm -rf /x"})
    assert check_call(tmp_path, "exec", {"command": "ls"}) is None
    assert check_call(tmp_path, "read_file", {"path": "secrets/a"}) is None


class _SpyTool(Tool):
    def __init__(self, name: str, key: str) -> None:
        self._name = name
        self._key = key
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "spy"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {self._key: {"type": "string"}},
            "required": [self._key],
        }

    async def execute(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return "ran"


def _ctx(workspace) -> RequestContext:
    return RequestContext(channel="cli", chat_id="t", workspace=workspace)


def test_prepare_call_enforces_constraints(tmp_path):
    (tmp_path / "MEMORY.md").write_text(MEM, encoding="utf-8")
    registry = ToolRegistry()
    writer = _SpyTool("write_file", "path")
    runner = _SpyTool("exec", "command")
    registry.register(writer)
    registry.register(runner)
    with request_context(_ctx(tmp_path)):
        _t, _p, err = registry.prepare_call("write_file", {"path": "secrets/key.txt"})
        assert err is not None and err.is_error
        assert "path 'secrets/*' is forbidden" in err
        _t, _p, err = registry.prepare_call("exec", {"command": "git push --force origin"})
        assert err is not None and err.is_error
        assert "command 'git push --force' is forbidden" in err
        assert writer.calls == [] and runner.calls == []
        tool, params, err = registry.prepare_call("write_file", {"path": "src/ok.py"})
        assert err is None and tool is writer and params == {"path": "src/ok.py"}
        _t, _p, err = registry.prepare_call("exec", {"command": "ls -la"})
        assert err is None


def test_recall_returns_known_error_or_clear_empty(tmp_path):
    write_settings(tmp_path, enabled=True, recall=True)
    record_error(tmp_path, "ModuleNotFoundError yaml", "pyyaml missing", "pip install pyyaml")
    tool = RecallTool(workspace=tmp_path)
    with request_context(_ctx(tmp_path)):
        hit = asyncio.run(tool.execute(query="ModuleNotFoundError yaml at import"))
        miss = asyncio.run(tool.execute(query="zzqx unrelated"))
    assert "Known errors" in str(hit) and "pip install pyyaml" in str(hit)
    assert "pyyaml missing" in str(hit)
    assert str(miss).startswith("No past turn or memory note matches 'zzqx unrelated'")


class _ScriptTool(Tool):
    def __init__(self, name: str, key: str, outputs: list[Any]) -> None:
        self._name = name
        self._key = key
        self.outputs = outputs

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "script"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {self._key: {"type": "string"}}, "required": [self._key]}

    async def execute(self, **kwargs: Any) -> Any:
        return self.outputs.pop(0)


FAIL_OUT = "E   ImportError: cannot import name 'frobnicate' from 'pkg'\n\nExit code: 1"


def _registry(outputs: list[Any]) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_ScriptTool("exec", "command", outputs))
    registry.register(_ScriptTool("write_file", "path", ["written"] * 5))
    return registry


def test_failure_then_success_records_fix(tmp_path):
    registry = _registry([FAIL_OUT, "1 passed\n\nExit code: 0"])
    with request_context(_ctx(tmp_path)):
        first = asyncio.run(registry.execute("exec", {"command": "pytest  -q"}))
        asyncio.run(registry.execute("write_file", {"path": "pkg/core.py"}))
        asyncio.run(registry.execute("exec", {"command": "pytest -q"}))
    assert "Erreur deja rencontree" not in first
    entries = load_errors(tmp_path)
    assert len(entries) == 1
    assert "ImportError" in entries[0]["symptom"]
    assert "pkg/core.py" in entries[0]["fix"]
    assert "pytest -q" in entries[0]["cause"]


def test_success_without_failure_records_nothing(tmp_path):
    registry = _registry(["ok\n\nExit code: 0"])
    with request_context(_ctx(tmp_path)):
        asyncio.run(registry.execute("exec", {"command": "pytest -q"}))
    assert load_errors(tmp_path) == []


def test_second_similar_failure_shows_recall_note(tmp_path):
    record_error(tmp_path, "ImportError: cannot import name 'frobnicate' from 'pkg'",
                 "failing check: exec", "modified: pkg/core.py")
    registry = _registry([ToolResult.error(FAIL_OUT)])
    with request_context(_ctx(tmp_path)):
        result = asyncio.run(registry.execute("exec", {"command": "pytest -q"}))
    assert isinstance(result, ToolResult) and result.is_error
    assert "Erreur deja rencontree" in result
    assert "modified: pkg/core.py" in result


def test_record_error_exception_does_not_break_tool(tmp_path, monkeypatch, caplog):
    def boom(*_a: Any, **_k: Any) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(error_registry, "record_error", boom)
    registry = _registry([FAIL_OUT, "1 passed\n\nExit code: 0"])
    with request_context(_ctx(tmp_path)), caplog.at_level("WARNING"):
        asyncio.run(registry.execute("exec", {"command": "pytest -q"}))
        result = asyncio.run(registry.execute("exec", {"command": "pytest -q"}))
    assert result == "1 passed\n\nExit code: 0"
    assert "record_error failed" in caplog.text
