"""Optional polish for a Career pack. Same no-invention guard as Tenders."""

from __future__ import annotations

import os
from typing import Any

from navin.tenders.ai import ask, keeps_only_known_facts, task_role

_OFF = {"0", "off", "false", "no"}

_STYLE = (
    "You tailor a CV and a cover letter to one job offer. You never invent.\n"
    "Rules:\n"
    "- Use only facts in the material (master CV, experiences, stack).\n"
    "- Reorder and echo the offer's wording. Do not add an employer, date, "
    "tool, diploma or skill that is not in the material.\n"
    "- Keep every gap (not on file / absent du CV) visible.\n"
    "- Keep the language of the draft.\n"
    "- Plain text only. Return two blocks separated by a line containing only --- "
    "(CV first, cover second)."
)


def career_ai_enabled(profile: dict[str, Any] | None = None) -> bool:
    if str(os.environ.get("NAVIN_CAREER_AI") or os.environ.get("NAVIN_TENDERS_AI") or "").strip().lower() in _OFF:
        return False
    return (profile or {}).get("ai_assist") is True


def _facts(job: dict[str, Any], profile: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Name: {profile.get('display_name') or 'not on file'}",
            f"Headline: {profile.get('headline') or 'not on file'}",
            f"Stack: {', '.join(profile.get('stack') or []) or 'not on file'}",
            f"Master CV: {str(profile.get('master_cv') or 'not on file')[:4000]}",
            f"Offer: {job.get('title') or ''} at {job.get('company') or ''}",
            f"Description: {str(job.get('description') or '')[:1500]}",
            f"Offer stack: {', '.join(str(item) for item in (job.get('stack') or []))}",
        ]
    )


def polish_pack(job: dict[str, Any], profile: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    if not career_ai_enabled(profile):
        return pack
    draft = f"{pack.get('cv_text') or ''}\n---\n{pack.get('cover') or ''}"
    material = f"{_facts(job, profile)}\n\nDraft:\n{draft}"
    text, model = ask(
        "write",
        _STYLE,
        (
            f"{material}\n\n"
            "Rewrite the CV then the cover so this offer is obvious, without "
            "adding a fact. Keep the two blocks separated by ---."
        ),
        profile=profile,
        max_tokens=2000,
    )
    if not text or not keeps_only_known_facts(material, text, require_gaps=False):
        return pack
    cv_text, _, cover = text.partition("\n---")
    out = dict(pack)
    if cv_text.strip():
        out["cv_text"] = cv_text.strip() + "\n"
    if cover.strip():
        out["cover"] = cover.strip("- \n")
    out["model"] = model
    out["route"] = task_role("write")
    return out
