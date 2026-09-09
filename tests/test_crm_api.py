# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""CRM store: sqlite, convert, kanban, invite, audit, line rollup."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.crm.db import sqlite_path
from navin.crm.store import (
    accept_invite,
    convert_lead,
    create_record,
    dashboard,
    delete_record,
    due_followups,
    get_settings,
    invite_member,
    list_audit,
    list_lines,
    list_members,
    list_records,
    sync_status,
    update_record,
    update_settings,
)
from navin.webui.crm_api import create_payload, delete_payload, list_payload, settings_payload


class _Scope:
    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path


class CrmStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="navin-crm-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_create_contact_and_list(self) -> None:
        row = create_record(
            self.root,
            "contacts",
            {"firstName": "Safouene", "lastName": "Ben", "email": "s@example.com"},
        )
        self.assertTrue(str(row["id"]).startswith("ct-"))
        listed = list_records(self.root, "contacts")
        self.assertEqual(1, len(listed))
        self.assertEqual("Safouene", listed[0]["firstName"])
        self.assertTrue(sqlite_path(self.root).is_file())

    def test_convert_lead_accepts_opportunity_fields(self) -> None:
        company = create_record(self.root, "companies", {"name": "Toyota"})
        contact = create_record(
            self.root,
            "contacts",
            {"firstName": "Safouene", "lastName": "Ben", "email": "s@t.tn", "country": "TN"},
        )
        lead = create_record(
            self.root,
            "leads",
            {"name": "Safouene Ben", "company": "Toyota", "email": "s@t.tn", "country": "TN"},
        )
        create_record(
            self.root,
            "activities",
            {"kind": "note", "title": "Note lead", "body": "brief", "leadId": lead["id"]},
        )
        result = convert_lead(
            self.root,
            lead["id"],
            owner="Aymen",
            fields={
                "opportunityName": "Deal IA",
                "companyId": company["id"],
                "contactId": contact["id"],
                "amount": 12000,
                "currency": "TND",
                "stage": "qualifie",
                "expectedCloseDate": "2026-09-01",
                "ownerId": "Aymen",
            },
        )
        self.assertTrue(result["created"])
        self.assertEqual("Deal IA", result["opportunity"]["name"])
        self.assertEqual(12000, result["opportunity"]["amount"])
        self.assertEqual("TND", result["opportunity"]["currency"])
        self.assertEqual("qualifie", result["opportunity"]["stage"])
        self.assertEqual("2026-09-01", result["opportunity"]["closeDate"])
        self.assertEqual(company["id"], result["company"]["id"])
        self.assertEqual(contact["id"], result["contact"]["id"])
        self.assertEqual("TN", result["lead"]["country"])
        copied = [
            row
            for row in list_records(self.root, "activities")
            if row.get("title") == "Note lead" and row.get("opportunityId") == result["opportunity"]["id"]
        ]
        self.assertTrue(copied)
        again = convert_lead(self.root, lead["id"], fields={"opportunityName": "Other"})
        self.assertFalse(again["created"])
        self.assertEqual(result["opportunity"]["id"], again["opportunity"]["id"])

    def test_convert_lead_creates_three_objects(self) -> None:
        lead = create_record(
            self.root,
            "leads",
            {"name": "Safouene Ben", "company": "Toyota", "email": "s@t.tn"},
        )
        result = convert_lead(self.root, lead["id"], owner="Aymen")
        self.assertTrue(result["created"])
        self.assertEqual("Toyota", result["company"]["name"])
        self.assertEqual("Safouene", result["contact"]["firstName"])
        self.assertEqual("converti", result["lead"]["status"])
        again = convert_lead(self.root, lead["id"])
        self.assertFalse(again["created"])
        self.assertEqual(result["opportunity"]["id"], again["opportunity"]["id"])
        history = list_audit(self.root, kind="leads", record_id=lead["id"])
        self.assertTrue(any(item["action"] == "convert" for item in history))

    def test_dashboard_counts_open_pipeline(self) -> None:
        create_record(
            self.root,
            "opportunities",
            {"name": "Toyota IA", "amount": 90000, "probability": 70, "stage": "proposition"},
        )
        create_record(
            self.root,
            "opportunities",
            {"name": "Won", "amount": 10000, "probability": 100, "stage": "gagne"},
        )
        snap = dashboard(self.root)
        self.assertEqual(90000, snap["pipelineTotal"])
        self.assertEqual(1, snap["openCount"])
        self.assertEqual(63000, snap["forecast"])
        self.assertIn("followups", snap)

    def test_http_payload_accepts_path_project(self) -> None:
        scope = _Scope(self.root)
        created = create_payload(
            scope,  # type: ignore[arg-type]
            "contacts",
            {"firstName": "Aymen", "lastName": "G"},
        )
        listed = list_payload(scope, "contacts")  # type: ignore[arg-type]
        self.assertEqual(created["record"]["id"], listed["records"][0]["id"])

    def test_kanban_stage_move_writes_audit(self) -> None:
        deal = create_record(
            self.root,
            "opportunities",
            {"name": "Kanban", "amount": 1000, "stage": "nouveau"},
        )
        moved = update_record(self.root, "opportunities", deal["id"], {"stage": "negociation"})
        self.assertEqual("negociation", moved["stage"])
        history = list_audit(self.root, kind="opportunities", record_id=deal["id"])
        self.assertTrue(any(item["action"] == "stage" for item in history))

    def test_invite_accept_and_members(self) -> None:
        invite_member(
            self.root,
            identity="colleague@company.com",
            role="member",
            actor="owner@company.com",
        )
        roster = list_members(self.root)
        self.assertEqual(1, len(roster["invites"]))
        self.assertEqual("pending", roster["invites"][0]["status"])
        accepted = accept_invite(self.root, actor="colleague@company.com", accept=True)
        self.assertEqual("accepted", accepted["status"])
        self.assertEqual("member", accepted["member"]["role"])
        again = list_members(self.root)
        self.assertEqual(2, len(again["members"]))
        roles = {row["role"] for row in again["members"]}
        self.assertIn("owner", roles)
        self.assertIn("member", roles)

    def test_line_rollup_sets_amount(self) -> None:
        deal = create_record(self.root, "opportunities", {"name": "Pack", "amount": 1})
        create_record(
            self.root,
            "opportunity_lines",
            {"opportunityId": deal["id"], "name": "Licence", "qty": 2, "unitPrice": 1500},
        )
        create_record(
            self.root,
            "opportunity_lines",
            {"opportunityId": deal["id"], "name": "Setup", "qty": 1, "unitPrice": 500},
        )
        listed = list_lines(self.root, deal["id"])
        self.assertEqual(2, len(listed))
        self.assertEqual(3000, listed[0]["total"])
        refreshed = list_records(self.root, "opportunities")[0]
        self.assertEqual(3500, refreshed["amount"])

    def test_sqlite_import_from_json(self) -> None:
        crm_dir = self.root / ".navin" / "crm"
        crm_dir.mkdir(parents=True)
        (crm_dir / "contacts.json").write_text(
            json.dumps(
                {
                    "contacts": [
                        {
                            "id": "ct-imported1",
                            "firstName": "Imported",
                            "lastName": "Lead",
                            "email": "i@example.com",
                            "createdAt": 1,
                            "updatedAt": 1,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        rows = list_records(self.root, "contacts")
        self.assertEqual(1, len(rows))
        self.assertEqual("Imported", rows[0]["firstName"])
        self.assertTrue(sqlite_path(self.root).is_file())

    def test_no_record_cap(self) -> None:
        for index in range(12):
            create_record(self.root, "contacts", {"firstName": f"N{index}", "lastName": "X"})
        self.assertEqual(12, len(list_records(self.root, "contacts")))

    def test_followups_and_sync_status(self) -> None:
        create_record(
            self.root,
            "opportunities",
            {"name": "Stale", "amount": 10, "stage": "nouveau"},
        )
        stale = due_followups(self.root, days=7, create=True)
        self.assertGreaterEqual(len(stale["items"]), 1)
        self.assertGreaterEqual(len(stale["created"]), 1)
        status = sync_status(self.root)
        self.assertIn("crm.sqlite", status["path"])
        self.assertFalse(status["cloud"])
        self.assertIn("memberCount", status)

    def test_settings_default_eur(self) -> None:
        settings = get_settings(self.root)
        self.assertEqual("EUR", settings["currency"])
        self.assertEqual("€", settings["currencySymbol"])
        self.assertEqual("fr-FR", settings["locale"])
        self.assertFalse(settings["configured"])
        snap = dashboard(self.root)
        self.assertEqual("EUR", snap["currency"])
        payload = settings_payload(_Scope(self.root))  # type: ignore[arg-type]
        self.assertEqual("EUR", payload["currency"])
        self.assertTrue(payload["canWrite"])

    def test_update_settings_currency(self) -> None:
        row = update_settings(self.root, {"companyName": "Navin SAS", "currency": "USD"})
        self.assertEqual("USD", row["currency"])
        self.assertEqual("$", row["currencySymbol"])
        self.assertTrue(row["configured"])
        again = get_settings(self.root)
        self.assertEqual("USD", again["currency"])
        self.assertEqual("Navin SAS", again["companyName"])
        opp = create_record(self.root, "opportunities", {"name": "Deal", "amount": 90_000})
        self.assertEqual("USD", opp["currency"])
        snap = dashboard(self.root)
        self.assertEqual("USD", snap["currency"])

    def test_delete_contact(self) -> None:
        row = create_record(
            self.root,
            "contacts",
            {"firstName": "Aymen", "lastName": "G"},
        )
        deleted = delete_record(self.root, "contacts", row["id"])
        self.assertTrue(deleted["ok"])
        self.assertEqual(0, len(list_records(self.root, "contacts")))
        again = create_record(
            self.root,
            "contacts",
            {"firstName": "Safouene", "lastName": "Ben"},
        )
        payload = delete_payload(_Scope(self.root), "contacts", again["id"])  # type: ignore[arg-type]
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main()
