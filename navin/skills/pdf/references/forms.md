# PDF forms: fields, filling, flattening, overlays

## Two kinds of forms

1. **Fillable (AcroForm)**: the PDF declares interactive fields. `pdf_forms.py fields` lists them; fill with `pdf_forms.py fill`.
2. **Printed (no fields)**: a scanned or exported form with blanks drawn as lines or boxes. Nothing to fill programmatically; write text on top with `pdf_overlay.py`.

`pdf_pages.py info` reports `has_form: true/false`. XFA forms (older Adobe LiveCycle output) are a third kind: pypdf cannot fill them; say so and fall back to the overlay method.

## Field types

| Type (`/FT`) | Widget | Value to write |
|--------------|--------|----------------|
| `/Tx` | Text box, may be multiline (`/Ff` bit 13) or a comb field | A string |
| `/Btn` checkbox | One widget, two appearance states | The export value of the "on" state (`/Yes`, `/On`, `/1`, anything the author chose) or `/Off` |
| `/Btn` radio group | Several widgets sharing a parent | The export value of the chosen widget |
| `/Btn` push button | Action button | Not fillable |
| `/Ch` list or combo | Choice | One of the `/Opt` values (the export value when options are pairs) |
| `/Sig` | Signature | Not fillable here; leave for the signer |

`pdf_forms.py fields --json` prints, per field, `name`, `type`, `value`, `options` (export values for checkbox, radio and choice fields), `flags` (read-only, required) and the page. Names can contain dots (hierarchy) and spaces: copy them exactly.

## Filling with pypdf

```python
from pypdf import PdfReader, PdfWriter
reader = PdfReader("form.pdf")
writer = PdfWriter(clone_from=reader)
for page in writer.pages:
    writer.update_page_form_field_values(page, {"Name": "Jane Doe", "Agree": "/Yes"}, auto_regenerate=False)
writer.set_need_appearances_writer(True)
writer.write("filled.pdf")
```

- `update_page_form_field_values` must run on every page because widgets can sit anywhere.
- Checkbox values are names: pass the export value with the leading slash (`"/Yes"`). `fields` shows it.
- Radio groups: set the value on the parent field name.
- Read-only fields are skipped; report them rather than forcing.

## Appearance streams and NeedAppearances

A field stores a value (`/V`) and, optionally, an appearance stream (`/AP`) that draws it. After a programmatic fill the appearance may be stale or missing. Two remedies:

- Set `/NeedAppearances true` on the AcroForm (what `pdf_forms.py fill` does): compliant viewers regenerate the appearance on open. Some viewers (browser previews, thumbnails) ignore it and show empty boxes.
- Flatten: regenerate appearances and bake them into the page content so every viewer shows the values and nothing stays editable.

## Flattening with PyMuPDF

```python
import fitz
doc = fitz.open("filled.pdf")
for page in doc:
    for widget in page.widgets():
        widget.update()                 # rebuild the appearance from the value
doc.bake()                              # or: doc.save(..., garbage=3) after converting widgets
doc.save("flat.pdf")
```

`pdf_forms.py fill --flatten` runs this after the pypdf fill. Flattening is irreversible: keep the fillable copy as well when the user may need to edit later.

## Overlay method for printed forms

1. Measure: `pdf_overlay.py form.pdf --grid --page 1 --step 50 --dpi 100 --out-dir grid/` renders the page with labelled lines every 50 points. Open the PNG, locate each blank and read its x (from the left) and y (from the bottom). Text sits on its baseline: choose y a few points above the printed line.
2. Write: an items file

```json
[
  {"page": 1, "x": 140, "y": 702, "text": "Jane Doe", "size": 10},
  {"page": 1, "x": 140, "y": 676, "text": "12 rue des Lilas, 75011 Paris", "size": 9},
  {"page": 1, "x": 92, "y": 611, "text": "X", "size": 11}
]
```

   then `pdf_overlay.py form.pdf --items items.json --out filled.pdf`. A checkbox is an "X" placed in the box. Colors default to Ink `#17171a`.
3. Verify: `pdf_pages.py render filled.pdf --pages 1 --dpi 110 --out-dir check/` and compare with the grid. Adjust coordinates in steps of 2 to 4 points.

Fonts: the overlay uses Helvetica by default; pass `--font Courier` for comb fields, or a TTF path for accented or non-Latin text (reportlab registers it on the fly).

## Pitfalls

- Field names are case-sensitive and may include hidden hierarchy (`topmostSubform[0].Page1[0].Name[0]`): always copy them from `fields`.
- A checkbox that stays empty after filling usually received `"Yes"` instead of `"/Yes"`, or the export value is something else (`/1`, `/Oui`).
- Dates: forms expect a format (`DD/MM/YYYY`); mimic the placeholder text or the JavaScript format action shown in the field flags.
- Multiline text does not wrap by itself in some viewers; keep lines short or insert `\n`.
- Encrypted forms must be decrypted with the owner password before filling; ask the user.
- Never fill a signature field; leave it for the person who signs.
