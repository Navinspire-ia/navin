"""Sanity checks for make / sh install-start-stop helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DOCKER_FILES = [
    ROOT / "Dockerfile",
    ROOT / "docker-compose.yml",
    ROOT / "entrypoint.sh",
]
DEV_SCRIPTS = [
    SCRIPTS / "dev-lib.sh",
    SCRIPTS / "install.sh",
    SCRIPTS / "start.sh",
    SCRIPTS / "stop.sh",
    SCRIPTS / "restart.sh",
    SCRIPTS / "status.sh",
    SCRIPTS / "build.sh",
]


def test_dev_scripts_are_valid_posix() -> None:
    sh = shutil.which("sh")
    if sh is None:
        return
    for path in DEV_SCRIPTS:
        subprocess.run([sh, "-n", str(path)], check=True)


def test_help_exits_zero() -> None:
    sh = shutil.which("sh")
    if sh is None:
        return
    for name in ("install.sh", "start.sh", "stop.sh", "restart.sh", "status.sh", "build.sh"):
        proc = subprocess.run(
            [sh, str(SCRIPTS / name), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, name
        assert "Usage" in proc.stdout or "Usage" in proc.stderr


def test_unknown_flag_fails() -> None:
    sh = shutil.which("sh")
    if sh is None:
        return
    proc = subprocess.run(
        [sh, str(SCRIPTS / "start.sh"), "--not-a-flag"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "inconnue" in proc.stderr.lower() or "inconnue" in proc.stdout.lower()


def test_makefile_scopes_to_scripts() -> None:
    make = shutil.which("make")
    if make is None:
        return
    cases = {
        ("start",): "sh scripts/start.sh all",
        ("start", "front"): "sh scripts/start.sh front",
        ("start", "backend"): "sh scripts/start.sh backend",
        ("stop", "front"): "sh scripts/stop.sh front",
        ("restart", "backend"): "sh scripts/restart.sh backend",
        ("install",): "sh scripts/install.sh all",
        ("install", "front"): "sh scripts/install.sh front",
        ("build",): "sh scripts/build.sh all",
        ("build", "front"): "sh scripts/build.sh front",
        ("build", "backend"): "sh scripts/build.sh backend",
        ("start-prod",): "sh scripts/start.sh --prod",
    }
    for goals, expected in cases.items():
        proc = subprocess.run(
            [make, "-n", *goals],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert expected in proc.stdout, (goals, proc.stdout)


def test_makefile_gateway_default_matches_schema() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "18790" in text
    assert "18791" not in text


def test_docker_has_no_nanobot_and_points_at_public_repo() -> None:
    blob = "\n".join(p.read_text(encoding="utf-8") for p in DOCKER_FILES)
    assert "nanobot" not in blob.lower()
    assert "hkuids" not in blob.lower()
    assert "navin-claw" not in blob
    assert "EIAGEN" not in blob
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "https://github.com/navinspire-ai/navin-agi" in dockerfile
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "navin-gateway" in compose
    assert "navinspire-ai/navin-agi" in compose


def test_dev_lib_defaults_to_schema_ports() -> None:
    text = (SCRIPTS / "dev-lib.sh").read_text(encoding="utf-8")
    assert "18790" in text
    assert "8765" in text
    assert "18791" not in text
