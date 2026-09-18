# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A portal probe must not lie about reachability.

A TLS verification failure used to trigger a verify=False retry that
promoted whatever answered - including a MITM - to ok: True. The probe now
fails closed with tls_failed instead.
"""

from __future__ import annotations

from unittest import mock

from navin.tenders.probe import probe_url


def test_tls_failure_is_reported_not_promoted() -> None:
    calls: list[bool] = []

    def fake_open(url, *, method="GET", verify=True):
        calls.append(verify)
        return 0, url, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"

    with mock.patch("navin.tenders.probe._open", side_effect=fake_open):
        result = probe_url("https://portal.example/")
    assert result["ok"] is False
    assert result["tls_failed"] is True
    assert "CERTIFICATE_VERIFY_FAILED" in result["error"] or "verification" in result["error"]
    assert calls == [True, True], "no unverified retry may be attempted"


def test_a_healthy_portal_stays_ok() -> None:
    calls: list[bool] = []

    def fake_open(url, *, method="GET", verify=True):
        calls.append(verify)
        return 200, url, ""

    with mock.patch("navin.tenders.probe._open", side_effect=fake_open):
        result = probe_url("https://portal.example/")
    assert result["ok"] is True
    assert not result.get("tls_failed")
    assert calls == [True]
