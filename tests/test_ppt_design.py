from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.documents import ppt_design
from navin.utils.document_templates import list_templates_payload

ENGINE = Path(__file__).resolve().parents[1] / "templates" / "ppt" / "_engine"


class PptDesignSystemTest(unittest.TestCase):
    def test_engine_is_hidden_from_the_picker(self) -> None:
        payload = list_templates_payload()
        ppt = next(item for item in payload["categories"] if item["id"] == "ppt")
        names = {entry["name"] for entry in ppt["templates"]}
        self.assertNotIn("_engine", names)
        self.assertIn("aurora_glass", names)
        self.assertEqual(len(names), 14)
        self.assertNotIn("elevator_pitch", names)
        self.assertNotIn("minimalist", names)
        self.assertEqual(ppt_design.resolve_theme("elevator_pitch"), "startup")
        self.assertEqual(ppt_design.load_theme("green")["name"], "premium_green")
        from navin.utils.document_templates import template_entry

        aliased = template_entry("ppt", "elevator_pitch")
        self.assertIsNotNone(aliased)
        self.assertEqual(aliased["name"], "startup")
        ppt_root = ENGINE.parent
        on_disk = {
            folder.name
            for folder in ppt_root.iterdir()
            if folder.is_dir() and not folder.name.startswith(("_", "."))
        }
        self.assertEqual(names, on_disk)
        self.assertEqual(len(on_disk), 14)

    def test_catalog_has_thirty_five_layouts(self) -> None:
        catalog = ppt_design.load_catalog()
        ids = [item["id"] for item in catalog["layouts"]]
        self.assertEqual(len(ids), 35)
        self.assertEqual(len(set(ids)), 35)
        self.assertTrue((ENGINE / "layouts" / "cover.html").is_file())

    def test_every_theme_has_tokens(self) -> None:
        ppt = ENGINE.parent
        missing = []
        for folder in sorted(ppt.iterdir()):
            if not folder.is_dir() or folder.name.startswith(("_", ".")):
                continue
            if not (folder / "design-system.json").is_file():
                missing.append(folder.name)
                continue
            tokens = json.loads((folder / "design-system.json").read_text(encoding="utf-8"))
            for key in ("bg", "text", "accent"):
                self.assertIn(key, tokens["colors"], folder.name)
        self.assertEqual(missing, [])

    def test_visual_director_maps_content_kinds(self) -> None:
        self.assertEqual(ppt_design.choose_visual("chronology")[0], "timeline")
        self.assertEqual(ppt_design.choose_visual("steps")[0], "process")
        self.assertEqual(ppt_design.choose_visual("loop")[0], "cycle")
        self.assertEqual(ppt_design.choose_visual("unknown-kind")[0], "text")

    def test_image_position_picks_the_layout(self) -> None:
        self.assertEqual(
            ppt_design.choose_layout(visual={"type": "image", "position": "left"}),
            "image-text",
        )
        self.assertEqual(
            ppt_design.choose_layout(visual={"type": "image", "position": "right"}),
            "text-image",
        )
        self.assertEqual(ppt_design.choose_layout(kind="steps"), "process")

    def test_fill_replaces_sample_copy(self) -> None:
        html = ppt_design.materialize(
            "startup",
            "image-text",
            title="Navin on the left",
            body="The product screenshot leads.",
            image="product.png",
        )
        self.assertIn("data-layout=\"image-text\"", html)
        self.assertIn("data-variant=\"left\"", html)
        self.assertIn("Navin on the left", html)
        self.assertIn('src="product.png"', html)
        self.assertIn("--nv-accent: #0066FF", html)
        self.assertNotIn("Image leads, text follows", html)
        self.assertNotIn("Caption-length argument", html)
        self.assertNotIn('src="image.png"', html)
        self.assertNotIn("Named fact.", html)

    def test_materialize_inlines_theme_tokens(self) -> None:
        html = ppt_design.materialize("aurora_glass", "process")
        self.assertIn("data-layout=\"process\"", html)
        self.assertIn("--nv-accent: #7C3AED", html)
        self.assertIn("nv-process", html)
        self.assertNotRegex(html, r"\u2014|\u2013")
        qa = ppt_design.quality_check(html, ppt_design.load_theme("aurora_glass"))
        self.assertTrue(qa["pass"], qa)

    def test_materialize_copies_the_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            from navin.utils.document_templates import materialize_document_template

            dest = materialize_document_template(
                {"category": "ppt", "name": "aurora_glass"},
                workspace,
            )
            self.assertIsNotNone(dest)
            engine = dest.parent / "_engine" / "catalog.json"
            self.assertTrue(engine.is_file())
            self.assertTrue((dest / "design-system.json").is_file())

    def test_quality_flags_sample_copy(self) -> None:
        tokens = ppt_design.load_theme("textbook")
        html = "<body><p>Lorem ipsum</p></body>"
        qa = ppt_design.quality_check(html, tokens)
        self.assertIn("empty", qa["failures"])
        self.assertFalse(qa["pass"])

    def test_a_filled_slide_drops_every_lookbook_phrase(self) -> None:
        html = ppt_design.materialize(
            "startup",
            "text-image",
            slide={
                "layout": "text-image",
                "title": "Enterprise AI is accelerating",
                "body": "One measured result.",
                "items": [
                    {"label": "14 days", "detail": "First agent in production."},
                    {"label": "One team", "detail": "Not a nine month IT program."},
                ],
                "image": "product.png",
                "visual": {"type": "image", "position": "right", "fit": "contain"},
            },
        )
        self.assertIn("Enterprise AI is accelerating", html)
        self.assertIn("14 days", html)
        self.assertIn('src="product.png"', html)
        self.assertIn("data-variant=\"right\"", html)
        self.assertIn("nv-row", html)
        self.assertNotIn("Text holds the claim", html)
        self.assertNotIn("Named fact.", html)
        self.assertNotIn('src="image.png"', html)
        qa = ppt_design.quality_check(html, ppt_design.load_theme("startup"))
        self.assertTrue(qa["pass"], qa)

    def test_missing_image_uses_the_theme_still(self) -> None:
        html = ppt_design.materialize(
            "startup",
            "text-image",
            title="The claim stands without a photo",
            body="Proof in the copy.",
        )
        self.assertNotIn("image.png", html)
        self.assertIn("The claim stands without a photo", html)
        self.assertIn("<img", html)
        self.assertIn("right.jpg", html)

    def test_chevron_step_numbers_use_on_accent_ink(self) -> None:
        css = (ENGINE / "navin-ppt.css").read_text(encoding="utf-8")
        self.assertIn(
            '.nv-process[data-variant="chevron"] .nv-step strong',
            css,
        )
        block = css.split('.nv-process[data-variant="chevron"] .nv-step strong', 1)[1]
        self.assertIn("var(--nv-on-accent)", block.split("}", 1)[0])
        html = ppt_design.materialize(
            "aurora_glass",
            "process",
            slide={
                "layout": "process",
                "title": "How the work moves",
                "visual": {"variant": "chevron"},
                "items": [
                    {"label": "Brief", "detail": "Scope agreed."},
                    {"label": "Build", "detail": "A slice."},
                    {"label": "Prove", "detail": "A number."},
                ],
            },
        )
        self.assertIn('data-variant="chevron"', html)
        self.assertIn("--nv-on-accent: #FFFFFF", html)
        self.assertIn("<strong>03</strong>", html)

    def test_thin_text_becomes_a_side_panel(self) -> None:
        from navin.documents import ppt_render

        enriched = ppt_render.enrich_slide(
            {
                "layout": "text",
                "title": "Les equipes perdent 6 h par semaine",
                "body": "Un agent operationnel en 14 jours.",
            }
        )
        self.assertEqual(enriched["layout"], "text-image")
        html = ppt_design.materialize("startup", "text", slide=enriched)
        self.assertTrue("nv-panel" in html or "nv-img" in html)
        self.assertTrue("14" in html or "6 h" in html or "nv-img" in html)
        qa = ppt_design.quality_check(html, ppt_design.load_theme("startup"))
        self.assertNotIn("thin-slide", qa["failures"])

    def test_two_sentences_become_cards(self) -> None:
        from navin.documents import ppt_render

        enriched = ppt_render.enrich_slide(
            {
                "layout": "text",
                "title": "Two claims fill the page",
                "body": "First measured result. Second measured result.",
            }
        )
        self.assertEqual(enriched["layout"], "cards")
        self.assertGreaterEqual(len(enriched["items"]), 2)
        html = ppt_design.materialize("startup", "cards", slide=enriched)
        self.assertGreaterEqual(html.count('class="nv-item"'), 2)

    def test_quality_flags_a_thin_content_slide(self) -> None:
        html = (
            '<html data-layout="text"><body>'
            '<div class="nv-slide" data-filled="1">'
            '<h1 class="nv-title">A title</h1>'
            '<p class="nv-body">One lonely sentence.</p>'
            "</div></body></html>"
        )
        qa = ppt_design.quality_check(html, ppt_design.load_theme("startup"))
        self.assertIn("thin-slide", qa["failures"])
        self.assertFalse(qa["pass"])

    def test_process_items_are_dynamic(self) -> None:
        html = ppt_design.materialize(
            "aurora_glass",
            "process",
            slide={
                "layout": "process",
                "title": "How the work moves",
                "items": [
                    {"label": "Brief", "detail": "Scope agreed in writing."},
                    {"label": "Build", "detail": "A slice the team can run."},
                    {"label": "Prove", "detail": "A number the room can check."},
                ],
            },
        )
        self.assertEqual(html.count('class="nv-step"'), 3)
        self.assertIn("Brief", html)
        self.assertNotIn("Owned by the team.", html)

    def test_chart_requires_an_insight(self) -> None:
        html = ppt_design.materialize(
            "startup",
            "chart",
            slide={
                "layout": "chart",
                "title": "Revenue has tripled since 2024",
                "insight": "+43% year on year",
                "series": [
                    {"label": "2024", "value": "40"},
                    {"label": "2025", "value": "80"},
                    {"label": "2026", "value": "120"},
                ],
            },
        )
        self.assertIn("+43% year on year", html)
        self.assertIn("nv-bar", html)
        self.assertNotIn("Enterprise revenue has tripled since 2024", html)

    def test_deck_writes_one_file_per_slide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deck = Path(tmp) / "deck.json"
            out = Path(tmp) / "slides"
            deck.write_text(
                json.dumps(
                    {
                        "theme": "startup",
                        "slides": [
                            {
                                "layout": "cover",
                                "kicker": "Pitch",
                                "title": "Stop losing six hours a week",
                                "body": "An agent in fourteen days.",
                            },
                            {
                                "layout": "kpi",
                                "title": "The number the room must remember",
                                "items": [
                                    {
                                        "label": "Hours saved each week",
                                        "value": "6 h",
                                        "meta": "Per team, measured.",
                                    }
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                ppt_design.main(["deck", "--slides", str(deck), "-o", str(out)]),
                0,
            )
            first = (out / "slide_01.html").read_text(encoding="utf-8")
            second = (out / "slide_02.html").read_text(encoding="utf-8")
            self.assertIn("Stop losing six hours a week", first)
            self.assertNotIn("The title is the message", first)
            self.assertIn("6 h", second)
            self.assertNotIn("$12.4M", second)
            self.assertEqual(len(list(out.glob("slide_*.html"))), 2)

    def test_a_deck_gains_a_sommaire_after_the_cover(self) -> None:
        from navin.documents import ppt_render

        slides = ppt_render.ensure_outline(
            [
                {"layout": "cover", "title": "Les equipes perdent 6 h", "kicker": "Pitch"},
                {"layout": "kpi", "title": "Six heures par semaine", "items": [{"value": "6 h"}]},
                {"layout": "process", "title": "Un agent en 14 jours", "items": [{"label": "A"}]},
                {"layout": "cards", "title": "Ce que vous signez", "items": [{"label": "B"}]},
            ],
            language="fr",
        )
        self.assertEqual(slides[0]["layout"], "cover")
        self.assertEqual(slides[1]["layout"], "agenda")
        self.assertEqual(slides[1]["kicker"], "Sommaire")
        labels = [item["label"] for item in slides[1]["items"]]
        self.assertEqual(
            labels,
            ["Six heures par semaine", "Un agent en 14 jours", "Ce que vous signez"],
        )
        html = ppt_design.materialize("startup", "agenda", slide=slides[1])
        self.assertIn("Sommaire", html)
        self.assertIn("Six heures par semaine", html)
        self.assertGreaterEqual(html.count('class="nv-item"'), 3)

    def test_an_existing_agenda_is_not_duplicated(self) -> None:
        from navin.documents import ppt_render

        slides = ppt_render.ensure_outline(
            [
                {"layout": "cover", "title": "Pitch"},
                {
                    "layout": "agenda",
                    "title": "Sommaire",
                    "items": [{"label": "Deja la"}],
                },
                {"layout": "kpi", "title": "Un"},
                {"layout": "kpi", "title": "Deux"},
                {"layout": "kpi", "title": "Trois"},
            ]
        )
        agendas = [slide for slide in slides if slide.get("layout") == "agenda"]
        self.assertEqual(len(agendas), 1)
        self.assertEqual(agendas[0]["items"][0]["label"], "Deja la")

    def test_rhythm_flips_after_two_same_family_pages(self) -> None:
        from navin.documents import ppt_render

        slides = ppt_render.ensure_rhythm(
            [
                {"layout": "cover", "title": "Hook"},
                {"layout": "cards", "title": "One"},
                {"layout": "cards", "title": "Two"},
                {"layout": "cards", "title": "Three"},
            ]
        )
        self.assertEqual(slides[0]["tone"], "hero-dark")
        families = [
            "dark" if "dark" in slide["tone"] else "light" for slide in slides
        ]
        self.assertFalse(
            families[1] == families[2] == families[3],
            families,
        )

    def test_stagecraft_adds_curtain_and_data_hero(self) -> None:
        from navin.documents import ppt_render

        raw = [{"layout": "cover", "title": "Hook"}]
        for index in range(8):
            raw.append(
                {
                    "layout": "cards",
                    "title": f"Claim {index}",
                    "items": [
                        {"label": "A", "detail": "One."},
                        {"label": "B", "value": "12", "detail": "Measured."},
                    ],
                }
            )
        slides = ppt_render.ensure_stagecraft(raw)
        layouts = [slide["layout"] for slide in slides]
        self.assertIn("section-break", layouts)
        self.assertTrue(set(layouts) & {"big-numbers", "kpi", "dashboard", "data-story"})
        self.assertEqual(ppt_render.stagecraft_gaps(slides), [])

    def test_gallery_is_added_when_photos_exist(self) -> None:
        from navin.documents import ppt_render

        raw = [
            {"layout": "cover", "title": "Hook"},
            {
                "layout": "text-image",
                "title": "Photo one",
                "image": "one.jpg",
                "items": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
            },
        ]
        for index in range(7):
            raw.append({"layout": "cards", "title": f"More {index}", "items": [{"label": "X"}]})
        raw[3]["image"] = "two.jpg"
        slides = ppt_render.ensure_stagecraft(raw)
        self.assertIn("gallery", [slide["layout"] for slide in slides])
        french = ppt_render.ensure_stagecraft(raw, language="fr")
        gallery = next(slide for slide in french if slide["layout"] == "gallery")
        self.assertEqual(gallery["title"], "Le travail, pas un collage")
        self.assertEqual(gallery["kicker"], "Preuve")
        html = ppt_design.materialize(
            "startup",
            "gallery",
            slide={
                "layout": "gallery",
                "title": "Le travail, pas un collage",
                "images": ["photos/background.jpg", "photos/left.jpg", "photos/right.jpg"],
            },
        )
        self.assertIn('class="nv-gallery" data-n="3"', html)

    def test_deck_writes_preview_and_stamps_notes(self) -> None:
        html = ppt_design.materialize(
            "startup",
            "statement",
            slide={
                "layout": "statement",
                "title": "Stop losing six hours a week",
                "tone": "hero-dark",
                "notes": "Open on the wasted hours.",
            },
        )
        self.assertIn("nv-tone-hero-dark", html)
        self.assertIn('data-notes="Open on the wasted hours."', html)
        self.assertIn("clamp(", (ENGINE / "navin-ppt.css").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            deck = Path(tmp) / "deck.json"
            out = Path(tmp) / "slides"
            deck.write_text(
                json.dumps(
                    {
                        "theme": "startup",
                        "slides": [
                            {
                                "layout": "cover",
                                "title": "Stop losing six hours a week",
                                "notes": "Open on the wasted hours.",
                            },
                            {
                                "layout": "kpi",
                                "title": "The number",
                                "items": [{"label": "Hours", "value": "6 h"}],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                ppt_design.main(["deck", "--slides", str(deck), "-o", str(out)]),
                0,
            )
            preview = (out / "index.html").read_text(encoding="utf-8")
            self.assertIn("slide_01.html", preview)
            self.assertIn("ArrowRight", preview)
            self.assertIn('event.key === "s"', preview)
            self.assertIn("Presenter (S)", preview)
            notes = json.loads((out / "notes.json").read_text(encoding="utf-8"))
            self.assertEqual(notes[0], "Open on the wasted hours.")
            critique = json.loads((out / "critique.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(critique["score"], 85)
            self.assertTrue(critique["pass"])

    def test_speaker_notes_reach_the_pptx(self) -> None:
        from pptx import Presentation

        from navin.documents import ppt_render

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deck.pptx"
            prs = Presentation()
            prs.slides.add_slide(prs.slide_layouts[0])
            prs.save(str(path))
            written = ppt_render.apply_speaker_notes(path, ["Open on the wasted hours."])
            self.assertEqual(written, 1)
            again = Presentation(str(path))
            self.assertIn(
                "wasted hours",
                again.slides[0].notes_slide.notes_text_frame.text,
            )

    def test_product_screenshot_gets_a_browser_frame(self) -> None:
        html = ppt_design.materialize(
            "startup",
            "product-hero",
            slide={
                "layout": "product-hero",
                "title": "The product holds the promise",
                "image": "app.png",
                "items": [
                    {"label": "14 days", "detail": "First agent live."},
                    {"label": "1 team", "detail": "Not a nine month IT project."},
                ],
            },
        )
        self.assertIn('class="nv-shot grow"', html)
        self.assertIn("nv-shot-bar", html)
        self.assertIn('data-kind="browser"', html)
        photo = ppt_design.materialize(
            "startup",
            "text-image",
            slide={
                "layout": "text-image",
                "title": "A real scene, not a capture",
                "image": "street.jpg",
                "items": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
            },
        )
        self.assertNotIn('class="nv-shot', photo)

    def test_product_theme_photo_has_no_browser_frame(self) -> None:
        html = ppt_design.materialize(
            "startup",
            "product-hero",
            slide={
                "layout": "product-hero",
                "title": "Navin execute le travail, pas un resume",
                "description": "Fichiers, terminal, navigateur, tests.",
                "features": [
                    {"title": "Code", "description": "Un depot, des tests."},
                    {"title": "Docs", "description": "PPTX et Word."},
                    {"title": "Controle", "description": "QA avant done."},
                ],
            },
        )
        body = html.split("<body", 1)[-1]
        self.assertIn("photos/right.jpg", body)
        self.assertNotIn("nv-shot-bar", body)
        self.assertNotIn('data-kind="browser"', body)

    def test_kpi_tower_uses_bar_heights(self) -> None:
        html = ppt_design.materialize(
            "black_and_white_clean",
            "kpi",
            slide={
                "layout": "kpi",
                "title": "Hours leave the week",
                "visual": {"variant": "tower"},
                "items": [
                    {"label": "Now", "value": "6 h"},
                    {"label": "Target", "value": "1 h"},
                    {"label": "Saved", "value": "5 h"},
                ],
            },
        )
        self.assertIn('data-variant="tower"', html)
        self.assertIn("height:100%", html)
        self.assertIn("height:22%", html)

    def test_critique_fails_a_topic_list(self) -> None:
        from navin.documents import ppt_render

        thin = [{"layout": "cards", "title": "Overview"} for _ in range(8)]
        report = ppt_render.critique_deck(thin)
        self.assertLess(report["score"], 85)
        self.assertFalse(report["pass"])
        rich = ppt_render.ensure_rhythm(
            ppt_render.ensure_stagecraft(
                [
                    {
                        "layout": "cover",
                        "title": "Teams lose six hours a week",
                        "notes": "Open on the wasted hours.",
                    },
                    {
                        "layout": "kpi",
                        "title": "Six hours leave every week",
                        "items": [{"label": "Hours", "value": "6 h"}],
                        "notes": "Hold the number.",
                    },
                    {
                        "layout": "text-image",
                        "title": "The product holds the promise",
                        "image": "app.png",
                        "visual": {"kind": "screenshot"},
                        "items": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
                        "notes": "Show the screen.",
                    },
                    {
                        "layout": "process",
                        "title": "An agent ships in 14 days",
                        "items": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
                        "notes": "Walk the steps.",
                    },
                    {
                        "layout": "cards",
                        "title": "What you sign this quarter",
                        "items": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
                        "notes": "Name the offer.",
                    },
                    {
                        "layout": "gallery",
                        "title": "The work, not a collage",
                        "images": ["one.jpg", "two.jpg"],
                        "notes": "Let the photos talk.",
                    },
                    {
                        "layout": "section-break",
                        "title": "What changes on Monday",
                        "notes": "Pause.",
                    },
                    {
                        "layout": "statement",
                        "title": "Start with one team this month",
                        "notes": "Ask.",
                    },
                ]
            )
        )
        report = ppt_render.critique_deck(rich)
        self.assertGreaterEqual(report["score"], 85)
        self.assertTrue(report["pass"])

    def test_agent_aliases_fill_the_theme(self) -> None:

        hero = ppt_design.materialize(
            "competitor_analysis_blue",
            "product-hero",
            slide={
                "layout": "product-hero",
                "title": "Qu'est-ce que Navin AI ?",
                "description": "Un agent qui execute le travail, pas un chat.",
                "features": [
                    {"title": "Code", "description": "Un depot, des tests, un preview."},
                    {"title": "Docs", "description": "PPTX et Word sur le theme choisi."},
                    {"title": "Audit", "description": "Secrets et dependances."},
                ],
            },
        )
        self.assertIn("Un agent qui execute le travail", hero)
        self.assertIn("Un depot, des tests, un preview.", hero)
        self.assertNotIn("Qu&#x27;est-ce</div>", hero)
        self.assertNotIn(">Qu'est-ce</div>", hero)

        compare = ppt_design.materialize(
            "competitor_analysis_blue",
            "comparison",
            slide={
                "layout": "comparison",
                "title": "Navin AI vs Assistants Classiques",
                "columns": [
                    {
                        "title": "Assistants classiques",
                        "items": ["Texte seul", "Pas de terminal"],
                    },
                    {
                        "title": "Navin AI Agent",
                        "items": ["Fichiers reels", "Tests avant livraison"],
                    },
                ],
            },
        )
        self.assertIn("Assistants classiques", compare)
        self.assertIn("Fichiers reels", compare)
        self.assertIn("nv-compare", compare)

        process = ppt_design.materialize(
            "startup",
            "process",
            slide={
                "layout": "process",
                "title": "An agent ships in 14 days",
                "steps": [
                    {"step": "01", "title": "Brief", "description": "Scope locked."},
                    {"step": "02", "title": "Build", "description": "A working slice."},
                    {"step": "03", "title": "Prove", "description": "A measured result."},
                ],
            },
        )
        self.assertIn("Scope locked.", process)
        self.assertIn("<strong>01</strong>", process)

    def test_critique_fails_a_hollow_content_slide(self) -> None:
        from navin.documents import ppt_render

        report = ppt_render.critique_deck(
            [
                {"layout": "cover", "title": "Teams lose six hours a week"},
                {"layout": "comparison", "title": "Navin AI vs Assistants Classiques"},
            ]
        )
        self.assertFalse(report["pass"])
        self.assertTrue(any("Hollow" in note for note in report["notes"]))

    def test_deck_rejects_a_title_only_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deck = Path(tmp) / "deck.json"
            out = Path(tmp) / "slides"
            deck.write_text(
                json.dumps(
                    {
                        "theme": "startup",
                        "slides": [
                            {
                                "layout": "cover",
                                "title": "Teams lose six hours a week",
                                "notes": "Open on the wasted hours.",
                            },
                            {
                                "layout": "comparison",
                                "title": "Navin AI vs Assistants Classiques",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                ppt_design.main(["deck", "--slides", str(deck), "-o", str(out)]),
                1,
            )
            critique = json.loads((out / "critique.json").read_text(encoding="utf-8"))
            self.assertFalse(critique["pass"])


if __name__ == "__main__":
    unittest.main()
