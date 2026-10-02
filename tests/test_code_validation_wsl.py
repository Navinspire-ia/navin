# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A Windows host watching a \\\\wsl.localhost project snapshots it from inside.

Measured on a desktop session: Windows git refuses the WSL-owned repo, the
os.walk fallback stats the tree over 9P, and every exec waited minutes before
starting. One wsl.exe call does the same listing in well under a second.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from navin.agent import code_validation as cv
from navin.utils.wsl import WslLocation

UNC = Path(r"\\wsl.localhost\Ubuntu\home\me\proj")
LOCATION = WslLocation(distro="Ubuntu", path=PurePosixPath("/home/me/proj"))


class WslSnapshotTest(unittest.TestCase):
    def test_a_unc_root_is_snapshotted_inside_the_distribution(self) -> None:
        out = (
            b"1790952750.751547679 12 src/app.ts\0"
            b"1790952750.000000001 3 README.md\0"
            b"1790952750.5 7 node_modules/x/index.js\0"
        )
        with mock.patch.object(cv, "_wsl_root", return_value=LOCATION), \
             mock.patch.object(cv, "_run_in_wsl", return_value=out) as run, \
             mock.patch.object(cv.os, "walk", side_effect=AssertionError("walked 9P")):
            snapshot = cv.workspace_code_snapshot(UNC)
        self.assertEqual(snapshot, {"src/app.ts": (1790952750751547679, 12)})
        self.assertEqual(run.call_args.args[1][:2], ["sh", "-c"])

    def test_an_unusable_answer_falls_back_to_the_host_walk(self) -> None:
        with mock.patch.object(cv, "_wsl_root", return_value=LOCATION), \
             mock.patch.object(cv, "_run_in_wsl", return_value=None), \
             mock.patch.object(cv.subprocess, "run", side_effect=OSError), \
             mock.patch.object(cv.os, "walk", return_value=iter([])) as walk:
            self.assertEqual(cv.workspace_code_snapshot(UNC), {})
        walk.assert_called_once()

    def test_an_old_stat_without_nanoseconds_is_not_trusted(self) -> None:
        self.assertIsNone(cv._parse_wsl_stamps(b"%.9Y 12 src/app.ts\0"))

    def test_the_script_matches_the_native_snapshot(self) -> None:
        root = Path(__file__).resolve().parents[1]
        raw = subprocess.run(
            ["sh", "-c", cv._WSL_SNAPSHOT_SCRIPT], cwd=root, capture_output=True, check=False,
        ).stdout
        stamps = cv._parse_wsl_stamps(raw) or {}
        listed = {
            path: stamp for path, stamp in stamps.items()
            if cv.development_path(path) and not cv._GENERATED_DIRS.intersection(PurePosixPath(path).parts)
        }
        self.assertEqual(listed, cv.workspace_code_snapshot(root))

    def test_known_edits_are_restamped_on_the_same_clock(self) -> None:
        state = cv.CodeValidationState()
        state.workspace_snapshot = {"src/app.ts": (1, 1)}
        with mock.patch.object(cv, "_wsl_root", return_value=LOCATION), \
             mock.patch.object(cv, "_file_stamp", return_value=(1790952750751547600, 12)), \
             mock.patch.object(cv, "_run_in_wsl", return_value=b"1790952750.751547679 12 src/app.ts\0"):
            state.sync_known_edits(UNC, {"src/app.ts"})
        self.assertEqual(state.workspace_snapshot["src/app.ts"], (1790952750751547679, 12))


if __name__ == "__main__":
    unittest.main()
