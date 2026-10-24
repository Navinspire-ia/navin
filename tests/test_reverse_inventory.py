"""Reverse inventory: exhaustive extraction, engine diagnostic, bare target folders."""

from __future__ import annotations

import zipfile
from pathlib import Path

from navin.reverse import inventory, toolchain
from navin.utils.helpers import _is_bare_target_folder, sync_workspace_templates

_FAKE_ELF = (
    b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 32
    + b"/home/u/.cargo/registry/src/index.crates.io-6f17d22bba15001f/memchr-2.6.4/src/lib.rs\x00"
    + b"/home/u/.cargo/registry/src/index.crates.io-6f17d22bba15001f/regex-syntax-0.8.2/src/ast.rs\x00"
    + b"/rustc/cc66ad468955717ab92600c770da8c1601a4ff33/library/std\x00rustc-1.73.0\x00"
    + b"libpcre2-8.so.0\x00libc.so.6\x00"
    + b"    --max-count=NUM  stop after NUM\x00    --no-ignore-vcs  skip\x00 -i, --ignore-case\x00"
    + b"see https://github.com/BurntSushi/ripgrep\x00"
)


def test_inventory_lists_every_crate_option_and_library(tmp_path: Path) -> None:
    target = tmp_path / "rg"
    target.write_bytes(_FAKE_ELF)
    data = inventory.build_inventory(target)
    assert data["kind"] == "elf"
    assert data["dependencies"]["rust_crates"] == [
        {"name": "memchr", "version": "2.6.4"},
        {"name": "regex-syntax", "version": "0.8.2"},
    ]
    assert data["dependencies"]["native_libraries"] == ["libc.so.6", "libpcre2-8.so.0"]
    assert data["cli_options"]["long"] == ["--ignore-case", "--max-count", "--no-ignore-vcs"]
    assert "-i" in data["cli_options"]["short"]
    assert data["toolchain"]["rustc"] == ["1.73.0"]
    assert data["counts"]["rust_crates"] == 2 and data["counts"]["long_options"] == 3
    assert data["urls"] == ["https://github.com/BurntSushi/ripgrep"]


def test_inventory_reads_inside_an_apk(tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libnative-lib.so", b"\x7fELF" + b"\x00" * 8 + b"libssl.so.3\x00")
    data = inventory.build_inventory(apk)
    assert data["kind"] == "apk"
    assert "libnative-lib.so" in data["dependencies"]["native_libraries"]
    assert "libssl.so.3" in data["dependencies"]["native_libraries"]


def test_rea_node_range_matches_the_package_engines() -> None:
    ok = inventory.node_satisfies_rea
    assert not ok((24, 8, 0))
    assert ok((24, 11, 0)) and ok((22, 19, 1)) and ok((26, 0, 0))
    assert not ok((22, 18, 9)) and not ok((23, 5, 0)) and not ok((25, 1, 0)) and not ok(None)


def test_doctor_names_ghidra_for_native_binaries_only(monkeypatch) -> None:
    monkeypatch.setattr(inventory, "_node_version", lambda: "v24.11.1")
    monkeypatch.setattr(inventory, "_which_ghidra", lambda: None)
    monkeypatch.setattr(inventory, "_which_jadx", lambda: "/opt/jadx/bin/jadx")
    native = inventory.engine_diagnostic("elf")
    assert [p["component"] for p in native["problems"]] == ["ghidra"]
    assert native["problems"][0]["install"]
    assert inventory.engine_diagnostic("apk")["ready"] is True


def test_doctor_flags_old_node_with_an_install_command(monkeypatch) -> None:
    monkeypatch.setattr(inventory, "_node_version", lambda: "v24.8.0")
    monkeypatch.setattr(inventory, "_which_ghidra", lambda: "/opt/ghidra/support/analyzeHeadless")
    for platform in ("windows", "macos", "linux"):
        monkeypatch.setattr(inventory, "_platform_key", lambda p=platform: p)
        report = inventory.engine_diagnostic("elf")
        assert report["ready"] is False
        assert report["problems"][0]["component"] == "node"
        assert "v24.8.0" in report["problems"][0]["why"]
        assert report["problems"][0]["install"] == "python -m navin.reverse.toolchain install node"
        assert report["problems"][0]["system_install"] == inventory._INSTALL["node"][platform]


def test_every_gap_is_installed_by_the_agent(monkeypatch) -> None:
    monkeypatch.setattr(inventory, "_node_version", lambda: "v24.8.0")
    monkeypatch.setattr(inventory, "_which_ghidra", lambda: None)
    assert inventory.engine_diagnostic("elf")["auto_install"] == ["node", "ghidra"]


def test_catalog_plans_dependencies_first(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NAVIN_REVERSE_TOOLS", str(tmp_path))
    monkeypatch.setattr(toolchain, "_system_satisfies", lambda name: False)
    assert toolchain.needed_for("elf") == ["node", "jdk", "ghidra"]
    assert toolchain.needed_for("apk") == ["node", "jdk", "jadx", "apktool"]
    for system in ("windows", "macos", "linux"):
        version, url = toolchain._node_url(system, "x64")
        assert url.startswith("https://nodejs.org/dist/") and version in url
        assert "api.adoptium.net" in toolchain._jdk_url(system, "arm64")[1]


def test_install_unpacks_privately_and_exposes_env(monkeypatch, tmp_path: Path) -> None:
    import io
    import tarfile

    root = tmp_path / "tools"
    monkeypatch.setenv("NAVIN_REVERSE_TOOLS", str(root))
    monkeypatch.setattr(toolchain, "_system_satisfies", lambda name: False)
    monkeypatch.setattr(toolchain, "CATALOG", {
        "jdk": toolchain.Tool("jdk", "jdk", ("elf",), ("bin/java",), resolve=lambda s, a: ("21", "u-jdk")),
        "ghidra": toolchain.Tool("ghidra", "g", ("elf",), ("support/analyzeHeadless",), requires=("jdk",),
                                 resolve=lambda s, a: ("11.4", "u-ghidra")),
    })
    layout = {"u-jdk": "jdk-21.0.5/bin/java", "u-ghidra": "ghidra_11.4_PUBLIC/support/analyzeHeadless"}

    def fake_download(url: str, dest: Path) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"#!/bin/sh\n"
            info = tarfile.TarInfo(layout[url])
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        dest.write_bytes(buf.getvalue())

    monkeypatch.setattr(toolchain, "_download", fake_download)
    toolchain.install("ghidra", log=lambda _m: None)
    assert (root / "jdk" / "bin" / "java").exists()
    assert (root / "ghidra" / "support" / "analyzeHeadless").exists()
    env = toolchain.tool_env({"PATH": "/usr/bin"})
    assert env["JAVA_HOME"] == str(root / "jdk")
    assert env["GHIDRA_INSTALL_DIR"] == str(root / "ghidra")
    assert env["PATH"].startswith(str(root / "jdk" / "bin"))
    assert toolchain.needed_for("elf") == []


def test_archive_cannot_escape_the_tools_folder(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(RuntimeError):
        toolchain._safe_members(["../evil"], tmp_path)


def test_tar_symlink_escaping_dest_is_refused(tmp_path: Path) -> None:
    import io
    import tarfile

    import pytest

    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        link = tarfile.TarInfo("tool/escape")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc"
        tf.addfile(link)
        data = b"x"
        info = tarfile.TarInfo("tool/escape/pwned")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(Exception):
        toolchain._extract(archive, tmp_path / "out")
    assert not (Path("/etc") / "pwned").exists()


def test_zip_setuid_bit_is_stripped(tmp_path: Path) -> None:
    archive = tmp_path / "t.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("tool/bin")
        info.external_attr = (0o104755) << 16
        zf.writestr(info, b"#!/bin/sh\n")
    toolchain._extract(archive, tmp_path / "out")
    mode = (tmp_path / "out" / "tool" / "bin").stat().st_mode
    assert mode & 0o7777 == 0o755


def test_bare_binary_folder_gets_no_continuity_pack(tmp_path: Path) -> None:
    (tmp_path / "rg").write_bytes(_FAKE_ELF)
    assert _is_bare_target_folder(tmp_path)
    assert sync_workspace_templates(tmp_path, silent=True) == []
    assert not (tmp_path / ".navin").exists()


def test_real_projects_still_get_the_pack(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
    assert not _is_bare_target_folder(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not _is_bare_target_folder(empty)
    with_dir = tmp_path / "withdir"
    (with_dir / "src").mkdir(parents=True)
    (with_dir / "blob.bin").write_bytes(b"\x00")
    assert not _is_bare_target_folder(with_dir)


def test_doctor_asks_for_jadx_on_apk(monkeypatch) -> None:
    monkeypatch.setattr(inventory, "_node_version", lambda: "v24.11.1")
    monkeypatch.setattr(inventory, "_which_jadx", lambda: None)
    report = inventory.engine_diagnostic("apk")
    assert [p["component"] for p in report["problems"]] == ["jadx"]
    assert report["problems"][0]["install"] == "python -m navin.reverse.toolchain install jadx"


def test_system_install_commands_name_real_packages() -> None:
    ghidra = inventory._INSTALL["ghidra"]
    assert ghidra["macos"] == "brew install ghidra"  # formula, not a cask
    assert "NationalSecurityAgency.Ghidra" not in ghidra["windows"]  # no winget package
    assert "Temurin.21.JDK" in ghidra["windows"] and "openjdk-21" in ghidra["linux"]
    assert inventory._INSTALL["jadx"]["macos"] == "brew install jadx"
    assert "nvm install 24" in inventory._INSTALL["node"]["linux"]
    assert "install.sh" in inventory._INSTALL["node"]["linux"]  # nvm itself is installed first
    for tool in inventory._INSTALL.values():
        assert set(tool) == {"windows", "macos", "linux"}


def test_pip_tools_use_external_python_in_packaged_build(tmp_path: Path, monkeypatch) -> None:
    import navin.python_runtime as rt

    monkeypatch.setenv("NAVIN_REVERSE_TOOLS", str(tmp_path))
    monkeypatch.setattr(rt, "external_python", lambda: None)
    try:
        toolchain.install("frida", log=lambda _m: None)
    except RuntimeError as exc:
        assert "Python 3 on PATH" in str(exc)
    else:
        raise AssertionError("expected a clear error without an interpreter")
    calls: list[list[str]] = []
    monkeypatch.setattr(rt, "external_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(toolchain.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or
                        type("R", (), {"stdout": "Version: 14.11.0\n"})())
    entry = toolchain.install("frida", log=lambda _m: None)
    assert calls[0][:3] == ["/usr/bin/python3", "-m", "venv"]
    assert entry["version"] == "14.11.0"


def test_catalog_covers_the_twelve_engines() -> None:
    wanted = {"ghidra", "ida", "binaryninja", "frida", "radare2", "jadx", "apktool",
              "ilspy", "playwright", "mitmproxy", "wireshark", "retdec"}
    assert wanted <= set(toolchain.CATALOG)
    for name in wanted:
        tool = toolchain.CATALOG[name]
        for system in ("windows", "macos", "linux"):
            installable = tool.method == "pip" or system in tool.platforms
            assert installable or tool.manual.get(system), (name, system)


def test_commercial_and_system_tools_refuse_with_a_hint(monkeypatch) -> None:
    monkeypatch.setattr(toolchain, "host", lambda: ("linux", "x64"))
    for name, needle in (("ida", "hex-rays"), ("wireshark", "apt-get install -y tshark"), ("retdec", None)):
        if name == "retdec":
            continue
        try:
            toolchain.install(name, log=lambda _m: None)
        except RuntimeError as exc:
            assert needle in str(exc)
        else:
            raise AssertionError(name)
    monkeypatch.setattr(toolchain, "host", lambda: ("windows", "x64"))
    try:
        toolchain.install("retdec", log=lambda _m: None)
    except RuntimeError as exc:
        assert ".7z" in str(exc)


def test_apktool_jar_installs_as_single_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NAVIN_REVERSE_TOOLS", str(tmp_path))
    monkeypatch.setattr(toolchain, "is_available", lambda name: True)
    monkeypatch.setattr(toolchain, "_apktool_url", lambda s, a: ("v3.0.3", "https://x/apktool_3.0.3.jar"))
    monkeypatch.setitem(toolchain.CATALOG, "apktool", toolchain.CATALOG["apktool"].__class__(
        **{**toolchain.CATALOG["apktool"].__dict__, "resolve": toolchain._apktool_url}))
    monkeypatch.setattr(toolchain, "_download", lambda url, dest: dest.write_bytes(b"PK"))
    entry = toolchain.install("apktool", log=lambda _m: None)
    assert (Path(entry["home"]) / "apktool.jar").read_bytes() == b"PK"
    assert toolchain.installed_home("apktool") == tmp_path / "apktool"


def test_release_asset_matchers_pick_real_names(monkeypatch) -> None:
    names = ["RetDec-v5.0-Linux-Release.tar.xz", "RetDec-v5.0-macOS-Release.tar.xz",
             "ILSpy_linux-x64_11.1.0.9782.zip", "ILSpy_windows_11.1.0.9782-x64.zip",
             "ILSpy_windows_selfcontained_11.1.0.9782-x64.zip", "radare2-6.2.4-w64.zip",
             "radare2-6.2.4-w64-arm64.zip", "apktool_3.0.3.jar"]
    monkeypatch.setattr(toolchain, "_github_asset",
                        lambda repo, match: ("t", next(n for n in names if match(n))))
    assert toolchain._retdec_url("linux", "x64")[1] == names[0]
    assert toolchain._ilspy_url("windows", "x64")[1] == names[3]
    assert toolchain._radare2_url("windows", "x64")[1] == names[5]
    assert toolchain._radare2_url("windows", "arm64")[1] == names[6]
    assert toolchain._apktool_url("linux", "x64")[1] == names[7]
