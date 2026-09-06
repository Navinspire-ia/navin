"""Deck QA: the score the engine has always declared, and what a browser sees."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from navin.documents import ppt_design, ppt_qa

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "templates" / "ppt" / "_engine"


def _chromium_available() -> bool:
    try:
        from navin.documents._chromium import find_chromium

        find_chromium(None)
    except Exception:  # pragma: no cover - depends on the machine
        return False
    return True


class DeclaredContractTests(unittest.TestCase):
    """quality.json is the contract; the module must not quietly disagree."""

    def setUp(self) -> None:
        self.declared = json.loads((ENGINE / "quality.json").read_text(encoding="utf-8"))

    def test_the_weights_come_from_the_engine(self) -> None:
        self.assertEqual(ppt_qa.weights(), self.declared["weights"])

    def test_the_threshold_comes_from_the_engine(self) -> None:
        self.assertEqual(ppt_qa.threshold(), self.declared["threshold"])

    def test_the_weights_add_up_to_a_hundred(self) -> None:
        self.assertEqual(sum(ppt_qa.weights().values()), 100)

    def test_every_declared_rule_is_actually_implemented(self) -> None:
        """The nine rules were declared long before anything computed them."""
        for rule in self.declared["rules"]:
            with self.subTest(rule=rule["id"]):
                self.assertIn(rule["id"], ppt_qa.RULES)

    def test_every_weighted_category_can_lose_points(self) -> None:
        """A category no rule touches is a weight that means nothing."""
        covered = {category for category, _, _ in ppt_qa.RULES.values()}
        for category in ppt_qa.weights():
            with self.subTest(category=category):
                self.assertIn(category, covered)

    def test_each_rule_names_a_weighted_category_and_a_fix(self) -> None:
        for code, (category, penalty, fix) in ppt_qa.RULES.items():
            with self.subTest(rule=code):
                self.assertIn(category, ppt_qa.weights())
                self.assertGreater(penalty, 0)
                self.assertTrue(fix.endswith("."), "a fix should read as an instruction")

    def test_no_single_fault_can_empty_a_category_on_its_own(self) -> None:
        points = ppt_qa.weights()
        for code, (category, penalty, _) in ppt_qa.RULES.items():
            if category == "theme":
                continue  # a deck that cannot be re-themed fails that category outright
            with self.subTest(rule=code):
                self.assertLess(penalty, points[category])


class ScoringTests(unittest.TestCase):
    def test_a_clean_slide_scores_a_hundred(self) -> None:
        self.assertEqual(ppt_qa.score_slide([])["score"], 100)

    def test_a_fault_costs_its_category_and_nothing_else(self) -> None:
        scored = ppt_qa.score_slide([{"code": "tiny-text", "count": 1, "examples": []}])
        self.assertEqual(scored["categories"]["readability"], 15)
        self.assertEqual(scored["categories"]["layout"], 20)
        self.assertEqual(scored["score"], 95)

    def test_repeating_a_fault_costs_more_but_not_endlessly(self) -> None:
        once = ppt_qa.score_slide([{"code": "contrast", "count": 1, "examples": []}])["score"]
        thrice = ppt_qa.score_slide([{"code": "contrast", "count": 3, "examples": []}])["score"]
        many = ppt_qa.score_slide([{"code": "contrast", "count": 40, "examples": []}])["score"]
        self.assertLess(thrice, once)
        self.assertLess(many, thrice)
        self.assertEqual(many, ppt_qa.score_slide([{"code": "contrast", "count": 4, "examples": []}])["score"])

    def test_a_category_never_goes_negative(self) -> None:
        scored = ppt_qa.score_slide(
            [
                {"code": "overflow", "count": 20, "examples": []},
                {"code": "overlaps", "count": 20, "examples": []},
                {"code": "margins", "count": 20, "examples": []},
            ]
        )
        self.assertEqual(scored["categories"]["layout"], 0)
        self.assertGreaterEqual(scored["score"], 0)


class DedupeTests(unittest.TestCase):
    def test_identical_findings_become_one_entry_with_a_count(self) -> None:
        grouped = ppt_qa._dedupe([{"code": "overflow", "detail": f"box {n}"} for n in range(5)])
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["count"], 5)
        self.assertEqual(len(grouped[0]["examples"]), 3)

    def test_a_code_the_rules_do_not_know_is_dropped(self) -> None:
        self.assertEqual(ppt_qa._dedupe([{"code": "invented", "detail": "x"}]), [])


class RepeatedLayoutTests(unittest.TestCase):
    def test_three_slides_running_the_same_composition_are_flagged(self) -> None:
        measured = [{"signature": "cards", "findings": []} for _ in range(3)]
        ppt_qa._flag_repeated_layouts(measured)
        codes = [f["code"] for slide in measured for f in slide["findings"]]
        self.assertEqual(codes, ["repeat-layout"])

    def test_two_in_a_row_is_a_rhythm_rather_than_a_rut(self) -> None:
        measured = [{"signature": "cards", "findings": []} for _ in range(2)]
        ppt_qa._flag_repeated_layouts(measured)
        self.assertEqual([f for slide in measured for f in slide["findings"]], [])

    def test_alternating_layouts_are_left_alone(self) -> None:
        measured = [{"signature": s, "findings": []} for s in ("a", "b", "a", "b", "a")]
        ppt_qa._flag_repeated_layouts(measured)
        self.assertEqual([f for slide in measured for f in slide["findings"]], [])

    def test_slides_with_no_signature_are_not_assumed_identical(self) -> None:
        measured = [{"signature": "", "findings": []} for _ in range(4)]
        ppt_qa._flag_repeated_layouts(measured)
        self.assertEqual([f for slide in measured for f in slide["findings"]], [])


class ThemeFindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="navin-ppt-qa-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _write(self, html: str) -> Path:
        path = self.dir / "slide_01.html"
        path.write_text(html, encoding="utf-8")
        return path

    def test_a_slide_carrying_no_theme_is_flagged_once(self) -> None:
        found = ppt_qa._theme_findings(self._write("<style>h1{color:#C0FFEE}</style><body></body>"))
        self.assertEqual([entry["code"] for entry in found], ["theme-unlinked"])

    def test_a_colour_outside_the_token_block_is_flagged(self) -> None:
        found = ppt_qa._theme_findings(
            self._write(
                "<style>:root{--nv-accent:#0066FF;}</style>"
                "<style>.nv-title{color:#C0FFEE}</style><body></body>"
            )
        )
        self.assertEqual(found[0]["code"], "theme-colors")
        self.assertEqual(found[0]["examples"], ["#c0ffee"])

    def test_a_slide_reusing_its_tokens_raises_nothing(self) -> None:
        self.assertEqual(
            ppt_qa._theme_findings(
                self._write(
                    "<style>:root{--nv-accent:#0066FF;}</style>"
                    "<style>.nv-title{color:var(--nv-accent)}</style><body></body>"
                )
            ),
            [],
        )

    def test_a_linked_theme_counts_as_themed(self) -> None:
        found = ppt_qa._theme_findings(
            self._write('<link rel="stylesheet" href="theme.css"><body></body>')
        )
        self.assertEqual(found, [])


class TemplateCopyTests(unittest.TestCase):
    """Every template declares replace_all_visible_text; this is what checks it.

    A deck was shipped with two slides written for its subject and thirteen
    still describing the template's imaginary company, and QA passed it.
    """

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="navin-ppt-qa-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.deck = self.dir / "deck"
        shutil.copytree(ROOT / "templates" / "ppt" / "startup", self.deck)
        self.shipped = ppt_qa._template_phrases(self.deck)

    def test_the_template_a_deck_was_copied_from_is_found_again(self) -> None:
        self.assertEqual(
            ppt_qa._bundled_template(self.deck),
            ROOT / "templates" / "ppt" / "startup",
        )
        self.assertTrue(self.shipped)

    def test_every_slide_left_untouched_is_flagged(self) -> None:
        for slide in sorted(self.deck.glob("slide_*.html")):
            with self.subTest(slide=slide.name):
                found = ppt_qa._template_findings(slide, self.shipped)
                self.assertEqual([entry["code"] for entry in found], ["template-copy"])

    def test_a_slide_rewritten_for_its_own_subject_is_clean(self) -> None:
        slide = self.deck / "slide_03.html"
        html = slide.read_text(encoding="utf-8")
        for shipped in re.findall(r">([^<>]{25,})<", html):
            html = html.replace(shipped, "Navin inspecte le depot, ecrit le code et verifie le rendu.")
        slide.write_text(html, encoding="utf-8")
        self.assertEqual(ppt_qa._template_findings(slide, self.shipped), [])

    def test_qa_on_the_bundled_template_itself_does_not_match_itself(self) -> None:
        self.assertIsNone(ppt_qa._bundled_template(ROOT / "templates" / "ppt" / "startup"))

    def test_a_deck_that_names_no_template_is_left_alone(self) -> None:
        (self.deck / "metadata.json").unlink()
        self.assertEqual(ppt_qa._template_phrases(self.deck), set())

    def test_another_template_is_not_mistaken_for_this_one(self) -> None:
        other = sorted((ROOT / "templates" / "ppt" / "portfolio").glob("slide_*.html"))[0]
        self.assertEqual(ppt_qa._template_findings(other, self.shipped), [])


class BlockingTests(unittest.TestCase):
    """Filler is not a bad score, it is a deck nobody wrote."""

    def test_every_blocking_code_is_a_declared_rule(self) -> None:
        for code in ppt_qa.BLOCKING:
            with self.subTest(rule=code):
                self.assertIn(code, ppt_qa.RULES)

    def test_a_slide_still_carrying_filler_blocks_the_deck(self) -> None:
        for code in ppt_qa.BLOCKING:
            with self.subTest(rule=code):
                scored = ppt_qa.score_slide([{"code": code, "count": 1, "examples": []}])
                scored["slide"] = 1
                # It comfortably clears the threshold, and must still not ship.
                self.assertGreater(scored["score"], ppt_qa.threshold())
                self.assertEqual(ppt_qa._blocked([scored]), [1])

    def test_a_clean_deck_blocks_nothing(self) -> None:
        scored = ppt_qa.score_slide([{"code": "tiny-text", "count": 1, "examples": []}])
        scored["slide"] = 1
        self.assertEqual(ppt_qa._blocked([scored]), [])


class CollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="navin-ppt-qa-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_slides_come_back_in_the_order_they_are_shown(self) -> None:
        for name in ("slide_10.html", "slide_02.html", "slide_01.html"):
            (self.dir / name).write_text("<body></body>", encoding="utf-8")
        self.assertEqual(
            [path.name for path in ppt_qa.collect_slides(self.dir)],
            ["slide_01.html", "slide_02.html", "slide_10.html"],
        )

    def test_a_folder_with_no_slides_says_so(self) -> None:
        from navin.documents._chromium import ConversionError

        with self.assertRaises(ConversionError):
            ppt_qa.collect_slides(self.dir)


class ReadableInkTests(unittest.TestCase):
    """The accent is chosen to be seen; the ink variant has to be readable."""

    def test_a_vivid_accent_on_black_is_moved_until_it_can_be_read(self) -> None:
        ink = ppt_design.readable_ink("#0066FF", "#000000", "#0A0A12")
        self.assertNotEqual(ink.lower(), "#0066ff")
        rgb = ppt_design._rgb(ink)
        self.assertGreaterEqual(ppt_design._contrast(rgb, ppt_design._rgb("#000000")), 4.5)

    def test_an_accent_that_already_reads_is_left_alone(self) -> None:
        self.assertEqual(ppt_design.readable_ink("#000000", "#FFFFFF"), "#000000")

    def test_the_result_is_never_worse_than_what_it_replaces(self) -> None:
        for accent, bg, surface in (
            ("#D0FFCD", "#9B9886", "#8A8776"),
            ("#FFFFFF", "#FFFFFF", "#111111"),
            ("#B5FF81", "#F5F1E8", "#FFFFFF"),
        ):
            with self.subTest(accent=accent):
                surfaces = [ppt_design._rgb(bg), ppt_design._rgb(surface)]
                before = min(ppt_design._contrast(ppt_design._rgb(accent), s) for s in surfaces)
                after = min(
                    ppt_design._contrast(ppt_design._rgb(ppt_design.readable_ink(accent, bg, surface)), s)
                    for s in surfaces
                )
                self.assertGreaterEqual(after, before)

    def test_a_theme_may_name_its_own_ink(self) -> None:
        css = ppt_design.theme_css({"colors": {"accent": "#0066FF", "accentInk": "#123456"}})
        self.assertIn("--nv-accent-ink: #123456;", css)

    def test_every_theme_renders_the_ink_token(self) -> None:
        for path in sorted((ENGINE.parent).glob("*/design-system.json")):
            with self.subTest(theme=path.parent.name):
                css = ppt_design.theme_css(ppt_design.load_theme(path.parent.name))
                self.assertIn("--nv-accent-ink:", css)


@unittest.skipUnless(_chromium_available(), "chromium is not installed")
class SlideInspectionTests(unittest.TestCase):
    """The half only a browser can answer."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="navin-ppt-slide-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    BODY = "<p class='nv-body'>Revenue grew across every region we operate in.</p>"

    def _review(self, body: str, *, style: str = "") -> dict:
        html = (
            "<html><head><style>:root{--nv-accent:#0066FF;--nv-margin:80px;}"
            "*{margin:0;padding:0;box-sizing:border-box;}"
            "html,body{width:1920px;height:1080px;background:#FFFFFF;color:#111111;}"
            ".nv-slide{position:relative;width:1920px;height:1080px;padding:80px;"
            "display:flex;flex-direction:column;gap:24px;font-size:24px;}"
            ".nv-title{font-size:64px;font-weight:700;}" + style + "</style></head>"
            f"<body><div class='nv-slide'>{body}</div></body></html>"
        )
        path = self.dir / "slide_01.html"
        path.write_text(html, encoding="utf-8")
        return ppt_qa.review(path)

    def _codes(self, report: dict) -> set[str]:
        return {f["code"] for slide in report["slides"] for f in slide["findings"]}

    def test_a_well_built_slide_comes_back_clean(self) -> None:
        # Enough copy to fill the slide: four lines on a 1080px canvas are
        # correctly read as a mostly empty slide.
        report = self._review(f"<h1 class='nv-title'>Revenue tripled</h1>{self.BODY * 8}")
        self.assertEqual(self._codes(report), set())
        self.assertEqual(report["score"], 100)
        self.assertTrue(report["pass"])

    def test_a_box_hanging_off_the_slide_is_caught(self) -> None:
        body = f"<h1 class='nv-title'>Wide</h1>{self.BODY}<p class='off'>Off the edge</p>"
        report = self._review(body, style=".off{position:absolute;left:1900px;top:400px;}")
        self.assertIn("overflow", self._codes(report))

    def test_two_texts_printing_over_each_other_are_caught(self) -> None:
        body = (
            "<h1 class='nv-title'>Stacked</h1>"
            "<p class='a'>First sentence sitting here.</p>"
            "<p class='b'>Second sentence on top of it.</p>"
        )
        report = self._review(
            body,
            style=".a,.b{position:absolute;left:200px;top:500px;width:600px;}",
        )
        self.assertIn("overlaps", self._codes(report))

    def test_grey_on_white_below_the_readable_ratio_is_caught(self) -> None:
        body = f"<h1 class='nv-title'>Notes</h1><p class='faint'>Small print.</p>{self.BODY}"
        report = self._review(body, style=".faint{color:#C8CDD6;font-size:22px;}")
        self.assertIn("contrast", self._codes(report))

    def test_a_label_sitting_outside_its_coloured_parent_is_judged_on_the_slide(self) -> None:
        """A chart label above its bar is on the slide, not on the bar."""
        body = (
            f"<h1 class='nv-title'>Chart</h1>{self.BODY}"
            "<div class='bar'><span>2026</span></div>"
        )
        report = self._review(
            body,
            style=(
                ".bar{position:relative;width:200px;height:300px;background:#0066FF;}"
                ".bar span{position:absolute;top:-40px;left:0;font-size:22px;color:#111111;}"
            ),
        )
        self.assertNotIn("contrast", self._codes(report))

    def test_text_too_small_to_read_from_the_room_is_caught(self) -> None:
        body = f"<h1 class='nv-title'>Small</h1>{self.BODY}<p class='mini'>Footnote copy.</p>"
        report = self._review(body, style=".mini{font-size:14px;}")
        self.assertIn("tiny-text", self._codes(report))

    def test_slide_furniture_is_allowed_to_be_discreet(self) -> None:
        body = (
            f"<h1 class='nv-title'>Report</h1>{self.BODY}"
            "<div class='nv-footer'><span>Confidential</span></div>"
        )
        report = self._review(body, style=".nv-footer span{font-size:14px;color:#9AA0A6;}")
        codes = self._codes(report)
        self.assertNotIn("tiny-text", codes)
        self.assertNotIn("contrast", codes)

    def test_a_slide_of_content_with_no_title_is_caught(self) -> None:
        report = self._review(self.BODY * 4)
        self.assertIn("no-title", self._codes(report))

    def test_a_quote_slide_is_its_own_title(self) -> None:
        report = self._review(
            "<blockquote class='q'>The rollout paid for itself in four months.</blockquote>"
            "<cite>Head of Operations</cite>",
            style=".q{font-size:48px;}",
        )
        self.assertNotIn("no-title", self._codes(report))

    def test_a_title_that_is_not_a_heading_is_named(self) -> None:
        report = self._review(f"<div class='nv-title'>Revenue tripled</div>{self.BODY * 3}")
        self.assertIn("title-not-heading", self._codes(report))

    def test_too_much_copy_for_one_slide_is_caught(self) -> None:
        report = self._review(f"<h1 class='nv-title'>Dense</h1>{self.BODY * 12}")
        self.assertIn("density-overflow", self._codes(report))

    def test_a_chart_without_an_insight_line_is_caught(self) -> None:
        body = "<h1 class='nv-title'>Growth</h1><div class='nv-chart'><div>bar</div></div>"
        self.assertIn("chart-insight", self._codes(self._review(body)))

    def test_a_chart_with_its_insight_passes(self) -> None:
        body = (
            "<h1 class='nv-title'>Growth</h1>"
            "<p class='nv-insight'>Up 43% year on year</p>"
            "<div class='nv-chart'><div>bar</div></div>"
        )
        self.assertNotIn("chart-insight", self._codes(self._review(body)))

    def test_sample_copy_left_in_place_is_caught(self) -> None:
        body = f"<h1 class='nv-title'>Plan</h1><p>Lorem ipsum dolor sit amet.</p>{self.BODY}"
        self.assertIn("empty", self._codes(self._review(body)))


@unittest.skipUnless(_chromium_available(), "chromium is not installed")
class MaterializedMasterTests(unittest.TestCase):
    """A master that fails its own QA makes the threshold meaningless."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="navin-ppt-master-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_the_reference_layouts_pass_on_a_reference_theme(self) -> None:
        for index, layout in enumerate(("cover", "agenda", "chart", "kpi", "quote"), 1):
            html = ppt_design.materialize("startup", layout)
            (self.dir / f"slide_{index:02d}.html").write_text(html, encoding="utf-8")
        report = ppt_qa.review(self.dir)
        self.assertTrue(report["pass"], ppt_qa.format_report(report))

    def test_a_materialized_slide_carries_its_theme(self) -> None:
        path = self.dir / "slide_01.html"
        path.write_text(ppt_design.materialize("textbook", "text"), encoding="utf-8")
        self.assertEqual(ppt_qa._theme_findings(path), [])


if __name__ == "__main__":
    unittest.main()
