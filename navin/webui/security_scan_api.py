# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Security panel API: deep scan of a project plus HTML / PDF report export.

Results are kept in a small in-memory store keyed by scan id so the report
endpoints render the exact findings the user saw, without a second scan.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from navin.security.deep_scan import run_deep_security_scan
from navin.security.report_deep import (
    pdf_backends_available,
    render_deep_report_html,
    render_deep_report_pdf,
)

_MAX_RESULTS = 12
_results: OrderedDict[str, dict[str, Any]] = OrderedDict()
_lock = threading.Lock()
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class SecurityScanError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _check_dir(raw_path: str) -> Path:
    cleaned = (raw_path or "").strip()
    if not cleaned:
        raise SecurityScanError("missing path")
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        raise SecurityScanError("path must be absolute")
    if not path.is_dir():
        raise SecurityScanError("not a directory", status=404)
    return path


def _remember(result: dict[str, Any]) -> str:
    scan_id = uuid.uuid4().hex[:16]
    with _lock:
        _results[scan_id] = result
        while len(_results) > _MAX_RESULTS:
            _results.popitem(last=False)
    return scan_id


def get_scan_result(scan_id: str) -> dict[str, Any] | None:
    with _lock:
        return _results.get((scan_id or "").strip())


def security_scan_payload(raw_path: str, *, kind: str = "full", subpath: str | None = None) -> dict[str, Any]:
    """Run the deep scan and return the JSON payload for the panel."""
    root = _check_dir(raw_path)
    sub = (subpath or "").strip().strip("/") or None
    if sub and (".." in sub.split("/") or Path(sub).is_absolute()):
        raise SecurityScanError("invalid subpath")
    result = run_deep_security_scan(root, kind=kind, subpath=sub)
    if not result.get("ok"):
        raise SecurityScanError(str(result.get("error") or "scan failed"), status=500)
    result["scan_id"] = _remember(result)
    result["pdf_available"] = bool(pdf_backends_available())
    result["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return result


def security_report_payload(scan_id: str, fmt: str = "html") -> tuple[bytes, str, str]:
    """Return (body, content_type, filename) for a stored scan."""
    result = get_scan_result(scan_id)
    if result is None:
        raise SecurityScanError("scan not found or expired; run the scan again", status=404)
    project = Path(str(result.get("root") or "project")).name or "project"
    stamp = time.strftime("%Y%m%d-%H%M", time.gmtime())
    base = _SAFE_NAME.sub("-", f"security-report-{project}-{stamp}").strip("-")
    title = f"Security assessment - {project}"
    fmt_n = (fmt or "html").strip().lower()
    if fmt_n == "pdf":
        try:
            body = render_deep_report_pdf(result, title=title)
        except RuntimeError as exc:
            raise SecurityScanError(str(exc), status=503) from exc
        return body, "application/pdf", f"{base}.pdf"
    if fmt_n == "html":
        html_doc = render_deep_report_html(result, title=title)
        return html_doc.encode("utf-8"), "text/html; charset=utf-8", f"{base}.html"
    raise SecurityScanError("format must be html or pdf")
