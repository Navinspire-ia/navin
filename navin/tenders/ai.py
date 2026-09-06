"""Model routing for the Tenders desk.

Every AI step here picks its model from Settings -> Models -> Task routing
(``model_routes``) instead of the session model, so the GO/NO-GO call can run
on the capable model while prose and short labels run on the economy one.

Two rules hold everywhere in this file:

- A model is an option, never a dependency. When no route is configured, the
  call fails or the answer breaks the no-invention rule, the caller keeps the
  deterministic template it already had.
- Nothing loops over the whole book. One notice, one call, on a user action.
"""

from __future__ import annotations

import asyncio
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# Tenders task -> Settings task-routing role. Only tasks the desk really calls
# are listed, so the UI never advertises a route that never fires.
# "qualify" is the only one on the expensive route: it is the call that puts
# days of bid work and a bid bond on the table.
TENDER_TASK_ROLES: dict[str, str] = {
    "qualify": "deep",
    "write": "docs",
    "mail": "docs",
}

# Route order per task: the mapped role first, then a cheaper generalist so a
# half-configured Settings page still gets a model instead of nothing.
_FALLBACK_ROLES: tuple[str, ...] = ("dev", "fast")

_MAX_TOKENS = 1200
_TIMEOUT_S = 60.0
_OFF = {"0", "off", "false", "no"}


def task_role(task: str) -> str:
    """Settings role for a desk task. Unknown tasks stay on everyday coding."""
    return TENDER_TASK_ROLES.get(str(task or "").strip().lower(), "dev")


def task_preset(task: str, routes: dict[str, str] | None = None) -> str | None:
    """Preset name the desk will call for *task*, or None when unrouted."""
    from navin.agent.model_routes import resolve_model_route

    for role in (task_role(task), *_FALLBACK_ROLES):
        preset = resolve_model_route(role, routes=routes)
        if preset:
            return preset
    return None


def preset_model(preset: str | None, config: Any = None) -> str:
    """Model id behind a preset name, for display. Empty when unresolvable."""
    if not preset:
        return ""
    try:
        if config is None:
            from navin.config.loader import load_config

            config = load_config()
        return str(config.resolve_preset(preset).model or "")
    except Exception:
        return ""


def ai_enabled(profile: dict[str, Any] | None = None) -> bool:
    """False disables every model call and keeps the desk fully deterministic."""
    if str(os.environ.get("NAVIN_TENDERS_AI") or "").strip().lower() in _OFF:
        return False
    return (profile or {}).get("ai_assist") is not False


def routing_snapshot(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the desk would call, so the UI can show it without a round trip."""
    enabled = ai_enabled(profile)
    config = None
    routes: dict[str, str] = {}
    try:
        from navin.config.loader import load_config

        config = load_config()
        routes = dict(config.model_routes)
    except Exception:
        enabled = False
    tasks = []
    for task in TENDER_TASK_ROLES:
        preset = task_preset(task, routes) if enabled else None
        tasks.append(
            {
                "task": task,
                "role": task_role(task),
                "preset": preset or "",
                "model": preset_model(preset, config),
            }
        )
    return {
        "enabled": enabled,
        "routed": sum(1 for row in tasks if row["preset"]),
        "tasks": tasks,
    }


def _run(coro: Any) -> Any:
    """Await *coro* from sync code, whether or not a loop owns this thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _chat(preset: str, system: str, user: str, max_tokens: int, temperature: float) -> str:
    from navin.providers.factory import load_provider_snapshot

    snapshot = await asyncio.to_thread(load_provider_snapshot, preset_name=preset)
    response = await asyncio.wait_for(
        snapshot.provider.chat_with_retry(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=snapshot.model,
            max_tokens=max_tokens,
            temperature=temperature,
        ),
        timeout=_TIMEOUT_S,
    )
    return str(response.content or "")


def ask(
    task: str,
    system: str,
    user: str,
    *,
    profile: dict[str, Any] | None = None,
    max_tokens: int = _MAX_TOKENS,
    temperature: float = 0.2,
) -> tuple[str, str]:
    """One routed call. Returns ``(text, model)``; ``("", "")`` means fall back."""
    if not ai_enabled(profile):
        return "", ""
    preset = task_preset(task)
    if not preset:
        return "", ""
    try:
        text = _run(_chat(preset, system, user, max_tokens, temperature))
    except Exception:
        return "", ""
    return _strip_fences(text).strip(), preset_model(preset) or preset


def _strip_fences(text: str) -> str:
    body = (text or "").strip()
    if not body.startswith("```"):
        return text or ""
    lines = body.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


# A thousands separator is one character followed by exactly three digits, so
# "180 000" reads as one figure while "31. 2026" stays two.
_THOUSANDS_RE = re.compile(r"(?<=\d)[\s.,'\u00a0\u202f](?=\d{3}(?!\d))")
_DIGITS_RE = re.compile(r"\d+")


def _numbers(text: str) -> set[str]:
    """Figures in *text*, normalized so 180 000 and 180000 are the same fact."""
    joined = _THOUSANDS_RE.sub("", text or "")
    return {
        token.lstrip("0") or "0" for token in _DIGITS_RE.findall(joined) if len(token) >= 2
    }


# FR and EN markers are the same fact: a French rewrite may drop "not on file"
# as long as "non renseigne" stays visible to the bid team.
_GAP_MARKERS = ("not on file", "non renseigne", "not stated")


def keeps_only_known_facts(
    material: str,
    draft: str,
    *,
    require_gaps: bool = True,
) -> bool:
    """True when *draft* invents no figure and hides no gap.

    A tender reply that adds a reference number, an amount or a headcount the
    company never stated is a disqualification, not a style issue. Numbers are
    the cheap, checkable proxy: any digit group in the answer must already be
    in the material we handed the model.
    """
    body = (draft or "").strip()
    if not body:
        return False
    if _numbers(body) - _numbers(material):
        return False
    if require_gaps:
        material_low = (material or "").lower()
        body_low = body.lower()
        if any(marker in material_low for marker in _GAP_MARKERS) and not any(
            marker in body_low for marker in _GAP_MARKERS
        ):
            return False
    return True


_STYLE_RULES = (
    "You improve the wording of a public-tender document. You never invent.\n"
    "Rules:\n"
    "- Use only the facts in the material. Never add a client, an amount, a "
    "date, a headcount, a certification or a reference that is not there.\n"
    "- Keep every gap marker (not on file / non renseigne) exactly as it is. "
    "A missing fact must stay visible to the bid team.\n"
    "- Keep the language of the draft (French draft stays French).\n"
    "- Plain text only: no markdown fences, no headings, no commentary about "
    "your own work.\n"
    "- Return the rewritten document and nothing else."
)


def _facts_block(tender: dict[str, Any], profile: dict[str, Any]) -> str:
    from navin.tenders.profile import filed_documents

    docs = filed_documents(profile)
    refs = [row.get("title") or row.get("name") or "" for row in docs if row.get("bucket") == "reference"]
    rows = [
        f"Company: {profile.get('name') or 'not on file'}",
        f"Specialty: {profile.get('specialty') or 'not on file'}",
        f"Crafts: {', '.join(profile.get('crafts') or []) or 'not on file'}",
        f"Tender types: {', '.join(profile.get('tender_types') or []) or 'not on file'}",
        f"Completed project types: {', '.join(profile.get('project_types') or []) or 'not on file'}",
        f"Strengths: {', '.join(profile.get('strengths') or []) or 'not on file'}",
        f"References on file: {', '.join(item for item in refs[:12] if item) or 'not on file'}",
        f"Notice: {tender.get('title') or ''}",
        f"Buyer: {tender.get('buyer') or 'not stated'}",
        f"Country: {tender.get('country') or 'not stated'}",
        f"Deadline: {tender.get('deadline') or 'not stated'}",
        f"Published budget: {tender.get('budget') if tender.get('budget') is not None else 'not stated'}",
        f"Description: {str(tender.get('description') or '')[:1200]}",
        f"Eligibility: {str(tender.get('eligibility') or '')[:600]}",
        f"CDC: {str(tender.get('cdc_text') or '')[:1500]}",
    ]
    if docs:
        rows.append("On-file models and references are mandatory. Reuse their extracts. Never invent a page.")
        for row in docs:
            excerpt = str(row.get("excerpt") or "").strip()
            label = f"{row.get('label') or 'File'} {row.get('name') or row.get('title') or ''}".strip()
            if excerpt:
                rows.append(f"{label}: {excerpt[:1500]}")
            else:
                rows.append(f"{label}: on file, no extract")
    return "\n".join(rows)


def polish_response(
    tender: dict[str, Any],
    profile: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any]:
    """Rewrite letter and summary on the ``write`` route. Silent no-op on doubt."""
    base = f"{response.get('letter') or ''}\n\n---\n\n{response.get('executive_summary') or ''}"
    if not base.strip("- \n"):
        return response
    material = f"{_facts_block(tender, profile)}\n\nDraft:\n{base}"
    text, model = ask(
        "write",
        _STYLE_RULES,
        (
            f"{material}\n\n"
            "Rewrite the draft above so it reads like a bid manager wrote it: "
            "direct, specific to this notice, no filler. Keep the two blocks "
            "separated by a line containing only ---."
        ),
        profile=profile,
        max_tokens=1600,
    )
    if not text or not keeps_only_known_facts(material, text):
        return polish_sections(tender, profile, response)
    letter, _, summary = text.partition("\n---")
    out = dict(response)
    out["letter"] = letter.strip()
    if summary.strip():
        out["executive_summary"] = summary.strip("- \n")
    out["model"] = model
    out["route"] = task_role("write")
    return polish_sections(tender, profile, out)


def polish_sections(
    tender: dict[str, Any],
    profile: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any]:
    """Rewrite architecture, planning and methodology. Silent no-op on doubt."""
    parts = [
        str(response.get("architecture") or ""),
        str(response.get("planning") or ""),
        str(response.get("methodology") or ""),
        str(response.get("need") or ""),
        str(response.get("functional") or ""),
        str(response.get("vision") or ""),
    ]
    if not any(part.strip() for part in parts):
        return response
    material = f"{_facts_block(tender, profile)}\n\nDraft:\n" + "\n---\n".join(parts)
    text, model = ask(
        "write",
        _STYLE_RULES,
        (
            f"{material}\n\n"
            "Rewrite architecture, then planning, then methodology, then need, "
            "then functional, then vision so a bid manager can hand this to a "
            "reviewer. Keep the six blocks separated by a line containing only "
            "---. Do not invent a stack, a date or a headcount."
        ),
        profile=profile,
        max_tokens=2200,
    )
    if not text or not keeps_only_known_facts(material, text):
        return response
    blocks = [part.strip() for part in text.split("\n---")]
    if len(blocks) < 3:
        return response
    keys = ("architecture", "planning", "methodology", "need", "functional", "vision")
    out = dict(response)
    for key, block in zip(keys, blocks):
        body = block.lstrip("- \n")
        if body:
            out[key] = body
    out["model"] = model or out.get("model")
    out["route"] = task_role("write")
    return out


def revise_response(
    tender: dict[str, Any],
    profile: dict[str, Any],
    response: dict[str, Any],
    remarks: str,
) -> dict[str, Any]:
    """Apply reviewer remarks. Keep the dossier if the model invents."""
    notes = str(remarks or "").strip()
    out = dict(response)
    previous = str(out.get("revision_notes") or "").strip()
    out["revision_notes"] = f"{previous}\n\n{notes}".strip() if previous else notes
    keys = (
        "letter",
        "executive_summary",
        "architecture",
        "planning",
        "methodology",
        "compliance_matrix",
        "staffing",
        "references",
    )
    draft = "\n---\n".join(f"{key.upper()}\n{out.get(key) or ''}" for key in keys)
    material = f"{_facts_block(tender, profile)}\n\nRemarks:\n{notes}\n\nDraft:\n{draft}"
    text, model = ask(
        "write",
        _STYLE_RULES,
        (
            f"{material}\n\n"
            "Apply the remarks to the draft. Return the same blocks in this "
            "order, each starting with its label on its own line "
            "(LETTER, EXECUTIVE_SUMMARY, ARCHITECTURE, PLANNING, METHODOLOGY, "
            "COMPLIANCE_MATRIX, STAFFING, REFERENCES), separated by ---. "
            "Keep every gap marker. Do not invent."
        ),
        profile=profile,
        max_tokens=2200,
    )
    if text and keeps_only_known_facts(material, text):
        parsed = _parse_labeled_blocks(text)
        for key in keys:
            body = parsed.get(key)
            if body:
                out[key] = body
        out["model"] = model or out.get("model")
        out["route"] = task_role("write")
        return out
    lang = str(out.get("language") or "en")
    prefix = (
        f"Revue interne.\nRemarques : {notes}\n\n"
        if lang == "fr"
        else f"Internal review.\nRemarks: {notes}\n\n"
    )
    letter = str(out.get("letter") or "")
    if notes and notes not in letter:
        out["letter"] = prefix + letter
    return out


def _parse_labeled_blocks(text: str) -> dict[str, str]:
    labels = {
        "LETTER": "letter",
        "EXECUTIVE_SUMMARY": "executive_summary",
        "ARCHITECTURE": "architecture",
        "PLANNING": "planning",
        "METHODOLOGY": "methodology",
        "COMPLIANCE_MATRIX": "compliance_matrix",
        "STAFFING": "staffing",
        "REFERENCES": "references",
    }
    out: dict[str, str] = {}
    current = ""
    buf: list[str] = []
    for line in (text or "").splitlines():
        token = line.strip().strip(":").replace(" ", "_").upper()
        if token in labels:
            if current:
                out[current] = "\n".join(buf).strip()
            current = labels[token]
            buf = []
            continue
        if line.strip() == "---":
            continue
        buf.append(line)
    if current:
        out[current] = "\n".join(buf).strip()
    return out


def qualify_note(tender: dict[str, Any], profile: dict[str, Any], decision: dict[str, Any]) -> str:
    """Plain-language reading of a GO/NO-GO on the ``qualify`` route.

    The verdict itself stays with the scoring rules: a model does not get to
    overrule the score bar or the deadline floor. It explains, in the bid
    team's words, what the numbers mean for this notice.
    """
    material = (
        f"{_facts_block(tender, profile)}\n"
        f"Fit score: {tender.get('score')} / 100 (bar: {profile.get('min_score') or 70})\n"
        f"Rule verdict: {'GO' if decision.get('go') else 'NO-GO'}\n"
        f"Rule reasons: {decision.get('reason') or ''}"
    )
    text, _model = ask(
        "qualify",
        (
            "You are a bid manager. The verdict is already decided by the "
            "scoring rules and you must not contradict it.\n"
            "Write at most three sentences explaining what this verdict means "
            "for this notice and the single thing to check next. Use only the "
            "facts given. No new figures, no markdown, no preamble."
        ),
        material,
        profile=profile,
        max_tokens=320,
        temperature=0.1,
    )
    if not text or not keeps_only_known_facts(material, text, require_gaps=False):
        return ""
    return text[:600]


def polish_mail(tender: dict[str, Any], profile: dict[str, Any], kind: str, draft: str) -> str:
    """Rewrite a buyer letter on the ``mail`` route, or return it untouched."""
    material = f"{_facts_block(tender, profile)}\n\nDraft ({kind}):\n{draft}"
    text, _model = ask(
        "mail",
        _STYLE_RULES,
        (
            f"{material}\n\n"
            "Rewrite the draft as a short, courteous letter to this "
            "contracting authority. Keep it under 180 words."
        ),
        profile=profile,
        max_tokens=600,
    )
    if not text or not keeps_only_known_facts(material, text):
        return draft
    return text
