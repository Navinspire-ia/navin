# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

import subprocess
import time
from pathlib import Path

import pytest

from navin.agent.integration_check import (
    build_frontend_gap_nudge,
    build_integration_nudge,
    frontend_gap,
    frontend_units,
    unwired_frontend_files,
)
from navin.agent.request_checklist import (
    MAX_ASKS,
    explicit_asks,
    request_checklist_context_provider,
    ui_terms,
)


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _vite_app(root: Path) -> None:
    _write(root, "web/package.json", '{"dependencies": {"react": "18", "react-router-dom": "6"}}')
    _write(root, "web/src/main.tsx", "import App from './App'\n")
    _write(root, "web/src/App.tsx", "import Home from './pages/Home'\n")
    _write(root, "web/src/pages/Home.tsx", "export default function Home() {}\n")


def test_dashboard_screen_created_but_never_routed_is_reported(tmp_path: Path) -> None:
    _vite_app(tmp_path)
    _write(tmp_path, "web/src/api/dashboard.ts", "export const getStats = 1\n")
    _write(tmp_path, "web/src/hooks/useStats.ts", "import { getStats } from '../api/dashboard'\n")
    _write(tmp_path, "web/src/pages/Dashboard.tsx", "import Chart from '../components/Chart'\n")
    _write(tmp_path, "web/src/components/Chart.tsx", "export default 1\n")
    _write(tmp_path, "web/src/pages/Dashboard.test.tsx", "import Dashboard from './Dashboard'\n")
    changed = [
        "web/src/api/dashboard.ts",
        "web/src/hooks/useStats.ts",
        "web/src/pages/Dashboard.tsx",
        "web/src/components/Chart.tsx",
        "web/src/pages/Dashboard.test.tsx",
    ]

    # The lowercase API module and the test import must not pass for the screen;
    # the chart is reached through the screen, so only the screen is named.
    assert unwired_frontend_files(tmp_path, changed) == ["web/src/pages/Dashboard.tsx"]


@pytest.mark.parametrize(
    "importer",
    [
        "import Dashboard from './pages/Dashboard'\n",
        "const Dashboard = lazy(() => import('./pages/Dashboard.tsx'))\n",
        "import Dashboard from '@/pages/Dashboard'\n",
        "export { default as Dashboard } from './pages/Dashboard'\n",
    ],
)
def test_screen_wired_by_any_import_form_is_not_reported(tmp_path: Path, importer: str) -> None:
    _vite_app(tmp_path)
    _write(tmp_path, "web/src/pages/Dashboard.tsx", "export default 1\n")
    _write(tmp_path, "web/src/App.tsx", "import Home from './pages/Home'\n" + importer)

    assert unwired_frontend_files(tmp_path, [str(tmp_path / "web/src/pages/Dashboard.tsx")]) == []


def test_folder_index_and_vue_screens(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"dependencies": {"vue": "3"}}')
    _write(tmp_path, "src/router.ts", "component: () => import('@/views/Reports.vue')\n")
    _write(tmp_path, "src/views/Reports.vue", "<template />\n")
    _write(tmp_path, "src/views/Billing/index.vue", "<template />\n")
    _write(tmp_path, "src/views/Stock/index.vue", "<template />\n")
    _write(tmp_path, "src/App.vue", "import Stock from './views/Stock'\n")
    changed = ["src/views/Reports.vue", "src/views/Billing/index.vue", "src/views/Stock/index.vue"]

    assert unwired_frontend_files(tmp_path, changed) == ["src/views/Billing/index.vue"]


def test_file_routed_and_auto_imported_frameworks_are_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "site/package.json", '{"dependencies": {"next": "15"}}')
    _write(tmp_path, "site/app/dashboard/page.tsx", "export default 1\n")
    _write(tmp_path, "site/app/dashboard/StatsCard.tsx", "export default 1\n")
    _write(tmp_path, "shop/package.json", '{"devDependencies": {"nuxt": "3"}}')
    _write(tmp_path, "shop/components/Cart.vue", "<template />\n")
    changed = [
        "site/app/dashboard/page.tsx",
        "site/app/dashboard/StatsCard.tsx",
        "shop/components/Cart.vue",
        "site/src/main.tsx",
    ]

    assert unwired_frontend_files(tmp_path, changed) == []


@pytest.mark.parametrize("workspace", ["", "web"], ids=["repo-root", "repo-subfolder"])
def test_only_files_new_in_this_turn_are_checked(tmp_path: Path, workspace: str) -> None:
    if subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("git is not installed")
    _vite_app(tmp_path)
    _write(tmp_path, "web/src/pages/Legacy.tsx", "export default 1\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([*git, "add", "."], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], cwd=tmp_path, check=True)
    time.sleep(1.1)  # commit dates have one-second resolution
    turn_start = time.time()
    _write(tmp_path, "web/src/pages/Dashboard.tsx", "export default 1\n")
    # A project opened on a sub-folder of the repository gets paths relative
    # to that folder from git too.
    root = tmp_path / workspace if workspace else tmp_path
    prefix = "" if workspace else "web/"
    changed = [f"{prefix}src/pages/Legacy.tsx", f"{prefix}src/pages/Dashboard.tsx"]
    expected = [f"{prefix}src/pages/Dashboard.tsx"]

    # Pre-existing dead code edited in passing is not this turn's integration gap.
    assert unwired_frontend_files(root, changed, since=turn_start) == expected

    # A task committed mid-turn is still checked against the turn start.
    subprocess.run([*git, "add", "."], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "task"], cwd=tmp_path, check=True)
    assert unwired_frontend_files(root, changed, since=turn_start) == expected


def test_slow_scan_reports_nothing_rather_than_a_guess(tmp_path: Path) -> None:
    _vite_app(tmp_path)
    _write(tmp_path, "web/src/pages/Dashboard.tsx", "export default 1\n")

    assert unwired_frontend_files(tmp_path, ["web/src/pages/Dashboard.tsx"], budget_s=-1) == []


def test_nudge_names_each_file_and_the_wiring_to_do() -> None:
    message = build_integration_nudge(["web/src/pages/Dashboard.tsx"])

    assert "- web/src/pages/Dashboard.tsx" in message
    assert "router" in message and "menu" in message
    assert "\u2014" not in message and "\u2013" not in message


# Full-stack coverage: the asks of a request and the frontend side of a UI ask.


def test_numbered_bulleted_and_semicolon_requests_list_each_ask() -> None:
    numbered = (
        "/forge voici le travail:\n"
        "1. ajoute un dashboard sur la facturation\n"
        "2) corrige le bug du login\n"
        "3. mets a jour la doc\n"
    )
    assert explicit_asks(numbered) == (
        "ajoute un dashboard sur la facturation",
        "corrige le bug du login",
        "mets a jour la doc",
    )
    bullets = "- [ ] add the export button\n- [x] fix the date filter\n* translate the labels"
    assert explicit_asks(bullets) == (
        "add the export button", "fix the date filter", "translate the labels",
    )
    clauses = "ajoute l'export CSV des factures; corrige le filtre par date; traduis en arabe"
    assert len(explicit_asks(clauses)) == 3
    many = "\n".join(f"{n}. task number {n}" for n in range(1, MAX_ASKS + 4))
    assert len(explicit_asks(many)) == MAX_ASKS


def test_one_ask_or_pasted_code_is_not_a_list() -> None:
    assert explicit_asks("ajoute un dashboard sur le module achats") == ()
    assert explicit_asks("const a = 1; const b = 2; return a + b;") == ()
    assert explicit_asks("1. seul\n2. ok") == ()


def test_prose_chaining_actions_lists_each_ask_but_a_plain_and_does_not() -> None:
    chained = (
        "/forge ajoute l'export CSV des factures, puis corrige le filtre par date, "
        "ensuite traduis les libelles en arabe"
    )
    assert explicit_asks(chained) == (
        "ajoute l'export CSV des factures",
        "corrige le filtre par date",
        "traduis les libelles en arabe",
    )
    english = "add the stats endpoint and then build the stats screen. Also add the menu entry"
    assert explicit_asks(english) == (
        "add the stats endpoint", "build the stats screen", "add the menu entry",
    )
    assert explicit_asks("ajoute un bouton export et un filtre par date") == ()
    assert explicit_asks("if the cache is empty then reload it from the API") == ()
    assert explicit_asks("it should also log the request id") == ()
    assert explicit_asks("je veux un agent qui comprend puis agit") == ()
    assert explicit_asks("il faut le faire depuis le module achats") == ()


def test_ui_terms_find_screens_buttons_and_front_but_not_backend_words() -> None:
    request = (
        "si ca necessite front et back il oublie pas les ecran, bouton et tout "
        "composant a rajouter"
    )
    assert ui_terms(request) == ("front", "ecran", "bouton", "composant")
    assert ui_terms("Ajoute un tableau de bord et un \u00e9cran de suivi") == (
        "tableau de bord", "\u00e9cran",
    )
    assert ui_terms("add a settings screen with a save button") == ("screen", "button")
    assert ui_terms("\u0623\u0636\u0641 \u0632\u0631 \u0627\u0644\u062d\u0641\u0638") == (
        "\u0632\u0631",
    )
    assert ui_terms("deploy the helm chart to the cluster") == ()
    assert ui_terms("add a typed interface for the API response; oui merci") == ()
    assert ui_terms("fix the format of the invoice table") == ()


def _request(text: str, workspace: Path) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(original_user_text=text, workspace=workspace)


def test_checklist_lists_the_asks_and_the_full_stack_chain(tmp_path: Path) -> None:
    import asyncio

    _vite_app(tmp_path)
    text = "1. ajoute un dashboard des ventes\n2. ajoute un bouton export\n3. corrige le filtre"
    block = asyncio.run(request_checklist_context_provider(_request(text, tmp_path)))

    assert block is not None and block.source == "request_checklist"
    assert "The request holds 3 separate asks" in block.content
    assert "  1. ajoute un dashboard des ventes" in block.content
    assert "  3. corrige le filtre" in block.content
    assert "user-facing part (dashboard, bouton)" in block.content
    assert "its route and its menu or navigation entry" in block.content
    assert "file them first as a board plan, one step per ask" in block.content
    assert "claim_next=true" in block.content


def test_checklist_skips_the_chain_without_frontend_and_plain_questions(tmp_path: Path) -> None:
    import asyncio

    _write(tmp_path, "pyproject.toml", "[project]\nname = 'svc'\n")
    backend_only = asyncio.run(
        request_checklist_context_provider(_request("ajoute un bouton; corrige le cron", tmp_path))
    )
    assert backend_only is not None
    assert "2 separate asks" in backend_only.content
    assert "user-facing part" not in backend_only.content
    assert "board plan" not in backend_only.content
    question = asyncio.run(
        request_checklist_context_provider(_request("pourquoi le build echoue ?", tmp_path))
    )
    assert question is None


def test_frontend_units_finds_ui_packages_and_ignores_node_services(tmp_path: Path) -> None:
    _vite_app(tmp_path)
    _write(tmp_path, "server/package.json", '{"dependencies": {"express": "4"}}')
    _write(tmp_path, "node_modules/react/package.json", '{"dependencies": {"react": "18"}}')
    _write(tmp_path, "apps/admin/package.json", '{"devDependencies": {"vue": "3"}}')

    assert frontend_units(tmp_path) == ("web", "apps/admin")


def test_frontend_gap_names_backend_files_only_when_no_frontend_changed(tmp_path: Path) -> None:
    _vite_app(tmp_path)
    _write(tmp_path, "server/package.json", '{"dependencies": {"express": "4"}}')
    backend = ["api/billing/routes.py", "api/billing/service.py", "db/0012_billing.sql"]

    assert frontend_gap(tmp_path, backend + ["README.md", "tests/test_billing.py"]) == backend
    assert frontend_gap(tmp_path, ["server/src/routes/stats.ts"]) == ["server/src/routes/stats.ts"]
    # Any frontend source counts, the client call included.
    assert frontend_gap(tmp_path, backend + ["web/src/api/billing.ts"]) == []
    assert frontend_gap(tmp_path, backend + ["web/src/pages/Billing.tsx"]) == []
    assert frontend_gap(tmp_path, backend + ["web/src/styles/billing.css"]) == []
    assert frontend_gap(tmp_path, ["web/src/App.test.tsx", "docs/billing.md"]) == []


def test_frontend_gap_reads_next_api_routes_as_backend(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"dependencies": {"next": "14", "react": "18"}}')

    assert frontend_gap(tmp_path, ["app/api/stats/route.ts"]) == ["app/api/stats/route.ts"]
    assert frontend_gap(tmp_path, ["app/api/stats/route.ts", "app/stats/page.tsx"]) == []


def test_frontend_gap_is_silent_without_a_frontend_unit(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'svc'\n")

    assert frontend_gap(tmp_path, ["svc/api.py"]) == []


def test_frontend_gap_nudge_names_terms_files_and_the_chain() -> None:
    text = build_frontend_gap_nudge(("dashboard", "bouton"), ["api/stats.py"])

    assert "user-facing part (dashboard, bouton)" in text
    assert "- api/stats.py" in text
    for piece in ("client call", "menu or navigation entry", "every button", "i18n"):
        assert piece in text


def test_missing_asks_names_only_the_asks_the_answer_never_mentions() -> None:
    from navin.agent.request_checklist import build_coverage_nudge, missing_asks

    asks = (
        "ajoute l'export CSV des factures",
        "corrige le filtre par date",
        "traduis les libelles en arabe",
    )
    answer = "J'ai ajout\u00e9 l'export des factures et corrig\u00e9 le filtre par date."
    assert missing_asks(asks, answer) == ("traduis les libelles en arabe",)
    assert missing_asks(asks, answer + " Les libell\u00e9s sont traduits en arabe.") == ()
    assert missing_asks(("add it", "fix that"), "Done.") == ()

    message = build_coverage_nudge(("traduis les libelles en arabe",))
    assert "1. traduis les libelles en arabe" in message
    assert "\u2014" not in message and "\u2013" not in message
