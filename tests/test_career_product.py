# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Product acceptance: one complete Career desk, as a company would review it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.career.desk import (
    apply_one,
    classify_inbox,
    find_mission,
    followup_draft,
    import_offer,
    ingest_hits,
    prepare_application,
    read_local,
    rescore,
    save_profile,
    set_stage,
    snapshot,
)
from navin.career.errors import CareerError
from navin.career.matching import score_opportunity
from navin.career.sources import (
    MARKETS,
    catalog,
    infer_country_iso,
    is_listing_hit,
    normalize_job_url,
    official_search_pack,
    stable_job_id,
    web_search_queries,
)
from navin.career.store import CareerStore, normalize_profile
from navin.career.watch import run_watch
from navin.webui.career_api import handle_career_action

GOLDEN_PROFILE = {
    "titles": ["Data Engineer"],
    "track": "freelance",
    "stack": ["Python", "Spark", "AWS"],
    "languages": ["fr", "en"],
    "work_mode": "remote",
    "min_rate": 650,
    "countries_primary": ["FR"],
    "countries_secondary": ["US"],
    "countries_excluded": ["GB"],
    "country_weights": {"FR": 100.0, "US": 50.0},
    "strengths": ["Spark"],
}

GOLDEN_JOB = {
    "title": "Senior Data Engineer freelance",
    "description": "Python Spark AWS remote Paris fr en",
    "remote": "remote",
    "compensation": 700,
    "track": "freelance",
    "stack": ["Python", "Spark", "AWS"],
    "languages": ["fr", "en"],
}

MASTER_CV = (
    "Aymen - Senior Data Engineer. Python, Spark, AWS on billed missions in Paris. "
    "No other employers or diplomas."
)


def _empty_collectors():
    return {
        "navin.career.collect._fetch_remotive": [],
        "navin.career.collect._fetch_ats_boards": [],
        "navin.career.collect.collect_official_apis": [],
        "navin.career.collect.search_web_hits": [],
        "navin.career.collect.scrape_open_net": {"jobs": [], "walls": [], "refused": []},
        "navin.career.collect.search_linkedin_jobs": {"jobs": [], "walls": [], "requests": 0},
        "navin.career.collect.search_freework_jobs": {"jobs": [], "walls": [], "requests": 0, "total": 0},
        "navin.career.collect.search_collective_jobs": {"jobs": [], "walls": [], "requests": 0, "total": 0},
        "navin.career.collect.collect_feeds": {"jobs": [], "walls": [], "requests": 0, "by_source": {}},
        "navin.career.collect.collect_employers": {"jobs": [], "checked": 0, "reports": [], "errors": []},
    }


def _patch_collectors(values: dict | None = None):
    merged = {**_empty_collectors(), **(values or {})}
    return [patch(target, return_value=value) for target, value in merged.items()]


class CareerGoldenScoreTest(unittest.TestCase):
    def test_studio_and_agent_share_the_same_numbers(self) -> None:
        paris = score_opportunity({**GOLDEN_JOB, "country": "FR"}, GOLDEN_PROFILE)
        usa = score_opportunity({**GOLDEN_JOB, "country": "US"}, GOLDEN_PROFILE)
        uk = score_opportunity({**GOLDEN_JOB, "country": "GB"}, GOLDEN_PROFILE)
        cdi = score_opportunity(
            {**GOLDEN_JOB, "title": "Senior Data Engineer", "country": "FR", "track": "jobs"},
            GOLDEN_PROFILE,
        )
        self.assertEqual(paris["match_score"], 100)
        self.assertEqual(paris["bucket"], "perfect")
        self.assertEqual(usa["match_score"], 91)
        self.assertEqual(usa["bucket"], "perfect")
        self.assertEqual(uk["match_score"], 24)
        self.assertEqual(uk["bucket"], "skip")
        self.assertEqual(cdi["match_score"], 88)
        self.assertGreater(paris["match_score"], usa["match_score"])
        self.assertGreater(usa["match_score"], uk["match_score"])


class CareerWorldDeskTest(unittest.TestCase):
    def test_full_desk_from_brief_to_interview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            saved = save_profile(
                store,
                {
                    "account_kind": "company",
                    "display_name": "Aymen",
                    "headline": "Senior Data Engineer",
                    "wizard_complete": True,
                    "apply_mode": "manual",
                    "master_cv": MASTER_CV,
                    "mail": {"gmail": True, "outlook": False},
                    "channels": {"email": True, "email_to": "talent@navin.test"},
                    "company": {"name": "Navin Talent", "city": "Paris", "country": "FR"},
                    "ats_boards": ["databricks"],
                    "source_ids": ["adzuna"],
                    **GOLDEN_PROFILE,
                    "countries_primary": ["FR", "BE"],
                    "countries_secondary": ["US", "CA", "AE"],
                    "countries_excluded": ["GB"],
                },
            )
            profile = saved["profile"]
            self.assertEqual(profile["account_kind"], "company")
            self.assertEqual(profile["company"]["name"], "Navin Talent")
            self.assertTrue(profile["mail"]["gmail"])
            self.assertEqual(profile["country_weights"]["FR"], 100.0)
            self.assertLess(profile["country_weights"]["US"], profile["country_weights"]["FR"])
            self.assertNotIn("GB", profile["country_weights"])
            self.assertIn("Search. Match. Tailor. Apply.", saved["tagline"])
            self.assertNotIn("\u2014", saved["tagline"])
            self.assertNotIn("\u2013", saved["tagline"])

            remotive = [
                {
                    "id": "job-remotive-paris",
                    "source": "remotive",
                    "title": "Senior Data Engineer freelance",
                    "company": "Paris Co",
                    "country": "FR",
                    "url": "https://remotive.com/remote-jobs/paris-1",
                    "description": "Python Spark AWS remote Paris fr en",
                    "compensation": 700,
                    "track": "freelance",
                    "stack": ["Python", "Spark", "AWS"],
                    "remote": "remote",
                    "languages": ["fr", "en"],
                    "ingest": "official_api",
                }
            ]
            ats = [
                {
                    "id": "job-gh-acme",
                    "source": "greenhouse",
                    "title": "Senior Data Engineer freelance",
                    "company": "Acme",
                    "country": "FR",
                    "url": "https://boards.greenhouse.io/acme/jobs/1",
                    "description": "Python Spark AWS remote Paris fr en",
                    "compensation": 700,
                    "track": "freelance",
                    "stack": ["Python", "Spark", "AWS"],
                    "remote": "remote",
                    "languages": ["fr", "en"],
                    "ingest": "ats_api",
                }
            ]
            official = [
                {
                    "id": "job-adzuna-1",
                    "source": "adzuna",
                    "title": "Senior Data Engineer freelance",
                    "company": "Adzuna Co",
                    "country": "BE",
                    "url": "https://www.adzuna.be/details/1",
                    "description": "Python Spark AWS remote Bruxelles fr en",
                    "compensation": 680,
                    "track": "freelance",
                    "stack": ["Python", "Spark", "AWS"],
                    "remote": "remote",
                    "languages": ["fr", "en"],
                    "ingest": "official_api",
                }
            ]
            web = [
                {
                    "id": "job-web-li",
                    "source": "linkedin",
                    "title": "Senior Data Engineer freelance - Closed Co",
                    "company": "Closed Co",
                    "country": "FR",
                    "url": "https://www.linkedin.com/jobs/view/4242",
                    "description": "Python Spark AWS remote Paris hiring",
                    "track": "freelance",
                    "ingest": "search_snippet",
                },
                {
                    "id": "job-web-indeed-dropped-if-not-web",
                    "source": "adzuna",
                    "title": "Closed aggregator",
                    "url": "https://www.indeed.com/viewjob?jk=x",
                    "country": "FR",
                    "description": "Python",
                },
            ]
            patches = _patch_collectors(
                {
                    "navin.career.collect._fetch_remotive": remotive,
                    "navin.career.collect._fetch_ats_boards": ats,
                    "navin.career.collect.collect_official_apis": official,
                    "navin.career.collect.search_web_hits": web,
                }
            )
            for item in patches:
                item.start()
            try:
                found = find_mission(
                    store,
                    "Senior Data Engineer freelance 650 EUR/day remote France Belgium",
                )
            finally:
                for item in patches:
                    item.stop()

            search = found["search"]
            self.assertEqual(search["official"], 1)
            self.assertEqual(search["web"], 2)
            sources = {row["source"] for row in found["opportunities"]}
            self.assertTrue({"remotive", "greenhouse", "adzuna", "linkedin"} <= sources)
            self.assertFalse(any("indeed.com" in str(row.get("url")) for row in found["opportunities"]))
            self.assertTrue(any(row.get("kind") == "linkedin" for row in search["portals"]))
            self.assertNotIn("GB", {row.get("country") for row in search["portals"]})

            ingested = ingest_hits(
                store,
                {
                    "hits": [
                        {**GOLDEN_JOB, "country": "US", "url": "https://remotive.com/remote-jobs/austin", "company": "Austin Co"},
                        {**GOLDEN_JOB, "country": "GB", "url": "https://www.jobserve.com/gb/job/1", "company": "London Co"},
                        {**GOLDEN_JOB, "country": "US", "url": "https://remotive.com/remote-jobs/austin", "company": "Austin Co"},
                    ]
                },
            )
            by_company = {row["company"]: row for row in ingested["opportunities"]}
            self.assertEqual(ingested["ingested"], 3)
            self.assertEqual(sum(1 for row in ingested["opportunities"] if row.get("company") == "Austin Co"), 1)
            self.assertGreater(by_company["Paris Co"]["match_score"], by_company["Austin Co"]["match_score"])
            self.assertLessEqual(by_company["London Co"]["match_score"], 24)
            self.assertEqual(by_company["London Co"]["bucket"], "skip")

            oid = by_company["Paris Co"]["id"]
            prepared = prepare_application(store, oid)
            pack = prepared["prepared"]
            self.assertNotIn("Do not invent", pack["summary"])
            self.assertIn("Spark", pack["summary"])
            self.assertNotIn("Google", pack["summary"])
            self.assertNotIn("Google", pack["cv_text"])
            self.assertNotIn("\u2014", pack["cover"])
            self.assertIn("Spark", pack["cv_text"])
            self.assertIn("Senior Data Engineer", pack["cv_text"])
            self.assertTrue(pack["pack_ready"])
            self.assertTrue(pack["cv_name"].endswith(".docx"))
            self.assertEqual(next(row for row in prepared["opportunities"] if row["id"] == oid)["stage"], "ready")
            self.assertTrue(next(row for row in prepared["opportunities"] if row["id"] == oid)["pack_ready"])

            applied = apply_one(store, oid)
            fr = next(row for row in applied["opportunities"] if row["id"] == oid)
            self.assertEqual(fr["stage"], "ready")
            self.assertIn("Open the original URL", fr["next_action"])

            set_stage(store, oid, "applied")
            inbox = classify_inbox(
                store,
                {
                    "opportunity_id": oid,
                    "sender": "Recruiter",
                    "body": "We would like a technical interview next Tuesday.",
                },
            )
            self.assertEqual(inbox["classified"]["classification"], "interview")
            self.assertEqual(next(row for row in inbox["opportunities"] if row["id"] == oid)["stage"], "interview")

            j3 = followup_draft(store, oid, "j3")
            j7 = followup_draft(store, oid, "j7")
            self.assertIn("follow up", j3["followup"]["body"])
            self.assertIn("second time", j7["followup"]["body"])
            self.assertNotIn("\u2014", j3["followup"]["body"])
            self.assertNotIn("\u2013", j7["followup"]["body"])

            imported = import_offer(
                store,
                {
                    "url": "https://www.linkedin.com/jobs/view/99",
                    "title": "Data Engineer LinkedIn",
                    "company": "Paste Co",
                    "body": "Python Spark Paris freelance",
                    "country": "FR",
                },
            )
            li = imported["imported"]
            self.assertEqual(li["source"], "linkedin")
            self.assertEqual(li["ingest"], "open_manual")
            with self.assertRaises(CareerError) as blocked:
                import_offer(store, {"url": "https://www.linkedin.com/jobs/view/1", "title": "X", "fetch": True})
            self.assertIn("never fetched", str(blocked.exception).lower())

            book = read_local(store, "book")
            self.assertIn("Aymen", book["text"])
            self.assertTrue(book["path"].endswith("INDEX.md") or "book" in book["file"])
            kpis = snapshot(store)["kpis"]
            self.assertGreaterEqual(kpis["opportunities"], 5)
            self.assertGreaterEqual(kpis["interviews"], 1)
            self.assertIn("opportunities", kpis["headline"])

            with patch("navin.career.notify.deliver_alert", return_value={"webui": True}):
                first_watch = run_watch(store)
                second_watch = run_watch(store)
            self.assertEqual(second_watch["count"], 0)
            self.assertIsInstance(first_watch["count"], int)
            self.assertTrue(first_watch.get("delivered") or first_watch["count"] == 0)

            rescored = rescore(store)
            self.assertTrue(any(row.get("match_score") for row in rescored["opportunities"]))

    def test_http_desk_rejects_bad_actions_and_keeps_linkedin_submission_manual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"apply_mode": "autopilot", "titles": ["Data Engineer"], **GOLDEN_PROFILE})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-li",
                        "title": "Data Engineer",
                        "source": "linkedin",
                        "url": "https://www.linkedin.com/jobs/view/1",
                        "stage": "ready",
                    }
                ]
            )
            with patch("navin.webui.career_api._store", return_value=store):
                with self.assertRaises(CareerError) as unknown:
                    handle_career_action("explode")
                self.assertEqual(unknown.exception.status, 400)
                applied = handle_career_action("apply", {"id": "job-li"})
                self.assertEqual(applied["applications"][0]["stage"], "ready")
                self.assertFalse(applied["applications"][0].get("applied_at"))
                with self.assertRaises(CareerError):
                    handle_career_action("prepare", {})
                with self.assertRaises(CareerError):
                    handle_career_action("inbox", {})
                with self.assertRaises(CareerError):
                    handle_career_action("find", {})
                snap = handle_career_action("status")
                self.assertIn("catalog", snap)
                self.assertEqual(len(snap["catalog"]), len(catalog()))
                book = handle_career_action("read", {"file": "book"})
                self.assertIn("text", book)
                handle_career_action("stage", {"id": "job-li", "stage": "rejected"})
                self.assertEqual(store.get_opportunity("job-li")["stage"], "rejected")


class CareerListingNoiseTest(unittest.TestCase):
    def test_drops_seo_pages_and_keeps_a_real_offer(self) -> None:
        from navin.career.collect import _hit_to_job

        self.assertTrue(is_listing_hit("Fiche metier Data engineer | MetierScope", "https://candidat.francetravail.fr/metierscope/fiche-metier/1"))
        self.assertTrue(is_listing_hit("86 offres d'emploi Data Engineer Freelance - LinkedIn"))
        self.assertTrue(is_listing_hit("Data Engineer Jobs for August 2026 | FreelancerMissions"))
        self.assertTrue(is_listing_hit("Jobs in Tunisia - Page 2 - Bayt.com"))
        self.assertFalse(is_listing_hit("Senior Data Engineer - Acme", "https://www.welcometothejungle.com/fr/jobs/1"))
        self.assertTrue(is_listing_hit("Engineer jobs | Dice.com"))
        self.assertTrue(is_listing_hit("engineer in various locations - Search - Job Bank"))
        dropped = _hit_to_job(
            {
                "title": "Fiche métier Data engineer | Apec",
                "url": "https://www.apec.fr/tous-nos-metiers/fiche-metier",
                "snippet": "Data engineer emploi",
            },
            country="FR",
            track="freelance",
        )
        kept = _hit_to_job(
            {
                "title": "Senior Data Engineer - Acme",
                "url": "https://www.welcometothejungle.com/fr/jobs/1",
                "snippet": "Python Spark remote Paris hiring",
            },
            country="FR",
            track="freelance",
        )
        self.assertIsNone(dropped)
        self.assertEqual(kept["source"], "web")
        self.assertEqual(kept["title"], "Senior Data Engineer - Acme")


class CareerApplicationDedupeTest(unittest.TestCase):
    def test_prepare_twice_updates_the_same_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"], "master_cv": MASTER_CV, "wizard_complete": True})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-acme",
                        "title": "Senior Data Engineer",
                        "company": "Acme",
                        "source": "greenhouse",
                        "url": "https://boards.greenhouse.io/acme/jobs/1",
                        "description": "Python Spark",
                        "stage": "matched",
                    }
                ]
            )
            first = prepare_application(store, "job-acme")
            second = prepare_application(store, "job-acme")
            self.assertEqual(len(second["applications"]), 1)
            self.assertEqual(first["applications"][0]["id"], second["applications"][0]["id"])
            apply_one(store, "job-acme")
            self.assertEqual(len(store.load_applications()), 1)


class CareerApplicationsTrackerTest(unittest.TestCase):
    def test_pipeline_jobs_are_not_applications_until_pack_or_sent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"], "master_cv": MASTER_CV, "wizard_complete": True})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-found",
                        "title": "Senior Data Engineer",
                        "company": "Acme",
                        "source": "greenhouse",
                        "url": "https://boards.greenhouse.io/acme/jobs/1",
                        "description": "Python Spark",
                        "stage": "matched",
                    },
                    {
                        "id": "job-other",
                        "title": "Data Engineer",
                        "company": "Beta",
                        "source": "remotive",
                        "url": "https://remotive.com/remote-jobs/1",
                        "description": "Python",
                        "stage": "discovered",
                    },
                ]
            )
            self.assertEqual(store.load_applications(), [])
            prepared = prepare_application(store, "job-found")
            self.assertEqual(len(prepared["applications"]), 1)
            self.assertEqual(prepared["applications"][0]["company"], "Acme")
            self.assertEqual(prepared["applications"][0]["stage"], "ready")
            applied = apply_one(store, "job-found")
            self.assertEqual(len(applied["applications"]), 1)
            self.assertTrue(applied["applications"][0].get("employer_opened"))
            found = next(row for row in applied["opportunities"] if row["id"] == "job-found")
            self.assertEqual(found["stage"], "ready")
            self.assertEqual(len(store.load_applications()), 1)
            marked = set_stage(store, "job-other", "applied")
            self.assertEqual(len(marked["applications"]), 2)
            self.assertEqual(
                {row["opportunity_id"] for row in marked["applications"]},
                {"job-found", "job-other"},
            )
            set_stage(store, "job-other", "matched")
            self.assertEqual(len(store.load_applications()), 2)


class CareerCvPackTest(unittest.TestCase):
    def test_filled_master_cv_docx_is_professional(self) -> None:
        from navin.career.export import render_docx
        from navin.career.writer import build_pack

        profile = {
            "display_name": "Aymen GHADGHADI",
            "headline": "Senior Data Engineer",
            "email": "aymen@example.com",
            "phone": "+33 6 00 00 00 00",
            "city": "Paris",
            "languages": ["fr", "en"],
            "stack": ["Python", "Spark", "AWS"],
            "strengths": ["Spark"],
            "master_cv": MASTER_CV,
            "experiences": [
                {
                    "title": "Data Engineer",
                    "company": "Acme",
                    "period": "2022-2024",
                    "facts": "Python Spark pipelines. AWS billed missions in Paris.",
                },
                {
                    "title": "Analyst",
                    "company": "Beta",
                    "period": "2019-2021",
                    "facts": "Reporting SQL.",
                },
            ],
            "education": [{"diploma": "MSc", "school": "Universite", "year": "2018"}],
        }
        job = {
            **GOLDEN_JOB,
            "title": "Senior Data Engineer freelance",
            "company": "Paris Co",
            "country": "FR",
            "description": "Python Spark AWS remote Paris fr en mission",
        }
        pack = build_pack(job, profile)
        cv = pack["cv"]
        self.assertEqual(cv["experiences"][0]["company"], "Acme")
        self.assertNotIn("Do not invent", cv["summary"])
        self.assertNotIn("Cible :", pack["cv_text"])
        self.assertNotIn("Cible :", cv["summary"])
        blob = render_docx(job, profile, pack)
        self.assertIsNotNone(blob)
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
            styles = archive.read("word/styles.xml").decode("utf-8")
        self.assertIn("1B365D", xml)
        self.assertIn("Calibri", styles)
        self.assertIn("Aymen GHADGHADI", xml)
        self.assertIn("EXPÉRIENCE", xml)
        self.assertIn("Acme", xml)
        self.assertIn("FORMATION", xml)
        self.assertNotIn("Do not invent", xml)
        self.assertNotIn("ats_notes", xml)
        self.assertNotIn("Cible :", xml)
        self.assertNotIn("not on file", xml.lower())
        self.assertNotIn("non renseigne", xml.lower())
        self.assertNotIn("\u2014", xml)
        self.assertTrue(cv["experiences"])
        self.assertTrue(cv["education"])
        self.assertTrue(cv["languages"])
        self.assertTrue(cv["summary"].strip())
        self.assertTrue(pack["cover"].strip())
        for row in cv["experiences"]:
            self.assertTrue(row["company"])
            self.assertTrue(row["title"])
            self.assertTrue(row["bullets"])


class CareerIngestBridgeTest(unittest.TestCase):
    def test_stable_id_is_sha1_and_urls_dedupe(self) -> None:
        import hashlib

        raw = "remotive|https://example.com/j|Data Engineer"
        self.assertEqual(
            stable_job_id("remotive", "https://example.com/j", "Data Engineer"),
            "job-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12],
        )
        accented_title = "Ingenieur " + "donnees"
        accented = f"remotive|https://example.com/j|{accented_title}"
        self.assertEqual(
            stable_job_id("remotive", "https://example.com/j", accented_title),
            "job-" + hashlib.sha1(accented.encode("utf-8")).hexdigest()[:12],
        )
        unicode_title = "Ing\u00e9nieur donn\u00e9es"
        unicode_raw = f"remotive|https://example.com/j|{unicode_title}"
        self.assertEqual(
            stable_job_id("remotive", "https://example.com/j", unicode_title),
            "job-" + hashlib.sha1(unicode_raw.encode("utf-8")).hexdigest()[:12],
        )
        self.assertEqual(
            normalize_job_url("https://www.Example.com/jobs/1/?utm_source=x"),
            "example.com/jobs/1?utm_source=x",
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(normalize_profile({**GOLDEN_PROFILE, "master_cv": MASTER_CV}))
            first = ingest_hits(
                store,
                {
                    "hits": [
                        {
                            "id": "job-aaa",
                            "title": "Data Engineer",
                            "url": "https://remotive.com/remote-jobs/dup",
                            "company": "First Co",
                            "country": "FR",
                        }
                    ]
                },
            )
            second = ingest_hits(
                store,
                {
                    "hits": [
                        {
                            "id": "job-bbb",
                            "title": "Data Engineer",
                            "url": "https://www.remotive.com/remote-jobs/dup",
                            "company": "Second Co",
                            "country": "FR",
                        }
                    ]
                },
            )
            remotive_rows = [
                row
                for row in second["opportunities"]
                if "remotive.com/remote-jobs/dup" in str(row.get("url") or "")
            ]
            self.assertEqual(len(remotive_rows), 1)
            self.assertEqual(remotive_rows[0]["id"], first["opportunities"][0]["id"])
            self.assertEqual(remotive_rows[0]["company"], "Second Co")

    def test_ingest_maps_mcp_jobs_and_drops_listing_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(normalize_profile({**GOLDEN_PROFILE, "master_cv": MASTER_CV}))
            result = ingest_hits(
                store,
                {
                    "via": "linkedin-mcp",
                    "hits": [
                        {
                            "job_title": "Staff Data Engineer",
                            "job_url": "https://www.linkedin.com/jobs/view/4242",
                            "company_name": "Session Co",
                            "location_name": "Paris",
                            "snippet": "Python Spark AWS remote",
                        },
                        {
                            "title": "Fiche métier Data engineer",
                            "url": "https://www.apec.fr/tous-nos-metiers/data",
                            "company": "Noise",
                        },
                    ],
                },
            )
            self.assertEqual(result["ingested"], 1)
            row = next(item for item in result["opportunities"] if item.get("company") == "Session Co")
            self.assertEqual(row["source"], "linkedin")
            self.assertEqual(row["ingest"], "linkedin_mcp")
            self.assertEqual(row["title"], "Staff Data Engineer")
            self.assertEqual(row["country"], "FR")
            self.assertEqual(row["remote"], "remote")
            self.assertGreaterEqual(row["match_score"], 80)
            self.assertTrue(is_listing_hit("Fiche métier Data engineer", "https://www.apec.fr/tous-nos-metiers"))
            self.assertFalse(any(item.get("company") == "Noise" for item in result["opportunities"]))

    def test_infer_country_from_mcp_location_not_english_us(self) -> None:
        self.assertEqual(infer_country_iso("", "Paris"), "FR")
        self.assertEqual(infer_country_iso("", "Paris, Ile-de-France, France"), "FR")
        self.assertEqual(infer_country_iso("France", ""), "FR")
        self.assertEqual(infer_country_iso("", "London, United Kingdom"), "GB")
        self.assertEqual(infer_country_iso("UK", ""), "GB")
        self.assertEqual(infer_country_iso("FR", "London, United Kingdom"), "FR")
        self.assertEqual(infer_country_iso("", "Berlin, Germany"), "DE")
        self.assertEqual(infer_country_iso("", "Dubai, UAE"), "AE")
        self.assertEqual(infer_country_iso("", "Montreal, Canada"), "CA")
        self.assertEqual(infer_country_iso("", "Remote"), "REMOTE")
        self.assertEqual(infer_country_iso("", "Join us in London for a team offsite"), "GB")
        self.assertNotEqual(infer_country_iso("", "Join us next week"), "US")

    def test_mcp_london_is_excluded_when_gb_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(normalize_profile({**GOLDEN_PROFILE, "master_cv": MASTER_CV}))
            result = ingest_hits(
                store,
                {
                    "via": "linkedin-mcp",
                    "hits": [
                        {
                            "job_title": "Staff Data Engineer",
                            "job_url": "https://www.linkedin.com/jobs/view/london-1",
                            "company_name": "London Co",
                            "location_name": "London, England, United Kingdom",
                            "snippet": "Python Spark AWS remote",
                        },
                        {
                            "job_title": "Staff Data Engineer",
                            "job_url": "https://www.linkedin.com/jobs/view/paris-1",
                            "company_name": "Paris Co",
                            "location_name": "Paris, France",
                            "snippet": "Python Spark AWS remote",
                        },
                    ],
                },
            )
            by_company = {row["company"]: row for row in result["opportunities"]}
            self.assertEqual(by_company["London Co"]["country"], "GB")
            self.assertEqual(by_company["London Co"]["bucket"], "skip")
            self.assertLessEqual(by_company["London Co"]["match_score"], 24)
            self.assertEqual(by_company["Paris Co"]["country"], "FR")
            self.assertEqual(by_company["Paris Co"]["remote"], "remote")
            self.assertGreaterEqual(by_company["Paris Co"]["match_score"], 80)
            self.assertGreater(by_company["Paris Co"]["match_score"], by_company["London Co"]["match_score"])
            watch = run_watch(store)
            self.assertGreaterEqual(watch["count"], 1)
            ids = {event["id"] for event in watch["events"]}
            self.assertIn(by_company["Paris Co"]["id"], ids)
            self.assertNotIn(by_company["London Co"]["id"], ids)

    def test_watch_is_silent_on_an_empty_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(normalize_profile({**GOLDEN_PROFILE, "wizard_complete": True}))
            watch = run_watch(store)
            self.assertEqual(watch["count"], 0)
            self.assertEqual(watch["digest"], "")
            self.assertEqual(watch["events"], [])

    def test_http_aliases_match_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(normalize_profile({**GOLDEN_PROFILE, "master_cv": MASTER_CV}))
            with patch("navin.webui.career_api._store", return_value=store):
                snap = handle_career_action("snapshot")
                self.assertIn("profile", snap)
                scored = handle_career_action("rescore")
                self.assertIn("opportunities", scored)
                empty = handle_career_action("hits", {"jobs": []})
                self.assertEqual(empty["ingested"], 0)
                with self.assertRaises(CareerError):
                    handle_career_action("mission", {})


class CareerWorldMarketsTest(unittest.TestCase):
    def test_every_market_opens_linkedin_first_in_its_languages(self) -> None:
        for iso, market in MARKETS.items():
            with self.subTest(market=iso):
                pack = official_search_pack("Senior Data Engineer", [iso], track="freelance", work_mode="remote")
                self.assertEqual(pack[0]["id"], f"li-{iso}")
                self.assertIn("linkedin.com/jobs/search", pack[0]["url"])
                self.assertTrue(any(row.get("kind") in {"board", "api"} for row in pack[1:]), iso)
                queries = web_search_queries(
                    titles=["Senior Data Engineer"],
                    countries=[iso],
                    track="freelance",
                    stack=["Python"],
                )
                blob = " ".join(row["query"] for row in queries)
                for lang in market["langs"]:
                    from navin.career.sources import SEARCH_LANG_TERMS

                    self.assertIn(SEARCH_LANG_TERMS[lang], blob)
                for domain in market["domains"]:
                    self.assertIn(f"site:{domain}", blob)

    def test_catalog_notes_never_use_long_dashes(self) -> None:
        for row in catalog():
            with self.subTest(source=row["id"]):
                text = " ".join(str(row.get(key) or "") for key in ("name", "notes", "url"))
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)


if __name__ == "__main__":
    unittest.main()
