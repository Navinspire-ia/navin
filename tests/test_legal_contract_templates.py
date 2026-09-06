"""Contract tests for the built-in legal document template catalog."""

from __future__ import annotations

import json
import re
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORD_TEMPLATES = ROOT / "templates" / "word"

EXPECTED_SLUGS = {
    "nda_mutuel",
    "lettre_intention",
    "protocole_accord",
    "partenariat_commercial",
    "apporteur_affaires",
    "agent_commercial",
    "distribution_exclusive",
    "contrat_revendeur",
    "marque_blanche",
    "partenariat_integrateur",
    "co_developpement",
    "joint_venture",
    "contrat_cadre_services",
    "statement_of_work",
    "contrat_saas",
    "licence_logicielle",
    "sla",
    "dpa_rgpd",
    "developpement_cession_pi",
    "poc_pilote",
    "sous_traitance",
    "contrat_freelance",
    "contrat_travail",
    "pacte_actionnaires",
    "protocole_transactionnel_resiliation",
}

PAGE_RE = re.compile(
    r"<section\b[^>]*\bclass\s*=\s*([\"'])[^\"']*\bpage\b[^\"']*\1",
    re.IGNORECASE,
)
LANG_UND_RE = re.compile(r"<html\b[^>]*\blang\s*=\s*([\"'])und\1", re.IGNORECASE)
RTL_RULE_RE = re.compile(
    r"html\s*\[\s*dir\s*=\s*([\"'])rtl\1\s*\]",
    re.IGNORECASE,
)


def load_metadata(directory: Path) -> dict[str, object]:
    with (directory / "metadata.json").open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise AssertionError(f"{directory.name}: metadata.json must contain an object")
    return data


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise AssertionError(f"{path}: not a valid PNG with an IHDR header")
    return struct.unpack(">II", data[16:24])


class LegalContractTemplateCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.legal_templates: dict[str, tuple[Path, dict[str, object]]] = {}
        for metadata_path in WORD_TEMPLATES.glob("*/metadata.json"):
            metadata = load_metadata(metadata_path.parent)
            if metadata.get("kind") == "legal_contract":
                cls.legal_templates[metadata_path.parent.name] = (
                    metadata_path.parent,
                    metadata,
                )

    def test_catalog_contains_exactly_the_expected_25_legal_contracts(self) -> None:
        self.assertEqual(set(self.legal_templates), EXPECTED_SLUGS)
        self.assertEqual(len(self.legal_templates), 25)

    def test_each_contract_has_complete_metadata_and_ordered_modules(self) -> None:
        for slug, (directory, metadata) in sorted(self.legal_templates.items()):
            with self.subTest(slug=slug):
                self.assertEqual(metadata.get("category"), "word")
                self.assertEqual(metadata.get("kind"), "legal_contract")
                self.assertEqual(metadata.get("output_formats"), ["docx", "pdf"])
                self.assertEqual(metadata.get("language_mode"), "dynamic")
                for field in ("title", "description"):
                    self.assertIsInstance(metadata.get(field), str)
                    self.assertTrue(str(metadata[field]).strip())

                modules = metadata.get("modules")
                self.assertIsInstance(modules, list)
                self.assertGreaterEqual(len(modules), 3)
                module_ids: list[str] = []
                for position, module in enumerate(modules):
                    self.assertIsInstance(module, dict, f"module {position}")
                    self.assertIsInstance(module.get("id"), str)
                    self.assertTrue(module["id"].strip())
                    self.assertIsInstance(module.get("title"), str)
                    self.assertTrue(module["title"].strip())
                    self.assertIsInstance(module.get("required"), bool)
                    module_ids.append(module["id"])
                self.assertEqual(len(module_ids), len(set(module_ids)))
                self.assertTrue(any(module["required"] for module in modules))

                pages = metadata.get("pages")
                self.assertIsInstance(pages, list)
                self.assertGreaterEqual(len(pages), 2)
                self.assertTrue(all(isinstance(page, str) and page.strip() for page in pages))
                self.assertTrue((directory / "document.html").is_file())
                self.assertTrue((directory / "image.png").is_file())

    def test_html_is_multi_page_language_neutral_and_rtl_ready(self) -> None:
        for slug, (directory, metadata) in sorted(self.legal_templates.items()):
            with self.subTest(slug=slug):
                html = (directory / "document.html").read_text(encoding="utf-8")
                pages = PAGE_RE.findall(html)
                self.assertGreaterEqual(len(pages), 2)
                self.assertEqual(len(pages), len(metadata["pages"]))
                self.assertRegex(html, LANG_UND_RE)
                self.assertRegex(html, RTL_RULE_RE)
                self.assertRegex(html, r"(?i)(noto[^;\"']*arabic|amiri)")
                self.assertRegex(html, r"(?i)(direction\s*:\s*rtl|text-align\s*:\s*right)")
                self.assertNotRegex(html, r"(?i)<html\b[^>]*\blang\s*=\s*([\"'])(fr|en)\1")

    def test_each_contract_has_a_nonempty_png_preview(self) -> None:
        for slug, (directory, _) in sorted(self.legal_templates.items()):
            with self.subTest(slug=slug):
                preview = directory / "image.png"
                width, height = png_dimensions(preview)
                self.assertGreaterEqual(width, 320)
                self.assertGreaterEqual(height, 200)
                self.assertGreater(preview.stat().st_size, 1_000)


if __name__ == "__main__":
    unittest.main()
