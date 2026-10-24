import asyncio
import json

import pytest

from navin.agent.tools.computer import _SESSIONS, ComputerTool, ComputerToolConfig
from navin.agent.tools.context import request_context
from navin.computer.base import WindowInfo
from navin.computer.permissions import AppPermissions, required_level
from navin.computer.route_first import route
from navin.webui.stall_watch import StallReporter
from navin.webui.ws_http import GatewayHTTPHandler
from tests.test_computer_tool import FakeBackend, _ctx


def test_levels_and_check():
    p = AppPermissions({"Firefox": "click", "Term*": "launch"})
    assert p.check("left_click", "Mozilla Firefox").allowed
    assert not p.check("type", "Mozilla Firefox").allowed
    assert p.check("focus_window", "Terminal").allowed
    assert p.check("type", "Other").allowed
    assert required_level("screenshot") == "see"


def test_persistence(tmp_path):
    path = tmp_path / ".navin" / "computer_permissions.json"
    p = AppPermissions(path=path)
    p.set("Gimp", "see")
    p.save()
    q = AppPermissions.load(path)
    assert q.rules == {"Gimp": "see"}
    assert not q.check("left_click", "GIMP").allowed


def test_route():
    assert route("create a github issue", which=lambda b: "/x/gh" if b == "gh" else None).kind == "cli"
    assert route("paint a picture", which=lambda b: None).kind == "screen"


# -- the computer tool enforces the rules ------------------------------------


class _ActiveBackend(FakeBackend):
    def active_window(self) -> WindowInfo:
        return self.windows()[0]  # app "notes"


def _run(tool: ComputerTool, **kwargs):
    async def go():
        with request_context(_ctx()):
            return await tool.execute(**kwargs)

    return asyncio.run(go())


@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _SESSIONS.clear()
    backend = _ActiveBackend()
    config = ComputerToolConfig(
        enabled=True, ask="never", settle_ms=0, audit_log=False,
        audit_screenshots=False, live_view=False,
    )
    t = ComputerTool(config=config, backend_factory=lambda: backend)
    t.backend_for_test = backend
    yield t
    _SESSIONS.clear()


def _rules(tmp_path, rules):
    p = AppPermissions(rules, path=tmp_path / ".navin" / "computer_permissions.json")
    p.save()


def test_tool_refuses_action_above_app_level(tool, tmp_path):
    _rules(tmp_path, {"notes": "see"})
    _run(tool, action="screenshot")
    result = _run(tool, action="left_click", coordinate=[10, 10], screenshot=False)
    assert str(result).startswith("Error:")
    assert "limited to 'see'" in str(result)
    assert not [c for c in tool.backend_for_test.calls if c[0] == "click"]


def test_tool_allows_action_within_app_level(tool, tmp_path):
    _rules(tmp_path, {"notes": "click"})
    _run(tool, action="screenshot")
    result = _run(tool, action="left_click", coordinate=[10, 10], screenshot=False)
    assert "left_click at" in str(result)
    assert [c for c in tool.backend_for_test.calls if c[0] == "click"]


def test_focus_window_suggests_installed_cli(tool, monkeypatch):
    monkeypatch.setattr(
        "navin.computer.route_first.shutil.which", lambda b: "/usr/bin/docker" if b == "docker" else None
    )
    _run(tool, action="screenshot")
    with monkeypatch.context() as m:
        m.setattr(_ActiveBackend, "windows", lambda self: [
            WindowInfo(id="3", title="Docker Desktop", left=0, top=0, width=10, height=10, app="docker"),
        ])
        result = _run(tool, action="focus_window", window="Docker", screenshot=False)
    assert "focused window 3" in str(result)
    assert "prefer the CLI `docker`" in str(result)


# -- WebUI route ------------------------------------------------------------


class _Request:
    def __init__(self, path: str) -> None:
        self.path = path


class _Log:
    def exception(self, *args, **kwargs) -> None:
        pass


def _handler() -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = lambda request: True
    handler._log = _Log()
    handler._stalls = StallReporter(handler._log)
    return handler


def _call(handler, path):
    op = path.split("?")[0].rsplit("/", 1)[-1]
    resp = handler._handle_webui_computer_permissions(_Request(path), op)
    return resp.status_code, json.loads(resp.body) if resp.status_code == 200 else None


def test_api_lists_upserts_and_deletes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = _handler()
    assert _call(h, "/api/webui/computer-permissions") == (
        200, {"levels": ["see", "click", "type", "launch"], "rules": []},
    )
    status, body = _call(h, "/api/webui/computer-permissions/update?app=Firefox&level=click")
    assert status == 200 and body["rules"] == [{"app": "Firefox", "level": "click"}]
    status, body = _call(h, "/api/webui/computer-permissions/update?app=Firefox&level=type")
    assert body["rules"] == [{"app": "Firefox", "level": "type"}]
    assert AppPermissions.load().rules == {"Firefox": "type"}
    status, body = _call(h, "/api/webui/computer-permissions/delete?app=Firefox")
    assert status == 200 and body["rules"] == []


def test_api_rejects_bad_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = _handler()
    assert _call(h, "/api/webui/computer-permissions/update?app=X&level=root")[0] == 400
    assert _call(h, "/api/webui/computer-permissions/update?app=&level=see")[0] == 400
    assert _call(h, "/api/webui/computer-permissions/delete?app=Nope")[0] == 400


# -- audit trail read back (API + view) ---------------------------------------


def _write_trail(root, session: str, rows: list, meta: dict | None = None) -> None:
    from navin.computer.safety import AuditLog

    log = AuditLog(directory=root / session, keep_screenshots=True)
    if meta is not None:
        log.write_meta(meta)
    for row, shot in rows:
        log.record(row, screenshot_png=shot)


def test_audit_view_lists_and_reads_sessions(tmp_path):
    from navin.computer.audit_view import AuditViewError, list_sessions, read_session

    _write_trail(tmp_path, "s1", [
        ({"action": "screenshot", "result": "ok"}, b"png"),
        ({"action": "type", "args": {"text": "x"}, "denied": "level see", "rule": "Firefox"}, None),
    ], meta={"session": "s1", "chat": "websocket:a", "started": "2026-10-23 10:00:00"})
    (tmp_path / "s1" / "actions.jsonl").open("a", encoding="utf-8").write("not json\n")
    (tmp_path / "empty-folder").mkdir()

    sessions = list_sessions(tmp_path)
    assert [s["id"] for s in sessions] == ["s1"]
    assert sessions[0] | {} == {
        "id": "s1", "chat": "websocket:a", "started": "2026-10-23 10:00:00", "ask": "",
        "backend": "", "steps": 2, "denied": 1, "broken": 1,
    }
    detail = read_session(tmp_path, "s1")
    assert detail["total"] == 2 and detail["broken"] == 1
    first, second = detail["steps"]
    assert first["action"] == "screenshot" and first["screenshot"] == "step-0001.png"
    assert second == {
        "step": 2, "t": second["t"], "action": "type", "args": {"text": "x"}, "result": "",
        "denied": "level see", "rule": "Firefox", "screenshot": "",
    }
    assert [s["step"] for s in read_session(tmp_path, "s1", last=1)["steps"]] == [2]
    for bad in ("../s1", "s1/../s1", "", ".hidden"):
        with pytest.raises(AuditViewError):
            read_session(tmp_path, bad)
    with pytest.raises(AuditViewError, match="no such session"):
        read_session(tmp_path, "missing")
    assert list_sessions(tmp_path / "nope") == []


def _audit(handler, path):
    resp = asyncio.run(handler._handle_webui_computer_audit(_Request(path)))
    return resp.status_code, json.loads(resp.body) if resp.status_code == 200 else None


def test_audit_api_lists_reads_and_rejects(tmp_path, monkeypatch):
    import navin.webui.computer_permissions_api as api

    _write_trail(tmp_path, "abc123", [({"action": "left_click", "result": "clicked"}, None)])
    monkeypatch.setattr(api, "audit_root", lambda: tmp_path)
    h = _handler()
    status, body = _audit(h, "/api/webui/computer-audit")
    assert status == 200 and [s["id"] for s in body["sessions"]] == ["abc123"]
    status, body = _audit(h, "/api/webui/computer-audit?session=abc123&last=5")
    assert status == 200 and body["session"]["steps"][0]["result"] == "clicked"
    assert _audit(h, "/api/webui/computer-audit?session=missing")[0] == 404
    assert _audit(h, "/api/webui/computer-audit?session=..%2Fetc")[0] == 400
    assert _audit(h, "/api/webui/computer-audit?last=many")[0] == 400
    h.check_api_token = lambda request: False
    assert _audit(h, "/api/webui/computer-audit")[0] == 401
