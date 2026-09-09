# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Workspace checkpoints on a WSL project opened from Windows.

The shadow repo used to be pinned to the Windows home while the work-tree sat
inside the distribution, so git was asked to straddle the boundary and the
whole feature was dead on the desktop app's most common Windows setup. The
repo now follows the project into the distro.

Nothing here needs a Windows host. A throwaway directory stands in for the
distribution's filesystem and a small shim stands in for ``wsl.exe``: it
rewrites the POSIX paths it is handed into that directory and runs the real
git there, which is exactly the translation the production path performs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from navin.utils import wsl
from navin.webui import checkpoints_api
from navin.webui.checkpoints_api import (
    CheckpointError,
    _digest,
    create_checkpoint,
    diff_checkpoint,
    list_checkpoints,
    restore_checkpoint,
    shadow_repo,
)

UNC = r"\\wsl.localhost\Ubuntu\home\me\proj"
POSIX_ROOT = "/home/me/proj"
DISTRO_HOME = "/home/me"

# Stands in for wsl.exe: everything after "--" is the command, and every
# argument that looks like a distribution path is re-rooted into the fake
# filesystem. Only path arguments start with "/" in the commands under test.
_SHIM = '''\
import os
import subprocess
import sys

root = os.environ["FAKE_DISTRO_ROOT"]


def host(value):
    return root + value if value.startswith("/") else value


args = sys.argv[1:]
cwd = None
index = 0
while index < len(args):
    if args[index] == "-d":
        index += 2
    elif args[index] == "--cd":
        cwd = host(args[index + 1])
        index += 2
    elif args[index] == "--":
        index += 1
        break
    else:
        index += 1
sys.exit(subprocess.run([host(a) for a in args[index:]], cwd=cwd).returncode)
'''


class FakeDistro:
    def __init__(self, root: Path) -> None:
        self.root = root

    def host(self, posix_path: str) -> Path:
        return self.root / posix_path.lstrip("/")

    @property
    def project(self) -> Path:
        return self.host(POSIX_ROOT)


@pytest.fixture()
def distro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDistro:
    fake = FakeDistro(tmp_path / "distro")
    fake.project.mkdir(parents=True)
    (fake.project / "app.py").write_text("print('v1')\n", encoding="utf-8")
    (fake.project / "lib").mkdir()
    (fake.project / "lib" / "util.py").write_text("X = 1\n", encoding="utf-8")

    shim = tmp_path / "fake-wsl.py"
    shim.write_text(_SHIM, encoding="utf-8")

    # The Windows-side home, where a pre-migration shadow repo would sit.
    monkeypatch.setenv("NAVIN_CHECKPOINTS_HOME", str(tmp_path / "windows-home"))
    monkeypatch.setenv("FAKE_DISTRO_ROOT", str(fake.root))
    monkeypatch.setattr(wsl, "wsl_executable", lambda: sys.executable)
    monkeypatch.setattr(wsl, "resolve_distro", lambda name: name)
    monkeypatch.setattr(wsl, "distro_home", lambda _distro, **_kw: DISTRO_HOME)
    monkeypatch.setattr(
        wsl,
        "command_prefix",
        lambda distro, cwd=None: [
            sys.executable,
            str(shim),
            "-d",
            distro,
            "--cd",
            str(cwd),
            "--",
        ],
    )
    monkeypatch.setattr(checkpoints_api, "_host_path", lambda _distro, path: fake.host(path))
    return fake


class TestShadowRepoPlacement:
    def test_shadow_repo_lives_inside_the_distribution(self, distro: FakeDistro) -> None:
        repo = shadow_repo(UNC, platform="win32")
        assert repo.distro == "Ubuntu"
        assert repo.git_dir == f"{DISTRO_HOME}/.navin/checkpoints/{_digest(POSIX_ROOT)}"
        assert repo.work_tree == POSIX_ROOT
        # Both halves are named in the distribution's own terms: git never
        # sees a Windows path, so it never crosses /mnt on the hot path.
        assert not repo.git_dir.startswith("/mnt/")
        assert repo.host_git_dir == distro.host(repo.git_dir)

    def test_both_sides_of_the_boundary_share_one_history(
        self, distro: FakeDistro, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Opened from Windows through the redirector...
        from_windows = shadow_repo(UNC, platform="win32")
        # ...and opened by a gateway running inside the distribution.
        monkeypatch.setattr(
            checkpoints_api,
            "checkpoints_home",
            lambda: Path(DISTRO_HOME) / ".navin" / "checkpoints",
        )
        from_linux = shadow_repo(POSIX_ROOT, platform="linux")
        assert from_linux.distro is None
        assert from_linux.git_dir == from_windows.git_dir

    def test_local_project_keeps_the_host_layout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NAVIN_CHECKPOINTS_HOME", str(tmp_path / "home"))
        root = tmp_path / "plain"
        root.mkdir()
        repo = shadow_repo(root, platform="linux")
        assert repo.distro is None
        assert repo.host_git_dir == repo.host_git_dir.parent / _digest(str(root.resolve()))
        assert repo.work_tree == str(root.resolve())


class TestLifecycleOverTheBoundary:
    def test_create_list_diff_and_restore(self, distro: FakeDistro) -> None:
        first = create_checkpoint(UNC, label="initial", platform="win32")
        assert first["created"] is True

        unchanged = create_checkpoint(UNC, platform="win32")
        assert unchanged["created"] is False
        assert unchanged["id"] == first["id"]

        (distro.project / "app.py").write_text("print('v2')\n", encoding="utf-8")
        (distro.project / "extra.txt").write_text("new file\n", encoding="utf-8")
        (distro.project / "lib" / "util.py").unlink()

        diff = diff_checkpoint(UNC, first["id"], platform="win32")
        assert {f["path"]: f["status"] for f in diff["files"]} == {
            "app.py": "M",
            "extra.txt": "A",
            "lib/util.py": "D",
        }

        second = create_checkpoint(UNC, label="after edit", reason="pre-turn", platform="win32")
        listing = list_checkpoints(UNC, platform="win32")["checkpoints"]
        assert [c["label"] for c in listing] == ["after edit", "initial"]
        assert listing[0]["id"] == second["id"]

        result = restore_checkpoint(UNC, first["id"], platform="win32")
        assert result["restored"] == first["id"]
        assert result["deletedFiles"] == 1
        assert (distro.project / "app.py").read_text() == "print('v1')\n"
        assert (distro.project / "lib" / "util.py").read_text() == "X = 1\n"
        assert not (distro.project / "extra.txt").exists()

        # The safety snapshot taken before the restore is itself restorable.
        restore_checkpoint(UNC, result["safety"], platform="win32")
        assert (distro.project / "app.py").read_text() == "print('v2')\n"
        assert (distro.project / "extra.txt").exists()

    def test_nothing_is_written_to_the_windows_home(self, distro: FakeDistro) -> None:
        create_checkpoint(UNC, label="only snapshot", platform="win32")
        windows_home = checkpoints_api.checkpoints_home()
        assert not windows_home.exists() or not any(windows_home.iterdir())
        # The objects are on the distribution's own filesystem, which is the
        # whole point: no /mnt round trip per object, no unreadable-from-Linux
        # repository.
        repo = shadow_repo(UNC, platform="win32")
        assert (repo.host_git_dir / "objects").is_dir()
        assert (repo.host_git_dir / "info" / "exclude").read_text().startswith(".git/")
        assert distro.root in repo.host_git_dir.parents

    def test_missing_project_is_reported_as_not_found(self, distro: FakeDistro) -> None:
        with pytest.raises(CheckpointError) as exc:
            create_checkpoint(
                r"\\wsl.localhost\Ubuntu\home\me\gone", platform="win32"
            )
        assert exc.value.status == 404

    def test_listing_before_any_snapshot_is_empty(self, distro: FakeDistro) -> None:
        assert list_checkpoints(UNC, platform="win32") == {"checkpoints": []}


def _seed_legacy_windows_shadow(distro: FakeDistro, path_text: str) -> Path:
    """Build the shadow repo an older build would have left on the Windows side."""
    legacy = checkpoints_api.checkpoints_home() / _digest(path_text)
    project = distro.project
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=checkpoints", str(legacy)],
        check=True,
        capture_output=True,
    )
    base = [
        "git",
        "--git-dir",
        str(legacy),
        "--work-tree",
        str(project),
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
    ]
    subprocess.run([*base, "config", "core.bare", "false"], check=True, capture_output=True)
    subprocess.run([*base, "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        [*base, "commit", "-qm", "[manual] legacy snapshot"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    return legacy


class TestMigration:
    def test_existing_windows_side_history_is_adopted(self, distro: FakeDistro) -> None:
        legacy = _seed_legacy_windows_shadow(distro, UNC)

        listing = list_checkpoints(UNC, platform="win32")["checkpoints"]
        assert [c["label"] for c in listing] == ["legacy snapshot"]

        repo = shadow_repo(UNC, platform="win32")
        assert (repo.host_git_dir / "HEAD").is_file()
        # The original is kept, renamed, never deleted behind the user's back.
        assert not legacy.exists()
        assert legacy.with_name(legacy.name + ".migrated").is_dir()

        # An adopted history is a working history, not just a readable one.
        (distro.project / "app.py").write_text("print('after migration')\n", encoding="utf-8")
        follow_up = create_checkpoint(UNC, label="after migration", platform="win32")
        assert follow_up["created"] is True
        restore_checkpoint(UNC, listing[0]["id"], platform="win32")
        assert (distro.project / "app.py").read_text() == "print('v1')\n"

    def test_the_older_redirector_spelling_is_found_too(self, distro: FakeDistro) -> None:
        # A path typed as \\wsl$\... hashes differently from \\wsl.localhost\...
        _seed_legacy_windows_shadow(distro, r"\\wsl$\Ubuntu\home\me\proj")
        listing = list_checkpoints(UNC, platform="win32")["checkpoints"]
        assert [c["label"] for c in listing] == ["legacy snapshot"]

    def test_adoption_happens_once(self, distro: FakeDistro) -> None:
        _seed_legacy_windows_shadow(distro, UNC)
        create_checkpoint(UNC, label="first", platform="win32")
        (distro.project / "app.py").write_text("print('v2')\n", encoding="utf-8")
        create_checkpoint(UNC, label="second", platform="win32")
        labels = [c["label"] for c in list_checkpoints(UNC, platform="win32")["checkpoints"]]
        assert labels == ["second", "legacy snapshot"]


class TestUnreachableDistribution:
    def test_no_wsl_is_an_actionable_error(
        self, distro: FakeDistro, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Never a bare "not a git repository", and never a shadow repo on the
        # Windows side that the Linux side could not read back.
        monkeypatch.setattr(wsl, "wsl_executable", lambda: None)
        with pytest.raises(CheckpointError) as exc:
            create_checkpoint(UNC, platform="win32")
        assert exc.value.code == "wslUnreachable"
        assert exc.value.status == 501

    def test_stopped_distribution_is_an_actionable_error(
        self, distro: FakeDistro, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(wsl, "distro_home", lambda _distro, **_kw: None)
        with pytest.raises(CheckpointError) as exc:
            list_checkpoints(UNC, platform="win32")
        assert exc.value.code == "wslHomeUnreachable"
        assert "Ubuntu" in exc.value.message
