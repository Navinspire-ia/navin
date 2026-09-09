# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Word constructs the converters need beyond what python-docx exposes.

python-docx covers paragraphs, runs and tables. A document that reads as though
somebody laid it out by hand needs more: a table of contents Word can refresh,
figure and table numbering that renumbers itself, cross references that follow
their target, section breaks so one page can turn landscape, and the pagination
controls that stop a heading from being stranded alone at the foot of a page.

All of that is raw WordprocessingML, and it lives here so the converters stay
about reading HTML rather than about the shape of the format they write.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Word measures borders and spacing in eighths of a point and twentieths of a
# point respectively; both show up often enough to name once.
EIGHTHS_PER_POINT = 8
TWIPS_PER_POINT = 20

_BOOKMARK_SAFE = re.compile(r"[^0-9A-Za-z_]")
# Word rejects bookmark names past 40 characters and refuses one that does not
# start with a letter.
_BOOKMARK_MAX = 40


def element(tag: str, **attributes: str):
    """One ``w:``-namespaced element with ``w:``-namespaced attributes."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    node = OxmlElement(f"w:{tag}")
    for key, value in attributes.items():
        node.set(qn(f"w:{key}"), value)
    return node


def bookmark_name(raw: str) -> str:
    """A Word-legal bookmark name for an author-supplied identifier.

    Authors write ``fig:architecture`` in the HTML. Word accepts letters,
    digits and underscores only, has to start on a letter, and stops at 40
    characters, so the name is folded rather than rejected: a cross reference
    that silently points nowhere is worse than an ugly name.
    """
    cleaned = _BOOKMARK_SAFE.sub("_", raw.strip())
    cleaned = cleaned.strip("_") or "ref"
    if not cleaned[0].isalpha():
        cleaned = f"b_{cleaned}"
    return cleaned[:_BOOKMARK_MAX]


def set_pagination(
    paragraph,
    *,
    keep_next: bool = False,
    keep_lines: bool = False,
    page_break_before: bool = False,
    widow_control: bool | None = None,
) -> None:
    """Apply Word's page-break controls to one paragraph.

    ``keep_next`` binds a paragraph to the one after it, which is what keeps a
    heading with its first line and a caption with its figure. ``keep_lines``
    forbids splitting the paragraph itself.
    """
    from docx.oxml.ns import qn

    properties = paragraph._p.get_or_add_pPr()
    wanted: list[tuple[str, bool | None]] = [
        ("keepNext", True if keep_next else None),
        ("keepLines", True if keep_lines else None),
        ("pageBreakBefore", True if page_break_before else None),
        ("widowControl", widow_control),
    ]
    for tag, value in wanted:
        existing = properties.find(qn(f"w:{tag}"))
        if existing is not None:
            properties.remove(existing)
        if value is None:
            continue
        properties.append(element(tag, val="true" if value else "false"))


def keep_row_together(row) -> None:
    """Forbid Word from splitting one table row across two pages."""
    row._tr.get_or_add_trPr().append(element("cantSplit", val="true"))


def add_field(paragraph, instruction: str, placeholder: str = "") -> None:
    """Append a complex field, with the text Word shows before it updates.

    The placeholder matters for anything Word computes on open: until the
    reader refreshes, the field body is all there is to show, and an empty one
    reads as a hole in the page.
    """
    run = paragraph.add_run()
    run._element.append(element("fldChar", fldCharType="begin"))
    code = element("instrText", space="preserve")
    code.text = f" {instruction} "
    run._element.append(code)
    run._element.append(element("fldChar", fldCharType="separate"))
    if placeholder:
        body = paragraph.add_run(placeholder)
        body._element.append(element("fldChar", fldCharType="end"))
        return
    run._element.append(element("fldChar", fldCharType="end"))


def add_table_of_contents(paragraph, levels: str = "1-3") -> None:
    r"""Insert a real ``TOC`` field over the given heading levels.

    ``\h`` makes the entries hyperlinks, ``\z`` hides the page numbers in web
    view and ``\u`` builds the table from the outline levels of the styles,
    which is what lets a reader refresh it after editing the document.
    """
    add_field(
        paragraph,
        f'TOC \\o "{levels}" \\h \\z \\u',
        placeholder="Update this field to build the table of contents.",
    )


def add_sequence(paragraph, label: str) -> None:
    """Insert a ``SEQ`` counter, the numbering behind "Figure 3"."""
    add_field(paragraph, f"SEQ {label} \\* ARABIC", placeholder="1")


def add_bookmark(paragraph, name: str, bookmark_id: int) -> None:
    """Wrap the paragraph content in a bookmark a cross reference can target."""
    from docx.oxml.ns import qn

    safe = bookmark_name(name)
    start = element("bookmarkStart", id=str(bookmark_id), name=safe)
    end = element("bookmarkEnd", id=str(bookmark_id))
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        paragraph._p.insert(0, start)
    else:
        properties.addnext(start)
    paragraph._p.append(end)


def add_cross_reference(paragraph, name: str, placeholder: str = "") -> None:
    """Insert a ``REF`` field pointing at a bookmark elsewhere in the document."""
    add_field(
        paragraph,
        f"REF {bookmark_name(name)} \\h",
        placeholder=placeholder or bookmark_name(name),
    )


def request_field_update(document) -> None:
    """Ask Word to offer refreshing the fields when the file opens.

    Without this a freshly generated table of contents shows its placeholder
    until somebody thinks to press F9, which reads as a broken document.
    """
    from docx.oxml.ns import qn

    settings = document.settings.element
    if settings.find(qn("w:updateFields")) is not None:
        return
    settings.append(element("updateFields", val="true"))


def set_columns(section, count: int, space_twips: int = 425) -> None:
    """Lay a section out over several newspaper columns."""
    from docx.oxml.ns import qn

    properties = section._sectPr
    existing = properties.find(qn("w:cols"))
    if existing is not None:
        properties.remove(existing)
    properties.append(
        element("cols", num=str(max(1, count)), space=str(max(0, space_twips)))
    )


def set_orientation(section, landscape: bool) -> None:
    """Turn a section landscape or portrait, swapping the page dimensions.

    python-docx sets the orientation flag but leaves the page size alone, and a
    landscape flag on a portrait-sized page changes nothing on screen.
    """
    from docx.enum.section import WD_ORIENT

    width, height = section.page_width, section.page_height
    section.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
    if landscape != (width > height):
        section.page_width, section.page_height = height, width


_HEADING_NUMBER_INDENTS = (0, 340, 680, 1020, 1360, 1700, 2040, 2380, 2720)


def bind_heading_numbering(document, levels: int = 4) -> int | None:
    """Number the Heading styles 1., 1.1, 1.1.1 the way Word does natively.

    Applied to the styles rather than to each paragraph: the numbering then
    survives editing, renumbers itself when a section moves, and feeds the
    table of contents. Returns the numbering id, or None when the document has
    no numbering part to extend.
    """
    from docx.oxml.ns import qn

    try:
        numbering = document.part.numbering_part.element
    except (AttributeError, KeyError, NotImplementedError):
        return None

    abstract_id = _next_id(numbering, "abstractNum", "abstractNumId", start=100)
    abstract = element("abstractNum", abstractNumId=str(abstract_id))
    abstract.append(element("multiLevelType", val="multilevel"))
    for index in range(9):
        abstract.append(_numbering_level(index, levels))
    first_num = numbering.find(qn("w:num"))
    if first_num is not None:
        first_num.addprevious(abstract)
    else:
        numbering.append(abstract)

    num_id = _next_id(numbering, "num", "numId", start=100)
    num = element("num", numId=str(num_id))
    num.append(element("abstractNumId", val=str(abstract_id)))
    numbering.append(num)

    for level in range(1, min(levels, 9) + 1):
        _bind_style_numbering(document, f"Heading {level}", num_id, level - 1)
    return num_id


def _numbering_level(index: int, numbered_levels: int):
    """One ``w:lvl``: numbered for the levels asked for, blank beyond them."""
    level = element("lvl", ilvl=str(index))
    level.append(element("start", val="1"))
    if index < numbered_levels:
        level.append(element("numFmt", val="decimal"))
        text = ".".join(f"%{depth + 1}" for depth in range(index + 1))
        level.append(element("lvlText", val=f"{text}."))
    else:
        level.append(element("numFmt", val="none"))
        level.append(element("lvlText", val=""))
    level.append(element("lvlJc", val="left"))
    properties = element("pPr")
    indent = _HEADING_NUMBER_INDENTS[index]
    properties.append(
        element("ind", left=str(indent), hanging=str(340 if index < numbered_levels else 0))
    )
    level.append(properties)
    return level


def _bind_style_numbering(document, style_name: str, num_id: int, level: int) -> None:
    from docx.oxml.ns import qn

    try:
        style = document.styles[style_name]
    except KeyError:  # pragma: no cover - the default template has Heading 1..9
        return
    properties = style.element.get_or_add_pPr()
    existing = properties.find(qn("w:numPr"))
    if existing is not None:
        properties.remove(existing)
    holder = element("numPr")
    holder.append(element("ilvl", val=str(level)))
    holder.append(element("numId", val=str(num_id)))
    properties.append(holder)


def _next_id(parent, tag: str, attribute: str, start: int = 100) -> int:
    """An id no sibling is using, so an added definition never collides."""
    from docx.oxml.ns import qn

    used = set()
    for node in parent.findall(qn(f"w:{tag}")):
        raw = node.get(qn(f"w:{attribute}"))
        try:
            used.add(int(raw))
        except (TypeError, ValueError):
            continue
    candidate = start
    while candidate in used:
        candidate += 1
    return candidate


def parse_directives(raw: Any) -> dict[str, str]:
    """Normalize the ``data-doc-*`` payload measured off one element."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): str(value).strip()
        for key, value in raw.items()
        if isinstance(key, str) and str(value).strip()
    }


def keep_flags(value: str) -> tuple[bool, bool]:
    """Read ``data-doc-keep`` into (keep_next, keep_lines)."""
    tokens = {token for token in re.split(r"[\s,]+", value.lower()) if token}
    if "all" in tokens:
        return True, True
    return "next" in tokens, "together" in tokens or "lines" in tokens


def iter_levels(value: str, maximum: int = 9) -> Iterable[int]:
    """Expand a ``1-3`` heading range into the levels it covers."""
    match = re.match(r"^\s*(\d)\s*-\s*(\d)\s*$", value or "")
    if not match:
        return range(1, 4)
    low, high = int(match.group(1)), int(match.group(2))
    if low > high:
        low, high = high, low
    return range(max(1, low), min(maximum, high) + 1)
