# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A Windows sharing refusal on rename must not surface as a tool error.

Measured on a desktop session: the board write onto a \\\\wsl.localhost project
failed once with WinError 5 and the agent re-issued the whole call.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.utils import atomic_io


class ReplaceWithRetryTest(unittest.TestCase):
    def test_a_transient_refusal_is_retried(self) -> None:
        with TemporaryDirectory() as tmp:
            src, dst = Path(tmp, "a.tmp"), Path(tmp, "a.json")
            src.write_text("new")
            dst.write_text("old")
            real = os.replace
            calls = iter([PermissionError(5, "Access is denied"), None])

            def flaky(a, b):
                outcome = next(calls)
                if outcome is not None:
                    raise outcome
                real(a, b)

            with mock.patch.object(atomic_io, "_IS_WINDOWS", True), \
                 mock.patch.object(atomic_io.os, "replace", side_effect=flaky), \
                 mock.patch.object(atomic_io.time, "sleep"):
                atomic_io.replace_with_retry(src, dst)
            self.assertEqual(dst.read_text(), "new")
            self.assertFalse(src.exists())

    def test_a_rename_that_never_works_still_lands_the_data(self) -> None:
        with TemporaryDirectory() as tmp:
            src, dst = Path(tmp, "a.tmp"), Path(tmp, "a.json")
            src.write_text("new")
            dst.write_text("old")
            with mock.patch.object(atomic_io, "_IS_WINDOWS", True), \
                 mock.patch.object(atomic_io.os, "replace", side_effect=PermissionError(5, "denied")), \
                 mock.patch.object(atomic_io.time, "sleep"):
                atomic_io.replace_with_retry(src, dst)
            self.assertEqual(dst.read_text(), "new")
            self.assertFalse(src.exists())

    def test_posix_permission_errors_are_real(self) -> None:
        with mock.patch.object(atomic_io, "_IS_WINDOWS", False), \
             mock.patch.object(atomic_io.os, "replace", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(PermissionError):
                atomic_io.replace_with_retry("a", "b")


if __name__ == "__main__":
    unittest.main()
