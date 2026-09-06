"""What a packaged build must do exactly like a source install.

PyInstaller changes two things that quietly remove features: ``sys.executable``
stops being an interpreter, and package metadata mostly disappears. Every check
here failed against the shipped binaries before, in ways that were invisible from
a source checkout - which is where all the other tests run.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin import optional_features, python_runtime
from navin.utils import document_templates

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Frozen:
    """Make the current process look like a PyInstaller build."""

    def __init__(self, executable: str | None = None):
        self.executable = executable
        self._patches: list[object] = []

    def __enter__(self):
        self._patches = [mock.patch.object(sys, "frozen", True, create=True)]
        if self.executable is not None:
            self._patches.append(mock.patch.object(sys, "executable", self.executable))
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()
        return False


class BundledExtrasTest(unittest.TestCase):
    """Metadata does not survive freezing; the build has to say what it shipped."""

    def tearDown(self):
        os.environ.pop("NAVIN_BUNDLED_EXTRAS", None)

    def test_a_source_install_declares_nothing(self):
        self.assertEqual(optional_features.bundled_extras(), frozenset())

    def test_a_manifest_left_by_a_local_build_is_ignored_from_source(self):
        """Building an artifact writes the file into the tree; it must not count."""
        manifest = Path(optional_features.__file__).with_name("bundled_extras.txt")
        existed = manifest.exists()
        previous = manifest.read_text(encoding="utf-8") if existed else ""
        manifest.write_text("slack\ntelegram\n", encoding="utf-8")
        try:
            self.assertEqual(optional_features.bundled_extras(), frozenset())
            with _Frozen():
                self.assertEqual(
                    optional_features.bundled_extras(), frozenset({"slack", "telegram"})
                )
        finally:
            if existed:
                manifest.write_text(previous, encoding="utf-8")
            else:
                manifest.unlink()

    def test_the_manifest_is_parsed_in_both_shapes(self):
        os.environ["NAVIN_BUNDLED_EXTRAS"] = "api,slack\ntelegram\n"
        self.assertEqual(
            optional_features.bundled_extras(),
            frozenset({"api", "slack", "telegram"}),
        )

    def test_a_declared_extra_counts_as_installed(self):
        """The symptom this fixes: every channel reported as missing_dependency."""
        absent = ["definitely-not-installed>=1.0"]
        with mock.patch.object(optional_features, "bundled_extras", return_value=frozenset()):
            self.assertFalse(optional_features.extra_installed("slack", absent))
        os.environ["NAVIN_BUNDLED_EXTRAS"] = "slack"
        self.assertTrue(optional_features.extra_installed("slack", absent))

    def test_an_undeclared_extra_stays_missing(self):
        os.environ["NAVIN_BUNDLED_EXTRAS"] = "slack"
        self.assertFalse(
            optional_features.extra_installed("telegram", ["definitely-not-installed>=1.0"])
        )

    def test_the_packaging_scripts_all_write_the_manifest(self):
        root = Path(__file__).resolve().parents[1] / "packaging"
        for relative in (
            "linux/build-offline.sh",
            "macos/build-offline.sh",
            "windows/build-offline.ps1",
        ):
            text = (root / relative).read_text(encoding="utf-8")
            self.assertIn("bundled_extras.txt", text, relative)


class PackagedInstallTest(unittest.TestCase):
    """A packaged build must not pretend it can install Python packages."""

    def test_it_does_not_run_pip_through_its_own_executable(self):
        calls: list[list[str]] = []

        def runner(argv):
            calls.append(argv)
            raise AssertionError("nothing should be executed")

        with _Frozen():
            result = optional_features.install_extra("slack", ["slack-sdk>=3"], runner=runner)
        self.assertFalse(result.ok)
        self.assertEqual(calls, [])
        self.assertIn("packaged build", result.output)

    def test_a_source_install_still_calls_pip(self):
        seen: list[list[str]] = []

        class Done:
            returncode = 0
            stdout = ""
            stderr = ""

        def runner(argv):
            seen.append(argv)
            return Done()

        result = optional_features.install_extra("slack", ["slack-sdk>=3"], runner=runner)
        self.assertTrue(result.ok)
        self.assertEqual(seen[0][:4], [sys.executable, "-m", "pip", "install"])


class EmbeddedInterpreterTest(unittest.TestCase):
    """The libraries are in the bundle; something has to be able to run them."""

    def test_a_source_install_names_its_own_interpreter(self):
        self.assertEqual(python_runtime.python_command(), [sys.executable])
        self.assertEqual(python_runtime.external_python(), sys.executable)

    def test_a_packaged_build_goes_through_its_own_subcommand(self):
        with _Frozen("/opt/navin/navin"):
            self.assertEqual(python_runtime.python_command(), ["/opt/navin/navin", "python"])

    def test_it_never_offers_itself_for_installing_packages(self):
        with _Frozen(sys.executable):
            self.assertNotEqual(python_runtime.external_python(), sys.executable)

    def test_the_shims_forward_to_this_build_and_are_executable(self):
        with tempfile.TemporaryDirectory() as tmp, _Frozen("/opt/navin/navin"):
            created = python_runtime.interpreter_shim_dir(Path(tmp))
            self.assertIsNotNone(created)
            names = sorted(path.name for path in created.iterdir())
            self.assertEqual(names, ["python", "python3"])
            for name in names:
                shim = created / name
                self.assertIn('"/opt/navin/navin" "python"', shim.read_text(encoding="utf-8"))
                if os.name != "nt":
                    self.assertTrue(shim.stat().st_mode & stat.S_IXUSR, name)

    def test_a_source_install_needs_no_shims(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(python_runtime.interpreter_shim_dir(Path(tmp)))

    def test_the_exec_tool_keeps_a_python_on_path(self):
        """Skills write short Python scripts; PATH is how they find an interpreter."""
        from navin.agent.tools.shell import _runtime_bin_dir

        self.assertEqual(_runtime_bin_dir(), str(Path(sys.executable).resolve().parent))
        with _Frozen("/opt/navin/navin"), tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "navin.config.paths.get_runtime_subdir",
                return_value=Path(tmp),
            ):
                self.assertEqual(_runtime_bin_dir(), tmp)


class ConverterCommandTest(unittest.TestCase):
    """The document converters are scripts: they need an interpreter that exists."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_command_starts_with_a_runnable_interpreter(self):
        command = document_templates.converter_command(self.workspace, "html2docx")
        self.assertIsNotNone(command, "the converters should be materializable from source")
        self.assertTrue(command.startswith(sys.executable))

    def test_a_packaged_build_calls_its_own_python_subcommand(self):
        with _Frozen("/opt/navin/navin"):
            command = document_templates.converter_command(self.workspace, "html2docx")
        self.assertIsNotNone(command)
        self.assertTrue(command.startswith("/opt/navin/navin python "), command)

    def test_the_specs_ship_the_converter_sources(self):
        """collect_data_files skips .py, so these have to be added by hand."""
        root = REPO_ROOT / "packaging" / "pyinstaller"
        contents = (root / "bundle_contents.py").read_text(encoding="utf-8")
        self.assertIn("navin/documents", contents)
        self.assertIn("scripts/*.py", contents)
        self.assertIn(
            "bundle_contents.script_data()",
            (root / "navin-onefile.spec").read_text(encoding="utf-8"),
        )

    def test_the_sidecar_build_is_filled_from_the_shared_list(self):
        """Every capability the sidecar ships comes from bundle_contents, so a
        missing tool is a one-line diff there instead of a silent regression."""
        root = REPO_ROOT / "packaging" / "pyinstaller"
        calls = [
            "bundle_contents.hidden_imports()",
            "bundle_contents.tool_binaries()",
            "bundle_contents.template_data()",
            "bundle_contents.script_data()",
            "bundle_contents.extra_data()",
            "bundle_contents.prefer_newest_openssl(a.binaries)",
        ]
        text = (root / "navin-onefile.spec").read_text(encoding="utf-8")
        for call in calls:
            self.assertIn(call, text, f"navin-onefile.spec does not use {call}")

    def test_the_bundle_carries_the_version_it_was_built_from(self):
        """`navin.__version__` reads package metadata, which freezing drops.

        Without navin-ai's own metadata in the tree, every packaged build fell
        back to the hard-coded "1.0.0": the updater would keep offering the
        release it had just installed, and nothing could tell which build
        installed the toolchains under ~/.navin.
        """
        contents = (
            REPO_ROOT / "packaging" / "pyinstaller" / "bundle_contents.py"
        ).read_text(encoding="utf-8")
        self.assertIn('_APP_METADATA = ("navin-ai",)', contents)
        self.assertIn("*_APP_METADATA", contents)
        stamped = (REPO_ROOT / "navin" / "_version.py").read_text(encoding="utf-8")
        self.assertRegex(stamped, r'__version__\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+')
        setter = (REPO_ROOT / "scripts" / "set-version.sh").read_text(encoding="utf-8")
        self.assertIn("navin/_version.py", setter)


class OpenSslDedupeTest(unittest.TestCase):
    """Two OpenSSLs met in one Mac build and the older one shipped, so
    cryptography failed to load and the WebUI never answered."""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        source = REPO_ROOT / "packaging" / "pyinstaller" / "openssl_dedupe.py"
        spec = importlib.util.spec_from_file_location("openssl_dedupe", source)
        cls.dedupe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.dedupe)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _library(self, name: str, version: str) -> Path:
        path = self.root / name
        path.write_bytes(b"\x00padding\x00OpenSSL " + version.encode() + b" 1 Jan 2026\x00")
        return path

    def test_the_version_is_read_from_the_banner(self):
        library = self._library("libssl.3.dylib", "3.6.1")
        self.assertEqual(self.dedupe.openssl_version(library), (3, 6, 1))
        self.assertEqual(self.dedupe.openssl_version(self.root / "absent"), (0, 0, 0))

    @unittest.skipIf(sys.platform == "win32", "the collision cannot happen on Windows")
    def test_the_newest_linked_copy_replaces_the_collected_one(self):
        """The exact failure: _ssl's OpenSSL 3.0 was collected while
        cryptography's binding needed the 3.6 next to it."""
        old = self._library("libssl.3.dylib", "3.0.9")
        new = (self.root / "brew").resolve() / "libssl.3.dylib"
        new.parent.mkdir()
        new.write_bytes(b"OpenSSL 3.6.1")
        binding = self.root / "_rust.abi3.so"
        binding.write_bytes(b"consumer")

        toc = [
            ("libssl.3.dylib", str(old), "BINARY"),
            ("cryptography/hazmat/bindings/_rust.abi3.so", str(binding), "EXTENSION"),
        ]
        rebuilt = self.dedupe.prefer_newest_openssl(
            toc, dependencies=lambda binary: [new] if binary == binding else []
        )
        sources = {dest: source for dest, source, _kind in rebuilt}
        self.assertEqual(sources["libssl.3.dylib"], str(new))
        self.assertIn("cryptography/hazmat/bindings/_rust.abi3.so", sources)

    @unittest.skipIf(sys.platform == "win32", "the collision cannot happen on Windows")
    def test_a_build_with_one_openssl_is_left_alone(self):
        only = self._library("libcrypto.3.dylib", "3.2.0")
        toc = [("libcrypto.3.dylib", str(only), "BINARY")]
        self.assertEqual(
            self.dedupe.prefer_newest_openssl(toc, dependencies=lambda binary: []), toc
        )

    def test_the_smoke_test_loads_cryptography_against_the_bundle(self):
        text = (REPO_ROOT / "packaging" / "smoke-test.sh").read_text(encoding="utf-8")
        self.assertIn("cryptography.hazmat.bindings._rust", text)
        self.assertIn("ships no navin-sandbox", text)

    def test_the_smoke_test_checks_document_libraries_without_weasyprint(self):
        """HTML→PDF uses Chromium / ReportLab; weasyprint is not a product dep."""
        text = (REPO_ROOT / "packaging" / "smoke-test.sh").read_text(encoding="utf-8")
        self.assertIn(
            "import Cryptodome, docx, openpyxl, pptx, pandas, reportlab, pdfplumber",
            text,
        )
        self.assertIn(
            "import navin.career.desk_cli, navin.leads.desk_cli, navin.marketing.desk_cli, navin.tenders.desk_cli, navin.trading.desk_cli",
            text,
        )
        self.assertNotIn("weasyprint", text)

    def test_windows_smoke_test_imports_the_five_studio_desks(self) -> None:
        text = (REPO_ROOT / "packaging" / "windows" / "smoke-test.ps1").read_text(encoding="utf-8")
        self.assertIn(
            "import navin.career.desk_cli, navin.leads.desk_cli, navin.marketing.desk_cli, navin.tenders.desk_cli, navin.trading.desk_cli",
            text,
        )

    def test_bundle_contents_skips_unimportable_skill_libraries(self):
        """Packages that fail at import must not go through collect_submodules."""
        text = (
            REPO_ROOT / "packaging" / "pyinstaller" / "bundle_contents.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def _importable(", text)
        self.assertIn("_importable(name)", text)
        self.assertIn("skipping", text)
        self.assertNotIn('"weasyprint"', text)
        self.assertIn("DESK_PACKAGES", text)
        self.assertIn("def desk_hidden_imports(", text)
        for pkg in (
            "navin.career",
            "navin.leads",
            "navin.marketing",
            "navin.tenders",
            "navin.trading",
        ):
            self.assertIn(f'"{pkg}"', text)


class CliLinkTest(unittest.TestCase):
    """A disk image drops an app bundle; no shell can call what is inside it."""

    def setUp(self):
        from navin import cli_link

        self.cli_link = cli_link
        self._tmp = tempfile.TemporaryDirectory()
        self.bindir = Path(self._tmp.name) / "bin"

    def tearDown(self):
        self._tmp.cleanup()

    def _link(self, **kwargs):
        with (
            _Frozen(),
            mock.patch.object(self.cli_link, "_candidate_dirs", return_value=[self.bindir]),
        ):
            return self.cli_link.install_cli_link(**kwargs)

    @unittest.skipIf(sys.platform == "win32", "the Windows installer owns the PATH entry")
    def test_it_links_the_command_to_this_executable(self):
        result = self._link()
        self.assertTrue(result.created, result.message)
        self.assertTrue(result.path.is_symlink())
        self.assertEqual(result.path.resolve(), Path(sys.executable).resolve())

    @unittest.skipIf(sys.platform == "win32", "the Windows installer owns the PATH entry")
    def test_it_says_so_when_the_directory_is_not_on_path(self):
        result = self._link()
        self.assertFalse(result.on_path)
        self.assertIn("PATH", result.message)

    @unittest.skipIf(sys.platform == "win32", "the Windows installer owns the PATH entry")
    def test_it_leaves_someone_elses_file_alone_unless_forced(self):
        self.bindir.mkdir(parents=True)
        (self.bindir / "navin").write_text("#!/bin/sh\n", encoding="utf-8")
        result = self._link()
        self.assertFalse(result.created)
        self.assertIn("--force", result.message)
        self.assertTrue(self._link(force=True).created)

    def test_a_source_install_is_told_it_needs_nothing(self):
        result = self.cli_link.install_cli_link()
        self.assertFalse(result.created)
        self.assertIn("source install", result.message)


class ReinstallTest(unittest.TestCase):
    """Reinstalling has to leave the terminal on the version that is installed.

    Windows compares its shim to the current CLI (`_windows_shims_current`);
    POSIX only asked whether `navin` resolved at all, so a reinstall that
    landed somewhere else - an admin-less copy in ~/Applications, an AppImage
    saved under a new name - kept the old link alive and the terminal kept
    running the build the user had just replaced.
    """

    def setUp(self):
        from navin import cli_link

        self.cli_link = cli_link
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "bin").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _binary(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path

    def _link_on_path(self, target: Path) -> Path:
        link = self.root / "bin" / "navin"
        link.symlink_to(target)
        return link

    def _stale(self, link: Path, current: Path):
        with (
            mock.patch("shutil.which", return_value=str(link)),
            mock.patch.object(self.cli_link, "_posix_cli_target", return_value=current.resolve()),
        ):
            return self.cli_link._stale_posix_link()

    def test_an_old_bundle_still_on_path_counts_as_stale(self):
        old = self._binary("Applications/Navin.app/Contents/Resources/navin-dist/navin")
        new = self._binary("home/Applications/Navin.app/Contents/Resources/navin-dist/navin")
        self.assertEqual(self._stale(self._link_on_path(old), new), self.root / "bin" / "navin")

    def test_a_pip_install_on_path_is_never_touched(self):
        theirs = self._binary("venv/bin/navin")
        mine = self._binary("usr/lib/navin/navin")
        self.assertIsNone(self._stale(self._link_on_path(theirs), mine))

    def test_the_current_install_is_not_reported_stale(self):
        mine = self._binary("opt/Navin.app/Contents/Resources/navin-dist/navin")
        self.assertIsNone(self._stale(self._link_on_path(mine), mine))

    def test_an_appimage_links_to_the_image_not_its_temporary_mount(self):
        """The squashfs mount is gone the moment the app closes."""
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "Navin-1.0.0-linux-x64.AppImage"
            image.write_bytes(b"")
            with (
                mock.patch.dict(os.environ, {"APPIMAGE": str(image)}),
                mock.patch.object(sys, "executable", "/tmp/.mount_Navin42/usr/lib/navin/navin"),
            ):
                self.assertEqual(self.cli_link._posix_cli_target(), image.resolve())

    def test_it_refuses_to_link_into_a_temporary_mount(self):
        mount = "/tmp/.mount_Navin42/usr/lib/navin/navin"
        with (
            _Frozen(mount),
            mock.patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("APPIMAGE", None)
            result = self.cli_link.install_cli_link()
        self.assertFalse(result.created)
        self.assertIn("AppImage", result.message)


class WindowsPathTest(unittest.TestCase):
    """Typing `navin` in a terminal has to work on Windows too.

    L'installateur Tauri (NSIS/MSI) ne touche pas au PATH : c'est le
    self-heal de navin/cli_link.py, exécuté au premier lancement de l'app,
    qui écrit les shims (navin.cmd + shim POSIX pour WSL) et étend le PATH
    utilisateur.
    """

    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.cli_link = (root / "navin" / "cli_link.py").read_text(encoding="utf-8")

    def test_the_self_heal_writes_the_windows_shims(self):
        """The console binary is navin-cli.exe; the documented command is navin."""
        self.assertIn("navin.cmd", self.cli_link)
        self.assertIn("navin-cli.exe", self.cli_link)

    def test_the_self_heal_extends_the_user_path(self):
        self.assertIn("_add_to_user_path_windows", self.cli_link)
        self.assertIn("ensure_cli_on_path", self.cli_link)


class MacUpdateTest(unittest.TestCase):
    """macOS used to be the one packaged build that could not update itself."""

    def setUp(self):
        from navin.update import service

        self.service = service
        os.environ.pop("NAVIN_INSTALL_KIND", None)

    def test_both_macos_shapes_are_recognised(self):
        bundle = "/Applications/Navin.app/Contents/MacOS/Navin"
        with _Frozen(bundle), mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(self.service._install_kind(), "macos-app")
        with (
            _Frozen("/usr/local/bin/navin-macos-arm64"),
            mock.patch.object(sys, "platform", "darwin"),
        ):
            self.assertEqual(self.service._install_kind(), "macos")

    def test_the_downloaded_image_keeps_a_name_hdiutil_accepts(self):
        self.assertTrue(str(self.service._download_path("9.9.9", "macos-app")).endswith(".dmg"))
        self.assertFalse(str(self.service._download_path("9.9.9", "macos")).endswith(".dmg"))
        self.assertTrue(str(self.service._download_path("9.9.9", "windows-setup")).endswith(".exe"))

    def test_the_bundle_is_found_from_the_executable_inside_it(self):
        with _Frozen("/Applications/Navin.app/Contents/MacOS/Navin"):
            self.assertEqual(self.service._app_bundle_path(), Path("/Applications/Navin.app"))
        with _Frozen("/usr/local/bin/navin"):
            self.assertIsNone(self.service._app_bundle_path())

    def test_it_refuses_when_there_is_no_bundle_to_replace(self):
        with _Frozen("/usr/local/bin/navin"):
            with self.assertRaises(self.service.UpdateError):
                self.service._install_macos_app(Path("/tmp/x.dmg"), pid=1, desktop_pid=0)

    def test_it_hands_the_swap_to_a_detached_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Navin.app"
            (app / "Contents" / "MacOS").mkdir(parents=True)
            executable = app / "Contents" / "MacOS" / "Navin"
            executable.write_text("", encoding="utf-8")
            spawned: list[list[str]] = []

            def fake_popen(command, **kwargs):
                spawned.append(command)
                return mock.Mock()

            with (
                _Frozen(str(executable)),
                mock.patch.object(self.service.subprocess, "Popen", fake_popen),
            ):
                self.service._install_macos_app(
                    Path(tmp) / "update.dmg", pid=4242, desktop_pid=77
                )

            command = spawned[0]
            self.assertEqual(command[0], "/bin/sh")
            self.assertIn("4242", command)
            # The window is closed too: the bundle cannot be replaced under a
            # shell that is still running from it.
            self.assertIn("77", command)
            self.assertIn(str(app), command)
            self.assertIn(f"{app}.old", command)
            # The running process must be gone before the bundle is touched.
            self.assertIn('kill -0 "$1"', command[2])
            self.assertIn("ditto", command[2])
            self.assertIn("-mountpoint", command[2])
            self.assertIn('find "$mnt"', command[2])


class WindowsUpdaterHelperTest(unittest.TestCase):
    """The Windows helper used to die on Tauri's ``\\\\?\\`` canonical path."""

    def test_the_helper_and_the_shell_strip_the_extended_prefix(self):
        helper = (REPO_ROOT / "packaging" / "windows" / "NavinUpdater.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("StripExtendedPrefix", helper)
        self.assertIn("NormalizePath", helper)
        rust = (REPO_ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(
            encoding="utf-8"
        )
        self.assertIn("strip_extended_path_prefix(&exe.to_string_lossy())", rust)

    def test_a_newer_setup_overwrites_in_place_instead_of_uninstalling(self):
        conf = json.loads(
            (REPO_ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )
        nsis = conf["bundle"]["windows"]["nsis"]
        self.assertEqual(nsis["template"], "windows/installer.nsi")
        template = (
            REPO_ROOT / "desktop" / "src-tauri" / "windows" / "installer.nsi"
        ).read_text(encoding="utf-8")
        self.assertIn("NAVIN-INPLACE-UPGRADE", template)
        self.assertIn("StrCpy $UpdateMode 1", template)
        helper = (REPO_ROOT / "packaging" / "windows" / "NavinUpdater.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn('/S /UPDATE', helper)
        hooks = (
            REPO_ROOT / "desktop" / "src-tauri" / "windows" / "installer-hooks.nsh"
        ).read_text(encoding="utf-8")
        self.assertIn('RMDir /r "$INSTDIR\\navin-dist"', hooks)


class QualityToolTest(unittest.TestCase):
    """lint, test_run, verify and lsp have to work in a packaged build too.

    Their tools are subprocesses started through console scripts, and a frozen
    build has no scripts directory: every one of them reported "not installed"
    while ruff was sitting in the bundle and pytest was importable.
    """

    def setUp(self):
        from navin.quality import linters

        self.linters = linters
        # Resolution is cached per (name, project, interpreter) triple, and these
        # tests change what the interpreter is.
        linters._resolve_binary.cache_clear()

    def tearDown(self):
        self.linters._resolve_binary.cache_clear()

    def test_a_module_only_tool_is_launched_through_the_interpreter(self):
        spec = {"binary": {"name": "navin-no-such-tool", "run_as_module": "json"}}
        launch = self.linters.tool_argv(spec, Path.cwd())
        self.assertEqual(launch, [*python_runtime.python_command(), "-m", "json"])

    def test_a_tool_that_is_neither_installed_nor_bundled_stays_missing(self):
        spec = {"binary": {"name": "navin-no-such-tool", "run_as_module": "navin_no_such_module"}}
        self.assertIsNone(self.linters.tool_argv(spec, Path.cwd()))

    def test_an_installed_program_wins_over_the_bundled_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / "node_modules" / ".bin").mkdir(parents=True)
            local = project / "node_modules" / ".bin" / "eslint"
            local.write_text("#!/bin/sh\n", encoding="utf-8")
            local.chmod(0o755)
            spec = {"binary": {"name": "eslint", "project_paths": ["node_modules/.bin/eslint"]}}
            self.assertEqual(self.linters.tool_argv(spec, project), [str(local)])

    def test_a_packaged_build_finds_the_program_it_ships(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp) / "tools"
            tools.mkdir()
            ruff = tools / ("ruff.exe" if os.name == "nt" else "ruff")
            ruff.write_text("binary", encoding="utf-8")
            ruff.chmod(0o755)
            with _Frozen(), mock.patch.object(sys, "_MEIPASS", tmp, create=True):
                self.assertEqual(python_runtime.bundled_tool("ruff"), str(ruff))
                self.assertIsNone(python_runtime.bundled_tool("mypy"))

    def test_a_source_install_has_nothing_bundled(self):
        self.assertIsNone(python_runtime.bundled_tool("ruff"))

    def test_python3_means_this_build_when_there_is_no_interpreter_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                _Frozen("/opt/navin/navin"),
                mock.patch("navin.config.paths.get_runtime_subdir", return_value=Path(tmp)),
            ):
                shim = self.linters._packaged_interpreter("python3")
            self.assertIsNotNone(shim)
            self.assertTrue(Path(shim).is_file())
            self.assertIn("/opt/navin/navin", Path(shim).read_text(encoding="utf-8"))

    def test_the_specs_ship_the_tools_the_tables_name(self):
        contents = (
            REPO_ROOT / "packaging" / "pyinstaller" / "bundle_contents.py"
        ).read_text(encoding="utf-8")
        # ruff is a program, the others are modules reached with `python -m`.
        self.assertIn('_QUALITY_PROGRAMS = ("ruff",)', contents)
        for module in ("pytest", "pylsp", "yamllint"):
            self.assertIn(f'"{module}"', contents)
        spec = (
            REPO_ROOT / "packaging" / "pyinstaller" / "navin-onefile.spec"
        ).read_text(encoding="utf-8")
        self.assertIn("bundle_contents.hidden_imports()", spec)
        self.assertIn("bundle_contents.tool_binaries()", spec)

    def test_the_builds_install_the_extra_that_provides_them(self):
        for relative in (
            "packaging/linux/build-offline.sh",
            "packaging/macos/build-offline.sh",
            "packaging/windows/build-offline.ps1",
        ):
            script = (REPO_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("dev-tools", script, relative)


class RestartTest(unittest.IsolatedAsyncioTestCase):
    """/restart has to bring navin back, packaged or not."""

    async def test_it_re_invokes_the_binary_without_the_module_flag(self):
        from navin.bus.events import InboundMessage
        from navin.command import builtin
        from navin.command.router import CommandContext

        recorded: dict[str, object] = {}

        def fake_execve(path, argv, env):
            recorded["path"] = path
            recorded["argv"] = list(argv)

        message = InboundMessage(
            channel="webui",
            chat_id="1",
            sender_id="user",
            content="/restart",
        )
        loop = mock.Mock()
        loop.restart_mode = "exec"
        ctx = CommandContext(msg=message, session=None, key="webui:1", raw="/restart", loop=loop)

        tasks: list[object] = []
        with (
            _Frozen("/opt/navin/navin"),
            mock.patch.object(sys, "argv", ["navin", "webui", "--port", "8765"]),
            mock.patch.object(os, "execve", fake_execve),
            mock.patch.object(asyncio, "create_task", side_effect=tasks.append),
        ):
            await builtin.cmd_restart(ctx)
            with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
                await tasks[0]

        self.assertEqual(recorded["path"], "/opt/navin/navin")
        self.assertEqual(recorded["argv"], ["/opt/navin/navin", "webui", "--port", "8765"])
        self.assertNotIn("-m", recorded["argv"])


if __name__ == "__main__":
    unittest.main()
