from pathlib import Path

from navin.agent.tools import preview_server as ps


def _setup(monkeypatch, listeners: dict[int, str | None], tmp_path: Path, declared=()):
    monkeypatch.setattr(ps, "loopback_host", lambda port: "127.0.0.1" if port in listeners else None)
    monkeypatch.setattr(ps, "_looks_like_navin_port", lambda port: False)
    monkeypatch.setattr(ps, "http_reachable", lambda url, **_: True)
    monkeypatch.setattr(ps, "workspace_declared_ports", lambda ws: frozenset(declared))

    class L:
        def __init__(self, cwd):
            self.cwd = cwd

    monkeypatch.setattr(ps, "who_listens", lambda port: L(listeners.get(port)))


def test_the_open_project_app_wins_over_another_server(monkeypatch, tmp_path):
    project = tmp_path / "shop"
    other = tmp_path / "crm"
    project.mkdir()
    other.mkdir()
    _setup(monkeypatch, {3000: str(other), 3001: str(project)}, tmp_path)
    assert ps.discover_running_project_url(workspace=project, allow_unproven=True) == "http://127.0.0.1:3001"


def test_the_known_port_is_tried_first_even_when_it_is_a_usual_one(monkeypatch, tmp_path):
    _setup(monkeypatch, {3000: None, 5180: None}, tmp_path)
    assert ps.discover_running_project_url(preferred_port=5180) == "http://127.0.0.1:5180"


def test_reserved_navin_ports_are_never_a_preview(monkeypatch, tmp_path):
    # 5173 is Navin's editor port: another project left there must not be shown,
    # even when it is the remembered port or the only server answering.
    project = tmp_path / "shop"
    project.mkdir()
    _setup(monkeypatch, {5173: str(project), 5174: None, 8767: None}, tmp_path, declared={5173})
    assert ps.discover_running_project_url(preferred_port=5173) is None
    assert ps.discover_running_project_url(workspace=project, preferred_port=5173, allow_unproven=True) is None
    assert ps.is_navin_reserved_port(5173) and ps.is_navin_reserved_port(5176)
    assert not ps.is_navin_reserved_port(5180) and not ps.is_navin_reserved_port(None)


def test_unreadable_folder_only_matches_this_project_port(monkeypatch, tmp_path):
    # A WSL app seen from Windows has no readable folder: any other project on a
    # usual port answered and was previewed. Only the project's own port counts.
    project = tmp_path / "shop"
    project.mkdir()
    _setup(monkeypatch, {3000: None}, tmp_path)
    assert ps.discover_running_project_url(workspace=project) is None
    assert ps.discover_running_project_url(workspace=project, allow_unproven=True) is None
    assert ps.discover_running_project_url(
        workspace=project, preferred_port=3000, allow_unproven=True
    ) == "http://127.0.0.1:3000"


def test_unreadable_folder_on_a_declared_port_is_a_fallback(monkeypatch, tmp_path):
    project = tmp_path / "shop"
    project.mkdir()
    _setup(monkeypatch, {3000: None, 4200: None}, tmp_path, declared={4200})
    assert ps.discover_running_project_url(workspace=project, allow_unproven=True) == "http://127.0.0.1:4200"


def test_declared_ports_come_from_the_project_config(tmp_path):
    project = tmp_path / "shop"
    project.mkdir()
    (project / "package.json").write_text(
        '{"name": "shop", "scripts": {"dev": "vite --port=4321"}, "devDependencies": {"vite": "5"}}',
        encoding="utf-8",
    )
    assert 4321 in ps.workspace_declared_ports(project)
    assert ps.workspace_declared_ports(None) == frozenset()


def test_preview_tools_use_the_conversation_project_not_the_default_folder(monkeypatch, tmp_path):
    # The desktop builds its tools once with ~/NavinProjects: a Guidia chat
    # started NavinProjects' "gestion-projet" app and previewed it on :5177.
    import asyncio

    from navin.agent.tools import open_preview, start_app
    from navin.agent.tools.context import RequestContext, request_context

    default = tmp_path / "NavinProjects"
    project = tmp_path / "guidia"
    default.mkdir()
    project.mkdir()
    seen: list[Path | None] = []

    async def fake_ensure(*, workspace, **_):
        seen.append(workspace)
        return "http://127.0.0.1:3000", None

    monkeypatch.setattr(open_preview, "ensure_project_preview_url", fake_ensure)
    monkeypatch.setattr(start_app, "ensure_project_preview_url", fake_ensure)
    monkeypatch.setattr(open_preview, "emit_preview_open_request", lambda **_: None)
    monkeypatch.setattr(start_app, "discover_project_dev_servers", lambda ws: [])

    async def run():
        with request_context(RequestContext(channel="websocket", chat_id="c", workspace=project)):
            await open_preview.OpenPreviewTool(bus=None, workspace=default).execute(kind="web")
            await start_app.StartAppTool(workspace=default).execute()
        # Outside a conversation the tool keeps its own folder.
        await start_app.StartAppTool(workspace=default).execute()

    asyncio.run(run())
    assert seen == [project, project, default]
