---
description: Update the documentation (README, docs/, docstrings) to match the uncommitted changes
argument-hint: "[base ref, default: uncommitted changes]"
allowed-tools: git, read_file, grep, find_files, code_index, edit_file, apply_patch, write_file
---

# Update the docs

Base requested by the user (may be empty): "$ARGUMENTS"

## 1. What changed

`git action=diff` (against the base when given) and `git action=status`. List every user-visible change: new or renamed command, option, environment variable, API endpoint, config key, behaviour, default value, removed feature.

## 2. Where it is documented

For each change, `grep` the old name or wording in `README*`, `docs/`, `CHANGELOG.md`, `*.md` next to the code, CLI `--help` strings, docstrings and inline examples. Also check the places that *should* mention it (a features list, an options table, a quick start).

## 3. Edit

- Update the existing text in place; keep the document's tone, structure and language. Do not add a new section when a sentence in an existing one does the job.
- Code samples must match the code: copy the real signature or command, do not paraphrase it.
- Fix a docstring only when the behaviour it describes changed.
- Never document something the code does not do yet.

## 4. Report

List the files touched and, for each, what was corrected. Say explicitly when a change needed no documentation. Do not commit.
