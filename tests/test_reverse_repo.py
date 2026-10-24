"""Reverse of a source repository: inventory, ordered plan, board batches, parity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navin.board.store import ProjectBoardStore
from navin.reverse import repo


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def odoo_like(tmp_path: Path) -> Path:
    src = tmp_path / "odoo"
    _write(src, "LICENSE", "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n")
    _write(src, "odoo-bin", "#!/usr/bin/env python3\n")
    _write(src, "addons/base/__manifest__.py", "{'name': 'Base', 'depends': [], 'license': 'LGPL-3'}")
    _write(src, "addons/base/models/res_partner.py", "class P: pass\n")
    _write(src, "addons/account/__manifest__.py", "# header\n{'name': 'Invoicing', 'depends': ['base', 'mail']}")
    for i in range(5):
        _write(src, f"addons/account/models/m{i}.py")
    _write(src, "addons/account/tests/test_move.py")
    _write(src, "addons/sale/__manifest__.py", "{'name': 'Sales', 'depends': ['account']}")
    _write(src, "addons/sale/views/sale.xml", "<odoo/>\n")
    _write(src, "addons/base/node_modules/skip.js")
    return src


def test_a_vendored_copy_does_not_shadow_the_real_addon(odoo_like: Path) -> None:
    _write(odoo_like, "addons/sale/tools/vendored/addons/base/__manifest__.py", "{'name': 'Copy', 'depends': []}")
    comps = {c["id"]: c for c in repo.build_repo_inventory(odoo_like)["components"]}
    assert comps["base"]["path"] == "addons/base"
    assert comps["base-4"]["path"] == "addons/sale/tools/vendored/addons/base"
    assert comps["account"]["internal_depends"] == ["base"]


def test_targets_accept_github_urls_slugs_and_folders(tmp_path: Path) -> None:
    spec = repo.parse_target("https://github.com/odoo/odoo/tree/17.0")
    assert (spec["owner"], spec["name"], spec["ref"]) == ("odoo", "odoo", "17.0")
    assert spec["url"] == "https://github.com/odoo/odoo.git"
    assert repo.parse_target("supabase/supabase")["url"] == "https://github.com/supabase/supabase.git"
    assert repo.parse_target("git@gitlab.com:grp/proj.git")["url"] == "https://gitlab.com/grp/proj.git"
    assert repo.parse_target("supabase/auth@v2.150.0")["ref"] == "v2.150.0"
    assert repo.parse_target(str(tmp_path))["kind"] == "local"
    with pytest.raises(ValueError):
        repo.parse_target("not a repo")


def test_inventory_finds_odoo_addons_and_their_dependencies(odoo_like: Path) -> None:
    inv = repo.build_repo_inventory(odoo_like, name="odoo")
    comps = {c["id"]: c for c in inv["components"]}
    assert set(comps) == {"root", "base", "account", "sale"}
    assert comps["root"]["name"] == "odoo"
    assert comps["account"]["internal_depends"] == ["base"]
    assert comps["account"]["external_depends"] == ["mail"]
    assert comps["sale"]["internal_depends"] == ["account"]
    assert comps["account"]["counts"]["tests"] == 1
    assert comps["root"]["files"] == ["LICENSE", "odoo-bin"]
    assert inv["license"]["spdx"] == "LGPL-3.0" and inv["license"]["copyleft"]
    assert not any("node_modules" in f for c in inv["components"] for f in c["files"])


def test_inventory_links_monorepo_packages(tmp_path: Path) -> None:
    src = tmp_path / "supa"
    _write(src, "LICENSE", "Apache License\nVersion 2.0\n")
    _write(src, "package.json", json.dumps({"name": "supabase", "workspaces": ["apps/*", "packages/*"],
                                            "devDependencies": {"ui": "*"}}))
    _write(src, "packages/ui/package.json", json.dumps({"name": "ui", "dependencies": {"react": "18"}}))
    _write(src, "packages/ui/src/Button.tsx")
    _write(src, "apps/studio/package.json", json.dumps({"name": "studio", "dependencies": {"ui": "*", "next": "14"}}))
    _write(src, "apps/studio/pages/index.tsx")
    _write(src, "docker/docker-compose.yml", "services:\n  db:\n    image: pg\n  auth:\n    image: gotrue\n")
    _write(src, "supabase/migrations/001_init.sql", "create table t();\n")
    inv = repo.build_repo_inventory(src)
    comps = {c["id"]: c for c in inv["components"]}
    assert comps["studio"]["internal_depends"] == ["ui"]
    assert comps["studio"]["external_depends"] == ["next"]
    assert inv["compose_services"] == ["auth", "db"]
    assert inv["license"]["spdx"] == "Apache-2.0"
    assert inv["counts"]["migrations"] == 1
    plan = repo.build_plan(inv, source_root=str(src))
    assert plan["waves"] == [["root"], ["ui"], ["studio"]]  # the root never waits for its own parts
    assert plan["cycles"] == []  # and that is not a cycle


def test_a_real_dependency_cycle_is_reported_and_still_planned(odoo_like: Path) -> None:
    _write(odoo_like, "addons/ping/__manifest__.py", "{'name': 'Ping', 'depends': ['pong']}")
    _write(odoo_like, "addons/pong/__manifest__.py", "{'name': 'Pong', 'depends': ['ping']}")
    plan = repo.build_plan(repo.build_repo_inventory(odoo_like), source_root=str(odoo_like))
    assert plan["cycles"] == ["ping", "pong"]
    assert {"ping", "pong"} <= {s["component"] for s in plan["steps"]}


def test_plan_orders_dependencies_first_and_splits_big_components(odoo_like: Path) -> None:
    inv = repo.build_repo_inventory(odoo_like)
    plan = repo.build_plan(inv, source_root=str(odoo_like), max_files=3)
    assert plan["waves"] == [["root"], ["base"], ["account"], ["sale"]]
    by_comp: dict[str, list[dict]] = {}
    for step in plan["steps"]:
        by_comp.setdefault(step["component"], []).append(step)
    account = by_comp["account"]
    assert len(account) > 2 and account[-1]["title"].startswith("Integrate and test")
    assert account[-1]["depends_on"] == [s["id"] for s in account[:-1]]
    covered = {f for s in account for f in s["files"]}
    assert covered == set(next(c for c in inv["components"] if c["id"] == "account")["files"])
    sale = by_comp["sale"][0]
    assert account[-1]["id"] in sale["depends_on"]
    assert plan["cycles"] == []


def _setup_plan(tmp_path: Path, src: Path, max_files: int = 3) -> Path:
    project = tmp_path / "rebuild"
    project.mkdir()
    inv = repo.build_repo_inventory(src)
    inv["source_root"] = str(src)
    folder = repo.plan_dir(project, "odoo")
    repo._save(folder / "inventory.json", inv)
    repo._save(folder / "rebuild-plan.json", repo.build_plan(inv, source_root=str(src), max_files=max_files))
    return project


def test_next_batch_files_ready_steps_in_bounded_batches(tmp_path: Path, odoo_like: Path) -> None:
    project = _setup_plan(tmp_path, odoo_like)
    total = len(json.loads((repo.plan_dir(project, "odoo") / "rebuild-plan.json").read_text())["steps"])
    first = repo.next_batch(project, "odoo", size=2)
    assert len(first["filed"]) == 2
    again = repo.next_batch(project, "odoo", size=2)
    assert again["filed"] == [] and "still open" in again["message"]
    store = ProjectBoardStore(project)
    seen: set[str] = set()
    for _ in range(total + 2):
        for task in store.read_tasks():
            if task["status"] != "done":
                store.update_task(task["id"], actor="t", actor_type="human",
                                  fields={"status": "done", "evidence": "ok", "validation": "none"})
        batch = repo.next_batch(project, "odoo", size=2, board_cap=4)
        for item in batch["filed"]:
            assert item["step"] not in seen
            seen.add(item["step"])
        assert len(store.read_tasks()) <= 4
        if not batch["filed"] and batch["state"]["done"] == total:
            break
    assert len(seen) + 2 == total
    assert "parity" in batch["message"]
    assert repo.runtime_lines(project)  # unfinished until parity says so


def test_parity_lists_gaps_until_the_rebuild_is_complete(tmp_path: Path, odoo_like: Path) -> None:
    project = _setup_plan(tmp_path, odoo_like)
    first = repo.parity(project, "odoo")
    assert not first["finished"] and first["percent"] == 0.0
    inv = json.loads((repo.plan_dir(project, "odoo") / "inventory.json").read_text())
    for comp in inv["components"]:
        for rel in comp["files"]:
            if "tests/" not in rel:
                _write(project, rel)
    partial = repo.parity(project, "odoo")
    assert [g["component"] for g in partial["gaps"]] == ["account"]
    assert partial["gaps"][0]["tests_ported"] is False
    _write(project, "addons/account/tests/test_move.py")
    done = repo.parity(project, "odoo")
    assert done["finished"] and done["percent"] == 100.0
    repo._write_state(project, "odoo", total=5)
    assert repo.runtime_lines(project) == []


def test_board_digest_shows_a_rebuild_only_to_the_chat_driving_it(tmp_path: Path, odoo_like: Path,
                                                                  monkeypatch) -> None:
    from navin.board import context, session_focus

    project = _setup_plan(tmp_path, odoo_like)
    filed = repo.next_batch(project, "odoo", size=1)["filed"]
    assert any("next-batch odoo" in line for line in context.board_digest(project))
    monkeypatch.setattr(session_focus, "touched", lambda key: [filed[0]["task_id"]] if key == "mine" else [])
    assert any("Reverse rebuild odoo" in line for line in context.board_digest(project, "mine"))
    assert not any("Reverse rebuild" in line for line in context.board_digest(project, "other"))


def test_cli_inventory_and_plan_on_a_local_folder(tmp_path: Path, odoo_like: Path, capsys) -> None:
    project = tmp_path / "p"
    project.mkdir()
    assert repo.main(["inventory", str(odoo_like), "--id", "odoo", "--project", str(project)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["components"] == 4
    assert repo.main(["plan", "odoo", "--project", str(project)]) == 0
    assert (repo.plan_dir(project, "odoo") / "rebuild-plan.json").exists()


def _product_repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    auth, ui, studio = tmp_path / "auth", tmp_path / "ui", tmp_path / "studio"
    _write(auth, "LICENSE", "MIT License\nPermission is hereby granted, free of charge\n")
    _write(auth, "go.mod", "module github.com/supabase/auth\n\ngo 1.22\n")
    _write(auth, "internal/api/api.go", "package api\n")
    _write(auth, "migrations/001_users.up.sql", "create table users();\n")
    _write(ui, "LICENSE", "Apache License\nVersion 2.0\n")
    _write(ui, "package.json", json.dumps({"name": "@supabase/ui", "dependencies": {"react": "18"}}))
    _write(ui, "src/Button.tsx")
    _write(studio, "LICENSE", "Apache License\nVersion 2.0\n")
    _write(studio, "package.json", json.dumps({"name": "studio", "dependencies": {"@supabase/ui": "*"}}))
    _write(studio, "pages/index.tsx")
    return auth, ui, studio


def test_several_repos_make_one_product_with_cross_repo_order(tmp_path: Path, capsys) -> None:
    auth, ui, studio = _product_repos(tmp_path)
    project = tmp_path / "rebuild"
    project.mkdir()
    code = repo.main(["inventory", str(studio), str(ui), str(auth), "--id", "supa", "--project", str(project)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["repos"] == 3
    assert [s["prefix"] for s in out["sources"]] == ["studio", "ui", "auth"]
    assert out["license"]["spdx"] == "mixed" and set(out["license"]["per_repo"]) == {"studio", "ui", "auth"}
    inv = json.loads((repo.plan_dir(project, "supa") / "inventory.json").read_text())
    comps = {c["id"]: c for c in inv["components"]}
    assert comps["studio-root"]["internal_depends"] == ["ui-root"]
    assert comps["studio-root"]["external_depends"] == []
    assert "auth/internal/api/api.go" in comps["auth-root"]["files"]
    assert comps["auth-root"]["source_root"] == str(auth)

    assert repo.main(["plan", "supa", "--project", str(project)]) == 0
    plan = json.loads((repo.plan_dir(project, "supa") / "rebuild-plan.json").read_text())
    wave_of = {cid: n for n, wave in enumerate(plan["waves"]) for cid in wave}
    assert wave_of["ui-root"] < wave_of["studio-root"]

    batch = repo.next_batch(project, "supa", size=10)
    tasks = {t["id"]: t for t in ProjectBoardStore(project).read_tasks()}
    task_of = {item["title"]: item["task_id"] for item in batch["filed"]}
    studio_task = tasks[next(v for k, v in task_of.items() if k.startswith("Rebuild studio"))]
    ui_task = next(v for k, v in task_of.items() if k.startswith("Rebuild @supabase/ui"))
    assert ui_task in studio_task["depends_on"]  # the board keeps studio waiting for ui
    task = next(t for t in tasks.values() if "(auth)" in t["title"])
    assert f"rebuild path auth/X comes from {auth}/X" in task["description"]

    for comp in inv["components"]:
        for rel in comp["files"]:
            _write(project, rel)
    assert repo.parity(project, "supa")["finished"]
