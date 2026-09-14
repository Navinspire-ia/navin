import base64
from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest

from navin.browser_import import clean_record, import_record, source_url
from navin.career.errors import CareerError
from navin.career.prospecting import _state, handle_prospecting
from navin.career.store import CareerStore
from navin.leads.store import LeadsStore
from navin.tenders.store import TenderStore
from navin.webui.browser_bridge import BrowserBridge


def record(**changes):
    return {"request_id": uuid4().hex, "kind": "mission", "title": "Data Engineer", "description": "Mission freelance Python SQL pour une plateforme data.",
            "country": "FR", "remote": "remote", "currency": "EUR", "daily_rate_min": 300, "daily_rate_max": 500,
            "posted_at": date.today().isoformat(), "url": "https://example.com/mission/123", "skills": ["Python", "SQL"],
            "capture_mode": "selection", "reviewed": True, **changes}


@pytest.fixture
def bridge(tmp_path):
    roots = {key: tmp_path / key for key in ("career", "leads", "tenders")}
    handle_prospecting(CareerStore(roots["career"]), "prospecting_config", {"criteria": {
        "roles": ["Data Engineer"], "skills": ["Python", "SQL"], "countries": ["FR"], "work_mode": "any", "track": "both",
        "sale_rate_remote": 300, "sale_rate_onsite": 650, "max_age_days": 30, "daily_search": False,
    }})
    return BrowserBridge(tmp_path / "bridge", roots=roots)


def pair(bridge):
    code = bridge.handle("create", {"label": "Chrome bureau"}, admin=True)["code"]
    result = bridge.handle("pair", {"code": code})
    return result["token"], result["device"]["id"]


def test_local_auto_pairing_needs_no_code_and_remote_still_requires_one(bridge):
    local = bridge.handle("pair", {"label": "Chrome auto"}, local=True)
    assert local["auto"] is True and local["token"]
    status = bridge.handle("status", {}, token=local["token"])
    assert status["device"]["label"] == "Chrome auto"
    with pytest.raises(CareerError, match="Code invalide"):
        bridge.handle("pair", {"label": "Distant"})
    remote = bridge.handle("create", {"label": "Distant"}, admin=True)
    paired = bridge.handle("pair", {"code": remote["code"]})
    assert paired["auto"] is False and paired["token"]


def test_import_accepts_more_than_twenty_records(bridge):
    token, _ = pair(bridge)
    rows = [record(title=f"Mission {index}") for index in range(25)]
    result = bridge.handle("import", {"records": rows}, token=token)
    assert len(result["results"]) == 25


def test_pairing_is_single_use_expires_and_never_exposes_stored_tokens(bridge):
    created = bridge.handle("create", {}, admin=True)
    result = bridge.handle("pair", {"code": created["code"]})
    with pytest.raises(CareerError, match="déjà utilisé"):
        bridge.handle("pair", {"code": created["code"]})
    second = bridge.handle("create", {}, admin=True)
    with patch("navin.webui.browser_bridge.time.time", return_value=second["expires_at"] + 1):
        with pytest.raises(CareerError, match="expiré"):
            bridge.handle("pair", {"code": second["code"]})
    assert result["token"] not in bridge.path.read_text()
    assert created["code"] not in bridge.path.read_text()
    assert "hash" not in str(bridge.handle("devices", {}, admin=True))
    assert bridge.path.stat().st_mode & 0o777 == 0o600
    assert bridge.root.stat().st_mode & 0o777 == 0o700


def test_import_only_token_cannot_manage_accounts_and_revocation_is_immediate(bridge):
    token, identity = pair(bridge)
    for action in ("create", "revoke", "devices", "package", "send_email", "search"):
        with pytest.raises(CareerError):
            bridge.handle(action, {}, token=token)
    bridge.handle("revoke", {"id": identity}, admin=True)
    for action in ("status", "criteria", "analyze", "import"):
        with pytest.raises(CareerError, match="révoqué"):
            bridge.handle(action, {"records": [record()]}, token=token)


def test_invalid_pair_codes_are_rate_limited(bridge):
    for _ in range(12):
        with pytest.raises(CareerError) as error:
            bridge.handle("pair", {"code": "bad"})
        assert error.value.status == 401
    with pytest.raises(CareerError) as error:
        bridge.handle("pair", {"code": "bad"})
    assert error.value.status == 429


def test_retries_are_idempotent_and_do_not_overwrite_changed_records(bridge):
    token, _ = pair(bridge)
    row = record()
    first = bridge.handle("import", {"records": [row]}, token=token)
    assert first["results"][0]["status"] == "imported"
    assert bridge.handle("import", {"records": [row]}, token=token) == first
    assert len(CareerStore(bridge.roots["career"]).load_opportunities()) == 1
    with pytest.raises(CareerError, match="fiche a changé"):
        bridge.handle("import", {"records": [{**row, "title": "Other"}]}, token=token)
    again = bridge.handle("import", {"records": [{**row, "request_id": uuid4().hex}]}, token=token)
    assert again["results"][0]["status"] == "updated"
    assert "description" not in bridge.path.read_text()


@pytest.mark.parametrize(("changes", "reason"), [
    ({"remote": "hybrid", "daily_rate_min": 649, "daily_rate_max": 700}, "rate"),
    ({"daily_rate_min": 299}, "rate"), ({"country": "US"}, "country"),
    ({"skills": [], "description": "Une mission de développement disponible."}, "skills"),
    ({"title": "Data Analyst"}, "role"), ({"remote": "", "description": "Python SQL mission freelance."}, "work_mode_unknown"),
    ({"posted_at": ""}, "date_unknown"),
])
def test_extension_import_uses_real_business_filters(bridge, changes, reason):
    result = import_record(record(**changes), "browser", roots=bridge.roots)
    assert result["status"] == "excluded" and result["reason"] == reason
    assert CareerStore(bridge.roots["career"]).load_opportunities() == []


def test_profiles_leads_jobs_and_tenders_reach_the_right_stores(bridge):
    rows = [record(kind="candidate", name="Alex Martin", headline="Data Engineer", availability="Disponible selon son profil", url="https://www.malt.fr/profile/alex"),
            record(kind="lead", name="Sam Martin", headline="CTO", company="Example", email="sam@example.com", url="https://www.linkedin.com/in/sam"),
            record(kind="job", title="Data Engineer CDI", url="https://example.com/jobs/9", daily_rate_min=None, daily_rate_max=None, salary_min=60000),
            record(kind="tender", title="RFP plateforme Data", need_type="rfp", company="Acheteur", url="https://example.com/rfp/8", budget_min=20000)]
    for row in rows:
        result = import_record(row, "browser", roots=bridge.roots)
        assert result["status"] == "imported"
    candidate = _state(CareerStore(bridge.roots["career"]))["candidates"][0]
    assert candidate["signal"] == "unknown" and "consent" not in candidate
    lead = LeadsStore(bridge.roots["leads"]).load_leads()[0]
    assert lead["email_status"] == "unverified" and lead["domain"] == ""
    assert "sequence" not in lead
    assert CareerStore(bridge.roots["career"]).load_opportunities()[0]["track"] == "jobs"
    assert TenderStore(bridge.roots["tenders"]).load_tenders()[0]["budget"] == 20000


def test_distinct_people_with_no_email_are_not_merged(bridge):
    for name in ("Alex", "Sam"):
        row = record(kind="lead", name=name, company="Example", url="https://www.linkedin.com/in/" + name)
        import_record(row, "browser", roots=bridge.roots)
        import_record(row, "browser", roots=bridge.roots)
    assert len(LeadsStore(bridge.roots["leads"]).load_leads()) == 2


def test_only_whitelisted_fields_are_imported_and_source_tokens_are_removed():
    row = clean_record(record(url="https://example.com/job?id=123&token=secret&code=oauth#access_token=secret", cookies="secret", password="secret", stage="applied", consent=True))
    assert row["url"] == "https://example.com/job?id=123"
    assert not {"cookies", "password", "stage", "consent"}.intersection(row)
    assert source_url("https://example.com/#/mission/12?id=34&code=secret") == "https://example.com/#/mission/12?id=34"


@pytest.mark.parametrize("changes", [{"reviewed": False}, {"daily_rate_min": float("nan")}, {"daily_rate_min": -1},
                                      {"daily_rate_min": 800}, {"posted_at": "2026-02-31"}, {"url": "javascript:alert(1)"},
                                      {"url": "https://user:secret@example.com/"}, {"email": "not an email"},
                                      {"url": "https://www.linkedin.com/in/abc", "capture_mode": "page"}])
def test_invalid_records_are_rejected_before_storage(changes):
    with pytest.raises(CareerError):
        clean_record(record(**changes))


def test_device_can_only_see_its_own_receipts(bridge):
    token, _ = pair(bridge)
    other, _ = pair(bridge)
    bridge.handle("import", {"records": [record()]}, token=token)
    assert len(bridge.handle("status", {}, token=token)["receipts"]) == 1
    assert bridge.handle("status", {}, token=other)["receipts"] == []


@pytest.mark.parametrize("browser", ["chrome", "edge", "firefox"])
def test_browser_package_download_preserves_bytes_and_name(bridge, tmp_path, monkeypatch, browser):
    import navin.webui.browser_bridge as module

    monkeypatch.setattr(module, "__file__", str(tmp_path / "navin" / "webui" / "browser_bridge.py"))
    packages = tmp_path / "navin" / "browser_extension"
    packages.mkdir(parents=True)
    content = b"PK\x03\x04\x00\xff"
    (packages / f"{browser}.zip").write_bytes(content)
    state = bridge.handle("devices", {}, admin=True)
    assert state["downloads"] == {name: name == browser for name in ("chrome", "edge", "firefox")}
    result = bridge.handle("package", {"browser": browser}, admin=True)["package"]
    assert result["name"] == f"navin-import-{browser}.zip"
    assert result["mime"] == "application/zip"
    assert base64.b64decode(result["data"]) == content


def test_analysis_reads_only_matching_preferences_and_requires_pairing(bridge):
    token, _ = pair(bridge)
    with pytest.raises(CareerError):
        bridge.handle("criteria", {})
    config = bridge.handle("criteria", {}, token=token)
    assert config["criteria"]["roles"] == ["Data Engineer"]
    assert config["profiles"]["skills"] == ["Python", "SQL"]
    assert not {"company", "signature", "candidate_cc", "auto_contact"}.intersection(config["criteria"])
    assert not {"candidates", "matches", "keys", "profile"}.intersection(config)
    assert bridge.handle("status", {}, token=token)["analysis_version"] == 1


def test_analysis_recommends_profiles_without_importing_and_marks_duplicates(bridge):
    token, _ = pair(bridge)
    row = record(kind="candidate", name="Alex", headline="Data Engineer", reviewed=False,
                 description="Data Engineer Python SQL, France. TJM 450 EUR/jour.")
    result = bridge.handle("analyze", {"records": [row, {**row, "request_id": uuid4().hex}]}, token=token)
    first, duplicate = result["results"]
    assert first["status"] == "recommended" and first["score"] == 100
    assert first["destination"] == "candidates" and first["record"]["kind"] == "candidate"
    assert duplicate["status"] == "duplicate"
    assert _state(CareerStore(bridge.roots["career"]))["candidates"] == []
    assert bridge.handle("status", {}, token=token)["receipts"] == []
    bridge.handle("import", {"records": [{**first["record"], "reviewed": True}], "criteria_revision": result["revision"]}, token=token)
    assert bridge.handle("analyze", {"records": [row]}, token=token)["results"][0]["status"] == "duplicate"
    candidate = _state(CareerStore(bridge.roots["career"]))["candidates"][0]
    assert candidate["signal"] == "unknown"


def test_analysis_uses_candidate_buying_ceiling_not_mission_selling_floor(bridge):
    token, _ = pair(bridge)
    handle_prospecting(CareerStore(bridge.roots["career"]), "prospecting_config", {"criteria": {"buy_rate_max": 400}})
    row = record(kind="candidate", name="Alex", headline="Data Engineer", daily_rate_min=450, daily_rate_max=500)
    result = bridge.handle("analyze", {"records": [row]}, token=token)["results"][0]
    assert result["status"] == "excluded"
    assert any(reason["key"] == "buy_rate" and reason["status"] == "mismatch" for reason in result["reasons"])


def test_unknown_candidate_skills_country_and_currency_are_not_invented(bridge):
    token, _ = pair(bridge)
    handle_prospecting(CareerStore(bridge.roots["career"]), "prospecting_config", {"criteria": {"buy_rate_max": 600}})
    row = record(kind="candidate", name="Alex", headline="Data Engineer", description="Data Engineer Python",
                 skills=[], country="", currency="", daily_rate_min=400, daily_rate_max=None)
    result = bridge.handle("analyze", {"records": [row]}, token=token)["results"][0]
    assert result["status"] == "review"
    assert result["record"]["country"] == "" and result["record"]["currency"] == ""
    assert result["record"]["skills"] == ["Python"]
    assert {reason["key"] for reason in result["reasons"] if reason["status"] == "unknown"} >= {"skills", "country", "buy_rate"}


def test_analysis_respects_profile_scope_and_active_search_mode(bridge):
    token, _ = pair(bridge)
    store = CareerStore(bridge.roots["career"])
    handle_prospecting(store, "prospecting_config", {"criteria": {
        "mode": "profiles", "profile_roles": ["Data Analyst"], "profile_skills": ["Excel"], "profile_countries": ["BE"]}})
    row = record(kind="candidate", name="Sam", headline="Data Analyst", description="Data Analyst Excel en Belgique",
                 country="BE", skills=["Excel"])
    results = bridge.handle("analyze", {"records": [row, record()]}, token=token)["results"]
    assert results[0]["status"] == "recommended"
    assert results[1]["status"] == "excluded"
    assert any(r["key"] == "mode" for r in results[1]["reasons"])


def test_reanalysis_is_required_when_criteria_changed(bridge):
    token, _ = pair(bridge)
    row = record()
    analyzed = bridge.handle("analyze", {"records": [row]}, token=token)
    handle_prospecting(CareerStore(bridge.roots["career"]), "prospecting_config", {"criteria": {"countries": ["BE"]}})
    with pytest.raises(CareerError, match="critères Navin ont changé"):
        bridge.handle("import", {"records": [row], "criteria_revision": analyzed["revision"]}, token=token)
    assert CareerStore(bridge.roots["career"]).load_opportunities() == []


def test_invalid_analysis_rows_are_actionable_and_do_not_bypass_review(bridge):
    token, _ = pair(bridge)
    raw = record(reviewed=False)
    results = bridge.handle("analyze", {"records": [raw, record(url="javascript:alert(1)")]}, token=token)["results"]
    assert results[0]["status"] == "recommended"
    assert results[1]["status"] == "invalid"
    imported = bridge.handle("import", {"records": [raw]}, token=token)
    assert imported["results"][0]["status"] == "error"
    assert CareerStore(bridge.roots["career"]).load_opportunities() == []


def test_analysis_routes_employment_evidence_to_jobs(bridge):
    token, _ = pair(bridge)
    row = record(description="Data Engineer CDI Python SQL. Poste permanent full remote France.",
                 daily_rate_min=None, daily_rate_max=None)
    result = bridge.handle("analyze", {"records": [row]}, token=token)["results"][0]
    assert result["record"]["kind"] == "job"
    assert result["destination"] == "career" and result["status"] == "recommended"


def test_analysis_does_not_recommend_paused_roles(bridge):
    token, _ = pair(bridge)
    handle_prospecting(CareerStore(bridge.roots["career"]), "prospecting_config", {"criteria": {"role_priorities": {"Data Engineer": 0}}})
    rows = [record(), record(kind="candidate", name="Alex", headline="Data Engineer")]
    assert all(row["status"] == "excluded" for row in bridge.handle("analyze", {"records": rows}, token=token)["results"])
