# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the LLM fix bridge used by the navin-engine daemon."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from navin.evolve import bridge
from navin.providers.base import LLMResponse

FINDING = {
    "id": "crash.load",
    "title": "Service crashes under load",
    "severity": "critical",
    "confidence": "high",
    "symptom": "process exited during the load fault",
    "root_cause": "unhandled exception in server.py",
    "remediation": "guard the handler",
    "family": "reliability",
    "evidence": ["server.py raised ValueError"],
}


def make_request(tmp_path, finding=FINDING):
    return json.dumps({
        "schema": bridge.REQUEST_SCHEMA,
        "engine_version": "0.1.0",
        "project_root": str(tmp_path),
        "finding": finding,
    })


class FakeProvider:
    def __init__(self, content, finish_reason="stop"):
        self._content = content
        self._finish_reason = finish_reason
        self.messages = None

    async def chat_with_retry(self, messages, **kwargs):
        self.messages = messages
        return LLMResponse(content=self._content, finish_reason=self._finish_reason)


class TestParseRequest:
    def test_valid_request(self, tmp_path):
        request = bridge.parse_request(make_request(tmp_path))
        assert request.finding["id"] == "crash.load"
        assert request.project_root == tmp_path

    def test_wrong_schema_rejected(self, tmp_path):
        payload = json.loads(make_request(tmp_path))
        payload["schema"] = "something/v9"
        with pytest.raises(ValueError, match="schema"):
            bridge.parse_request(json.dumps(payload))

    def test_missing_finding_rejected(self, tmp_path):
        payload = json.loads(make_request(tmp_path))
        del payload["finding"]
        with pytest.raises(ValueError, match="finding"):
            bridge.parse_request(json.dumps(payload))

    def test_garbage_rejected(self):
        with pytest.raises(ValueError, match="JSON"):
            bridge.parse_request("not json at all")


class TestCollectContext:
    def test_mentioned_files_come_first(self, tmp_path):
        (tmp_path / "server.py").write_text("print('big server')" * 50)
        (tmp_path / "tiny.py").write_text("x = 1")
        files = bridge.collect_context(tmp_path, FINDING)
        names = [rel for rel, _ in files]
        # server.py is named in the finding, so it beats the smaller file.
        assert names[0] == "server.py"
        assert "tiny.py" in names

    def test_skips_noise_and_binaries(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "dep.js").write_text("junk")
        (tmp_path / "photo.png").write_bytes(b"\x89PNG")
        (tmp_path / "app.py").write_text("ok")
        names = [rel for rel, _ in bridge.collect_context(tmp_path, FINDING)]
        assert names == ["app.py"]

    def test_respects_file_size_bound(self, tmp_path):
        (tmp_path / "huge.py").write_text("x" * (bridge.MAX_FILE_BYTES + 1))
        assert bridge.collect_context(tmp_path, FINDING) == []

    def test_a_full_path_in_a_stack_trace_beats_a_bare_name(self, tmp_path):
        # Two files with the same name: only the path pinpoints the right one.
        (tmp_path / "src" / "api").mkdir(parents=True)
        (tmp_path / "src" / "api" / "server.py").write_text("the real one" * 100)
        (tmp_path / "server.py").write_text("tiny impostor")
        finding = dict(FINDING, evidence=["File \"src/api/server.py\", line 42, in handle"])
        names = [rel for rel, _ in bridge.collect_context(tmp_path, finding)]
        assert names[0] == "src/api/server.py"

    def test_entry_points_beat_anonymous_files(self, tmp_path):
        (tmp_path / "zz_helpers.py").write_text("x = 1")
        (tmp_path / "main.py").write_text("a much longer entry point" * 20)
        finding = dict(FINDING, root_cause="something opaque", evidence=[])
        names = [rel for rel, _ in bridge.collect_context(tmp_path, finding)]
        assert names.index("main.py") < names.index("zz_helpers.py")

    def test_limits_are_env_tunable(self, tmp_path, monkeypatch):
        for i in range(4):
            (tmp_path / f"f{i}.py").write_text("x = 1")
        monkeypatch.setenv("NAVIN_BRIDGE_MAX_FILES", "2")
        assert len(bridge.collect_context(tmp_path, FINDING)) == 2
        monkeypatch.setenv("NAVIN_BRIDGE_MAX_FILES", "not-a-number")
        assert len(bridge.collect_context(tmp_path, FINDING)) == 4


class TestAppScope:
    """In a monorepo, only the app the engine started is worth showing."""

    def monorepo(self, tmp_path):
        (tmp_path / "site").mkdir()
        (tmp_path / "site" / "app.js").write_text("const app = 1;")
        (tmp_path / "unrelated").mkdir()
        (tmp_path / "unrelated" / "other.py").write_text("y = 2")
        return tmp_path

    def test_cd_names_the_app_directory(self, tmp_path):
        root = self.monorepo(tmp_path)
        assert bridge.app_scope(root, "cd site && npm run dev") == [
            (root / "site").resolve()
        ]

    def test_prefix_flag_names_it_too(self, tmp_path):
        root = self.monorepo(tmp_path)
        assert bridge.app_scope(root, "npm --prefix site run dev") == [
            (root / "site").resolve()
        ]

    def test_a_file_path_points_at_its_directory(self, tmp_path):
        root = self.monorepo(tmp_path)
        assert bridge.app_scope(root, "node site/app.js") == [(root / "site").resolve()]

    def test_a_command_naming_nothing_scopes_nothing(self, tmp_path):
        assert bridge.app_scope(self.monorepo(tmp_path), "npm run dev") == []

    def test_paths_outside_the_project_are_ignored(self, tmp_path):
        assert bridge.app_scope(self.monorepo(tmp_path), "cd ../elsewhere && run") == []

    def test_only_the_started_app_reaches_the_model(self, tmp_path):
        root = self.monorepo(tmp_path)
        names = [
            rel
            for rel, _ in bridge.collect_context(root, FINDING, "cd site && npm run dev")
        ]
        assert names == ["site/app.js"]

    def test_without_a_start_command_the_whole_project_is_used(self, tmp_path):
        root = self.monorepo(tmp_path)
        names = sorted(rel for rel, _ in bridge.collect_context(root, FINDING))
        assert names == ["site/app.js", "unrelated/other.py"]

    def test_the_prompt_says_which_app_was_measured(self, tmp_path):
        request = bridge.parse_request(
            json.dumps({
                "schema": bridge.REQUEST_SCHEMA,
                "engine_version": "0.1.0",
                "project_root": str(tmp_path),
                "finding": FINDING,
                "start_command": "cd site && npm run dev",
            })
        )
        assert request.start_command == "cd site && npm run dev"
        prompt = bridge.build_messages(request, [], 3)[1]["content"]
        assert "cd site && npm run dev" in prompt


class TestParseCandidates:
    def test_normalises_and_forces_target(self):
        reply = json.dumps([{
            "id": "guard-handler",
            "target_finding": "some.other.finding",
            "rationale": "wrap the handler in try/except",
            "family": "reliability",
            "patch": {"kind": "files", "edits": [{"path": "server.py", "contents": "new"}]},
        }])
        result = bridge.parse_candidates(reply, FINDING)
        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate["target_finding"] == "crash.load"
        assert candidate["patch"]["edits"][0]["path"] == "server.py"

    def test_markdown_fences_are_tolerated(self):
        reply = "```json\n" + json.dumps([{
            "patch": {"kind": "files", "edits": [{"path": "a.py", "contents": "x"}]},
        }]) + "\n```"
        result = bridge.parse_candidates(reply, FINDING)
        assert len(result.candidates) == 1
        # Missing id/rationale/family get defaults.
        assert result.candidates[0]["id"] == "llm-1"
        assert result.candidates[0]["family"] == "reliability"

    def test_prose_around_the_array_is_tolerated(self):
        reply = (
            "Here are my fixes:\n"
            + json.dumps([{"patch": {"kind": "unified_diff", "diff": "--- a\n+++ b\n"}}])
            + "\nGood luck!"
        )
        result = bridge.parse_candidates(reply, FINDING)
        assert len(result.candidates) == 1
        assert result.candidates[0]["patch"]["kind"] == "unified_diff"

    def test_unsafe_paths_are_dropped(self):
        reply = json.dumps([
            {"patch": {"kind": "files", "edits": [{"path": "../evil.py", "contents": "x"}]}},
            {"patch": {"kind": "files", "edits": [{"path": "/etc/passwd", "contents": "x"}]}},
            {"patch": {"kind": "files", "edits": [{"path": "ok.py", "contents": "x"}]}},
        ])
        result = bridge.parse_candidates(reply, FINDING)
        assert [c["patch"]["edits"][0]["path"] for c in result.candidates] == ["ok.py"]
        assert len(result.notes) == 2

    def test_unknown_patch_kind_is_dropped(self):
        reply = json.dumps([{"patch": {"kind": "telepathy"}}])
        result = bridge.parse_candidates(reply, FINDING)
        assert result.candidates == []
        assert result.notes

    def test_unparseable_reply_yields_no_candidates(self):
        result = bridge.parse_candidates("I cannot help with that.", FINDING)
        assert result.candidates == []
        assert result.notes


class TestGenerate:
    def test_end_to_end_with_fake_provider(self, tmp_path):
        (tmp_path / "server.py").write_text("raise ValueError('boom')")
        request = bridge.parse_request(make_request(tmp_path))
        reply = json.dumps([{
            "id": "fix-1",
            "rationale": "guard",
            "patch": {"kind": "files", "edits": [{"path": "server.py", "contents": "pass"}]},
        }])
        provider = FakeProvider(reply)
        result = asyncio.run(bridge.generate(request, provider, "test-model", 3))
        assert len(result.candidates) == 1
        # The prompt carried the finding and the source file.
        user_prompt = provider.messages[1]["content"]
        assert "crash.load" in user_prompt
        assert "server.py" in user_prompt

    def test_llm_error_yields_note_not_crash(self, tmp_path):
        request = bridge.parse_request(make_request(tmp_path))
        provider = FakeProvider("rate limited", finish_reason="error")
        result = asyncio.run(bridge.generate(request, provider, "test-model", 3))
        assert result.candidates == []
        assert "LLM call failed" in result.notes[0]

    def test_max_candidates_is_enforced(self, tmp_path):
        request = bridge.parse_request(make_request(tmp_path))
        reply = json.dumps([
            {"patch": {"kind": "files", "edits": [{"path": f"f{i}.py", "contents": "x"}]}}
            for i in range(5)
        ])
        provider = FakeProvider(reply)
        result = asyncio.run(bridge.generate(request, provider, "test-model", 2))
        assert len(result.candidates) == 2


class TestMain:
    def test_bad_request_exits_nonzero(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO("garbage"))
        assert bridge.main([]) == 1
        assert "navin.evolve.bridge" in capsys.readouterr().err

    def test_provider_failure_still_prints_empty_array(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("sys.stdin", io.StringIO(make_request(tmp_path)))

        def boom(*args, **kwargs):
            raise RuntimeError("no provider configured")

        monkeypatch.setattr("navin.providers.factory.load_provider_snapshot", boom)
        assert bridge.main([]) == 0
        out = capsys.readouterr()
        assert json.loads(out.out) == []
        assert "provider error" in out.err

    def test_the_chosen_preset_reaches_the_provider_factory(self, monkeypatch, capsys, tmp_path):
        """Any provider must work: the bridge only forwards the preset name
        and lets the factory resolve provider, key and model."""
        monkeypatch.setattr("sys.stdin", io.StringIO(make_request(tmp_path)))
        monkeypatch.setenv("NAVIN_BRIDGE_PRESET", "llama-local")
        asked: dict[str, object] = {}

        class Snapshot:
            provider = FakeProvider("[]")
            model = "llama3.2:latest"

        def capture(config_path=None, *, preset_name=None):
            asked["preset"] = preset_name
            return Snapshot()

        monkeypatch.setattr("navin.providers.factory.load_provider_snapshot", capture)
        assert bridge.main([]) == 0
        capsys.readouterr()
        assert asked["preset"] == "llama-local"

    def test_happy_path_prints_candidates(self, monkeypatch, capsys, tmp_path):
        (tmp_path / "server.py").write_text("raise ValueError('boom')")
        monkeypatch.setattr("sys.stdin", io.StringIO(make_request(tmp_path)))
        reply = json.dumps([{
            "id": "fix-1",
            "rationale": "guard",
            "patch": {"kind": "files", "edits": [{"path": "server.py", "contents": "pass"}]},
        }])

        class Snapshot:
            provider = FakeProvider(reply)
            model = "test-model"

        monkeypatch.setattr(
            "navin.providers.factory.load_provider_snapshot", lambda *a, **k: Snapshot()
        )
        assert bridge.main([]) == 0
        candidates = json.loads(capsys.readouterr().out)
        assert candidates[0]["id"] == "fix-1"
        assert candidates[0]["target_finding"] == "crash.load"


class TestModelPicker:
    """The Evolve campaign picker offered by the gateway."""

    def write_config(self, tmp_path, monkeypatch, payload):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr("navin.config.loader.get_config_path", lambda: path)
        return path

    def test_every_provider_is_offered(self, tmp_path, monkeypatch):
        from navin.webui import evolve_api

        self.write_config(tmp_path, monkeypatch, {
            "modelPresets": {
                "managed": {"provider": "navin", "model": "google/gemini-3.7-flash"},
                "byok": {"provider": "anthropic", "model": "claude-opus-5"},
                "local": {"provider": "ollama", "model": "llama3.2:latest"},
                "retired": {"provider": "zai", "model": "glm-4.5", "enabled": False},
                "picture": {"provider": "navin", "model": "veo-3.1", "modality": "video"},
            },
            "agents": {"defaults": {"modelPreset": "byok"}},
        })
        names, default = evolve_api._model_presets()
        assert names == ["byok", "local", "managed"]
        assert default == "byok"

    def test_snake_case_config_is_understood(self, tmp_path, monkeypatch):
        from navin.webui import evolve_api

        self.write_config(tmp_path, monkeypatch, {
            "model_presets": {"local": {"provider": "ollama", "model": "llama3.2:latest"}},
            "agents": {"defaults": {"model_preset": "local"}},
        })
        assert evolve_api._model_presets() == (["local"], "local")


class TestBridgeOffer:
    """A campaign that needs candidates must reach a model without the user
    editing `.navin/evolve.toml` first."""

    def enqueue(self, monkeypatch, tmp_path, kind, query=None):
        from navin.webui import evolve_api

        seen: dict[str, object] = {}

        def fake_call(root, method, params):
            seen["method"] = method
            seen["params"] = params
            return {"job": 1}

        monkeypatch.setattr(evolve_api, "daemon_call", fake_call)
        monkeypatch.setattr(evolve_api, "_save_autorun", lambda *a, **k: None)
        evolve_api.enqueue_campaign(str(tmp_path), kind, query or {})
        return seen["params"]["params"]

    def test_optimize_is_offered_the_llm_bridge(self, tmp_path, monkeypatch):
        params = self.enqueue(monkeypatch, tmp_path, "optimize.run")
        assert "navin.evolve.bridge" in params["generator"]

    def test_evolve_is_offered_the_llm_bridge(self, tmp_path, monkeypatch):
        params = self.enqueue(monkeypatch, tmp_path, "evolve.run")
        assert "navin.evolve.bridge" in params["generator"]

    def test_a_proof_never_calls_a_model(self, tmp_path, monkeypatch):
        params = self.enqueue(monkeypatch, tmp_path, "proof.run")
        assert "generator" not in params

    def test_the_request_cannot_choose_the_command(self, tmp_path, monkeypatch):
        params = self.enqueue(
            monkeypatch, tmp_path, "optimize.run", {"generator": ["rm -rf /"]}
        )
        assert "rm -rf" not in params["generator"]


class TestReviewSurface:
    """What the dashboard needs in order to review a change instead of
    trusting it: the diagnosed problems, and the diff behind a promotion."""

    def artefact(self, root, subdir, name, payload):
        directory = root / ".navin" / subdir
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(json.dumps(payload), encoding="utf-8")

    def test_the_overview_carries_diagnoses_and_diffs(self, tmp_path, monkeypatch):
        from navin.webui import evolve_api

        self.artefact(tmp_path, "diagnoses", "abc.json", {
            "schema": "navin-diagnosis/v1",
            "commit": "abc",
            "collected_at": "epoch:1",
            "source_verdict": "fail",
            "robustness_score": 40,
            "summary": "1 critical.",
            "findings": [{
                "id": "crash.load",
                "title": "Service crashes under load",
                "severity": "critical",
                "confidence": "high",
                "symptom": "process exited",
                "root_cause": "unhandled exception",
                "remediation": "guard the handler",
                "family": "reliability",
            }],
        })
        self.artefact(tmp_path, "promotions", "promo-1.json", {
            "schema": "navin-promotion/v1",
            "id": "promo-1",
            "finding": "crash.load",
            "candidate_id": "guard-handler",
            "mode": "safe",
            "outcome": "branch_only",
            "reasons": ["safe mode"],
            "branch": "navin/evolve/crash-load-1",
            "merged": False,
            "created_at": "epoch:2",
            "diff": "--- a/server.py\n+++ b/server.py\n+try:\n",
            "pull_request": "https://github.com/acme/app/pull/7",
        })
        monkeypatch.setattr(evolve_api, "daemon_call", lambda *a, **k: {"jobs": []})
        monkeypatch.setattr(evolve_api, "resolve_engine_bin", lambda: "/bin/true")
        monkeypatch.setattr(evolve_api, "_model_presets", lambda: ([], None))

        payload = evolve_api.overview(str(tmp_path))
        assert payload["diagnoses"][0]["findings"][0]["id"] == "crash.load"
        promotion = payload["promotions"][0]
        assert "+try:" in promotion["diff"]
        assert promotion["pull_request"].endswith("/pull/7")

    def test_publishing_needs_a_promotion_id(self, tmp_path):
        from navin.webui import evolve_api

        with pytest.raises(evolve_api.EvolveApiError) as caught:
            evolve_api.publish_promotion(str(tmp_path), "")
        assert caught.value.status == 400

    def test_publishing_calls_the_engine_pr_command(self, tmp_path, monkeypatch):
        from navin.webui import evolve_api

        seen: dict[str, object] = {}

        def capture(args):
            seen["args"] = args
            return {"id": "promo-1"}

        monkeypatch.setattr(evolve_api, "_run_engine_cli", capture)
        evolve_api.publish_promotion(str(tmp_path), "promo-1")
        assert seen["args"][0] == "pr"
        assert seen["args"][-2:] == ["--id", "promo-1"]
