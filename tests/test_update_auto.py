# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Automatic updates: one policy, background download, install only when idle.

Covers the 2.4.3 requirements: channels (stable, beta, enterprise), suspended
and revoked releases, signed rollback that must read the local data, the
per-format engine (Tauri shell or Navin), the background cycle that never
installs while an agent works, the checkpoint before the restart, and the
local data that no update touches.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from navin.config.schema import UpdatesConfig
from navin.update import activity, auto, data_compat, service
from navin.update import policy as update_policy


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(Path, "home", lambda: root)
    monkeypatch.setattr(service, "_STATE", {"state": "idle", "progress": 0})
    monkeypatch.setattr(service, "_CACHE", (0.0, None))
    monkeypatch.setattr(activity, "_PROBES", {})
    return root


def manifest(**extra) -> dict:
    base = {
        "schemaVersion": 1,
        "channel": "stable",
        "version": "2.4.4",
        "minimumVersion": "",
        "rollout": 100,
        "notes": "",
        "artifacts": {
            "windows-setup-x64": {
                "url": "https://updates.example/releases/2.4.4/setup.exe",
                "size": 10,
                "sha256": "a" * 64,
            }
        },
    }
    base.update(extra)
    return base


ROLLBACK = {
    "version": "2.4.2",
    "notes": "Back to the last good build",
    "dataVersion": 1,
    "artifacts": {
        "windows-setup-x64": {
            "url": "https://updates.example/releases/2.4.2/setup.exe",
            "size": 10,
            "sha256": "b" * 64,
        }
    },
}


def release_info(data: dict, *, current: str = "2.4.3", in_rollout: bool = True):
    with (
        mock.patch.object(service, "__version__", current),
        mock.patch.object(service, "_install_kind", return_value="windows-setup"),
        mock.patch.object(service, "_platform_keys", return_value=("windows-setup-x64",)),
        mock.patch.object(service, "_in_rollout", return_value=in_rollout),
    ):
        return service._release_info(data, base_url="https://updates.example", skipped_version="")


# --- policy ---------------------------------------------------------------


def test_enterprise_is_a_channel():
    assert UpdatesConfig(channel="enterprise").channel == "enterprise"
    assert UpdatesConfig().auto_install is True
    with pytest.raises(ValueError):
        UpdatesConfig(channel="nightly")


def test_a_suspended_release_is_offered_to_nobody(home):
    assert release_info(manifest(suspended=True)) is None
    decision = update_policy.decide(
        manifest(suspended=True), current_version="2.4.3", candidate_version="2.4.4", in_rollout=True,
    )
    assert decision == {"allowed": False, "reason": "release suspended", "mandatory": False}


def test_rollout_and_revocation(home):
    assert release_info(manifest(), in_rollout=False) is None
    # The running version was withdrawn: the fix comes at once, whatever the rollout.
    info = release_info(manifest(revokedVersions=["2.4.3"]), in_rollout=False)
    assert info["latestVersion"] == "2.4.4" and info["mandatory"] is True
    assert info["rollback"] is False
    # A revoked release is never installed, even inside the rollout.
    assert release_info(manifest(revokedVersions=["2.4.4"])) is None


def test_a_revoked_version_without_fix_rolls_back_to_the_signed_build(home):
    data = manifest(version="2.4.3", revokedVersions=["2.4.3"], rollback=ROLLBACK)
    info = release_info(data, current="2.4.3")
    assert info["rollback"] is True and info["mandatory"] is True
    assert info["latestVersion"] == "2.4.2"
    assert info["artifact"]["sha256"] == "b" * 64
    # Nothing is revoked: no downgrade is ever offered.
    assert release_info(manifest(version="2.4.3", rollback=ROLLBACK), current="2.4.3") is None


def test_a_rollback_that_cannot_read_the_local_data_is_refused(home):
    data_compat.stamp_data_version(2)
    data = manifest(version="2.4.3", revokedVersions=["2.4.3"], rollback=ROLLBACK)
    assert update_policy.rollback_offer(data, "2.4.3", data_compat.local_data_version()) is None
    assert release_info(data, current="2.4.3") is None


def test_the_shell_asks_the_same_policy():
    data = manifest(revokedVersions=["2.4.3"], rollback=ROLLBACK)
    allowed = update_policy.decide(data, current_version="2.4.3", candidate_version="2.4.4", in_rollout=False)
    assert allowed["allowed"] and allowed["mandatory"]
    assert not update_policy.decide(
        manifest(), current_version="2.4.3", candidate_version="2.4.4", in_rollout=False,
    )["allowed"]
    assert update_policy.decide(
        manifest(), current_version="2.4.3", candidate_version="2.4.9", in_rollout=True,
    )["reason"] == "not the signed release"
    downgrade = update_policy.decide(
        manifest(), current_version="2.4.3", candidate_version="2.4.2", in_rollout=True,
    )
    assert downgrade["allowed"] is False
    rollback = update_policy.decide(
        manifest(version="2.4.3", revokedVersions=["2.4.3"], rollback=ROLLBACK),
        current_version="2.4.3", candidate_version="2.4.2", in_rollout=True,
    )
    assert rollback["allowed"] and rollback["rollback"]


def test_update_policy_for_reads_the_signed_manifest(home):
    with (
        mock.patch.object(service, "__version__", "2.4.3"),
        mock.patch.object(service, "_update_config", return_value=("https://updates.example", "enterprise", "")),
        mock.patch.object(service, "_fetch_manifest", return_value=manifest(channel="enterprise")),
        mock.patch.object(service, "_in_rollout", return_value=True),
    ):
        answer = service.update_policy_for("2.4.4")
    assert answer["allowed"] is True and answer["channel"] == "enterprise"


# --- engines and strategy -------------------------------------------------


def test_every_install_format_has_a_strategy():
    kinds = set(service._INSTALL_KINDS) | {"source", "linux-package"}
    for kind in kinds:
        strategy = update_policy.install_strategy(kind)
        assert strategy["engine"] in {"tauri", "navin", "manual"}, kind
        assert strategy["mode"], kind
    assert update_policy.install_strategy("windows-setup")["engine"] == "tauri"
    assert update_policy.install_strategy("linux-deb")["engine"] == "navin"
    assert update_policy.install_strategy("cli")["engine"] == "navin"


def test_the_shell_owns_its_formats_only_while_it_reports(home):
    assert update_policy.engine_for("windows-setup") == "navin"
    update_policy.note_engine("tauri", "2.4.4")
    assert update_policy.engine_for("windows-setup") == "tauri"
    assert update_policy.engine_for("macos-app") == "tauri"
    assert update_policy.engine_for("linux-deb") == "navin"
    later = time.time() + update_policy.TAURI_ENGINE_TTL_S + 1
    assert update_policy.engine_for("windows-setup", now=later) == "navin"
    with pytest.raises(ValueError):
        update_policy.note_engine("electron")


def test_history_is_newest_first_and_bounded(home):
    for index in range(update_policy._HISTORY_MAX_LINES + 5):
        update_policy.record_event("checked", version=f"2.4.{index}")
    update_policy.record_event("error", version="2.4.4", detail="signature", ok=False)
    rows = update_policy.read_history(limit=3)
    assert rows[0]["event"] == "error" and rows[0]["ok"] is False
    lines = update_policy.history_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == update_policy._HISTORY_MAX_LINES
    update_policy.history_path().write_text("not json\n" + "\n".join(lines[-1:]) + "\n", encoding="utf-8")
    assert len(update_policy.read_history()) == 1


# --- local data -----------------------------------------------------------


def test_the_data_version_only_goes_up_and_backups_are_kept(home):
    assert data_compat.local_data_version() == 1
    assert data_compat.stamp_data_version(3) == 3
    assert data_compat.stamp_data_version(1) == 3
    assert not data_compat.compatible(2) and data_compat.compatible(3)
    assert data_compat.backup_before_install("2.4.3", "2.4.4") is None
    config = home / ".navin" / "config.json"
    config.write_text('{"updates": {"channel": "beta"}}', encoding="utf-8")
    for index in range(7):
        last = data_compat.backup_before_install("2.4.3", f"2.4.{index + 4}")
    backups = list(data_compat.backups_dir().iterdir())
    assert len(backups) == 5
    assert last in backups
    assert (last / "config.json").read_text(encoding="utf-8") == config.read_text(encoding="utf-8")


def test_an_install_keeps_config_sessions_and_memories(home, monkeypatch):
    navin = home / ".navin"
    files = {
        navin / "config.json": '{"agents": {}}',
        navin / "workspace" / "sessions" / "a.jsonl": '{"key": "a"}\n',
        navin / "workspace" / "memory" / "MEMORY.md": "# memory\n",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    artifact = home / "update.exe"
    artifact.write_bytes(b"x")
    monkeypatch.setattr(service, "_STATE", {
        "state": "ready", "path": str(artifact),
        "update": {"latestVersion": "2.4.4", "installKind": "windows-setup", "rollback": False},
    })
    monkeypatch.setattr(service, "_stage_windows_helper", lambda version: home / "helper.exe")
    monkeypatch.setattr(service, "_start_desktop_updater", lambda command: None)
    monkeypatch.setattr(service, "_desktop_pid", lambda: None)
    monkeypatch.setattr(service, "_desktop_app", lambda: home / "Navin.exe")
    monkeypatch.setattr(service, "notify", mock.Mock())
    monkeypatch.setattr(service, "_schedule_shutdown", mock.Mock())
    assert service.install_update()["state"] == "restarting"
    for path, text in files.items():
        assert path.read_text(encoding="utf-8") == text
    assert list(data_compat.backups_dir().glob("*/config.json"))
    assert update_policy.read_history()[0]["event"] == "installing"


# --- background cycle -----------------------------------------------------


def settings(**values):
    return SimpleNamespace(**{"enabled": True, "auto_check": True, "auto_install": True, **values})


def updater(home, monkeypatch, *, state: dict, values: dict | None = None, saved: list | None = None):
    monkeypatch.setattr(auto.AutoUpdater, "_settings", lambda self: settings(**(values or {})))
    monkeypatch.setattr(service, "check_for_update", mock.Mock(return_value={}))
    monkeypatch.setattr(service, "update_status", lambda: state)
    monkeypatch.setattr(service, "start_update_download", mock.Mock())
    monkeypatch.setattr(service, "install_update", mock.Mock(return_value={"state": "restarting"}))
    monkeypatch.setattr(activity, "idle_for_s", lambda: 3600.0)

    def save() -> int:
        (saved if saved is not None else []).append("saved")
        return 2

    return auto.AutoUpdater(save_sessions=save)


OFFER = {"available": True, "supported": True, "installKind": "linux-deb", "latestVersion": "2.4.4"}


def test_checks_at_start_then_every_two_hours(home, monkeypatch):
    clock = [0.0]
    up = updater(home, monkeypatch, state={"state": "idle", "update": {}})
    up.clock = lambda: clock[0]
    up.tick()
    clock[0] = 60.0
    up.tick()
    assert service.check_for_update.call_count == 1
    clock[0] = update_policy.CHECK_INTERVAL_S + 1
    up.tick()
    assert service.check_for_update.call_count == 2
    assert update_policy.CHECK_INTERVAL_S == 2 * 60 * 60


def test_downloads_in_the_background(home, monkeypatch):
    up = updater(home, monkeypatch, state={"state": "available", "update": OFFER})
    assert up.tick() == "download-started"
    service.start_update_download.assert_called_once()
    service.install_update.assert_not_called()


def test_auto_install_off_only_announces(home, monkeypatch):
    up = updater(home, monkeypatch, state={"state": "available", "update": OFFER},
                 values={"auto_install": False})
    assert up.tick() == "announce-only"
    service.start_update_download.assert_not_called()
    up = updater(home, monkeypatch, state={"state": "available", "update": OFFER},
                 values={"auto_check": False})
    assert up.tick() == "disabled"


def test_never_installs_while_an_agent_works(home, monkeypatch):
    saved: list = []
    up = updater(home, monkeypatch, state={"state": "ready", "update": OFFER}, saved=saved)
    activity.register_busy_probe("subagents", lambda: 1)
    assert up.tick() == "waiting-busy"
    service.install_update.assert_not_called()
    assert saved == []


def test_a_mandatory_update_also_waits_for_the_mission(home, monkeypatch):
    up = updater(home, monkeypatch, state={"state": "ready", "update": {**OFFER, "mandatory": True}})
    activity.register_busy_probe("agents", lambda: 1)
    assert up.tick() == "waiting-busy"
    service.install_update.assert_not_called()


def test_a_probe_that_fails_counts_as_busy(home, monkeypatch):
    up = updater(home, monkeypatch, state={"state": "ready", "update": OFFER})

    def broken() -> int:
        raise RuntimeError("set changed size during iteration")

    activity.register_busy_probe("agents", broken)
    assert up.tick() == "waiting-busy"
    assert activity.busy_reasons() == ["agents:unknown (RuntimeError)"]


def test_waits_for_a_quiet_spell_then_checkpoints_and_installs(home, monkeypatch):
    saved: list = []
    up = updater(home, monkeypatch, state={"state": "ready", "update": OFFER}, saved=saved)
    monkeypatch.setattr(activity, "idle_for_s", lambda: 30.0)
    assert up.tick() == "waiting-idle"
    service.install_update.assert_not_called()
    monkeypatch.setattr(activity, "idle_for_s", lambda: up.idle_before_install_s + 1)
    assert up.tick() == "installing"
    assert saved == ["saved"]
    service.install_update.assert_called_once()


def test_formats_the_shell_installs_are_left_to_it(home, monkeypatch):
    update_policy.note_engine("tauri", "2.4.4")
    offer = {**OFFER, "installKind": "windows-setup"}
    up = updater(home, monkeypatch, state={"state": "available", "update": offer})
    assert up.tick() == "shell-installs"
    service.start_update_download.assert_not_called()


def test_the_cli_is_announced_not_swapped_under_the_terminal(home, monkeypatch):
    offer = {**OFFER, "installKind": "cli"}
    up = updater(home, monkeypatch, state={"state": "ready", "update": offer})
    assert up.tick() == "announce-only"
    service.install_update.assert_not_called()


def test_the_cli_names_a_rollback_and_a_required_update(home, monkeypatch):
    from navin.update import notice

    monkeypatch.setattr(service, "__version__", "2.4.3")
    monkeypatch.setattr(notice, "__version__", "2.4.3")
    base = {"available": True, "supported": True, "installKind": "cli"}
    monkeypatch.setattr(notice, "latest_update_info", lambda force=False: {
        **base, "latestVersion": "2.4.2", "rollback": True, "mandatory": True,
    })
    assert notice.update_notice().startswith("navin 2.4.3 was withdrawn; navin 2.4.2")
    monkeypatch.setattr(notice, "latest_update_info", lambda force=False: {
        **base, "latestVersion": "2.4.4", "mandatory": True,
    })
    assert notice.update_notice().startswith("navin 2.4.4 is a required update")


def test_navin_update_check_reports_a_paused_release(home, monkeypatch):
    from typer.testing import CliRunner

    from navin.cli.commands import app

    monkeypatch.setattr(service, "__version__", "2.4.3")
    monkeypatch.setattr(service, "_install_kind", lambda: "cli")
    monkeypatch.setattr(service, "_platform_keys", lambda kind: ("cli-linux-x64",))
    monkeypatch.setattr(service, "_update_config", lambda: ("https://updates.example", "enterprise", ""))
    monkeypatch.setattr(service, "_fetch_manifest", lambda base, channel: manifest(
        channel="enterprise", suspended=True,
        artifacts={"cli-linux-x64": {"url": "x.tar.gz", "size": 1, "sha256": "c" * 64}},
    ))
    result = CliRunner().invoke(app, ["update", "--check"])
    assert result.exit_code == 1, result.output
    assert "paused by the publisher" in " ".join(result.output.split())


def test_prepare_refuses_while_work_runs_and_saves_otherwise(home, monkeypatch):
    activity.register_busy_probe("commands", lambda: 1)
    assert auto.prepare_for_install(lambda: 3) == {"ready": False, "busy": ["commands:1"], "checkpointed": 0}
    activity.register_busy_probe("commands", lambda: 0)
    assert auto.prepare_for_install(lambda: 3) == {"ready": True, "busy": [], "checkpointed": 3}


def test_a_chat_turn_blocks_the_restart(home, monkeypatch):
    from navin.session import webui_turns

    monkeypatch.setattr(webui_turns, "_WEBSOCKET_TURN_WALL_STARTED_AT", {"chat-1": time.time()})
    assert activity.busy_reasons() == ["chat:1"]


def test_only_packaged_builds_start_the_cycle(home, monkeypatch):
    monkeypatch.setattr(auto, "_INSTANCE", None)
    monkeypatch.delattr("sys.frozen", raising=False)
    assert auto.start_auto_updater(lambda: 0) is None
    assert auto.current_save_sessions() is not None


# --- routes ---------------------------------------------------------------


def router():
    from navin.webui.settings_routes import WebUISettingsRouter

    return WebUISettingsRouter(
        bus=SimpleNamespace(),
        logger=SimpleNamespace(),
        check_api_token=lambda _request: True,
        parse_query=lambda path: _query(path),
        json_response=lambda payload: ("json", payload),
        error_response=lambda status, message: ("error", status, message),
        runtime_surface="desktop",
        runtime_capabilities={},
    )


def _query(path: str) -> dict[str, list[str]]:
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(path).query)


async def call(path: str, *, local: bool = True):
    from navin.webui import settings_routes

    request = SimpleNamespace(path=path, headers={})
    with mock.patch.object(settings_routes, "_is_local_browser_request", return_value=local):
        return await router().dispatch(None, request, path.split("?", 1)[0])


@pytest.mark.asyncio
async def test_routes_for_the_shell(home, monkeypatch):
    from navin.webui import settings_routes

    monkeypatch.setattr(settings_routes, "update_policy_for",
                        lambda version: {"allowed": version == "2.4.4", "reason": "", "mandatory": False})
    assert (await call("/api/settings/update-policy?version=2.4.4"))[1]["allowed"] is True
    assert (await call("/api/settings/update-policy"))[:2] == ("error", 400)

    assert (await call("/api/settings/update-engine?engine=tauri&version=2.4.4"))[1]["ok"] is True
    assert update_policy.tauri_engine_active()
    assert (await call("/api/settings/update-engine?engine=tauri", local=False))[:2] == ("error", 403)

    report = await call("/api/settings/update-report?event=error&version=2.4.4&detail=bad%20signature")
    assert report[1]["entry"]["ok"] is False
    assert (await call("/api/settings/update-report?event=pwned"))[:2] == ("error", 400)

    monkeypatch.setattr(settings_routes, "update_status", lambda: {"state": "idle"})
    history = await call("/api/settings/update-history?limit=5")
    assert history[1]["history"][0]["detail"] == "bad signature"
    assert history[1]["status"] == {"state": "idle"}

    auto.register_session_saver(lambda: 4)
    assert (await call("/api/settings/update-prepare"))[1] == {"ready": True, "busy": [], "checkpointed": 4}
    activity.register_busy_probe("agents", lambda: 2)
    assert (await call("/api/settings/update-prepare"))[1]["ready"] is False
    assert (await call("/api/settings/update-prepare", local=False))[:2] == ("error", 403)


@pytest.mark.asyncio
async def test_preferences_accept_enterprise_and_auto_install(home, monkeypatch):
    from navin.config.schema import Config
    from navin.webui import settings_routes

    config = Config()
    stored: list = []
    monkeypatch.setattr(settings_routes, "load_config", lambda: config)
    monkeypatch.setattr(settings_routes, "save_config", lambda value: stored.append(value))
    result = await call("/api/settings/update-preferences?channel=enterprise&autoInstall=false")
    assert result[1]["channel"] == "enterprise" and result[1]["autoInstall"] is False
    assert stored and config.updates.auto_install is False
    assert json.loads(config.updates.model_dump_json(by_alias=True))["autoInstall"] is False
    assert (await call("/api/settings/update-preferences?channel=nightly"))[:2] == ("error", 400)
