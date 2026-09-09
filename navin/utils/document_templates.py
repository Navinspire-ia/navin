# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTML document template library (PPT, Word, PDF, Excel).

Templates live in ``<repo>/templates/<category>/<name>/`` (root overridable
with the ``NAVIN_PRESENTATION_TEMPLATES_DIR`` environment variable). Each
template folder contains:

- ``ppt``: ``slide_XX.html`` masters (1920x1080), one file per slide;
- ``word`` / ``pdf``: ``document.html`` with A4 ``.page`` sections;
- ``excel``: ``document.html`` mocking the sheet layout (header styles,
  groupings, conditional formatting);
- ``metadata.json`` - title/description;
- ``image.png`` - preview thumbnail shown in the WebUI picker;
- optional assets (``uploads/``…) referenced by the HTML.

The WebUI lists templates by category (:func:`list_templates_payload`),
serves their files (:func:`template_file`), and attaches the user's pick to
the message metadata; :func:`document_template_context_provider` then tells
the agent which template to use and how to deliver the final file.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from navin.python_runtime import python_command
from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from navin.utils.workspace_resources import prepare_workspace_resources

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

DOCUMENT_TEMPLATE_METADATA_KEY = "document_template"

CATEGORIES: tuple[str, ...] = ("ppt", "word", "pdf", "excel")

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_PREVIEW_FILENAME = "image.png"
_PAGE_MARKER_RE = re.compile(r"<\w+[^>]*class=\"[^\"]*\b(?:page|sheet)\b")
_OPTIONAL_STRING_METADATA = (
    "kind",
    "language_mode",
    "text_policy",
    "image_text_policy",
    "design_system",
)
_OPTIONAL_LIST_METADATA = ("output_formats",)
_WORKSPACE_RESOURCE_FOLDER = Path("document-templates")
_WORKSPACE_TOOLS_FOLDER = Path("tools")
_CONVERTER_SOURCE = Path(__file__).resolve().parent.parent / "documents"

_CATEGORY_GUIDANCE: dict[str, str] = {
    "ppt": (
        "Each slide_XX.html is a 1920x1080 lookbook sample. Do not copy it "
        "into a new deck. Write semantic JSON (layout, title, items, image) "
        "and render with python -m navin.documents.ppt_design deck "
        "--slides deck.json -o slides/. The engine places text and images. "
        "A content slide needs at least three items, a process, a KPI, or "
        "a real photo. Title plus one sentence is a rejected slide. "
        "A deck of three or more content slides gets a sommaire (agenda) "
        "after the cover, built from the slide titles. "
        "Never invent coordinates. Never hand-write HTML: leftover sample "
        "copy and image.png are how decks look broken. Convert with "
        "html2pptx. See skills/pptx-generator/SKILL.md and "
        "skills/presentation-designer/SKILL.md."
    ),
    "word": (
        "document.html is the lookbook sample. For a report, letter, "
        "proposal or brief, write semantic JSON and render with "
        "python -m navin.documents.word_design render --document doc.json "
        "-o document.html. Do not copy the sample and edit it by hand: "
        "leftover KPIs and image.png are how Word docs look broken. A "
        "report or proposal gets a Sommaire page after the cover "
        "(data-doc-toc). Letters and briefs do not. Then "
        "convert the finished file into a .docx whose headings, paragraphs, "
        "lists and tables stay editable in Word. Tag Word-only behaviour with "
        "data-doc-* (data-doc-toc, data-doc-section=landscape, "
        "data-doc-caption, data-doc-bookmark, data-doc-ref, "
        "data-doc-numbering, data-doc-break, data-doc-keep, data-doc-cover) "
        "instead of inventing coordinates. Colours and fonts live only in the "
        "<style id=\"navin-theme\"> block: write var(--nv-accent) and friends, "
        "never a hex, so the document can change theme. Restyle with "
        "python -m navin.documents.word_design apply --theme <name> file.html "
        "(15 themes; `themes` lists them, `audit` fails on any literal colour "
        "left behind). _engine/navin-word.css holds ready components: "
        "callouts, KPI, insight blocks, figures, signature blocks. Never "
        "rebuild the design by hand and never paste a rendered page as an "
        "image. See skills/docx-generator/SKILL.md (or render the HTML to PDF "
        "if the user asked for a PDF)."
    ),
    "pdf": (
        "document.html contains A4 .page sections with the design in the "
        "<style id=\"navin-theme\"> block. Copy it into the workspace, keep the "
        "CSS, replace every visible sample string with the real content, add or "
        "remove .page sections as the content needs (one section prints as one "
        "sheet; a section longer than A4 spills and is reported). Colours and "
        "fonts stay tokens (var(--nv-accent)...); never write a hex in the body. "
        "A contract or a legal document keeps the legal notice block. Then print "
        "it: the bundled html2pdf command below runs headless Chromium, refuses a "
        "page under the quality threshold and reads the PDF back (page count, "
        "text layer, title). See skills/pdf-generator/SKILL.md."
    ),
    "excel": (
        "document.html mocks the spreadsheet layout (header row styling, "
        "groupings, totals, conditional formatting). Fill it with the real "
        "data, then convert it into a live .xlsx: real cells, real number "
        "formats, SUM formulas on total rows. Add `--csv <folder>` when a CSV "
        "is wanted. Adjust the result with openpyxl if something is missing, "
        "never rebuild the whole sheet by hand. See "
        "skills/spreadsheet-analyst/SKILL.md."
    ),
}

# category -> (converter module, argument template shown to the agent)
_CONVERTER_USAGE: dict[str, tuple[str, str]] = {
    "ppt": ("html2pptx", "<slides-folder> -o deck.pptx"),
    "word": ("html2docx", "<filled-document.html> -o document.docx"),
    "excel": ("html2xlsx", "<filled-document.html> -o workbook.xlsx"),
    "pdf": ("html2pdf", "<filled-document.html> -o document.pdf"),
}

# category -> (quality module, argument template). Read before converting: that
# is the stage where a finding is still one edit away from being fixed.
_QA_USAGE: dict[str, tuple[str, str]] = {
    "ppt": ("ppt_qa", "<slides-folder>"),
    "word": ("word_qa", "<filled-document.html>"),
    "pdf": ("word_qa", "<filled-document.html>"),
}

# category -> the file the final checks run on.
_FINAL_FILE: dict[str, str] = {
    "ppt": "deck.pptx",
    "word": "document.docx",
    "excel": "workbook.xlsx",
    "pdf": "document.pdf",
}


def templates_root() -> Path | None:
    """Template library directory, or None when it does not exist."""
    override = os.environ.get("NAVIN_PRESENTATION_TEMPLATES_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_dir() else None
    package_root = Path(__file__).resolve().parents[1]
    candidates = [
        Path(__file__).resolve().parents[2] / "templates",
        package_root / "resources" / "templates",
    ]
    # Frozen builds (PyInstaller) bundle the library under _MEIPASS/templates.
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        frozen_root = Path(meipass)
        candidates.extend(
            (
                frozen_root / "templates",
                frozen_root / "navin" / "resources" / "templates",
            )
        )
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "templates")
    for path in candidates:
        if path.is_dir():
            return path
    return None


def _template_metadata(directory: Path) -> dict[str, Any]:
    try:
        with open(directory / "metadata.json", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _display_title(name: str, metadata: dict[str, Any]) -> str:
    title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return name.replace("_", " ").replace("-", " ").title()


def _template_dir(category: str, name: str) -> Path | None:
    root = templates_root()
    if root is None or category not in CATEGORIES:
        return None
    if not _NAME_RE.match(name):
        return None
    resolved = name
    if category == "ppt":
        # Retired picker names map onto their replacement, but existence on
        # disk decides: a custom or test template root must stay usable even
        # when its folders are not in the bundled theme catalog.
        try:
            from navin.documents import ppt_design

            resolved = ppt_design.resolve_theme(name)
        except Exception:
            resolved = name
        if not (root / category / resolved).is_dir():
            resolved = name
    directory = root / category / resolved
    return directory if directory.is_dir() else None


def _count_pages(document: Path) -> int:
    try:
        html = document.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 1
    return max(1, len(_PAGE_MARKER_RE.findall(html)))


def template_entry(category: str, name: str) -> dict[str, Any] | None:
    """Catalog entry for one template, or None when unknown/invalid."""
    directory = _template_dir(category, name)
    if directory is None:
        return None
    if category == "ppt":
        files = sorted(p.name for p in directory.glob("slide_*.html") if p.is_file())
        item_count = len(files)
    else:
        document = directory / "document.html"
        files = ["document.html"] if document.is_file() else []
        item_count = _count_pages(document) if files else 0
    if not files:
        return None
    metadata = _template_metadata(directory)
    entry: dict[str, Any] = {
        "category": category,
        "name": directory.name,
        "title": _display_title(name, metadata),
        "item_count": item_count,
        "files": files,
    }
    description = metadata.get("description")
    if isinstance(description, str) and description.strip():
        entry["description"] = description.strip()
    for key in _OPTIONAL_STRING_METADATA:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            entry[key] = value.strip()
    for key in _OPTIONAL_LIST_METADATA:
        value = metadata.get(key)
        if isinstance(value, list):
            normalized = [
                item.strip()
                for item in value
                if isinstance(item, str) and item.strip()
            ]
            if normalized:
                entry[key] = normalized
    modules = metadata.get("modules")
    if isinstance(modules, list):
        normalized_modules: list[dict[str, Any]] = []
        for module in modules:
            if not isinstance(module, Mapping):
                continue
            module_id = module.get("id")
            title = module.get("title")
            if not isinstance(module_id, str) or not module_id.strip():
                continue
            if not isinstance(title, str) or not title.strip():
                continue
            normalized_module: dict[str, Any] = {
                "id": module_id.strip(),
                "title": title.strip(),
            }
            if isinstance(module.get("required"), bool):
                normalized_module["required"] = module["required"]
            normalized_modules.append(normalized_module)
        if normalized_modules:
            entry["modules"] = normalized_modules
    if (directory / _PREVIEW_FILENAME).is_file():
        entry["preview_url"] = (
            f"/api/document-templates/{category}/{name}/{_PREVIEW_FILENAME}"
        )
    return entry


def list_templates_payload() -> dict[str, Any]:
    """WebUI payload listing every usable template, grouped by category."""
    root = templates_root()
    categories: list[dict[str, Any]] = []
    for category in CATEGORIES:
        templates: list[dict[str, Any]] = []
        category_dir = root / category if root is not None else None
        if category_dir is not None and category_dir.is_dir():
            official_ppt = None
            if category == "ppt":
                from navin.documents import ppt_design

                official_ppt = set(ppt_design.theme_names())
            for directory in sorted(category_dir.iterdir()):
                if not directory.is_dir() or directory.name.startswith((".", "_")):
                    continue
                if official_ppt is not None and directory.name not in official_ppt:
                    continue
                entry = template_entry(category, directory.name)
                if entry is not None:
                    templates.append(entry)
        templates.sort(key=lambda entry: str(entry.get("title", "")).lower())
        categories.append({"id": category, "templates": templates})
    return {"categories": categories}


def template_file(category: str, name: str, relative: str) -> tuple[bytes, str] | None:
    """Read one file inside a template folder: ``(body, content_type)``.

    Rejects traversal outside the template directory.
    """
    directory = _template_dir(category, name)
    if directory is None:
        return None
    if not relative or relative.startswith(("/", "\\")) or ".." in relative.split("/"):
        return None
    target = (directory / relative).resolve()
    try:
        target.relative_to(directory.resolve())
    except ValueError:
        return None
    if not target.is_file():
        return None
    try:
        body = target.read_bytes()
    except OSError:
        return None
    content_type, _ = mimetypes.guess_type(target.name)
    if content_type is None:
        content_type = "application/octet-stream"
    if content_type.startswith("text/"):
        content_type = f"{content_type}; charset=utf-8"
    return body, content_type


def normalize_document_template_mention(value: Any) -> dict[str, Any] | None:
    """Validate one template mention from the WS envelope.

    Accepts ``{"category": ..., "name": ...}`` (or a ``"category/name"``
    string); returns a ``{"category", "name", "title"}`` snapshot only when
    the template exists on disk.
    """
    category = ""
    name = ""
    if isinstance(value, str):
        category, _, name = value.strip().partition("/")
    elif isinstance(value, Mapping):
        if isinstance(value.get("category"), str):
            category = str(value["category"]).strip()
        if isinstance(value.get("name"), str):
            name = str(value["name"]).strip()
    if not category or not name:
        return None
    entry = template_entry(category, name)
    if entry is None:
        return None
    mention = {
        "category": entry["category"],
        "name": entry["name"],
        "title": entry["title"],
    }
    for key in (*_OPTIONAL_STRING_METADATA, *_OPTIONAL_LIST_METADATA, "modules"):
        if key in entry:
            mention[key] = entry[key]
    if isinstance(value, Mapping):
        language = value.get("language")
        if isinstance(language, str) and language.strip():
            mention["language"] = language.strip()[:100]
    return mention


def materialize_document_template(
    mention: Mapping[str, Any],
    workspace: Path,
) -> Path | None:
    """Copy one trusted bundled template into the active project workspace.

    Agent tools can be intentionally restricted to the selected user project.
    Bundled Navin resources therefore cannot be consumed reliably by exposing
    their installation path. A per-project materialized copy gives every tool,
    including sandboxed shell commands, the same workspace-local path.
    """
    category = mention.get("category")
    name = mention.get("name")
    if not isinstance(category, str) or not isinstance(name, str):
        return None
    source = _template_dir(category, name)
    if source is None:
        return None
    resources = prepare_workspace_resources(workspace)
    if resources is None:
        return None
    destination = resources / _WORKSPACE_RESOURCE_FOLDER / category / name
    try:
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                ".DS_Store",
            ),
        )
        _materialize_engine(category, destination.parent)
    except OSError:
        return None
    return destination


def _materialize_engine(category: str, category_root: Path) -> None:
    """Copy the category's shared design system next to the selected template.

    The engine belongs to the whole category rather than to one template: PPT
    keeps its master layouts and component CSS there, Word its theme tokens and
    component library. Materializing it is what lets a sandboxed shell run
    ``word_design.py apply`` or read navin-word.css from the workspace.
    """
    engine = source_engine(category)
    if engine is None:
        return
    target = category_root / "_engine"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        engine,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "generate.py"),
    )


def _ensure_engine(category: str, category_root: Path) -> None:
    """Materialize the engine once; refresh it only when the source is newer.

    Called on every document turn, so it must not rewrite a folder a running
    converter may be reading.
    """
    engine = source_engine(category)
    if engine is None:
        return
    target = category_root / "_engine"
    try:
        if target.is_dir():
            newest_source = max(
                (p.stat().st_mtime for p in engine.rglob("*") if p.is_file()), default=0.0
            )
            newest_target = max(
                (p.stat().st_mtime for p in target.rglob("*") if p.is_file()), default=0.0
            )
            if newest_target >= newest_source:
                return
        category_root.mkdir(parents=True, exist_ok=True)
        _materialize_engine(category, category_root)
    except OSError:
        return


def source_engine(category: str) -> Path | None:
    root = templates_root()
    if root is None:
        return None
    engine = root / category / "_engine"
    return engine if engine.is_dir() else None


def materialize_converters(workspace: Path) -> Path | None:
    """Drop the HTML document converters inside the project workspace.

    Same reasoning as :func:`materialize_document_template`: the agent runs
    shell commands scoped to the user project and cannot reach Navin's
    installation directory, so the converters have to live next to the
    templates. They are written to run either as a package or as a plain
    folder of scripts, which is what lands here.
    """
    if not _CONVERTER_SOURCE.is_dir():
        return None
    resources = prepare_workspace_resources(workspace)
    if resources is None:
        return None
    destination = resources / _WORKSPACE_TOOLS_FOLDER
    try:
        destination.mkdir(parents=True, exist_ok=True)
        copied = set()
        for source in _CONVERTER_SOURCE.glob("*.py"):
            if source.name == "__init__.py":
                continue
            target = destination / source.name
            copied.add(source.name)
            # Called several times per document turn: copy only what changed,
            # so a converter the agent is running is not rewritten under it.
            try:
                src_stat = source.stat()
                dst_stat = target.stat()
                if (
                    dst_stat.st_size == src_stat.st_size
                    and int(dst_stat.st_mtime) == int(src_stat.st_mtime)
                ):
                    continue
            except OSError:
                pass
            shutil.copy2(source, target)
        # A tool left over from an older version keeps being importable, so a
        # converter shipped today can end up calling a helper from months ago.
        for stale in destination.glob("*.py"):
            if stale.name not in copied:
                stale.unlink(missing_ok=True)
    except OSError:
        return None
    return destination


def converter_command(workspace: Path | None, tool: str) -> str | None:
    """Command line the agent should run for one converter.

    The interpreter matters: the one running Navin is the one with python-docx,
    python-pptx and openpyxl installed, while a bare ``python3`` in the user
    shell often has none of them. In a packaged build that interpreter has no
    path of its own, so the command goes through ``navin python``, which runs the
    script inside this executable with the same libraries.
    """
    if workspace is None:
        return None
    folder = materialize_converters(workspace)
    if folder is None:
        return None
    script = folder / f"{tool}.py"
    if not script.is_file():
        return None
    interpreter = " ".join(shlex.quote(part) for part in python_command())
    return f"{interpreter} {_workspace_relative(script, workspace)}"


def _workspace_relative(path: Path, workspace: Path | None) -> str:
    if workspace is None:
        return str(path)
    try:
        return path.relative_to(workspace.resolve(strict=False)).as_posix()
    except ValueError:
        return str(path)


def document_template_runtime_lines(
    metadata: Mapping[str, Any] | None,
    *,
    workspace: Path | None = None,
) -> list[str]:
    """Model-visible annotation for the template attached to this turn."""
    raw = metadata.get(DOCUMENT_TEMPLATE_METADATA_KEY) if isinstance(metadata, Mapping) else None
    mention = normalize_document_template_mention(raw)
    if mention is None:
        return []
    directory = (
        materialize_document_template(mention, workspace)
        if workspace is not None
        else _template_dir(mention["category"], mention["name"])
    )
    if directory is None:
        return []
    display_directory = _workspace_relative(directory, workspace)
    guidance = _CATEGORY_GUIDANCE.get(mention["category"], "")
    converter = _CONVERTER_USAGE.get(mention["category"])
    if converter is not None:
        command = converter_command(workspace, converter[0])
        if command is not None:
            guidance += f" Run the bundled converter to build it: `{command} {converter[1]}`."
            guidance += (
                " It prints the defects a reader would notice first (clipped copy, "
                "unreadable text, a picture reused across slides): fix them in the "
                "HTML rather than shipping them."
            )
            qa = _QA_USAGE.get(mention["category"])
            if qa is not None:
                qa_command = converter_command(workspace, qa[0])
                if qa_command is not None:
                    guidance += (
                        f" Before converting, score the filled HTML: `{qa_command} {qa[1]}`. "
                        "It lays the pages out in a browser and marks each one out of 100, "
                        "naming what to change. It fails when the weakest one falls under "
                        "the threshold, because that is the page the reader remembers."
                    )
            final_file = _FINAL_FILE.get(mention["category"], "output")
            check = converter_command(workspace, "doc_check")
            if check is not None:
                guidance += (
                    f" After converting, validate the file itself: `{check} {final_file}`. "
                    "It opens the result and blocks on leftover sample copy, unresolved "
                    "placeholders, em dashes, emoji, empty pages and a missing title; "
                    "fix the source and convert again until it passes."
                )
            preview = converter_command(workspace, "preview_document")
            if preview is not None:
                guidance += (
                    f" Then look at what you built: `{preview} {final_file} previews/` "
                    "renders the real pages to PNG and a contact sheet for you to read back."
                )
            if mention["category"] in {"word", "ppt"}:
                pdf = converter_command(workspace, "html2pdf")
                if pdf is not None:
                    source = "<slides-folder>" if mention["category"] == "ppt" else "<filled-document.html>"
                    guidance += (
                        f" If the user wants a PDF as well, print the same HTML: "
                        f"`{pdf} {source} -o {final_file.rsplit('.', 1)[0]}.pdf`."
                    )
            if mention["category"] == "ppt":
                design = converter_command(workspace, "ppt_design")
                if design is not None:
                    guidance += (
                        f" Render slides from semantic JSON, never from the lookbook: "
                        f"`{design} deck --theme {mention['name']} --slides deck.json "
                        f"-o slides/` or `{design} materialize --theme {mention['name']} "
                        f"--slide slide.json -o slides/slide_03.html`."
                    )
    capabilities: list[str] = []
    if mention.get("kind"):
        capabilities.append(f"kind={mention['kind']}")
    if mention.get("output_formats"):
        capabilities.append(
            "output_formats=" + ", ".join(mention["output_formats"])
        )
    if mention.get("language_mode"):
        capabilities.append(f"language_mode={mention['language_mode']}")
    if mention.get("language"):
        capabilities.append(f"required_output_language={mention['language']}")
    if mention.get("text_policy"):
        capabilities.append(f"text_policy={mention['text_policy']}")
    if mention.get("image_text_policy"):
        capabilities.append(f"image_text_policy={mention['image_text_policy']}")
    if mention.get("modules"):
        module_labels = [
            f"{module['id']} ({module['title']}"
            + (", required" if module.get("required") else ", optional")
            + ")"
            for module in mention["modules"]
        ]
        capabilities.append("ordered_modules=" + ", ".join(module_labels))
    capability_context = (
        " Template metadata: " + "; ".join(capabilities) + "."
        if capabilities
        else ""
    )
    return [
        "Document Template Attachment: "
        f"the user selected the {mention['category'].upper()} template "
        f"'{mention['title']}'. A complete workspace-local copy is available "
        f"at '{display_directory}'. This path is inside the active user project "
        "and is accessible to read_file, list_dir and sandboxed shell commands. "
        f"{capability_context} "
        "It is the mandatory visual base for the requested deliverable. "
        "START NOW: this is a simple deliverable. Do not call board "
        "ledger_init and stop. Do not wait for the user to click Build. "
        "Do not open a two-step HTML-only plan. Write the semantic JSON, "
        "render with ppt_design/word_design, convert to PPTX/DOCX, and "
        "hand the file in this turn. "
        "FILL this template: replace every sample string, keep theme.css / "
        "design-system.json / fonts / colors / radius. The theme already "
        "ships photos/background.jpg, photos/left.jpg and photos/right.jpg; "
        "the engine places them (cover = background, image-text = left, "
        "text-image = right). A file the user joined (logo, icon, screenshot) "
        "replaces that slot. Keep theme motion tokens for animation. "
        "If the user asked for Three.js or a 3D wow, ship a companion web page. "
        "Never replace this template with a new look. Inventing another design "
        "is a failed deliverable. Check metadata.json for the intended "
        f"structure. {guidance} "
        "Navin already provides the document-generation Python dependencies. "
        "Do not use pip, pipx, apt, brew or another installer unless a real "
        "import or executable check fails and the user explicitly approves it. "
        "Bundled skill instructions are readable through workspace-style paths "
        "such as skills/pptx-generator/SKILL.md; never try to open Navin's "
        "installation directory. "
        "For every template whose language_mode is dynamic, every visible source "
        "text is sample content only: replace all of it with content in the explicitly "
        "selected output language, including titles, labels, legends, tables, notes, "
        "headers, footers, dates and numbers. Set the final language and writing "
        "direction explicitly. Never reuse a raster image or CSS background that "
        "contains source-language text; replace it with a text-free asset or rebuild "
        "its labels as editable localized text. "
        "Typography rules for all generated content: never use em/en dashes "
        "(\u2014 or \u2013); use commas, colons, periods or parentheses instead. "
        "Never use emoji or generic decorative icons (\U0001f4ca, \u2705, "
        "\U0001f4a1...); prefer clean typographic elements (numbers, plain "
        "glyphs, CSS shapes) consistent with the template design."
    ]


async def document_template_context_provider(
    request: "RequestContext",
) -> RuntimeContextBlock | None:
    """Runtime-context provider registered by the agent loop."""
    lines = document_template_runtime_lines(
        request.metadata,
        workspace=request.workspace,
    )
    content = wrap_runtime_context_lines(lines)
    if not content:
        return None
    return RuntimeContextBlock(source="document_template", content=content)


# ---------------------------------------------------------------------------
# Toolchain without a template: the user asks for "a PDF", "a deck", "un
# rapport Word" or hands over their own .pptx / .docx. The generators are the
# same; the agent has to know they exist and where they are before it starts
# hand-writing Office XML or reaching for pip.

# Words that name a document format on their own.
_DOC_FORMAT_RE = re.compile(
    r"(?<!\w)(?:pdf|docx?|pptx?|xlsx|powerpoint|power point|libreoffice|"
    r"pr[ée]sentation|diaporama|diapos?|tableur|classeur|spreadsheet)(?!\w)",
    re.IGNORECASE,
)
# Words that need a creation verb next to them ("word" and "deck" are ordinary
# words; "excel" is a verb in English).
_DOC_WEAK_RE = re.compile(
    r"(?<![\w.])(?:word|excel|slides?|deck|office|rapport|report|proposal|"
    r"proposition|contrat|contract|lettre|letter|invoice|facture|devis|brochure|"
    r"flyer|fiche|memo|whitepaper|livre blanc|attestation|certificat|certificate|"
    r"cv|resume|compte[- ]rendu|minutes|cahier des charges|one[- ]pager|pitch|"
    r"plaquette|dossier)(?!\w)",
    re.IGNORECASE,
)
_DOC_VERB_RE = re.compile(
    r"(?<!\w)(?:g[ée]n[èe]re[rz]?|generate|creat(?:e|ing)|cr[ée]e[rz]?|r[ée]dige[rz]?|"
    r"write|writing|make|produce|produi[st]|produire|pr[ée]pare[rz]?|prepare|build|"
    r"export(?:e[rz]?)?|convert(?:i[sr]?)?|mets? en page|design|fais|faire|draft|"
    r"assemble|monte[rz]?|sors|sortir|livre[rz]?|deliver)(?!\w)",
    re.IGNORECASE,
)
_DOC_SUFFIXES = (".docx", ".pptx", ".xlsx", ".pdf", ".doc", ".ppt", ".odt", ".odp")


def wants_document_toolchain(text: str | None, metadata: Mapping[str, Any] | None) -> bool:
    """True when the turn is about producing or reworking an Office/PDF file."""
    if isinstance(metadata, Mapping) and metadata.get(DOCUMENT_TEMPLATE_METADATA_KEY):
        return False
    if isinstance(metadata, Mapping):
        mentions = metadata.get("file_mentions")
        if isinstance(mentions, list):
            for item in mentions:
                path = item.get("path") if isinstance(item, Mapping) else None
                if isinstance(path, str) and path.lower().endswith(_DOC_SUFFIXES):
                    return True
    body = (text or "").strip()
    if not body or len(body) > 20_000:
        return False
    if _DOC_FORMAT_RE.search(body):
        return True
    return bool(_DOC_WEAK_RE.search(body) and _DOC_VERB_RE.search(body))


def _attached_office_files(metadata: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(metadata, Mapping):
        return []
    mentions = metadata.get("file_mentions")
    if not isinstance(mentions, list):
        return []
    out: list[str] = []
    for item in mentions:
        path = item.get("path") if isinstance(item, Mapping) else None
        if isinstance(path, str) and path.lower().endswith((".docx", ".pptx", ".xlsx")):
            out.append(path)
    return out


def document_toolchain_runtime_lines(
    text: str | None,
    metadata: Mapping[str, Any] | None,
    *,
    workspace: Path | None,
) -> list[str]:
    """One compact block naming the bundled generators for a template-less turn."""
    if workspace is None or not wants_document_toolchain(text, metadata):
        return []
    folder = materialize_converters(workspace)
    if folder is None:
        return []
    resources = prepare_workspace_resources(workspace)
    if resources is not None:
        for category in ("ppt", "word"):
            _ensure_engine(category, resources / _WORKSPACE_RESOURCE_FOLDER / category)
    interpreter = " ".join(shlex.quote(part) for part in python_command())
    tools = _workspace_relative(folder, workspace)
    own = _attached_office_files(metadata)
    lines = [
        "Document toolchain: the user wants an Office or PDF deliverable and no "
        "template is attached. Navin ships generators with their libraries; use "
        "them instead of hand-writing Office XML, pasting rendered pages as images "
        "or installing anything. START NOW and hand the file in this turn. "
        f"Every tool below runs as `{interpreter} {tools}/<tool>.py ...`.",
        "- Word (.docx): write semantic JSON (cover, sections, tables, KPIs, "
        "callouts), `word_design.py render --document doc.json -o document.html` "
        "(`themes` lists themes, `apply --theme <name>` restyles), score "
        "`word_qa.py document.html`, convert `html2docx.py document.html -o "
        "document.docx` (headings, lists, tables stay editable).",
        "- PowerPoint (.pptx): semantic deck JSON (layout, title, items, series or "
        "chart, image, notes), `ppt_design.py deck --theme <name> --slides deck.json "
        "-o slides/` (`layouts` lists layout ids), score `ppt_qa.py slides/`, convert "
        "`html2pptx.py slides/ -o deck.pptx`. `series` / `chart` become native "
        "editable charts.",
        "- PDF: lay out A4 `.page` sections (word_design output prints as is), then "
        "`html2pdf.py document.html -o document.pdf`; a slides folder prints as 16:9 "
        "pages. It refuses a page under the quality threshold and reads the PDF back.",
        "- Excel (.xlsx): `html2xlsx.py document.html -o workbook.xlsx` writes real "
        "cells, number formats and SUM formulas on totals.",
        "- The user's own .pptx / .docx: `office_template.py inspect <file>` (layouts, "
        "placeholders, theme, styles), `fill <file> --data values.json -o out` "
        "({{key}}, lists, {{image:key}}, repeated table rows), `build <file>.pptx "
        "--slides deck.json -o out.pptx` (new slides on its own layouts).",
        "- Always finish with `doc_check.py <output>` (blocks on leftover sample copy, "
        "placeholders, em dashes, emoji, empty pages, no title) and look at the "
        "result: `preview_document.py <output> previews/` renders the pages and a "
        "contact sheet.",
        "Skills: skills/docx-generator/SKILL.md, skills/pptx-generator/SKILL.md, "
        "skills/pdf-generator/SKILL.md, skills/spreadsheet-analyst/SKILL.md. "
        "Typography: no em/en dashes, no emoji; the reader's language throughout.",
    ]
    if own:
        lines.append(
            "Attached Office files: "
            + ", ".join(f"'{_workspace_relative(Path(path), workspace)}'" for path in own[:6])
            + ". Inspect them first; a template the user provides is the design "
            "to keep, never redraw it."
        )
    return lines


async def document_toolchain_context_provider(
    request: "RequestContext",
) -> RuntimeContextBlock | None:
    """Runtime-context provider: bundled document generators, template or not."""
    lines = document_toolchain_runtime_lines(
        request.original_user_text,
        request.metadata,
        workspace=request.workspace,
    )
    content = wrap_runtime_context_lines(lines)
    if not content:
        return None
    return RuntimeContextBlock(source="document_toolchain", content=content)
