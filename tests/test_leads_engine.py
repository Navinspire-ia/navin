"""Deterministic leads engine: normalize, validate, dedupe, qualify, export."""

from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path

from navin.agent.tools.leads import LeadsTool, LeadsToolConfig
from navin.leads import (
    Lead,
    dedupe_leads,
    export_leads,
    normalize_domain,
    normalize_email,
    normalize_leads,
    normalize_phone,
    qualify_tier,
    validate_lead,
)


class NormalizeTest:
    pass


def test_email_is_lowercased_and_validated() -> None:
    assert normalize_email("  John.Doe@Example.COM ") == "john.doe@example.com"
    assert normalize_email("mailto:a@b.io") == "a@b.io"
    assert normalize_email("not-an-email") == ""
    assert normalize_email("a@b") == ""


def test_phone_formats_to_e164_with_country_hint() -> None:
    assert normalize_phone("06 12 34 56 78", country="FR") == "+33612345678"
    assert normalize_phone("+1 (415) 555-2671") == "+14155552671"
    assert normalize_phone("00 44 20 7946 0958") == "+442079460958"
    assert normalize_phone("12345", country="FR") == ""  # too short after trunk drop
    assert normalize_phone("") == ""


def test_domain_reduces_to_registrable_host() -> None:
    assert normalize_domain("https://www.Example.com/path?x=1") == "example.com"
    assert normalize_domain("shop.example.co.uk") == "shop.example.co.uk"
    assert normalize_domain("") == ""


def test_validate_flags_crm_gaps() -> None:
    lead = Lead(company="Acme", website="acme.io", source="scrape").normalized()
    assert validate_lead(lead) == []
    bad = Lead(company="", website="", source="").normalized()
    issues = validate_lead(bad)
    assert "missing company" in issues
    assert "missing website/domain" in issues
    assert "missing source" in issues


def test_dedupe_merges_by_email_and_keeps_richest() -> None:
    rows = [
        {"company": "Acme", "website": "acme.io", "email": "sam@acme.io", "source": "a"},
        {"company": "Acme Inc", "website": "acme.io", "email": "SAM@acme.io",
         "phone": "+14155552671", "source": "b", "confidence": "high"},
    ]
    leads = normalize_leads(rows)
    unique, removed = dedupe_leads(leads)
    assert removed == 1
    assert len(unique) == 1
    merged = unique[0]
    assert merged.email == "sam@acme.io"
    assert merged.phone == "+14155552671"  # filled from the second row
    assert merged.confidence == "high"


def test_dedupe_by_person_and_domain_without_email() -> None:
    rows = [
        {"company": "Acme", "website": "acme.io", "person": "Sam Poe", "source": "a"},
        {"company": "Acme", "website": "acme.io", "person": "Sam Poe",
         "role": "CTO", "source": "b"},
    ]
    unique, removed = dedupe_leads(normalize_leads(rows))
    assert removed == 1
    assert unique[0].role == "CTO"


def test_qualify_assigns_tiers_by_completeness() -> None:
    strong = Lead(
        company="Acme", website="acme.io", email="s@acme.io", email_status="verified",
        phone="+14155552671", person="Sam Poe", role="CTO", signal="hiring",
        confidence="high", source="x",
    ).normalized()
    weak = Lead(company="Acme", website="acme.io", source="x").normalized()
    assert qualify_tier(strong) == "A"
    assert qualify_tier(weak) == "C"


def test_export_csv_is_atomic_and_has_canonical_header(tmp_path: Path) -> None:
    leads = normalize_leads(
        [{"company": "Acme", "website": "acme.io", "email": "s@acme.io", "source": "x"}]
    )
    out = tmp_path / "sales" / "leads.csv"
    info = export_leads(leads, "csv", out)
    assert info.count == 1
    assert out.is_file()
    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["company"] == "Acme"
    assert rows[0]["email"] == "s@acme.io"
    # No stray temp files left beside the target.
    assert [p.name for p in out.parent.iterdir()] == ["leads.csv"]


def test_export_json_roundtrips(tmp_path: Path) -> None:
    leads = normalize_leads([{"company": "Acme", "website": "acme.io", "source": "x"}])
    out = tmp_path / "leads.json"
    export_leads(leads, "json", out)
    data = json.loads(out.read_text())
    assert data[0]["domain"] == "acme.io"


def test_tool_pipeline_normalizes_dedupes_and_exports(tmp_path: Path) -> None:
    tool = LeadsTool(workspace=tmp_path, config=LeadsToolConfig())
    records = json.dumps(
        [
            {"company": "Acme", "website": "www.acme.io", "email": "SAM@acme.io",
             "phone": "06 12 34 56 78", "country": "FR", "source": "scrape"},
            {"company": "Acme Inc", "website": "acme.io", "email": "sam@acme.io",
             "role": "CTO", "source": "scrape", "confidence": "high"},
        ]
    )
    result = asyncio.run(
        tool.execute(action="pipeline", records=records, format="csv", path="sales/out.csv")
    )
    payload = json.loads(result)
    assert payload["export"]["count"] == 1  # deduped to one
    assert payload["deduped_removed"] == 1
    assert (tmp_path / "sales" / "out.csv").is_file()


def test_tool_validate_reports_gaps(tmp_path: Path) -> None:
    tool = LeadsTool(workspace=tmp_path, config=LeadsToolConfig())
    records = json.dumps([{"company": "", "website": "", "source": ""}])
    result = asyncio.run(tool.execute(action="validate", records=records))
    payload = json.loads(result)
    assert payload["crm_ready"] == 0
    assert payload["rows"][0]["issues"]


def test_leads_tool_is_discovered_and_wired_into_config() -> None:
    from navin.agent.tools.leads import LeadsTool
    from navin.agent.tools.loader import ToolLoader
    from navin.config.schema import Config

    names = {cls.__name__ for cls in ToolLoader().discover()}
    assert "LeadsTool" in names
    config = Config()
    assert config.tools.leads.enabled is True
    tool = LeadsTool.create(
        type("Ctx", (), {"workspace": "/tmp", "config": config.tools})()
    )
    assert tool.name == "leads"
