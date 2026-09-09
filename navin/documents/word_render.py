# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Turn a semantic Word document into finished A4 HTML.

Same failure as the old PPT path: the agent copied ``document.html``, changed
the title, and left the lookbook company, the sample KPIs and ``image.png``.
This module builds the pages from JSON. Sample phrases never enter the output.
A missing image is omitted, not framed as an empty box.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Mapping

from navin.documents import word_design

_FAKE_IMAGES = {"", "image.png", "placeholder.png", "photo.png", "screenshot.png"}

SAMPLE_PHRASES = (
    "company name",
    "strategic review & outlook 2026",
    "summary of the year's results",
    "executive management",
    "board of directors",
    "the past fiscal year confirms",
    "consulting & services",
    "point of attention: reliance on the digital segment",
    "industrializing the digital platform",
    "lorem ipsum",
    "your title here",
    "your company here",
    "contact@example.com",
)

_SHELL = """\
<!DOCTYPE html>
<html lang="und" data-language-mode="dynamic" data-kind="{kind}">
<head>
<meta charset="UTF-8">
<title>{title}</title>
{theme}
<style>
{css}
</style>
<style id="navin-dynamic-language">
  html[dir="rtl"] body {{ direction: rtl; }}
  html[dir="rtl"] p, html[dir="rtl"] li, html[dir="rtl"] td,
  html[dir="rtl"] th, html[dir="rtl"] label {{ direction: rtl; text-align: right; }}
</style>
</head>
<body data-localize="all-visible-text" data-filled="1">
{body}
</body>
</html>
"""


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _paragraphs(value: Any) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("text") or item.get("body") or item.get("content")
            if _text(item):
                out.append(_text(item))
        return out
    if isinstance(value, dict):
        value = value.get("text") or value.get("body") or value.get("content")
    text = _text(value)
    if not text:
        return []
    return [part.strip() for part in text.split("\n\n") if part.strip()]


# Keys a model writes for the same thing. Body copy first, then list items.
_BODY_KEYS = ("body", "description", "paragraphs", "text", "content", "summary", "intro")
_ITEM_KEYS = ("items", "bullets", "points", "list_items", "steps", "checklist")


def _first(section: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = section.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _callout_parts(value: Any) -> tuple[str, str, str]:
    """(title, text, variant) from a string or a {title, text, variant} object."""
    if isinstance(value, dict):
        title = _text(value.get("title") or value.get("label") or value.get("heading"))
        text = _text(value.get("text") or value.get("body") or value.get("content") or value.get("description"))
        variant = _text(value.get("variant") or value.get("kind") or value.get("type"))
        return title, text, variant
    return "", _text(value), ""


def _items(value: Any) -> list[dict[str, str]]:
    raw = value if isinstance(value, list) else []
    out: list[dict[str, str]] = []
    for entry in raw:
        if isinstance(entry, str):
            if entry.strip():
                out.append({"label": entry.strip(), "detail": "", "value": "", "meta": ""})
            continue
        if not isinstance(entry, dict):
            continue
        label = _text(entry.get("label") or entry.get("title") or entry.get("name"))
        detail = _text(
            entry.get("detail")
            or entry.get("body")
            or entry.get("text")
            or entry.get("description")
        )
        value_s = _text(entry.get("value") or entry.get("num"))
        meta = _text(entry.get("meta") or entry.get("trend") or entry.get("role"))
        if label or detail or value_s:
            out.append({"label": label, "detail": detail, "value": value_s, "meta": meta})
    return out


def _image_src(value: Any) -> str:
    src = _text(value)
    return "" if src.lower() in _FAKE_IMAGES else src


def leftover_sample_phrases(html_text: str) -> list[str]:
    lowered = html_text.lower()
    return [phrase for phrase in SAMPLE_PHRASES if phrase in lowered]


def has_placeholder_image(html_text: str) -> bool:
    lowered = html_text.lower()
    return any(f'src="{name}"' in lowered for name in _FAKE_IMAGES if name)


def _kpis(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    klass = {2: " two", 4: " four"}.get(len(rows), "")
    parts = [f'<div class="kpis{klass}">']
    for row in rows:
        parts.append('<div class="kpi">')
        if row["value"]:
            parts.append(f'<div class="value">{_esc(row["value"])}</div>')
        if row["label"]:
            parts.append(f'<div class="label">{_esc(row["label"])}</div>')
        if row["meta"]:
            parts.append(f'<div class="trend">{_esc(row["meta"])}</div>')
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def _table(table: Mapping[str, Any] | None) -> str:
    if not isinstance(table, dict):
        return ""
    headers = [str(cell) for cell in (table.get("headers") or [])]
    rows = table.get("rows") or []
    if not headers:
        return ""
    variant = _text(table.get("variant"))
    klass = "table-financial" if variant in {"financial", "pnl"} else ""
    if variant == "minimal":
        klass = "table-minimal"
    parts = [f'<table class="{klass}"><thead><tr>']
    parts.extend(f"<th>{_esc(cell)}</th>" for cell in headers)
    parts.append("</tr></thead><tbody>")
    for row in rows:
        if isinstance(row, dict):
            row = [row.get(h) if h in row else v for h, v in zip(headers, list(row.values()) + [""] * len(headers), strict=False)]
        if not isinstance(row, list):
            continue
        parts.append("<tr>")
        parts.extend(f"<td>{_esc(cell)}</td>" for cell in row)
        parts.append("</tr>")
    parts.append("</tbody>")
    # A totals row, however the JSON names it, closes the table in a footer so
    # the converters can style it and html2xlsx can put a SUM on it.
    total = table.get("total") or table.get("totals") or table.get("footer") or table.get("total_row")
    if isinstance(total, (list, tuple)) and any(_text(c) for c in total):
        cells = [_text(c) for c in total]
        parts.append("<tfoot><tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr></tfoot>")
    elif isinstance(total, dict) and _text(total.get("value") or total.get("amount")):
        label = _text(total.get("label")) or "Total"
        cells = [""] * max(0, len(headers) - 2) + [label, _text(total.get("value") or total.get("amount"))]
        parts.append("<tfoot><tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr></tfoot>")
    parts.append("</table>")
    return "".join(parts)


def _list_block(items: list[dict[str, str]], variant: str) -> str:
    if not items:
        return ""
    tag = "ol" if variant == "steps" else "ul"
    klass = {"steps": "steps", "check": "checklist"}.get(variant, "")
    attr = f' class="{klass}"' if klass else ""
    parts = [f"<{tag}{attr}>"]
    for item in items:
        line = item["label"]
        if item["detail"]:
            line = f"{line}: {item['detail']}" if line else item["detail"]
        parts.append(f"<li>{_esc(line)}</li>")
    parts.append(f"</{tag}>")
    return "".join(parts)


def _figure(src: str, caption: str = "") -> str:
    if not src:
        return ""
    cap = f"<figcaption>{_esc(caption)}</figcaption>" if caption else ""
    return f'<figure><img alt="{_esc(caption)}" src="{_esc(src)}">{cap}</figure>'


def _callout(text: str, variant: str = "important", title: str = "") -> str:
    if not text and not title:
        return ""
    head = f"<p><strong>{_esc(title)}</strong></p>" if title else ""
    body = f"<p>{_esc(text)}</p>" if text else ""
    return (
        f'<div class="callout callout-{_esc(variant or "important")}">'
        f"{head}{body}</div>"
    )


def _insight(text: str) -> str:
    if not text:
        return ""
    return (
        '<div class="insight"><span class="insight-label">Insight</span>'
        f"<p>{_esc(text)}</p></div>"
    )


def _section_html(section: Mapping[str, Any]) -> str:
    heading = _text(section.get("heading") or section.get("title"))
    parts: list[str] = ['<div class="section">']
    if heading:
        parts.append(f"<h2>{_esc(heading)}</h2><div class=\"rule\"></div>")
    for para in _paragraphs(_first(section, _BODY_KEYS)):
        parts.append(f"<p>{_esc(para)}</p>")
    for sub in section.get("subsections") or section.get("children") or []:
        if isinstance(sub, dict):
            sub_heading = _text(sub.get("heading") or sub.get("title"))
            if sub_heading:
                parts.append(f"<h3>{_esc(sub_heading)}</h3>")
            for para in _paragraphs(_first(sub, _BODY_KEYS)):
                parts.append(f"<p>{_esc(para)}</p>")
            sub_items = _first(sub, _ITEM_KEYS)
            if sub_items:
                parts.append(_list_block(_items(sub_items), "bullets"))
    kpis = section.get("kpis") or section.get("metrics") or section.get("figures")
    if kpis:
        parts.append(_kpis(_items(kpis)))
    elif _text(section.get("kind")) == "kpi":
        parts.append(_kpis(_items(section.get("items"))))
    parts.append(_table(section.get("table") if isinstance(section.get("table"), dict) else None))
    list_kind = _text(section.get("list") or section.get("list_variant"))
    if not list_kind:
        if section.get("steps"):
            list_kind = "steps"
        elif section.get("checklist"):
            list_kind = "check"
    items = _first(section, _ITEM_KEYS)
    if items and not kpis and _text(section.get("kind")) != "kpi":
        parts.append(_list_block(_items(items), list_kind or "bullets"))
    quote = _text(section.get("quote"))
    if quote:
        author = _text(section.get("quote_author") or section.get("author"))
        cite = f"<cite>{_esc(author)}</cite>" if author else ""
        parts.append(f"<blockquote><p>{_esc(quote)}</p>{cite}</blockquote>")
    title, text, variant = _callout_parts(section.get("callout") or section.get("note") or section.get("warning"))
    if section.get("warning") and not variant:
        variant = "warning"
    parts.append(_callout(text, _text(section.get("callout_variant")) or variant or "important", title))
    parts.append(_insight(_text(section.get("insight"))))
    parts.append(_figure(_image_src(section.get("image")), _text(section.get("caption"))))
    parts.append("</div>")
    return "".join(parts)


def _meta_labels(language: str) -> tuple[tuple[str, str], ...]:
    if (language or "").lower().startswith("fr"):
        return (
            ("prepared_by", "Pr\u00e9par\u00e9 par"),
            ("author", "Pr\u00e9par\u00e9 par"),
            ("recipients", "Destinataires"),
            ("date", "Date"),
            ("confidentiality", "Diffusion"),
            ("ref", "R\u00e9f\u00e9rence"),
        )
    return (
        ("prepared_by", "Prepared by"),
        ("author", "Prepared by"),
        ("recipients", "Recipients"),
        ("date", "Date"),
        ("confidentiality", "Confidentiality"),
        ("ref", "Reference"),
    )


def _meta_row(meta: Mapping[str, Any], language: str = "") -> str:
    labels = _meta_labels(language)
    cells: list[str] = []
    seen: set[str] = set()
    for key, label in labels:
        value = _text(meta.get(key))
        if not value or key in {"author"} and "prepared_by" in seen:
            continue
        if key == "prepared_by":
            seen.add("author")
        seen.add(key)
        cells.append(f"<div><strong>{_esc(label)}</strong>{_esc(value)}</div>")
    extra = meta.get("fields")
    if isinstance(extra, list):
        for field in extra:
            if isinstance(field, dict) and _text(field.get("label")):
                value = _text(field.get("value"))
                cells.append(
                    f"<div><strong>{_esc(field.get('label'))}</strong>"
                    f"{_esc(value)}</div>"
                    if value
                    else f"<div>{_esc(field.get('label'))}</div>"
                )
    for key in ("version", "client"):
        if _text(meta.get(key)):
            label = {"version": "Version", "client": "Client"}[key]
            cells.append(f"<div><strong>{label}</strong>{_esc(meta.get(key))}</div>")
    if not cells:
        return ""
    return f'<div class="cover-meta">{"".join(cells)}</div>'


def _brand(doc: Mapping[str, Any]) -> str:
    name = _text(doc.get("brand") or doc.get("company"))
    if not name:
        return ""
    mark = name[0].upper()
    return (
        f'<div class="brand"><div class="brand-mark">{_esc(mark)}</div>'
        f'<div class="brand-name">{_esc(name)}</div></div>'
    )


def _footer(doc: Mapping[str, Any], page: int, total: int) -> str:
    left = _text(doc.get("footer")) or _text(doc.get("brand") or doc.get("company"))
    return (
        f'<div class="footer"><span>{_esc(left)}</span>'
        f"<span>Page {page} / {total}</span></div>"
    )


_SKIP_TOC_KINDS = {"letter", "brief", "contract"}
_OUTLINE_HEADING_RE = re.compile(
    r"^(sommaire|agenda|contents|table of contents|table des matieres|"
    r"au programme|outline)$",
    re.I,
)


def outline_copy(language: str, *hints: str) -> dict[str, str]:
    lang = (language or "").lower()
    blob = " ".join(hints).lower()
    french = lang.startswith("fr") or bool(
        re.search(r"\b(les|des|une|pour|avec|dans|cette|equipe|risques|synthese)\b", blob)
    )
    if french:
        return {"kicker": "Document", "title": "Sommaire"}
    if lang.startswith("ar"):
        return {"kicker": "الوثيقة", "title": "الفهرس"}
    return {"kicker": "Document", "title": "Contents"}


def _section_heading(section: Mapping[str, Any]) -> str:
    return _text(section.get("heading") or section.get("title"))


def _is_outline_section(section: Mapping[str, Any]) -> bool:
    kind = _text(section.get("kind")).lower()
    if kind in {"toc", "sommaire", "agenda", "contents"}:
        return True
    return bool(_OUTLINE_HEADING_RE.match(_section_heading(section)))


def _headed_sections(sections: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        section
        for section in sections
        if _section_heading(section) and not _is_outline_section(section)
    ]


def _should_toc(doc: Mapping[str, Any], sections: list[Mapping[str, Any]]) -> bool:
    kind = _text(doc.get("kind") or "report")
    if kind in _SKIP_TOC_KINDS:
        return False
    if any(_is_outline_section(section) for section in sections):
        return False
    return len(_headed_sections(sections)) >= 2


def _toc_page(
    doc: Mapping[str, Any],
    entries: list[tuple[str, int]],
    page_no: int,
    total: int,
) -> str:
    copy = outline_copy(
        _text(doc.get("language")),
        _text(doc.get("title")),
        *(heading for heading, _ in entries[:4]),
    )
    title = _text(doc.get("outline_title")) or copy["title"]
    rows: list[str] = []
    for index, (heading, page) in enumerate(entries, 1):
        rows.append(
            "<li>"
            f'<span class="toc-num">{index:02d}</span>'
            f'<span class="toc-label">{_esc(heading)}</span>'
            '<span class="toc-dots"></span>'
            f'<span class="toc-pg">{page}</span>'
            "</li>"
        )
    return (
        '<section class="page toc-page">'
        f'<p class="kicker">{_esc(copy["kicker"])}</p>'
        f'<p class="lead toc-title">{_esc(title)}</p>'
        '<div class="rule"></div>'
        '<nav class="toc" data-doc-toc="2-3">'
        f"<ol>{''.join(rows)}</ol>"
        "</nav>"
        f"{_footer(doc, page_no, total)}"
        "</section>"
    )


def _cover_image(doc: Mapping[str, Any]) -> str:
    src = _image_src(doc.get("image"))
    if not src:
        pack = word_design.engine_dir() / "photos" / "background.jpg"
        if pack.is_file():
            src = str(pack)
    if not src:
        return ""
    return (
        f'<div class="cover-photo"><img alt="" src="{_esc(src)}"></div>'
    )


def _meta_dict(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The cover metadata, from ``meta`` (object or list of lines) or the doc."""
    raw = doc.get("meta")
    meta: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    if isinstance(raw, list):
        fields = []
        for entry in raw:
            if isinstance(entry, dict) and _text(entry.get("label")):
                fields.append({"label": _text(entry.get("label")), "value": _text(entry.get("value"))})
            elif _text(entry):
                label, _, value = _text(entry).partition(":")
                fields.append({"label": label.strip(), "value": value.strip()} if value else {"label": label.strip(), "value": ""})
        meta["fields"] = fields
    for key in ("date", "author", "prepared_by", "recipients", "confidentiality", "ref", "reference", "version", "client"):
        if key not in meta and _text(doc.get(key)):
            meta["ref" if key == "reference" else key] = _text(doc.get(key))
    return meta


def _cover(doc: Mapping[str, Any]) -> str:
    # A nested "cover" object is the same thing as the cover fields at the root.
    nested = doc.get("cover") if isinstance(doc.get("cover"), dict) else {}
    if nested:
        merged = dict(doc)
        for key, value in nested.items():
            if value in (None, "", [], {}):
                continue
            # The document title names the file; the cover's own title only
            # fills in when the root has none.
            if key == "title" and _text(doc.get("title")):
                continue
            merged[key] = value
        doc = merged
    kicker = _text(doc.get("kicker") or doc.get("kind") or "Document")
    title = _text(doc.get("title"))
    subtitle = _text(doc.get("subtitle") or doc.get("lead") or doc.get("tagline"))
    meta = _meta_dict(doc)
    sub = f'<p class="subtitle">{_esc(subtitle)}</p>' if subtitle else ""
    photo = _cover_image(doc)
    has_photo = " has-photo" if photo else ""
    return (
        f'<section class="page cover{has_photo}">'
        f"{photo}"
        f"{_brand(doc)}"
        '<div class="cover-main">'
        f'<div class="kicker">{_esc(kicker)}</div>'
        f"<h1>{_esc(title)}</h1>"
        '<div class="rule"></div>'
        f"{sub}"
        "</div>"
        f"{_meta_row(meta, _text(doc.get('language')))}"
        "</section>"
    )


def _letter(doc: Mapping[str, Any]) -> str:
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    sender = _text(doc.get("sender") or meta.get("sender") or doc.get("brand"))
    recipient = _text(doc.get("recipient") or meta.get("recipient"))
    date = _text(meta.get("date") or doc.get("date"))
    parts = ['<section class="page">']
    if sender:
        parts.append(f'<p class="muted">{_esc(sender)}</p>')
    if date:
        parts.append(f'<p class="muted">{_esc(date)}</p>')
    if recipient:
        parts.append(f"<p>{_esc(recipient)}</p>")
    title = _text(doc.get("title"))
    if title:
        parts.append(f"<h1>{_esc(title)}</h1>")
    for para in _paragraphs(doc.get("body")):
        parts.append(f"<p>{_esc(para)}</p>")
    for section in doc.get("sections") or []:
        if isinstance(section, dict):
            parts.append(_section_html(section))
    sign = _text(doc.get("sign") or doc.get("signature") or meta.get("author"))
    if sign:
        parts.append(
            '<div class="signature-grid"><div class="signature">'
            '<div class="line"></div>'
            f'<div class="name">{_esc(sign)}</div></div></div>'
        )
    parts.append("</section>")
    return "".join(parts)


def _chunk_sections(sections: list[Mapping[str, Any]], size: int = 2) -> list[list[Mapping[str, Any]]]:
    pages: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    for section in sections:
        current.append(section)
        dense = bool(section.get("table") or section.get("kpis") or section.get("image"))
        if len(current) >= size or dense:
            pages.append(current)
            current = []
    if current:
        pages.append(current)
    return pages or [[]]


def render_body(doc: Mapping[str, Any]) -> str:
    kind = _text(doc.get("kind") or "report")
    if kind == "letter":
        return _letter(doc)
    sections = [item for item in (doc.get("sections") or []) if isinstance(item, dict)]
    pages_html: list[str] = []
    if kind != "brief":
        pages_html.append(_cover(doc))
    elif _text(doc.get("title")):
        lead = _text(doc.get("subtitle"))
        lead_html = f'<p class="lead">{_esc(lead)}</p>' if lead else ""
        pages_html.append(
            '<section class="page">'
            f'<div class="kicker">{_esc(_text(doc.get("kicker")) or "Brief")}</div>'
            f'<h1>{_esc(_text(doc.get("title")))}</h1>'
            f"{lead_html}"
            "</section>"
        )
    chunks = _chunk_sections(sections)
    want_toc = _should_toc(doc, sections)
    total = len(pages_html) + (1 if want_toc else 0) + len(chunks)
    if want_toc:
        first_content = len(pages_html) + 2
        entries: list[tuple[str, int]] = []
        for offset, chunk in enumerate(chunks):
            page = first_content + offset
            for section in chunk:
                heading = _section_heading(section)
                if heading:
                    entries.append((heading, page))
        pages_html.append(_toc_page(doc, entries, len(pages_html) + 1, total))
    start = len(pages_html)
    for offset, chunk in enumerate(chunks, 1):
        page_no = start + offset
        inner = "".join(_section_html(section) for section in chunk)
        pages_html.append(
            f'<section class="page">{inner}{_footer(doc, page_no, total)}</section>'
        )
    return "".join(pages_html)


def render_document(doc: Mapping[str, Any], *, theme: str | None = None) -> str:
    """Self-contained A4 HTML. Colours come only from the chosen theme."""
    name = _text(theme or doc.get("theme") or "executive")
    css = (word_design.engine_dir() / "navin-word.css").read_text(encoding="utf-8")
    return _SHELL.format(
        kind=_esc(_text(doc.get("kind") or "report")),
        title=_esc(_text(doc.get("title") or "Document")),
        theme=word_design.theme_style_block(name),
        css=css,
        body=render_body(doc),
    )


def load_document_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("document JSON must be an object")
    return data


def quality_check(html_text: str) -> dict[str, Any]:
    failures: list[str] = []
    if leftover_sample_phrases(html_text):
        failures.append("empty")
    if has_placeholder_image(html_text):
        failures.append("empty")
    if html_text.count("<h1") != 1:
        failures.append("hierarchy")
    kind_match = re.search(r'data-kind="([^"]+)"', html_text)
    kind = (kind_match.group(1) if kind_match else "").lower()
    if (
        kind not in _SKIP_TOC_KINDS
        and html_text.count("<h2") >= 2
        and "data-doc-toc" not in html_text
    ):
        failures.append("missing-toc")
    return {
        "score": max(0, 100 - 15 * len(failures)),
        "pass": not failures,
        "failures": failures,
    }
