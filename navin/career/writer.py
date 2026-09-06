"""Per-mission Career pack: same facts, order and wording aimed at this offer."""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.#/-]{1,}")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ö])")


def _tokens(text: str) -> set[str]:
    return {item.lower() for item in _TOKEN.findall(text or "") if len(item) > 1}


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return cleaned[:80] or "Navin_CV"


def job_keywords(job: dict[str, Any]) -> list[str]:
    parts = [
        str(job.get("title") or ""),
        str(job.get("description") or ""),
        " ".join(str(item) for item in (job.get("stack") or [])),
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for item in job.get("stack") or []:
        token = str(item).strip()
        key = token.lower()
        if token and key not in seen:
            seen.add(key)
            ordered.append(token)
    for token in _TOKEN.findall(" ".join(parts)):
        key = token.lower()
        if key in seen or len(token) < 3:
            continue
        seen.add(key)
        ordered.append(token)
    return ordered[:40]


def _score(text: str, keys: set[str]) -> int:
    return len(_tokens(text) & keys)


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, dict)]


def _line_exp(row: dict[str, Any]) -> str:
    title = str(row.get("title") or row.get("role") or "").strip()
    company = str(row.get("company") or "").strip()
    period = str(row.get("period") or row.get("year") or "").strip()
    facts = str(row.get("facts") or row.get("detail") or row.get("summary") or "").strip()
    head = " - ".join(part for part in (title, company, period) if part)
    return f"{head}. {facts}".strip(" .") if facts else head


def _exp_bullets(row: dict[str, Any]) -> list[str]:
    facts = str(row.get("facts") or row.get("detail") or row.get("summary") or "").strip()
    if not facts:
        return []
    parts = [part.strip(" -;") for part in re.split(r"[;•●]|(?<=\.)\s+(?=[A-ZÀ-Ö])", facts) if part.strip()]
    return parts[:6] or [facts]


def _job_language(job: dict[str, Any], profile: dict[str, Any]) -> str:
    blob = " ".join(str(job.get(key) or "") for key in ("title", "description"))
    fr = len(re.findall(r"\b(le|la|les|des|une|pour|avec|poste|mission|candidature)\b|[àâéèêëïôùç]", blob, re.I))
    en = len(re.findall(r"\b(the|and|for|with|this|role|engineer|remote|please)\b", blob, re.I))
    if fr >= 3 and fr > en:
        return "fr"
    if en >= 3 and en > fr:
        return "en"
    langs = [str(item).lower() for item in (profile.get("languages") or [])]
    if langs and langs[0].startswith("fr"):
        return "fr"
    country = str(job.get("country") or profile.get("residence_country") or "").upper()
    return "fr" if country in {"FR", "BE", "LU", "MC", "CH"} else "en"


def _split_blocks(text: str) -> list[str]:
    body = str(text or "").strip()
    if not body:
        return []
    blocks = [part.strip() for part in re.split(r"\n{2,}", body) if part.strip()]
    if len(blocks) == 1 and len(body) > 160:
        blocks = [part.strip() for part in _SENTENCE.split(body) if part.strip()]
    return blocks or [body]


def _on_file_text(profile: dict[str, Any]) -> str:
    bits = [
        profile.get("master_cv"),
        profile.get("headline"),
        profile.get("display_name"),
        " ".join(str(item) for item in (profile.get("stack") or [])),
        " ".join(str(item) for item in (profile.get("strengths") or [])),
        " ".join(str(item) for item in (profile.get("highlights") or [])),
        " ".join(_line_exp(row) for row in _rows(profile.get("experiences"))),
        " ".join(
            " ".join(str(row.get(key) or "") for key in ("school", "diploma", "year"))
            for row in _rows(profile.get("education"))
        ),
    ]
    return " ".join(str(item) for item in bits if item)


def build_pack(job: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Deterministic tailored CV + cover. Never invents a fact."""
    title = str(job.get("title") or "Role").strip() or "Role"
    company = str(job.get("company") or "Company").strip() or "Company"
    person = str(profile.get("display_name") or "Candidate").strip() or "Candidate"
    headline = str(profile.get("headline") or "").strip()
    master = str(profile.get("master_cv") or "").strip()
    keywords = job_keywords(job)
    keyset = {item.lower() for item in keywords}
    filed = _on_file_text(profile)
    filed_keys = _tokens(filed)
    matched = [item for item in keywords if item.lower() in filed_keys]
    missing = [item for item in keywords if item.lower() not in filed_keys][:8]
    profile_stack = [str(item).strip() for item in (profile.get("stack") or []) if str(item).strip()]
    strengths = [str(item).strip() for item in (profile.get("strengths") or []) if str(item).strip()]
    highlights = [str(item).strip() for item in (profile.get("highlights") or []) if str(item).strip()]
    matched_stack = [item for item in profile_stack if item.lower() in keyset or item.lower() in str(job.get("description") or "").lower()]
    other_stack = [item for item in profile_stack if item not in matched_stack]
    def _exp_year(row: dict[str, Any]) -> int:
        period = str(row.get("period") or row.get("year") or "")
        years = [int(item) for item in re.findall(r"(?:19|20)\d{2}", period)]
        return years[-1] if years else _score(_line_exp(row), keyset)

    experiences = sorted(_rows(profile.get("experiences")), key=_exp_year, reverse=True)
    education = _rows(profile.get("education"))
    blocks = sorted(_split_blocks(master), key=lambda block: _score(block, keyset), reverse=True)
    titles = [str(item).strip() for item in (profile.get("titles") or []) if str(item).strip()]
    banner = headline or (titles[0] if titles else "")
    first = person.split()[0]
    cv_name = f"{_safe_filename(first + '_' + title + '_' + company)}.docx"
    french = _job_language(job, profile) == "fr"
    target = f"{title} chez {company}" if french else f"{title} at {company}"
    summary = (
        f"Target: {target}. "
        f"Reorder real experience toward {', '.join(matched[:8]) or 'the posted stack'}. "
        "Do not invent employers, dates or tools that are not in the master CV."
    )
    if master:
        summary += f" Master CV length: {len(master)} characters."
    if missing:
        summary += f" Offer terms not on file (left out): {', '.join(missing)}."
    cv_lines = [
        person,
        banner,
        str(profile.get("email") or "").strip(),
        str(profile.get("phone") or "").strip(),
        "",
        f"Candidature : {target}" if french else f"Application: {target}",
        "",
    ]
    if matched or matched_stack:
        label = "Competences alignees" if french else "Aligned skills"
        cv_lines.append(f"{label}: {', '.join(dict.fromkeys([*matched_stack, *matched[:10]]))}")
        cv_lines.append("")
    if other_stack:
        label = "Autres competences au dossier" if french else "Other on-file skills"
        cv_lines.append(f"{label}: {', '.join(other_stack)}")
        cv_lines.append("")
    if strengths:
        cv_lines.append(("Points forts : " if french else "Strengths: ") + ", ".join(strengths))
        cv_lines.append("")
    if highlights:
        cv_lines.append(("Faits : " if french else "Highlights: ") + " ".join(highlights[:6]))
        cv_lines.append("")
    if experiences:
        cv_lines.append("Experience")
        for row in experiences:
            line = _line_exp(row)
            if line:
                cv_lines.append(f"- {line}")
            for bullet in _exp_bullets(row):
                if bullet and bullet not in line:
                    cv_lines.append(f"  - {bullet}")
        cv_lines.append("")
    if education:
        cv_lines.append("Formation" if french else "Education")
        for row in education:
            line = " - ".join(
                str(row.get(key) or "").strip() for key in ("diploma", "school", "year") if str(row.get(key) or "").strip()
            )
            if line:
                cv_lines.append(f"- {line}")
        cv_lines.append("")
    if blocks:
        cv_lines.append("Profil" if french else "Profile")
        cv_lines.extend(blocks)
    elif master:
        cv_lines.append(master)
    elif not experiences and not profile_stack:
        gap = "Master CV absent du dossier." if french else "Master CV is not on file."
        cv_lines.append(gap)
    cv_text = "\n".join(line for line in cv_lines if line is not None).strip() + "\n"
    match_phrase = ", ".join(matched[:6] or matched_stack[:4]) or (
        "l'experience deja au CV" if french and (master or experiences) else
        "experience already on my CV" if master or experiences else
        "les exigences publiees une fois le CV maitre au dossier" if french else
        "the published requirements once the master CV is on file"
    )
    profile_line = (blocks[0] if blocks else master)[:280]
    first_exp = _line_exp(experiences[0]) if experiences else ""
    email = str(profile.get("email") or "").strip()
    phone = str(profile.get("phone") or "").strip()
    if french:
        cover = (
            f"Madame, Monsieur,\n\n"
            f"Je vous adresse ma candidature pour {title} chez {company}. "
            f"Le fil de ce dossier est uniquement le parcours deja au CV.\n\n"
            f"Mon parcours recouvre {match_phrase}."
            + (f" {profile_line}" if profile_line else "")
            + (f"\n\nParmi les faits deja etablis : {first_exp}." if first_exp else "")
            + (
                f"\n\nJe reste disponible pour preciser ces elements et pour un echange "
                f"sur le besoin publie.\n\nCordialement,\n{person}"
            )
            + (f"\n{email}" if email else "")
            + (f"\n{phone}" if phone else "")
        )
    else:
        cover = (
            f"Dear hiring team,\n\n"
            f"I am applying for {title} at {company}. "
            f"This letter uses only facts already on my CV.\n\n"
            f"My background matches {match_phrase}."
            + (f" {profile_line}" if profile_line else "")
            + (f"\n\nOn-file facts include: {first_exp}." if first_exp else "")
            + (
                f"\n\nI am available to detail these points and to discuss the published need.\n\n"
                f"Kind regards,\n{person}"
            )
            + (f"\n{email}" if email else "")
            + (f"\n{phone}" if phone else "")
        )
    notes = []
    if matched:
        notes.append(("Aligne : " if french else "Matched: ") + ", ".join(matched[:12]))
    if missing:
        notes.append(
            ("Sur l'offre, absent du CV (non ajoute) : " if french else "On the offer, not on file (left out): ")
            + ", ".join(missing)
        )
    aligned = ", ".join(dict.fromkeys([*matched_stack, *matched[:10]]))
    other = ", ".join(other_stack)
    aligned_phrase = ", ".join(dict.fromkeys([*matched_stack, *matched[:8]])) or (
        "l'experience deja au dossier" if french else "experience already on file"
    )
    if french:
        cv_summary = " ".join(
            part
            for part in (
                (f"{banner}." if banner else ""),
                f"Parcours aligne sur {aligned_phrase.rstrip('. ')}.",
                ("Points forts : " + ", ".join(strengths) + ".") if strengths else "",
                ("Faits : " + " ".join(highlights[:3]) + ".") if highlights else "",
                (blocks[0] if blocks else master[:240]),
            )
            if part
        )
    else:
        cv_summary = " ".join(
            part
            for part in (
                (f"{banner}." if banner else ""),
                f"Background aligned on {aligned_phrase.rstrip('. ')}.",
                ("Strengths: " + ", ".join(strengths) + ".") if strengths else "",
                ("Highlights: " + " ".join(highlights[:3]) + ".") if highlights else "",
                (blocks[0] if blocks else master[:240]),
            )
            if part
        )
    return {
        "cv_name": cv_name,
        "cv_text": cv_text,
        "cover": cover,
        "summary": summary,
        "ats_notes": "\n".join(notes),
        "keywords_matched": matched,
        "keywords_missing": missing,
        "pack_ready": bool(master or experiences or profile_stack or headline),
        "from_master": bool(master),
        "language": "fr" if french else "en",
        "cv": {
            "headline": banner,
            "contacts": [
                item
                for item in (
                    str(profile.get("email") or "").strip(),
                    str(profile.get("phone") or "").strip(),
                    str(profile.get("city") or profile.get("location") or "").strip(),
                    str(profile.get("linkedin") or "").strip(),
                )
                if item
            ],
            "target": target,
            "summary": cv_summary,
            "skills": " · ".join(part for part in (aligned, other) if part),
            "strengths": strengths,
            "experiences": [
                {
                    "title": str(row.get("title") or row.get("role") or "").strip(),
                    "company": str(row.get("company") or "").strip(),
                    "period": str(row.get("period") or row.get("year") or "").strip(),
                    "bullets": _exp_bullets(row) or ([_line_exp(row)] if _line_exp(row) else []),
                }
                for row in experiences
            ],
            "education": education,
            "languages": [str(item).strip() for item in (profile.get("languages") or []) if str(item).strip()],
        },
    }


def _french(profile: dict[str, Any], job: dict[str, Any]) -> bool:
    return _job_language(job, profile) == "fr"
