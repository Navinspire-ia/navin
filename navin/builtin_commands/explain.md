---
description: Explain a file, a symbol or a piece of the codebase, read-only
argument-hint: "<path, symbol or question>"
allowed-tools: read_file, grep, find_files, code_index, lsp, metagraph, git, list_dir
---

# Explain

Subject: "$ARGUMENTS"

This is a **read-only** turn: no edit, no shell that mutates anything.

1. Locate the subject: a path -> `read_file`; a symbol -> `code_index action=definition` then `callers` and `references`; a question -> `code_index action=semantic` then read the top files.
2. Follow the data: where the inputs come from, what the code does with them, where the results go, and which other modules depend on it (`metagraph action=file`). Check `git action=log paths=[...]` when the "why" matters: recent commits often carry the intent.
3. Answer in the user's language, in this order: one-paragraph summary, how it works step by step (with `file:line` anchors), the non-obvious decisions and gotchas, and what to look at next. Quote only the lines that matter, never a whole file.
4. If something is unclear or looks like a bug, say so as a hypothesis with its evidence; do not fix it here.
