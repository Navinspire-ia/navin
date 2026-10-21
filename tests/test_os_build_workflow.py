# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The OS installers workflow has to publish the CLI next to the desktop app.

build-offline.sh (Linux, macOS) and build-desktop.ps1 (Windows) pack
navin-cli-<version>-<os>-<arch>.tar.gz|.zip and its .sha256; the curl installer
and `navin update` download them from the release. These tests run the real
release upload steps of os-build.yml in bash against a fake build tree, with
release-upload.sh replaced by a stub that prints what it would upload.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "os-build.yml"
VERSION = "2.3.2"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="the steps run in bash")

# job, release step, matrix arch, installers, CLI archive
PLATFORMS = {
    "linux": (
        "linux",
        "Upload Linux installers to Release",
        "x64",
        [
            f"os/linux/appimage/x64/Navin-{VERSION}-linux-x64.AppImage",
            f"os/linux/deb/x64/navin_{VERSION}_amd64.deb",
            "os/linux/bin/x64/SHA256SUMS-linux-x64.txt",
        ],
        f"os/linux/bin/x64/navin-cli-{VERSION}-linux-x64.tar.gz",
    ),
    "macos-arm64": (
        "macos",
        "Upload notarized DMG to Release",
        "arm64",
        [
            f"os/macos/arm64/Navin-Desktop-{VERSION}-macos-arm64.dmg",
            "os/macos/arm64/SHA256SUMS-desktop-arm64.txt",
        ],
        f"os/macos/arm64/navin-cli-{VERSION}-macos-arm64.tar.gz",
    ),
    "macos-x64": (
        "macos",
        "Upload notarized DMG to Release",
        "x64",
        [
            f"os/macos/x64/Navin-Desktop-{VERSION}-macos-x64.dmg",
            "os/macos/x64/SHA256SUMS-desktop-x64.txt",
        ],
        f"os/macos/x64/navin-cli-{VERSION}-macos-x64.tar.gz",
    ),
    "windows": (
        "windows",
        "Upload Windows installers to Release",
        "x64",
        [
            f"os/windows/x64/Navin-Desktop-{VERSION}-windows-x64.msi",
            f"os/windows/x64/Navin-Desktop-{VERSION}-windows-x64-setup.exe",
        ],
        f"os/windows/x64/navin-cli-{VERSION}-windows-x64.zip",
    ),
}


def _steps(job: str) -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"][job]["steps"]


def _script(job: str, name: str, arch: str) -> str:
    step = next((s for s in _steps(job) if s.get("name") == name), None)
    assert step is not None, f"{job}: step {name!r} is gone"
    script = step["run"].replace("${{ matrix.arch }}", arch)
    assert "${{" not in script, f"{job}: unexpected expression left in {name!r}"
    return script


def _upload(tmp_path: Path, platform: str, files: list[str]) -> tuple[int, str, list[str]]:
    job, name, arch, _, _ = PLATFORMS[platform]
    workspace = tmp_path / "workspace"
    stub = workspace / ".github" / "scripts" / "release-upload.sh"
    stub.parent.mkdir(parents=True)
    stub.write_text('shift\nfor f in "$@"; do echo "UPLOAD ${f##*/}"; done\n', encoding="utf-8")
    build = tmp_path / "claw"
    build.mkdir()
    for relative in files:
        path = build / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
        "RUNNER_TEMP": str(tmp_path),
        "TAG": f"v{VERSION}",
        "ARCH": arch,
    }
    result = subprocess.run(
        ["bash", "-c", _script(job, name, arch)],
        cwd=build, env=env, capture_output=True, text=True, timeout=30,
    )
    output = result.stdout + result.stderr
    uploaded = [line.split(" ", 1)[1] for line in output.splitlines() if line.startswith("UPLOAD ")]
    return result.returncode, output, uploaded


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
def test_release_upload_ships_the_cli_archive_and_its_checksum(tmp_path: Path, platform: str) -> None:
    _, _, _, installers, cli = PLATFORMS[platform]
    code, output, uploaded = _upload(tmp_path, platform, [*installers, cli, f"{cli}.sha256"])
    assert code == 0, output
    archive = Path(cli).name
    assert archive in uploaded and f"{archive}.sha256" in uploaded, output
    assert {Path(f).name for f in installers} <= set(uploaded), output


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
def test_release_upload_fails_when_the_cli_archive_is_missing(tmp_path: Path, platform: str) -> None:
    _, _, _, installers, _ = PLATFORMS[platform]
    code, output, uploaded = _upload(tmp_path, platform, installers)
    assert code != 0 and uploaded == [], output
    assert "CLI archive" in output, output


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
def test_release_upload_fails_when_the_cli_checksum_is_missing(tmp_path: Path, platform: str) -> None:
    _, _, _, installers, cli = PLATFORMS[platform]
    code, output, uploaded = _upload(tmp_path, platform, [*installers, cli])
    assert code != 0 and uploaded == [], output
    assert "CLI archive" in output, output


@pytest.mark.parametrize(
    ("job", "name", "expected"),
    [
        ("linux", "Upload Linux installers", "/os/linux/bin/x64/navin-cli-*"),
        ("macos", "Upload macOS DMG artifact", "/os/macos/${{ matrix.arch }}/navin-cli-*"),
        ("windows", "Upload Windows installers", "/os/windows/x64/"),
    ],
)
def test_run_artifacts_keep_the_cli_archive(job: str, name: str, expected: str) -> None:
    step = next(s for s in _steps(job) if s.get("name") == name)
    paths = [p.strip() for p in step["with"]["path"].splitlines() if p.strip()]
    assert any(p.endswith(expected) for p in paths), paths
