"""Render a ready Word / PowerPoint pack from the written dossier."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from loguru import logger

from navin.tenders.bid_pack import SECTION_KEYS, section_title
from navin.tenders.profile import filed_documents

_NAVY = (27, 54, 93)
_SLATE = (51, 65, 85)
_GOLD = (184, 148, 90)
_ROW = "F4F7FA"
_LINE = "D0D7DE"


def _safe_name(value: str, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())[:80]
    return token or fallback


def _lang_of(response: dict[str, Any]) -> str:
    lang = str(response.get("language") or "en").strip().lower()
    return "fr" if lang.startswith("fr") else "en"


def _clean(text: Any) -> str:
    return str(text or "").replace("\u2014", " - ").replace("\u2013", "-")


def _mapping(tender: dict[str, Any], profile: dict[str, Any], response: dict[str, Any]) -> dict[str, str]:
    fields = {
        "letter": response.get("letter"),
        "executive_summary": response.get("executive_summary"),
        "summary": response.get("executive_summary"),
        "cover": response.get("cover"),
        "toc": response.get("toc"),
        "company": response.get("company"),
        "need": response.get("need"),
        "approach": response.get("approach"),
        "vision": response.get("vision"),
        "functional": response.get("functional"),
        "architecture": response.get("architecture"),
        "methodology": response.get("methodology"),
        "followup_kpi": response.get("followup_kpi"),
        "raci_risks": response.get("raci_risks"),
        "governance": response.get("governance"),
        "planning": response.get("planning"),
        "staffing": response.get("staffing"),
        "references": response.get("references"),
        "compliance_matrix": response.get("compliance_matrix"),
        "matrix": response.get("compliance_matrix"),
        "financial_schedule": response.get("financial_schedule"),
        "title": tender.get("title"),
        "buyer": tender.get("buyer"),
        "deadline": tender.get("deadline"),
        "company_name": profile.get("name"),
        "revision_notes": response.get("revision_notes"),
    }
    out: dict[str, str] = {}
    for key, value in fields.items():
        text = _clean(value).strip()
        out[f"{{{{{key}}}}}"] = text
        out[f"{{{{{key.upper()}}}}}"] = text
    return out


def _replace_text(text: str, mapping: dict[str, str]) -> tuple[str, bool]:
    changed = False
    body = text or ""
    for key, value in mapping.items():
        if key in body:
            body = body.replace(key, value)
            changed = True
    return body, changed


def _first_template(profile: dict[str, Any], store: Any, bucket: str) -> Path | None:
    for row in filed_documents(profile):
        if row.get("bucket") != bucket or not row.get("path"):
            continue
        path = store.files_dir / Path(str(row["path"])).name
        if path.is_file():
            return path
    return None


def _set_paragraph_text(paragraph: Any, text: str) -> None:
    if not paragraph.runs:
        paragraph.add_run(text)
        return
    paragraph.runs[0].text = text
    for run in paragraph.runs[1:]:
        run.text = ""


def _fill_docx(doc: Any, mapping: dict[str, str]) -> bool:
    changed = False
    for paragraph in doc.paragraphs:
        new, hit = _replace_text(paragraph.text, mapping)
        if hit:
            _set_paragraph_text(paragraph, new)
            changed = True
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    new, hit = _replace_text(paragraph.text, mapping)
                    if hit:
                        _set_paragraph_text(paragraph, new)
                        changed = True
    for section in doc.sections:
        for part in (section.header, section.footer):
            for paragraph in part.paragraphs:
                new, hit = _replace_text(paragraph.text, mapping)
                if hit:
                    _set_paragraph_text(paragraph, new)
                    changed = True
    return changed


def doc_mod_pt(size: int) -> Any:
    from docx.shared import Pt

    return Pt(size)


def _style_run(run: Any, *, size: int = 11, bold: bool = False, color: Any = None, name: str = "Calibri") -> None:
    run.font.name = name
    run.font.size = doc_mod_pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color
    try:
        from docx.oxml.ns import qn

        run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
        run._element.rPr.rFonts.set(qn("w:ascii"), name)
        run._element.rPr.rFonts.set(qn("w:hAnsi"), name)
    except Exception:
        pass


def _apply_theme(doc: Any) -> None:
    from docx.shared import Pt, RGBColor

    try:
        normal = doc.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)
        normal.font.color.rgb = RGBColor(*_SLATE)
    except Exception:
        pass
    for name, size in (("Heading 1", 16), ("Heading 2", 13)):
        try:
            style = doc.styles[name]
            style.font.name = "Calibri"
            style.font.size = Pt(size)
            style.font.bold = True
            style.font.color.rgb = RGBColor(*_NAVY)
            style.font.italic = False
        except Exception:
            pass


def _shade_cell(cell: Any, fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _set_cell_borders(cell: Any, color: str = _LINE) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    tc_borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        item = OxmlElement(f"w:{edge}")
        item.set(qn("w:val"), "single")
        item.set(qn("w:sz"), "4")
        item.set(qn("w:space"), "0")
        item.set(qn("w:color"), color)
        tc_borders.append(item)
    tc_pr.append(tc_borders)


def _add_heading_rule(paragraph: Any) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "12")
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), "1B365D")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


def _add_docx_heading(doc: Any, text: str, *, level: int = 1) -> None:
    from docx.shared import Pt, RGBColor

    try:
        heading = doc.add_heading(_clean(text), level=level)
        heading.paragraph_format.space_before = Pt(16)
        heading.paragraph_format.space_after = Pt(8)
        for run in heading.runs:
            _style_run(run, size=16 if level == 1 else 13, bold=True, color=RGBColor(*_NAVY))
        _add_heading_rule(heading)
    except Exception:
        doc.add_paragraph(_clean(text))


def _add_cover_page(doc: Any, tender: dict[str, Any], profile: dict[str, Any], response: dict[str, Any]) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    for section in doc.sections:
        section.top_margin = Cm(1.8)
        section.bottom_margin = Cm(1.8)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.2)
        section.different_first_page_header_footer = True
    lang = _lang_of(response)
    company = _clean(profile.get("name") or ("Candidat" if lang == "fr" else "Bidder"))
    title = _clean(tender.get("title") or ("Avis" if lang == "fr" else "Notice"))
    buyer = _clean(tender.get("buyer") or "")
    ref = _clean(tender.get("reference") or tender.get("notice_id") or "")
    country = _clean(tender.get("country") or "")
    deadline = _clean(tender.get("deadline") or "")
    band = doc.add_table(rows=1, cols=1)
    cell = band.cell(0, 0)
    _shade_cell(cell, "1B365D")
    para = cell.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.paragraph_format.space_before = Pt(10)
    para.paragraph_format.space_after = Pt(10)
    run = para.add_run("DOSSIER DE CANDIDATURE" if lang == "fr" else "SUBMISSION DOSSIER")
    _style_run(run, size=13, bold=True, color=RGBColor(255, 255, 255))
    accent = doc.add_table(rows=1, cols=1)
    gold = accent.cell(0, 0)
    _shade_cell(gold, "B8945A")
    gold.paragraphs[0].add_run(" ")
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(36)
    name = doc.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = name.add_run(company)
    _style_run(run, size=28, bold=True, color=RGBColor(*_NAVY))
    notice = doc.add_paragraph()
    notice.alignment = WD_ALIGN_PARAGRAPH.CENTER
    notice.paragraph_format.space_after = Pt(18)
    run = notice.add_run(title)
    _style_run(run, size=16, bold=False, color=RGBColor(*_SLATE))
    labels = (
        (("Acheteur", buyer), ("Reference", ref), ("Pays", country), ("Date limite", deadline))
        if lang == "fr"
        else (
            ("Contracting authority", buyer),
            ("Reference", ref),
            ("Country", country),
            ("Deadline", deadline),
        )
    )
    meta = doc.add_table(rows=len(labels), cols=2)
    for index, (label, value) in enumerate(labels):
        left = meta.cell(index, 0)
        right = meta.cell(index, 1)
        left.text = label
        right.text = value or ("non renseigne" if lang == "fr" else "not on file")
        _shade_cell(left, "1B365D")
        _shade_cell(right, _ROW if index % 2 else "FFFFFF")
        _set_cell_borders(left, "1B365D")
        _set_cell_borders(right)
        for paragraph in left.paragraphs:
            for run in paragraph.runs:
                _style_run(run, size=11, bold=True, color=RGBColor(255, 255, 255))
        for paragraph in right.paragraphs:
            for run in paragraph.runs:
                _style_run(run, size=11, color=RGBColor(*_NAVY))
    note = doc.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    note.paragraph_format.space_before = Pt(28)
    run = note.add_run(
        "Memoire technique et financier - usage exclusif de la commission d'analyse."
        if lang == "fr"
        else "Technical and financial memorandum - for the evaluation committee only."
    )
    _style_run(run, size=10, color=RGBColor(*_SLATE))
    try:
        doc.add_page_break()
    except Exception:
        pass


def _style_data_table(table: Any) -> None:
    from docx.shared import RGBColor

    for r_i, row in enumerate(table.rows):
        for cell in row.cells:
            _set_cell_borders(cell)
            if r_i == 0:
                _shade_cell(cell, "1B365D")
                color = RGBColor(255, 255, 255)
                bold = True
            else:
                _shade_cell(cell, _ROW if r_i % 2 == 0 else "FFFFFF")
                color = RGBColor(*_SLATE)
                bold = False
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    _style_run(run, size=10, bold=bold, color=color)


def _add_docx_table(doc: Any, body: str) -> bool:
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in _clean(body).splitlines()
        if line.strip().startswith("|") and "---" not in line
    ]
    if len(rows) < 2:
        return False
    width = max(len(row) for row in rows)
    table = doc.add_table(rows=len(rows), cols=width)
    for r_i, row in enumerate(rows):
        for c_i in range(width):
            table.rows[r_i].cells[c_i].text = row[c_i] if c_i < len(row) else ""
    _style_data_table(table)
    doc.add_paragraph("")
    return True


def _add_docx_body(doc: Any, text: str) -> None:
    from docx.shared import Pt, RGBColor

    body = _clean(text).strip()
    if not body:
        return
    if body.startswith("|") and "\n| ---" in body:
        leftover = "\n".join(line for line in body.splitlines() if not line.strip().startswith("|"))
        _add_docx_table(doc, body)
        if leftover.strip():
            _add_docx_body(doc, leftover)
        return
    for block in body.split("\n\n"):
        chunk = block.strip()
        if not chunk:
            continue
        if chunk.startswith("|") and "---" in chunk:
            _add_docx_table(doc, chunk)
            continue
        for line in chunk.splitlines():
            row = line.strip()
            if not row:
                continue
            if re.match(r"^[-*]\s+", row):
                para = doc.add_paragraph(row[2:].strip(), style="List Bullet")
            elif re.match(r"^\d+[.)]\s+", row):
                para = doc.add_paragraph(re.sub(r"^\d+[.)]\s+", "", row), style="List Number")
            else:
                para = doc.add_paragraph(row)
            para.paragraph_format.space_after = Pt(6)
            for run in para.runs:
                _style_run(run, size=11, color=RGBColor(*_SLATE))


def _add_header_footer(doc: Any, tender: dict[str, Any], profile: dict[str, Any], response: dict[str, Any]) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import RGBColor

    lang = _lang_of(response)
    company = _clean(profile.get("name") or "")
    title = _clean(tender.get("title") or "")[:70]
    label = "Confidentiel" if lang == "fr" else "Confidential"
    for section in doc.sections:
        header = section.header.paragraphs[0]
        header.text = "  ·  ".join(part for part in (company, title) if part)
        for run in header.runs:
            _style_run(run, size=9, color=RGBColor(*_SLATE))
        _add_heading_rule(header)
        footer = section.footer.paragraphs[0]
        footer.text = f"{label}  ·  "
        for run in footer.runs:
            _style_run(run, size=9, color=RGBColor(*_SLATE))
        try:
            fld = OxmlElement("w:fldChar")
            fld.set(qn("w:fldCharType"), "begin")
            instr = OxmlElement("w:instrText")
            instr.text = " PAGE "
            end = OxmlElement("w:fldChar")
            end.set(qn("w:fldCharType"), "end")
            run = footer.add_run()
            run._r.append(fld)
            run._r.append(instr)
            run._r.append(end)
        except Exception:
            pass


def _write_docx_sections(doc: Any, tender: dict[str, Any], profile: dict[str, Any], response: dict[str, Any]) -> None:
    lang = _lang_of(response)
    _add_cover_page(doc, tender, profile, response)
    toc = _clean(response.get("toc") or "").strip()
    if toc:
        _add_docx_heading(doc, section_title("toc", lang))
        _add_docx_body(doc, toc)
        try:
            doc.add_page_break()
        except Exception:
            pass
    for key in SECTION_KEYS:
        if key in {"cover", "toc"}:
            continue
        body = _clean(response.get(key) or "").strip()
        if not body:
            continue
        _add_docx_heading(doc, section_title(key, lang))
        _add_docx_body(doc, body)
    _add_header_footer(doc, tender, profile, response)


def render_docx(
    store: Any,
    tender: dict[str, Any],
    profile: dict[str, Any],
    response: dict[str, Any],
) -> bytes | None:
    try:
        from docx import Document
    except ImportError:
        logger.warning("python-docx missing - Word pack skipped")
        return None
    mapping = _mapping(tender, profile, response)
    src = _first_template(profile, store, "word")
    doc = None
    filled = False
    if src is not None:
        try:
            doc = Document(str(src))
            filled = _fill_docx(doc, mapping)
        except Exception as exc:
            logger.warning("Word model {} could not be opened ({}) - generating a clean dossier", src.name, exc)
            doc = None
            filled = False
    if doc is None or not filled:
        doc = Document()
        _apply_theme(doc)
        _write_docx_sections(doc, tender, profile, response)
        try:
            doc.core_properties.title = _clean(tender.get("title") or "")
            doc.core_properties.author = _clean(profile.get("name") or "")
        except Exception:
            pass
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _fill_pptx_text(prs: Any, mapping: dict[str, str]) -> bool:
    changed = False
    for slide in prs.slides:
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for paragraph in shape.text_frame.paragraphs:
                full = "".join(run.text or "" for run in paragraph.runs) or paragraph.text
                new, hit = _replace_text(full, mapping)
                if not hit:
                    continue
                if paragraph.runs:
                    paragraph.runs[0].text = new
                    for run in paragraph.runs[1:]:
                        run.text = ""
                else:
                    paragraph.text = new
                changed = True
    return changed


def _pptx_lines(body: str, limit: int = 12) -> list[str]:
    rows: list[str] = []
    for raw in _clean(body).splitlines():
        line = raw.strip()
        if not line or line.startswith("| ---"):
            continue
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|") if cell.strip()]
            if cells:
                rows.append(" · ".join(cells))
            continue
        rows.append(re.sub(r"^[-*]\s+", "", line))
        if len(rows) >= limit:
            break
    return rows


def _add_pptx_cover(prs: Any, company: str, title: str, buyer: str, deadline: str, lang: str) -> None:
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[0]
    slide = prs.slides.add_slide(layout)
    width = prs.slide_width
    height = prs.slide_height
    fill = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, width, height)
    fill.fill.solid()
    fill.fill.fore_color.rgb = RGBColor(*_NAVY)
    fill.line.fill.background()
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, height - Inches(0.18), width, Inches(0.18))
    bar.fill.solid()
    bar.fill.fore_color.rgb = RGBColor(*_GOLD)
    bar.line.fill.background()
    box = slide.shapes.add_textbox(Inches(0.7), Inches(1.6), Inches(12.0), Inches(4.6))
    frame = box.text_frame
    frame.word_wrap = True
    kicker = frame.paragraphs[0]
    kicker.alignment = PP_ALIGN.LEFT
    run = kicker.add_run()
    run.text = "DOSSIER DE CANDIDATURE" if lang == "fr" else "SUBMISSION DOSSIER"
    run.font.size = Pt(14)
    run.font.bold = True
    run.font.color.rgb = RGBColor(*_GOLD)
    name = frame.add_paragraph()
    run = name.add_run()
    run.text = company
    run.font.size = Pt(32)
    run.font.bold = True
    run.font.color.rgb = RGBColor(255, 255, 255)
    notice = frame.add_paragraph()
    run = notice.add_run()
    run.text = title
    run.font.size = Pt(18)
    run.font.color.rgb = RGBColor(226, 232, 240)
    meta = frame.add_paragraph()
    run = meta.add_run()
    run.text = "  ·  ".join(part for part in (buyer, deadline) if part)
    run.font.size = Pt(14)
    run.font.color.rgb = RGBColor(203, 213, 225)


def _add_pptx_slide(prs: Any, title: str, body: str) -> None:
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    layout = prs.slide_layouts[1] if len(prs.slide_layouts) > 1 else prs.slide_layouts[0]
    slide = prs.slides.add_slide(layout)
    lines = _pptx_lines(body)
    filled = False
    for shape in slide.placeholders:
        name = str(getattr(shape, "name", "") or "").lower()
        idx = int(getattr(shape, "placeholder_format", type("X", (), {"idx": -1})).idx)
        if idx == 0 or "title" in name:
            shape.text = _clean(title)[:120]
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.color.rgb = RGBColor(*_NAVY)
                    run.font.bold = True
            filled = True
        elif getattr(shape, "has_text_frame", False):
            shape.text = (lines[0] if lines else _clean(body)[:220])[:220]
            frame = shape.text_frame
            frame.word_wrap = True
            for paragraph in frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(16)
                    run.font.color.rgb = RGBColor(*_SLATE)
            for line in lines[1:]:
                paragraph = frame.add_paragraph()
                paragraph.text = line[:220]
                paragraph.level = 0
                for run in paragraph.runs:
                    run.font.size = Pt(16)
                    run.font.color.rgb = RGBColor(*_SLATE)
            filled = True
    if filled:
        return
    box = slide.shapes.add_textbox(Inches(0.6), Inches(0.4), Inches(12.0), Inches(0.8))
    box.text_frame.paragraphs[0].text = _clean(title)
    box.text_frame.paragraphs[0].font.size = Pt(26)
    body_box = slide.shapes.add_textbox(Inches(0.6), Inches(1.4), Inches(12.0), Inches(5.6))
    body_box.text_frame.word_wrap = True
    body_box.text_frame.paragraphs[0].text = "\n".join(lines)[:1800]


def render_pptx(
    store: Any,
    tender: dict[str, Any],
    profile: dict[str, Any],
    response: dict[str, Any],
) -> bytes | None:
    try:
        from pptx import Presentation
    except ImportError:
        logger.warning("python-pptx missing - PowerPoint pack skipped")
        return None
    mapping = _mapping(tender, profile, response)
    src = _first_template(profile, store, "ppt")
    prs = None
    filled = False
    if src is not None:
        try:
            prs = Presentation(str(src))
            filled = _fill_pptx_text(prs, mapping)
        except Exception as exc:
            logger.warning("PPT model {} could not be opened ({}) - generating a clean deck", src.name, exc)
            prs = None
            filled = False
    if prs is None or not filled:
        prs = Presentation()
        lang = _lang_of(response)
        company = _clean(profile.get("name") or ("Candidat" if lang == "fr" else "Bidder"))
        title = _clean(tender.get("title") or ("Avis" if lang == "fr" else "Notice"))
        _add_pptx_cover(
            prs,
            company,
            title,
            _clean(tender.get("buyer") or ""),
            _clean(tender.get("deadline") or ""),
            lang,
        )
        toc = _clean(response.get("toc") or "").strip()
        if toc:
            _add_pptx_slide(prs, section_title("toc", lang), toc)
        for key in SECTION_KEYS:
            if key in {"cover", "toc"}:
                continue
            body = _clean(response.get(key) or "").strip()
            if body:
                _add_pptx_slide(prs, section_title(key, lang), body)
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def attach_exports(
    store: Any,
    tender: dict[str, Any],
    profile: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any]:
    """Write Word/PPT next to the notice and return export refs on the response."""
    out = dict(response)
    tid = _safe_name(str(tender.get("id") or "notice"), "notice")
    exports: dict[str, Any] = {}
    docx = render_docx(store, tender, profile, out)
    if docx:
        name = f"{tid}_response.docx"
        record = store.save_bytes(name, docx, file_id=f"out_docx_{tid}")
        exports["docx"] = {
            **record,
            "kind": "docx",
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    pptx = render_pptx(store, tender, profile, out)
    if pptx:
        name = f"{tid}_response.pptx"
        record = store.save_bytes(name, pptx, file_id=f"out_pptx_{tid}")
        exports["pptx"] = {
            **record,
            "kind": "pptx",
            "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
    if exports:
        out["exports"] = exports
        out["pack_ready"] = True
    return out
