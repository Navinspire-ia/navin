# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Visual QA: the scoring arithmetic, and what Chromium actually sees on a page."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from navin.documents import word_qa

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "word"


def _chromium_available() -> bool:
    try:
        from navin.documents._chromium import find_chromium

        find_chromium(None)
    except Exception:  # pragma: no cover - depends on the machine
        return False
    return True


class RuleTableTests(unittest.TestCase):
    def test_every_rule_names_a_category_that_carries_points(self) -> None:
        for code, (category, penalty, fix) in word_qa.RULES.items():
            with self.subTest(rule=code):
                self.assertIn(category, word_qa.CATEGORY_POINTS)
                self.assertGreater(penalty, 0)
                self.assertTrue(fix.endswith("."), "a fix should read as an instruction")

    def test_the_categories_add_up_to_a_hundred(self) -> None:
        self.assertEqual(sum(word_qa.CATEGORY_POINTS.values()), 100)

    def test_no_single_fault_can_empty_a_category_on_its_own(self) -> None:
        """One mistake should cost points, never the whole category at once."""
        for code, (category, penalty, _) in word_qa.RULES.items():
            if category == "theme":
                continue  # a document that cannot be re-themed fails that category outright
            with self.subTest(rule=code):
                self.assertLess(penalty, word_qa.CATEGORY_POINTS[category])


class ScoringTests(unittest.TestCase):
    def test_a_clean_page_scores_a_hundred(self) -> None:
        self.assertEqual(word_qa.score_page([])["score"], 100)

    def test_a_fault_costs_its_category_and_nothing_else(self) -> None:
        scored = word_qa.score_page([{"code": "tiny-text", "count": 1, "examples": []}])
        self.assertEqual(scored["categories"]["readability"], 10)
        self.assertEqual(scored["categories"]["layout"], word_qa.CATEGORY_POINTS["layout"])
        self.assertEqual(scored["score"], 95)

    def test_repeating_a_fault_costs_more_but_not_endlessly(self) -> None:
        once = word_qa.score_page([{"code": "tiny-text", "count": 1, "examples": []}])["score"]
        thrice = word_qa.score_page([{"code": "tiny-text", "count": 3, "examples": []}])["score"]
        many = word_qa.score_page([{"code": "tiny-text", "count": 40, "examples": []}])["score"]
        self.assertLess(thrice, once)
        self.assertLess(many, thrice)
        # Twelve stretched images is one habit to fix, so the cost plateaus.
        self.assertEqual(many, word_qa.score_page([{"code": "tiny-text", "count": 4, "examples": []}])["score"])

    def test_a_category_never_goes_negative(self) -> None:
        scored = word_qa.score_page(
            [
                {"code": "overflow-right", "count": 30, "examples": []},
                {"code": "overflow-bottom", "count": 30, "examples": []},
                {"code": "orphan-heading", "count": 30, "examples": []},
            ]
        )
        self.assertEqual(scored["categories"]["layout"], 0)
        self.assertGreaterEqual(scored["score"], 0)

    def test_findings_come_back_with_the_fix_attached(self) -> None:
        finding = word_qa.score_page(
            [{"code": "low-contrast", "count": 2, "examples": ["p.note at 2.9:1"]}]
        )["findings"][0]
        self.assertEqual(finding["category"], "readability")
        self.assertEqual(finding["examples"], ["p.note at 2.9:1"])
        self.assertIn("4.5:1", finding["fix"])


class DedupeTests(unittest.TestCase):
    def test_identical_findings_become_one_entry_with_a_count(self) -> None:
        grouped = word_qa._dedupe(
            [{"code": "tiny-text", "detail": f"span {n}"} for n in range(6)]
        )
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["count"], 6)
        self.assertEqual(len(grouped[0]["examples"]), 3, "three examples are enough to act on")

    def test_a_code_the_rules_do_not_know_is_dropped(self) -> None:
        self.assertEqual(word_qa._dedupe([{"code": "invented", "detail": "x"}]), [])


class ReportTests(unittest.TestCase):
    def test_the_weakest_page_decides_the_verdict(self) -> None:
        report = {
            "document": "d.html",
            "pages": [
                {"page": 1, "score": 100, "findings": []},
                {"page": 2, "score": 60, "findings": []},
            ],
            "score": 80,
            "worst_page": 60,
            "threshold": 85,
            "pass": False,
            "rework": [2],
        }
        text = word_qa.format_report(report)
        self.assertIn("REWORK", text)
        self.assertIn("pages to rework: 2", text)

    def test_a_missing_file_is_reported_as_such(self) -> None:
        from navin.documents._chromium import ConversionError

        with self.assertRaises(ConversionError):
            word_qa.review(Path("/nonexistent/document.html"))


class ThemeFindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="navin-qa-test-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _write(self, html: str) -> Path:
        path = self.dir / "document.html"
        path.write_text(html, encoding="utf-8")
        return path

    def test_an_untokenized_document_is_flagged(self) -> None:
        found = word_qa._theme_findings(self._write("<style>h1{color:red}</style>"))
        self.assertEqual(found[0]["code"], "theme-missing")

    def test_a_stray_colour_is_flagged_with_the_colour_itself(self) -> None:
        found = word_qa._theme_findings(
            self._write(
                '<style id="navin-theme" data-theme="minimal">:root{--nv-accent:#111}</style>'
                "<style>h1{color:#C0FFEE}</style>"
            )
        )
        self.assertEqual(found[0]["code"], "theme-literal-color")
        self.assertEqual(found[0]["examples"], ["#c0ffee"])

    def test_a_tokenized_document_raises_nothing(self) -> None:
        self.assertEqual(
            word_qa._theme_findings(
                self._write(
                    '<style id="navin-theme" data-theme="minimal">:root{--nv-accent:#111}</style>'
                    "<style>h1{color:var(--nv-accent)}</style>"
                )
            ),
            [],
        )


@unittest.skipUnless(_chromium_available(), "chromium is not installed")
class PageInspectionTests(unittest.TestCase):
    """The half that only a browser can answer."""

    maxDiff = None

    # Long enough that the page counts as running text: the hierarchy rules stay
    # quiet on a page holding a couple of lines, where a title proves nothing.
    COPY = "<p>Trading held up through the quarter, with volumes ahead of plan in every region.</p>"

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="navin-qa-page-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _review(self, body: str, *, style: str = "") -> dict:
        html = (
            "<html><head>"
            '<style id="navin-theme" data-theme="minimal">:root{--nv-accent:#111111;}</style>'
            "<style>* { margin: 0; padding: 0; box-sizing: border-box; }"
            ".page { width: 210mm; min-height: 297mm; padding: 20mm; background: #fff; }"
            "@page { size: A4; margin: 0; }" + style + "</style></head>"
            f"<body><div class='page'>{body}</div></body></html>"
        )
        path = self.dir / "document.html"
        path.write_text(html, encoding="utf-8")
        return word_qa.review(path)

    def _codes(self, report: dict) -> set[str]:
        return {finding["code"] for page in report["pages"] for finding in page["findings"]}

    def test_a_well_built_page_comes_back_clean(self) -> None:
        body = "<h1>Quarterly review</h1>" + "<p>Trading held up through the quarter.</p>" * 12
        report = self._review(body)
        self.assertEqual(self._codes(report), set())
        self.assertEqual(report["score"], 100)
        self.assertTrue(report["pass"])

    def test_grey_on_white_below_the_readable_ratio_is_caught(self) -> None:
        body = "<h1>Notes</h1>" + "<p class='faint'>Small print nobody can read.</p>" * 10
        report = self._review(body, style=".faint { color: #C8CDD6; }")
        self.assertIn("low-contrast", self._codes(report))

    def test_text_running_past_the_margin_is_caught(self) -> None:
        body = "<h1>Wide</h1>" + "<p>Body copy.</p>" * 10 + "<p class='wide'>x</p>"
        report = self._review(body, style=".wide { position: relative; left: 260mm; }")
        self.assertIn("overflow-right", self._codes(report))

    def test_a_footer_placed_in_the_margin_is_not_an_overflow(self) -> None:
        """Page furniture lives outside the text column on purpose."""
        body = (
            "<h1>Report</h1>"
            + "<p>Body copy for the page.</p>" * 12
            + "<div class='foot'><span>Confidential</span></div>"
        )
        report = self._review(body, style=".foot { position: absolute; bottom: 8mm; left: 20mm; }")
        self.assertNotIn("overflow-bottom", self._codes(report))

    def test_a_heading_inside_a_column_grid_is_reported_as_lost_to_word(self) -> None:
        body = (
            "<div class='row'><div class='col'><h2>Left</h2><p>One.</p></div>"
            "<div class='col'><h2>Right</h2><p>Two.</p></div></div>"
            + "<p>Body copy for the page.</p>" * 10
        )
        report = self._review(body, style=".row { display: flex; gap: 8mm; } .col { flex: 1; }")
        self.assertIn("heading-in-grid", self._codes(report))

    def test_a_heading_that_is_the_rows_own_child_is_left_alone(self) -> None:
        """html2docx keeps a title bar as a heading, so QA must not cry wolf."""
        body = (
            "<div class='row'><h2>Section title</h2><span>Ref. 12</span></div>"
            + "<p>Body copy for the page.</p>" * 10
        )
        report = self._review(
            body, style=".row { display: flex; justify-content: space-between; }"
        )
        self.assertNotIn("heading-in-grid", self._codes(report))

    def test_a_div_dressed_as_a_title_is_named(self) -> None:
        body = "<div class='title'>Executive summary</div>" + self.COPY * 8
        report = self._review(body, style=".title { font-size: 24px; font-weight: 700; }")
        codes = self._codes(report)
        self.assertIn("heading-styled-div", codes)
        self.assertNotIn("no-heading", codes, "naming the culprit replaces the vaguer finding")

    def test_a_table_wider_than_the_column_is_caught(self) -> None:
        body = (
            "<h1>Data</h1><table class='wide'><tr><th>A</th><th>B</th></tr>"
            "<tr><td>1</td><td>2</td></tr></table>" + "<p>Body copy.</p>" * 10
        )
        report = self._review(body, style=".wide { width: 260mm; }")
        self.assertIn("table-overflow", self._codes(report))

    def test_a_cover_page_is_not_judged_on_how_full_it_is(self) -> None:
        body = "<h1>Annual report</h1><p>2026</p>"
        report = self._review(f"<div data-doc-cover></div>{body}")
        self.assertNotIn("page-nearly-empty", self._codes(report))


@unittest.skipUnless(_chromium_available(), "chromium is not installed")
class ShippedTemplateTests(unittest.TestCase):
    """The library has to survive its own QA, or the score means nothing."""

    def test_the_reference_templates_pass(self) -> None:
        for name in ("rapport_executif", "proposition_commerciale", "compte_rendu_reunion"):
            with self.subTest(template=name):
                report = word_qa.review(TEMPLATES / name / "document.html")
                self.assertTrue(
                    report["pass"],
                    f"{name} scored {report['worst_page']}: {word_qa.format_report(report)}",
                )


if __name__ == "__main__":
    unittest.main()
