"""ICP + BANT-F scoring with evidence. This is the desk score, not a vanity number."""

from __future__ import annotations

import re
from typing import Any

DECISION_TITLES = (
    "ceo",
    "cto",
    "cfo",
    "coo",
    "founder",
    "co-founder",
    "owner",
    "president",
    "managing director",
    "directeur",
    "dirigeant",
    "head of",
    "vp ",
    "vice president",
    "chief ",
    "partner",
)

SIGNAL_HINTS = (
    ("funding", ("funding", "leve", "raised", "series", "seed")),
    ("hiring", ("hiring", "recrut", "careers", "jobs", "poste")),
    ("news", ("launch", "annonce", "expansion", "ouvert")),
    # Named in a "top / best / list of" page: a peer already vouches for them.
    (
        "listed",
        (
            "listed on",
            "top ",
            "classement",
            "ranking",
            "meilleur",
            "best ",
            "annuaire",
            "directory",
        ),
    ),
    # OpenStreetMap / Places: a real address, a phone, often a site.
    ("local", ("local business", "osm", "places")),
    ("registry", ("sirene", "insee", "companies house", "opencorporates", "pappers")),
)


# INSEE tranche codes (and midpoints) so "12" is 20-49 people, not 12 employees.
_TRANCHE_LABEL = {
    "00": "0",
    "01": "1-2",
    "02": "3-5",
    "03": "6-9",
    "11": "10-19",
    "12": "20-49",
    "21": "50-99",
    "22": "100-199",
    "31": "200-249",
    "32": "250-499",
    "41": "500-999",
    "42": "1000-1999",
    "51": "2000-4999",
    "52": "5000-9999",
    "53": "10000+",
}
_TRANCHE_MID = {
    "00": 0,
    "01": 2,
    "02": 4,
    "03": 8,
    "11": 15,
    "12": 35,
    "21": 75,
    "22": 150,
    "31": 225,
    "32": 375,
    "41": 750,
    "42": 1500,
    "51": 3500,
    "52": 7500,
    "53": 12000,
}


def size_display(raw: Any) -> str:
    """Map an INSEE tranche code like 21 to 50-99. Leave ranges and counts as-is."""
    text = str(raw or "").strip()
    if not text:
        return ""
    key = text.split()[0]
    if key in _TRANCHE_LABEL and (text == key or text.startswith(f"{key} ")):
        return _TRANCHE_LABEL[key]
    return text


def _size_int(raw: Any) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    key = text.split()[0].split("-")[0]
    if key in _TRANCHE_MID and (text == key or text.startswith(f"{key} ")):
        return _TRANCHE_MID[key]
    if text in _TRANCHE_MID:
        return _TRANCHE_MID[text]
    digits = re.findall(r"\d+", text.replace(",", ""))
    if not digits:
        return None
    if len(digits) >= 2:
        try:
            return (int(digits[0]) + int(digits[1])) // 2
        except ValueError:
            return None
    try:
        return int(digits[0])
    except ValueError:
        return None


def detect_signals(row: dict[str, Any]) -> list[dict[str, str]]:
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("signal", "source", "sector", "extra")
    ).casefold()
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    blob = f"{blob} {extra}"
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for kind, tokens in SIGNAL_HINTS:
        if any(token in blob for token in tokens) and kind not in seen:
            seen.add(kind)
            found.append({"kind": kind, "text": str(row.get("signal") or kind)})
    if row.get("email_status") == "verified" and "verified" not in seen:
        found.append({"kind": "verified", "text": "verified email"})
    return found


def _title_match(role: str, titles: list[str]) -> bool:
    role_cf = role.casefold()
    if not role_cf:
        return False
    wanted = [str(item).casefold() for item in titles if str(item).strip()]
    if wanted and any(item in role_cf or role_cf in item for item in wanted):
        return True
    return any(token in role_cf for token in DECISION_TITLES)


def qualify(row: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return score, tier, BANT-F breakdown, evidence and the next action."""
    profile = profile if isinstance(profile, dict) else {}
    evidence: list[str] = []
    countries = {str(item).upper()[:2] for item in (profile.get("countries") or []) if str(item).strip()}
    country = str(row.get("country") or "").upper()[:2]
    sector = str(profile.get("sector") or "").casefold()
    row_sector = str(row.get("sector") or "").casefold()
    titles = [str(item) for item in (profile.get("titles") or [])]
    role = str(row.get("role") or "")
    size = _size_int(row.get("size"))
    try:
        size_min = int(profile.get("size_min") or 0)
    except (TypeError, ValueError):
        size_min = 0
    try:
        size_max = int(profile.get("size_max") or 0)
    except (TypeError, ValueError):
        size_max = 0

    fit = 6
    if countries and country and country in countries:
        fit += 10
        evidence.append(f"country {country} matches ICP")
    if sector and sector in row_sector:
        fit += 10
        evidence.append(f"sector matches {profile.get('sector')}")
    elif row_sector:
        fit += 3
    fit = min(30, fit)

    need = 0
    if row.get("email"):
        # A pattern guess is a lead on an inbox, not an inbox.
        need += 3 if row.get("email_status") == "guessed" else 6
    if row.get("email_status") == "verified":
        need += 10
        evidence.append("verified email")
    if row.get("phone"):
        need += 5
    if row.get("linkedin_url"):
        need += 4
    if row.get("person") and role:
        need += 4
        evidence.append(f"named contact {row.get('person')}")
    need = min(25, need)

    signals = detect_signals(row)
    timing = 8 if str(row.get("stage") or "new") in {"new", "qualified"} else 4
    if row.get("email_status") == "verified":
        timing += 4
        evidence.append("inbox is ready now")
    if any(item["kind"] in {"funding", "hiring", "news"} for item in signals):
        timing += 8
        evidence.append("buying signal: " + ",".join(item["kind"] for item in signals))
    elif any(item["kind"] == "listed" for item in signals):
        timing += 5
        evidence.append("named in a market list")
    elif any(item["kind"] in {"registry", "local"} for item in signals):
        timing += 4
        evidence.append(
            "official registry hit"
            if any(item["kind"] == "registry" for item in signals)
            else "local business listing"
        )
    timing = min(20, timing)

    authority = 4 if role else 0
    if _title_match(role, titles):
        authority = 15
        evidence.append(f"decision title: {role}")
    authority = min(15, authority)

    budget = 4
    if size and size_min and size_max and size_min <= size <= size_max:
        budget = 10
        evidence.append(f"headcount {size} in ICP band")
    elif size:
        budget = 6
    budget = min(10, budget)

    score = max(0, min(100, fit + need + timing + authority + budget))
    if score >= 80:
        tier = "A"
        next_action = "contact now"
    elif score >= 55:
        tier = "B"
        next_action = "enrich then nurture"
    else:
        tier = "C"
        next_action = "skip or revisit after a better signal"
    disqualify = []
    if countries and country and country not in countries:
        disqualify.append(f"country {country} outside ICP")
    if not row.get("company"):
        disqualify.append("missing company")
    return {
        "score": score,
        "tier": tier,
        "bant": {
            "fit": fit,
            "need": need,
            "timing": timing,
            "authority": authority,
            "budget": budget,
        },
        "why": evidence,
        "next_action": next_action,
        "signals": signals,
        "disqualify": disqualify,
    }


def score_lead(row: dict[str, Any], profile: dict[str, Any] | None = None) -> int:
    return int(qualify(row, profile)["score"])


def apply_qualification(row: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write score fields onto the row. Used by hunt, enrich, rescore and watch."""
    payload = qualify(row, profile)
    next_row = dict(row)
    next_row["score"] = payload["score"]
    next_row["tier"] = payload["tier"]
    next_row["bant"] = payload["bant"]
    next_row["why"] = payload["why"]
    next_row["next_action"] = payload["next_action"]
    next_row["signals"] = payload["signals"]
    next_row["disqualify"] = payload["disqualify"]
    label = size_display(next_row.get("size"))
    if label:
        next_row["size"] = label
    return next_row
