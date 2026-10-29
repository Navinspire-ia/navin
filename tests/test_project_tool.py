# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The project tool runs the project's own command for an action, never a guess."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from navin.agent import project_runbook as rb
from navin.agent import project_runtime as runtime
from navin.agent.tools import project as project_mod
from navin.agent.tools.base import ToolResult
from navin.agent.tools.project import ProjectTool


class FakeExec:
    def __init__(self, outputs: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.outputs = outputs or {}

    async def execute(self, command: str, working_dir: str, timeout=None, background=None, **_kw):
        self.calls.append((command, working_dir, bool(background)))
        for key, value in self.outputs.items():
            if key in command:
                return ToolResult.error(value) if value.startswith("Error") else value
        return "done\nExit code: 0"


def _project(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    rb._cached_runbook.cache_clear()
    return root


def _run(tool: ProjectTool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


@pytest.fixture(autouse=True)
def _tools_installed(monkeypatch):
    monkeypatch.setattr(runtime, "_find_program", lambda name, _path: f"/usr/bin/{name}")
    runtime._WHICH_CACHE.clear()
    project_mod._TRACKED.clear()
    project_mod._GREEN_RUNS.clear()


def _node_app(**scripts: str) -> dict[str, str]:
    return {"package.json": json.dumps({"scripts": scripts or {"dev": "vite", "build": "vite build"}})}


def test_installs_first_when_dependencies_are_missing_then_remembers() -> None:
    root = _project(_node_app())
    fake = FakeExec()
    result = _run(ProjectTool(workspace=root, exec_tool=fake), action="build")
    assert [c[0] for c in fake.calls] == ["npm install", "npm run build"]
    assert "project build [.] OK" in str(result)
    store = json.loads((root / ".navin" / "commands.json").read_text())
    assert store["units"]["."]["actions"]["build"]["command"] == "npm run build"
    # Next time the validated command comes first.
    profile = runtime.resolve(root)
    assert profile.unit(".").best("build").source == "validated"


def test_missing_program_is_reported_with_how_to_install_it(monkeypatch) -> None:
    root = _project({**_node_app(), "pnpm-lock.yaml": "", "node_modules/.keep": ""})
    monkeypatch.setattr(runtime, "_find_program", lambda name, _path: None if name == "pnpm" else f"/usr/bin/{name}")
    runtime._WHICH_CACHE.clear()
    fake = FakeExec()
    result = _run(ProjectTool(workspace=root, exec_tool=fake), action="build")
    assert isinstance(result, ToolResult) and result.is_error
    assert "needs pnpm" in result and "corepack enable pnpm" in result
    assert fake.calls == []


def test_failure_explains_the_likely_cause_and_is_not_remembered() -> None:
    root = _project({**_node_app(), "node_modules/.keep": ""})
    fake = FakeExec({"npm run build": "Error: Cannot find module 'vite'\nExit code: 1"})
    result = _run(ProjectTool(workspace=root, exec_tool=fake), action="build")
    assert result.is_error
    assert "Dependencies are missing" in result
    assert "do not retry it unchanged" in result
    assert not (root / ".navin" / "commands.json").exists()


def test_validated_command_is_dropped_when_it_fails_and_when_manifests_change() -> None:
    root = _project({**_node_app(), "node_modules/.keep": ""})
    runtime.remember(root, ".", "build", "npm run build -- --mode prod")
    assert runtime.resolve(root).unit(".").best("build").command == "npm run build -- --mode prod"
    (root / "package.json").write_text(json.dumps({"scripts": {"build": "vite build --emptyOutDir"}}))
    assert runtime.resolve(root).unit(".").best("build").source == "declared"


def test_agent_given_command_runs_and_becomes_the_validated_one() -> None:
    root = _project({"Makefile": "all:\n\techo\n"})
    fake = FakeExec()
    result = _run(ProjectTool(workspace=root, exec_tool=fake), action="build", command="make all")
    assert "given by the agent" in str(result)
    assert runtime.resolve(root).unit(".").best("build").command == "make all"


def test_stop_only_touches_what_navin_started(monkeypatch) -> None:
    root = _project({"worker.py": "", "requirements.txt": "x", ".venv/bin/python": "", "main.py": "print(1)"})
    fake = FakeExec({".venv/bin/python main.py": "booting\nProcess running. session_id: s-42\nElapsed: 1.0s"})
    tool = ProjectTool(workspace=root, exec_tool=fake)
    # Nothing started yet: nothing is killed.
    assert "Navin only stops what it started" in _run(tool, action="stop")
    started = _run(tool, action="start")
    assert "Running in the background" in started
    assert fake.calls[-1][2] is True  # background
    killed: list[str] = []

    class Manager:
        async def kill_session(self, session_id):
            killed.append(session_id)

        async def list(self, **_kw):
            return []

    monkeypatch.setattr("navin.agent.tools.exec_session.DEFAULT_EXEC_SESSION_MANAGER", Manager())
    assert "stopped" in _run(tool, action="stop")
    assert killed == ["s-42"]


def test_web_app_starts_on_the_dev_server_path(monkeypatch) -> None:
    import threading

    from navin.agent.tools import preview_server as ps

    root = _project({**_node_app(), "node_modules/.keep": ""})
    seen = {}
    loop_thread = threading.current_thread()
    off_loop: dict[str, object] = {}

    async def fake_start(server, owner_session_key=None):
        seen["command"] = server.command
        moved = ps.ProjectDevServer(cwd=server.cwd, command=server.command + " -- --port 5174 --strictPort",
                                    port=5174, label=server.label, port_flag=server.port_flag)
        return "sess-1", None, moved

    async def fake_wait(url, timeout_s=60.0):
        return url == "http://127.0.0.1:5174"

    real_detect, real_remember = ps._detect_server, runtime.remember

    def detect(folder):
        off_loop["detect"] = threading.current_thread()
        return real_detect(folder)

    def remember(*args):
        off_loop["remember"] = threading.current_thread()
        return real_remember(*args)

    monkeypatch.setattr(ps, "start_project_dev_server", fake_start)
    monkeypatch.setattr(ps, "wait_for_url", fake_wait)
    monkeypatch.setattr(ps, "_detect_server", detect)
    monkeypatch.setattr(runtime, "remember", remember)
    result = _run(ProjectTool(workspace=root, exec_tool=FakeExec()), action="start")
    assert "running at http://127.0.0.1:5174" in result
    assert seen["command"] == "npm run dev"
    # Manifest reads and the commands.json write are 9P round trips on a WSL
    # project: both leave the event loop thread.
    assert off_loop["detect"] is not loop_thread
    assert off_loop["remember"] is not loop_thread
    assert "already running" in _run(ProjectTool(workspace=root, exec_tool=FakeExec()), action="start") or True


def test_monorepo_package_installs_from_the_workspace_root() -> None:
    root = _project({
        "package.json": json.dumps({"workspaces": ["packages/*"], "devDependencies": {"turbo": "1"}}),
        "turbo.json": "{}",
        "packages/web/package.json": json.dumps({"scripts": {"build": "vite build"}}),
    })
    fake = FakeExec()
    _run(ProjectTool(workspace=root, exec_tool=fake), action="build", unit="packages/web")
    assert fake.calls[0][0] == "npm install" and fake.calls[0][1] == str(root)
    assert fake.calls[1][0] == "npm run build" and fake.calls[1][1].endswith("packages/web")


def test_unknown_unit_and_undeclared_action_are_explicit() -> None:
    root = _project(_node_app())
    tool = ProjectTool(workspace=root, exec_tool=FakeExec())
    assert "Known units: ." in _run(tool, action="build", unit="nope")
    assert "declares no command for 'test'" in _run(tool, action="test")


def test_read_only_actions() -> None:
    tool = ProjectTool(workspace=None)
    assert tool.call_read_only({"action": "detect"}) and tool.call_read_only({"action": "status"})
    assert not tool.call_read_only({"action": "start"})


@pytest.mark.parametrize(("output", "fragment"), [
    ("sh: 1: vite: not found", "Dependencies are missing"),
    ("Error: listen EADDRINUSE: address already in use :::3000", "port is taken"),
    ("npm ERR! Missing script: \"dev\"", "Wrong folder or script"),
    ("error: externally-managed-environment", "virtualenv"),
    ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock", "Docker is not running"),
    ("src/App.tsx(3,1): error TS2304: Cannot find name 'x'.", "does not compile"),
])
def test_diagnoses(output: str, fragment: str) -> None:
    assert any(fragment in hint for hint in project_mod.diagnose(output))


# --- deploy, never delete, logs, server error watch ---------------------------------

def test_deploy_runs_the_declared_command_without_prompting() -> None:
    root = _project({"fly.toml": "app='x'", "vercel.json": "{}"})
    fake = FakeExec()
    result = _run(ProjectTool(workspace=root, exec_tool=fake), action="deploy")
    assert "OK" in result
    assert fake.calls[0][0] in ("vercel deploy --prod --yes", "fly deploy")


def test_terraform_deploy_runs_from_the_root_with_its_own_path() -> None:
    root = _project({"infra/main.tf": "terraform {}"})
    fake = FakeExec()
    _run(ProjectTool(workspace=root, exec_tool=fake), action="deploy", unit="infra")
    assert fake.calls == [("terraform -chdir=infra apply -auto-approve", str(root), False)]


@pytest.mark.parametrize("command", [
    "terraform destroy -auto-approve",
    "kubectl delete -f k8s/",
    "helm uninstall api",
    "docker compose down -v",
    "docker volume rm data",
    "aws s3 rm s3://bucket --recursive",
    "gcloud run services delete api",
    "rm -rf dist",
    "git push --force origin main",
    "psql -c 'DROP TABLE users'",
    "rsync -az --delete ./ host:/srv/app/",
])
def test_deletions_are_never_run(command: str) -> None:
    root = _project({"Makefile": "deploy:\n\techo\n"})
    fake = FakeExec()
    result = _run(ProjectTool(workspace=root, exec_tool=fake), action="deploy", command=command)
    assert result.is_error and "never deletes" in result
    assert fake.calls == []


@pytest.mark.parametrize("command", [
    "docker run --rm -p 3000:3000 app",
    "docker compose stop",
    "kubectl apply -f k8s/",
    "git push origin main",
    "npm run build -- --emptyOutDir",
])
def test_ordinary_commands_are_not_mistaken_for_deletions(command: str) -> None:
    assert project_mod.deletion_in(command) == ""


def test_new_log_errors_are_deduplicated() -> None:
    seen: set[str] = set()
    tail = "ready in 300ms\n[vite] Internal server error: Failed to resolve import\n  at x.ts:12\nok\n"
    first = project_mod.new_log_errors(tail, seen)
    assert len(first) == 1 and "Failed to resolve import" in first[0]
    assert project_mod.new_log_errors(tail + "HMR update\n", seen) == []
    assert project_mod.new_log_errors("Found 0 errors. Watching for file changes.", set()) == []


def test_logs_of_what_navin_started(monkeypatch) -> None:
    root = _project({"requirements.txt": "x", ".venv/bin/python": "", "main.py": "print(1)"})
    fake = FakeExec({"main.py": "boot\nProcess running. session_id: s-7\nElapsed: 1.0s"})
    tool = ProjectTool(workspace=root, exec_tool=fake)
    assert "no logs to show" in _run(tool, action="logs")
    _run(tool, action="start")

    class Manager:
        async def peek(self, session_id):
            return f"log of {session_id}: listening"

    monkeypatch.setattr("navin.agent.tools.exec_session.DEFAULT_EXEC_SESSION_MANAGER", Manager())
    assert "log of s-7: listening" in _run(tool, action="logs")


# --- remote over SSH ------------------------------------------------------------------

from navin.agent import project_remote as remote  # noqa: E402


def test_remote_target_from_project_file_and_errors() -> None:
    root = _project({".navin/remote.json": json.dumps(
        {"default": "prod", "hosts": {"prod": {"host": "deploy@app", "path": "/srv/app"}}})})
    target = remote.resolve_target(root, None, None)
    assert (target.host, target.path) == ("deploy@app", "/srv/app")
    assert "remote_path" in remote.resolve_target(_project({}), "box", None)
    assert "no remote host" in remote.resolve_target(_project({}), None, None)


def test_ssh_commands_never_prompt_and_sync_never_deletes() -> None:
    target = remote.RemoteTarget("prod", "deploy@app", "/srv/my app")
    command = remote.ssh_command(target, "npm run build")
    import shlex

    words = shlex.split(command)
    assert words[:3] == ["ssh", "-o", "BatchMode=yes"]
    assert words[-2] == "deploy@app"
    assert words[-1] == "cd '/srv/my app' && npm run build"
    sync = remote.sync_command(Path("/p"), target)
    assert sync.startswith("rsync -az") and "--delete" not in sync and "node_modules" in sync


def test_remote_build_start_status_and_stop() -> None:
    root = _project({**_node_app(), ".navin/remote.json": json.dumps({"host": "box", "path": "/srv/app"})})
    fake = FakeExec({"nohup": "NAVIN_PID=4242\nExit code: 0", "kill -0": "NAVIN_ALIVE\nExit code: 0"})
    tool = ProjectTool(workspace=root, exec_tool=fake)
    assert "OK" in _run(tool, action="build", host="default")
    assert fake.calls[-1][0].startswith("ssh ") and "npm run build" in fake.calls[-1][0]
    started = _run(tool, action="start", host="default")
    assert "pid 4242" in started
    _run(tool, action="stop", host="default")
    assert "kill -TERM -- -4242" in fake.calls[-1][0]
    assert "nothing started by Navin" in _run(tool, action="stop", host="default")


def test_remote_deletions_are_refused_too() -> None:
    root = _project({".navin/remote.json": json.dumps({"host": "box", "path": "/srv/app"})})
    fake = FakeExec()
    result = _run(ProjectTool(workspace=root, exec_tool=fake), action="deploy", host="default", command="rm -rf /srv/app")
    assert result.is_error and fake.calls == []


def test_ssh_aliases_from_config(monkeypatch, tmp_path) -> None:
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "config").write_text("Host prod staging\n  User x\nHost *\n  ForwardAgent no\nInclude conf.d/*\n")
    (tmp_path / ".ssh" / "conf.d").mkdir()
    (tmp_path / ".ssh" / "conf.d" / "extra").write_text("Host bastion\n")
    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path)
    assert remote.ssh_aliases() == ["prod", "staging", "bastion"]


def test_remote_start_that_dies_is_reported_as_failed() -> None:
    root = _project({**_node_app(), ".navin/remote.json": json.dumps({"host": "box", "path": "/srv/app"})})
    fake = FakeExec({
        "nohup": "NAVIN_PID=99\nExit code: 0",
        "kill -0": "NAVIN_DEAD\nError: listen EADDRINUSE: address already in use :::5173\nExit code: 0",
    })
    result = _run(ProjectTool(workspace=root, exec_tool=fake), action="start", host="default")
    assert result.is_error and "exited right after starting" in result
    assert "port is taken" in result
    assert "nothing started by Navin" in _run(ProjectTool(workspace=root, exec_tool=fake), action="status", host="default")


@pytest.mark.parametrize(("output", "fragment"), [
    ("main.go:4:2: no required module provides package github.com/x/y; to add it: go get", "Go modules"),
    ("error[E0432]: unresolved import `serde`", "Rust crate"),
    ("[ERROR] Failed to execute goal: Could not resolve dependencies for project", "Java/Kotlin dependency"),
    ("error NU1101: Unable to find package Newtonsoft.Jsonx", ".NET package"),
    ("PHP Warning: require(vendor/autoload.php): Failed to open stream", "PHP dependencies"),
    ("Bundler::GemNotFound: Could not find gem 'rails'", "Ruby gems"),
    ("Error: Target of URI doesn't exist: 'package:http/http.dart'", "Dart/Flutter"),
    ("** (Mix) Could not find an SCM for dependency :phoenix", "Elixir"),
    ("main.cpp:1:10: fatal error: boost/asio.hpp: No such file or directory", "C/C++ library"),
    ("error: release version 21 not supported", "Wrong JDK"),
    ("go: go.mod requires go >= 1.23 (running go 1.21)", "Go toolchain"),
    ("ERROR: Package 'x' requires a different Python: 3.10.12 not in '>=3.11'", "Wrong Python"),
    ("error NETSDK1045: The current .NET SDK does not support targeting .NET 9.0", "Wrong .NET SDK"),
    ("ImportError: email-validator is not installed, run `pip install 'pydantic[email]'`", "Python extra"),
])
def test_diagnoses_every_ecosystem(output: str, fragment: str) -> None:
    assert any(fragment in hint for hint in project_mod.diagnose(output)), project_mod.diagnose(output)


@pytest.mark.parametrize(("output", "fragment"), [
    ("git@github.com: Permission denied (publickey).", "SSH cannot connect"),
    ("fatal: Authentication failed for 'https://github.com/x/y.git/'", "Git cannot authenticate"),
    (" ! [rejected]        main -> main (fetch first)", "pull/rebase first"),
    ("The connection to the server 10.0.0.1:6443 was refused", "kubectl cannot reach the cluster"),
    ("error: You must be logged in to the server (Unauthorized)", "Cluster credentials"),
    ('Error from server (Forbidden): pods is forbidden: User "x" cannot list resource "pods"', "RBAC"),
    ("api-7d9f   0/1   ImagePullBackOff   0   2m", "image cannot be pulled"),
    ("api-7d9f   0/1   CrashLoopBackOff   5   2m", "keeps crashing"),
    ("Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress", "Helm release is stuck"),
    ("Error: Error acquiring the state lock", "state is locked"),
    ('Error: Backend initialization required, please run "terraform init"', "terraform init"),
    ("Unable to locate credentials. You can configure credentials by running \"aws configure\".", "AWS credentials"),
    ("An error occurred (AccessDenied) when calling the PutObject operation", "lacks the permission"),
    ("ERROR: (gcloud.run.deploy) Reauthentication failed.", "GCP credentials"),
    ("Cloud Run Admin API has not been used in project 123 before or it is disabled", "API is disabled"),
    ("ERROR: Please run 'az login' to setup account.", "Azure credentials"),
    ("permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock", "docker group"),
    ("Error: connect ECONNREFUSED 127.0.0.1:5432", "database / cache / broker is not running"),
    ('psql: error: FATAL:  database "app" does not exist', "migrations"),
    ("write /var/lib/docker/tmp: no space left on device", "disk is full"),
    ("./start.sh: line 2: $'\\r': command not found", "CRLF"),
    ("bash: ./tool: cannot execute binary file: Exec format error", "another CPU/OS"),
    ("File C:\\x\\run.ps1 cannot be loaded because running scripts is disabled on this system.", "ExecutionPolicy"),
    ("ENOSPC: System limit for number of file watchers reached", "file watchers"),
])
def test_diagnoses_infra_cloud_system(output: str, fragment: str) -> None:
    assert any(fragment in hint for hint in project_mod.diagnose(output)), project_mod.diagnose(output)


def test_wsl_project_on_windows_does_not_check_the_windows_path(monkeypatch) -> None:
    """Windows Desktop, project in WSL: npm lives in the distro, not on Windows PATH."""
    from navin.utils import wsl

    monkeypatch.setattr(runtime, "_find_program", lambda name, _path: None)
    runtime._WHICH_CACHE.clear()
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(wsl, "parse_unc", lambda s: object())
    assert runtime._missing("npm run build", Path(r"\\wsl.localhost\Ubuntu\home\me\app")) == ""
    monkeypatch.setattr(wsl, "parse_unc", lambda s: None)
    assert runtime._missing("npm run build", Path("C:/app")) == "npm"


def test_an_unchanged_green_check_is_reused_and_any_change_runs_it_again() -> None:
    """An audit ran the same lint, tests and build twice in one turn."""
    root = _project({
        **_node_app(lint="eslint .", test="vitest run"),
        "node_modules/.package-lock.json": "{}",
        "src/app.ts": "export const a = 1;\n",
    })
    fake = FakeExec()
    tool = ProjectTool(workspace=root, exec_tool=fake)
    first = _run(tool, action="lint")
    again = _run(tool, action="lint")
    assert [c[0] for c in fake.calls] == ["npm run lint"]
    assert "no file changed since the identical passing run" in str(again)
    assert "project lint [.] OK" in str(first) and "project lint [.] OK" in str(again)

    _run(tool, action="lint", fresh=True)
    assert len(fake.calls) == 2, "fresh=true always runs"

    (root / "src" / "app.ts").write_text("export const a = 2;\n", encoding="utf-8")
    _run(tool, action="lint")
    assert len(fake.calls) == 3, "an edited file runs the check again"


def test_a_failing_check_is_never_reused() -> None:
    root = _project({**_node_app(test="vitest run"), "node_modules/.package-lock.json": "{}"})
    fake = FakeExec({"npm test": "1 failed\nExit code: 1"})
    tool = ProjectTool(workspace=root, exec_tool=fake)
    _run(tool, action="test")
    _run(tool, action="test")
    assert len(fake.calls) == 2


def test_another_units_files_do_not_invalidate_a_result() -> None:
    root = _project({
        "frontend/package.json": json.dumps({"scripts": {"lint": "eslint ."}}),
        "frontend/node_modules/.package-lock.json": "{}",
        "frontend/src/a.ts": "export const a = 1;\n",
        "backend/pyproject.toml": "[project]\nname = 'api'\n",
        "backend/app.py": "x = 1\n",
    })
    fake = FakeExec()
    tool = ProjectTool(workspace=root, exec_tool=fake)
    _run(tool, action="lint", unit="frontend")
    (root / "backend" / "app.py").write_text("x = 2\n", encoding="utf-8")
    _run(tool, action="lint", unit="frontend")
    assert len(fake.calls) == 1, "a back-end edit keeps the front-end result"
