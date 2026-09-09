# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Guards against changes that would break navin on another operating system.

The repository is developed on Linux, so the ways it can stop working elsewhere
are invisible locally: a module-level ``import fcntl``, a path joined with a
literal "/", a URI built by percent-encoding native separators. These tests fail
on Linux the moment such a change lands, instead of waiting for a Windows user.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path, PureWindowsPath

from navin.lsp import client as lsp_client

_IMPORT_PROBE = textwrap.dedent(
    '''
    import importlib, json, pkgutil, sys

    BLOCKED = {"fcntl", "termios", "tty", "pwd", "grp", "resource", "syslog"}


    class _Blocker:
        def find_spec(self, fullname, path=None, target=None):
            root = fullname.split(".")[0]
            if root in BLOCKED:
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=root)
            return None


    for name in [n for n in sys.modules if n.split(".")[0] in BLOCKED]:
        del sys.modules[name]
    sys.meta_path.insert(0, _Blocker())

    import navin

    # Only a failure traced back to a blocked module counts. An optional
    # third-party package that is simply not installed is expected and says
    # nothing about portability, so the result does not depend on which extras
    # the caller installed.
    failures = {}
    for mod in pkgutil.walk_packages(navin.__path__, prefix="navin."):
        if ".skills." in mod.name or mod.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(mod.name)
        except ImportError as exc:
            missing = getattr(exc, "name", None)
            if missing and missing.split(".")[0] in BLOCKED:
                failures[mod.name] = f"needs {missing}: {exc}"
        except Exception:
            pass
    print(json.dumps(failures))
    '''
)


class PosixOnlyImportTest(unittest.TestCase):
    def test_every_module_imports_without_the_posix_stdlib(self) -> None:
        """A module-level POSIX import with no fallback breaks Windows at startup.

        Runs in a subprocess because the blocker has to be installed before
        navin is imported for the first time.
        """
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _IMPORT_PROBE],
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(proc.returncode, 0, f"probe crashed:\n{proc.stderr[-2000:]}")
        failures = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(
            failures,
            {},
            "these modules would fail to import on Windows; guard the import "
            "with try/except ImportError and degrade gracefully:\n"
            + "\n".join(f"  {n}: {r}" for n, r in failures.items()),
        )


class FileUriTest(unittest.TestCase):
    """Language servers are handed ``file:`` URIs; a Windows drive must survive."""

    def test_a_native_path_round_trips(self) -> None:
        path = Path(tempfile.gettempdir()).resolve() / "a b" / "c.py"
        uri = lsp_client.path_to_uri(path)
        self.assertTrue(uri.startswith("file:///"))
        self.assertNotIn(" ", uri)
        self.assertEqual(Path(lsp_client.uri_to_path(uri)), path)

    def test_a_drive_letter_is_not_percent_encoded(self) -> None:
        """Regression: quoting the raw string produced file://C%3A%5Cdir%5Cf.py."""
        uri = PureWindowsPath(r"C:\dir\f.py").as_uri()
        self.assertEqual(uri, "file:///C:/dir/f.py")
        self.assertNotIn("%5C", uri)
        self.assertNotIn("%3A", uri)

    def test_a_non_file_scheme_is_left_alone(self) -> None:
        self.assertEqual(lsp_client.uri_to_path("untitled:Untitled-1"), "untitled:Untitled-1")


if __name__ == "__main__":
    unittest.main()
