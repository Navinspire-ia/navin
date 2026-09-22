# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI one-liner install scripts shipped at /install on navin.live."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "site/front/src/app/install"
POSIX = INSTALL / "posix.sh"
WINDOWS = INSTALL / "windows.ps1"
ROUTE = INSTALL / "route.ts"
GENERATED = INSTALL / "scripts.generated.ts"
EMBED = ROOT / "site/front/scripts/embed-install-scripts.mjs"


pytestmark = pytest.mark.skipif(
    not POSIX.is_file(),
    reason="site/ is not in this checkout",
)


def test_posix_script_is_valid_bash() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not on PATH")
    subprocess.run([bash, "-n", str(POSIX)], check=True)


def test_posix_script_uses_official_channel() -> None:
    text = POSIX.read_text(encoding="utf-8")
    assert "navinagent.s3.eu-north-1.amazonaws.com" in text
    assert 'SITE="${NAVIN_SITE:-https://navin.live}"' in text
    assert "releases.json" in text
    assert "SHA256SUMS.txt" in text
    assert "dpkg-deb" in text
    assert "hdiutil" in text
    assert "curl https://navin.live/install" in text
    assert 'PREFIX="${NAVIN_PREFIX:-${HOME}/.local}"' in text
    assert "navin-cli-" in text
    assert "install_from_tarball" in text
    assert "replace_file()" in text
    assert 'rm -f "$dest"' in text
    assert "chflags nouchg" in text
    assert "xattr -s -c" in text
    assert 'cat > "$out"' not in text
    # navin-cli wrapper: the terminal UI in the current folder.
    assert '${BIN_DIR}/navin-cli' in text
    assert '_emit_wrapper "${BIN_DIR}/navin-cli" "tui "' in text
    assert '${sub}"\\$@"' in text
    assert "navin-cli " in text
    assert "navin agent" not in text
    assert "cd your-project && navin-cli" in text
    assert "https://navin.live/en/docs/cli" in text
    assert "_print_ready" in text
    assert "sudo pacman -U --noconfirm --needed" in text
    assert "pkexec pacman -U --noconfirm --needed" in text
    assert "install_with_pacman" in text
    assert "omarchy" in text


def test_install_route_rewrites_only_loopback() -> None:
    text = ROUTE.read_text(encoding="utf-8")
    assert "isLocalInstallHost" in text
    assert "localizeInstallScript" in text
    local = (ROOT / "site/front/src/lib/install-local.ts").read_text(encoding="utf-8")
    assert "/releases" in local
    assert "navin.live" in local
    assert "navinagent.s3.eu-north-1.amazonaws.com" in local


def test_windows_script_is_silent_official_setup() -> None:
    text = WINDOWS.read_text(encoding="utf-8")
    assert "navinagent.s3.eu-north-1.amazonaws.com" in text
    assert "SHA256SUMS.txt" in text
    assert "/S" in text
    assert "windows-x64-setup.exe" in text
    assert "navin-cli-" in text
    assert "win32=true" in text
    assert "Navin\\bin" in text
    assert "navin agent" not in text
    assert "cd your-project; navin-cli" in text
    assert "https://navin.live/en/docs/cli" in text


def test_install_route_dispatches_on_win32() -> None:
    text = ROUTE.read_text(encoding="utf-8")
    assert "scripts.generated" in text
    assert 'searchParams.get("win32")' in text
    assert "text/plain" in text
    assert "/download#cli" in text
    assert "recordCliInstallEvent" in text


def test_cli_install_tracks_download_events() -> None:
    event = (ROOT / "site/front/src/lib/cli-install-event.ts").read_text(
        encoding="utf-8"
    )
    assert 'platform: "cli"' in event
    assert "download_events" in event
    assert "cli-posix" in event
    assert "cli-win32" in event
    assert "install.sh" in event
    assert "install.ps1" in event
    assert "/api/cli-install" in event
    schema = (ROOT / "site/back/supabase/schema.sql").read_text(encoding="utf-8")
    assert "'macos', 'windows', 'linux', 'cli'" in schema
    assert "filename like 'navin-cli-%'" in schema
    migration = (
        ROOT / "site/back/supabase/2026-09-04-download-cli.sql"
    ).read_text(encoding="utf-8")
    assert "download_events_platform_check" in migration
    dashboard = (
        ROOT / "site/front/src/components/admin-dashboard.tsx"
    ).read_text(encoding="utf-8")
    assert "Installations CLI" in dashboard
    assert "Téléchargements par plateforme" in dashboard
    assert "downloadPlatformRows" in dashboard
    stats_api = (
        ROOT / "site/back/src/app/api/admin/stats/route.ts"
    ).read_text(encoding="utf-8")
    assert "CLI_DOWNLOAD_OR" in stats_api
    assert "cliInstallCounts" in stats_api
    helper = (ROOT / "site/back/src/lib/cli-downloads.ts").read_text(
        encoding="utf-8"
    )
    assert "navin-cli-" in helper
    assert "install.sh" in helper
    download = (ROOT / "site/back/src/app/api/download/route.ts").read_text(
        encoding="utf-8"
    )
    assert "insertDownloadEvent" in download
    cli_api = (ROOT / "site/back/src/app/api/cli-install/route.ts").read_text(
        encoding="utf-8"
    )
    assert "cli-posix" in cli_api
    install_route = ROUTE.read_text(encoding="utf-8")
    assert "no-store" in install_route
    assert "max-age=300" not in install_route
    tabs = (
        ROOT / "site/front/src/components/platform-download-tabs.tsx"
    ).read_text(encoding="utf-8")
    assert "px-5 py-3" in tabs
    assert "h-8 w-8" in tabs


CLI_DOC_PAGES = (
    "agi",
    "overview",
    "install",
    "quickstart",
    "commands",
    "interactive",
    "slash-commands",
    "shortcuts",
    "tools",
    "settings",
    "scripting",
    "license",
    "troubleshooting",
)


def test_public_docs_catalog_lists_cli_hub() -> None:
    catalog = (ROOT / "site/front/src/lib/docs-catalog.ts").read_text(encoding="utf-8")
    back = (ROOT / "site/back/src/lib/docs-catalog.ts").read_text(encoding="utf-8")
    for page in CLI_DOC_PAGES:
        rel = f"cli/{page}.md"
        src = ROOT / "docs" / rel
        assert src.is_file(), rel
        text = src.read_text(encoding="utf-8")
        assert "\u2014" not in text, rel
        assert "\u2013" not in text, rel
        assert f'file: "{rel}"' in catalog, rel
        slug = "cli" if page == "overview" else f"cli/{page}"
        assert f'slug: "{slug}"' in catalog, rel
        assert f'file: "{rel}"' in back, rel


def test_replace_file_does_not_follow_symlink(tmp_path: Path) -> None:
    """macOS EPERM: cat > dest followed a symlink into a signed binary."""
    posix = POSIX.read_text(encoding="utf-8")
    start = posix.index("replace_file()")
    end = posix.index("\nwrite_wrapper()")
    func = posix[start:end]
    engine = tmp_path / "Navin.app" / "Contents" / "Resources" / "navin-dist" / "navin"
    engine.parent.mkdir(parents=True)
    engine.write_bytes(b"SIGNED-BINARY")
    engine.chmod(0o755)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    dest = bindir / "navin"
    dest.symlink_to(engine)
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'die() { printf "%s\\n" "$*" >&2; exit 1; }\n'
        f"{func}\n"
        f"replace_file '{dest}' <<'EOF'\n"
        "#!/bin/sh\n"
        "echo wrapper\n"
        "EOF\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_path)
    subprocess.run(["bash", str(script)], check=True, env=env)
    assert engine.read_bytes() == b"SIGNED-BINARY"
    assert dest.is_file()
    assert not dest.is_symlink()
    assert "echo wrapper" in dest.read_text(encoding="utf-8")


def test_generated_scripts_match_sources() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH")
    subprocess.run([node, str(EMBED)], check=True)
    text = GENERATED.read_text(encoding="utf-8")
    posix_lit = text.split("export const POSIX = ", 1)[1]
    posix_lit, rest = posix_lit.split(";\nexport const WINDOWS = ", 1)
    windows_lit = rest.split(";\n", 1)[0]
    assert json.loads(posix_lit) == POSIX.read_text(encoding="utf-8")
    assert json.loads(windows_lit) == WINDOWS.read_text(encoding="utf-8")


def _run_installer_functions(tmp_path: Path, script: str) -> subprocess.CompletedProcess:
    functions = POSIX.read_text(encoding="utf-8").split('\nOS="$(os_name)"', 1)[0]
    env = os.environ | {"NAVIN_PREFIX": str(tmp_path / "prefix"), "TMPDIR": str(tmp_path)}
    return subprocess.run(
        ["bash", "-c", functions + "\n" + script], env=env,
        text=True, capture_output=True, timeout=30,
    )


def _cli_archive(tmp_path: Path, name: str, *, broken: bool = False) -> Path:
    tree = tmp_path / name / "navin-dist"
    tree.mkdir(parents=True)
    engine = tree / "navin"
    engine.write_text(
        '#!/bin/sh\nif [ "$1" = python ]; then\n'
        f'  exit {1 if broken else 0}\nfi\nprintf "%s\\n" "{name}:$*"\n',
        encoding="utf-8",
    )
    engine.chmod(0o755)
    archive = tmp_path / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(tree, arcname="navin-dist")
    return archive


def test_reinstall_preserves_running_engine_and_switches_both_commands(tmp_path: Path) -> None:
    first = _cli_archive(tmp_path, "first")
    second = _cli_archive(tmp_path, "second")
    legacy = tmp_path / "prefix/share/navin/pkg/navin-dist/navin"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("previous running archive", encoding="utf-8")
    proc = _run_installer_functions(tmp_path, f'''
install_from_tarball {shlex.quote(str(first))}
"$BIN_DIR/navin" --version
install_from_tarball {shlex.quote(str(second))}
"$BIN_DIR/navin" --version
"$BIN_DIR/navin-cli" "a message with spaces"
''')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "first:--version", "second:--version", "second:tui a message with spaces",
    ]
    trees = list((tmp_path / "prefix/share/navin/installs").glob("*/navin-dist/navin"))
    assert len(trees) == 2
    assert any("first:" in engine.read_text() for engine in trees)
    assert legacy.read_text() == "previous running archive"


@pytest.mark.parametrize("bad_payload", ["engine", "archive"])
def test_failed_reinstall_keeps_previous_commands(tmp_path: Path, bad_payload: str) -> None:
    first = _cli_archive(tmp_path, "first")
    proc = _run_installer_functions(tmp_path, f"install_from_tarball {shlex.quote(str(first))}")
    assert proc.returncode == 0, proc.stderr
    bindir = tmp_path / "prefix/bin"
    before = {name: (bindir / name).read_bytes() for name in ("navin", "navin-cli")}
    bad = _cli_archive(tmp_path, "broken", broken=True)
    if bad_payload == "archive":
        bad.write_bytes(b"truncated download")
    proc = _run_installer_functions(tmp_path, f"install_from_tarball {shlex.quote(str(bad))}")
    assert proc.returncode != 0
    assert {name: (bindir / name).read_bytes() for name in before} == before
    assert len(list((tmp_path / "prefix/share/navin/installs").iterdir())) == 1
    assert subprocess.check_output([str(bindir / "navin"), "--version"], text=True) == "first:--version\n"


@pytest.mark.parametrize("launcher", ["root", "sudo", "pkexec", "missing", "failure"])
def test_pacman_install_and_launcher_fallbacks(tmp_path: Path, launcher: str) -> None:
    engine = tmp_path / "system/usr/lib/Navin/navin-dist/navin"
    engine.parent.mkdir(parents=True)
    engine.write_text('#!/bin/sh\nexit 0\n', encoding="utf-8")
    engine.chmod(0o755)
    legacy = tmp_path / "prefix/share/navin/pkg/active-archive"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("running", encoding="utf-8")
    script = f'''
id() {{ echo {0 if launcher == "root" else 1000}; }}
command() {{
  case "$*" in
    '-v pacman') return 0 ;;
    '-v sudo') [ {shlex.quote(launcher)} = sudo ] || [ {shlex.quote(launcher)} = failure ] ;;
    '-v pkexec') [ {shlex.quote(launcher)} = pkexec ] ;;
    *) builtin command "$@" ;;
  esac
}}
pacman() {{
  if [ "$1" = -Qlq ]; then
    printf '%s\\n' {shlex.quote(str(engine))}
  else
    printf 'pacman:%s\\n' "$*"
    [ {shlex.quote(launcher)} != failure ]
  fi
}}
sudo() {{ printf 'sudo\\n'; "$@"; }}
pkexec() {{ printf 'pkexec\\n'; "$@"; }}
install_from_pacman '/tmp/official package.pkg.tar.zst'
'''
    proc = _run_installer_functions(tmp_path, script)
    assert legacy.read_text() == "running"
    bindir = tmp_path / "prefix/bin"
    if launcher in {"missing", "failure"}:
        assert proc.returncode != 0
        assert not (bindir / "navin").exists()
    else:
        assert proc.returncode == 0, proc.stderr
        assert "pacman:-U --noconfirm --needed /tmp/official package.pkg.tar.zst" in proc.stdout
        for name in ("navin", "navin-cli"):
            assert str(engine) in (bindir / name).read_text()
        if launcher != "root":
            assert proc.stdout.startswith(f"{launcher}\n")


@pytest.mark.parametrize(
    ("family", "pacman", "expected"),
    [("arch", True, "navin-2.0.7-1-x86_64.pkg.tar.zst"),
     ("other", True, "navin-2.0.7-1-x86_64.pkg.tar.zst"),
     ("debian", False, "navin_2.0.7_amd64.deb"),
     ("other", False, "navin-cli-2.0.7-linux-x64.tar.gz")],
)
def test_linux_package_selection(tmp_path: Path, family: str, pacman: bool, expected: str) -> None:
    manifest = tmp_path / "releases.json"
    manifest.write_text(json.dumps([
        "navin-2.0.7-1-x86_64.pkg.tar.zst", "navin_2.0.7_amd64.deb",
        "navin-cli-2.0.7-linux-x64.tar.gz",
    ]), encoding="utf-8")
    proc = _run_installer_functions(tmp_path, f'''
linux_family() {{ printf '%s\\n' {shlex.quote(family)}; }}
can_extract() {{ return 0; }}
command() {{
  if [ "$*" = '-v pacman' ]; then return {0 if pacman else 1}; fi
  builtin command "$@"
}}
pick_linux_file 2.0.7 {shlex.quote(str(manifest))}
''')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == expected


def test_full_piped_install_checks_download_and_launches_both_commands(tmp_path: Path) -> None:
    depot = tmp_path / "depot"
    release = depot / "v2.0.7"
    release.mkdir(parents=True)
    name = "navin-cli-2.0.7-linux-x64.tar.gz"
    archive = _cli_archive(tmp_path, "official")
    shutil.copyfile(archive, release / name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release / "SHA256SUMS.txt").write_text(f"{digest}  {name}\n", encoding="utf-8")
    (depot / "releases.json").write_text(json.dumps([
        {"version": "2.0.7", "files": [name]},
    ]), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ | {
        "HOME": str(home), "NAVIN_PREFIX": str(home / ".local"),
        "NAVIN_DOWNLOAD_BASE": depot.as_uri(), "TMPDIR": str(tmp_path),
        "SHELL": "/bin/bash",
    }
    proc = subprocess.run(
        ["bash"], input=POSIX.read_text(), env=env,
        text=True, capture_output=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "checking the installed engine" in proc.stderr
    assert "installed:" in proc.stderr
    for command, args, expected in (
        ("navin", ["--version"], "official:--version\n"),
        ("navin-cli", ["--help"], "official:tui --help\n"),
    ):
        assert subprocess.check_output(
            [str(home / ".local/bin" / command), *args], env=env, text=True,
        ) == expected
