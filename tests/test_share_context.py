# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The picture a share guest or a bundle reader gets: dates, project, git, prompts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from navin.share.context import context_markdown, session_context


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_context_carries_git_history_changes_and_prompts_without_secrets(tmp_path):
    repo = tmp_path / "shop"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev One")
    (repo / "package.json").write_text(json.dumps({"scripts": {"dev": "vite", "test": "vitest run"}}))
    (repo / "README.md").write_text("# Shop\n\nA small shop.")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "first commit")
    _git(repo, "checkout", "-b", "feature/cart")
    _git(repo, "remote", "add", "origin", "https://user:ghp_secret@github.com/acme/shop.git")
    (repo / "cart.ts").write_text("export const cart = [];\n")

    session = tmp_path / "s.jsonl"
    rows = [
        {"_type": "metadata", "created_at": "2026-10-01T09:00:00", "updated_at": "2026-10-04T10:00:00",
         "metadata": {"title": "Panier"}},
        {"role": "user", "content": "Ajoute un panier", "timestamp": "2026-10-01T09:00:01"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "write_file", "arguments": json.dumps({"path": "cart.ts", "content": "x"})}}]},
        {"role": "assistant", "content": "Panier ajouté dans cart.ts."},
    ]
    session.write_text("\n".join(json.dumps(r) for r in rows))

    ctx = session_context(session, repo)
    git = ctx["git"]
    assert git["branch"] == "feature/cart" and git["commits"][0]["subject"] == "first commit"
    assert git["commits"][0]["author"] == "Dev One" and "example.com" not in json.dumps(ctx)
    assert git["remote"] == "https://github.com/acme/shop.git"
    assert {"status": "??", "path": "cart.ts"} in git["changes"]
    assert ctx["project"]["stack"] == ["Node.js"] and "npm run test" in ctx["project"]["commands"]
    conv = ctx["conversation"]
    assert conv["title"] == "Panier" and conv["prompts"][0]["text"] == "Ajoute un panier"
    assert conv["files_touched"] == ["cart.ts"] and conv["last_reply"].startswith("Panier")

    page = context_markdown(ctx)
    assert "# Panier" in page and "feature/cart" in page and "first commit" in page and "ghp_secret" not in page


def test_context_without_git_or_session_still_answers(tmp_path):
    ctx = session_context(None, tmp_path)
    assert ctx["git"] is None and ctx["conversation"] is None and ctx["project"]["name"] == tmp_path.name
    assert context_markdown(ctx).startswith("# ")
