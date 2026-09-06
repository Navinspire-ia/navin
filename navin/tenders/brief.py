"""What the company is hunting: the brief every official fetcher targets.

Before this, each API returned "the latest 25 notices" of a whole country or
of the whole EU, so a data / cloud shop saw green-space maintenance and
school buses. The brief turns the wizard answers (crafts, project types,
countries) into server-side filters (CPV divisions, full-text terms, buyer
country, recency) and into one client-side relevance test for feeds whose
API cannot filter (Find a Tender, Contracts Finder, CanadaBuys).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from navin.tenders.needs import expand_need_terms, lookup_need, query_need_terms

DEFAULT_DAYS = 30
MAX_KEYWORDS = 8
MAX_CPV = 8

# Terms too short or too generic to prove a notice is on-craft by themselves.
_WEAK_TERMS = frozenset(
    {
        "si ",
        "bi ",
        "run",
        "services",
        "service",
        "prestations",
        "works",
        "travaux",
        "supplies",
        "fournitures",
        "open",
        "framework",
        "etude",
        "audit",
        "conseil",
        "formation",
        "training",
        "eau",
        "road",
        "web",
        "lot",
    }
)


@dataclass(frozen=True)
class FetchBrief:
    """Targets for one collect run. Empty tuples mean "no filter on that axis"."""

    keywords: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    cpv: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    days: int = DEFAULT_DAYS
    keywords_fr: tuple[str, ...] = field(default=())

    @property
    def targeted(self) -> bool:
        return bool(self.terms or self.cpv)

    def public(self) -> dict[str, Any]:
        """JSON shape for the desk: what this collect actually asked the APIs for."""
        return {
            "keywords": list(self.search_terms),
            "cpv": list(self.cpv),
            "countries": list(self.countries),
            "days": self.days,
            "targeted": self.targeted,
        }

    @property
    def search_terms(self) -> tuple[str, ...]:
        """Labels for server-side full-text clauses, English then French."""
        out: list[str] = []
        seen: set[str] = set()
        for item in (*self.keywords, *self.keywords_fr):
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return tuple(out[:MAX_KEYWORDS])

    def or_query(self, limit: int = 8, min_len: int = 4) -> str:
        """`"a b" OR c` for feeds with one free-text box (World Bank, Contracts Finder).

        Phrases are quoted (unquoted multi-word terms return nothing on the
        World Bank search) and very short tokens are skipped: `ai` alone is
        stemmed into `irrigation` and floods the result with noise.
        """
        picked: list[str] = []
        for term in (*self.terms, *self.search_terms):
            clean = _clean_term(term).replace("&", " ").strip()
            clean = " ".join(clean.split())
            if len(clean) < min_len or clean.isdigit() or clean.lower() in {p.lower() for p in picked}:
                continue
            picked.append(clean)
            if len(picked) >= limit:
                break
        return " OR ".join(f'"{term}"' if " " in term else term for term in picked)


def _clean_term(value: Any) -> str:
    text = re.sub(r"[\"()\[\]{}<>~*?:\\/]", " ", str(value or ""))
    return " ".join(text.split()).strip()


def build_brief(
    *,
    countries: list[str] | None = None,
    crafts: list[str] | None = None,
    project_types: list[str] | None = None,
    tender_types: list[str] | None = None,
    days: int = DEFAULT_DAYS,
) -> FetchBrief:
    """Turn wizard labels into API filters. Tender types never narrow a feed."""
    del tender_types  # procedure forms are scored, not used to exclude notices
    isos = tuple(
        dict.fromkeys(
            str(item).strip().upper()
            for item in (countries or [])
            if str(item).strip() and len(str(item).strip()) == 2
        )
    )
    keywords: list[str] = []
    keywords_fr: list[str] = []
    cpv: list[str] = []
    for label in query_need_terms(crafts, project_types, limit=MAX_KEYWORDS):
        row = lookup_need(label)
        english = _clean_term(row["label"] if row else label)
        if english and english.lower() not in {k.lower() for k in keywords}:
            keywords.append(english)
        if row:
            french = _clean_term(row.get("label_fr"))
            if french and french.lower() != english.lower():
                keywords_fr.append(french)
            code = str(row.get("cpv") or "").strip()
            if code and code not in cpv:
                cpv.append(code)
    terms = tuple(
        term
        for term in expand_need_terms(list(crafts or []) + list(project_types or []))
        if term not in _WEAK_TERMS and len(term.strip()) >= 2 and not term.strip().isdigit()
    )
    try:
        window = max(1, min(int(days), 120))
    except (TypeError, ValueError):
        window = DEFAULT_DAYS
    return FetchBrief(
        keywords=tuple(keywords[:MAX_KEYWORDS]),
        keywords_fr=tuple(keywords_fr[:MAX_KEYWORDS]),
        terms=terms,
        cpv=tuple(cpv[:MAX_CPV]),
        countries=isos,
        days=window,
    )


def brief_from_profile(profile: dict[str, Any] | None) -> FetchBrief:
    data = profile if isinstance(profile, dict) else {}
    return build_brief(
        countries=list(data.get("countries") or []),
        crafts=list(data.get("crafts") or []),
        project_types=list(data.get("project_types") or []),
        tender_types=list(data.get("tender_types") or []),
        days=int(data.get("collect_days") or DEFAULT_DAYS),
    )


def _haystack(row: dict[str, Any]) -> str:
    parts = [row.get("title"), row.get("description"), row.get("sector"), row.get("eligibility")]
    return " ".join(str(part or "") for part in parts).lower()


def _cpv_codes(row: dict[str, Any]) -> list[str]:
    raw = row.get("cpv")
    if isinstance(raw, (list, tuple)):
        values = [str(item) for item in raw]
    else:
        values = re.split(r"[\s,;|]+", str(raw or ""))
    return [re.sub(r"\D", "", value)[:8] for value in values if re.sub(r"\D", "", value)]


def cpv_in_brief(row: dict[str, Any], brief: FetchBrief) -> bool:
    """CPV divisions match on their two leading digits (72000000 covers 72263000)."""
    if not brief.cpv:
        return False
    wanted = {code[:2] for code in brief.cpv if len(code) >= 2}
    return any(code[:2] in wanted for code in _cpv_codes(row) if len(code) >= 2)


def term_in_brief(row: dict[str, Any], brief: FetchBrief) -> bool:
    hay = _haystack(row)
    if not hay:
        return False
    for term in brief.terms:
        needle = term.strip().lower()
        if not needle:
            continue
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", hay):
            return True
    return False


def matches_brief(row: dict[str, Any], brief: FetchBrief | None) -> bool:
    """Client-side relevance for feeds that cannot filter server-side."""
    if brief is None or not brief.targeted:
        return True
    return cpv_in_brief(row, brief) or term_in_brief(row, brief)


def filter_brief(rows: list[dict[str, Any]], brief: FetchBrief | None) -> list[dict[str, Any]]:
    return [row for row in rows if matches_brief(row, brief)]
