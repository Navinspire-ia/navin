"""The Word theme layer: tokens, instant re-theming, and the audit behind it."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from navin.documents import _fonts, word_design

ROOT = Path(__file__).resolve().parents[1]
WORD_TEMPLATES = ROOT / "templates" / "word"
TEMPLATE_FILES = sorted(WORD_TEMPLATES.glob("*/document.html"))
EXPECTED_THEMES = 15


class ThemeCatalogTests(unittest.TestCase):
    def test_catalog_declares_the_documented_themes(self) -> None:
        names = word_design.theme_names()
        self.assertEqual(len(names), EXPECTED_THEMES)
        self.assertIn("executive", names)
        self.assertIn("luxury", names)

    def test_every_theme_renders_every_token(self) -> None:
        declared = json.loads(
            (word_design.engine_dir() / word_design.THEMES_NAME).read_text(encoding="utf-8")
        )["tokens"]
        for name in word_design.theme_names():
            with self.subTest(theme=name):
                css = word_design.theme_css(word_design.load_theme(name))
                for token in declared:
                    self.assertIn(f"--nv-{token}:", css)

    def test_theme_fonts_survive_the_trip_into_word(self) -> None:
        """A theme naming a font Office lacks would be silently substituted."""
        for name in word_design.theme_names():
            fonts = word_design.load_theme(name)["fonts"]
            for role, family in fonts.items():
                with self.subTest(theme=name, role=role):
                    self.assertIn(family.lower(), _fonts.OFFICE_FONTS)

    def test_unknown_theme_names_the_alternatives(self) -> None:
        with self.assertRaises(KeyError) as caught:
            word_design.load_theme("chartreuse")
        self.assertIn("executive", str(caught.exception))


class ApplyThemeTests(unittest.TestCase):
    DOCUMENT = (
        "<html><head>\n"
        '<style id="navin-theme" data-theme="executive">\n'
        "  :root { --nv-accent: #1F3A5F; }\n"
        "</style>\n"
        "<style>h1 { color: var(--nv-accent); }</style>\n"
        "</head><body><h1>Quarterly review</h1></body></html>"
    )

    def test_switching_theme_leaves_the_content_alone(self) -> None:
        switched = word_design.apply_theme(self.DOCUMENT, "luxury")
        self.assertEqual(word_design.current_theme(switched), "luxury")
        self.assertIn("<h1>Quarterly review</h1>", switched)
        self.assertIn("h1 { color: var(--nv-accent); }", switched)
        self.assertNotIn("#1F3A5F", switched)

    def test_applying_the_same_theme_twice_changes_nothing(self) -> None:
        once = word_design.apply_theme(self.DOCUMENT, "modern")
        self.assertEqual(once, word_design.apply_theme(once, "modern"))

    def test_an_unthemed_document_gets_the_block_before_its_css(self) -> None:
        plain = "<html><head><style>h1 { color: red; }</style></head><body></body></html>"
        themed = word_design.apply_theme(plain, "corporate")
        self.assertLess(themed.index("navin-theme"), themed.index("h1 { color: red; }"))

    def test_a_document_without_css_cannot_be_themed(self) -> None:
        with self.assertRaises(ValueError):
            word_design.apply_theme("<html><body>text</body></html>", "corporate")


class AuditTests(unittest.TestCase):
    def test_a_literal_colour_in_the_css_is_reported(self) -> None:
        document = (
            '<style id="navin-theme" data-theme="minimal">:root{--nv-accent:#111111;}</style>'
            "<style>h2 { color: #C0FFEE; }</style><body></body>"
        )
        report = word_design.audit(document)
        self.assertFalse(report["pass"])
        self.assertEqual(report["stray_colors"], ["#c0ffee"])

    def test_an_inline_colour_in_the_body_is_reported(self) -> None:
        document = (
            '<style id="navin-theme" data-theme="minimal">:root{--nv-accent:#111111;}</style>'
            '<style>h2 { color: var(--nv-accent); }</style>'
            '<body><td style="background:#C0FFEE"></td></body>'
        )
        self.assertEqual(word_design.audit(document)["stray_colors"], ["#c0ffee"])

    def test_black_and_white_are_structural_rather_than_palette(self) -> None:
        document = (
            '<style id="navin-theme" data-theme="minimal">:root{--nv-accent:#111111;}</style>'
            "<style>hr { color: #fff; background: #000000; }</style><body></body>"
        )
        self.assertTrue(word_design.audit(document)["pass"])

    def test_an_untokenized_document_fails_even_without_stray_colours(self) -> None:
        report = word_design.audit("<style>h1 { color: var(--nv-accent); }</style><body></body>")
        self.assertFalse(report["pass"])
        self.assertFalse(report["themed"])


class BundledTemplateTests(unittest.TestCase):
    """The 36 shipped templates have to hold the contract, not just the engine."""

    def test_the_library_is_still_all_there(self) -> None:
        self.assertGreaterEqual(len(TEMPLATE_FILES), 36)

    def test_every_template_is_tokenized_and_names_a_known_theme(self) -> None:
        known = set(word_design.theme_names())
        for path in TEMPLATE_FILES:
            with self.subTest(template=path.parent.name):
                report = word_design.audit(path.read_text(encoding="utf-8"))
                self.assertTrue(report["themed"])
                self.assertIn(report["theme"], known)
                self.assertEqual(report["stray_colors"], [])

    def test_every_template_survives_every_theme(self) -> None:
        for path in TEMPLATE_FILES:
            source = path.read_text(encoding="utf-8")
            for name in word_design.theme_names():
                with self.subTest(template=path.parent.name, theme=name):
                    switched = word_design.apply_theme(source, name)
                    self.assertTrue(word_design.audit(switched)["pass"])

    def test_templates_read_tokens_rather_than_repeating_them(self) -> None:
        """A template that never says var(--nv-...) is themed in name only."""
        for path in TEMPLATE_FILES:
            with self.subTest(template=path.parent.name):
                body_css = word_design._THEME_STYLE_RE.sub(
                    "", path.read_text(encoding="utf-8")
                )
                self.assertGreater(len(re.findall(r"var\(--nv-", body_css)), 8)

    def test_the_engine_stays_out_of_the_gallery(self) -> None:
        """list_templates_payload skips underscore folders; nothing may re-add it."""
        self.assertFalse((WORD_TEMPLATES / "_engine" / "metadata.json").exists())
        self.assertFalse(list((WORD_TEMPLATES / "_engine").glob("*.html")))


class ComponentLibraryTests(unittest.TestCase):
    def test_the_component_css_never_names_a_colour(self) -> None:
        css = (word_design.engine_dir() / "navin-word.css").read_text(encoding="utf-8")
        stray = {
            found.lower()
            for found in word_design._HEX_RE.findall(css)
            if found.lower() not in word_design._NEUTRAL_HEX
        }
        self.assertEqual(stray, set())


class SemanticRenderTests(unittest.TestCase):
    def test_a_report_keeps_none_of_the_lookbook(self) -> None:
        from navin.documents import word_render

        html = word_render.render_document(
            {
                "kind": "report",
                "theme": "luxury",
                "brand": "Navin",
                "kicker": "Executive report",
                "title": "Revue strategique T3 2026",
                "subtitle": "Traction, risques, decisions.",
                "meta": {
                    "prepared_by": "Direction",
                    "recipients": "Comite",
                    "date": "15 aout 2026",
                },
                "sections": [
                    {
                        "heading": "1. Synthese",
                        "body": "Le trimestre confirme la trajectoire.",
                        "kpis": [
                            {"label": "ARR", "value": "4,2 M€", "meta": "+18%"}
                        ],
                    },
                    {
                        "heading": "2. Priorites",
                        "items": [
                            {"label": "Industrialiser la plateforme"},
                            {"label": "Renforcer l export"},
                        ],
                    },
                ],
            }
        )
        self.assertIn("Revue strategique T3 2026", html)
        self.assertIn("4,2 M€", html)
        self.assertIn("data-theme=\"luxury\"", html)
        self.assertIn("data-filled=\"1\"", html)
        self.assertIn("data-doc-toc=\"2-3\"", html)
        self.assertIn("Sommaire", html)
        self.assertIn("1. Synthese", html)
        self.assertNotIn("Company Name", html)
        self.assertNotIn("Strategic review", html)
        self.assertNotIn("€4.2M", html)
        self.assertNotIn("image.png", html)
        qa = word_render.quality_check(html)
        self.assertTrue(qa["pass"], qa)

    def test_a_missing_image_leaves_no_frame(self) -> None:
        from navin.documents import word_render

        html = word_render.render_document(
            {
                "kind": "brief",
                "title": "Note interne",
                "sections": [{"heading": "Contexte", "body": "Rien a montrer."}],
            }
        )
        self.assertNotIn("<img", html)
        self.assertNotIn("image.png", html)

    def test_render_cli_writes_the_file(self) -> None:
        import tempfile

        from navin.documents import word_render

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "doc.json"
            out = Path(tmp) / "document.html"
            src.write_text(
                json.dumps(
                    {
                        "kind": "letter",
                        "theme": "corporate",
                        "title": "Objet: lancement",
                        "body": "Nous confirmons le demarrage lundi.",
                        "sender": "Navin",
                        "recipient": "Client",
                        "sign": "Aymen",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                word_design.main(
                    ["render", "--document", str(src), "-o", str(out)]
                ),
                0,
            )
            html = out.read_text(encoding="utf-8")
            self.assertIn("Objet: lancement", html)
            self.assertIn("Aymen", html)
            self.assertTrue(word_render.quality_check(html)["pass"])
            self.assertNotIn("data-doc-toc", html)

    def test_a_report_without_a_toc_fails_quality(self) -> None:
        from navin.documents import word_render

        html = (
            '<html data-kind="report"><body data-filled="1">'
            "<h1>Revue</h1><h2>Un</h2><h2>Deux</h2>"
            "</body></html>"
        )
        qa = word_render.quality_check(html)
        self.assertIn("missing-toc", qa["failures"])
        self.assertFalse(qa["pass"])


if __name__ == "__main__":
    unittest.main()
