"""Canonical lead schema and validation.

The old CSV path required only 4 columns (company/website/source/confidence),
so an "enriched" export could ship without a single validated email or phone.
This schema is the contract a CRM-ready row must meet; ``validate_lead`` returns
the specific gaps so the caller can decide to keep, flag, or drop the row.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from navin.leads.normalize import (
    normalize_company,
    normalize_domain,
    normalize_email,
    normalize_phone,
    split_person_name,
)

# Order matters: this is the CSV/xlsx column order too.
CANONICAL_FIELDS: tuple[str, ...] = (
    "company",
    "domain",
    "website",
    "person",
    "first_name",
    "last_name",
    "role",
    "email",
    "email_status",
    "phone",
    "linkedin_url",
    "country",
    "sector",
    "size",
    "signal",
    "source",
    "confidence",
    "score",
    "tier",
)

_CONFIDENCE = {"low", "medium", "high"}
_EMAIL_STATUS = {"", "unknown", "unverified", "verified", "invalid", "guessed"}


@dataclass
class Lead:
    company: str = ""
    domain: str = ""
    website: str = ""
    person: str = ""
    first_name: str = ""
    last_name: str = ""
    role: str = ""
    email: str = ""
    email_status: str = ""
    phone: str = ""
    linkedin_url: str = ""
    country: str = ""
    sector: str = ""
    size: str = ""
    signal: str = ""
    source: str = ""
    confidence: str = ""
    score: int = 0
    tier: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Lead":
        known = {f for f in CANONICAL_FIELDS}
        core = {k: row.get(k, "") for k in known if k not in ("score",)}
        lead = cls(**{k: ("" if v is None else v) for k, v in core.items()})  # type: ignore[arg-type]
        try:
            lead.score = int(row.get("score") or 0)
        except (TypeError, ValueError):
            lead.score = 0
        lead.extra = {k: v for k, v in row.items() if k not in known and k != "extra"}
        return lead

    def normalized(self, *, default_country: str | None = None) -> "Lead":
        """Return a copy with every field run through its normalizer."""
        country = (self.country or default_country or "").strip()
        domain = normalize_domain(self.domain or self.website)
        first, last = (self.first_name, self.last_name)
        if self.person and not (first and last):
            first, last = split_person_name(self.person)
        return Lead(
            company=normalize_company(self.company),
            domain=domain,
            website=self.website.strip(),
            person=self.person.strip(),
            first_name=first,
            last_name=last,
            role=self.role.strip(),
            email=normalize_email(self.email),
            email_status=(self.email_status or "").strip().lower() or "unknown",
            phone=normalize_phone(self.phone, country=country),
            linkedin_url=self.linkedin_url.strip(),
            country=country,
            sector=self.sector.strip(),
            size=str(self.size).strip(),
            signal=self.signal.strip(),
            source=self.source.strip(),
            confidence=(self.confidence or "").strip().lower(),
            score=self.score,
            tier=self.tier.strip().upper(),
            extra=dict(self.extra),
        )

    def to_row(self) -> dict[str, Any]:
        row = {k: getattr(self, k) for k in CANONICAL_FIELDS}
        return row


def validate_lead(lead: Lead) -> list[str]:
    """Return a list of human-readable issues; empty means CRM-ready."""
    issues: list[str] = []
    if not lead.company.strip():
        issues.append("missing company")
    if not (lead.domain or lead.website):
        issues.append("missing website/domain")
    if not lead.source.strip():
        issues.append("missing source")
    if lead.confidence and lead.confidence not in _CONFIDENCE:
        issues.append(f"invalid confidence '{lead.confidence}'")
    if lead.email:
        if normalize_email(lead.email) != lead.email:
            issues.append("email not normalized/invalid")
    if lead.email_status and lead.email_status not in _EMAIL_STATUS:
        issues.append(f"invalid email_status '{lead.email_status}'")
    if lead.phone and not lead.phone.startswith("+"):
        issues.append("phone not E.164")
    return issues


def lead_as_dict(lead: Lead) -> dict[str, Any]:
    data = asdict(lead)
    return data
