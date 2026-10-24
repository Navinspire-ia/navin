# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The agent gets each project's real commands, whatever the stack."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from navin.agent import project_runbook as rb


def _project(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    rb._cached_runbook.cache_clear()
    return root


def _text(root: Path) -> str:
    return "\n".join(rb.project_runbook(root))


def test_node_subproject_with_missing_modules() -> None:
    root = _project({"app/package.json": json.dumps({"scripts": {"dev": "vite", "build": "vite build", "lint": "eslint ."}}),
                     "app/pnpm-lock.yaml": ""})
    text = _text(root)
    assert "[app] Node - dev server port 5173 - dependencies NOT installed yet" in text
    assert "install: pnpm install   (declared)" in text
    assert "start: pnpm dev   (declared)" in text and "build: pnpm build   (declared)" in text


@pytest.mark.parametrize(("files", "expected"), [
    ({"pyproject.toml": "[tool.uv]\n[tool.pytest.ini_options]\n", "uv.lock": ""}, ["install: uv sync", "uv run python -m pytest -q"]),
    ({"requirements.txt": "flask", "app.py": "app = Flask(__name__)"}, ["no virtualenv yet", "flask --app app run"]),
    ({"manage.py": "", "requirements.txt": "django"}, ["manage.py runserver", "manage.py migrate"]),
    ({"go.mod": "module x", "main.go": "package main"}, ["go run .", "go test ./..."]),
    ({"Cargo.toml": "[package]"}, ["cargo build", "cargo test"]),
    ({"pom.xml": "spring-boot", "mvnw": ""}, ["./mvnw spring-boot:run", "./mvnw test"]),
    ({"build.gradle.kts": "id(\"org.springframework.boot\")", "gradlew": ""}, ["./gradlew bootRun", "./gradlew test"]),
    ({"App.csproj": "<Project/>"}, ["dotnet run", "dotnet test"]),
    ({"composer.json": "{}", "artisan": ""}, ["composer install", "php artisan serve"]),
    ({"Gemfile": "gem 'rails'"}, ["bundle install", "rails server"]),
    ({"pubspec.yaml": "flutter:\n"}, ["flutter pub get", "flutter test"]),
    ({"mix.exs": "phoenix"}, ["mix deps.get", "mix phx.server"]),
    ({"deno.json": json.dumps({"tasks": {"dev": "deno run main.ts"}})}, ["deno task dev"]),
    ({"Makefile": "build:\n\tgo build\ntest:\n\tgo test\n"}, ["make build", "make test"]),
    ({"start.ps1": "", "start.sh": ""}, ["bash start.sh", "start.ps1"]),
])
def test_stacks(files: dict[str, str], expected: list[str]) -> None:
    text = _text(_project(files))
    for fragment in expected:
        assert fragment in text, (fragment, text)


def test_kubernetes_helm_terraform_and_clouds() -> None:
    root = _project({
        "deploy/k8s/app.yaml": "apiVersion: apps/v1\nkind: Deployment\n",
        "charts/api/Chart.yaml": "name: api",
        "infra/main.tf": "terraform {}",
        "vercel.json": "{}",
        "wrangler.toml": "name='w'",
        "firebase.json": "{}",
        "fly.toml": "",
        "Dockerfile": "FROM node\nEXPOSE 3000\n",
    })
    text = _text(root)
    for fragment in [
        "kubectl apply --dry-run=client -f deploy/k8s",
        "helm lint charts/api",
        "terraform -chdir=infra plan",
        "vercel dev", "npx wrangler dev", "firebase emulators:start", "fly deploy",
        "docker run --rm -p 3000:3000",
    ]:
        assert fragment in text, fragment
    # Deploys are flagged, never presented as routine.
    assert "kubectl apply -f deploy/k8s   <- deploys to real infrastructure" in text


def test_ci_commands_including_block_and_folded_steps() -> None:
    root = _project({".github/workflows/ci.yml": (
        "jobs:\n  t:\n    steps:\n"
        "      - run: npm ci\n"
        "      - run: |\n          npm run build\n          npm test -- --run\n"
        "      - run: >-\n          python -m pytest\n          tests -q\n"
        "      - run: echo done\n"
    )})
    text = _text(root)
    assert "CI runs these" in text
    for fragment in ["  npm ci", "  npm run build", "  npm test -- --run", "  python -m pytest tests -q"]:
        assert fragment in text
    assert "echo done" not in text


def test_windows_forms(monkeypatch) -> None:
    monkeypatch.setattr(rb, "_IS_WINDOWS", True)
    root = _project({"pom.xml": "", "mvnw.cmd": "", "requirements.txt": "x", ".venv/Scripts/python.exe": ""})
    text = _text(root)
    assert "Host: Windows" in text
    assert ".\\mvnw.cmd" in text
    assert ".venv\\Scripts\\python.exe -m pip install -r requirements.txt" in text


def test_empty_folder_still_says_how_to_find_commands() -> None:
    text = _text(_project({"notes.txt": "hi"}))
    assert "No project manifest found" in text
    assert "web_search the official docs" in text


def test_container_host_is_said(monkeypatch) -> None:
    monkeypatch.setattr(rb, "_IS_WINDOWS", False)
    monkeypatch.setattr(rb.sys, "platform", "linux")
    monkeypatch.setattr(rb, "_in_container", lambda: True)
    assert "inside a container" in rb.host_line()


def test_which_lists_each_path_folder_once_and_matches_shutil(tmp_path, monkeypatch):
    """One listing per PATH folder (slow /mnt/c folders on WSL), same answers as shutil.which."""
    import os
    import shutil
    import stat

    from navin.agent import project_runtime as runtime

    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    for folder, name in ((first, "tool"), (second, "tool"), (second, "other"), (first, "notexec")):
        path = folder / name
        path.write_text("#!/bin/sh\n")
        if name != "notexec":
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
    path_value = os.pathsep.join([str(first), str(second)])
    monkeypatch.setenv("PATH", path_value)
    runtime._WHICH_CACHE.clear()
    runtime._PATH_INDEX.clear()
    listed: list[str] = []
    real_listdir = os.listdir
    monkeypatch.setattr(runtime.os, "listdir", lambda folder: listed.append(folder) or real_listdir(folder))
    for name in ("tool", "other", "notexec", "missing"):
        assert runtime.which(name) == shutil.which(name, path=path_value)
    assert runtime.which("tool") == str(first / "tool")
    assert sorted(listed) == sorted([str(first), str(second)])


def test_slow_read_serves_the_last_good_runbook(monkeypatch):
    # A WSL project read through 9P can take longer than the provider bound:
    # the turn keeps the last good commands and the read finishes behind it.
    import asyncio
    import threading
    import time

    from navin.agent.tools.context import RequestContext

    root = _project({"package.json": json.dumps({"scripts": {"dev": "vite"}})})
    key = str(root)
    rb._LAST_GOOD.pop(key, None)
    rb._PENDING.pop(key, None)
    fresh = rb.project_runbook(root)
    assert fresh
    rb._LAST_GOOD[key] = ["old line"]
    gate = threading.Event()

    def slow(path):
        gate.wait(5)
        return fresh

    monkeypatch.setattr(rb, "project_runbook", slow)
    monkeypatch.setattr(rb, "FRESH_WAIT_S", 0.05)

    async def run() -> tuple[str, list[str]]:
        block = await rb.project_runbook_context_provider(RequestContext(channel="cli", chat_id="t", workspace=root))
        gate.set()
        await rb._PENDING[key]
        return block.content, list(rb._LAST_GOOD[key])

    started = time.monotonic()
    content, kept = asyncio.run(run())
    assert time.monotonic() - started < 5
    assert "old line" in content
    assert kept == fresh
