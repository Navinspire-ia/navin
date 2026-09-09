# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navin.webui.marketing_api import (
    MarketingQAApiError,
    get_marketing_qa_report,
    list_marketing_qa_assets,
    list_marketing_qa_reports,
    override_marketing_qa_report,
)


def _report(candidate: Path) -> dict:
    return {
        "schema_version": 1,
        "created_at": "2026-08-23T00:00:00+00:00",
        "candidate": {"path": str(candidate), "width": 1200, "height": 1200},
        "references": [],
        "claims": ["composition"],
        "requirements": {},
        "provider": {
            "provider": None,
            "model": None,
            "usage": {},
            "cost_usd": None,
        },
        "scores": {"deterministic": 91, "vision": 0, "overall": 41},
        "verdict": "BLOCK",
        "findings": [
            {
                "check": "vision_analysis",
                "source": "gate",
                "status": "BLOCK",
                "score": 0,
                "evidence": ["provider unavailable"],
                "recommendation": "Configure vision.",
            }
        ],
        "pass": False,
    }


def test_assets_reports_detail_and_audited_override(tmp_path: Path) -> None:
    asset = tmp_path / "marketing" / "creatives" / "candidate.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"png")
    qa_dir = tmp_path / "marketing" / "qa"
    qa_dir.mkdir()
    (qa_dir / "delivery.json").write_text(json.dumps(_report(asset)), encoding="utf-8")

    assets = list_marketing_qa_assets(tmp_path)
    assert assets["assets"][0]["path"] == "marketing/creatives/candidate.png"

    recent = list_marketing_qa_reports(tmp_path)
    assert recent["reports"][0]["effective_verdict"] == "BLOCK"
    assert (
        get_marketing_qa_report(tmp_path, "delivery")["findings"][0]["check"] == "vision_analysis"
    )

    overridden = override_marketing_qa_report(
        tmp_path,
        "delivery",
        reason="Brand owner approved the intentional crop.",
        verdict="WARN",
    )
    assert overridden["verdict"] == "BLOCK"
    assert overridden["effective_verdict"] == "WARN"
    assert overridden["human_override"]["actor"] == "webui-human"
    audit = json.loads((qa_dir / "human-overrides.json").read_text(encoding="utf-8"))
    assert audit[-1]["report_id"] == "delivery"
    assert audit[-1]["reason"] == "Brand owner approved the intentional crop."


def test_override_requires_reason_and_safe_report_id(tmp_path: Path) -> None:
    qa_dir = tmp_path / "marketing" / "qa"
    qa_dir.mkdir(parents=True)
    (qa_dir / "delivery.json").write_text(
        json.dumps(_report(tmp_path / "candidate.png")), encoding="utf-8"
    )

    with pytest.raises(MarketingQAApiError, match="reason"):
        override_marketing_qa_report(tmp_path, "delivery", reason=" ")
    with pytest.raises(MarketingQAApiError, match="invalid report id"):
        get_marketing_qa_report(tmp_path, "../outside")
