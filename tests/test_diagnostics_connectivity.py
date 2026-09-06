"""Behavioral tests for doctor connectivity checks without live services."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from navin.diagnostics import connectivity_checks, doctor_report, sandbox_check
from navin.ports import PortCheckResult, ResolvedPort, port_registry_by_name


def _row(name: str, status: str, *, host: str = "127.0.0.1", port: int = 9000):
    spec = port_registry_by_name()[name]
    return PortCheckResult(
        role=ResolvedPort(spec=spec, host=host, port=port),
        status=status,
        detail="test",
    )


def test_free_ports_are_reported_without_http_probe() -> None:
    calls: list[str] = []

    def probe(url: str) -> tuple[bool, str]:
        calls.append(url)
        return True, "HTTP 200"

    with patch(
        "navin.ports.check_ports",
        return_value=[
            _row("webui", "free", host="0.0.0.0", port=8765),
            _row("gateway", "free", port=18790),
            _row("api", "free", port=8900),
        ],
    ):
        checks = connectivity_checks(workspace=Path("/workspace"), health_probe=probe)

    by_name = {check.name: check for check in checks}
    assert by_name["recommended URL"].detail == "http://127.0.0.1:8765/"
    assert by_name["webui port"].ok
    assert "service not running" in by_name["webui port"].detail
    assert calls == []


def test_running_port_uses_http_health_result() -> None:
    with patch(
        "navin.ports.check_ports",
        return_value=[_row("gateway", "busy", port=18790)],
    ):
        checks = connectivity_checks(
            health_probe=lambda url: (
                url == "http://127.0.0.1:18790/health",
                "HTTP 200",
            )
        )

    port = next(check for check in checks if check.name == "gateway port")
    assert port.ok
    assert "health HTTP 200" in port.detail


def test_unhealthy_listener_has_actionable_health_url() -> None:
    with patch(
        "navin.ports.check_ports",
        return_value=[_row("api", "busy", host="::", port=8900)],
    ):
        checks = connectivity_checks(
            health_probe=lambda _url: (False, "HTTP 503"),
        )

    port = next(check for check in checks if check.name == "api port")
    assert not port.ok
    assert "HTTP 503" in port.detail
    assert "http://127.0.0.1:8900/health" in port.hint


def test_wsl_unc_context_is_explicit() -> None:
    workspace = Path(r"\\wsl.localhost\Ubuntu\home\me\project")
    with patch("navin.ports.check_ports", return_value=[]):
        (context,) = connectivity_checks(workspace=workspace)

    assert context.ok
    assert "WSL UNC workspace" in context.detail
    assert "Ubuntu:/home/me/project" in context.detail
    assert "wsl.exe" in context.hint


def test_doctor_report_includes_connectivity_and_existing_media_checks() -> None:
    with (
        patch("navin.diagnostics.connectivity_checks", return_value=[]),
        patch("navin.diagnostics.token_efficiency_checks", return_value=[]),
    ):
        sections = doctor_report()

    by_title = {section.title: section for section in sections}
    assert "Connectivity" in by_title
    install_names = {check.name for check in by_title["Installation"].checks}
    assert "navin-sandbox" in install_names
    external_names = {check.name for check in by_title["External tools"].checks}
    assert {"ffmpeg", "chromium"} <= external_names


def test_sandbox_check_missing_is_reported_without_blocking_doctor() -> None:
    with (
        patch("navin.agent.tools.sandbox.native_sandbox_binary", return_value=None),
        patch("navin.python_runtime.packaged", return_value=True),
        patch("navin.diagnostics.sys.platform", "linux"),
    ):
        check = sandbox_check()
    assert check.name == "navin-sandbox"
    assert not check.ok
    assert not check.required


def test_sandbox_check_is_ok_when_the_binary_is_present() -> None:
    with (
        patch(
            "navin.agent.tools.sandbox.native_sandbox_binary",
            return_value="/opt/navin/navin-sandbox",
        ),
        patch("navin.diagnostics.sys.platform", "linux"),
    ):
        check = sandbox_check()
    assert check.ok
    assert "/opt/navin/navin-sandbox" in check.detail


def test_sandbox_check_on_windows_reports_the_shipped_helper() -> None:
    with (
        patch(
            "navin.agent.tools.sandbox.native_sandbox_binary",
            return_value=r"C:\Navin\navin-sandbox.exe",
        ),
        patch("navin.diagnostics.sys.platform", "win32"),
    ):
        check = sandbox_check()
    assert check.ok
    assert r"C:\Navin\navin-sandbox.exe" in check.detail
    assert "pass-through" in check.detail
