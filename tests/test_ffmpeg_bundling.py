# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The bundled ffmpeg: staging at build time, resolution at runtime, licence.

Navin redistributes a GPL binary, so the compliance parts are asserted here
rather than left to a reviewer's memory: a missing licence file or an
out-of-sync notices copy is a distribution defect, not a cosmetic one.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packaging"))

import ffmpeg_vendor  # noqa: E402

from navin.montage import detect  # noqa: E402

LICENCE_NAME = "ffmpeg-COPYING.GPLv3.txt"
BUNDLED_LICENCES = REPO_ROOT / "desktop" / "src-tauri" / "resources" / "licenses"


class TestTargets:
    def test_host_target_is_known(self):
        assert ffmpeg_vendor.current_target() in ffmpeg_vendor.TARGETS

    def test_windows_targets_use_the_exe_suffix(self):
        assert ffmpeg_vendor.binary_name("windows-x64") == "ffmpeg.exe"
        assert ffmpeg_vendor.binary_name("linux-x64") == "ffmpeg"
        assert ffmpeg_vendor.binary_name("macos-arm64") == "ffmpeg"
        assert ffmpeg_vendor.binary_name("windows-x64", "ffprobe") == "ffprobe.exe"
        assert ffmpeg_vendor.binary_name("linux-x64", "ffprobe") == "ffprobe"

    def test_vendor_path_is_per_target(self):
        a = ffmpeg_vendor.vendor_path("linux-x64")
        b = ffmpeg_vendor.vendor_path("macos-arm64")
        assert a != b
        assert a.parent.name == "linux-x64"

    def test_ffprobe_lives_next_to_its_ffmpeg(self):
        mpeg = ffmpeg_vendor.vendor_path("linux-x64")
        probe = ffmpeg_vendor.vendor_path("linux-x64", "ffprobe")
        assert probe.parent == mpeg.parent
        assert probe.name == "ffprobe"


class TestManifest:
    @pytest.fixture
    def manifest(self) -> dict:
        return ffmpeg_vendor.load_manifest()

    def test_every_target_has_a_source(self, manifest: dict):
        for target in ffmpeg_vendor.TARGETS:
            entry = manifest["targets"].get(target)
            assert entry, f"no manifest entry for {target}"
            assert entry["url"].startswith("https://"), target
            assert entry["archive"] in {"zip", "tar.xz", "tar.gz", "tar"}, target

    def test_macos_arm64_is_covered(self, manifest: dict):
        """Apple Silicon had no source at all; that gap must not come back."""
        entry = manifest["targets"]["macos-arm64"]
        assert entry["url"].startswith("https://")
        assert "evermeet" not in entry["url"], "evermeet ships x86_64 only"

    def test_downloads_are_checksum_pinned(self, manifest: dict):
        for target in ffmpeg_vendor.TARGETS:
            digest = manifest["targets"][target].get("sha256") or ""
            assert len(digest) == 64, f"{target}: sha256 must be pinned"
            int(digest, 16)

    def test_macos_ffprobe_comes_from_a_pinned_separate_archive(self, manifest: dict):
        """The macOS ffmpeg archives ship no ffprobe, unlike Linux/Windows.

        Without a sub-entry those bundles would silently lose frame-precise
        probing (trims, crossfade offsets) while the other platforms keep it.
        """
        for target in ("macos-x64", "macos-arm64"):
            sub = manifest["targets"][target].get("ffprobe")
            assert sub, f"{target}: needs an ffprobe sub-entry"
            assert sub["url"].startswith("https://"), target
            digest = sub.get("sha256") or ""
            assert len(digest) == 64, f"{target}: ffprobe sha256 must be pinned"
            int(digest, 16)

    def test_same_archive_platforms_declare_no_redundant_ffprobe(self, manifest: dict):
        for target in ("linux-x64", "linux-arm64", "windows-x64", "windows-arm64"):
            assert "ffprobe" not in manifest["targets"][target], (
                f"{target}: ffprobe is inside the ffmpeg archive; a sub-entry "
                "would download it twice"
            )

    def test_licence_is_declared(self, manifest: dict):
        assert manifest["license"].startswith("GPL")


class TestMemberSelection:
    def test_picks_the_program_not_a_resource_fork(self):
        assert ffmpeg_vendor._is_ffmpeg_member("ffmpeg", "macos-arm64")
        assert not ffmpeg_vendor._is_ffmpeg_member("__MACOSX/._ffmpeg", "macos-arm64")
        assert not ffmpeg_vendor._is_ffmpeg_member("._ffmpeg", "macos-arm64")

    def test_ignores_siblings(self):
        for name in ("ffprobe", "ffplay", "bin/ffprobe", "ffmpeg.txt"):
            assert not ffmpeg_vendor._is_ffmpeg_member(name, "linux-x64"), name

    def test_the_program_argument_selects_ffprobe(self):
        assert ffmpeg_vendor._is_ffmpeg_member("bin/ffprobe", "linux-x64", "ffprobe")
        assert ffmpeg_vendor._is_ffmpeg_member(
            "ffmpeg-7.0/bin/ffprobe.exe", "windows-x64", "ffprobe"
        )
        assert not ffmpeg_vendor._is_ffmpeg_member("ffmpeg", "linux-x64", "ffprobe")
        assert not ffmpeg_vendor._is_ffmpeg_member(
            "__MACOSX/._ffprobe", "macos-arm64", "ffprobe"
        )

    def test_matches_nested_layouts(self):
        assert ffmpeg_vendor._is_ffmpeg_member("ffmpeg-7.0-static/ffmpeg", "linux-x64")
        assert ffmpeg_vendor._is_ffmpeg_member("ffmpeg-7.0/bin/ffmpeg.exe", "windows-x64")

    def test_platform_suffix_is_respected(self):
        assert not ffmpeg_vendor._is_ffmpeg_member("bin/ffmpeg", "windows-x64")
        assert not ffmpeg_vendor._is_ffmpeg_member("bin/ffmpeg.exe", "linux-x64")


class TestExtraction:
    def test_zip_shallowest_match_wins(self, tmp_path: Path):
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("nested/deep/ffmpeg", b"deep")
            bundle.writestr("ffmpeg", b"shallow")
            bundle.writestr("__MACOSX/._ffmpeg", b"junk")
        dest = tmp_path / "out" / "ffmpeg"
        ffmpeg_vendor._extract(archive, "zip", "linux-x64", dest)
        assert dest.read_bytes() == b"shallow"

    def test_tar_xz_extraction(self, tmp_path: Path):
        payload = tmp_path / "ffmpeg"
        payload.write_bytes(b"binary")
        archive = tmp_path / "a.tar.xz"
        with tarfile.open(archive, "w:xz") as bundle:
            bundle.add(payload, arcname="ffmpeg-7.0-static/ffmpeg")
        dest = tmp_path / "out" / "ffmpeg"
        ffmpeg_vendor._extract(archive, "tar.xz", "linux-x64", dest)
        assert dest.read_bytes() == b"binary"

    def test_missing_program_is_an_error(self, tmp_path: Path):
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("readme.txt", b"nothing here")
        with pytest.raises(ffmpeg_vendor.VendorError, match="no ffmpeg"):
            ffmpeg_vendor._extract(archive, "zip", "linux-x64", tmp_path / "out" / "ffmpeg")

    def test_unknown_archive_kind_is_an_error(self, tmp_path: Path):
        with pytest.raises(ffmpeg_vendor.VendorError, match="unsupported archive"):
            ffmpeg_vendor._extract(tmp_path / "a.rar", "rar", "linux-x64", tmp_path / "o")


class TestChecksumEnforcement:
    def _stub_download(self, monkeypatch, payload: bytes, tmp_path: Path):
        def fake_download(url: str, dest: Path) -> str:
            with zipfile.ZipFile(dest, "w") as bundle:
                bundle.writestr("ffmpeg", payload)
                bundle.writestr("ffprobe", b"probe:" + payload)
            return hashlib.sha256(dest.read_bytes()).hexdigest()

        monkeypatch.setattr(ffmpeg_vendor, "_download", fake_download)
        monkeypatch.setattr(ffmpeg_vendor, "VENDOR_ROOT", tmp_path / "vendor")

    def test_mismatch_fails_the_build(self, tmp_path: Path, monkeypatch):
        self._stub_download(monkeypatch, b"payload", tmp_path)
        manifest = {
            "targets": {
                "linux-x64": {"url": "https://x/a.zip", "archive": "zip", "sha256": "00" * 32}
            }
        }
        with pytest.raises(ffmpeg_vendor.VendorError, match="checksum mismatch"):
            ffmpeg_vendor.ensure_ffmpeg("linux-x64", manifest=manifest)

    def test_unpinned_source_fails_the_build(self, tmp_path: Path, monkeypatch):
        self._stub_download(monkeypatch, b"payload", tmp_path)
        manifest = {
            "targets": {"linux-x64": {"url": "https://x/a.zip", "archive": "zip", "sha256": ""}}
        }
        with pytest.raises(ffmpeg_vendor.VendorError, match="no sha256"):
            ffmpeg_vendor.ensure_ffmpeg("linux-x64", manifest=manifest)

    def test_update_mode_records_the_checksum(self, tmp_path: Path, monkeypatch):
        self._stub_download(monkeypatch, b"payload", tmp_path)
        entry = {"url": "https://x/a.zip", "archive": "zip", "sha256": ""}
        manifest = {"targets": {"linux-x64": entry}}
        staged = ffmpeg_vendor.ensure_ffmpeg("linux-x64", manifest=manifest, update_manifest=True)
        assert len(entry["sha256"]) == 64
        assert staged.read_bytes() == b"payload"

    def test_unknown_target_is_an_error(self):
        with pytest.raises(ffmpeg_vendor.VendorError, match="no ffmpeg source"):
            ffmpeg_vendor.ensure_ffmpeg("solaris-sparc", manifest={"targets": {}})


class TestFfprobeStaging:
    def test_same_archive_targets_stage_both_programs_from_one_download(
        self, tmp_path: Path, monkeypatch
    ):
        downloads: list[str] = []

        def fake_download(url: str, dest: Path) -> str:
            downloads.append(url)
            with zipfile.ZipFile(dest, "w") as bundle:
                bundle.writestr("ffmpeg-7.0-static/ffmpeg", b"mpeg")
                bundle.writestr("ffmpeg-7.0-static/ffprobe", b"probe")
            return hashlib.sha256(dest.read_bytes()).hexdigest()

        monkeypatch.setattr(ffmpeg_vendor, "_download", fake_download)
        monkeypatch.setattr(ffmpeg_vendor, "VENDOR_ROOT", tmp_path / "vendor")
        manifest = {
            "targets": {
                "linux-x64": {"url": "https://x/a.zip", "archive": "zip", "sha256": ""}
            }
        }
        staged = ffmpeg_vendor.ensure_ffmpeg(
            "linux-x64", manifest=manifest, update_manifest=True
        )
        assert staged.read_bytes() == b"mpeg"
        assert ffmpeg_vendor.vendor_path("linux-x64", "ffprobe").read_bytes() == b"probe"
        assert downloads == ["https://x/a.zip"]

    def test_a_separate_ffprobe_archive_is_fetched_verified_and_pinned(
        self, tmp_path: Path, monkeypatch
    ):
        downloads: list[str] = []

        def fake_download(url: str, dest: Path) -> str:
            downloads.append(url)
            with zipfile.ZipFile(dest, "w") as bundle:
                if "ffprobe" in url:
                    bundle.writestr("ffprobe", b"probe")
                    bundle.writestr("__MACOSX/._ffprobe", b"junk")
                else:
                    bundle.writestr("ffmpeg", b"mpeg")
            return hashlib.sha256(dest.read_bytes()).hexdigest()

        monkeypatch.setattr(ffmpeg_vendor, "_download", fake_download)
        monkeypatch.setattr(ffmpeg_vendor, "VENDOR_ROOT", tmp_path / "vendor")
        probe_entry = {"url": "https://x/ffprobe.zip", "archive": "zip", "sha256": ""}
        manifest = {
            "targets": {
                "macos-arm64": {
                    "url": "https://x/ffmpeg.zip",
                    "archive": "zip",
                    "sha256": "",
                    "ffprobe": probe_entry,
                }
            }
        }
        ffmpeg_vendor.ensure_ffmpeg(
            "macos-arm64", manifest=manifest, update_manifest=True
        )
        assert (
            ffmpeg_vendor.vendor_path("macos-arm64", "ffprobe").read_bytes() == b"probe"
        )
        assert len(probe_entry["sha256"]) == 64
        assert downloads == ["https://x/ffmpeg.zip", "https://x/ffprobe.zip"]

    def test_a_missing_ffprobe_alone_is_restaged_without_refetching_ffmpeg(
        self, tmp_path: Path, monkeypatch
    ):
        """Re-running the vendor step after adding ffprobe must not redo ffmpeg."""
        downloads: list[str] = []

        def fake_download(url: str, dest: Path) -> str:
            downloads.append(url)
            with zipfile.ZipFile(dest, "w") as bundle:
                bundle.writestr("ffprobe", b"probe")
            return hashlib.sha256(dest.read_bytes()).hexdigest()

        monkeypatch.setattr(ffmpeg_vendor, "_download", fake_download)
        monkeypatch.setattr(ffmpeg_vendor, "VENDOR_ROOT", tmp_path / "vendor")
        already = ffmpeg_vendor.vendor_path("macos-arm64", "ffmpeg")
        already.parent.mkdir(parents=True)
        already.write_bytes(b"mpeg")
        manifest = {
            "targets": {
                "macos-arm64": {
                    "url": "https://x/ffmpeg.zip",
                    "archive": "zip",
                    "sha256": "",
                    "ffprobe": {
                        "url": "https://x/ffprobe.zip",
                        "archive": "zip",
                        "sha256": "",
                    },
                }
            }
        }
        ffmpeg_vendor.ensure_ffmpeg(
            "macos-arm64", manifest=manifest, update_manifest=True
        )
        assert downloads == ["https://x/ffprobe.zip"]
        assert already.read_bytes() == b"mpeg"

    def test_an_archive_without_ffprobe_fails_the_release(
        self, tmp_path: Path, monkeypatch
    ):
        def fake_download(url: str, dest: Path) -> str:
            with zipfile.ZipFile(dest, "w") as bundle:
                bundle.writestr("ffmpeg", b"mpeg")
            return hashlib.sha256(dest.read_bytes()).hexdigest()

        monkeypatch.setattr(ffmpeg_vendor, "_download", fake_download)
        monkeypatch.setattr(ffmpeg_vendor, "VENDOR_ROOT", tmp_path / "vendor")
        manifest = {
            "targets": {
                "linux-x64": {"url": "https://x/a.zip", "archive": "zip", "sha256": ""}
            }
        }
        with pytest.raises(ffmpeg_vendor.VendorError, match="no ffprobe"):
            ffmpeg_vendor.ensure_ffmpeg(
                "linux-x64", manifest=manifest, update_manifest=True
            )


class TestRuntimeResolution:
    def test_path_wins_so_an_operator_can_override(self, monkeypatch):
        monkeypatch.setattr(detect.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        monkeypatch.setattr(detect, "ffmpeg_bundled_bin", lambda: "/bundle/tools/ffmpeg")
        assert detect.find_ffmpeg() == "/usr/bin/ffmpeg"

    def test_bundled_wins_over_the_user_local_download(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(detect.shutil, "which", lambda name: None)
        monkeypatch.setattr(detect, "ffmpeg_bundled_bin", lambda: "/bundle/tools/ffmpeg")
        assert detect.find_ffmpeg() == "/bundle/tools/ffmpeg"

    def test_falls_back_to_the_user_local_copy(self, monkeypatch, tmp_path: Path):
        local = tmp_path / "ffmpeg"
        local.write_bytes(b"x")
        local.chmod(0o755)
        monkeypatch.setattr(detect.shutil, "which", lambda name: None)
        monkeypatch.setattr(detect, "ffmpeg_bundled_bin", lambda: None)
        monkeypatch.setattr(detect, "ffmpeg_user_bin", lambda: local)
        assert detect.find_ffmpeg() == str(local)

    def test_nothing_available_returns_none(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(detect.shutil, "which", lambda name: None)
        monkeypatch.setattr(detect, "ffmpeg_bundled_bin", lambda: None)
        monkeypatch.setattr(detect, "ffmpeg_user_bin", lambda: tmp_path / "absent")
        assert detect.find_ffmpeg() is None

    def test_source_install_has_no_bundled_copy(self):
        """``bundled_tool`` only answers inside a frozen build."""
        assert detect.ffmpeg_bundled_bin() is None

    def test_the_container_installs_ffmpeg_from_apt(self):
        """A pip install has no bundle, so the image must get ffmpeg elsewhere.

        A container filesystem is usually read-only and always discarded, so the
        runtime installer is not a fallback here: it has to be in the image.
        """
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        apt_lines = [
            line for line in dockerfile.splitlines() if "apt-get install" in line
        ]
        assert apt_lines, "no apt-get install line in the Dockerfile"
        assert any(
            re.search(r"\bffmpeg\b", line) for line in apt_lines
        ), "the image would ship without ffmpeg"

    def test_restores_the_exec_bit_an_installer_stripped(self, monkeypatch, tmp_path: Path):
        staged = tmp_path / "ffmpeg"
        staged.write_bytes(b"x")
        staged.chmod(0o644)
        monkeypatch.setattr(
            "navin.python_runtime.bundled_tool", lambda name: str(staged), raising=True
        )
        if sys.platform == "win32":
            pytest.skip("POSIX permission bits only")
        assert detect.ffmpeg_bundled_bin() == str(staged)
        assert staged.stat().st_mode & 0o111

    def test_the_bundled_ffprobe_resolves_the_same_way(self, monkeypatch, tmp_path: Path):
        staged = tmp_path / "ffprobe"
        staged.write_bytes(b"x")
        staged.chmod(0o644)
        monkeypatch.setattr(
            "navin.python_runtime.bundled_tool",
            lambda name: str(staged) if name == "ffprobe" else None,
            raising=True,
        )
        if sys.platform == "win32":
            pytest.skip("POSIX permission bits only")
        assert detect.ffprobe_bundled_bin() == str(staged)
        assert staged.stat().st_mode & 0o111

    def test_find_ffprobe_falls_back_to_the_bundle_last(self, monkeypatch):
        """A PATH ffmpeg that travels alone must still get precise probing."""
        from navin.montage import assemble

        monkeypatch.setattr("shutil.which", lambda _name: None)
        monkeypatch.setattr(
            "navin.montage.detect.ffprobe_bundled_bin",
            lambda: "/bundle/tools/ffprobe",
        )
        assert assemble.find_ffprobe("/nowhere/ffmpeg") == "/bundle/tools/ffprobe"

    def test_the_sibling_of_the_ffmpeg_in_use_still_wins(self, monkeypatch, tmp_path: Path):
        """Same-version pairing beats the bundle: an operator-pinned ffmpeg is
        measured by its own ffprobe, not by a different build's."""
        from navin.montage import assemble

        sibling = tmp_path / "ffprobe"
        sibling.write_bytes(b"x")
        monkeypatch.setattr(
            "navin.montage.detect.ffprobe_bundled_bin",
            lambda: "/bundle/tools/ffprobe",
        )
        assert assemble.find_ffprobe(str(tmp_path / "ffmpeg")) == str(sibling)


class TestMacOSArmRuntimeGap:
    def test_apple_silicon_now_has_a_download(self, monkeypatch):
        from navin.montage import install

        monkeypatch.setattr(install, "_IS_LINUX", False)
        monkeypatch.setattr(install, "_IS_WINDOWS", False)
        monkeypatch.setattr(install, "_IS_DARWIN", True)
        monkeypatch.setattr(install.platform, "machine", lambda: "arm64")
        result = install._ffmpeg_static_url()
        assert result is not None, "Apple Silicon must have a no-root fallback"
        url, kind = result
        assert url.startswith("https://")
        assert kind == "zip"

    def test_intel_still_uses_evermeet(self, monkeypatch):
        from navin.montage import install

        monkeypatch.setattr(install, "_IS_LINUX", False)
        monkeypatch.setattr(install, "_IS_WINDOWS", False)
        monkeypatch.setattr(install, "_IS_DARWIN", True)
        monkeypatch.setattr(install.platform, "machine", lambda: "x86_64")
        url, _ = install._ffmpeg_static_url()
        assert "evermeet" in url

    @pytest.mark.parametrize(
        ("machine", "fragment"),
        [("arm64", "osxexperts"), ("x86_64", "evermeet")],
    )
    def test_macos_also_gets_a_separate_ffprobe(self, monkeypatch, machine, fragment):
        """The macOS ffmpeg archives ship no ffprobe, so user-local installs
        need the companion download that Linux/Windows get for free."""
        from navin.montage import install

        monkeypatch.setattr(install, "_IS_LINUX", False)
        monkeypatch.setattr(install, "_IS_WINDOWS", False)
        monkeypatch.setattr(install, "_IS_DARWIN", True)
        monkeypatch.setattr(install.platform, "machine", lambda: machine)
        result = install._ffprobe_static_url()
        assert result is not None
        url, kind = result
        assert fragment in url
        assert "ffprobe" in url.lower()
        assert kind == "zip"

    def test_linux_needs_no_separate_ffprobe_download(self, monkeypatch):
        from navin.montage import install

        monkeypatch.setattr(install, "_IS_LINUX", True)
        monkeypatch.setattr(install, "_IS_WINDOWS", False)
        monkeypatch.setattr(install, "_IS_DARWIN", False)
        assert install._ffprobe_static_url() is None

    def test_the_user_local_archive_extraction_carries_ffprobe_along(
        self, tmp_path: Path
    ):
        from navin.montage import install

        exe = ".exe" if sys.platform == "win32" else ""
        archive = tmp_path / "bundle.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(f"ffmpeg-7.0-static/ffmpeg{exe}", b"mpeg")
            bundle.writestr(f"ffmpeg-7.0-static/ffprobe{exe}", b"probe")
        dest = tmp_path / "bin" / f"ffmpeg{exe}"
        install._extract_ffmpeg_binary(archive, "zip", dest)
        assert dest.read_bytes() == b"mpeg"
        assert dest.with_name(f"ffprobe{exe}").read_bytes() == b"probe"

    def test_an_ffmpeg_only_archive_still_installs_ffmpeg(self, tmp_path: Path):
        """ffprobe is a precision upgrade; its absence must not fail ffmpeg."""
        from navin.montage import install

        exe = ".exe" if sys.platform == "win32" else ""
        archive = tmp_path / "bundle.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(f"ffmpeg{exe}", b"mpeg")
        dest = tmp_path / "bin" / f"ffmpeg{exe}"
        install._extract_ffmpeg_binary(archive, "zip", dest)
        assert dest.read_bytes() == b"mpeg"
        assert not dest.with_name(f"ffprobe{exe}").exists()


class TestGPLCompliance:
    def test_the_licence_text_ships_with_the_app(self):
        licence = BUNDLED_LICENCES / LICENCE_NAME
        assert licence.is_file(), f"missing {licence}"
        text = licence.read_text(encoding="utf-8")
        assert "GNU GENERAL PUBLIC LICENSE" in text
        assert "Version 3" in text
        assert len(text) > 30_000

    def test_notices_declare_ffmpeg_and_the_source_offer(self):
        raw = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        # The document is hard-wrapped, so phrases straddle newlines.
        notices = " ".join(raw.split())
        assert "## FFmpeg" in notices
        assert "GNU General Public License" in notices
        # GPLv3 section 6 requires a written offer for the corresponding source.
        assert "written offer" in notices.lower()
        assert "three years" in notices
        assert "contact@navinspire.com" in notices
        assert LICENCE_NAME in notices

    def test_the_debian_source_is_declared_for_the_container(self):
        """The image redistributes Debian's ffmpeg, which needs its own pointer."""
        raw = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        notices = " ".join(raw.split())
        assert "sources.debian.org" in notices

    def test_every_shipped_notices_copy_is_in_sync(self):
        """No script syncs these, so a stale copy would ship silently."""
        canonical = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_bytes()
        copies = (
            REPO_ROOT / "webui/public/licenses/THIRD_PARTY_NOTICES.md",
            REPO_ROOT / "site/front/public/licenses/THIRD_PARTY_NOTICES.md",
            BUNDLED_LICENCES / "THIRD_PARTY_NOTICES.md",
            BUNDLED_LICENCES / "THIRD_PARTY_NOTICES.txt",
        )
        for copy in copies:
            assert copy.is_file(), f"missing {copy}"
            assert copy.read_bytes() == canonical, (
                f"{copy.relative_to(REPO_ROOT)} is stale; copy THIRD_PARTY_NOTICES.md over it"
            )

    def test_the_licence_is_served_to_the_app_and_the_site(self):
        for served in (
            REPO_ROOT / "webui/public/licenses" / LICENCE_NAME,
            REPO_ROOT / "site/front/public/licenses" / LICENCE_NAME,
        ):
            assert served.is_file(), f"missing {served}"

    def test_manifest_sources_are_documented_in_the_notices(self):
        notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        manifest = ffmpeg_vendor.load_manifest()
        for target, entry in manifest["targets"].items():
            sources = [entry, *([entry["ffprobe"]] if "ffprobe" in entry else [])]
            for source in sources:
                host = source["url"].split("/")[2]
                assert host in notices, (
                    f"{target}: build provider {host} is not attributed"
                )


class TestBundleWiring:
    def test_the_spec_ships_ffmpeg(self):
        spec = (REPO_ROOT / "packaging/pyinstaller/navin-onefile.spec").read_text()
        assert "bundle_contents.ffmpeg_data()" in spec

    def test_every_build_script_stages_ffmpeg(self):
        scripts = (
            "packaging/linux/build-offline.sh",
            "packaging/macos/build-offline.sh",
            "packaging/windows/build-offline.ps1",
        )
        for name in scripts:
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            assert "ffmpeg_vendor.py" in text, f"{name} does not stage ffmpeg"

    def test_every_build_script_gates_on_ffprobe_too(self):
        for name in (
            "packaging/linux/build-offline.sh",
            "packaging/macos/build-offline.sh",
        ):
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            assert "tools/ffprobe" in text, f"{name} does not verify ffprobe"
        windows = (REPO_ROOT / "packaging/windows/build-offline.ps1").read_text(
            encoding="utf-8"
        )
        assert "ffprobe.exe" in windows, "windows build does not verify ffprobe"

    def test_vendored_binaries_are_not_committed(self):
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "/packaging/vendor/" in ignored
