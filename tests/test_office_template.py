"""office_template: inspect, fill and build from the user's own .docx / .pptx."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.documents import office_template
from navin.documents.office_template import TemplateError


def _png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (120, 60), (30, 90, 200)).save(path)
    return path


def _letter(path: Path) -> Path:
    """A letterhead: header, split placeholder, list, repeated rows, image."""
    from docx import Document

    document = Document()
    document.sections[0].header.paragraphs[0].text = "ACME - {{ref}}"
    heading = document.add_paragraph(style="Heading 1")
    heading.add_run("Offer for ").bold = True
    heading.add_run("{{cli")
    heading.add_run("ent}}")
    document.add_paragraph("Dear {{contact.name}},")
    document.add_paragraph("{{points}}", style="List Bullet")
    document.add_paragraph("{{image:logo|3cm}}")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Item"
    table.rows[0].cells[1].text = "Price"
    table.rows[1].cells[0].text = "{{lines.item}}"
    table.rows[1].cells[1].text = "{{lines.price}}"
    document.add_paragraph("Sincerely, {{signer}}")
    document.save(str(path))
    return path


class DocxInspectTests(unittest.TestCase):
    def test_placeholders_styles_and_sections_are_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            info = office_template.inspect(_letter(Path(tmp) / "letter.docx"))
        self.assertEqual(info["kind"], "docx")
        for key in ("ref", "client", "contact.name", "points", "image:logo|3cm", "lines.item", "signer"):
            self.assertIn(key, info["placeholders"], key)
        self.assertEqual(info["sections"][0]["orientation"], "portrait")
        self.assertIn("ACME", info["sections"][0]["header"])
        self.assertIn("Heading 1", info["styles_used"])
        self.assertEqual(len(info["tables"]), 1)


class DocxFillTests(unittest.TestCase):
    def test_every_kind_of_placeholder_is_filled_in_place(self) -> None:
        from docx import Document

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            template = _letter(folder / "letter.docx")
            _png(folder / "logo.png")
            data = {
                "ref": "OF-2026-17",
                "client": "Globex",
                "contact": {"name": "Ada"},
                "points": ["Fast delivery", "Fixed price"],
                "logo": "logo.png",
                "lines": [{"item": "Audit", "price": "4 000"}, {"item": "Build", "price": "12 000"}],
                "signer": "Aymen",
                "title": "Offer Globex",
            }
            (folder / "values.json").write_text(json.dumps(data), encoding="utf-8")
            report = office_template.fill(template, data, folder / "out" / "offer.docx", data_path=folder / "values.json")
            document = Document(str(folder / "out" / "offer.docx"))
            texts = [p.text for p in document.paragraphs]
            header = document.sections[0].header.paragraphs[0].text
            rows = [[c.text for c in r.cells] for r in document.tables[0].rows]
            images = len(document.inline_shapes)
            heading_runs = [(r.text, r.bold) for r in document.paragraphs[0].runs]
            title = document.core_properties.title

        self.assertEqual(report["missing"], [])
        self.assertGreaterEqual(report["replaced"], 6)
        self.assertEqual(report["rows_expanded"], 2)
        self.assertEqual(report["images"], 1)
        self.assertEqual(header, "ACME - OF-2026-17")
        # The placeholder split over three runs is joined and the bold run kept.
        self.assertEqual("".join(t for t, _ in heading_runs), "Offer for Globex")
        self.assertEqual(heading_runs[0], ("Offer for ", True))
        self.assertIn("Dear Ada,", texts)
        self.assertIn("Fast delivery", texts)
        self.assertIn("Fixed price", texts)
        self.assertNotIn("{{points}}", texts)
        self.assertEqual(rows, [["Item", "Price"], ["Audit", "4 000"], ["Build", "12 000"]])
        self.assertEqual(images, 1)
        self.assertIn("Sincerely, Aymen", texts)
        self.assertEqual(title, "Offer Globex")
        self.assertFalse(any("{{" in t for t in texts), texts)

    def test_missing_values_are_reported_and_left_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            template = _letter(folder / "letter.docx")
            report = office_template.fill(template, {"ref": "X"}, folder / "out.docx")
        self.assertIn("client", report["missing"])
        self.assertIn("signer", report["missing"])
        self.assertTrue(any(m.startswith("image:logo") for m in report["missing"]))

    def test_strict_mode_fails_the_cli_on_a_missing_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            template = _letter(folder / "letter.docx")
            values = folder / "values.json"
            values.write_text(json.dumps({"ref": "X"}), encoding="utf-8")
            code = office_template.main(
                ["fill", str(template), "--data", str(values), "-o", str(folder / "o.docx"), "--strict"]
            )
            self.assertEqual(code, 1)
            code = office_template.main(
                ["fill", str(template), "--data", str(values), "-o", str(folder / "o.docx"), "--json"]
            )
            self.assertEqual(code, 0)

    def test_the_wrong_format_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "x.xlsx"
            other.write_bytes(b"PK")
            with self.assertRaises(TemplateError):
                office_template.fill(other, {}, Path(tmp) / "o.xlsx")
            with self.assertRaises(TemplateError):
                office_template.inspect(Path(tmp) / "missing.docx")


def _brand_deck(path: Path) -> Path:
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "{{deck_title}}"
    slide.placeholders[1].text_frame.text = "Prepared for {{client}}"
    slide.notes_slide.notes_text_frame.text = "Say hello to {{client}}"
    presentation.save(str(path))
    return path


class PptxTests(unittest.TestCase):
    def test_inspect_lists_layouts_placeholders_and_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            info = office_template.inspect(_brand_deck(Path(tmp) / "brand.pptx"))
        self.assertEqual(info["kind"], "pptx")
        names = [layout["name"] for layout in info["layouts"]]
        self.assertIn("Title and Content", names)
        title_layout = next(layout for layout in info["layouts"] if layout["name"] == "Title Slide")
        self.assertTrue(any(p["type"] in ("TITLE", "CENTER_TITLE") for p in title_layout["placeholders"]))
        self.assertIn("deck_title", info["placeholders"])
        self.assertIn("client", info["placeholders"])
        self.assertEqual(tuple(info["slide_size_in"]), (10.0, 7.5))
        self.assertTrue(info["theme"]["fonts"].get("body"))

    def test_fill_reaches_titles_bodies_and_notes(self) -> None:
        from pptx import Presentation

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            template = _brand_deck(folder / "brand.pptx")
            report = office_template.fill(
                template, {"deck_title": "Kickoff", "client": "Globex"}, folder / "filled.pptx"
            )
            presentation = Presentation(str(folder / "filled.pptx"))
            slide = presentation.slides[0]
            title = slide.shapes.title.text
            body = slide.placeholders[1].text_frame.text
            notes = slide.notes_slide.notes_text_frame.text
            deck_title = presentation.core_properties.title
        self.assertEqual(report["missing"], [])
        self.assertEqual(title, "Kickoff")
        self.assertEqual(body, "Prepared for Globex")
        self.assertEqual(notes, "Say hello to Globex")
        self.assertEqual(deck_title, "Kickoff")

    def test_build_uses_the_templates_layouts_and_drops_its_samples(self) -> None:
        from pptx import Presentation

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            template = _brand_deck(folder / "brand.pptx")
            _png(folder / "chart.png")
            deck = {
                "title": "Q3 review",
                "slides": [
                    {"layout": "Title Slide", "title": "Q3 review", "subtitle": "September 2026"},
                    {
                        "layout": "Title and Content",
                        "title": "Three wins",
                        "body": ["Revenue +12%", {"text": "Churn down", "children": ["2.1% now"]}, "Two new markets"],
                        "notes": "Pause on the churn point.",
                    },
                    {"layout": "Title and Content", "title": "Pipeline", "table": {"headers": ["Stage", "Deals"], "rows": [["Lead", "40"], ["Won", "9"]]}},
                    {"layout": "Title Only", "title": "The chart", "picture": "chart.png"},
                ],
            }
            report = office_template.build(template, deck, folder / "q3.pptx", deck_path=folder / "deck.json")
            presentation = Presentation(str(folder / "q3.pptx"))
            slides = list(presentation.slides)
            layouts = [slide.slide_layout.name for slide in slides]
            wins = slides[1]
            body = next(s for s in wins.placeholders if s.placeholder_format.idx != 0)
            paragraphs = [(p.text, p.level) for p in body.text_frame.paragraphs]
            notes = wins.notes_slide.notes_text_frame.text
            table = next(s for s in slides[2].shapes if getattr(s, "has_table", False))
            grid = [[c.text for c in r.cells] for r in table.table.rows]
            pictures = [s for s in slides[3].shapes if s.shape_type == 13]
            empty_boxes = [
                s for slide in slides for s in slide.placeholders
                if s.has_text_frame and not s.text_frame.text.strip()
            ]
            title = presentation.core_properties.title

        self.assertEqual(report["template_slides_removed"], 1)
        self.assertEqual(report["slides"], 4)
        self.assertEqual(report["warnings"], [])
        self.assertEqual(layouts, ["Title Slide", "Title and Content", "Title and Content", "Title Only"])
        self.assertEqual(paragraphs, [("Revenue +12%", 0), ("Churn down", 0), ("2.1% now", 1), ("Two new markets", 0)])
        self.assertEqual(notes, "Pause on the churn point.")
        self.assertEqual(grid, [["Stage", "Deals"], ["Lead", "40"], ["Won", "9"]])
        self.assertEqual(len(pictures), 1)
        self.assertEqual(empty_boxes, [])
        self.assertEqual(title, "Q3 review")

    def test_build_refuses_a_word_template_and_an_empty_deck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with self.assertRaises(TemplateError):
                office_template.build(_letter(folder / "l.docx"), {"slides": [{}]}, folder / "o.pptx")
            with self.assertRaises(TemplateError):
                office_template.build(_brand_deck(folder / "b.pptx"), {"slides": []}, folder / "o.pptx")


if __name__ == "__main__":
    unittest.main()
