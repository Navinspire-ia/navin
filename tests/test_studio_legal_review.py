# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""contract-reviewer is preloaded on legal /studio cards only."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

import navin.agent.skills  # noqa: F401
from navin.command.builtin import (
    _LEGAL_CARD_FOCUS_MARKER,
    _LEGAL_CONTRACT_REVIEW_CLAUSE,
    _WORKFLOW_BRIEFS,
    _studio_uses_legal_contract,
    _workflow_handler,
)
from navin.command.modules import PRELOAD_SKILLS_METADATA_KEY
from navin.utils.document_templates import (
    document_template_runtime_lines,
    normalize_document_template_mention,
)

ROOT = Path(__file__).resolve().parents[1]
STUDIO_UI = ROOT / "webui/src/components/studio/StudioWorkspace.tsx"
LEGAL_SLUGS = (
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
)


def _run_studio(focus: str, metadata: dict | None = None):
    msg = SimpleNamespace(
        content="",
        metadata={"product_module": "content", **(metadata or {})},
        channel="cli",
        chat_id="studio-legal-test",
    )
    ctx = SimpleNamespace(
        args=focus,
        raw=f"/studio {focus}",
        msg=msg,
        loop=None,
    )
    result = asyncio.run(_workflow_handler("/studio")(ctx))  # type: ignore[arg-type]
    return result, msg


class StudioLegalReviewTest(unittest.TestCase):
    def test_studio_brief_does_not_preload_reviewer_for_every_card(self) -> None:
        names = [
            name.strip()
            for name in _WORKFLOW_BRIEFS["/studio"][1].split(",")
            if name.strip()
        ]
        self.assertNotIn("contract-reviewer", names)
        self.assertIn("docx-generator", names)
        self.assertIn("pptx-generator", names)

    def test_legal_detector_accepts_attached_contract_only(self) -> None:
        self.assertFalse(_studio_uses_legal_contract({}, "pitch deck textbook"))
        self.assertTrue(
            _studio_uses_legal_contract(
                {},
                "Draft a complete, professional Mutual NDA using the attached "
                "contractual template.",
            )
        )
        self.assertTrue(
            _studio_uses_legal_contract(
                {
                    "document_template": {
                        "category": "word",
                        "name": "nda_mutuel",
                    }
                },
                "fill this NDA for two parties",
            )
        )
        self.assertFalse(
            _studio_uses_legal_contract(
                {
                    "document_template": {
                        "category": "ppt",
                        "name": "textbook",
                    }
                },
                "project pitch deck 10-12 slides",
            )
        )
        self.assertFalse(
            _studio_uses_legal_contract(
                {
                    "document_template": {
                        "category": "word",
                        "name": "rapport_executif",
                    }
                },
                "executive report for the board",
            )
        )

    def test_handler_preloads_reviewer_for_legal_template(self) -> None:
        result, msg = _run_studio(
            "fill this NDA for two parties",
            {"document_template": {"category": "word", "name": "nda_mutuel"}},
        )
        self.assertIsNone(result)
        preloaded = msg.metadata.get(PRELOAD_SKILLS_METADATA_KEY) or []
        self.assertIn("contract-reviewer", preloaded)
        self.assertIn("docx-generator", preloaded)
        self.assertIn(_LEGAL_CONTRACT_REVIEW_CLAUSE, msg.content)
        self.assertIn("contract-reviewer", msg.content)

    def test_handler_preloads_reviewer_for_legal_card_prompt(self) -> None:
        result, msg = _run_studio(
            "Draft a complete, professional Mutual NDA using the attached "
            "contractual template. Adapt every clause."
        )
        self.assertIsNone(result)
        preloaded = msg.metadata.get(PRELOAD_SKILLS_METADATA_KEY) or []
        self.assertIn("contract-reviewer", preloaded)
        self.assertIn(_LEGAL_CARD_FOCUS_MARKER, msg.content.casefold())

    def test_handler_skips_reviewer_for_ppt_and_word_report(self) -> None:
        for focus, template in (
            (
                "Create a project pitch deck using the attached HTML design template",
                {"category": "ppt", "name": "textbook"},
            ),
            (
                "Create an executive report for the board",
                {"category": "word", "name": "rapport_executif"},
            ),
        ):
            with self.subTest(template=template["name"]):
                result, msg = _run_studio(
                    focus, {"document_template": template}
                )
                self.assertIsNone(result)
                preloaded = msg.metadata.get(PRELOAD_SKILLS_METADATA_KEY) or []
                self.assertNotIn("contract-reviewer", preloaded)
                self.assertNotIn(_LEGAL_CONTRACT_REVIEW_CLAUSE, msg.content)


class StudioLegalCardsUiTest(unittest.TestCase):
    def test_twenty_five_legal_cards_bind_templates_and_reviewer(self) -> None:
        text = STUDIO_UI.read_text(encoding="utf-8")
        self.assertIn("LEGAL_CONTRACTS", text)
        self.assertIn("legal: true", text)
        self.assertIn("contract-reviewer", text)
        self.assertIn(_LEGAL_CARD_FOCUS_MARKER, text)
        for slug in LEGAL_SLUGS:
            with self.subTest(slug=slug):
                self.assertIn(f'"{slug}"', text)

    def test_legal_slugs_are_real_legal_contract_templates(self) -> None:
        self.assertEqual(len(LEGAL_SLUGS), 25)
        for slug in LEGAL_SLUGS:
            mention = normalize_document_template_mention(
                {"category": "word", "name": slug}
            )
            self.assertIsNotNone(mention, slug)
            assert mention is not None
            self.assertEqual(mention.get("kind"), "legal_contract", slug)


class DocumentTemplatePipelineTest(unittest.TestCase):
    """PPT and Word consume the attached template; they do not invent a look."""

    def test_ppt_runtime_binds_theme_to_ppt_design(self) -> None:
        mention = normalize_document_template_mention(
            {"category": "ppt", "name": "textbook"}
        )
        self.assertIsNotNone(mention)
        lines = document_template_runtime_lines(
            {"document_template": {"category": "ppt", "name": "textbook"}}
        )
        blob = "\n".join(lines)
        self.assertIn("Document Template Attachment", blob)
        self.assertIn("templates/ppt/textbook", blob)
        self.assertIn("navin.documents.ppt_design", blob)
        self.assertIn("html2pptx", blob)
        self.assertNotIn("kind=legal_contract", blob)

    def test_word_report_runtime_uses_word_design(self) -> None:
        lines = document_template_runtime_lines(
            {"document_template": {"category": "word", "name": "rapport_executif"}}
        )
        blob = "\n".join(lines)
        self.assertIn("Document Template Attachment", blob)
        self.assertIn("templates/word/rapport_executif", blob)
        self.assertIn("word_design render", blob)
        self.assertIn(".docx", blob)
        self.assertNotIn("kind=legal_contract", blob)

    def test_word_legal_runtime_keeps_contract_html(self) -> None:
        lines = document_template_runtime_lines(
            {"document_template": {"category": "word", "name": "nda_mutuel"}}
        )
        blob = "\n".join(lines)
        self.assertIn("Document Template Attachment", blob)
        self.assertIn("kind=legal_contract", blob)
        self.assertIn("templates/word/nda_mutuel", blob)
        self.assertIn("replace every sample string", blob.casefold())
        self.assertIn(".docx", blob)


class StudioLegalDocsTest(unittest.TestCase):
    def test_docs_say_reviewer_is_legal_only(self) -> None:
        paths = (
            ROOT / "docs/navin_contenant/en/skills.md",
            ROOT / "docs/navin_contenant/fr/skills.md",
            ROOT / "site/front/content/docs/navin_contenant/en/skills.md",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertIn("contract-reviewer", text)
                self.assertIn("25", text)
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)


if __name__ == "__main__":
    unittest.main()
