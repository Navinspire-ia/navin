"""Tender Writer: dossier drafts from profile + notice. Never invent missing facts."""

from __future__ import annotations

import re
from typing import Any

from navin.tenders.enrich import fetch_text_is_noise
from navin.tenders.bid_pack import (
    _DURATION_FACT,
    _is_blank,
    _price_book_lines,
    crafts_cover_notice,
    detect_notice_language,
    draft_approach,
    draft_budget,
    draft_company,
    draft_cover,
    draft_followup_kpi,
    draft_functional,
    draft_governance,
    draft_letter,
    draft_methodology,
    draft_need,
    draft_raci_risks,
    draft_references,
    draft_summary,
    draft_toc,
    draft_vision,
    need_is_thin,
    notice_blobs,
    pick_notice_fact,
)
from navin.tenders.profile import NOT_ON_FILE, filed_documents, normalize_references, stated, template_excerpts

_REQ_SPLIT = re.compile(r"[\n;•●▪‣]|(?:\s+\d+[.)]\s+)|(?:\s+[-*]\s+)")
_REQ_FORCE = re.compile(
    r"\b(shall|must|doit|doivent|obligatoire|mandatory|required|exigence|"
    r"fournir|joindre|produire|justifier|justificatif|cdc|cahier)\b",
    re.I,
)
_ARCH_HINT = re.compile(
    r"architect|solution technique|stack|workstream|lot technique|"
    r"schema|livrable|composant|module|integration|volume|heberg|"
    r"postgresql|api rest|lac de donn",
    re.I,
)
_PLAN_HINT = re.compile(
    r"plann|calendrier|jalon|phase|milestone|semaine|deadline|delai|"
    r"kick.?off|remise|depot|duree|mois|jours.?homme|charge",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ö])")


def _line(label: str, value: Any) -> str:
    text = str(value or "").strip()
    return f"- {label}: {text}" if text else f"- {label}: not stated in the notice"


# Buyers read their own language. FR covers the francophone procurement space.
_FR_COUNTRIES = frozenset(
    {
        "FR", "BE", "LU", "MC", "CH", "DZ", "MA", "TN", "SN", "CI", "ML", "BF",
        "NE", "TG", "BJ", "GA", "CG", "CD", "CM", "MG", "HT", "GN", "TD", "MR",
    }
)


_CRITERIA_FR = {
    "price-weighted award": "critere prix predominant",
    "quality / MEAT": "qualite / offre economiquement la plus avantageuse",
    "not extracted - read the cahier des charges": "non extrait - lire le cahier des charges",
}


def _criteria_text(analysis: dict[str, Any], fr: bool) -> str:
    rows = [str(item) for item in (analysis.get("award_criteria") or [])]
    if fr:
        rows = [_CRITERIA_FR.get(item, item) for item in rows]
    return ", ".join(rows)


def response_language(tender: dict[str, Any], profile: dict[str, Any] | None = None) -> str:
    detected = detect_notice_language(tender)
    if detected in {"fr", "en"}:
        return detected
    country = str((tender or {}).get("country") or "").strip().upper()
    if country in _FR_COUNTRIES:
        return "fr"
    if country and country not in {"INTL", "EU"}:
        return "en"
    locale = str((profile or {}).get("locale") or "").strip().lower()
    return "fr" if locale.startswith("fr") else "en"


def analyse_tender(tender: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    hay = " ".join(
        str(tender.get(k) or "")
        for k in ("title", "description", "eligibility", "submission_method", "cdc_text")
    ).lower()
    missing: list[str] = []
    if not tender.get("budget"):
        missing.append("budget not published")
    if not tender.get("deadline"):
        missing.append("deadline missing")
    if "certif" in hay and not (profile.get("certifications") or []):
        missing.append("certifications required - none on file")
    if ("kbis" in hay or "rne" in hay) and not any(
        "kbis" in str(x).lower() or "rne" in str(x).lower()
        for x in (profile.get("documents") or [])
    ):
        missing.append("company registry extract (Kbis/RNE) not in the knowledge base")
    criteria = []
    if "lowest price" in hay or "moins-disant" in hay:
        criteria.append("price-weighted award")
    if "meilleure offre" in hay or "best value" in hay or "qualite" in hay:
        criteria.append("quality / MEAT")
    analysis = {
        "need": (
            ""
            if fetch_text_is_noise(str(tender.get("description") or ""))
            else str(tender.get("description") or "")
        )
        or tender.get("title")
        or "",
        "eligibility": tender.get("eligibility")
        or ("Voir l'avis source." if response_language(tender, profile) == "fr" else "See source notice."),
        "mandatory_docs": [
            item
            for item in (
                (
                    "lettre de candidature",
                    "offre technique",
                    "offre financiere",
                    "references",
                    "CV de l'equipe",
                )
                if response_language(tender, profile) == "fr"
                else (
                    "submission letter",
                    "technical offer",
                    "financial offer",
                    "references",
                    "staff CVs",
                )
            )
        ],
        "deadline": tender.get("deadline") or "",
        "guarantees": "bid bond likely" if "caution" in hay or "bond" in hay else "not stated",
        "certifications": ", ".join(profile.get("certifications") or []) or "none on file",
        "award_criteria": criteria or ["not extracted - read the cahier des charges"],
        "budget": tender.get("budget"),
        "risks": missing
        + (["short deadline"] if (tender.get("score_breakdown") or {}).get("days_left") is not None and int((tender.get("score_breakdown") or {}).get("days_left") or 99) < 10 else []),
        "gaps": missing,
        "source_url": tender.get("source_url"),
    }
    analysis["requirements"] = extract_requirements(tender, profile, analysis)
    return analysis


def go_nogo(tender: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    score = float(tender.get("score") or 0)
    min_score = float(profile.get("min_score") or 70)
    days = (tender.get("score_breakdown") or {}).get("days_left")
    min_deadline = int(profile.get("min_deadline_days") or 10)
    reasons: list[str] = []
    go = score >= min_score
    if days is not None and int(days) < min_deadline:
        go = False
        reasons.append(f"deadline in {days} days is below the {min_deadline}-day floor")
    if days is not None and int(days) < 0:
        go = False
        reasons.append("deadline already passed")
    if score >= min_score:
        reasons.append(f"score {score:.0f}/100 meets the {min_score:.0f} bar")
    else:
        reasons.append(f"score {score:.0f}/100 is below the {min_score:.0f} bar")
        go = False
    breakdown = tender.get("score_breakdown") or {}
    crafts = [str(c).strip() for c in (profile.get("crafts") or []) if str(c).strip()]
    if float(breakdown.get("technical") or 0) < 45:
        listed = ", ".join(crafts[:6])
        reasons.append(
            f"no match with your domains ({listed})" if listed else "weak technical fit"
        )
        go = False
    budget = tender.get("budget")
    max_budget = float(profile.get("max_budget") or profile.get("project_size_max") or 0)
    if max_budget > 0 and isinstance(budget, (int, float)) and budget > max_budget:
        go = False
        reasons.append(f"budget {budget:,.0f} is above the {max_budget:,.0f} ceiling")
    effort = 8 if go else 2
    if breakdown.get("days_left") is not None and int(breakdown["days_left"]) < 14:
        effort = max(effort, 12)
    pct = min(97, max(8, int(round(score))))
    return {
        "go": go,
        "go_pct": pct,
        "reason": "; ".join(reasons),
        "effort_days": effort,
    }


def _clip_req(text: str, limit: int = 220) -> str:
    body = " ".join(str(text or "").split())
    return body[:limit].rstrip(" .,:;") if body else ""


def _push_req(rows: list[dict[str, str]], seen: set[str], text: str, source: str) -> None:
    item = _clip_req(text)
    if len(item) < 8:
        return
    if re.match(r"(date limite|deadline)\b", item, re.I):
        return
    key = item.lower()
    if key in seen:
        return
    seen.add(key)
    rows.append({"id": str(len(rows) + 1), "text": item, "source": source})


def _split_requirements(blob: str, source: str, rows: list[dict[str, str]], seen: set[str]) -> None:
    text = str(blob or "").strip()
    if not text:
        return
    chunks = [part.strip(" \t-*.") for part in _REQ_SPLIT.split(text) if part and part.strip()]
    if len(chunks) <= 1:
        chunks = [part.strip() for part in _SENTENCE.split(text) if part.strip()]
    for chunk in chunks:
        if len(chunk) < 8:
            continue
        if len(chunks) == 1 and len(chunk) > 80 and not _REQ_FORCE.search(chunk):
            continue
        _push_req(rows, seen, chunk, source)


def extract_requirements(
    tender: dict[str, Any],
    profile: dict[str, Any],
    analysis: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Requirement rows from the notice, CDC text, eligibility and on-file docs."""
    analysis = analysis or {}
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    title = str(tender.get("title") or "").strip()
    if title:
        _push_req(rows, seen, title, "notice")
    _split_requirements(str(tender.get("eligibility") or ""), "eligibility", rows, seen)
    desc = str(tender.get("description") or "")
    cdc = str(tender.get("cdc_text") or "")
    if not fetch_text_is_noise(desc):
        _split_requirements(desc, "description", rows, seen)
    if not fetch_text_is_noise(cdc):
        _split_requirements(cdc, "cdc", rows, seen)
    _split_requirements(str(tender.get("submission_method") or ""), "submission", rows, seen)
    raw_docs = tender.get("documents") or []
    if isinstance(raw_docs, list):
        for item in raw_docs[:20]:
            if isinstance(item, dict):
                _push_req(rows, seen, str(item.get("title") or item.get("name") or item.get("url") or ""), "document")
            else:
                _push_req(rows, seen, str(item), "document")
    for item in analysis.get("mandatory_docs") or []:
        _push_req(rows, seen, str(item), "mandatory")
    for item in analysis.get("award_criteria") or []:
        token = str(item)
        if token.startswith("not extracted"):
            continue
        _push_req(rows, seen, token, "award")
    if normalize_references(profile.get("references")) or any(
        row.get("bucket") == "reference" for row in filed_documents(profile)
    ):
        _push_req(
            rows,
            seen,
            (
                "Les references au dossier doivent etre mappees a cet avis"
                if response_language(tender, profile) == "fr"
                else "References on file must be mapped to this notice"
            ),
            "profile",
        )
    return rows[:40]


def _excerpt_hits(texts: list[str], hint: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for blob in texts:
        for para in re.split(r"\n{2,}", str(blob or "")):
            body = para.strip()
            if body and hint.search(body):
                hits.append(body[:900])
        if len(hits) >= 4:
            break
    return hits[:4]


def draft_architecture(
    tender: dict[str, Any],
    profile: dict[str, Any],
    *,
    fr: bool,
    missing: str,
    analysis: dict[str, Any],
    methodology: str,
    excerpts: list[str],
) -> str:
    crafts = [str(item).strip() for item in (profile.get("crafts") or []) if str(item).strip()]
    projects = [str(item).strip() for item in (profile.get("project_types") or []) if str(item).strip()]
    specialty = stated(profile.get("specialty"), missing)
    title = str(tender.get("title") or missing)
    thin = need_is_thin(tender, analysis, missing)
    need = str(analysis.get("need") or title or missing)
    aligned = crafts_cover_notice(tender, ", ".join(crafts), profile)
    hits = _excerpt_hits([methodology, *excerpts, *notice_blobs(tender)], _ARCH_HINT)
    if fr:
        lines = [
            "Architecture de livraison proposee a partir des metiers et de la methode au dossier.",
            f"Avis : {title}",
        ]
        if thin:
            lines.append(
                "Le CDC n'est pas lisible. Aucune architecture de solution n'est inventee a partir de l'intitule."
            )
        else:
            lines.append(f"Besoin de l'avis : {need}")
        lines.extend(
            [
                f"Specialite au dossier : {specialty}." if specialty != missing else "Specialite au dossier : a completer.",
                f"Metiers au dossier : {', '.join(crafts) or 'a completer au dossier'}.",
                f"Projets deja realises : {', '.join(projects) or 'a completer au dossier'}.",
                "",
                "La reponse technique ne nomme aucune stack, volumetrie ou schema absent du dossier.",
            ]
        )
        if crafts:
            lines.append("")
            if aligned:
                lines.append("Lots techniques (domaines au dossier qui recouvrent l'avis)")
                for craft in crafts[:8]:
                    lines.append(
                        f"- {craft} : lot tenu pour couvrir le besoin ci-dessus, "
                        "dans le perimetre deja declare, sans composant invente."
                    )
            else:
                lines.append("Metiers au dossier - non poses comme lots de cet avis")
                for craft in crafts[:8]:
                    lines.append(
                        f"- {craft} : capacite au fichier societe, pas un lot de ce marche."
                    )
        if hits:
            lines.append("")
            lines.append("Extraits modeles / methode / avis conserves")
            lines.extend(f"- {hit}" for hit in hits)
        else:
            lines.append("")
            lines.append(
                "Aucun schema, volume ou stack n'est nomme dans l'avis. Rien n'est invente."
            )
        return "\n".join(lines)
    lines = [
        "Delivery architecture proposed from on-file crafts and method.",
        f"Notice: {title}",
    ]
    if thin:
        lines.append(
            "The specification is not readable. No solution architecture is invented from the title."
        )
    else:
        lines.append(f"Notice need: {need}")
    lines.extend(
        [
            f"On-file specialty: {specialty}." if specialty != missing else "On-file specialty: still to complete.",
            f"On-file crafts: {', '.join(crafts) or 'still to complete on file'}.",
            f"Completed project types: {', '.join(projects) or 'still to complete on file'}.",
            "",
            "The technical response names no stack, volume or schema that is not on file.",
        ]
    )
    if crafts:
        lines.append("")
        if aligned:
            lines.append("Technical lots (on-file domains that cover the notice)")
            for craft in crafts[:8]:
                lines.append(
                    f"- {craft}: lot held to cover the need above, "
                    "within the declared scope, with no invented component."
                )
        else:
            lines.append("On-file crafts - not presented as lots for this notice")
            for craft in crafts[:8]:
                lines.append(
                    f"- {craft}: company-file capability, not a lot of this contract."
                )
    if hits:
        lines.append("")
        lines.append("Kept model / method / notice extracts")
        lines.extend(f"- {hit}" for hit in hits)
    else:
        lines.append("")
        lines.append(
            "No schema, volume or stack is named in the notice. Nothing is invented."
        )
    return "\n".join(lines)


def draft_planning(
    tender: dict[str, Any],
    profile: dict[str, Any],
    *,
    fr: bool,
    missing: str,
    excerpts: list[str],
    methodology: str,
    staffing: str,
) -> str:
    deadline = stated(tender.get("deadline"), missing)
    duration = pick_notice_fact(tender, _DURATION_FACT)
    hits = _excerpt_hits([methodology, *excerpts, *notice_blobs(tender)], _PLAN_HINT)
    if fr:
        lines = [
            "Planning et jalons. Les seules dates fermes sont celles lues dans l'avis.",
            f"Date limite : {deadline}.",
            f"Charge / duree de mission : {duration}." if duration else "Charge / duree de mission : non lue dans l'avis. Aucune duree n'est inventee.",
            f"Equipe au dossier :\n{staffing}",
            "",
            "A. Calendrier de depot de l'offre",
            "1. Cadrage et lecture du CDC / de l'avis",
            "2. Offre technique (architecture, methode, references)",
            "3. Offre financiere et pieces administratives",
            "4. Relecture interne et remarques",
            f"5. Depot avant {deadline}",
            "",
            "B. Jalons de realisation (ordre de travail, sans dates inventees)",
            "1. Lancement et cadrage",
            "2. Conception fonctionnelle et technique",
            "3. Realisation et integrations",
            "4. Recette, transfert et cloture",
        ]
        if hits:
            lines.append("")
            lines.append("Phases deja presentes dans la methode ou les modeles :")
            lines.extend(f"- {hit}" for hit in hits)
        return "\n".join(lines)
    lines = [
        "Schedule and milestones. The only firm dates are those read in the notice.",
        f"Deadline: {deadline}.",
        f"Mission load / duration: {duration}." if duration else "Mission load / duration: not read in the notice. No duration is invented.",
        f"Team on file:\n{staffing}",
        "",
        "A. Offer filing calendar",
        "1. Frame the notice / CDC",
        "2. Technical offer (architecture, method, references)",
        "3. Financial offer and admin exhibits",
        "4. Internal review and remarks",
        f"5. File before {deadline}",
        "",
        "B. Delivery milestones (work order only, no invented dates)",
        "1. Kick-off and framing",
        "2. Functional and technical design",
        "3. Build and integrations",
        "4. Acceptance, transfer and close-out",
    ]
    if hits:
        lines.append("")
        lines.append("Phases already on file in the method or models:")
        lines.extend(f"- {hit}" for hit in hits)
    return "\n".join(lines)


def _answer_for_requirement(
    item: dict[str, str],
    *,
    crafts: str,
    tender_types: str,
    project_types: str,
    refs_ok: bool,
    missing: str,
    deadline: str,
    methodology: str,
    price_on_file: bool,
    fr: bool,
) -> tuple[str, str]:
    todo = "a completer au dossier societe" if fr else "still to complete on file"
    text = str(item.get("text") or "").lower()
    source = str(item.get("source") or "")
    if source == "deadline" or "deadline" in text or "date limite" in text:
        return (deadline, "Avis" if fr else "Notice")
    if source == "profile" or "reference" in text:
        proof = "Base de connaissances" if fr else "Knowledge base"
        return (("au dossier" if fr else "on file") if refs_ok else todo, proof)
    if source in {"mandatory", "document"}:
        if "reference" in text:
            return (("au dossier" if fr else "on file") if refs_ok else todo, "Dossier" if fr else "File")
        if any(token in text for token in ("cv", "staff", "equipe", "team")):
            return (stated(crafts, todo), "Equipe" if fr else "Team")
        if any(token in text for token in ("financ", "price", "prix", "bordereau")):
            if price_on_file:
                return ("bordereau au dossier" if fr else "price book on file", "Dossier" if fr else "File")
            return (todo, "Dossier" if fr else "File")
        return (methodology[:180] if methodology and not _is_blank(methodology, missing) else todo, "Methode" if fr else "Method")
    if source == "award":
        return (f"{crafts} / {tender_types}", "Profil" if fr else "Profile")
    if source in {"eligibility", "cdc", "description", "notice", "submission"}:
        mapped = ", ".join(part for part in (crafts, tender_types, project_types) if part and not _is_blank(part, missing))
        return (mapped or todo, "Profil + avis" if fr else "Profile + notice")
    return (
        ("a traiter a la revue, sans invention" if fr else "to be handled at review, nothing invented"),
        ("Avis" if fr else "Notice"),
    )


def build_compliance_matrix(
    tender: dict[str, Any],
    profile: dict[str, Any],
    *,
    fr: bool,
    missing: str,
    crafts: str,
    tender_types: str,
    project_types: str,
    methodology: str,
) -> str:
    analysis = tender.get("analysis") if isinstance(tender.get("analysis"), dict) else {}
    reqs = analysis.get("requirements") if isinstance(analysis.get("requirements"), list) else None
    if not reqs:
        reqs = extract_requirements(tender, profile, analysis)
    refs_ok = bool(normalize_references(profile.get("references"))) or any(
        row.get("bucket") == "reference" for row in filed_documents(profile)
    )
    deadline = stated(tender.get("deadline"), missing)
    price_on_file = bool(_price_book_lines(profile, missing))
    header = (
        ["| # | Exigence | Reponse | Preuve |", "| --- | --- | --- | --- |"]
        if fr
        else ["| # | Requirement | Answer | Evidence |", "| --- | --- | --- | --- |"]
    )
    lines = list(header)
    for index, item in enumerate(reqs[:40], start=1):
        if not isinstance(item, dict):
            item = {"text": str(item), "source": "notice"}
        answer, proof = _answer_for_requirement(
            item,
            crafts=crafts,
            tender_types=tender_types,
            project_types=project_types,
            refs_ok=refs_ok,
            missing=missing,
            deadline=deadline,
            methodology=methodology,
            price_on_file=price_on_file,
            fr=fr,
        )
        req = str(item.get("text") or "").replace("|", "/")
        lines.append(f"| {index} | {req} | {answer.replace('|', '/')} | {proof.replace('|', '/')} |")
    return "\n".join(lines)


def build_response(tender: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    lang = response_language(tender, profile)
    fr = lang == "fr"
    missing = "non renseigne" if fr else NOT_ON_FILE
    company = stated(profile.get("name"), "le candidat" if fr else "the bidder")
    crafts = ", ".join(profile.get("crafts") or []) or missing
    tender_types = ", ".join(profile.get("tender_types") or []) or missing
    project_types = ", ".join(profile.get("project_types") or []) or missing
    specialty = stated(profile.get("specialty"), missing)
    strengths = ", ".join(profile.get("strengths") or []) or missing
    refs = normalize_references(profile.get("references"))
    filed = filed_documents(profile)
    style_docs = [row for row in filed if row.get("bucket") in {"word", "ppt", "reuse_slides"} and row.get("excerpt")]
    ref_docs = [row for row in filed if row.get("bucket") == "reference"]
    ref_lines = "\n".join(
        (
            f"- {item.get('title')} ({item.get('client') or item.get('year') or item.get('country') or 'au dossier'})"
            + (f"\n  {str(item.get('excerpt') or '').strip()[:500]}" if str(item.get("excerpt") or "").strip() else "")
        ).strip()
        for item in (ref_docs or refs)[:12]
    )
    excerpts = template_excerpts(profile)
    style_quote = next((row.get("excerpt") or "" for row in style_docs), "")
    style_names = ", ".join(row.get("name") or row.get("title") or row.get("label") for row in style_docs)
    analysis = tender.get("analysis") or analyse_tender(tender, profile)
    title = tender.get("title") or ("l'avis" if fr else "the notice")
    buyer = stated(tender.get("buyer"), "l'acheteur public" if fr else "the contracting authority")
    deadline = stated(tender.get("deadline"), "voir l'avis" if fr else "see notice")
    portal = stated(tender.get("source_url"), "voir la source" if fr else "see source")
    criteria = _criteria_text(analysis, fr)
    need = str(analysis.get("need") or title)
    if need_is_thin(tender, analysis, missing):
        need = (
            "l'avis ne livre que l'intitule ; le CDC n'a pas ete lu."
            if fr
            else "the notice only states the title; the specification was not read."
        )
    letter = draft_letter(
        tender,
        profile,
        lang=lang,
        missing=missing,
        company=company,
        specialty=specialty,
        crafts=crafts,
        tender_types=tender_types,
        project_types=project_types,
        strengths=strengths,
        buyer=buyer,
        deadline=deadline,
        portal=portal,
        title=title,
        need=need,
        style_docs=style_docs,
        style_names=style_names,
        style_quote=style_quote,
        excerpts=excerpts,
    )
    summary = draft_summary(tender, analysis, lang=lang, company=company, title=title, criteria=criteria)
    financial = draft_budget(tender, profile, lang=lang, missing=missing)
    methodology = stated(profile.get("methodology"), missing)
    if style_docs:
        blocks = []
        if methodology != missing:
            blocks.append(methodology)
        for row in style_docs:
            label = row.get("name") or row.get("label") or "model"
            blocks.append(f"{label}: {str(row.get('excerpt') or '')[:1800]}")
        methodology = "\n\n".join(blocks)
    elif methodology == missing and excerpts:
        methodology = excerpts[0][:800]
    team_rows = []
    for item in (profile.get("team") or [])[:8]:
        if isinstance(item, dict):
            role = str(item.get("role") or "").strip() or ("role a pourvoir" if fr else "role to staff")
            name = str(item.get("name") or "").strip() or ("nom a pourvoir" if fr else "name to staff")
            team_rows.append(f"- {role}: {name}")
        elif str(item).strip():
            team_rows.append(f"- {item}")
    staffing = "\n".join(team_rows) or (
        "Aucun nom au dossier societe. Les roles du RACI restent a pourvoir."
        if fr
        else "No name on the company file. RACI roles stay to be staffed."
    )
    excerpt_texts = [str(row.get("excerpt") or "") for row in style_docs if row.get("excerpt")]
    excerpt_texts.extend(excerpts)
    architecture = draft_architecture(
        tender,
        profile,
        fr=fr,
        missing=missing,
        analysis=analysis,
        methodology=methodology,
        excerpts=excerpt_texts,
    )
    planning = draft_planning(
        tender,
        profile,
        fr=fr,
        missing=missing,
        excerpts=excerpt_texts,
        methodology=methodology,
        staffing=staffing,
    )
    matrix = build_compliance_matrix(
        tender,
        profile,
        fr=fr,
        missing=missing,
        crafts=crafts,
        tender_types=tender_types,
        project_types=project_types,
        methodology=methodology,
    )
    company_txt = draft_company(
        tender,
        profile,
        lang=lang,
        missing=missing,
        company=company,
        crafts=crafts,
        tender_types=tender_types,
        project_types=project_types,
        specialty=specialty,
        strengths=strengths,
    )
    need_txt = draft_need(
        tender,
        analysis,
        lang=lang,
        missing=missing,
        buyer=buyer,
        criteria=criteria,
    )
    return {
        "cover": draft_cover(tender, profile, lang=lang, missing=missing, company=company),
        "toc": draft_toc(lang),
        "letter": letter,
        "executive_summary": summary,
        "company": company_txt,
        "need": need_txt,
        "approach": draft_approach(
            tender, profile, lang=lang, missing=missing, methodology=methodology, crafts=crafts
        ),
        "vision": draft_vision(
            tender,
            analysis,
            lang=lang,
            missing=missing,
            company=company,
            criteria=criteria,
            strengths=strengths,
        ),
        "functional": draft_functional(
            tender, analysis, lang=lang, missing=missing, crafts=crafts, methodology=methodology
        ),
        "architecture": architecture,
        "methodology": draft_methodology(
            tender, lang=lang, missing=missing, methodology=methodology, crafts=crafts
        ),
        "followup_kpi": draft_followup_kpi(
            tender, analysis, lang=lang, missing=missing, deadline=deadline
        ),
        "raci_risks": draft_raci_risks(
            tender, profile, analysis, lang=lang, missing=missing, staffing=staffing
        ),
        "governance": draft_governance(
            tender, profile, lang=lang, missing=missing, buyer=buyer, staffing=staffing
        ),
        "planning": planning,
        "staffing": staffing,
        "references": draft_references(ref_lines, lang=lang, missing=missing, title=title),
        "compliance_matrix": matrix,
        "financial_schedule": financial,
        "language": lang,
        "send_mode": profile.get("send_mode") or "approval",
        "used_files": [row.get("name") or row.get("title") for row in filed if row.get("name") or row.get("title")],
        "from_file": bool(style_docs or any(row.get("excerpt") for row in ref_docs)),
        "ready": True,
        "requirements_count": len(analysis.get("requirements") or extract_requirements(tender, profile, analysis)),
    }


def commercial_draft(
    tender: dict[str, Any],
    kind: str,
    profile: dict[str, Any] | None = None,
) -> str:
    lang = response_language(tender, profile) if profile is not None else "en"
    title = tender.get("title") or ("l'avis" if lang == "fr" else "the tender")
    if lang == "fr":
        if kind == "clarification":
            return (
                f"Madame, Monsieur,\n\nNous sollicitons les precisions suivantes sur {title} :\n"
                "- format de remise et compte sur le portail\n"
                "- admission des variantes\n"
                "- visite de site et date limite des questions\n"
            )
        if kind == "ack":
            return f"Nous accusons reception de votre courrier concernant {title}."
        if kind == "relance":
            return (
                f"Suite au depot de notre offre sur {title}, nous restons a votre disposition "
                "pour toute precision et vous remercions de nous indiquer le calendrier d'analyse."
            )
        return f"Brouillon concernant {title}."
    if kind == "clarification":
        return (
            f"Dear contracting authority,\n\nPlease clarify the following on {title}:\n"
            "- submission format and portal account\n"
            "- whether variants are admitted\n"
            "- site visit / Q&A deadline\n"
        )
    if kind == "ack":
        return f"We acknowledge receipt of your correspondence regarding {title}."
    if kind == "relance":
        return (
            f"Following our submission on {title}, we remain available for any "
            "clarification and kindly ask for an update on the evaluation timetable."
        )
    return f"Draft regarding {title}."
