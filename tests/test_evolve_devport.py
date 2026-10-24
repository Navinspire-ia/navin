# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Evolve suggests the URL the app really listens on, not a framework default.

Reported on a Vite app pinned to 5180 in vite.config.js: the form said
http://127.0.0.1:3000/ (the React default), and a proof sent without a URL
made the engine boot the app once to find it, then again for the run. With
`strictPort` the second boot could not get the port and the proof failed in
5 ms.
"""

from __future__ import annotations

import json
from pathlib import Path

from navin.evolve.devport import detect_dev_port, split_cd, start_folder
from navin.webui import evolve_api


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestDetectDevPort:
    def test_a_vite_config_server_port_wins_over_the_vite_default(self, tmp_path):
        _write(tmp_path / "package.json", json.dumps({"scripts": {"dev": "vite --host 0.0.0.0"}, "devDependencies": {"vite": "^8"}}))
        _write(tmp_path / "vite.config.js", "export default defineConfig({plugins:[react()],server:{port:5180,strictPort:true,host:'0.0.0.0'}});")
        found = detect_dev_port(tmp_path, "npm run dev")
        assert (found.port, found.explicit, found.source) == (5180, True, "vite.config.js")

    def test_a_port_in_the_package_script_is_read(self, tmp_path):
        _write(tmp_path / "package.json", json.dumps({"scripts": {"dev": "vite --host 0.0.0.0 --port 5182 --strictPort"}}))
        found = detect_dev_port(tmp_path, "npm run dev")
        assert (found.port, found.explicit) == (5182, True)

    def test_the_start_command_itself_comes_first(self, tmp_path):
        assert detect_dev_port(tmp_path, "uvicorn app:app --port 8021").port == 8021
        assert detect_dev_port(tmp_path, "PORT=4100 node server.js").port == 4100
        assert detect_dev_port(tmp_path, "gunicorn -b 0.0.0.0:8100 app:app").port == 8100

    def test_an_env_file_port_is_used(self, tmp_path):
        _write(tmp_path / ".env", "PORT=4321\n")
        assert detect_dev_port(tmp_path, "npm start").port == 4321

    def test_without_config_the_dev_server_default_is_a_guess(self, tmp_path):
        _write(tmp_path / "package.json", json.dumps({"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^6"}}))
        found = detect_dev_port(tmp_path, "npm run dev")
        assert (found.port, found.explicit) == (5173, False)

    def test_nothing_known_gives_nothing(self, tmp_path):
        assert detect_dev_port(tmp_path, "make run") is None


class TestStartFolder:
    def test_the_cd_target_is_the_folder(self, tmp_path):
        (tmp_path / "equipe").mkdir()
        assert split_cd("cd equipe && npm run dev") == ("equipe", "npm run dev")
        assert start_folder(tmp_path, "cd equipe && npm run dev") == (tmp_path / "equipe").resolve()

    def test_a_folder_outside_the_project_is_ignored(self, tmp_path):
        assert start_folder(tmp_path, "cd ../elsewhere && npm run dev") == tmp_path


def _monorepo(root: Path) -> None:
    _write(root / "equipe" / "package.json", json.dumps({"scripts": {"dev": "vite", "test": "vitest run"}, "devDependencies": {"vite": "^8"}}))
    _write(root / "equipe" / "vite.config.js", "export default {server:{port:5180,strictPort:true}}")


def _manifest() -> dict:
    return {
        "start_command": "cd equipe && npm run dev",
        "units": [
            {"path": "equipe", "framework": "react", "commands": {"dev": "npm run dev", "test": "npm run test"}},
            {"path": "equipe/server", "framework": "fastapi", "commands": {"start": "uvicorn app:app --port 8000"}},
        ],
    }


class TestManifestHint:
    def test_the_hint_reads_the_folder_the_app_starts_in(self, monkeypatch, tmp_path):
        _monorepo(tmp_path)
        monkeypatch.setattr(evolve_api, "daemon_call", lambda root, method, payload: _manifest())
        hint = evolve_api._compute_manifest_hint(tmp_path)
        assert hint["url"] == "http://127.0.0.1:5180/"
        assert hint["url_certain"] is True
        assert hint["start"] == "cd equipe && npm run dev"
        assert hint["test"] == "cd equipe && npm run test"

    def test_a_framework_default_alone_is_not_certain(self, monkeypatch, tmp_path):
        _write(tmp_path / "equipe" / "package.json", json.dumps({"scripts": {"dev": "react-scripts start"}}))
        monkeypatch.setattr(evolve_api, "daemon_call", lambda root, method, payload: _manifest())
        hint = evolve_api._compute_manifest_hint(tmp_path)
        assert hint["url"] == "http://127.0.0.1:3000/"
        assert hint["url_certain"] is False


class TestEnqueueHandsOverACertainUrl:
    def _capture(self, monkeypatch, tmp_path, hint):
        captured: dict = {}
        monkeypatch.setattr(evolve_api, "_project_root", lambda raw: tmp_path)
        monkeypatch.setattr(evolve_api, "_save_autorun", lambda *args, **kwargs: None)
        monkeypatch.setattr(evolve_api, "_manifest_hint", lambda root: hint)

        def fake_call(root, method, payload):
            captured["payload"] = payload
            return {"job": 1}

        monkeypatch.setattr(evolve_api, "daemon_call", fake_call)
        return captured

    def test_a_certain_url_and_its_start_command_reach_the_engine(self, monkeypatch, tmp_path):
        captured = self._capture(monkeypatch, tmp_path, {
            "start": "cd equipe && npm run dev", "url": "http://127.0.0.1:5180/", "url_certain": True,
        })
        evolve_api.enqueue_campaign(str(tmp_path), "proof.run", {"dirty": ["1"]})
        params = captured["payload"]["params"]
        assert params["url"] == "http://127.0.0.1:5180/"
        assert params["start"] == "cd equipe && npm run dev"

    def test_a_guess_lets_the_engine_find_the_url_itself(self, monkeypatch, tmp_path):
        captured = self._capture(monkeypatch, tmp_path, {
            "start": "npm start", "url": "http://127.0.0.1:3000/", "url_certain": False,
        })
        evolve_api.enqueue_campaign(str(tmp_path), "proof.run", {})
        assert "url" not in captured["payload"]["params"]

    def test_what_the_user_typed_is_never_replaced(self, monkeypatch, tmp_path):
        captured = self._capture(monkeypatch, tmp_path, {
            "start": "cd equipe && npm run dev", "url": "http://127.0.0.1:5180/", "url_certain": True,
        })
        evolve_api.enqueue_campaign(str(tmp_path), "proof.run", {"url": ["http://127.0.0.1:9999/"]})
        params = captured["payload"]["params"]
        assert params["url"] == "http://127.0.0.1:9999/"
        assert "start" not in params
