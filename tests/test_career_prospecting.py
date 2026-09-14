# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import base64
import io
import json
import time
import zipfile
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

import pytest
from filelock import FileLock

from navin.career.errors import CareerError
from navin.career.prospecting import (
    _candidates,
    _match,
    _save,
    _state,
    handle_prospecting,
    prospecting_snapshot,
    score_candidate,
)
from navin.career.sourcing_mail import (
    _ref,
    _send,
    proposal_rate,
    read_candidate_cv,
    record_sourcing_reply,
    run_sourcing_cycle,
)
from navin.career.store import CareerStore
from navin.desk_archive import archive_reset, download_archive, list_archives
from navin.tenders.store import TenderStore


def test_bundled_source_catalog_matches_gateway():
    from navin.career.prospecting_catalog import mission_catalog, platform_catalog

    catalog = json.loads((Path(__file__).resolve().parents[1] / "webui/src/lib/career-source-catalog.json").read_text())
    assert catalog == {"missions": mission_catalog(), "platforms": platform_catalog()}


def test_profile_navigation_restores_www_without_changing_saved_identity(tmp_path):
    store = CareerStore(tmp_path / "career")
    candidate = {"id": "existing-id", "url": "https://lesbonsfreelances.com/freelance/example?x=1"}
    _save(store, {"candidates": [candidate], "matches": {"offer": {"results": [{"candidate": candidate}]}}})
    snapshot = prospecting_snapshot(store)
    expected = "https://www.lesbonsfreelances.com/freelance/example?x=1"
    assert snapshot["candidates"][0]["url"] == expected
    assert snapshot["matches"]["offer"]["results"][0]["candidate"]["url"] == expected
    assert snapshot["candidates"][0]["id"] == "existing-id"
    assert _state(store)["candidates"][0]["url"] == candidate["url"]


def test_company_multiple_choices_survive_reload(tmp_path):
    store = CareerStore(tmp_path / "career")
    criteria = {
        "domain": "Santé, Finance et banque, Domaine personnalisé",
        "roles": ["Infirmier", "Comptable", "Métier personnalisé"],
        "skills": ["Soins", "SQL", "Compétence personnalisée"],
        "countries": ["SA", "OM", "AE", "BH", "QA", "KW", "MA"],
        "sources": ["linkedin", "google_jobs", "bayt", "rekrute"],
        "platforms": ["linkedin", "freelancer", "peopleperhour"],
        "profile_domain": "Santé, Industrie",
        "profile_roles": ["Coordinateur de soins", "Technicien de maintenance"],
        "profile_skills": ["Coordination", "Maintenance industrielle"],
        "profile_countries": ["FR", "MA"],
    }
    handle_prospecting(store, "prospecting_config", {"criteria": criteria})
    persisted = prospecting_snapshot(CareerStore(store.root))["criteria"]
    for key, value in criteria.items():
        assert persisted[key] == value


def test_company_no_key_defaults_and_manual_removals_survive_reload(tmp_path):
    from navin.career.prospecting_catalog import default_mission_sources

    store = CareerStore(tmp_path / "career")
    sources = prospecting_snapshot(store)["criteria"]["sources"]
    assert sources == default_mission_sources("freelance")
    assert "mon-consultant-independant" in sources and "jobicy" not in sources and "google_jobs" not in sources
    defaults = {"version": 1, "generated": {"skills": ["Soins"]}, "dismissed": {"profile_skills": ["Soins"], "sources": sources}}
    handle_prospecting(store, "prospecting_config", {"criteria": {"sources": [], "profile_roles": [], "autofill": defaults,
        "skills": [f"Skill {index}" for index in range(80)]}})
    reloaded = prospecting_snapshot(CareerStore(store.root))["criteria"]
    assert reloaded["sources"] == [] and reloaded["profile_roles"] == []
    assert reloaded["autofill"] == defaults
    assert len(reloaded["skills"]) == 80


def test_profile_search_uses_candidate_roles_and_offer_matching_uses_offer_requirements(store):
    handle_prospecting(store, "prospecting_config", {"criteria": {"sources": [], "profile_roles": ["Comptable"], "profile_skills": ["Comptabilité"]}})
    with patch("navin.career.prospecting._candidates", return_value=[]) as search:
        handle_prospecting(store, "prospecting_search", {})
    assert all(call.args[2]["roles"] == ["Comptable"] for call in search.call_args_list)
    store.upsert_opportunities([offer()])
    with patch("navin.career.prospecting._candidates", return_value=[]) as search:
        handle_prospecting(store, "prospecting_search", {"id": "job-one"})
    assert search.call_count > 0
    assert all(call.args[2]["roles"] == ["Infirmier"] and call.args[2]["skills"] == ["soins"] for call in search.call_args_list)


@pytest.fixture
def store(tmp_path):
    store = CareerStore(tmp_path / "career")
    handle_prospecting(store, "prospecting_config", {"criteria": {
        "company": {"name": "Agence", "email": "agency@example.com"},
        "domain": "Santé", "roles": ["Infirmier"], "skills": ["soins"], "countries": ["MA"],
        "sources": ["google_jobs", "linkedin"], "internal_talents": True, "work_mode": "any",
    }, "activate_company": True})
    return store


def candidate(**extra):
    return {"id": "person-1", "name": "Sam", "headline": "Infirmier soins Maroc", "snippet": "Disponible, soins au Maroc",
            "skills": ["soins"], "country": "MA", "city": "", "url": "https://linkedin.com/in/sam",
            "source": "LinkedIn", "signal": "declared", "observed_at": time.time(), "email": "sam@example.com", **extra}


def offer():
    return {"id": "job-one", "title": "Infirmier", "description": "soins", "stack": ["soins"], "country": "MA",
            "posted_at": datetime.now(timezone.utc).date().isoformat(),
            "source": "google_jobs", "url": "https://example.com/jobs/nurse", "stage": "discovered", "track": "freelance",
            "application_email": "client@example.com"}


def seed(store):
    state = _state(store)
    state["candidates"] = [candidate()]
    store.upsert_opportunities([offer()])
    _match(store, state, offer())
    _save(store, state)
    return state, state["matches"]["job-one"]["results"][0]


def test_daily_company_search_persists_results_and_reuses_profiles_next_day(store):
    from navin.career.loop import maybe_tick, peek_loop

    handle_prospecting(store, "prospecting_config", {"activate_company": True, "configure_daily": True,
                                                       "tz": "Asia/Dubai", "criteria": {"daily_search": True}})
    loop = peek_loop(store)
    assert loop["enabled"] and loop["schedule"]["hour"] == 9
    assert loop["schedule"]["tz"] == "Asia/Dubai"
    assert store.load_profile()["talents"] == []
    with patch("navin.career.prospecting._mission_source", return_value=[offer()]), \
         patch("navin.career.prospecting._candidates", return_value=[candidate()]) as search_profiles, \
         patch("navin.career.notify.deliver_alert") as notify:
        first = maybe_tick(store, now=loop["next_due"], watch_fn=lambda _: {"count": 0})
        assert first["did_work"]
        next_due = peek_loop(store)["next_due"]
        assert next_due > loop["next_due"]
        # No new search results tomorrow: the saved candidate still matches the offer.
        search_profiles.return_value = []
        second = maybe_tick(store, now=next_due, watch_fn=lambda _: {"count": 0})
        assert second["did_work"]
    fresh_store = CareerStore(store.root)
    state = _state(fresh_store)
    assert len(fresh_store.load_opportunities()) == 1
    assert len(state["candidates"]) == 1
    assert len(state["runs"]) == 2
    assert state["runs"][0]["offers"] == 1 and state["runs"][0]["new_profiles"] == 1
    assert state["runs"][1]["offers"] == 0 and state["runs"][1]["new_profiles"] == 0
    assert state["runs"][1]["matched_offers"] == 1
    assert state["matches"]["job-one"]["results"][0]["score"] > 0
    assert sum(call.kwargs.get("event_type") == "company_search" for call in notify.call_args_list) == 2
    handle_prospecting(store, "prospecting_config", {"activate_company": True, "configure_daily": True,
                                                       "criteria": {"daily_search": False}})
    assert not peek_loop(store)["enabled"]


def test_public_profile_search_works_without_api_credentials(store):
    from navin.career.prospecting import _web

    hit = {"title": "Sam - Infirmier - Open to work", "url": "https://linkedin.com/in/sam", "snippet": "Disponible pour une mission de soins au Maroc"}
    with patch("navin.career.collect._search_ddgs", return_value=[hit]) as search:
        assert _web(store, "public_web", "Infirmier Maroc") == [hit]
        profiles = _candidates(store, "public_web", _state(store)["criteria"])
    assert profiles and profiles[0]["url"] == hit["url"]
    assert all(call.kwargs.get("strict") for call in search.call_args_list)


def test_company_without_cv_and_all_requested_markets(store):
    assert store.load_profile()["wizard_complete"]
    assert store.load_profile()["talents"] == []
    codes = ["SA", "OM", "AE", "BH", "QA", "KW", "MA"]
    handle_prospecting(store, "prospecting_config", {"criteria": {"countries": codes, "city": "Dubai"}})
    assert _state(store)["criteria"]["countries"] == codes
    assert len(prospecting_snapshot(store)["platform_catalog"]) == 33


def test_partial_failures_dont_lose_results_and_cooldown_is_used(store):
    store.save_secret("CAREER_SERPAPI_KEY", "test-search-key")
    def missions(_, source, criteria):
        if source == "linkedin":
            raise CareerError("quota")
        return [offer()]
    with patch("navin.career.prospecting._mission_source", side_effect=missions), patch("navin.career.prospecting._candidates", return_value=[candidate()]):
        handle_prospecting(store, "prospecting_search", {})
    state = _state(store)
    assert state["last_run"]["status"] == "partial"
    assert len(store.load_opportunities()) == 1
    assert len(state["candidates"]) == 1
    assert len(state["matches"]["job-one"]["results"]) == 1
    with patch("navin.career.prospecting._mission_source", return_value=[]), patch("navin.career.prospecting._candidates", return_value=[]):
        handle_prospecting(store, "prospecting_search", {})
    assert any(s["status"] == "cooldown" for s in _state(store)["last_run"]["sources"])


def test_each_country_has_its_own_query(store):
    store.save_secret("CAREER_SERPAPI_KEY", "test-search-key")
    handle_prospecting(store, "prospecting_config", {"criteria": {"countries": ["SA", "OM", "AE", "BH", "QA", "KW", "MA"], "sources": ["google_jobs"]}})
    with patch("navin.career.prospecting._mission_source", return_value=[]) as search, patch("navin.career.prospecting._candidates", return_value=[]):
        handle_prospecting(store, "prospecting_search", {})
    assert {call.args[2]["countries"][0] for call in search.call_args_list} == {"SA", "OM", "AE", "BH", "QA", "KW", "MA"}


def test_saved_profiles_survive_empty_refresh_and_keep_cv(store):
    state, match = seed(store)
    state["candidates"][0]["cv"] = {"name": "cv.pdf"}
    match.update({"stage": "contacted", "interest": "confirmed", "note": "Appelé"})
    _save(store, state)
    with patch("navin.career.prospecting._mission_source", return_value=[]), patch("navin.career.prospecting._candidates", return_value=[candidate()]):
        handle_prospecting(store, "prospecting_search", {})
    state = _state(store)
    assert state["candidates"][0]["cv"]["name"] == "cv.pdf"
    assert state["matches"]["job-one"]["results"][0]["note"] == "Appelé"


def test_reuse_does_not_call_network_and_honors_budget(store):
    state, _ = seed(store)
    state["candidates"].append(candidate(id="expensive", daily_rate=900, currency="EUR"))
    state["criteria"]["buy_rate_max"] = 500
    _save(store, state)
    with patch("navin.career.prospecting._web", side_effect=AssertionError("network")):
        handle_prospecting(store, "prospecting_reuse", {"id": "job-one"})
    assert [r["candidate"]["id"] for r in _state(store)["matches"]["job-one"]["results"]] == ["person-1"]


def test_scoring_is_explainable_and_not_it_only(store):
    criteria = _state(store)["criteria"]
    assert score_candidate(candidate(), offer(), criteria)["score"] == 100
    unrelated = candidate(headline="Développeur", snippet="Java", skills=["Java"], country="FR")
    assert score_candidate(unrelated, offer(), criteria)["score"] == 0


def test_indexed_profiles_reject_noise_and_negative_availability(store):
    hits = [{"title": "Sam infirmier", "url": "https://linkedin.com/in/sam", "snippet": "Disponible pour soins Maroc"},
            {"title": "Job", "url": "https://linkedin.com/jobs/view/1", "snippet": "available"},
            {"title": "Fake", "url": "https://linkedin.com.evil.test/in/1", "snippet": "available"},
            {"title": "Busy", "url": "https://linkedin.com/in/busy", "snippet": "not available"}]
    with patch("navin.career.prospecting._web", return_value=hits):
        result = _candidates(store, "serpapi", _state(store)["criteria"])
    assert len(result) == 1
    assert result[0]["signal"] == "declared"
    assert "interest" not in result[0]


def test_keys_never_return_in_snapshot_or_profile(store):
    handle_prospecting(store, "prospecting_config", {"keys": {"serpapi": "secret-test-value"}})
    assert prospecting_snapshot(store)["keys"]["serpapi"]
    assert "secret-test-value" not in json.dumps(prospecting_snapshot(store))
    assert "secret-test-value" not in store.profile_path.read_text()
    assert store.secrets_path.stat().st_mode & 0o777 == 0o600


def test_placement_and_contract_need_confirmed_interest(store):
    seed(store)
    with pytest.raises(CareerError):
        handle_prospecting(store, "prospecting_dossier", {"id": "job-one", "candidate_id": "person-1", "stage": "placed"})
    handle_prospecting(store, "prospecting_dossier", {"id": "job-one", "candidate_id": "person-1", "stage": "placed", "interest": "confirmed", "availability": "confirmed"})
    with pytest.raises(CareerError):
        handle_prospecting(store, "prospecting_dossier", {"id": "job-one", "candidate_id": "person-1", "stage": "contracted"})


@pytest.mark.parametrize("module", ["career", "tenders"])
def test_archive_reset_is_recoverable_and_preserves_secrets(tmp_path, module):
    store = CareerStore(tmp_path / module) if module == "career" else TenderStore(tmp_path / module)
    store.profile_path.write_text('{"name":"Old","wizard_complete":true}')
    store.secrets_path.write_text('{"key":"private"}')
    store.loop_path.write_text('{"enabled":true}')
    store.files_dir.mkdir()
    (store.files_dir / "document.txt").write_text("archived document")
    if module == "career":
        state = _state(store)
        state["candidates"] = [candidate()]
        _save(store, state)
        (store.root / "talent-files").mkdir()
        (store.root / "talent-files" / "cv.pdf").write_bytes(b"%PDF-1.4")
    receipt = archive_reset(store, module=module, confirmed=True)
    if module == "career":
        assert not store.load_loop()["enabled"]
        assert store.profile_path.read_text() == '{"name":"Old","wizard_complete":true}'
    else:
        assert not store.loop_path.exists()
        assert not store.profile_path.exists()
    assert store.secrets_path.read_text() == '{"key":"private"}'
    bundle = download_archive(store.root, receipt["id"])
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(bundle["data_b64"]))) as archive:
        assert archive.read("files/document.txt") == b"archived document"
        assert "secrets.json" not in archive.namelist()
        if module == "career":
            assert archive.read("talent-files/cv.pdf") == b"%PDF-1.4"
    assert len(list_archives(store.root)) == 1
    if module == "career":
        assert len(_state(store)["candidates"]) == 1
        assert (store.root / "talent-files" / "cv.pdf").exists()


def test_reset_requires_confirmation_and_refuses_running_cycle(store):
    with pytest.raises(CareerError):
        archive_reset(store, module="career", confirmed=False)
    with FileLock(str(store.root / "desk.lock")):
        with pytest.raises(CareerError):
            archive_reset(store, module="career", confirmed=True)
    assert store.profile_path.exists()


class SMTP:
    messages = []
    def login(self, *args): pass
    def mail(self, *args): return 250, b"OK"
    def rcpt(self, *args): return 250, b"OK"
    def data(self, message):
        self.messages.append(message)
        return 250, b"OK"
    def quit(self): pass
    def close(self): pass


def mail_setup(store):
    store.save_profile({"mailbox": {"enabled": True, "sender_email": "agency@example.com", "sender_name": "Agence",
                                   "smtp_host": "smtp.example.com", "smtp_username": "agency@example.com"}})
    store.save_secret("CAREER_SMTP_PASSWORD", "test-password")
    state, match = seed(store)
    state["criteria"].update({"auto_contact": True, "auto_present": True, "sale_rate": 600, "margin_percent": 20, "max_per_day": 5})
    _save(store, state)
    SMTP.messages = []
    return state, match


def test_outreach_is_idempotent_and_price_uses_margin(store):
    state, match = mail_setup(store)
    assert proposal_rate({"sale_rate": 450, "margin_percent": 20}, {"purchase_rate": 400}) == 500
    for _ in range(2):
        assert _send(store, state, match, offer(), "candidate", smtp_factory=lambda _: SMTP())["status"] == "accepted"
    assert len(SMTP.messages) == 1
    with pytest.raises(CareerError):
        _send(store, state, match, offer(), "client", smtp_factory=lambda _: SMTP())


def reply(record, text, sender="sam@example.com", cv=True):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "agency@example.com"
    message["In-Reply-To"] = record["message_id"]
    message.set_content(text)
    if cv:
        message.add_attachment(candidate_pdf(), maintype="application", subtype="pdf", filename="cv.pdf")
    return message


def candidate_pdf():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 50 750 Td (Infirmier soins Maroc. Coordination clinique.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("fixed_project", [False, True])
def test_candidate_consent_and_genuine_cv_enable_automatic_client_email(store, fixed_project):
    state, match = mail_setup(store)
    if fixed_project:
        store.upsert_opportunities([{**offer(), "price_model": "fixed", "budget": 20000, "currency": "EUR", "need_type": "rfp"}])
    receipt = _send(store, state, match, offer(), "candidate", smtp_factory=lambda _: SMTP())
    _save(store, state)
    text = f"ACCORD {_ref('job-one', 'person-1')}\nTJM: 400 EUR / jour\nDisponible le mois prochain."
    message = reply(receipt, text)
    assert record_sourcing_reply(store, {"notifications": {}}, message, receipt, text, "reply-1")
    with patch("navin.career.mailbox.sync_mailbox", return_value={"status": "complete"}), patch("navin.career.mail.flush_mail_notifications"):
        run_sourcing_cycle(store, smtp_factory=lambda _: SMTP())
    saved = _state(store)["matches"]["job-one"]["results"][0]
    assert saved["stage"] == "submitted"
    assert saved["purchase_rate"] == 400
    assert saved["client_mail"]["recipient"] == "client@example.com"
    if fixed_project:
        assert "chiffrage du projet reste à établir" in saved["client_mail"]["body"]
        assert "TJM proposé" not in saved["client_mail"]["body"]
    assert len(SMTP.messages) == 2
    assert b"application/pdf" in SMTP.messages[-1]
    assert read_candidate_cv(store, saved["candidate"])["name"] == "cv.pdf"


def test_quoted_agreement_wrong_sender_and_negative_reply_do_not_authorize(store):
    state, match = mail_setup(store)
    receipt = _send(store, state, match, offer(), "candidate", smtp_factory=lambda _: SMTP())
    text = f"Merci, j'ai une question.\nACCORD {receipt['application_id']}"
    assert record_sourcing_reply(store, {"notifications": {}}, reply(receipt, text), receipt, text, "reply-x")
    assert not _state(store)["matches"]["job-one"]["results"][0].get("sharing_consent")
    text = f"ACCORD {receipt['application_id']}"
    assert not record_sourcing_reply(store, {"notifications": {}}, reply(receipt, text, sender="attacker@example.com"), receipt, text, "reply-y")


def test_uncertain_smtp_result_is_never_retried_automatically(store):
    class Uncertain(SMTP):
        def data(self, message):
            self.messages.append(message)
            raise TimeoutError()
    state, match = mail_setup(store)
    first = _send(store, state, match, offer(), "candidate", smtp_factory=lambda _: Uncertain())
    assert first["status"] == "unknown"
    _send(store, state, match, offer(), "candidate", smtp_factory=lambda _: SMTP())
    assert len(SMTP.messages) == 1


def cycle(store):
    with patch("navin.career.mailbox.sync_mailbox", return_value={"status": "complete"}), patch("navin.career.mail.flush_mail_notifications"):
        return run_sourcing_cycle(store, smtp_factory=lambda _: SMTP())


def latest_record(store, kind):
    from navin.career.mail import load_mail_state

    return next(r for r in reversed(list(load_mail_state(store)["outbox"].values())) if r["sourcing_kind"] == kind)


def receive(store, kind, text, identity, *, cv=False, dossier=False):
    record = latest_record(store, kind)
    message = reply(record, text, sender=record["recipient"], cv=cv)
    if dossier:
        message.add_attachment(candidate_pdf(), maintype="application", subtype="pdf", filename="dossier_competences.pdf")
    with patch("navin.tenders.ai.ask", return_value=("", "")):
        assert record_sourcing_reply(store, {"notifications": {}}, message, record, text, identity)
    return message


def test_full_company_flow_natural_reply_documents_cc_and_interview(store):
    from datetime import datetime, timedelta, timezone
    from email import policy
    from email.parser import BytesParser

    from navin.career.mailbox import _thread

    state, _ = mail_setup(store)
    state["criteria"].update({"max_per_day": 25, "require_dossier": True,
                              "candidate_cc": ["talent-team@example.com"], "client_cc": ["sales@example.com"]})
    _save(store, state)
    cycle(store)
    receive(store, "candidate", "Je suis intéressé.\nDisponible immédiatement.\nVous pouvez transmettre mon CV au client.\nTJM: 400 EUR", "candidate-agrees", cv=True, dossier=True)
    cycle(store)
    proposed = BytesParser(policy=policy.default).parsebytes(SMTP.messages[-1])
    assert proposed["Cc"] == "sales@example.com"
    assert len(list(proposed.iter_attachments())) == 2
    assert "600.00 EUR" in proposed.get_body(preferencelist=("plain",)).get_content()
    slot = (datetime.now(timezone.utc) + timedelta(days=7)).replace(second=0, microsecond=0).isoformat()
    receive(store, "client", f"Nous souhaitons organiser un entretien.\n{slot}\nEn visioconférence, lien à suivre.", "client-ok")
    cycle(store)
    invitation = latest_record(store, "candidate_interview")
    assert invitation["cc"] == ["talent-team@example.com"]
    confirmation = receive(store, "candidate_interview", "Je confirme ce créneau.", "candidate-slot")
    confirmation["References"] = latest_record(store, "candidate")["message_id"] + " " + invitation["message_id"]
    assert _thread(confirmation, {"first": latest_record(store, "candidate"), "next": invitation})["sourcing_kind"] == "candidate_interview"
    cycle(store)
    cycle(store)
    saved = _state(store)["matches"]["job-one"]["results"][0]
    assert saved["interview"]["status"] == "confirmed"
    assert saved["stage"] == "interview"
    assert "soins" in saved["preparation"]
    assert "Préparation" in latest_record(store, "candidate_preparation")["body"]
    assert len(SMTP.messages) == 5
    cycle(store)
    assert len(SMTP.messages) == 5


def test_missing_cv_followup_is_not_repeated_every_cycle(store):
    mail_setup(store)
    cycle(store)
    receive(store, "candidate", f"ACCORD {_ref('job-one', 'person-1')}\nTJM: 400 EUR\nDisponible immédiatement.", "needs-docs")
    cycle(store)
    cycle(store)
    assert len(SMTP.messages) == 2
    assert "CV en pièce jointe" in latest_record(store, "candidate_followup")["body"]
    receive(store, "candidate_followup", "Voici mon CV.", "got-docs", cv=True)
    cycle(store)
    assert _state(store)["matches"]["job-one"]["results"][0]["stage"] == "submitted"


def test_multiple_candidates_and_withdrawal_do_not_stop_other_dossiers(store):
    state, _ = mail_setup(store)
    state["candidates"].append(candidate(id="person-2", name="Alex", email="alex@example.com"))
    _match(store, state, offer())
    _save(store, state)
    cycle(store)
    assert len(SMTP.messages) == 2
    receive(store, "candidate", "Je ne suis pas disponible.", "withdrawal")
    rows = _state(store)["matches"]["job-one"]["results"]
    assert sorted(r["stage"] for r in rows) == ["contacted", "rejected"]
    cycle(store)
    assert len(SMTP.messages) == 2


def test_reply_ai_requires_verbatim_evidence_and_ignores_quoted_consent():
    from navin.career.sourcing_replies import extract_reply

    with patch("navin.tenders.ai.ask", return_value=(json.dumps({"evidence": {"consent": "I authorize sharing my CV"}}), "test")):
        result = extract_reply("Une question.\n> I authorize sharing my CV", "candidate", "REF", ai=True)
    assert not result["consent"]
    result = extract_reply("Je suis disponible vendredi à 10h.", "candidate_interview", "REF")
    assert not result["slot_accept"]


def test_cross_platform_dedup_keeps_links_and_pipeline_without_merging_other_city(store):
    description = "Nous recherchons un infirmier coordinateur pour une équipe de soins à domicile. " * 5
    a = {**offer(), "company": "Clinique", "location": "Casablanca", "description": description, "stage": "interview"}
    b = {**a, "id": "second-board", "source": "collective", "url": "https://collective.work/jobs/other", "stage": "discovered", "application_email": ""}
    store.upsert_opportunities([a, b])
    rows = store.load_opportunities()
    assert len(rows) == 1
    assert rows[0]["stage"] == "interview"
    assert rows[0]["application_email"] == "client@example.com"
    assert len(rows[0]["source_links"]) == 2
    store.upsert_opportunities([{**b, "id": "different-city", "url": "https://example.com/jobs/other", "location": "Rabat"}])
    assert len(store.load_opportunities()) == 2


def test_freelancescope_cards_and_original_source_are_real_job_data():
    from navin.career.freelancescope import enrich_mission, parse_missions

    html = '<article><h3><a href="/missions/nurse-123">Infirmier</a></h3><p>Clinique · Santé</p><ul><li>Paris</li></ul></article>'
    rows = parse_missions(html + '<h3><a href="/missions/categorie/sante">Santé</a></h3>')
    assert len(rows) == 1
    assert rows[0]["title"] == "Infirmier"
    detail = '<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": "Infirmier", "description": "Soins à domicile"}) + '</script><a href="https://candidat.francetravail.fr/offres/recherche/detail/123">Source</a>'
    enriched = enrich_mission(rows[0], detail)
    assert enriched["description"] == "Soins à domicile"
    assert len(enriched["source_links"]) == 2


def test_internal_pool_is_available_to_agent_and_matching(store):
    store.save_profile({"talents": [{"id": "nurse", "name": "Internal", "headline": "Infirmier", "stack": ["soins"], "email": "internal@example.com", "residence_country": "MA"}]})
    state = _state(store)
    _match(store, state, offer())
    assert state["matches"]["job-one"]["results"][0]["candidate"]["email"] == "internal@example.com"
    state["criteria"]["internal_talents"] = False
    _match(store, state, offer())
    assert not state["matches"]["job-one"]["results"]


def test_transport_refusal_can_retry_without_duplicate_data(store):
    from navin.career.mail import load_mail_state, save_mail_state

    class Refused(SMTP):
        def rcpt(self, *args):
            return 421, b"try later"

    state, match = mail_setup(store)
    first = _send(store, state, match, offer(), "candidate", smtp_factory=lambda _: Refused())
    assert first["status"] == "failed"
    assert not SMTP.messages
    mailbox = load_mail_state(store)
    for receipt in mailbox["outbox"].values():
        receipt["retry_at"] = 0
    save_mail_state(store, mailbox)
    assert _send(store, state, match, offer(), "candidate", smtp_factory=lambda _: SMTP())["status"] == "accepted"
    assert len(SMTP.messages) == 1
    assert len(load_mail_state(store)["sourcing_attempts"]) == 1


def test_company_pause_during_authentication_prevents_data(store):
    state, match = mail_setup(store)
    store.save_loop({**store.load_loop(), "enabled": True})

    class Pausing(SMTP):
        def login(self, *args):
            store.save_loop_intent({"enabled": False})

    receipt = _send(store, state, match, offer(), "candidate", smtp_factory=lambda _: Pausing(), automatic=True)
    assert receipt["status"] == "failed"
    assert not SMTP.messages


def test_client_budget_currency_must_be_comparable(store):
    mail_setup(store)
    cycle(store)
    receive(store, "candidate", f"ACCORD {_ref('job-one', 'person-1')}\nTJM: 400 EUR\nDisponible immédiatement.", "currency-reply", cv=True)
    store.update_opportunity("job-one", {"daily_rate_max": 800, "currency": "USD"})
    cycle(store)
    assert len(SMTP.messages) == 1
    assert "currency" in _state(store)["matches"]["job-one"]["results"][0]["next_action"]


def test_human_interview_dates_require_timezone():
    from datetime import datetime

    from navin.career.sourcing_replies import reply_slot

    year = datetime.now().year + 1
    assert reply_slot(f"Entretien le 15/02/{year} à 14h30 heure de Dubai") == f"{year}-02-15T14:30:00+04:00"
    assert reply_slot(f"Entretien le 15/02/{year} à 14h30") == ""


def test_priority_sources_cover_all_countries_before_extra_boards(store):
    from navin.career.prospecting_catalog import mission_catalog

    store.save_secret("CAREER_SERPAPI_KEY", "test-search-key")
    countries = ["SA", "OM", "AE", "BH", "QA", "KW", "MA"]
    handle_prospecting(store, "prospecting_config", {"criteria": {"track": "jobs", "countries": countries, "sources": [s["id"] for s in mission_catalog()]}})
    with patch("navin.career.prospecting._mission_source", return_value=[]) as missions, patch("navin.career.prospecting._candidates", return_value=[]) as profiles:
        handle_prospecting(store, "prospecting_search", {})
    assert {call.args[2]["countries"][0] for call in missions.call_args_list if call.args[1] == "linkedin"} == set(countries)
    assert {call.args[2]["countries"][0] for call in missions.call_args_list if call.args[1] == "google_jobs"} == set(countries)
    assert {call.args[2]["countries"][0] for call in profiles.call_args_list} == set(countries)


def test_stop_remains_available_during_api_search(store):
    from navin.webui.career_api import handle_career_action

    store.save_loop({**store.load_loop(), "enabled": True})
    with patch("navin.webui.career_api._store", return_value=store), FileLock(str(store.root / "lifecycle.lock")):
        result = handle_career_action("stop", {})
    assert not result["loop"]["enabled"]
