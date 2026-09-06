"""A-to-Z chain: every tenders API action, index, and agent tool against an isolated store."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from navin.agent.tools.tenders import TendersTool
from navin.tenders.errors import TenderError
from navin.tenders.normalize import normalize_tender
from navin.tenders.store import TenderStore
from navin.webui.tenders_api import handle_tenders_action


def _docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Offer model Atelier Cloud</w:t></w:r></w:p></w:body></w:document>",
        )
    return buffer.getvalue()


def _go_notice(**overrides: object) -> dict:
    raw = {
        "title": "Modernisation du SI decisionnel et plateforme Cloud AI",
        "description": "Refonte du SI decisionnel, data platform et hebergement cloud.",
        "country": "FR",
        "buyer": "DINUM",
        "deadline": "2099-12-31",
        "publication_date": "2026-01-15",
        "budget": 250_000,
        "source_url": "https://ted.europa.eu/en/notice/-/detail/2026-999001",
        "reference": "AO-2026-CHAIN",
        "eligibility": "three references",
        "sector": "IT",
    }
    raw.update(overrides)
    return normalize_tender(raw, source_id="ted")


class TendersChainAZTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = TenderStore(Path(self._tmp.name))
        self._patch = patch("navin.webui.tenders_api._store", return_value=self.store)
        self._patch.start()
        os.environ["NAVIN_TENDERS_AI"] = "off"

    def tearDown(self) -> None:
        self._patch.stop()
        os.environ.pop("NAVIN_TENDERS_AI", None)
        self._tmp.cleanup()

    def test_every_action_from_empty_wizard_to_won_and_back(self) -> None:
        snap = handle_tenders_action("snapshot")
        self.assertFalse(snap["wizard_ready"])
        self.assertEqual(snap["tenders"], [])
        self.assertIn("files", snap)
        self.assertTrue((self.store.root / "INDEX.md").is_file())
        self.assertTrue((self.store.root / "book.md").is_file())

        saved = handle_tenders_action(
            "profile",
            {
                "name": "Atelier Chain",
                "legal_name": "Atelier Chain SAS",
                "specialty": "Cloud AI",
                "country": "FR",
                "currency": "EUR",
                "locale": "fr-FR",
                "crafts": "AI,Data,Cloud",
                "countries": "FR,AE",
                "min_score": 70,
                "min_deadline_days": 10,
                "min_budget": 50_000,
                "send_mode": "approval",
                "source_ids": ["ted", "boamp"],
                "wizard_complete": True,
                "wizard_step": 7,
                "brief": "We keep the margin.",
                "channels": {"email": True, "email_to": "ops@atelier.test"},
            },
        )
        self.assertTrue(saved["wizard_ready"])
        self.assertEqual(saved["profile"]["name"], "Atelier Chain")
        self.assertEqual(saved["profile"]["brief"], "We keep the margin.")

        upload = handle_tenders_action(
            "upload",
            {
                "kind": "word_template",
                "name": "modele.docx",
                "data": base64.b64encode(_docx_bytes()).decode("ascii"),
            },
        )
        self.assertEqual(upload["upload"]["kind"], "word_template")
        self.assertIn("Atelier Cloud", upload["upload"]["excerpt"])

        custom = handle_tenders_action(
            "custom-source",
            {
                "name": "Portal X",
                "url": "https://example.gov/tenders",
                "country": "SN",
                "zone": "africa",
                "ingest": "html",
            },
        )
        self.assertEqual(custom["custom_source"]["country"], "SN")
        handle_tenders_action("secret", {"name": "sam_gov", "value": "test-key"})
        self.assertTrue(self.store.has_secret("sam_gov"))

        notice = _go_notice()
        nogo = _go_notice(
            title="Entretien des espaces verts",
            description="Tonte et arrosage.",
            reference="AO-GREEN",
            budget=12_000,
        )
        incoming = [notice, nogo]
        with patch(
            "navin.tenders.desk.collect",
            return_value={
                "tenders": incoming,
                "reports": [{"source_id": "ted", "ok": True, "detail": "2 TED", "count": 2}],
                "discovered_sources": [
                    {"host": "example.gov.xx", "url": "https://example.gov.xx/", "hits": 2}
                ],
            },
        ):
            collected = handle_tenders_action("collect")
        self.assertEqual(len(collected["tenders"]), 2)
        self.assertTrue(collected["collect"][0]["ok"])
        go_row = next(row for row in collected["tenders"] if row["reference"] == "AO-2026-CHAIN")
        self.assertTrue(go_row["go"])
        self.assertEqual(go_row["stage"], "go")

        accepted = handle_tenders_action("discover-accept", {"host": "example.gov.xx"})
        self.assertTrue(any(row.get("added") for row in accepted["discoveries"]))

        hits = handle_tenders_action("search", {"query": "decisionnel"})
        self.assertGreaterEqual(hits["count"], 1)
        self.assertEqual(hits["hits"][0]["id"], go_row["id"])
        listed = handle_tenders_action("list", {"go": "go"})
        self.assertTrue(any(row["id"] == go_row["id"] for row in listed["hits"]))
        got = handle_tenders_action("get", {"id": go_row["id"]})
        self.assertEqual(got["notice"]["buyer"], "DINUM")

        qualified = handle_tenders_action("qualify", {"id": go_row["id"]})
        row = next(item for item in qualified["tenders"] if item["id"] == go_row["id"])
        self.assertTrue(row["go"])
        self.assertTrue(row["go_reason"])
        self.assertGreaterEqual(float(row["score"]), 70)

        written = handle_tenders_action("write", {"id": go_row["id"]})
        row = next(item for item in written["tenders"] if item["id"] == go_row["id"])
        self.assertEqual(row["stage"], "drafting")
        self.assertIn("Atelier Chain", row["response"]["letter"])
        self.assertIn(row["title"], row["response"]["letter"])
        self.assertTrue(row["response"]["from_file"])
        self.assertIn("Atelier Cloud", row["response"]["letter"])
        self.assertIn("Atelier Cloud", row["response"]["methodology"])

        mailed = handle_tenders_action("mail", {"id": go_row["id"], "kind": "clarification"})
        self.assertTrue(mailed["draft"])
        self.assertIn(row["title"], mailed["draft"])
        relance = handle_tenders_action("mail", {"id": go_row["id"], "kind": "relance"})
        self.assertTrue(relance["draft"])

        with (
            patch("navin.crm.outreach.outreach", side_effect=TenderError("no project")),
            patch("navin.crm.outreach._send_email", return_value=None),
        ):
            sent = handle_tenders_action(
                "send",
                {"id": go_row["id"], "to": "acheteur@dinum.gouv.fr", "approved": True},
            )
        self.assertEqual(sent["send"]["to"], "acheteur@dinum.gouv.fr")
        row = next(item for item in sent["tenders"] if item["id"] == go_row["id"])
        self.assertTrue(any(item.get("sent") for item in row.get("mail") or []))

        submitted = handle_tenders_action("stage", {"id": go_row["id"], "stage": "submitted"})
        row = next(item for item in submitted["tenders"] if item["id"] == go_row["id"])
        self.assertEqual(row["stage"], "submitted")
        self.assertTrue(row.get("submitted_at"))

        follow = handle_tenders_action("follow", {"send": False})
        self.assertIn("watch", follow)

        scored = handle_tenders_action("rescore")
        self.assertIn("tenders", scored)

        known = handle_tenders_action(
            "knowledge",
            {
                "methodology": "Design then build.",
                "references": [{"title": "SI decisionnel 2024", "client": "DINUM"}],
            },
        )
        self.assertEqual(known["profile"]["methodology"], "Design then build.")

        ping = handle_tenders_action("notify", {"title": "Navin Tenders", "detail": "chain test"})
        self.assertIn("notify", ping)

        won = handle_tenders_action("stage", {"id": go_row["id"], "stage": "won"})
        self.assertEqual(won["kpis"]["won"], 1)
        self.assertEqual(won["kpis"]["win_rate"], 100.0)

        indexed = handle_tenders_action("index")
        self.assertEqual(indexed["count"], 2)
        self.assertTrue(Path(indexed["files"]["book"]).is_file())
        book = Path(indexed["files"]["book"]).read_text(encoding="utf-8")
        self.assertIn(go_row["id"], book)
        self.assertIn("Atelier Chain", book)

        tool = TendersTool()
        status = asyncio.run(tool.execute(action="status"))
        self.assertIn("TENDERS LOCAL BOOK", status)
        self.assertIn("LOOP", status)
        self.assertIn("tenders action=start", status)
        self.assertIn("Atelier Chain", status)
        self.assertIn(go_row["id"], status)
        searched = asyncio.run(tool.execute(action="search", query="DINUM"))
        self.assertGreaterEqual(searched["count"], 1)
        opened = asyncio.run(tool.execute(action="get", id=go_row["id"]))
        self.assertEqual(opened["notice"]["id"], go_row["id"])
        listed_tool = asyncio.run(tool.execute(action="list", go="false"))
        self.assertTrue(any(row["id"] == nogo["id"] for row in listed_tool["hits"]))

        with self.assertRaises(TenderError):
            handle_tenders_action("send", {"id": go_row["id"], "to": "not-an-email", "approved": True})
        handle_tenders_action("profile", {"send_mode": "draft"})
        with self.assertRaises(TenderError):
            handle_tenders_action(
                "send",
                {"id": go_row["id"], "to": "acheteur@dinum.gouv.fr", "approved": True},
            )

    def test_crm_sync_needs_a_project_and_skips_nogo(self) -> None:
        notice = _go_notice()
        self.store.save_profile(
            {
                "name": "Atelier Chain",
                "specialty": "Cloud AI",
                "country": "FR",
                "currency": "EUR",
                "crafts": ["AI"],
                "countries": ["FR"],
                "source_ids": ["ted"],
                "wizard_complete": True,
            }
        )
        self.store.save_tenders([{**notice, "stage": "no-go", "go": False, "score": 40}])
        with self.assertRaises(TenderError) as ctx:
            handle_tenders_action("crm-sync", {"id": notice["id"]})
        self.assertIn("not a deal", ctx.exception.message)

        self.store.save_tenders([{**notice, "stage": "go", "go": True, "score": 88}])
        with self.assertRaises(TenderError) as ctx:
            handle_tenders_action("crm-sync", {"id": notice["id"]})
        self.assertIn("project folder", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
