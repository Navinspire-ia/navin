# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The template-less document turn: detection and the guidance it injects."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.utils import document_templates as dt


class DetectionTests(unittest.TestCase):
    def _wants(self, text: str, metadata: dict | None = None) -> bool:
        return dt.wants_document_toolchain(text, metadata)

    def test_explicit_formats_trigger(self) -> None:
        for text in (
            "fais moi un pdf du compte rendu",
            "Generate a PPTX for the board",
            "je veux une présentation de 10 diapos",
            "export this as .docx please",
            "build a spreadsheet of the costs",
        ):
            self.assertTrue(self._wants(text), text)

    def test_weak_words_need_a_creation_verb(self) -> None:
        self.assertTrue(self._wants("rédige un rapport pour le client"))
        self.assertTrue(self._wants("write a proposal for Globex"))
        self.assertFalse(self._wants("the report said the server was down"))
        self.assertFalse(self._wants("open the word at index 3 of the array"))

    def test_ordinary_coding_talk_does_not_trigger(self) -> None:
        for text in (
            "fix the failing test in tests/test_loop.py",
            "why does the deck of cards shuffle twice",
            "add a docstring to this function",
            "",
        ):
            self.assertFalse(self._wants(text), text)

    def test_an_attached_office_file_triggers_and_a_picked_template_does_not(self) -> None:
        attached = {"file_mentions": [{"path": "/w/brand.pptx"}]}
        self.assertTrue(self._wants("regarde ce fichier", attached))
        picked = {dt.DOCUMENT_TEMPLATE_METADATA_KEY: {"category": "ppt", "name": "aurora_glass"}}
        self.assertFalse(self._wants("fais une présentation", picked))

    def test_a_huge_message_is_skipped(self) -> None:
        self.assertFalse(self._wants("pdf " * 6000))


class RuntimeLinesTests(unittest.TestCase):
    def test_nothing_without_a_workspace_or_an_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(dt.document_toolchain_runtime_lines("un pdf", None, workspace=None), [])
            self.assertEqual(dt.document_toolchain_runtime_lines("fix the bug", None, workspace=Path(tmp)), [])

    def test_the_block_names_every_generator_and_materializes_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            lines = dt.document_toolchain_runtime_lines(
                "fais un pdf et un docx du rapport",
                {"file_mentions": [{"path": str(workspace / "brand.pptx")}]},
                workspace=workspace,
            )
            joined = "\n".join(lines)
            tools = workspace / ".navin" / "resources" / "tools"
            present = {p.name for p in tools.glob("*.py")} if tools.is_dir() else set()
            engine_css = list((workspace / ".navin" / "resources").rglob("navin-ppt.css"))

        self.assertTrue(lines)
        self.assertIn("START NOW", joined)
        for tool in (
            "word_design.py", "word_qa.py", "html2docx.py",
            "ppt_design.py", "ppt_qa.py", "html2pptx.py",
            "html2pdf.py", "html2xlsx.py",
            "office_template.py", "doc_check.py", "preview_document.py",
        ):
            self.assertIn(tool, joined, tool)
            self.assertIn(tool, present, f"{tool} not materialized")
        for helper in ("_office.py", "_pdf.py", "_chromium.py"):
            self.assertIn(helper, present, helper)
        self.assertIn("Attached Office files", joined)
        self.assertIn("brand.pptx", joined)
        self.assertNotIn(tmp, joined, "the block must use workspace-relative paths")
        self.assertIn("no em/en dashes", joined)
        self.assertTrue(engine_css, "the PPT engine must be materialized for ppt_design")

    def test_the_second_call_reuses_the_copy(self) -> None:
        import time

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            dt.document_toolchain_runtime_lines("make a pdf", None, workspace=workspace)
            tools = workspace / ".navin" / "resources" / "tools"
            before = {p.name: p.stat().st_mtime_ns for p in tools.glob("*.py")}
            time.sleep(0.02)
            dt.document_toolchain_runtime_lines("make a pdf", None, workspace=workspace)
            after = {p.name: p.stat().st_mtime_ns for p in tools.glob("*.py")}
        self.assertEqual(before, after, "unchanged converters must not be rewritten on every turn")


class TemplateGuidanceTests(unittest.TestCase):
    def test_pdf_templates_ship_with_their_html_and_get_pdf_guidance(self) -> None:
        root = dt.templates_root()
        pdf_root = root / "pdf"
        if not pdf_root.is_dir():
            self.skipTest("bundled pdf templates are not present in this checkout")
        for folder in sorted(p for p in pdf_root.iterdir() if p.is_dir() and not p.name.startswith("_")):
            self.assertTrue((folder / "metadata.json").is_file(), folder.name)
            self.assertTrue((folder / "document.html").is_file(), f"{folder.name} has no document.html")
            self.assertTrue((folder / "image.png").is_file(), f"{folder.name} has no preview image")
        with tempfile.TemporaryDirectory() as tmp:
            name = next(p.name for p in sorted(pdf_root.iterdir()) if p.is_dir() and not p.name.startswith("_"))
            lines = dt.document_template_runtime_lines(
                {dt.DOCUMENT_TEMPLATE_METADATA_KEY: {"category": "pdf", "name": name}},
                workspace=Path(tmp),
            )
        joined = "\n".join(lines)
        self.assertIn("html2pdf", joined)
        self.assertIn("word_qa", joined)
        self.assertIn("doc_check", joined)


if __name__ == "__main__":
    unittest.main()
