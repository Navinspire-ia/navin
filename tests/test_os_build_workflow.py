# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The OS installers workflow has to publish the CLI next to the desktop app.

build-offline.sh (Linux, macOS) and build-desktop.ps1 (Windows) pack
navin-cli-<version>-<os>-<arch>.tar.gz|.zip and its .sha256; the curl installer
and `navin update` download them from the release. These tests run the real
release upload steps of os-build.yml in bash against a fake build tree, with
release-upload.sh replaced by a stub that prints what it would upload.

The optional S3 job rebuilds the os/ tree of a local build from the run
artifacts and runs the same scripts/publish-os-to-s3.sh as `make aws-upload`;
its staging and publish steps run here too, with the script stubbed.
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


# --- S3 publish: the run artifacts become the os/ tree of a local build -------

# Artifact name -> files inside it, as upload-artifact roots them.
ARTIFACTS = {
    "linux-x64": [
        f"appimage/x64/Navin-{VERSION}-linux-x64.AppImage",
        f"deb/x64/navin_{VERSION}_amd64.deb",
        f"bin/x64/navin-cli-{VERSION}-linux-x64.tar.gz",
    ],
    "macos-arm64": [f"Navin-Desktop-{VERSION}-macos-arm64.dmg", f"navin-cli-{VERSION}-macos-arm64.tar.gz"],
    "macos-x64": [f"Navin-Desktop-{VERSION}-macos-x64.dmg", f"navin-cli-{VERSION}-macos-x64.tar.gz"],
    "windows-x64": [f"Navin-Desktop-{VERSION}-windows-x64.msi", f"navin-cli-{VERSION}-windows-x64.zip"],
    "webui-dist": ["index.html"],
}


def _run_s3_step(tmp_path: Path, name: str, env: dict[str, str]) -> tuple[int, str, Path]:
    build = tmp_path / "claw"
    build.mkdir(exist_ok=True)
    result = subprocess.run(
        ["bash", "-c", _script("s3", name, "x64")],
        cwd=build, capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "RUNNER_TEMP": str(tmp_path),
             "GITHUB_OUTPUT": str(tmp_path / "github-output"), **env},
    )
    return result.returncode, result.stdout + result.stderr, build


def _stage(tmp_path: Path, artifacts: list[str], **results: str) -> tuple[int, str, Path]:
    for artifact in artifacts:
        for relative in ARTIFACTS[artifact]:
            path = tmp_path / "os-artifacts" / artifact / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(artifact, encoding="utf-8")
    env = {f"{job.upper()}_RESULT": results.get(job, "skipped") for job in ("linux", "macos", "windows")}
    return _run_s3_step(tmp_path, "Stage the os/ tree", env)


def test_s3_publish_only_runs_when_asked() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    publish = (workflow.get("on") or workflow[True])["workflow_dispatch"]["inputs"]["publish_s3"]
    assert publish["type"] == "boolean" and publish["default"] is False
    job = workflow["jobs"]["s3"]
    assert "inputs.publish_s3" in job["if"] and set(job["needs"]) == {"test", "linux", "macos", "windows"}


def test_s3_stage_rebuilds_the_local_os_tree(tmp_path: Path) -> None:
    code, output, build = _stage(
        tmp_path, ["linux-x64", "macos-arm64", "macos-x64", "windows-x64", "webui-dist"],
        linux="success", macos="success", windows="success",
    )
    assert code == 0, output
    # The paths publish-os-to-s3.sh reads (its MAPPINGS column 1, under os/).
    for relative in (
        f"linux/appimage/x64/Navin-{VERSION}-linux-x64.AppImage",
        f"linux/deb/x64/navin_{VERSION}_amd64.deb",
        f"linux/bin/x64/navin-cli-{VERSION}-linux-x64.tar.gz",
        f"macos/arm64/Navin-Desktop-{VERSION}-macos-arm64.dmg",
        f"macos/x64/navin-cli-{VERSION}-macos-x64.tar.gz",
        f"windows/x64/navin-cli-{VERSION}-windows-x64.zip",
    ):
        assert (build / "os" / relative).is_file(), relative
    assert not (build / "os" / "webui-dist").exists()
    assert "staged=linux-x64 macos-arm64 macos-x64 windows-x64" in (tmp_path / "github-output").read_text()


def test_s3_stage_leaves_out_a_failed_matrix_leg(tmp_path: Path) -> None:
    code, output, build = _stage(tmp_path, ["linux-x64", "macos-x64"], linux="success", macos="failure")
    assert code == 0, output
    assert (build / "os" / "macos" / "x64" / f"Navin-Desktop-{VERSION}-macos-x64.dmg").is_file()
    assert not (build / "os" / "macos" / "arm64").exists()
    assert "skip macos-arm64" in output and "skip windows-x64" in output, output


def test_s3_stage_fails_when_a_successful_job_left_no_artifact(tmp_path: Path) -> None:
    code, output, build = _stage(tmp_path, ["macos-arm64", "macos-x64"], linux="success", macos="success")
    assert code != 0 and "linux-x64: the job succeeded but its artifact is missing" in output, output
    assert not (build / "os").exists()


def test_s3_stage_fails_when_nothing_was_built(tmp_path: Path) -> None:
    code, output, _ = _stage(tmp_path, ["webui-dist"], linux="failure", macos="failure", windows="failure")
    assert code != 0 and "nothing to publish" in output, output


def _publish(tmp_path: Path, **env: str) -> tuple[int, str, Path]:
    script = tmp_path / "claw" / "scripts" / "publish-os-to-s3.sh"
    script.parent.mkdir(parents=True)
    script.write_text(
        'echo "PUBLISH args=$# env=$(wc -c < .env) key=${AWS_ACCESS_KEY_ID:+set} bucket=${BUCKET_NAME:-default}"\n',
        encoding="utf-8",
    )
    (tmp_path / "claw" / ".env").write_text("AWS_ACCESS_KEY_ID=stale\n", encoding="utf-8")
    return _run_s3_step(tmp_path, "Publish with scripts/publish-os-to-s3.sh", env)


def test_s3_publish_runs_the_local_script_with_the_repository_secrets(tmp_path: Path) -> None:
    code, output, _ = _publish(tmp_path, AWS_ACCESS_KEY_ID="id", AWS_SECRET_ACCESS_KEY="secret", BUCKET_NAME="b")
    assert code == 0, output
    # Same call as `make aws-upload` (version from tauri.conf.json, all
    # platforms), with the secrets from the environment and an empty .env.
    assert "PUBLISH args=0 env=0 key=set bucket=b" in output, output


def test_s3_publish_names_the_missing_secrets(tmp_path: Path) -> None:
    code, output, _ = _publish(tmp_path, AWS_ACCESS_KEY_ID="", AWS_SECRET_ACCESS_KEY="")
    assert code != 0 and "PUBLISH" not in output, output
    assert "AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY" in output, output
