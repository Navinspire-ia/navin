# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Evidence-backed on-page and technical SEO checks."""

from __future__ import annotations

from collections import Counter
from urllib.parse import urlparse

from navin.seo.models import Confidence, Evidence, Finding, PageRecord, Severity


def _finding(
    page: PageRecord,
    code: str,
    severity: Severity,
    title: str,
    message: str,
    value: object,
    recommendation: str,
    *,
    category: str = "onpage",
) -> Finding:
    return Finding(
        code=code,
        category=category,
        severity=severity,
        title=title,
        message=message,
        url=page.url,
        evidence=[Evidence(kind=code, value=value, source=page.final_url)],
        recommendation=recommendation,
        confidence=Confidence.HIGH,
    )


def audit_page(page: PageRecord) -> list[Finding]:
    findings: list[Finding] = []
    if page.error:
        findings.append(
            _finding(
                page, "fetch_error", Severity.CRITICAL, "Page inaccessible",
                page.error, page.error, "Restore access and crawl the page again.",
                category="crawl",
            )
        )
        return findings
    if page.status >= 400 or page.status == 0:
        findings.append(
            _finding(
                page, "http_status", Severity.CRITICAL, "Non-success HTTP status",
                f"The page returned HTTP {page.status}.", page.status,
                "Return a valid 2xx response or redirect intentionally.", category="crawl",
            )
        )
    elif 300 <= page.status < 400 or page.url != page.final_url:
        findings.append(
            _finding(
                page, "redirect", Severity.MEDIUM, "URL redirects",
                "The requested URL did not resolve directly.", {
                    "status": page.status, "final_url": page.final_url,
                    "chain": page.redirect_chain,
                }, "Update internal links to the final URL.", category="crawl",
            )
        )
    if not page.title:
        findings.append(_finding(
            page, "title_missing", Severity.HIGH, "Missing title",
            "No title element was found.", "", "Add a unique descriptive title."
        ))
    elif len(page.title) < 15 or len(page.title) > 65:
        findings.append(_finding(
            page, "title_length", Severity.MEDIUM, "Title length",
            f"The title contains {len(page.title)} characters.", page.title,
            "Use a concise title of roughly 15 to 65 characters."
        ))
    if not page.meta_description:
        findings.append(_finding(
            page, "meta_description_missing", Severity.MEDIUM, "Missing meta description",
            "No meta description was found.", "", "Add a useful page-specific description."
        ))
    if len(page.h1) != 1:
        findings.append(_finding(
            page, "h1_count", Severity.HIGH if not page.h1 else Severity.MEDIUM,
            "Invalid H1 count", f"Expected one H1 and found {len(page.h1)}.",
            page.h1, "Use exactly one clear primary H1."
        ))
    if not page.canonical:
        findings.append(_finding(
            page, "canonical_missing", Severity.MEDIUM, "Missing canonical",
            "No canonical link was found.", "", "Add a self-referencing canonical."
        ))
    elif urlparse(page.canonical).scheme not in {"http", "https"}:
        findings.append(_finding(
            page, "canonical_invalid", Severity.HIGH, "Invalid canonical",
            "The canonical URL is not HTTP(S).", page.canonical,
            "Use an absolute HTTP(S) canonical URL."
        ))
    directives = {part.strip().lower() for part in page.robots.split(",") if part.strip()}
    if "noindex" in directives:
        findings.append(_finding(
            page, "robots_noindex", Severity.CRITICAL, "Page is noindex",
            "The meta robots directive contains noindex.", page.robots,
            "Remove noindex if this page should appear in search.", category="indexability",
        ))
    for lang, target in sorted(page.hreflang.items()):
        if not lang or not target:
            findings.append(_finding(
                page, "hreflang_invalid", Severity.HIGH, "Invalid hreflang",
                "An alternate language link is incomplete.", {lang: target},
                "Provide a valid language code and absolute alternate URL.",
                category="international",
            ))
    return findings


def audit_site(pages: list[PageRecord]) -> list[Finding]:
    findings = [finding for page in pages for finding in audit_page(page)]
    titles = Counter(page.title.casefold() for page in pages if page.title)
    incoming = Counter(link.split("#", 1)[0] for page in pages for link in page.links)
    for page in pages:
        if page.title and titles[page.title.casefold()] > 1:
            findings.append(_finding(
                page, "title_duplicate", Severity.MEDIUM, "Duplicate title",
                "This title is shared by multiple crawled pages.", page.title,
                "Write a unique title for each indexable page."
            ))
        if page is not pages[0] and incoming[page.url.split("#", 1)[0]] == 0:
            findings.append(_finding(
                page, "internal_orphan", Severity.HIGH, "No internal links found",
                "No crawled page links to this URL.", 0,
                "Add relevant internal links from discoverable pages.", category="links",
            ))
    return sorted(findings, key=lambda item: (item.url or "", item.code, item.title))
