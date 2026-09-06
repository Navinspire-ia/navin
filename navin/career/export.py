"""Write a professional Word pack for one Career application."""

from __future__ import annotations

import io
from typing import Any

from loguru import logger

_NAVY = (27, 54, 93)
_SLATE = (51, 65, 85)
_LINE = (203, 213, 225)


def _clean(text: Any) -> str:
    return str(text or "").replace("\u2014", " - ").replace("\u2013", "-").strip()


def _style_run(run: Any, *, size: int = 11, bold: bool = False, color: Any = None, name: str = "Calibri") -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color
    try:
        run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
        run._element.rPr.rFonts.set(qn("w:ascii"), name)
        run._element.rPr.rFonts.set(qn("w:hAnsi"), name)
    except Exception:
        pass


def _add_rule(doc: Any) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(2)
    para.paragraph_format.space_after = Pt(10)
    p_pr = para._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "16")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "1B365D")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)
    run = para.add_run("")
    _style_run(run, size=4, color=RGBColor(*_LINE))


def _heading(doc: Any, text: str) -> None:
    from docx.shared import Pt, RGBColor

    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(14)
    para.paragraph_format.space_after = Pt(2)
    run = para.add_run(_clean(text).upper())
    _style_run(run, size=11, bold=True, color=RGBColor(*_NAVY))
    _add_rule(doc)


def _para(doc: Any, text: str, *, size: int = 11, bold: bool = False, center: bool = False) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    para = doc.add_paragraph()
    if center:
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.paragraph_format.space_after = Pt(4)
    run = para.add_run(_clean(text))
    _style_run(run, size=size, bold=bold, color=RGBColor(*_SLATE))


def _bullet(doc: Any, text: str) -> None:
    from docx.shared import RGBColor

    try:
        para = doc.add_paragraph(_clean(text), style="List Bullet")
    except Exception:
        para = doc.add_paragraph(f"- {_clean(text)}")
    for run in para.runs:
        _style_run(run, size=11, color=RGBColor(*_SLATE))


def _job_lang(job: dict[str, Any], profile: dict[str, Any], pack: dict[str, Any]) -> str:
    if str(pack.get("language") or "").lower().startswith("fr"):
        return "fr"
    langs = [str(item).lower() for item in (profile.get("languages") or [])]
    if langs and langs[0].startswith("fr"):
        return "fr"
    country = str(job.get("country") or profile.get("residence_country") or "").upper()
    return "fr" if country in {"FR", "BE", "LU", "MC", "CH"} else "en"


def render_docx(job: dict[str, Any], profile: dict[str, Any], pack: dict[str, Any]) -> bytes | None:
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Cm, Pt, RGBColor
    except ImportError:
        logger.warning("python-docx missing - Career CV pack skipped")
        return None
    doc = Document()
    try:
        normal = doc.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)
        normal.font.color.rgb = RGBColor(*_SLATE)
    except Exception:
        pass
    for section in doc.sections:
        section.top_margin = Cm(1.5)
        section.bottom_margin = Cm(1.5)
        section.left_margin = Cm(1.8)
        section.right_margin = Cm(1.8)
    lang = _job_lang(job, profile, pack)
    person = _clean(profile.get("display_name") or "Candidate")
    title = _clean(job.get("title") or "Role")
    company = _clean(job.get("company") or "")
    headline = _clean(profile.get("headline") or "")
    cv = pack.get("cv") if isinstance(pack.get("cv"), dict) else {}
    name = doc.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name.paragraph_format.space_after = Pt(0)
    run = name.add_run(person)
    _style_run(run, size=22, bold=True, color=RGBColor(*_NAVY))
    banner = _clean(cv.get("headline") or headline or title)
    if banner:
        sub = doc.add_paragraph()
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sub.paragraph_format.space_after = Pt(2)
        run = sub.add_run(banner)
        _style_run(run, size=12, color=RGBColor(*_SLATE))
    target = _clean(cv.get("target") or f"{title} - {company}".strip(" -"))
    if target:
        aim = doc.add_paragraph()
        aim.alignment = WD_ALIGN_PARAGRAPH.CENTER
        aim.paragraph_format.space_after = Pt(4)
        run = aim.add_run(target)
        _style_run(run, size=10, bold=True, color=RGBColor(*_NAVY))
    contacts = [_clean(item) for item in (cv.get("contacts") or []) if _clean(item)]
    if not contacts:
        contacts = [
            _clean(profile.get("email") or ""),
            _clean(profile.get("phone") or ""),
            _clean(profile.get("city") or profile.get("location") or ""),
            _clean(profile.get("linkedin") or ""),
        ]
        contacts = [item for item in contacts if item]
    if contacts:
        line = doc.add_paragraph()
        line.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = line.add_run("  ·  ".join(contacts))
        _style_run(run, size=10, color=RGBColor(*_SLATE))
    _add_rule(doc)
    summary = _clean(cv.get("summary") or "")
    if summary:
        _heading(doc, "Profil" if lang == "fr" else "Profile")
        _para(doc, summary)
    skills = _clean(cv.get("skills") or "")
    if skills:
        _heading(doc, "Competences" if lang == "fr" else "Skills")
        _para(doc, skills.replace(" · ", "  ·  "))
    strengths = [_clean(item) for item in (cv.get("strengths") or []) if _clean(item)]
    if strengths:
        _heading(doc, "Points forts" if lang == "fr" else "Strengths")
        for item in strengths:
            _bullet(doc, item)
    experiences = cv.get("experiences") if isinstance(cv.get("experiences"), list) else []
    if experiences:
        _heading(doc, "Experience")
        for row in experiences:
            if not isinstance(row, dict):
                continue
            role = _clean(row.get("title") or "")
            firm = _clean(row.get("company") or "")
            period = _clean(row.get("period") or "")
            head = "  ·  ".join(part for part in (role, firm) if part)
            if head:
                line = f"{head}    {period}" if period else head
                _para(doc, line, bold=True)
            for bullet in row.get("bullets") or []:
                if _clean(bullet):
                    _bullet(doc, bullet)
    education = cv.get("education") if isinstance(cv.get("education"), list) else []
    if education:
        _heading(doc, "Formation" if lang == "fr" else "Education")
        for row in education:
            if isinstance(row, dict):
                line = "  ·  ".join(
                    _clean(row.get(key) or "") for key in ("diploma", "school", "year") if _clean(row.get(key) or "")
                )
                if line:
                    _para(doc, line)
            elif _clean(row):
                _para(doc, str(row))
    languages = [_clean(item) for item in (cv.get("languages") or profile.get("languages") or []) if _clean(item)]
    if languages:
        _heading(doc, "Langues" if lang == "fr" else "Languages")
        _para(doc, "  ·  ".join(languages))
    if not experiences and not education and not skills:
        for block in _clean(pack.get("cv_text") or "").split("\n\n"):
            if block.strip():
                _para(doc, block)
    cover = _clean(pack.get("cover") or "")
    if cover:
        try:
            doc.add_page_break()
        except Exception:
            pass
        letter_name = doc.add_paragraph()
        letter_name.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = letter_name.add_run(person)
        _style_run(run, size=16, bold=True, color=RGBColor(*_NAVY))
        if contacts:
            line = doc.add_paragraph()
            line.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = line.add_run("  ·  ".join(contacts[:3]))
            _style_run(run, size=10, color=RGBColor(*_SLATE))
        _heading(doc, "Lettre de motivation" if lang == "fr" else "Cover letter")
        for block in cover.split("\n\n"):
            if block.strip():
                _para(doc, block)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def attach_exports(
    store: Any,
    job: dict[str, Any],
    profile: dict[str, Any],
    pack: dict[str, Any],
) -> dict[str, Any]:
    out = dict(pack)
    raw = render_docx(job, profile, out)
    if not raw:
        return out
    name = str(out.get("cv_name") or "Navin_CV.docx")
    if not name.lower().endswith(".docx"):
        name = f"{name.rsplit('.', 1)[0]}.docx"
    oid = str(job.get("id") or "job")
    record = store.save_bytes(name, raw, file_id=f"cv_{oid}")
    out["cv_name"] = name
    out["exports"] = {
        "docx": {
            **record,
            "kind": "docx",
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    }
    out["pack_ready"] = True
    return out
