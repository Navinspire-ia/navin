# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Leads desk: waterfall fill, store merge, snapshot, hunt empty."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.agent.loop import AgentLoop
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.agent.tools.leads import LeadsTool, LeadsToolConfig
from navin.agent.tools.sandbox import writable_host_paths
from navin.command.modules import extra_denied_tools_for_module
from navin.config.paths import get_runtime_subdir
from navin.gateway.heartbeat_desks import tick_heartbeat_desks
from navin.leads.desk import (
    format_agent_status,
    hunt,
    lookalike,
    outreach,
    push_crm,
    rescore,
    sequence_start,
    set_stage,
    snapshot,
)
from navin.leads.errors import LeadsError
from navin.leads.heartbeat import (
    HEARTBEAT_LEADS_ACTIONS,
    heartbeat_prompt_note,
    profile_is_armed,
    tick_watch,
)
from navin.leads.qualify import score_lead
from navin.leads.scrape_net import extract_contacts, hunt_web, is_linkedin_url, parse_search_hits
from navin.leads.sources import catalog
from navin.leads.store import LeadsStore, lead_key, merge_lead
from navin.leads.waterfall import (
    USER_AGENT,
    _basic_auth,
    _http_headers,
    apply_fields,
    enrich_crunchbase,
    enrich_hunter_verify,
    enrich_places_details,
    enrich_row,
    hunt_companies,
    hunt_crunchbase,
    hunt_opencorporates,
    hunt_places,
    hunt_sirene,
    is_public_body,
    probe_sirene,
)
from navin.webui.leads_api import handle_leads_action

SIRENE_PAYLOAD = {
    "results": [
        {
            "nom_complet": "Acme Logistics",
            "siren": "123456789",
            "domaine": "acme-logistics.fr",
            "section_activite_principale": "logistique",
            "tranche_effectif_salarie": "22",
        },
        {"nom_complet": "", "siren": "000"},
    ]
}


class LeadsDeskTest(unittest.TestCase):
    def test_linkedin_is_reference_only(self) -> None:
        linkedin = next(row for row in catalog() if row["id"] == "linkedin")
        self.assertEqual(linkedin["kind"], "reference")
        self.assertIn("never scrapes", str(linkedin["notes"]).lower())

    def test_apply_fields_keeps_the_first_email(self) -> None:
        row = apply_fields(
            {"company": "Acme", "email": "first@acme.io", "email_status": "verified"},
            {"email": "second@acme.io", "phone": "+33123456789", "source": "hunter"},
        )
        self.assertEqual(row["email"], "first@acme.io")
        self.assertEqual(row["phone"], "+33123456789")
        self.assertEqual(row["source"], "hunter")

    def test_apply_fields_does_not_overwrite_source(self) -> None:
        row = apply_fields({"source": "sirene"}, {"source": "apollo", "signal": "funding"})
        self.assertEqual(row["source"], "sirene")
        self.assertIn("funding", row["signal"])

    def test_score_rewards_verified_contact(self) -> None:
        weak = score_lead({"company": "Acme"}, {"countries": ["FR"]})
        strong = score_lead(
            {
                "email": "cto@acme.io",
                "email_status": "verified",
                "phone": "+33123456789",
                "linkedin_url": "https://www.linkedin.com/in/x",
                "person": "Jean Dupont",
                "role": "CTO",
                "country": "FR",
                "sector": "logistique",
            },
            {"countries": ["FR"], "sector": "logistique"},
        )
        self.assertGreaterEqual(strong, 80)
        self.assertLess(weak, strong)

    def test_companies_house_auth_is_basic_base64(self) -> None:
        header = _basic_auth("uk-key")
        expected = "Basic " + base64.b64encode(b"uk-key:").decode("ascii")
        self.assertEqual(header, expected)

    def test_sirene_parse_skips_empty_names(self) -> None:
        with patch("navin.leads.waterfall._get_json", return_value=SIRENE_PAYLOAD):
            rows = hunt_sirene({"sector": "logistique", "size_min": 50, "size_max": 500}, 10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Acme Logistics")
        self.assertEqual(rows[0]["country"], "FR")
        self.assertEqual(rows[0]["source"], "sirene")
        self.assertEqual(rows[0]["extra"]["siren"], "123456789")

    def test_sirene_skips_communes(self) -> None:
        payload = {
            "results": [
                {"nom_complet": "COMMUNE DE RIVIERE SAAS ET GOURBY", "siren": "1"},
                {"nom_complet": "Datadog France", "siren": "2", "domaine": "datadoghq.com"},
            ]
        }
        with patch("navin.leads.waterfall._get_json", return_value=payload):
            rows = hunt_sirene({"sector": "SaaS", "size_min": 20, "size_max": 200}, 10)
        self.assertEqual([row["company"] for row in rows], ["Datadog France"])

    def test_store_dedupes_and_keeps_verified_email(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            first = {
                "company": "Acme",
                "domain": "acme.io",
                "email": "cto@acme.io",
                "email_status": "verified",
                "source": "hunter",
            }
            store.upsert_leads([first])
            rows, added = store.upsert_leads(
                [
                    {
                        "company": "Acme Inc",
                        "domain": "acme.io",
                        "email": "cto@acme.io",
                        "email_status": "unverified",
                        "phone": "+33123456789",
                    }
                ]
            )
            self.assertEqual(added, 0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["email"], "cto@acme.io")
            self.assertEqual(rows[0]["email_status"], "verified")
            self.assertEqual(rows[0]["phone"], "+33123456789")
            self.assertEqual(lead_key(rows[0]), "e:cto@acme.io")

    def test_merge_lead_skips_blank_overwrite(self) -> None:
        merged = merge_lead(
            {"id": "ld-1", "company": "Acme", "email": "a@acme.io"},
            {"id": "ld-2", "email": "", "phone": "+33123456789"},
        )
        self.assertEqual(merged["id"], "ld-1")
        self.assertEqual(merged["email"], "a@acme.io")
        self.assertEqual(merged["phone"], "+33123456789")

    def test_snapshot_and_unknown_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            with patch("navin.webui.leads_api._store", return_value=store):
                snap = handle_leads_action("snapshot")
                self.assertIn("providers", snap)
                self.assertIn("channels", snap)
                self.assertIn("ready", snap["channels"])
                self.assertFalse(snap["wizard_ready"])
                self.assertEqual(snap["kpis"]["total"], 0)
                with self.assertRaises(LeadsError) as ctx:
                    handle_leads_action("explode")
                self.assertEqual(ctx.exception.status, 400)

    def test_hunt_empty_is_422(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            with patch("navin.webui.leads_api._store", return_value=store):
                with patch("navin.leads.desk.hunt_companies", return_value=[]):
                    with self.assertRaises(LeadsError) as ctx:
                        hunt(
                            store,
                            {
                                "icp_name": "SaaS FR",
                                "sector": "SaaS",
                                "countries": ["US"],
                                "wizard_ready": True,
                            },
                        )
                    self.assertEqual(ctx.exception.status, 422)

    def test_hunt_upserts_from_free_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            found = [
                {
                    "company": "Acme Logistics",
                    "domain": "acme-logistics.fr",
                    "country": "FR",
                    "sector": "logistique",
                    "source": "sirene",
                    "extra": {"siren": "123456789"},
                }
            ]
            with patch("navin.leads.desk.hunt_companies", return_value=found):
                with patch("navin.leads.desk.enrich_row", side_effect=lambda row, *_: {**row, "score": 40, "tier": "C"}):
                    payload = hunt(store, {"icp_name": "Logistique FR", "sector": "logistique", "count": 20})
            self.assertEqual(payload["hunt"]["added"], 1)
            self.assertEqual(payload["leads"][0]["company"], "Acme Logistics")
            self.assertTrue(payload["wizard_ready"])
            again = snapshot(store)
            self.assertEqual(again["kpis"]["total"], 1)

    def test_sandbox_can_write_leads_runtime_dir(self) -> None:
        leads_dir = get_runtime_subdir("leads").resolve()
        paths = writable_host_paths(str(tempfile.gettempdir()))
        resolved = []
        for item in paths:
            try:
                resolved.append(Path(item).resolve())
            except (OSError, RuntimeError, ValueError):
                continue
        self.assertIn(leads_dir, resolved)

    def test_other_studios_hide_the_leads_tool(self) -> None:
        self.assertIn("leads", extra_denied_tools_for_module("career"))
        self.assertIn("leads", extra_denied_tools_for_module("tenders"))
        self.assertIn("career", extra_denied_tools_for_module("leads"))
        self.assertNotIn("leads", extra_denied_tools_for_module("leads"))
        denied = AgentLoop._locked_denied_tools(None, {"product_module": "leads", "heartbeat": True})
        self.assertIn("scrape", denied)
        self.assertIn("cron", denied)
        self.assertNotIn("leads", denied)

    def test_watch_is_silent_until_a_strong_lead(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            self.assertFalse(profile_is_armed(store.load_profile()))
            self.assertIsNone(tick_watch(store))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "wizard_ready": True})
            self.assertTrue(profile_is_armed(store.load_profile()))
            empty = tick_watch(store)
            self.assertEqual(empty["count"], 0)
            self.assertEqual(heartbeat_prompt_note(empty), "")
            store.upsert_leads(
                [
                    {
                        "company": "Acme",
                        "email": "cto@acme.io",
                        "email_status": "verified",
                        "phone": "+33123456789",
                        "linkedin_url": "https://www.linkedin.com/in/x",
                        "person": "Jean Dupont",
                        "role": "CTO",
                        "country": "FR",
                        "sector": "SaaS",
                        "source": "hunter",
                    }
                ]
            )
            first = tick_watch(store)
            self.assertGreaterEqual(first["count"], 1)
            self.assertIn("Acme", first["digest"])
            self.assertIn("watch.count=", heartbeat_prompt_note(first))
            second = tick_watch(store)
            self.assertEqual(second["count"], 0)

    def test_heartbeat_refuses_hunt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            token = bind_request_context(
                RequestContext(channel="cli", chat_id="1", session_key="heartbeat", metadata={"heartbeat": True})
            )
            try:
                with patch("navin.webui.leads_api._store", return_value=store):
                    with self.assertRaises(LeadsError) as ctx:
                        handle_leads_action("hunt", {"icp_name": "SaaS", "sector": "SaaS"})
                    self.assertIn("heartbeat", ctx.exception.message)
                    snap = handle_leads_action("snapshot")
                    self.assertIn("kpis", snap)
            finally:
                reset_request_context(token)

    def test_agent_tool_status_reads_the_desk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "wizard_ready": True})
            tool = LeadsTool(workspace=tmp, config=LeadsToolConfig())
            with patch("navin.webui.leads_api._store", return_value=store):
                text = asyncio.run(tool.execute(action="status"))
            self.assertIn("Navin Leads", text)
            self.assertIn("SaaS FR", text)
            self.assertIn("Loop:", text)
            self.assertIn("do not create a chat cron", text)
            self.assertIn("navin leads", text)
            self.assertTrue(tool.call_read_only({"action": "status"}))
            self.assertFalse(tool.call_read_only({"action": "hunt"}))

    def test_heartbeat_desks_isolates_leads_failure(self) -> None:
        with patch("navin.tenders.heartbeat.tick_watch", return_value=None):
            with patch("navin.career.heartbeat.tick_watch", return_value=None):
                with patch("navin.leads.heartbeat.tick_watch", side_effect=RuntimeError("leads down")):
                    self.assertEqual(tick_heartbeat_desks(), "")

    def test_heartbeat_docs_name_the_leads_watch(self) -> None:
        from pathlib import Path as PathType

        root = PathType(__file__).resolve().parents[1]
        for rel in (".navin/HEARTBEAT.md", "navin/templates/HEARTBEAT.md"):
            body = (root / rel).read_text(encoding="utf-8")
            self.assertIn("leads action=watch", body)
            self.assertIn("Never hunt", body)
            self.assertIn("The Leads desk loop", body)
            self.assertIn("That is not this heartbeat", body)
            self.assertNotIn("\u2014", body)
            self.assertNotIn("\u2013", body)
        self.assertEqual(
            HEARTBEAT_LEADS_ACTIONS,
            {"status", "snapshot", "watch", "follow", "rescore", "score"},
        )

    def test_loop_seed_names_the_desk_tool(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for rel in (
            "webui/src/i18n/locales/en/common.json",
            "webui/src/i18n/locales/fr/common.json",
        ):
            data = json.loads((root / rel).read_text(encoding="utf-8"))
            seed = data["thread"]["sessionInfo"]["createSeed"]["leads"]
            self.assertIn("leads action=start", seed)
            self.assertIn("leads action=stop", seed)
            self.assertIn("leads action=schedule", seed)
            self.assertIn("leads action=watch", seed)
            self.assertIn("navin leads", seed)
            self.assertIn("desk_cli", seed)
            self.assertIn("Do not create a chat cron" if "en/" in rel else "Ne cree pas une cron de chat", seed)
            self.assertNotIn("Create a loop for this leads chat", seed)
            self.assertNotIn("\u2014", seed)
            self.assertNotIn("\u2013", seed)

    def test_tool_is_discoverable_and_enabled(self) -> None:
        from navin.agent.tools.loader import ToolLoader, clear_tool_discovery_cache

        clear_tool_discovery_cache()
        names = {cls.__name__ for cls in ToolLoader().discover()}
        self.assertIn("LeadsTool", names)
        self.assertTrue(LeadsToolConfig().enabled)

    def test_agent_tool_refuses_hunt_on_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "wizard_ready": True})
            tool = LeadsTool(workspace=tmp, config=LeadsToolConfig())
            token = bind_request_context(
                RequestContext(
                    channel="telegram",
                    chat_id="1",
                    session_key="heartbeat",
                    metadata={"heartbeat": True},
                )
            )
            try:
                with patch("navin.webui.leads_api._store", return_value=store):
                    hunt = asyncio.run(tool.execute(action="hunt", icp_name="SaaS"))
                    watch = asyncio.run(tool.execute(action="watch"))
            finally:
                reset_request_context(token)
            self.assertTrue(getattr(hunt, "is_error", False), hunt)
            self.assertIn("heartbeat", str(hunt).lower())
            self.assertFalse(getattr(watch, "is_error", False), watch)
            self.assertEqual(watch["watch"]["count"], 0)

    def test_loop_turn_can_hunt_heartbeat_cannot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "wizard_ready": True})
            tool = LeadsTool(workspace=tmp, config=LeadsToolConfig())
            loop_ctx = RequestContext(
                channel="webui",
                chat_id="leads-1",
                session_key="webui:leads-1",
                metadata={"product_module": "leads"},
            )
            hb_ctx = RequestContext(
                channel="telegram",
                chat_id="1",
                session_key="heartbeat",
                metadata={"heartbeat": True},
            )
            with patch("navin.webui.leads_api._store", return_value=store):
                with patch(
                    "navin.leads.desk.hunt_companies",
                    return_value=[{"company": "Acme", "source": "sirene"}],
                ):
                    with patch(
                        "navin.leads.desk.enrich_row",
                        side_effect=lambda row, *_: {**row, "score": 40, "tier": "C"},
                    ):
                        loop_token = bind_request_context(loop_ctx)
                        try:
                            loop_hunt = asyncio.run(tool.execute(action="hunt"))
                        finally:
                            reset_request_context(loop_token)
                hb_token = bind_request_context(hb_ctx)
                try:
                    hb_hunt = asyncio.run(tool.execute(action="hunt"))
                finally:
                    reset_request_context(hb_token)
            self.assertFalse(getattr(loop_hunt, "is_error", False), loop_hunt)
            self.assertTrue(getattr(hb_hunt, "is_error", False), hb_hunt)

    def test_desk_cli_watch_is_the_same_tick(self) -> None:
        from navin.leads.desk_cli import main

        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "wizard_ready": True})
            store.upsert_leads(
                [
                    {
                        "company": "Acme",
                        "email": "cto@acme.io",
                        "email_status": "verified",
                        "phone": "+33123456789",
                        "linkedin_url": "https://www.linkedin.com/in/x",
                        "person": "Jean Dupont",
                        "role": "CTO",
                        "country": "FR",
                        "sector": "SaaS",
                        "source": "hunter",
                    }
                ]
            )
            buf = io.StringIO()
            with (
                patch("navin.webui.leads_api._store", return_value=store),
                patch("sys.argv", ["navin.leads.desk_cli", "watch"]),
                patch("sys.stdin", io.StringIO("{}")),
                patch("sys.stdout", buf),
            ):
                code = main()
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertGreaterEqual(payload["watch"]["count"], 1)
            self.assertIn("Acme", payload["watch"]["digest"])
            self.assertEqual(tick_watch(store)["count"], 0)

    def test_heartbeat_desks_appends_leads_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "wizard_ready": True})
            store.upsert_leads(
                [
                    {
                        "company": "Acme",
                        "email": "cto@acme.io",
                        "email_status": "verified",
                        "phone": "+33123456789",
                        "linkedin_url": "https://www.linkedin.com/in/x",
                        "person": "Jean Dupont",
                        "role": "CTO",
                        "country": "FR",
                        "sector": "SaaS",
                        "source": "hunter",
                    }
                ]
            )
            with patch("navin.tenders.heartbeat.tick_watch", return_value=None):
                with patch("navin.career.heartbeat.tick_watch", return_value=None):
                    note = tick_heartbeat_desks(leads_store=store)
            self.assertIn("watch.count=", note)
            self.assertIn("Acme", note)
            with patch("navin.tenders.heartbeat.tick_watch", return_value=None):
                with patch("navin.career.heartbeat.tick_watch", return_value=None):
                    self.assertEqual(tick_heartbeat_desks(leads_store=store), "")

    def test_notify_crash_still_marks_so_the_next_tick_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "wizard_ready": True})
            store.upsert_leads(
                [
                    {
                        "company": "Acme",
                        "email": "cto@acme.io",
                        "email_status": "verified",
                        "phone": "+33123456789",
                        "linkedin_url": "https://www.linkedin.com/in/x",
                        "person": "Jean Dupont",
                        "role": "CTO",
                        "country": "FR",
                        "sector": "SaaS",
                        "source": "hunter",
                    }
                ]
            )
            with patch("navin.leads.notify.deliver_alert", side_effect=RuntimeError("bus down")):
                payload = tick_watch(store)
            self.assertGreaterEqual(payload["count"], 1)
            self.assertEqual(payload["sent"], {})
            self.assertEqual(tick_watch(store)["count"], 0)

    def test_web_search_skips_linkedin_and_directories(self) -> None:
        self.assertTrue(is_linkedin_url("https://www.linkedin.com/company/acme"))
        text = (
            "Results for: saas\n"
            "1. Acme on LinkedIn\n   https://www.linkedin.com/company/acme\n"
            "2. Acme Logistics\n   https://acme-logistics.fr/about\n"
            "3. Pappers fiche\n   https://www.pappers.fr/entreprise/acme\n"
        )
        hits = parse_search_hits(text)
        self.assertEqual([hit["url"] for hit in hits], [
            "https://www.linkedin.com/company/acme",
            "https://acme-logistics.fr/about",
            "https://www.pappers.fr/entreprise/acme",
        ])
        rows = hunt_web({"sector": "SaaS", "countries": ["FR"]}, 10, search_fn=lambda *_: text)
        self.assertEqual([row["domain"] for row in rows], ["acme-logistics.fr"])
        self.assertEqual(rows[0]["source"], "web")

    def test_extract_contacts_from_public_page(self) -> None:
        contacts = extract_contacts(
            "Write to cto@acme-logistics.fr or call +33 1 23 45 67 89",
            country="FR",
        )
        self.assertEqual(contacts["email"], "cto@acme-logistics.fr")
        self.assertTrue(contacts["phone"].startswith("+33"))

    def test_hunt_companies_uses_web_when_registries_are_empty(self) -> None:
        web_rows = [
            {
                "company": "Acme",
                "domain": "acme.io",
                "website": "https://acme.io",
                "source": "web",
            }
        ]
        with patch("navin.leads.waterfall.hunt_sirene", return_value=[]):
            with patch("navin.leads.discover.hunt_web", return_value=web_rows) as web:
                found = hunt_companies({"countries": ["US"], "sector": "SaaS"}, {}, 10)
        web.assert_called_once()
        self.assertEqual(found[0]["source"], "web")

    def test_hunt_companies_merges_sources_and_ranks_hiring_first(self) -> None:
        profile = {"countries": ["FR"], "sector": "logistique", "signals": ["responsable supply chain"]}
        sirene = [{"company": "Acme Logistique", "country": "FR", "source": "sirene", "extra": {"siren": "1"}}]
        hiring = [{"company": "Acme Logistique", "country": "FR", "source": "hiring", "signal": "hiring: responsable supply chain"}]
        web = [{"company": "Acme Logistique", "domain": "acme-logistique.fr", "website": "https://acme-logistique.fr", "country": "FR", "source": "web", "signal": "web: logistique Lyon"}]
        osm = [{"company": "Transports Durand", "domain": "durand.fr", "country": "FR", "source": "osm", "phone": "+33100000000"}]
        with patch("navin.leads.waterfall.hunt_sirene", return_value=sirene):
            with patch("navin.leads.discover.hunt_hiring", return_value=hiring):
                with patch("navin.leads.discover.hunt_web", return_value=web):
                    with patch("navin.leads.discover.hunt_osm", return_value=osm):
                        found = hunt_companies(profile, {}, 10, cursor=3)
        self.assertEqual([row["company"] for row in found], ["Acme Logistique", "Transports Durand"])
        merged = found[0]
        # The registry row came first, the hiring and web rows filled it in.
        self.assertEqual(merged["source"], "sirene")
        self.assertEqual(merged["domain"], "acme-logistique.fr")
        self.assertIn("hiring: responsable supply chain", merged["signal"])
        self.assertIn("web: logistique Lyon", merged["signal"])
        self.assertEqual(merged["extra"]["siren"], "1")

    def test_hunt_companies_respects_profile_sources(self) -> None:
        profile = {"countries": ["US"], "sector": "SaaS", "sources": ["web"]}
        with patch("navin.leads.waterfall.hunt_sirene", return_value=[]):
            with patch("navin.leads.discover.hunt_hiring", return_value=[]) as hiring:
                with patch("navin.leads.discover.hunt_osm", return_value=[]) as osm:
                    with patch("navin.leads.discover.hunt_web", return_value=[]) as web:
                        hunt_companies(profile, {}, 10)
        hiring.assert_not_called()
        osm.assert_not_called()
        web.assert_called_once()

    def test_opencorporates_and_crunchbase_parse(self) -> None:
        oc = {
            "results": {
                "companies": [
                    {
                        "company": {
                            "name": "ACME LTD",
                            "company_number": "123",
                            "jurisdiction_code": "gb",
                            "registered_address_in_full": "London",
                        }
                    }
                ]
            }
        }
        cb = {
            "entities": [
                {
                    "identifier": {"value": "Acme", "permalink": "acme"},
                    "short_description": "funding",
                }
            ]
        }
        with patch("navin.leads.waterfall._get_json", return_value=oc):
            rows = hunt_opencorporates({"sector": "SaaS", "countries": ["GB"]}, "oc-key", 5)
        self.assertEqual(rows[0]["company"], "ACME LTD")
        self.assertEqual(rows[0]["extra"]["company_number"], "123")
        with patch("navin.leads.waterfall._get_json", return_value=cb):
            rows = hunt_crunchbase({"sector": "SaaS", "countries": ["US"]}, "cb-key", 5)
        self.assertEqual(rows[0]["extra"]["permalink"], "acme")
        with patch(
            "navin.leads.waterfall._get_json",
            return_value={"properties": {"website": {"value": "https://acme.io"}, "funding_total": {"value_usd": 10}}},
        ):
            filled = enrich_crunchbase("acme", "cb-key")
        self.assertEqual(filled["domain"], "acme.io")

    def test_hunter_verify_upgrades_email_status(self) -> None:
        with patch(
            "navin.leads.waterfall._get_json",
            return_value={"data": {"status": "valid"}},
        ):
            filled = enrich_hunter_verify("cto@acme.io", "hunter-key")
        self.assertEqual(filled["email_status"], "verified")
        row = apply_fields(
            {"email": "cto@acme.io", "email_status": "unverified"},
            {"email_status": "verified"},
        )
        self.assertEqual(row["email_status"], "verified")

    def test_places_details_and_iso_country(self) -> None:
        payload = {
            "results": [
                {
                    "name": "Acme Clinic",
                    "formatted_address": "London",
                    "place_id": "Ch123",
                }
            ]
        }
        with patch("navin.leads.waterfall._get_json", return_value=payload):
            rows = hunt_places({"sector": "clinic", "countries": ["GB"]}, "places-key", 5)
        self.assertEqual(rows[0]["country"], "GB")
        self.assertEqual(rows[0]["extra"]["place_id"], "Ch123")
        with patch(
            "navin.leads.waterfall._get_json",
            return_value={
                "result": {
                    "website": "https://acme.clinic",
                    "international_phone_number": "+44 20 1234",
                }
            },
        ):
            details = enrich_places_details("Ch123", "places-key")
        self.assertEqual(details["domain"], "acme.clinic")

    def test_bulk_enrich_skips_scrape_deep_calls_it(self) -> None:
        row = {"company": "Acme", "domain": "acme.io", "website": "https://acme.io"}
        profile = {"countries": ["FR"], "titles": ["CTO"]}
        with patch("navin.leads.scrape_net.enrich_site", return_value={"email": "a@acme.io"}) as scrape:
            bulk = enrich_row(row, profile, {}, deep=False)
            deep = enrich_row(row, profile, {}, deep=True)
        scrape.assert_called_once()
        self.assertFalse(bulk.get("email"))
        self.assertEqual(deep.get("email"), "a@acme.io")

    def test_sirene_uses_siege_when_domaine_is_absent(self) -> None:
        payload = {
            "results": [
                {
                    "nom_complet": "Datadog France",
                    "siren": "2",
                    "section_activite_principale": "J",
                    "siege": {"geo_adresse": "Paris", "siret": "2"},
                }
            ]
        }
        with patch("navin.leads.waterfall._get_json", return_value=payload):
            rows = hunt_sirene({"sector": "SaaS"}, 10)
        self.assertEqual(rows[0]["company"], "Datadog France")
        self.assertEqual(rows[0]["domain"], "")
        self.assertEqual(rows[0]["extra"]["siren"], "2")
        self.assertIn("Paris", rows[0]["signal"])

    def test_profile_keeps_alert_channels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile(
                {
                    "icp_name": "SaaS FR",
                    "sector": "SaaS",
                    "channels": {"telegram": True, "telegram_to": "4242"},
                }
            )
            profile = store.load_profile()
            self.assertTrue(profile["channels"]["telegram"])
            self.assertEqual(profile["channels"]["telegram_to"], "4242")
            self.assertFalse(profile["channels"]["email"])

    def test_outreach_email_sends_and_marks_contacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            rows, _ = store.upsert_leads(
                [{"company": "Acme", "email": "cto@acme.io", "person": "Jean"}]
            )
            lead_id = rows[0]["id"]
            with patch("navin.leads.notify.send_channel", return_value=True) as send:
                payload = outreach(
                    store,
                    lead_id,
                    channel="email",
                    subject="Hello",
                    body="Hi Jean",
                    send=True,
                )
            send.assert_called_once_with("email", "cto@acme.io", subject="Hello", body="Hi Jean")
            self.assertTrue(payload["outreach"]["sent"])
            self.assertEqual(store.get_lead(lead_id)["stage"], "contacted")

    def test_outreach_whatsapp_uses_the_bus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            rows, _ = store.upsert_leads(
                [{"company": "Acme", "phone": "+33123456789"}]
            )
            with patch("navin.leads.notify._put_outbound", return_value=True) as outbound:
                payload = outreach(
                    store,
                    rows[0]["id"],
                    channel="whatsapp",
                    body="Hello",
                    send=True,
                )
            outbound.assert_called_once()
            self.assertEqual(outbound.call_args.args[0], "whatsapp")
            self.assertEqual(outbound.call_args.args[1], "+33123456789")
            self.assertTrue(payload["outreach"]["sent"])

    def test_outreach_draft_does_not_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            rows, _ = store.upsert_leads([{"company": "Acme", "email": "a@acme.io"}])
            with patch("navin.leads.notify.send_channel") as send:
                payload = outreach(store, rows[0]["id"], channel="email", send=False)
            send.assert_not_called()
            self.assertTrue(payload["outreach"]["prepared"])
            self.assertEqual(store.get_lead(rows[0]["id"])["stage"], "new")

    def test_heartbeat_refuses_outreach(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            token = bind_request_context(
                RequestContext(
                    channel="cli",
                    chat_id="1",
                    session_key="heartbeat",
                    metadata={"heartbeat": True},
                )
            )
            try:
                with patch("navin.webui.leads_api._store", return_value=store):
                    with self.assertRaises(LeadsError) as ctx:
                        handle_leads_action("outreach", {"id": "ld-1", "channel": "email", "send": True})
                    self.assertIn("heartbeat", ctx.exception.message)
            finally:
                reset_request_context(token)

    def test_deliver_alert_fans_out_enabled_channels(self) -> None:
        from navin.leads.notify import deliver_alert

        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile(
                {
                    "icp_name": "SaaS",
                    "sector": "SaaS",
                    "channels": {
                        "telegram": True,
                        "telegram_to": "99",
                        "email": True,
                        "email_to": "me@navin.live",
                    },
                }
            )
            with (
                patch("navin.leads.notify.notify", return_value=True),
                patch("navin.leads.notify._put_outbound", return_value=True) as outbound,
                patch("navin.leads.notify._send_email", return_value=True) as mail,
            ):
                sent = deliver_alert(store, title="1 lead alert", detail="Lead: Acme")
            outbound.assert_called_once()
            self.assertEqual(outbound.call_args.args[0], "telegram")
            mail.assert_called_once()
            self.assertTrue(sent["telegram"])
            self.assertTrue(sent["email"])
            self.assertFalse(sent["whatsapp"])

    def test_qualify_writes_bant_why_and_next_action(self) -> None:
        from navin.leads.qualify import apply_qualification, qualify

        profile = {"countries": ["FR"], "sector": "logistique", "titles": ["CTO"]}
        strong = qualify(
            {
                "email": "cto@acme.io",
                "email_status": "verified",
                "phone": "+33123456789",
                "linkedin_url": "https://www.linkedin.com/in/x",
                "person": "Jean Dupont",
                "role": "CTO",
                "country": "FR",
                "sector": "logistique",
            },
            profile,
        )
        self.assertGreaterEqual(strong["score"], 80)
        self.assertEqual(strong["tier"], "A")
        self.assertEqual(strong["next_action"], "contact now")
        self.assertGreater(strong["bant"]["authority"], 0)
        self.assertTrue(strong["why"])
        row = apply_qualification({"company": "Acme", "country": "DE"}, {"countries": ["FR"]})
        self.assertIn("country DE outside ICP", row["disqualify"])
        self.assertEqual(row["tier"], "C")

    def test_sequence_due_steps_and_mark_sent(self) -> None:
        from navin.leads.sequence import due_steps, mark_step_sent, start_sequence

        now = 1_700_000_000.0
        seq = start_sequence({"company": "Acme"}, now=now)
        self.assertEqual(seq["steps"][0]["status"], "due")
        self.assertEqual(len(due_steps({"sequence": seq}, now=now)), 1)
        later = due_steps({"sequence": seq}, now=now + 3 * 86400)
        self.assertEqual(len(later), 1)
        self.assertEqual(later[0]["n"], 1)
        sent = mark_step_sent({"sequence": seq}, channel="email", now=now)
        self.assertEqual(sent["sequence"]["steps"][0]["status"], "sent")
        self.assertEqual(len(due_steps(sent, now=now)), 0)
        day3 = due_steps(sent, now=now + 3 * 86400)
        self.assertEqual(len(day3), 1)
        self.assertEqual(day3[0]["n"], 2)

    def test_watch_alerts_due_follow_up_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "wizard_ready": True})
            rows, _ = store.upsert_leads([{"company": "Acme", "country": "US"}])
            sequence_start(store, rows[0]["id"])
            with patch("navin.leads.notify.deliver_alert", return_value={"telegram": True}) as notify:
                payload = tick_watch(store)
            notify.assert_called_once()
            self.assertGreaterEqual(payload["count"], 1)
            self.assertIn("Follow-up", payload["digest"])
            self.assertEqual(store.get_lead(rows[0]["id"])["stage"], "new")
            self.assertEqual(tick_watch(store)["count"], 0)

    def test_watch_alerts_buying_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "wizard_ready": True})
            store.upsert_leads(
                [
                    {
                        "company": "Acme",
                        "country": "US",
                        "signal": "Series B funding",
                    }
                ]
            )
            payload = tick_watch(store)
            self.assertGreaterEqual(payload["count"], 1)
            self.assertIn("Signal funding", payload["digest"])

    def test_outreach_advances_the_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            rows, _ = store.upsert_leads([{"company": "Acme", "email": "cto@acme.io"}])
            lead_id = rows[0]["id"]
            sequence_start(store, lead_id)
            with patch("navin.leads.notify.send_channel", return_value=True):
                outreach(store, lead_id, channel="email", subject="Hi", body="Hello", send=True)
            steps = store.get_lead(lead_id)["sequence"]["steps"]
            self.assertEqual(steps[0]["status"], "sent")

    def test_lookalike_upserts_peers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            rows, _ = store.upsert_leads(
                [{"company": "Acme", "sector": "SaaS", "country": "FR"}]
            )
            peers = [{"company": "Beta", "sector": "SaaS", "country": "FR", "source": "web"}]
            with patch("navin.leads.desk.hunt_companies", return_value=peers):
                with patch(
                    "navin.leads.desk.enrich_row",
                    side_effect=lambda row, *_: {**row, "score": 40, "tier": "C"},
                ):
                    payload = lookalike(store, rows[0]["id"])
            self.assertEqual(payload["lookalike"]["added"], 1)
            self.assertEqual(payload["lookalike"]["found"], 1)

    def test_rescore_writes_kpis_due_and_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "countries": ["FR"]})
            rows, _ = store.upsert_leads(
                [
                    {
                        "company": "Acme",
                        "email": "cto@acme.io",
                        "email_status": "verified",
                        "phone": "+33123456789",
                        "linkedin_url": "https://www.linkedin.com/in/x",
                        "person": "Jean Dupont",
                        "role": "CTO",
                        "country": "FR",
                        "sector": "SaaS",
                    }
                ]
            )
            sequence_start(store, rows[0]["id"])
            snap = rescore(store)
            self.assertGreaterEqual(snap["kpis"]["strong"], 1)
            self.assertGreaterEqual(snap["kpis"]["due"], 1)
            self.assertTrue(snap["kpis"]["queue"])
            self.assertEqual(store.get_lead(rows[0]["id"])["next_action"], "contact now")

    def test_heartbeat_refuses_sequence_and_lookalike(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            token = bind_request_context(
                RequestContext(
                    channel="cli",
                    chat_id="1",
                    session_key="heartbeat",
                    metadata={"heartbeat": True},
                )
            )
            try:
                with patch("navin.webui.leads_api._store", return_value=store):
                    with self.assertRaises(LeadsError) as seq:
                        handle_leads_action("sequence", {"id": "ld-1"})
                    with self.assertRaises(LeadsError) as alike:
                        handle_leads_action("lookalike", {"id": "ld-1"})
                    self.assertIn("heartbeat", seq.exception.message)
                    self.assertIn("heartbeat", alike.exception.message)
            finally:
                reset_request_context(token)

    def test_public_body_filter_covers_chu_and_hopital(self) -> None:
        self.assertTrue(is_public_body("COMMUNE DE RIVIERE SAAS ET GOURBY"))
        self.assertTrue(is_public_body("CHU de Bordeaux"))
        self.assertTrue(is_public_body("Hopital Saint-Louis"))
        self.assertTrue(is_public_body("Ville de Paris"))
        self.assertTrue(is_public_body("ASSOCIATION DES AMIS DU SAAS"))
        self.assertTrue(
            is_public_body(
                "GROUPE D'ETUDE POUR L'INCLUSION SOCIALE POUR TOUS EN MAYENNE (GEIST MAYENNE)"
            )
        )
        self.assertFalse(is_public_body("Datadog France"))
        self.assertFalse(is_public_body("SAAS OFFSHORE"))

    def test_merge_lead_replaces_empty_why(self) -> None:
        merged = merge_lead(
            {"id": "ld-1", "company": "Acme", "why": ["old"], "signals": [{"kind": "funding"}]},
            {"why": [], "signals": []},
        )
        self.assertEqual(merged["why"], [])
        self.assertEqual(merged["signals"], [])

    def test_whatsapp_outreach_advances_email_sequence(self) -> None:
        from navin.leads.sequence import mark_step_sent, start_sequence

        now = 1_700_000_000.0
        row = {"company": "Acme", "sequence": start_sequence({}, now=now)}
        sent = mark_step_sent(row, channel="whatsapp", now=now)
        self.assertEqual(sent["sequence"]["steps"][0]["status"], "sent")
        self.assertEqual(sent["sequence"]["steps"][0]["sent_channel"], "whatsapp")
        self.assertEqual(mark_step_sent({"company": "Acme"}, channel="email").get("sequence"), None)

    def test_rescore_archives_public_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.save_profile({"icp_name": "SaaS FR", "sector": "SaaS", "countries": ["FR"]})
            rows, _ = store.upsert_leads(
                [
                    {"company": "COMMUNE DE RIVIERE SAAS ET GOURBY", "country": "FR"},
                    {"company": "Datadog France", "country": "FR", "sector": "SaaS"},
                ]
            )
            snap = rescore(store)
            live_ids = {row["id"] for row in snap["leads"] if not row.get("archived")}
            archived = [row for row in store.load_leads() if row.get("archived")]
            self.assertEqual(len(archived), 1)
            self.assertIn("COMMUNE", archived[0]["company"])
            self.assertEqual(snap["kpis"]["total"], 1)
            self.assertTrue(any(row["company"] == "Datadog France" for row in snap["leads"] if row["id"] in live_ids or not row.get("archived")))

    def test_bulk_hunt_does_not_call_paid_enrich(self) -> None:
        row = {"company": "Acme", "domain": "acme.io", "website": "https://acme.io"}
        with patch("navin.leads.waterfall.enrich_hunter_domain") as hunter:
            enrich_row(row, {"countries": ["FR"], "titles": ["CTO"]}, {"hunter": "k"}, deep=False)
        hunter.assert_not_called()
        with patch("navin.leads.waterfall.enrich_hunter_domain", return_value={"email": "a@acme.io"}) as hunter:
            deep = enrich_row(row, {"countries": ["FR"], "titles": ["CTO"]}, {"hunter": "k"}, deep=True)
        hunter.assert_called_once()
        self.assertEqual(deep.get("email"), "a@acme.io")

    def test_tranche_code_is_not_twelve_employees(self) -> None:
        from navin.leads.qualify import _size_int

        self.assertEqual(_size_int("12"), 35)
        self.assertEqual(_size_int("20-49"), 34)
        self.assertEqual(_size_int("150"), 150)
        from navin.leads.qualify import apply_qualification, size_display

        self.assertEqual(size_display("21"), "50-99")
        self.assertEqual(size_display("20-49"), "20-49")
        labeled = apply_qualification({"company": "Acme", "size": "21", "country": "FR"}, {"countries": ["FR"]})
        self.assertEqual(labeled["size"], "50-99")

    def test_archived_lead_refuses_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            rows, _ = store.upsert_leads(
                [{"company": "Acme", "email": "a@acme.io", "country": "FR"}]
            )
            lead_id = rows[0]["id"]
            store.patch_lead(lead_id, {"archived": True})
            with self.assertRaises(LeadsError) as seq:
                sequence_start(store, lead_id)
            self.assertEqual(seq.exception.status, 409)
            with self.assertRaises(LeadsError) as alike:
                lookalike(store, lead_id)
            self.assertEqual(alike.exception.status, 409)
            with self.assertRaises(LeadsError) as stage:
                set_stage(store, lead_id, "contacted")
            self.assertEqual(stage.exception.status, 409)
            with self.assertRaises(LeadsError) as send:
                outreach(store, lead_id, channel="email", send=True)
            self.assertEqual(send.exception.status, 409)

    def test_agent_status_skips_archived_leads(self) -> None:
        text = format_agent_status(
            {
                "profile": {"icp_name": "SaaS FR", "sector": "SaaS", "countries": ["FR"]},
                "kpis": {"headline": "1 live"},
                "leads": [
                    {"company": "COMMUNE DE TEST", "archived": True, "score": 10, "stage": "new"},
                    {"company": "Acme", "score": 80, "tier": "A", "stage": "new"},
                ],
            }
        )
        self.assertIn("Acme", text)
        self.assertNotIn("COMMUNE", text)

    def test_outreach_telegram_without_dest_is_422(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            rows, _ = store.upsert_leads([{"company": "Acme"}])
            with self.assertRaises(LeadsError) as ctx:
                outreach(store, rows[0]["id"], channel="telegram", send=True)
            self.assertEqual(ctx.exception.status, 422)

    def test_hunt_companies_drops_public_bodies_from_any_source(self) -> None:
        with patch("navin.leads.waterfall.hunt_sirene", return_value=[]):
            with patch(
                "navin.leads.discover.hunt_web",
                return_value=[
                    {"company": "COMMUNE DE RIVIERE", "source": "web"},
                    {"company": "Acme", "source": "web"},
                ],
            ):
                found = hunt_companies({"countries": ["US"], "sector": "SaaS"}, {}, 10)
        self.assertEqual([row["company"] for row in found], ["Acme"])

    def test_sirene_maps_tranche_code_to_range(self) -> None:
        with patch("navin.leads.waterfall._get_json", return_value=SIRENE_PAYLOAD):
            rows = hunt_sirene({"sector": "logistique", "size_min": 50, "size_max": 500}, 10)
        self.assertEqual(rows[0]["size"], "100-199")

    def test_http_headers_identify_navin(self) -> None:
        headers = _http_headers({"X-Api-Key": "k"})
        self.assertEqual(headers["User-Agent"], USER_AGENT)
        self.assertIn("NavinLeads", headers["User-Agent"])
        self.assertEqual(headers["X-Api-Key"], "k")

    def test_sirene_reads_dirigeants_and_skips_closed_or_asso(self) -> None:
        payload = {
            "results": [
                {
                    "nom_complet": "Closed SaaS",
                    "siren": "1",
                    "etat_administratif": "C",
                    "dirigeants": [{"prenoms": "A", "nom": "B", "qualite": "PDG"}],
                },
                {
                    "nom_complet": "Asso SaaS",
                    "siren": "2",
                    "complements": {"est_association": True},
                },
                {
                    "nom_complet": "Datadog France",
                    "siren": "3",
                    "etat_administratif": "A",
                    "tranche_effectif_salarie": "22",
                    "dirigeants": [
                        {
                            "prenoms": "KERRY SHANNON",
                            "nom": "ACOCELLA",
                            "qualite": "Directeur General",
                            "type_dirigeant": "personne physique",
                        }
                    ],
                },
            ]
        }
        with patch("navin.leads.waterfall._get_json", return_value=payload):
            rows = hunt_sirene({"sector": "SaaS", "size_min": 20, "size_max": 200}, 10)
        self.assertEqual([row["company"] for row in rows], ["Datadog France"])
        self.assertEqual(rows[0]["person"], "KERRY SHANNON ACOCELLA")
        self.assertEqual(rows[0]["role"], "Directeur General")

    def test_places_denied_returns_empty(self) -> None:
        with patch("navin.leads.waterfall._get_json", return_value={"status": "REQUEST_DENIED", "results": []}):
            self.assertEqual(hunt_places({"sector": "clinic", "countries": ["GB"]}, "bad-key", 5), [])

    def test_probe_and_catalog_cover_every_listed_system(self) -> None:
        ids = {row["id"] for row in catalog()}
        self.assertEqual(
            ids,
            {
                "web",
                "sirene",
                "pappers",
                "companies_house",
                "places",
                "apollo",
                "pdl",
                "hunter",
                "crunchbase",
                "opencorporates",
                "linkedin",
            },
        )
        with patch(
            "navin.leads.waterfall._get_json",
            return_value={"results": [{"nom_complet": "LA POSTE"}]},
        ):
            ping = probe_sirene()
        self.assertTrue(ping["live"])
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            with patch("navin.leads.waterfall._get_json", return_value={"results": [{"nom_complet": "LA POSTE"}]}):
                snap = snapshot(store, probe=True)
        by_id = {row["id"]: row for row in snap["providers"]}
        self.assertTrue(all(row.get("wired") for row in snap["providers"]))
        self.assertTrue(by_id["sirene"]["live"])
        self.assertTrue(by_id["web"]["configured"])
        self.assertTrue(by_id["linkedin"]["configured"])
        self.assertTrue(by_id["apollo"]["needs_key"])

    def test_kpis_count_a_sequence_as_outreach(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.upsert_leads(
                [
                    {
                        "id": "ld-seq",
                        "company": "Acme",
                        "stage": "qualified",
                        "sequence": {"steps": [{"n": 1, "status": "due"}]},
                    },
                    {"id": "ld-new", "company": "Beta", "stage": "new"},
                ]
            )
            snap = snapshot(store)
            self.assertEqual(snap["kpis"]["pipeline"], 1)
            self.assertEqual(snap["kpis"]["total"], 2)

    def test_push_crm_uses_project_and_keeps_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.upsert_leads(
                [
                    {
                        "id": "ld-crm",
                        "company": "Acme",
                        "stage": "contacted",
                        "person": "Ada",
                        "email": "ada@acme.io",
                    }
                ]
            )
            project = Path(tmp) / "acme-app"
            project.mkdir()
            payload = push_crm(store, "ld-crm", project=project)
            self.assertTrue(payload["crm"]["id"])
            row = next(item for item in store.load_leads() if item["id"] == "ld-crm")
            self.assertEqual(row["stage"], "contacted")
            self.assertEqual(row["crm_lead_id"], payload["crm"]["id"])

    def test_push_crm_refuses_navinprojects_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.upsert_leads([{"id": "ld-crm", "company": "Acme", "stage": "new"}])
            root = Path(tmp) / "NavinProjects"
            root.mkdir()
            with self.assertRaises(LeadsError) as ctx:
                push_crm(store, "ld-crm", project=root)
            self.assertEqual(ctx.exception.status, 400)
            self.assertIn("folder of projects", str(ctx.exception).lower())

    def test_push_crm_works_when_project_already_has_an_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.upsert_leads(
                [
                    {
                        "id": "ld-crm",
                        "company": "Acme",
                        "stage": "contacted",
                        "person": "Ada",
                    }
                ]
            )
            project = Path(tmp) / "acme-app"
            project.mkdir()
            from navin.crm.store import ensure_owner

            ensure_owner(project, "owner@company.com")
            payload = push_crm(store, "ld-crm", project=project)
            self.assertTrue(payload["crm"]["id"])
            row = next(item for item in store.load_leads() if item["id"] == "ld-crm")
            self.assertEqual(row["stage"], "contacted")
            self.assertEqual(row["crm_lead_id"], payload["crm"]["id"])

    def test_delete_lead_drops_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            store.upsert_leads(
                [
                    {"id": "ld-keep", "company": "Keep Co", "stage": "new"},
                    {"id": "ld-gone", "company": "Gone Co", "stage": "new"},
                ]
            )
            with patch("navin.webui.leads_api._store", return_value=store):
                payload = handle_leads_action("delete", {"id": "ld-gone"})
            ids = {str(row.get("id") or "") for row in payload["leads"]}
            self.assertEqual(payload["deleted"]["id"], "ld-gone")
            self.assertIn("ld-keep", ids)
            self.assertNotIn("ld-gone", ids)
            with patch("navin.webui.leads_api._store", return_value=store):
                with self.assertRaises(LeadsError) as ctx:
                    handle_leads_action("remove", {"id": "ld-gone"})
            self.assertEqual(ctx.exception.status, 404)
