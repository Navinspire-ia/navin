"""Desktop CLI settings reach the next agent turn and the selected instance."""

from __future__ import annotations

import io
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rich.console import Console
from typer.testing import CliRunner

from navin.agent.tools.computer import refresh_computer_registration
from navin.agent.tools.registry import ToolRegistry
from navin.cli.computer import _xvfb_transport_args, create_computer_app
from navin.computer.base import Check, NotSupportedError, PermissionMissingError
from navin.computer.detect import BackendChoice
from navin.config.loader import get_config_path, load_config, save_config, set_config_path
from navin.config.schema import Config, ModelPresetConfig


@pytest.fixture
def cli():
    output = io.StringIO()
    app = create_computer_app(console=Console(file=output, color_system=None, width=180))
    runner = CliRunner()

    def run(*args):
        output.seek(0)
        output.truncate()
        result = runner.invoke(app, list(args))
        return result, output.getvalue()

    return run


def test_permissions_command_prepares_all_access_by_default(cli, monkeypatch):
    backend = MagicMock()
    backend.request_permissions.return_value = [Check("screen_recording", False, "awaiting consent")]
    monkeypatch.setattr("navin.computer.detect.create_backend", lambda _config: backend)
    result, output = cli("permissions")
    assert result.exit_code == 0, result.exception
    backend.request_permissions.assert_called_once_with("all")
    backend.close.assert_called_once()
    assert "awaiting consent" in output


def test_reenable_preserves_dedicated_desktop_and_policy_until_explicitly_changed(cli, tmp_path):
    path = tmp_path / "dedicated" / "config.json"
    config = Config()
    config.tools.computer.ask = "always"
    config.tools.computer.session_mode = "dedicated"
    config.tools.computer.display = ":131"
    config.tools.computer.live_view = False
    save_config(config, path)

    result, output = cli("enable", "--config", str(path))
    assert result.exit_code == 0, result.exception
    saved = load_config(path).tools.computer
    assert saved.enabled
    assert (saved.ask, saved.session_mode, saved.display) == ("always", "dedicated", ":131")
    assert not saved.live_view
    assert "ask: always, session: dedicated" in output

    result, _ = cli("enable", "--shared", "--ask", "never", "--live-view", "--config", str(path))
    assert result.exit_code == 0, result.exception
    saved = load_config(path).tools.computer
    assert (saved.ask, saved.session_mode, saved.live_view) == ("never", "shared", True)


@pytest.mark.parametrize("backend", ["auto", "windows", "macos", "x11", "wayland", "none"])
def test_cli_computer_config_updates_registration_without_restarting_agent(cli, tmp_path, backend):
    path = tmp_path / "instance" / "config.json"
    config = Config()
    config.tools.computer.enabled = False
    config.model_presets["desktop"] = ModelPresetConfig(provider="openai", model="private/visual")
    save_config(config, path)
    registry = ToolRegistry()

    def loader():
        return load_config(path).tools.computer

    refresh_computer_registration(registry, loader)
    assert not registry.has("computer")

    result, _ = cli("enable", "--backend", backend, "--model", "desktop", "--no-live-view",
                    "--anthropic-native", "--config", str(path))
    assert result.exit_code == 0, result.exception
    refresh_computer_registration(registry, loader)
    assert registry.has("computer")
    saved = load_config(path)
    assert saved.tools.computer.backend == backend
    assert saved.tools.computer.anthropic_native
    assert not saved.tools.computer.live_view
    assert saved.model_routes["computer"] == "desktop"

    result, _ = cli("disable", "--config", str(path))
    assert result.exit_code == 0, result.exception
    refresh_computer_registration(registry, loader)
    assert not registry.has("computer")
    assert load_config(path).model_routes["computer"] == "desktop"


@pytest.mark.parametrize("options", [("--ask", "sometimes"), ("--backend", "invented"),
                                     ("--model", "missing-preset")])
def test_rejected_options_do_not_partly_enable_the_tool(cli, options):
    config = Config()
    config.tools.computer.enabled = False
    save_config(config)
    before = get_config_path().read_bytes()
    result, _ = cli("enable", *options)
    assert result.exit_code == 1
    assert get_config_path().read_bytes() == before


def test_stop_and_go_only_control_selected_config_instance_even_with_broken_config(cli, tmp_path):
    default_path = get_config_path()
    selected = tmp_path / "selected" / "config.json"
    save_config(Config(), selected)
    selected.write_text('{"tools": ', encoding="utf-8")
    result, _ = cli("stop", "maintenance", "--config", str(selected))
    assert result.exit_code == 0, result.exception
    selected_stop = selected.parent / "computer" / "STOP"
    assert "maintenance" in selected_stop.read_text()
    assert not (default_path.parent / "computer" / "STOP").exists()

    set_config_path(default_path)
    result, _ = cli("go", "--config", str(selected))
    assert result.exit_code == 0, result.exception
    assert not selected_stop.exists()
    assert get_config_path() == selected


def test_display_start_and_stop_store_pid_in_selected_instance(cli, tmp_path, monkeypatch):
    default_path = get_config_path()
    selected = tmp_path / "selected" / "config.json"
    save_config(Config(), selected)
    proc = MagicMock(pid=54321)
    proc.poll.return_value = None
    popen = MagicMock(return_value=proc)
    killpg = MagicMock()
    monkeypatch.setattr("navin.cli.computer.sys.platform", "linux")
    monkeypatch.setattr("navin.cli.computer.shutil.which", lambda _: "/test/Xvfb")
    monkeypatch.setattr("navin.cli.computer.subprocess.Popen", popen)
    monkeypatch.setattr("navin.cli.computer.time.sleep", lambda _: None)
    monkeypatch.setattr("navin.cli.computer.os.killpg", killpg, raising=False)

    result, _ = cli("display", "start", "--display", ":141", "--no-wm", "--config", str(selected))
    assert result.exit_code == 0, result.exception
    assert (selected.parent / "computer" / "xvfb-141.pid").read_text() == "54321"
    assert not (default_path.parent / "computer" / "xvfb-141.pid").exists()
    assert load_config(selected).tools.computer.session_mode == "dedicated"
    assert popen.call_args.args[0][1] == ":141"

    set_config_path(default_path)
    result, _ = cli("display", "stop", "--display", ":141", "--config", str(selected))
    assert result.exit_code == 0, result.exception
    assert not (selected.parent / "computer" / "xvfb-141.pid").exists()
    assert load_config(selected).tools.computer.display is None
    killpg.assert_called_once()
    assert killpg.call_args.args[0] == 54321


@pytest.mark.parametrize("options", [("--size", "0x800"), ("--size", "800x-10"),
                                     ("--display", ":../other")])
def test_bad_display_options_are_rejected_before_starting_process(cli, monkeypatch, options):
    popen = MagicMock()
    monkeypatch.setattr("navin.cli.computer.sys.platform", "linux")
    monkeypatch.setattr("navin.cli.computer.shutil.which", lambda _: "/test/Xvfb")
    monkeypatch.setattr("navin.cli.computer.subprocess.Popen", popen)
    result, _ = cli("display", "start", *options)
    assert result.exit_code == 1, result.exception
    popen.assert_not_called()


@pytest.mark.parametrize("readonly", [False, True])
def test_wslg_readonly_socket_mount_uses_local_abstract_transport(monkeypatch, readonly):
    flag = getattr(os, "ST_RDONLY", 1)
    monkeypatch.setattr(os, "ST_RDONLY", flag, raising=False)
    monkeypatch.setattr(os, "statvfs", lambda _: SimpleNamespace(f_flag=flag if readonly else 0), raising=False)
    args = _xvfb_transport_args()
    assert args == (["-nolisten", "unix", "-listen", "local"] if readonly else [])


def test_macos_doctor_fails_when_capture_only_shows_wallpaper(cli, monkeypatch):
    backend = MagicMock(label="macOS")
    backend.doctor.return_value = [Check("screen", True, "1440x900"),
                                   Check("screenshot", True, "PNG captured"),
                                   Check("screen_recording", False, "permission not granted")]
    monkeypatch.setattr("navin.computer.detect.detect_platform",
                        lambda **_: BackendChoice("macos", "macOS desktop"))
    monkeypatch.setattr("navin.computer.detect.create_backend", lambda _: backend)
    result, output = cli("doctor")
    assert result.exit_code == 1
    assert "screen_recording" in output
    backend.close.assert_called_once()


def test_doctor_closes_backend_and_reports_failed_probe(cli, monkeypatch):
    backend = MagicMock()
    backend.doctor.side_effect = PermissionMissingError("allow desktop access")
    monkeypatch.setattr("navin.computer.detect.detect_platform",
                        lambda **_: BackendChoice("x11", "test display"))
    monkeypatch.setattr("navin.computer.detect.create_backend", lambda _: backend)
    result, output = cli("doctor")
    assert result.exit_code == 1
    assert "allow desktop access" in output
    assert isinstance(result.exception, SystemExit)
    backend.close.assert_called_once()


def test_screenshot_without_display_returns_actionable_cli_error(cli, monkeypatch):
    create = MagicMock(side_effect=NotSupportedError("no desktop to control: DISPLAY is not set"))
    monkeypatch.setattr("navin.computer.detect.create_backend", create)
    result, output = cli("screenshot")
    assert result.exit_code == 1
    assert "DISPLAY is not set" in output
    assert isinstance(result.exception, SystemExit)
