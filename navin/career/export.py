# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Editable, single-column Word exports from the same canonical CV as the preview."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
from typing import Any

from loguru import logger

from navin.career.writer import clean_text, section_label, synchronize_pack

_NAVY = (27, 54, 93)
_SLATE = (51, 65, 85)
_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _clean(text: Any) -> str:
    return clean_text(text)


def _set_language(style: Any, language: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    props = style.element.get_or_add_rPr()
    element = props.find(qn("w:lang"))
    if element is None:
        element = OxmlElement("w:lang")
        props.append(element)
    element.set(qn("w:val"), "fr-FR" if language == "fr" else "en-GB")


def _new_document(language: str) -> Any:
    from docx import Document
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    doc.core_properties.language = "fr-FR" if language == "fr" else "en-GB"
    for section in doc.sections:
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        section.top_margin = section.bottom_margin = Cm(1.55)
        section.left_margin = section.right_margin = Cm(1.7)
    styles = {
        "Normal": (11, False, _SLATE, 0, 4),
        "Title": (22, True, _NAVY, 0, 2),
        "Subtitle": (12, False, _SLATE, 0, 3),
        "Heading 1": (10.5, True, _NAVY, 10, 4),
        "Heading 2": (11, True, _SLATE, 6, 1),
        "List Bullet": (11, False, _SLATE, 0, 2.5),
    }
    for name, (size, bold, color, before, after) in styles.items():
        style = doc.styles[name]
        style.font.name, style.font.size = "Calibri", Pt(size)
        style.font.bold, style.font.color.rgb = bold, RGBColor(*color)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.line_spacing = 1.06
        style.paragraph_format.widow_control = True
        style.paragraph_format.keep_with_next = name in {"Title", "Subtitle", "Heading 1", "Heading 2"}
        _set_language(style, language)
    doc.styles["List Bullet"].paragraph_format.left_indent = Cm(0.45)
    doc.styles["List Bullet"].paragraph_format.first_line_indent = Cm(-0.2)
    return doc


def _rule(paragraph: Any) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in {"val": "single", "sz": "5", "space": "4", "color": "1B365D"}.items():
        bottom.set(qn(f"w:{key}"), value)
    border.append(bottom)
    paragraph._p.get_or_add_pPr().append(border)


def _heading(doc: Any, text: str) -> None:
    para = doc.add_paragraph(_clean(text).upper(), style="Heading 1")
    _rule(para)


def _para(doc: Any, text: str, *, style: str = "Normal", keep_next: bool = False) -> Any:
    if not _clean(text):
        return None
    para = doc.add_paragraph(_clean(text), style=style)
    para.paragraph_format.keep_with_next = keep_next or style in {"Heading 2", "Subtitle"}
    para.paragraph_format.keep_together = True
    return para


def _bullet(doc: Any, text: str) -> None:
    if _clean(text):
        _para(doc, text, style="List Bullet")


def _blocks(doc: Any, paragraphs: list[str], *, kind: str = "") -> None:
    """Keep original master-CV paragraphs; convert actual list lines to Word lists."""
    for block in paragraphs:
        for line in _clean(block).splitlines():
            value = line.strip()
            if re.match(r"^[-*•●]\s+", value):
                _bullet(doc, re.sub(r"^[-*•●]\s+", "", value))
            elif value:
                dated = kind == "experience" and len(value) < 160 and bool(re.search(r"\b(?:19|20)\d{2}\b", value))
                _para(doc, value, style="Heading 2" if dated and (" | " in value or " - " in value) else "Normal")


def _job_lang(job: dict[str, Any], profile: dict[str, Any], pack: dict[str, Any]) -> str:
    explicit = _clean(pack.get("language") or (pack.get("cv") or {}).get("language")).lower()
    if explicit.split("-")[0] in {"fr", "en"}:
        return explicit.split("-")[0]
    from navin.career.writer import _job_language

    return _job_language(job, profile)


def _header(doc: Any, cv: dict[str, Any], language: str, *, letter: bool = False) -> None:
    from docx.shared import Pt

    if cv.get("name"):
        doc.add_paragraph(_clean(cv["name"]), style="Title")
    if cv.get("headline") and not letter:
        _para(doc, cv["headline"], style="Subtitle")
    contacts = [_clean(value) for value in (cv.get("contacts") or []) if _clean(value)]
    if contacts:
        para = _para(doc, " | ".join(contacts), keep_next=True)
        for run in para.runs:
            run.font.size = Pt(10)
    if cv.get("target"):
        prefix = "Objet : " if letter and language == "fr" else "Subject: " if letter else "Candidature : " if language == "fr" else "Application: "
        _para(doc, prefix + _clean(cv["target"]), keep_next=True)


def _write_cv(doc: Any, cv: dict[str, Any], language: str) -> None:
    _header(doc, cv, language)
    if cv.get("summary"):
        _heading(doc, section_label("profile", language))
        _para(doc, cv["summary"])
    highlights = [_clean(value) for value in (cv.get("highlights") or []) if _clean(value)]
    if highlights:
        _heading(doc, section_label("highlights", language))
        for value in highlights:
            _bullet(doc, value)
    experiences = [row for row in (cv.get("experiences") or []) if isinstance(row, dict)]
    if experiences:
        _heading(doc, section_label("experience", language))
        for row in experiences:
            head = " | ".join(_clean(row.get(key)) for key in ("title", "company") if _clean(row.get(key)))
            if head:
                _para(doc, head, style="Heading 2")
            if row.get("period"):
                _para(doc, row["period"], keep_next=bool(row.get("bullets")))
            for bullet in row.get("bullets") or []:
                _bullet(doc, bullet)
    education = [row for row in (cv.get("education") or []) if isinstance(row, dict)]
    if education:
        _heading(doc, section_label("education", language))
        for row in education:
            _para(doc, " | ".join(_clean(row.get(key)) for key in ("diploma", "school", "year") if _clean(row.get(key))))
    if cv.get("skills"):
        _heading(doc, section_label("skills", language))
        _para(doc, cv["skills"])
    if cv.get("languages"):
        _heading(doc, section_label("languages", language))
        _para(doc, " | ".join(_clean(value) for value in cv["languages"] if _clean(value)))
    for section in cv.get("sections") or []:
        if not isinstance(section, dict):
            continue
        paragraphs = section.get("paragraphs")
        if not isinstance(paragraphs, list) or not any(_clean(value) for value in paragraphs):
            continue
        _heading(doc, section_label(str(section.get("kind") or "additional"), language))
        _blocks(doc, paragraphs, kind=str(section.get("kind") or "additional"))


def render_docx(
    job: dict[str, Any],
    profile: dict[str, Any],
    pack: dict[str, Any],
    *,
    document: str = "pack",
) -> bytes | None:
    """Render the CV, cover or combined pack. The historical default stays a pack."""
    if document not in {"pack", "cv", "cover"}:
        raise ValueError("Unknown Career document kind")
    try:
        doc = _new_document(_job_lang(job, profile, pack))
    except ImportError:
        logger.warning("python-docx missing - Career Word export unavailable")
        return None
    language = _job_lang(job, profile, pack)
    cv = copy.deepcopy(pack["cv"]) if isinstance(pack.get("cv"), dict) else {}
    cv.setdefault("name", _clean(profile.get("display_name")))
    cv.setdefault("language", language)
    if not cv.get("contacts"):
        cv["contacts"] = [_clean(profile.get(key)) for key in ("email", "phone", "city", "linkedin") if _clean(profile.get(key))]
    if not cv.get("schema_version") and not cv.get("experiences"):
        # Existing saved packs may predate structured CVs. Keep their complete text.
        legacy = _clean(pack.get("cv_text") or profile.get("master_cv"))
        if legacy and not cv.get("sections"):
            cv["sections"] = [{"kind": "additional", "paragraphs": re.split(r"\n{2,}", legacy)}]
    doc.core_properties.title = _clean(cv.get("name")) + (" - Lettre de motivation" if document == "cover" and language == "fr" else " - Cover letter" if document == "cover" else " - CV")
    if document in {"pack", "cv"}:
        _write_cv(doc, cv, language)
    cover = _clean(pack.get("cover"))
    if cover and document in {"pack", "cover"}:
        if document == "pack":
            doc.add_page_break()
        _header(doc, cv, language, letter=True)
        _heading(doc, "Lettre de motivation" if language == "fr" else "Cover letter")
        for paragraph in cover.split("\n\n"):
            _para(doc, paragraph)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def attach_exports(
    store: Any,
    job: dict[str, Any],
    profile: dict[str, Any],
    pack: dict[str, Any],
) -> dict[str, Any]:
    canonical = isinstance(pack.get("cv"), dict) and pack["cv"].get("schema_version") == 2
    out = synchronize_pack(pack) if canonical else copy.deepcopy(pack)
    if not out.get("pack_ready"):
        return out
    name = str(out.get("cv_name") or "CV.docx")
    if not name.lower().endswith(".docx"):
        name = f"{name.rsplit('.', 1)[0]}.docx"
    stem = name[:-5]
    oid = re.sub(r"[^A-Za-z0-9._-]+", "_", str(job.get("id") or "job"))[:80]
    exports = dict(out.get("exports") or {})
    variants = [
        ("docx", "pack", name, f"cv_{oid}"),
        ("cv_docx", "cv", f"{stem}_CV.docx", f"resume_{oid}"),
    ]
    if out.get("cover"):
        variants.append(("cover_docx", "cover", f"{stem}_Lettre.docx" if out.get("language") == "fr" else f"{stem}_Cover.docx", f"cover_{oid}"))
    for key, document, filename, file_id in variants:
        raw = render_docx(job, profile, out, document=document)
        if raw is None:
            generation = dict(out.get("generation") or {})
            warning = "Export Word indisponible; le CV reste accessible en texte." if out.get("language") == "fr" else "Word export unavailable; the complete text CV is retained."
            out["generation"] = {**generation, "status": "needs_review", "warnings": [*(generation.get("warnings") or []), warning]}
            break
        # A changed filename must never resolve to an older export with the same ID.
        revision = hashlib.sha256(raw).hexdigest()[:12]
        record = store.save_bytes(filename, raw, file_id=f"{file_id}_{revision}")
        exports[key] = {**record, "kind": key, "mime": _MIME, "source_digest": document_fingerprint(out)}
    out["cv_name"] = name
    out["exports"] = exports
    return out


def document_fingerprint(pack: dict[str, Any]) -> str:
    """Bind editable exports to their canonical CV and letter, independent of ZIP timestamps."""
    source = {key: pack.get(key) for key in ("cv", "cv_text", "cover", "language")}
    return hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
