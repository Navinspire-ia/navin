# OOXML anatomy for .docx files

A .docx is a zip archive. Nothing is magic: every change Word can make is a change in one of these XML parts.

## Package layout

| Part | Role |
|------|------|
| `[Content_Types].xml` | Declares the content type of every part by extension or by exact path. Any part missing here is ignored or breaks the file. Must be the first zip entry. |
| `_rels/.rels` | Root relationships: points to `word/document.xml`, core properties, app properties. |
| `word/document.xml` | The body: `w:body` with paragraphs (`w:p`), tables (`w:tbl`) and a final `w:sectPr` (page size, margins, header and footer references). |
| `word/_rels/document.xml.rels` | Relationships from the body to styles, numbering, headers, footers, footnotes, comments, images (`media/*`), hyperlinks (external targets). Every `r:id` / `r:embed` in document.xml must resolve here. |
| `word/styles.xml` | Paragraph, character, table and numbering styles. `w:styleId` is what paragraphs reference; `w:name` is what Word shows. |
| `word/numbering.xml` | Abstract numbering definitions (`w:abstractNum`) and instances (`w:num`). A paragraph joins a list with `w:numPr` (`w:numId` + `w:ilvl`). |
| `word/settings.xml` | Document settings, including `w:trackRevisions` (tracking on) and compatibility mode. |
| `word/header1.xml`, `word/footer1.xml` | Referenced from `w:sectPr` through `w:headerReference` / `w:footerReference` with `w:type` default, first or even. |
| `word/footnotes.xml`, `word/endnotes.xml` | Note bodies; the body holds `w:footnoteReference w:id` markers. |
| `word/comments.xml` | Comment bodies; the body holds `w:commentRangeStart`, `w:commentRangeEnd` and a run with `w:commentReference`. Newer files add `commentsExtended.xml`, `commentsIds.xml`, `commentsExtensible.xml` for threading and resolution state. |
| `word/media/*` | Images. Referenced through relationships, never by path from the XML. |
| `docProps/core.xml`, `docProps/app.xml` | Title, author, created and modified dates, application name. |

The main namespace is `http://schemas.openxmlformats.org/wordprocessingml/2006/main` (prefix `w`). Relationships use `http://schemas.openxmlformats.org/officeDocument/2006/relationships` (prefix `r`).

## Paragraphs and runs

```xml
<w:p>
  <w:pPr>
    <w:pStyle w:val="Heading1"/>
    <w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr>
    <w:jc w:val="both"/>
  </w:pPr>
  <w:r>
    <w:rPr><w:b/><w:sz w:val="24"/></w:rPr>
    <w:t xml:space="preserve">Bold text </w:t>
  </w:r>
  <w:r><w:t>plain text</w:t></w:r>
</w:p>
```

- `w:pPr` holds paragraph properties: style, numbering, alignment, spacing, indentation, `w:rPr` for the paragraph mark.
- `w:r` is a run: a stretch of text with one set of character properties (`w:rPr`). Word splits runs freely (spell-check history, formatting edits), so a single sentence often spans several runs. Searching for text must concatenate runs; replacing must put the new text in the first matched run and empty the others, which is what `docx_text.py replace` does.
- `w:t` carries text. Set `xml:space="preserve"` whenever the text starts or ends with a space.
- Line break inside a paragraph: `w:br`. Tab: `w:tab`. Page break: `<w:br w:type="page"/>`.
- Font sizes in `w:sz` are half-points: `24` means 12 pt. Spacing and indentation are in twentieths of a point (twips): 1440 twips = 1 inch.

## Tables

`w:tbl` > `w:tblPr` (style, width, borders) > `w:tblGrid` (column widths) > `w:tr` rows > `w:tc` cells. Each cell contains at least one `w:p`. Merged cells use `w:gridSpan` (horizontal) and `w:vMerge` (vertical: `restart` on the first cell, empty on the continued ones). A cell without a paragraph corrupts the file.

## Styles and numbering

- Paragraph style reference: `w:pStyle w:val="StyleId"`. The style must exist in styles.xml; an unknown id silently falls back to Normal.
- Direct formatting (`w:rPr` on the run) always wins over the style. When a replacement looks wrong, check for direct formatting on the original run.
- Lists: every numbered paragraph references `w:numId`. To restart numbering, create a new `w:num` pointing to the same `w:abstractNumId` with a `w:lvlOverride` / `w:startOverride`.

## Headers, footers and sections

`w:sectPr` ends the body (or a paragraph, for mid-document section breaks). It lists `w:headerReference` and `w:footerReference` by relationship id, page size (`w:pgSz`), margins (`w:pgMar`) and `w:titlePg` for a distinct first page. Text replacement must visit header and footer parts too; the scripts here do.

## Tracked changes

```xml
<w:ins w:id="7" w:author="Navin" w:date="2026-10-29T10:00:00Z">
  <w:r><w:t>45 days</w:t></w:r>
</w:ins>
<w:del w:id="8" w:author="Navin" w:date="2026-10-29T10:00:00Z">
  <w:r><w:delText>30 days</w:delText></w:r>
</w:del>
```

- Inserted runs are wrapped in `w:ins`; deleted runs are wrapped in `w:del` and their text uses `w:delText` instead of `w:t`.
- Formatting changes are recorded as `w:rPrChange` (inside `w:rPr`) or `w:pPrChange` (inside `w:pPr`) holding the previous properties.
- Paragraph-mark insertions and deletions live in `w:pPr/w:rPr/w:ins` or `w:del`.
- `w:id` must be unique across the part. The date is ISO 8601 UTC.
- Accept = unwrap `w:ins`, remove `w:del`, drop the `*Change` records. Reject = remove `w:ins`, unwrap `w:del` converting `w:delText` back to `w:t`.
- Turning tracking on for the user's next edits: add `<w:trackRevisions/>` to settings.xml.

## Comments

```xml
<w:commentRangeStart w:id="0"/>
<w:r><w:t>Payment terms</w:t></w:r>
<w:commentRangeEnd w:id="0"/>
<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="0"/></w:r>
```

`word/comments.xml` holds `<w:comment w:id="0" w:author="..." w:date="..." w:initials="...">` with one or more paragraphs. The comments part needs its content type override and a relationship of type `.../comments` from document.xml.rels. python-docx 1.2 exposes `Document.add_comment(runs, text, author, initials)`; `docx_comments.py` uses it and reports clearly when the installed version is older.

## Pitfalls that corrupt a file

1. A part referenced in `[Content_Types].xml` or in a `.rels` file that does not exist in the zip, or a part present in the zip with no content type.
2. `[Content_Types].xml` not being the first entry, or the archive written with a directory entry Word does not expect.
3. Pretty-printed XML left in place: whitespace between `w:r` elements is harmless, but whitespace inside `w:t` changes the text. `office_pkg.py pack` minifies for that reason.
4. Duplicate `w:id` values on revisions, comments or bookmarks.
5. A `w:tc` with no `w:p`, or a `w:tbl` with no `w:tblGrid`.
6. Unescaped `&`, `<` or `>` in text nodes when editing by hand. Use lxml or `edit_file` on a well-formed snippet; never string-concatenate raw user text into XML.
7. Fonts: declaring `w:rFonts` for a font that is not installed on the reader's machine is allowed, but Arabic or CJK text needs a font with the glyphs or it renders as boxes. Prefer Noto families for those scripts.

## Quick checks

- `office_pkg.py validate file.docx --json` catches points 1, 2, 4 and 6 and performs a python-docx round trip.
- `docx_text.py dump file.docx --format json` lists every paragraph, including table cells, with its style; an empty dump on a non-empty file means the text lives in text boxes (`w:txbxContent`) or in headers only.
- `unzip -l file.docx` shows the parts and their order.
