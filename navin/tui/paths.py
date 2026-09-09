# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""File-path detection for TUI coloring.

Inline markdown used to paint every backtick span in brand blue, including
shell commands. Only real paths and filenames get a distinct ink.
"""

from __future__ import annotations

import os
import re

# Sage green: readable on black, not neon. Light theme CSS uses a darker forest.
PATH_INK = "#8FBC8F"

_FILE_EXTS = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".json",
        ".jsonc",
        ".toml",
        ".yaml",
        ".yml",
        ".md",
        ".mdx",
        ".rst",
        ".txt",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".html",
        ".htm",
        ".vue",
        ".svelte",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".kts",
        ".swift",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".m",
        ".mm",
        ".rb",
        ".php",
        ".cs",
        ".fs",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
        ".cmd",
        ".sql",
        ".graphql",
        ".proto",
        ".tf",
        ".hcl",
        ".nix",
        ".lock",
        ".env",
        ".xml",
        ".svg",
        ".nsi",
        ".nsh",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".app",
        ".dmg",
        ".msi",
        ".deb",
        ".rpm",
        ".zip",
        ".gz",
        ".tgz",
    }
)

_DOTFILES = frozenset({".env", ".gitignore", ".dockerignore", ".editorconfig"})
_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_COMMAND_WORDS = frozenset(
    {
        "apt",
        "apt-get",
        "bash",
        "brew",
        "bun",
        "cargo",
        "cat",
        "cd",
        "chmod",
        "chown",
        "cmake",
        "cp",
        "curl",
        "deno",
        "dnf",
        "docker",
        "echo",
        "env",
        "export",
        "git",
        "go",
        "helm",
        "kubectl",
        "ln",
        "ls",
        "make",
        "mkdir",
        "mv",
        "navin",
        "navin-cli",
        "node",
        "npm",
        "npx",
        "pacman",
        "perl",
        "php",
        "pip",
        "pip3",
        "pnpm",
        "pwd",
        "python",
        "python3",
        "rm",
        "rsync",
        "ruby",
        "scp",
        "sh",
        "ssh",
        "sudo",
        "tar",
        "terraform",
        "touch",
        "unzip",
        "uv",
        "wget",
        "yarn",
        "yum",
        "zip",
        "zsh",
    }
)


def looks_like_path(value: str) -> bool:
    """True when ``value`` is a filesystem path or a bare filename, not a command."""
    text = (value or "").strip().strip("`\"'")
    if not text or "\n" in text:
        return False
    if "://" in text:
        return False
    if "&&" in text or "||" in text or "$(" in text or "`" in text:
        return False
    if " | " in text or "; " in text:
        return False
    if " " in text:
        first = text.split(None, 1)[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        if first in _COMMAND_WORDS or first.endswith(".exe"):
            return False
        return False
    if text.startswith(("/", "./", "../", "~/", "~\\")):
        return True
    if text == "~":
        return True
    if _WIN_DRIVE.match(text) or text.startswith("\\\\"):
        return True
    if "/" in text or "\\" in text:
        return True
    base = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if base.lower() in _DOTFILES:
        return True
    root, ext = os.path.splitext(base)
    if not root or not ext:
        return False
    return ext.lower() in _FILE_EXTS
