"""CLI one-liner install scripts shipped at /install on navin.live."""

from __future__ import annotations

import json
import shutil
import subprocess
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
    # navin-cli wrapper: the terminal UI in the current folder.
    assert '${BIN_DIR}/navin-cli' in text
    assert 'tui "\\$@"' in text
    assert "navin-cli " in text
    assert "navin agent" not in text
    assert "cd your-project && navin-cli" in text
    assert "https://navin.live/en/docs/cli" in text
    assert "_print_ready" in text


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
    schema = (ROOT / "site/back/supabase/schema.sql").read_text(encoding="utf-8")
    assert "'macos', 'windows', 'linux', 'cli'" in schema
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
    assert "cliInstallCounts" in stats_api
    assert 'eq("platform", "cli")' in stats_api
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
