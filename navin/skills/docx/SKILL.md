---
name: docx
description: Works on existing Word (.docx) files at the package level with python-docx and lxml. Dumps and replaces text while keeping run formatting, adds or lists comments, accepts, rejects or creates tracked changes, unpacks, repacks and validates the OOXML package, converts with headless LibreOffice. Use when the user asks to "edit this Word file", "add comments to the docx", "accept all tracked changes", "replace the text in the contract" or "check why this docx is corrupted". In French "modifie ce fichier Word", "ajoute des commentaires dans ce document", "accepte les modifications suivies" or "remplace le texte du contrat". New documents from scratch belong to docx-generator.
metadata: {"navin":{"emoji":"📝","category":"documents"}}
---

# DOCX Toolkit

## Overview

This skill edits Word files that already exist: contracts, reports, templates sent by the user. It preserves what is there (styles, numbering, headers, images, revision history) and changes only what was asked. All scripts run with `exec` and the bundled libraries (python-docx, lxml); nothing has to be installed first.

Pick the sibling skill when the job is different:

- `docx-generator`: write a new report, letter or proposal from a brief or an HTML template.
- `pdf-generator`: the deliverable must be a PDF rendered from HTML.
- `pdf`: the input is a PDF, not a Word file.

## Workflow

### 1. Read before touching

1. Dump the text with `python3 navin/skills/docx/scripts/docx_text.py dump input.docx --format markdown` (or `--format json` for paragraph indexes, style names and table flags). Read the result with `read_file` when it is long.
2. Count revisions and comments: `python3 navin/skills/docx/scripts/docx_track.py count input.docx --json` and `python3 navin/skills/docx/scripts/docx_comments.py list input.docx`. A document with pending revisions must be accepted or rejected first when the user wants a clean copy.
3. Validate the package when Word complains or when the file comes from an unknown generator: `python3 navin/skills/docx/scripts/office_pkg.py validate input.docx --json`.

### 2. Edit

Always write to a new file with `--out edited.docx` unless the user explicitly wants the original overwritten.

- Plain text replacement that keeps bold, italics and fonts of the matched run: `docx_text.py replace input.docx --find "Old name" --with "New name" --all --out edited.docx`.
- Legal or review context, where the change must stay visible: `docx_track.py replace input.docx --find "30 days" --with "45 days" --author "Navin" --out edited.docx` records a tracked deletion followed by a tracked insertion.
- Comments for a reviewer: `docx_comments.py add input.docx --anchor "Payment terms" --text "Confirm the due date with finance." --author "Navin" --out edited.docx` anchors the comment on the first paragraph containing the anchor text.
- Accept or reject everything: `docx_track.py accept input.docx --out clean.docx` keeps insertions and drops deletions; `reject` does the opposite. Both clear formatting-change records in body, headers, footers, footnotes and endnotes.

### 3. Package-level surgery

When python-docx has no API for the change (content controls, custom XML, numbering restart, header images, broken relationships):

1. `office_pkg.py unpack input.docx work/` extracts the parts with pretty-printed XML.
2. Edit `work/word/document.xml` (or styles.xml, numbering.xml, header1.xml) with `edit_file`. Keep `w:r` runs intact; a text node always lives inside `w:t` inside `w:r` inside `w:p`.
3. `office_pkg.py pack work/ output.docx` zips the folder back with `[Content_Types].xml` first and minified XML.
4. `office_pkg.py validate output.docx` before delivering: zip integrity, every content type and relationship target present, well-formed XML, python-docx round trip.

Read `references/ooxml-anatomy.md` first: it maps the parts, the paragraph and run model, tracked-change and comment markup, and the pitfalls that break Word.

### 4. Convert

`python3 navin/skills/docx/scripts/office_convert.py edited.docx --to pdf --out-dir out/` drives headless LibreOffice. The script exits with a clear message when `soffice` is missing: say so instead of silently skipping the PDF, and offer the `pdf-generator` route if the content can be re-rendered from HTML.

### 5. Deliver

Re-run `docx_text.py dump` on the result and quote the changed paragraphs to the user. Attach the file. State what was not changed (styles, images, other sections) so the user knows the scope.

## Scripts

| Script | Command |
|--------|---------|
| `scripts/docx_text.py` | `dump FILE [--format json\|markdown] [--no-tables]`; `replace FILE --find T --with T [--all] [--out F]` |
| `scripts/docx_comments.py` | `list FILE`; `add FILE --anchor T --text T [--author A] [--initials I] [--out F]` |
| `scripts/docx_track.py` | `accept\|reject FILE [--out F]`; `replace FILE --find T --with T [--author A] [--all] [--out F]`; `count FILE [--json]` |
| `scripts/office_pkg.py` | `unpack ARCHIVE DIR [--no-pretty]`; `pack DIR ARCHIVE [--no-minify]`; `validate ARCHIVE [--json] [--no-roundtrip]` |
| `scripts/office_convert.py` | `SOURCE --to pdf\|docx\|html\|pptx\|xlsx [--out-dir D] [--timeout S] [--json]` |

Exit codes: 0 success, 1 nothing matched or validation failed, 2 usage or I/O error, 3 LibreOffice missing.

## Rules

- Language gate: when new text is written into the document (comments, inserted sentences, headings), ask for the document language if the user did not state it, and keep the language of the surrounding text.
- Never put em dashes (U+2014) or en dashes (U+2013) in produced text. Use "-" or rephrase.
- Keep the file editable: no screenshots pasted in place of text, no flattening to PDF unless asked.
- Preserve run formatting: replace inside runs, do not rebuild paragraphs from scratch.
- Tracked changes are the default in legal, HR and contract documents; silent edits are the default in drafts and templates. Ask when unsure.
- Validate every file produced by package-level edits before delivering it; a corrupted docx is worse than no file.
- Report partial results honestly: `exit 1` from `replace` means the text was not found; say it and show the closest paragraphs from the dump.

## References

- `references/ooxml-anatomy.md`: package parts, paragraph and run model, styles and numbering, headers and footers, tracked changes and comments markup, common pitfalls.
