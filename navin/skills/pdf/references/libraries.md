# PDF libraries shipped with Navin

| Library | Import | Best at | Avoid for |
|---------|--------|---------|-----------|
| pypdf | `from pypdf import PdfReader, PdfWriter` | Page-level operations (merge, split, rotate, crop), metadata, encryption, AcroForm fields, bookmarks | Text extraction with layout, rendering |
| pdfplumber | `import pdfplumber` | Text with positions, tables, words and characters with coordinates, cropping regions | Writing PDFs, large files (slow on hundreds of pages) |
| PyMuPDF | `import fitz` | Fast text, rendering pages to PNG, images, annotations, flattening forms, redaction, searching | Table detection quality lower than pdfplumber on messy layouts |
| reportlab | `from reportlab.pdfgen import canvas` | Drawing new content: overlays, stamps, simple generated pages, AcroForm widgets | Reading anything |

All four are bundled; do not install anything before trying.

## Text

```python
import pdfplumber
with pdfplumber.open("file.pdf") as pdf:
    page = pdf.pages[0]
    text = page.extract_text(layout=True)          # keeps columns aligned
    words = page.extract_words()                   # dicts with x0, x1, top, bottom, text
    region = page.crop((0, 0, page.width / 2, page.height)).extract_text()
```

```python
import fitz
doc = fitz.open("file.pdf")
text = doc[0].get_text("text")                    # fast plain text
blocks = doc[0].get_text("dict")["blocks"]        # spans with font, size, bbox
hits = doc[0].search_for("Total")                  # list of rectangles
```

A page whose `extract_text()` is empty while the page shows text is a scan: hand it to the OCR skill.

## Tables

```python
with pdfplumber.open("file.pdf") as pdf:
    tables = pdf.pages[2].extract_tables()         # list of tables, each a list of rows
    settings = {"vertical_strategy": "text", "horizontal_strategy": "text"}
    loose = pdf.pages[2].extract_tables(table_settings=settings)   # tables without ruling lines
```

Clean cells (`None` to "", strip whitespace, join wrapped lines) before loading them into pandas. For charts rendered as vectors there is no table; render the page and read it.

## Images

```python
doc = fitz.open("file.pdf")
for index, img in enumerate(doc[0].get_images(full=True)):
    pix = fitz.Pixmap(doc, img[0])
    if pix.n > 4:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    pix.save(f"image-{index}.png")
```

Render a whole page: `doc[0].get_pixmap(dpi=150).save("page-1.png")`.

## Metadata

```python
from pypdf import PdfReader, PdfWriter
reader = PdfReader("file.pdf")
print(reader.metadata.title, reader.metadata.author, len(reader.pages))
writer = PdfWriter(clone_from=reader)
writer.add_metadata({"/Title": "Quarterly report", "/Author": "Navin"})
writer.write("out.pdf")
```

## Encryption

```python
reader = PdfReader("locked.pdf")
if reader.is_encrypted:
    reader.decrypt("password")            # ask the user; never try to crack it
writer = PdfWriter(clone_from=reader)
writer.encrypt(user_password="read", owner_password="admin", algorithm="AES-256")
writer.write("protected.pdf")
```

## Watermark or stamp

```python
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from pypdf import PdfReader, PdfWriter
import io

buf = io.BytesIO()
c = canvas.Canvas(buf, pagesize=A4)
c.setFont("Helvetica-Bold", 48)
c.setFillColorRGB(0.78, 0.91, 0.54, alpha=0.35)   # Lime at 35 percent
c.saveState(); c.translate(300, 400); c.rotate(35); c.drawCentredString(0, 0, "DRAFT"); c.restoreState()
c.save()
stamp = PdfReader(buf).pages[0]

writer = PdfWriter()
for page in PdfReader("file.pdf").pages:
    page.merge_page(stamp)
    writer.add_page(page)
writer.write("stamped.pdf")
```

`pdf_overlay.py` wraps the same mechanism for text at coordinates.

## Bookmarks (outline)

```python
writer = PdfWriter(clone_from=PdfReader("file.pdf"))
parent = writer.add_outline_item("Chapter 1", 0)
writer.add_outline_item("Section 1.1", 2, parent=parent)
writer.write("with-outline.pdf")
```

Read an existing outline with `reader.outline` (nested lists of destinations).

## Redaction

```python
doc = fitz.open("file.pdf")
for page in doc:
    for rect in page.search_for("123-45-6789"):
        page.add_redact_annot(rect, fill=(0.09, 0.09, 0.1))
    page.apply_redactions()
doc.save("redacted.pdf")
```

Redaction removes the text from the content stream, unlike a black rectangle drawn over it.

## Coordinates

- PDF points: 72 per inch; A4 is 595 x 842, US Letter 612 x 792.
- pypdf and reportlab use the PDF origin at the bottom-left corner, y grows upward.
- pdfplumber and PyMuPDF report `top` / `y0` from the top-left corner, y grows downward. Convert with `y_pdf = page_height - y_top`.
- Rendering at `dpi` scales points by `dpi / 72`; a grid PNG at 100 dpi has 1 point = 1.39 px.

## Performance notes

- Open a file once and keep the handle; pdfplumber reopening per page is slow.
- For hundreds of pages prefer PyMuPDF text extraction and only fall back to pdfplumber on pages with tables.
- Rendering at 300 dpi for review is wasteful; 100 to 150 dpi is enough to read.
