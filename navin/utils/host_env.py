# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Environment for host programs started from the packaged (frozen) Navin.

PyInstaller points the dynamic loader at the bundle's own libraries so the
frozen interpreter finds them: on Linux the bootloader prepends its directory
(``sys._MEIPASS``, ``/usr/lib/Navin/navin-dist/_internal`` in the deb, rpm and
pacman packages) to ``LD_LIBRARY_PATH``. A host program started with that
environment loads the bundle's libraries instead of its own. On a distribution
newer than the build machine (Arch, Fedora, openSUSE) git-remote-https and curl
then abort with ``version `OPENSSL_3.2.0' not found``, node and npm with
``version `LIBFFI_CALL_PLAN_8.4' not found`` (issue #15). The AppImage adds
GTK, GIO and GStreamer variables pointing into its own mount, which a host GTK
program then loads, built against another GLib.

The loader reads these variables only when a program starts, so the frozen
process keeps its own libraries once they are gone from ``os.environ``:
:func:`sanitize_process_environment` removes them at startup, before anything
is spawned, and :func:`host_tool_env` gives the same guarantee to a caller that
builds an environment from another mapping.
"""

from __future__ import annotations

import os
import ssl
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import PurePath

from loguru import logger

# The loader search paths. PyInstaller sets the first on Linux; on macOS it
# rewrites install names instead, but a runtime hook may still set a DYLD one.
_LOADER_KEYS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH")
# What the AppImage's AppRun hooks (linuxdeploy gtk and gstreamer plugins) and
# the desktop shell export, each a path or a list of paths into the mount.
_APPIMAGE_KEYS = (
    "XDG_DATA_DIRS",
    "GTK_PATH",
    "GTK_DATA_PREFIX",
    "GTK_EXE_PREFIX",
    "GTK_IM_MODULE_FILE",
    "GDK_PIXBUF_MODULE_FILE",
    "GSETTINGS_SCHEMA_DIR",
    "GIO_EXTRA_MODULES",
    "GIO_MODULE_DIR",
    "GI_TYPELIB_PATH",
    "QT_PLUGIN_PATH",
    "GST_PLUGIN_SYSTEM_PATH_1_0",
    "GST_PLUGIN_PATH_1_0",
    "GST_PLUGIN_SCANNER_1_0",
    "GST_PTP_HELPER_1_0",
)
# Where distributions keep their CA bundle (the list Go's crypto/x509 reads).
# The bundled OpenSSL looks in its build machine's place, /usr/lib/ssl, which
# Arch, Fedora and openSUSE do not have.
_HOST_CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/cert.pem",
    "/etc/pki/tls/cacert.pem",
)


def _norm(path: str) -> str:
    return os.path.normpath(path)


def _bundle_roots(env: Mapping[str, str]) -> tuple[str, ...]:
    roots: list[str] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(_norm(str(meipass)))
        roots.append(_norm(os.path.dirname(sys.executable)))
    appdir = env.get("APPDIR")
    if appdir and os.path.isabs(appdir):
        roots.append(_norm(appdir))
    return tuple(dict.fromkeys(roots))


def _anchored(part: str, roots: tuple[str, ...]) -> bool:
    path = _norm(part)
    if any(path == root or path.startswith(root.rstrip(os.sep) + os.sep) for root in roots):
        return True
    # A frozen parent's bundle seen from a process that is not that bundle (a
    # source navin started from the packaged one): the package layout names it.
    parts = PurePath(path).parts
    return any(
        name == "navin-dist" and index + 1 < len(parts) and parts[index + 1] == "_internal"
        for index, name in enumerate(parts)
    )


def _strip(value: str, roots: tuple[str, ...]) -> tuple[list[str], bool]:
    parts = [part for part in value.split(os.pathsep) if part]
    kept = [part for part in parts if not _anchored(part, roots)]
    return kept, len(kept) != len(parts)


def _clean(env: MutableMapping[str, str]) -> dict[str, str | None]:
    """Remove the bundle's entries from ``env`` in place; return what changed."""
    roots = _bundle_roots(env)
    changes: dict[str, str | None] = {}
    for key in _LOADER_KEYS:
        value = env.get(key)
        if not value:
            continue
        kept, stripped = _strip(value, roots)
        if not stripped:
            continue
        # The bootloader parks the caller's own value beside the one it wrote.
        original = env.get(f"{key}_ORIG")
        if not kept and original:
            kept, _ = _strip(original, roots)
        changes[key] = os.pathsep.join(kept) if kept else None
    if env.get("APPDIR"):
        for key in _APPIMAGE_KEYS:
            value = env.get(key)
            if not value:
                continue
            kept, stripped = _strip(value, roots)
            if stripped:
                changes[key] = os.pathsep.join(kept) if kept else None
    for key, value in changes.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return changes


def host_tool_env(base: Mapping[str, str] | None = None, **extra: str) -> dict[str, str]:
    """``base`` (the process environment by default) without the bundle's paths.

    Only the entries anchored in the bundle (or the AppImage mount) are
    dropped: anything else the user put on these paths stays. When nothing is
    left, the value the bootloader saved as ``<KEY>_ORIG`` is put back.
    ``extra`` is applied last.
    """
    env = dict(os.environ if base is None else base)
    _clean(env)
    env.update(extra)
    return env


def _ensure_ca_bundle(env: MutableMapping[str, str]) -> str | None:
    """Point OpenSSL at the host's CA bundle when its built-in place is missing.

    Without it, every in-process TLS client on the default context (smtplib,
    urllib, the OpenAI subscription login, channel checks) fails with
    ``CERTIFICATE_VERIFY_FAILED`` on Arch or Fedora. Host programs inherit the
    variable and read the very file they would read anyway.
    """
    if env.get("SSL_CERT_FILE") or env.get("SSL_CERT_DIR"):
        return None
    defaults = ssl.get_default_verify_paths()
    if (defaults.openssl_cafile and os.path.isfile(defaults.openssl_cafile)) or (
        defaults.openssl_capath and os.path.isdir(defaults.openssl_capath)
    ):
        return None
    for candidate in _HOST_CA_FILES:
        if os.path.isfile(candidate):
            env["SSL_CERT_FILE"] = candidate
            return candidate
    return None


def sanitize_process_environment(env: MutableMapping[str, str] | None = None) -> dict[str, str | None]:
    """Make the packaged Navin's environment safe to hand to host programs.

    Called once by the frozen entry point before anything is spawned. Changes
    nothing in a source install. The ``<KEY>_ORIG`` values stay, so a child
    navin restores the user's own loader path the way it always did.
    """
    if not getattr(sys, "frozen", False):
        return {}
    target = os.environ if env is None else env
    changes = _clean(target)
    if sys.platform.startswith("linux"):
        ca_file = _ensure_ca_bundle(target)
        if ca_file:
            changes["SSL_CERT_FILE"] = ca_file
    if sys.platform == "win32" and env is None and _release_windows_dll_directory():
        changes["DLL directory"] = None
    if changes:
        logger.debug("host environment: {}", ", ".join(sorted(changes)))
    return changes


# Kept for the life of the process: closing the handle would take the bundle
# off this process's own DLL search path.
_BUNDLE_DLL_DIRECTORY: object | None = None


def _release_windows_dll_directory() -> bool:
    """Stop child processes from loading the bundle's DLLs.

    The bootloader calls ``SetDllDirectoryW(_MEIPASS)``, which every child
    inherits: a host ``python.exe`` started from Navin then loaded Navin's
    ``python312.dll``, a host tool its ``libssl-3.dll`` or ``libffi-8.dll``.
    The bundle is put back on this process's own search path first, through a
    directory children do not inherit, so its extension modules still load.
    """
    global _BUNDLE_DLL_DIRECTORY
    meipass = getattr(sys, "_MEIPASS", None)
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if not meipass or add_dll_directory is None:
        return False
    import ctypes

    try:
        _BUNDLE_DLL_DIRECTORY = add_dll_directory(str(meipass))
    except OSError as exc:
        # Without it, dropping the DLL directory could break the next import.
        logger.warning("host environment: cannot add {} to the DLL search path: {}", meipass, exc)
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.SetDllDirectoryW(None):
        logger.warning("host environment: SetDllDirectoryW failed: {}", ctypes.get_last_error())
        return False
    return True


def failure_detail(raw: str | None, limit: int = 400) -> str:
    """Shorten tool output keeping both the first error and the final line.

    A plain ``[-limit:]`` slice cut the first error in half (the dialog then
    started with ``uired by /usr/lib/libcurl.so.4``).
    """
    text = (raw or "").strip()
    if len(text) <= limit:
        return text
    half = max(limit // 2, 1)
    return f"{text[:half]}\n...\n{text[-half:]}"
