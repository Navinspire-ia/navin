# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Leads chain end to end, offline: discover -> fill -> qualify -> sequence -> loop -> API.

Every network edge is injected (search_fn, http_get, jobs_fn, resolver, send_fn).
The NAVIN_LEADS_OFFLINE flag from conftest guarantees the defaults stay silent.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.leads import discover
from navin.leads.discover import (
    bulk_enrich,
    company_from_domain,
    guess_email,
    hunt_hiring,
    hunt_osm,
    hunt_web,
    mine_listicle,
    osm_queries,
    parse_osm_results,
    web_queries,
)
from navin.leads.draft import ai_draft, draft_sequence, template_draft
from navin.leads.errors import LeadsError
from navin.leads.loop import maybe_tick, start_loop
from navin.leads.pipeline import auto_enroll, run_hunt, run_sequences
from navin.leads.qualify import qualify
from navin.leads.scrape_net import domain_matches_company, lookup_domain
from navin.leads.sequence import due_steps, start_sequence
from navin.leads.store import LeadsStore, normalize_profile
from navin.leads.watch import pending_alerts
from navin.leads.waterfall import hunt_companies
from navin.webui.leads_api import handle_leads_action

SEARCH_TEXT = """Results:
1. Top 20 agences web a Lyon en 2026 - Classement
   https://blog.example.org/top-20-agences-web-lyon
2. Agence Lumiere - Agence web Lyon
   https://www.agence-lumiere.fr/
3. Agences web Lyon | Sortlist
   https://www.sortlist.fr/agences-web/lyon
4. Jean Dupont - Directeur | LinkedIn
   https://fr.linkedin.com/in/jeandupont
"""

LISTICLE_HTML = """<html><body>
<h1>Top 20 agences web a Lyon</h1>
<ol>
<li><a href="https://www.studio-b.fr/">Studio B</a> - creation de sites</li>
<li><a href="https://agence-lumiere.fr/agence">Agence Lumiere</a></li>
<li><a href="https://go.example.org/out?url=https://www.pixel-park.fr/">Site web</a></li>
<li><a href="https://www.linkedin.com/company/studio-b">LinkedIn</a></li>
<li><a href="https://www.sortlist.fr/agency/studio-b">Sortlist</a></li>
<li><a href="/contact">Nous contacter</a></li>
<li><a href="https://blog.example.org/article/agence-web-tendances-2026">Tendances</a></li>
</ol>
</body></html>"""

OSM_PAYLOAD = [
    {
        "osm_id": 1,
        "osm_type": "way",
        "name": "Atelier Numerique",
        "class": "office",
        "type": "company",
        "address": {"city": "Lyon"},
        "extratags": {
            "website": "https://www.atelier-numerique.fr",
            "contact:email": "hello@atelier-numerique.fr",
            "phone": "04 72 00 00 00",
        },
    },
    {
        "osm_id": 2,
        "osm_type": "node",
        "name": "Maison",
        "class": "building",
        "type": "house",
        "address": {},
    },
    {
        "osm_id": 3,
        "osm_type": "node",
        "name": "Lycee",
        "class": "amenity",
        "type": "school",
        "address": {},
    },
]

JOBS_PAYLOAD = {
    "jobs": [
        {
            "company": "Cargo Express",
            "title": "Responsable supply chain",
            "location": "Lyon, France",
            "country": "FR",
            "url": "https://fr.linkedin.com/jobs/view/1",
        },
        {
            "company": "Cargo Express",
            "title": "Chef de projet logistique",
            "location": "Paris",
            "country": "FR",
            "url": "https://fr.linkedin.com/jobs/view/2",
        },
        {
            "company": "Foreign Corp",
            "title": "Supply chain lead",
            "location": "Berlin",
            "country": "DE",
            "url": "https://de.linkedin.com/jobs/view/3",
        },
    ]
}


def _search(query: str, count: int = 6) -> str:
    return SEARCH_TEXT


def _http_get(url: str) -> tuple[int, str]:
    if "top-20-agences" in url:
        return 200, LISTICLE_HTML
    if "nominatim" in url:
        return 200, json.dumps(OSM_PAYLOAD)
    return 404, ""


def _jobs(**kwargs: object) -> dict[str, object]:
    return JOBS_PAYLOAD


def _no_search(query: str, count: int = 6) -> str:
    return f"No results for: {query}"


class DiscoverTest(unittest.TestCase):
    def test_web_queries_are_localized_and_rotate(self) -> None:
        profile = {"sector": "agence web", "countries": ["FR", "GB"], "keywords": ["shopify"]}
        first = web_queries(profile, cursor=0)
        second = web_queries(profile, cursor=1)
        countries = {country for country, _ in first}
        self.assertEqual(countries, {"FR", "GB"})
        fr = [query for country, query in first if country == "FR"]
        gb = [query for country, query in first if country == "GB"]
        self.assertTrue(any("Paris" in query for query in fr))
        self.assertTrue(any("London" in query for query in gb))
        self.assertTrue(any("shopify" in query for query in fr))
        self.assertNotEqual([q for _, q in first], [q for _, q in second])
        self.assertFalse(web_queries({"countries": ["FR"]}))

    def test_profile_cities_override_the_defaults(self) -> None:
        rows = web_queries(
            {"sector": "cabinet comptable", "countries": ["FR"], "cities": ["Annecy"]}
        )
        self.assertTrue(all("Annecy" in query or "France" in query for _, query in rows))
        self.assertTrue(any("Annecy" in query for _, query in rows))

    def test_mine_listicle_keeps_company_sites_only(self) -> None:
        rows = mine_listicle(LISTICLE_HTML, "https://blog.example.org/top-20-agences-web-lyon")
        domains = [row["domain"] for row in rows]
        self.assertEqual(domains, ["studio-b.fr", "agence-lumiere.fr", "pixel-park.fr"])
        self.assertEqual(rows[0]["company"], "Studio B")
        # Generic anchor text falls back to a name derived from the domain.
        self.assertEqual(rows[2]["company"], "Pixel Park")
        self.assertEqual(company_from_domain("www.acme-io.co.uk"), "Acme Io")

    def test_hunt_web_mines_listicles_and_skips_linkedin_and_directories(self) -> None:
        rows = hunt_web(
            {"sector": "agence web", "countries": ["FR"]}, 10, search_fn=_search, http_get=_http_get
        )
        companies = {row["company"] for row in rows}
        self.assertIn("Studio B", companies)
        self.assertIn("Agence Lumiere", companies)
        self.assertNotIn("Jean Dupont", " ".join(companies))
        self.assertTrue(all("sortlist" not in row["domain"] for row in rows))
        self.assertTrue(all(row["source"] == "web" for row in rows))
        listed = [row for row in rows if row["signal"].startswith("listed on")]
        self.assertTrue(listed)
        self.assertEqual(len({row["domain"] for row in rows}), len(rows))

    def test_hunt_web_respects_limit_and_empty_profile(self) -> None:
        self.assertEqual(
            hunt_web({"countries": ["FR"]}, 10, search_fn=_search, http_get=_http_get), []
        )
        rows = hunt_web(
            {"sector": "agence web", "countries": ["FR"]}, 2, search_fn=_search, http_get=_http_get
        )
        self.assertEqual(len(rows), 2)

    def test_osm_parse_keeps_businesses_and_drops_houses(self) -> None:
        rows = parse_osm_results(OSM_PAYLOAD, country="FR", city="Lyon", sector="agence web")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["company"], "Atelier Numerique")
        self.assertEqual(row["domain"], "atelier-numerique.fr")
        self.assertEqual(row["email"], "hello@atelier-numerique.fr")
        self.assertTrue(row["phone"].startswith("+33"))
        self.assertEqual(row["source"], "osm")
        self.assertEqual(row["extra"]["city"], "Lyon")

    def test_hunt_osm_queries_one_city_per_step_with_pacing(self) -> None:
        naps: list[float] = []
        rows = hunt_osm(
            {"sector": "agence web", "countries": ["FR"]}, 10, http_get=_http_get, sleep=naps.append
        )
        self.assertEqual(len(rows), 1)  # same business in every city collapses
        self.assertEqual(len(osm_queries({"sector": "x", "countries": ["FR"]})), 3)
        self.assertEqual(len(naps), 2)
        self.assertTrue(all(nap >= 1.0 for nap in naps))

    def test_hunt_hiring_groups_by_company_and_keeps_icp_countries(self) -> None:
        rows = hunt_hiring(
            {"sector": "logistique", "countries": ["FR"], "signals": ["responsable supply chain"]},
            10,
            jobs_fn=_jobs,
        )
        self.assertEqual([row["company"] for row in rows], ["Cargo Express"])
        self.assertEqual(rows[0]["source"], "hiring")
        self.assertTrue(rows[0]["signal"].startswith("hiring: Responsable supply chain"))
        self.assertEqual(rows[0]["extra"]["job_url"], "https://fr.linkedin.com/jobs/view/1")
        self.assertEqual(hunt_hiring({"countries": ["FR"]}, 10, jobs_fn=_jobs), [])

    def test_hiring_default_stays_offline_in_tests(self) -> None:
        self.assertEqual(hunt_hiring({"sector": "x", "countries": ["FR"]}, 5), [])

    def test_guess_email_and_mx_gate(self) -> None:
        self.assertEqual(
            guess_email("Jean-Pierre", "Dupont", "acme.fr"), "jeanpierre.dupont@acme.fr"
        )
        self.assertEqual(guess_email("Elise", "", "acme.fr"), "elise@acme.fr")
        self.assertEqual(guess_email("", "", "acme.fr"), "")
        rows = [
            {"company": "Acme", "domain": "acme.fr", "person": "Jean Dupont", "source": "sirene"},
            {
                "company": "Nomail",
                "domain": "nomail.example",
                "person": "Ana Silva",
                "source": "sirene",
            },
            {"company": "Nameless", "source": "web"},
        ]
        discover._MX_CACHE.clear()
        spent = bulk_enrich(
            rows,
            {"countries": ["FR"]},
            search_fn=_no_search,
            fetch_fn=lambda urls: [],
            resolver=lambda domain: domain == "acme.fr",
        )
        self.assertEqual(rows[0]["email"], "jean.dupont@acme.fr")
        self.assertEqual(rows[0]["email_status"], "guessed")
        self.assertNotIn("email", rows[1])
        self.assertEqual(spent["emails"], 1)
        self.assertEqual(spent["domains"], 1)  # the nameless row got one lookup

    def test_lookup_domain_rejects_directories_and_unrelated_hits(self) -> None:
        def search(query: str, count: int = 5) -> str:
            return (
                "Results:\n"
                "1. TEDDY SMITH - numero de telephone\n   https://www.telephone.fr/teddy-smith\n"
                "2. Groupe V33 - Overview, Contacts\n   https://rocketreach.co/groupe-v33\n"
                "3. Assistance\n   https://www.voissa.com/help\n"
                "4. Teddy Smith - Site officiel\n   https://www.teddysmith.com/fr/\n"
            )

        self.assertEqual(lookup_domain("TEDDY SMITH", "FR", search_fn=search), "teddysmith.com")
        # No hit shares a word with the name: better no domain than a stranger's.
        self.assertEqual(lookup_domain("Groupe V33", "FR", search_fn=search), "")
        self.assertTrue(domain_matches_company("v33.com", "Groupe V33"))
        self.assertTrue(domain_matches_company("agence-lumiere.fr", "Agence Lumiere SAS"))
        self.assertTrue(domain_matches_company("plusquepro.fr", "Plus que pro"))
        self.assertTrue(domain_matches_company("kpmg.fr", "Klynveld Peat Marwick Goerdeler"))
        self.assertFalse(domain_matches_company("u.is", "Utopia"))
        self.assertFalse(domain_matches_company("telephone.fr", "Teddy Smith"))

    def test_bulk_enrich_respects_budgets(self) -> None:
        rows = [{"company": f"Firm {index}", "source": "web"} for index in range(20)]
        calls = {"n": 0}

        def counting_search(query: str, count: int = 6) -> str:
            calls["n"] += 1
            return f"No results for: {query}"

        spent = bulk_enrich(
            rows,
            {},
            search_fn=counting_search,
            fetch_fn=lambda urls: [],
            resolver=lambda d: False,
            domain_budget=3,
        )
        self.assertEqual(spent["domains"], 3)
        self.assertEqual(calls["n"], 3)


class QualifyAndDraftTest(unittest.TestCase):
    def test_new_signals_move_timing_and_guessed_emails_count_less(self) -> None:
        base = {"company": "Acme", "country": "FR", "sector": "logistique"}
        profile = {"countries": ["FR"], "sector": "logistique"}
        listed = qualify({**base, "signal": "listed on blog.example.org: Top 20 agences"}, profile)
        hiring = qualify({**base, "signal": "hiring: Responsable supply chain"}, profile)
        plain = qualify(base, profile)
        self.assertGreater(hiring["score"], listed["score"])
        self.assertGreater(listed["score"], plain["score"])
        self.assertIn("listed", {item["kind"] for item in listed["signals"]})
        guessed = qualify({**base, "email": "a@acme.fr", "email_status": "guessed"}, profile)
        known = qualify({**base, "email": "a@acme.fr", "email_status": "unverified"}, profile)
        self.assertLess(guessed["score"], known["score"])

    def test_template_draft_speaks_the_prospect_language(self) -> None:
        profile = {
            "offer": "Nous automatisons la prospection B2B.",
            "sender_name": "Aymen",
            "sector": "logistique",
            "countries": ["FR"],
        }
        fr = template_draft(
            {
                "company": "Cargo Express",
                "country": "FR",
                "person": "Jean Dupont",
                "signal": "hiring: Responsable supply chain",
            },
            profile,
            1,
        )
        self.assertEqual(fr["lang"], "fr")
        self.assertTrue(fr["body"].startswith("Bonjour Jean,"))
        self.assertIn("recrute", fr["body"])
        self.assertIn("Nous automatisons la prospection B2B.", fr["body"])
        self.assertIn("STOP", fr["body"])
        self.assertTrue(fr["body"].rstrip().endswith("STOP."))
        de = template_draft({"company": "Muster GmbH", "country": "DE"}, profile, 2)
        self.assertEqual(de["lang"], "de")
        self.assertTrue(de["body"].startswith("Guten Tag,"))
        self.assertIn("Muster GmbH", de["body"])
        en = template_draft({"company": "Acme Ltd", "country": "GB"}, {**profile, "offer": ""}, 3)
        self.assertEqual(en["lang"], "en")
        self.assertIn("Last note", en["subject"])
        self.assertTrue(en["body"].startswith("Hello,"))
        no_offer = template_draft(
            {"company": "Acme Ltd", "country": "GB"}, {**profile, "offer": ""}, 2
        )
        self.assertIn("companies like yours", no_offer["body"])
        forced = template_draft(
            {"company": "Acme Ltd", "country": "GB"}, {**profile, "language": "fr"}, 1
        )
        self.assertEqual(forced["lang"], "fr")

    def test_ai_draft_uses_the_model_and_keeps_unsubscribe(self) -> None:
        profile = {"offer": "Prospecting on autopilot.", "countries": ["GB"], "ai_assist": True}
        row = {"company": "Acme Ltd", "country": "GB", "person": "Ann Lee"}

        def fake_ask(role, system, user, **kwargs):
            self.assertEqual(role, "writer")
            self.assertIn("Acme Ltd", user)
            return {
                "subject": "Acme x Navin",
                "body": "Hello Ann,\n\nSaw your team grow. " * 3,
            }, "gpt-test"

        out = ai_draft(row, profile, 1, ask_fn=fake_ask)
        self.assertEqual(out["model"], "gpt-test")
        self.assertEqual(out["subject"], "Acme x Navin")
        self.assertIn("Reply STOP", out["body"])
        # A broken model answer falls back to the template.
        bad = ai_draft(row, profile, 1, ask_fn=lambda *a, **k: ({"subject": "", "body": "x"}, "m"))
        self.assertEqual(bad["model"], "")
        off = ai_draft(
            row,
            {**profile, "ai_assist": False},
            1,
            ask_fn=lambda *a, **k: self.fail("must not call"),
        )
        self.assertEqual(off["model"], "")

    def test_draft_sequence_fills_every_unsent_step_once(self) -> None:
        row = {"company": "Acme", "country": "FR", "sequence": start_sequence({}, now=1000.0)}
        drafted = draft_sequence(row, {"offer": "x", "ai_assist": False})
        steps = drafted["sequence"]["steps"]
        self.assertEqual(len(steps), 3)
        self.assertTrue(all(step["draft"]["body"] for step in steps))
        subjects = [step["draft"]["subject"] for step in steps]
        self.assertEqual(len(set(subjects)), 3)
        again = draft_sequence(
            {**drafted, "company": "Renamed"}, {"offer": "x", "ai_assist": False}
        )
        self.assertEqual(again["sequence"]["steps"][0]["draft"]["subject"], subjects[0])


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = LeadsStore(Path(self.tmp.name))
        self.store.save_profile(
            {
                "icp_name": "Agences web Lyon",
                "sector": "agence web",
                "countries": ["FR"],
                "signals": ["developpeur web"],
                "offer": "Nous automatisons la prospection B2B.",
                "wizard_ready": True,
                "count": 20,
            }
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_profile_normalizes_new_fields(self) -> None:
        profile = normalize_profile(
            {
                "cities": "Lyon, Annecy",
                "keywords": ["shopify"],
                "signals": "growth, cto",
                "sources": "web,osm",
                "execution_mode": "AUTONOMOUS",
                "daily_send_cap": "999",
                "offer": "x" * 500,
            }
        )
        self.assertEqual(profile["cities"], ["Lyon", "Annecy"])
        self.assertEqual(profile["keywords"], ["shopify"])
        self.assertEqual(profile["signals"], ["growth", "cto"])
        self.assertEqual(profile["sources"], ["web", "osm"])
        self.assertEqual(profile["execution_mode"], "autonomous")
        self.assertEqual(profile["daily_send_cap"], 200)
        self.assertEqual(len(profile["offer"]), 400)
        self.assertEqual(normalize_profile({})["execution_mode"], "approval")
        self.assertEqual(normalize_profile({})["sources"], ["web", "osm", "hiring"])

    def test_run_hunt_rotates_cursor_and_attributes_sources(self) -> None:
        with patch("navin.leads.waterfall.hunt_sirene", return_value=[]):
            result = run_hunt(
                self.store,
                search_fn=_search,
                http_get=_http_get,
                jobs_fn=_jobs,
                fetch_fn=lambda urls: [],
                resolver=lambda domain: False,
            )
        self.assertGreaterEqual(result["found"], 4)
        self.assertEqual(result["added"], result["found"])
        self.assertEqual(result["cursor"], 0)
        self.assertEqual(set(result["by_source"]), {"web", "osm", "hiring"})
        self.assertEqual(self.store.load_cursor(), 1)
        rows = self.store.load_leads()
        hiring = [row for row in rows if row["source"] == "hiring"]
        self.assertEqual(hiring[0]["company"], "Cargo Express")
        self.assertTrue(all(row.get("score") is not None for row in rows))
        # Hiring rows sort first: they are the warmest signal.
        self.assertEqual(rows[0]["source"], "hiring")
        journal = self.store.journal_path.read_text(encoding="utf-8")
        self.assertIn('"by_source"', journal)

    def test_run_hunt_skips_opted_out_domains(self) -> None:
        self.store.add_optout("studio-b.fr")
        with patch("navin.leads.waterfall.hunt_sirene", return_value=[]):
            result = run_hunt(
                self.store,
                search_fn=_search,
                http_get=_http_get,
                jobs_fn=lambda **kwargs: {"jobs": []},
                fetch_fn=lambda urls: [],
                resolver=lambda domain: False,
            )
        self.assertEqual(result["optout"], 1)
        self.assertFalse(
            [row for row in self.store.load_leads() if row.get("domain") == "studio-b.fr"]
        )

    def _seed_a_tier(self, *, email_status: str = "unverified") -> str:
        rows, _ = self.store.upsert_leads(
            [
                {
                    "company": "Cargo Express",
                    "domain": "cargo-express.fr",
                    "country": "FR",
                    "sector": "agence web",
                    "person": "Jean Dupont",
                    "role": "CEO",
                    "email": "jean.dupont@cargo-express.fr",
                    "email_status": email_status,
                    "signal": "hiring: developpeur web",
                    "score": 88,
                    "tier": "A",
                    "stage": "new",
                }
            ]
        )
        return str(rows[0]["id"])

    def test_approval_mode_drafts_but_never_sends(self) -> None:
        lead_id = self._seed_a_tier()
        row = self.store.get_lead(lead_id)
        self.store.patch_lead(lead_id, {"sequence": start_sequence(row, now=1000.0)})
        sent: list[tuple] = []
        self.assertEqual(auto_enroll(self.store, now=2000.0), 0)
        result = run_sequences(
            self.store,
            now=2000.0,
            send_fn=lambda *a, **k: sent.append(a) or True,
            ready_fn=lambda: {"email": {"ready": True}},
        )
        self.assertEqual(result["drafted"], 1)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["ready"], 1)
        self.assertFalse(result["autonomous"])
        self.assertEqual(sent, [])
        step = self.store.get_lead(lead_id)["sequence"]["steps"][0]
        self.assertEqual(step["status"], "due")
        self.assertTrue(step["draft"]["body"].startswith("Bonjour Jean,"))

    def test_autonomous_mode_enrolls_sends_and_caps(self) -> None:
        self.store.save_profile(
            {"execution_mode": "autonomous", "daily_send_cap": 1, "sender_name": "Aymen"}
        )
        self._seed_a_tier()
        self.store.upsert_leads(
            [
                {
                    "company": "Second Co",
                    "domain": "second.fr",
                    "country": "FR",
                    "email": "ceo@second.fr",
                    "score": 90,
                    "tier": "A",
                    "stage": "new",
                }
            ]
        )
        self.assertEqual(auto_enroll(self.store, now=1000.0), 2)
        sent: list[dict] = []

        def send(channel: str, dest: str, *, subject: str, body: str) -> bool:
            sent.append({"channel": channel, "to": dest, "subject": subject, "body": body})
            return True

        result = run_sequences(
            self.store, now=1000.0, send_fn=send, ready_fn=lambda: {"email": {"ready": True}}
        )
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["ready"], 1)
        self.assertEqual(result["blocked"], "daily cap reached")
        self.assertEqual(sent[0]["channel"], "email")
        self.assertIn("STOP", sent[0]["body"])
        self.assertEqual(self.store.sends_today(now=1000.0), 1)
        contacted = [row for row in self.store.load_leads() if row.get("stage") == "contacted"]
        self.assertEqual(len(contacted), 1)
        seq = contacted[0]["sequence"]
        self.assertEqual(seq["steps"][0]["status"], "sent")
        self.assertEqual(seq["steps"][1]["status"], "pending")
        # Next day, the cap resets and the other lead goes out.
        later = 1000.0 + 86400
        result = run_sequences(
            self.store, now=later, send_fn=send, ready_fn=lambda: {"email": {"ready": True}}
        )
        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(sent), 2)

    def test_autonomous_mode_never_sends_to_guessed_emails_or_without_channel(self) -> None:
        self.store.save_profile({"execution_mode": "autonomous", "daily_send_cap": 10})
        lead_id = self._seed_a_tier(email_status="guessed")
        self.assertEqual(auto_enroll(self.store, now=1000.0), 0)
        row = self.store.get_lead(lead_id)
        self.store.patch_lead(lead_id, {"sequence": start_sequence(row, now=1000.0)})
        result = run_sequences(
            self.store,
            now=1000.0,
            send_fn=lambda *a, **k: self.fail("guessed email must not be sent"),
            ready_fn=lambda: {"email": {"ready": True}},
        )
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["ready"], 1)
        self.store.patch_lead(lead_id, {"email_status": "verified"})
        result = run_sequences(
            self.store,
            now=1000.0,
            send_fn=lambda *a, **k: True,
            ready_fn=lambda: {"email": {"ready": False}},
        )
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["blocked"], "email channel not connected")

    def test_optout_stops_sequences_and_hunt_never_readds(self) -> None:
        lead_id = self._seed_a_tier()
        row = self.store.get_lead(lead_id)
        self.store.patch_lead(lead_id, {"sequence": start_sequence(row, now=1000.0)})
        with patch("navin.webui.leads_api._store", return_value=self.store):
            payload = handle_leads_action("optout", {"value": "cargo-express.fr"})
        self.assertEqual(payload["optout"]["stopped"], 1)
        row = self.store.get_lead(lead_id)
        self.assertEqual(row["stage"], "lost")
        self.assertEqual(row["sequence"]["stopped"], "opt-out")
        self.assertTrue(self.store.is_opted_out({"email": "someone@cargo-express.fr"}))
        # The stopped sequence is silent for the watch, the heartbeat and the UI.
        self.assertEqual(due_steps(row, now=1000.0), [])
        self.assertFalse(
            [event for event in pending_alerts(self.store) if event["kind"] == "sequence"]
        )
        with self.assertRaises(LeadsError):
            with patch("navin.webui.leads_api._store", return_value=self.store):
                handle_leads_action("sequence", {"id": lead_id})

    def test_api_draft_sequences_and_export(self) -> None:
        lead_id = self._seed_a_tier()
        with patch("navin.webui.leads_api._store", return_value=self.store):
            preview = handle_leads_action("draft", {"id": lead_id})
            self.assertIn("Cargo Express", preview["draft"]["subject"])
            started = handle_leads_action("sequence", {"id": lead_id})
            self.assertTrue(all(step["draft"]["body"] for step in started["sequence"]["steps"]))
            redrafted = handle_leads_action("redraft", {"id": lead_id, "step": 2})
            self.assertEqual(
                redrafted["sequence"]["steps"][1]["draft"]["subject"],
                started["sequence"]["steps"][1]["draft"]["subject"],
            )
            with patch(
                "navin.leads.pipeline.run_sequences",
                return_value={
                    "drafted": 0,
                    "sent": 0,
                    "ready": 1,
                    "stopped": 0,
                    "blocked": "",
                    "autonomous": False,
                },
            ) as run:
                ran = handle_leads_action("sequences", {})
            run.assert_called_once()
            self.assertEqual(ran["sequences"]["ready"], 1)
            self.assertEqual(ran["sequences"]["enrolled"], 0)
            exported = handle_leads_action("export", {"tier": "A"})
        self.assertEqual(exported["rows"], 1)
        header, line = exported["csv"].strip().splitlines()
        self.assertTrue(header.startswith("company,person,role,email"))
        self.assertIn("Cargo Express", line)
        self.assertIn("jean.dupont@cargo-express.fr", line)
        self.assertEqual(exported["filename"], "navin-leads.csv")

    def test_snapshot_kpis_count_sources_and_sent_steps(self) -> None:
        lead_id = self._seed_a_tier()
        row = self.store.get_lead(lead_id)
        seq = start_sequence(row, now=1000.0)
        seq["steps"][0]["status"] = "sent"
        self.store.patch_lead(lead_id, {"sequence": seq, "source": "hiring"})
        with patch("navin.webui.leads_api._store", return_value=self.store):
            snap = handle_leads_action("snapshot", {})
        self.assertEqual(snap["kpis"]["sources"], {"hiring": 1})
        self.assertEqual(snap["kpis"]["sent_steps"], 1)
        self.assertEqual(snap["profile"]["execution_mode"], "approval")


class LoopSequencePhaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = LeadsStore(Path(self.tmp.name))
        self.store.save_profile(
            {"icp_name": "x", "sector": "SaaS", "countries": ["FR"], "wizard_ready": True}
        )
        self.now = 1_700_000_000.0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_tick_runs_hunt_watch_then_sequences(self) -> None:
        order: list[str] = []

        def collect(store: LeadsStore, **kwargs: object) -> dict[str, object]:
            order.append("hunt")
            return {"added": 2, "found": 2, "scanned": 2}

        def watch(store: LeadsStore) -> dict[str, object]:
            order.append("watch")
            return {"count": 0, "sent": {}}

        def sequences(store: LeadsStore, **kwargs: object) -> dict[str, object]:
            order.append("sequence")
            return {"drafted": 2, "sent": 1, "ready": 1}

        start_loop(self.store, run_now=False, now=self.now, collect_fn=collect)
        result = maybe_tick(
            self.store,
            force=True,
            now=self.now,
            collect_fn=collect,
            watch_fn=watch,
            sequence_fn=sequences,
        )
        self.assertEqual(order, ["hunt", "watch", "sequence"])
        self.assertEqual(result["sequences"]["sent"], 1)
        self.assertIn("1 sent", result["reason"])
        self.assertEqual(result["loop"]["sent"], 1)
        self.assertEqual(result["loop"]["drafts_ready"], 1)
        self.assertEqual(result["loop"]["phase"], "idle")

    def test_sequence_failure_does_not_break_the_cycle(self) -> None:
        def boom(store: LeadsStore, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("smtp down")

        start_loop(
            self.store,
            run_now=False,
            now=self.now,
            collect_fn=lambda s, **k: {"added": 1, "found": 1, "scanned": 1},
        )
        result = maybe_tick(
            self.store,
            force=True,
            now=self.now,
            collect_fn=lambda s, **k: {"added": 1, "found": 1, "scanned": 1},
            watch_fn=lambda s: {"count": 0, "sent": {}},
            sequence_fn=boom,
        )
        self.assertTrue(result["did_work"])
        self.assertEqual(result["sequences"]["error"], "smtp down")
        self.assertEqual(result["loop"]["error_streak"], 0)

    def test_default_sequence_phase_is_silent_on_an_empty_book(self) -> None:
        start_loop(
            self.store,
            run_now=False,
            now=self.now,
            collect_fn=lambda s, **k: {"added": 0, "found": 0, "scanned": 0},
        )
        result = maybe_tick(
            self.store,
            force=True,
            now=self.now,
            collect_fn=lambda s, **k: {"added": 0, "found": 0, "scanned": 0},
            watch_fn=lambda s: {"count": 0, "sent": {}},
        )
        self.assertEqual(result["sequences"]["sent"], 0)
        self.assertEqual(result["sequences"]["enrolled"], 0)


class HuntCompaniesOfflineTest(unittest.TestCase):
    def test_defaults_are_silent_under_the_offline_flag(self) -> None:
        with patch("navin.leads.waterfall.hunt_sirene", return_value=[]) as sirene:
            rows = hunt_companies({"countries": ["FR"], "sector": "SaaS"}, {}, 10)
        sirene.assert_called_once()
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
