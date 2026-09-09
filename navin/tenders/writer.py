"""Tender Writer: dossier drafts from profile + notice. Never invent missing facts."""

from __future__ import annotations

import re
from typing import Any

from navin.tenders.bid_pack import (
    _DURATION_FACT,
    _is_blank,
    build_evidence_register,
    build_requirement_responses,
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
    missing_certifications,
    need_is_thin,
    notice_blobs,
    pick_notice_fact,
    render_requirement_sections,
    required_certifications,
)
from navin.tenders.enrich import fetch_text_is_noise
from navin.tenders.profile import (
    NOT_ON_FILE,
    filed_documents,
    normalize_references,
    stated,
    template_excerpts,
)

_REQ_SPLIT = re.compile(r"\n+|[•●▪‣]")
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
    certs_missing = missing_certifications(tender, profile)
    missing.extend(f"required certification absent from company file: {cert}" for cert in certs_missing)
    if "certif" in hay and not required_certifications(tender) and not (profile.get("certifications") or []):
        missing.append("certification requirement needs clarification - none on file")
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
        "mandatory_docs": [label for pattern, label in (
            (r"lettre de candidature|submission letter", "lettre de candidature"),
            (r"memoire technique|offre technique|technical (?:offer|memorandum)", "offre technique"),
            (r"offre financi|bordereau|price schedule|financial offer", "offre financiere"),
            (r"r[eé]f[eé]rences|references", "references"),
            (r"\bcv\b|staff cvs|resumes", "CV de l'equipe"),
        ) if re.search(pattern, hay, re.I)],
        "deadline": tender.get("deadline") or "",
        "guarantees": "bid bond likely" if "caution" in hay or "bond" in hay else "not stated",
        "certifications": ", ".join(profile.get("certifications") or []) or "none on file",
        "award_criteria": criteria or ["not extracted - read the cahier des charges"],
        "budget": tender.get("budget"),
        "risks": missing
        + (["short deadline"] if (tender.get("score_breakdown") or {}).get("days_left") is not None and int((tender.get("score_breakdown") or {}).get("days_left") or 99) < 10 else []),
        "gaps": missing,
        "eligibility_blockers": [f"required certification absent from company file: {cert}" for cert in certs_missing],
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
    for certification in missing_certifications(tender, profile):
        go = False
        reasons.append(f"required certification absent from company file: {certification}")
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


def _clip_req(text: str, limit: int | None = None) -> str:
    body = " ".join(str(text or "").split())
    return body[:limit] if limit else body


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
    buyer = re.match(r"^((?:EXG|REQ|R|CCTP)[- .]?\d+(?:[.-]\d+)*|\d+(?:\.\d+)*[.)])\s*", item, re.I)
    rows.append({
        "id": f"R{len(rows) + 1:03d}", "text": item, "source": source,
        "buyer_id": buyer.group(1).rstrip(".)") if buyer else "",
        "kind": "context" if source in {"notice", "award", "document"} else "requirement",
    })


def _split_requirements(blob: str, source: str, rows: list[dict[str, str]], seen: set[str]) -> None:
    text = str(blob or "").strip()
    if not text:
        return
    chunks = [part.strip(" \t-*") for part in _REQ_SPLIT.split(text) if part and part.strip()]
    if source == "eligibility":
        chunks = [part.strip() for chunk in chunks for part in re.split(r"[;,]|\b(?:et|and)\s+(?=(?:(?:les|des|the)\s+)?(?:CV|curriculum))", chunk, flags=re.I) if part.strip()]
        chunks = [part.strip() for chunk in chunks for part in _SENTENCE.split(chunk) if part.strip()]
    if len(chunks) <= 1:
        chunks = [part.strip() for part in _SENTENCE.split(text) if part.strip()]
    for chunk in chunks:
        if len(chunk) < 8:
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
    _split_requirements(str(tender.get("eligibility") or ""), "eligibility", rows, seen)
    desc = str(tender.get("description") or "")
    cdc = str(tender.get("cdc_text") or "")
    if not fetch_text_is_noise(cdc):
        _split_requirements(cdc, "cdc", rows, seen)
    if not fetch_text_is_noise(desc):
        description_rows: list[dict[str, str]] = []
        _split_requirements(desc, "description", description_rows, set())
        numbered_cdc = sum(bool(row.get("buyer_id")) for row in rows if row.get("source") == "cdc") >= 2
        cdc_words = {word.lower() for word in re.findall(r"\w{4,}", cdc)}
        cdc_numbers = set(re.findall(r"\d+", cdc))
        for row in description_rows:
            text = row["text"]
            words = {word.lower() for word in re.findall(r"\w{4,}", text)} - {"doit", "titulaire", "prestataire", "assurer", "marche", "fictif", "contractuelle"}
            numbers = set(re.findall(r"\d+", text))
            # Detailed numbered CDC takes precedence over its executive synopsis.
            # An additional obligation or quantity absent from the CDC remains a source row.
            covered = bool(words) and len(words & cdc_words) / len(words) >= 0.45
            if numbered_cdc and not (numbers - cdc_numbers) and (covered or not _REQ_FORCE.search(text)):
                continue
            _push_req(rows, seen, text, "description")
    _split_requirements(str(tender.get("submission_method") or ""), "submission", rows, seen)
    raw_docs = tender.get("documents") or []
    if isinstance(raw_docs, list):
        for item in raw_docs:
            if isinstance(item, dict):
                if item.get("text") or item.get("excerpt"):
                    _split_requirements(str(item.get("text") or item.get("excerpt")), "document", rows, seen)
            else:
                continue
    return rows


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
    lang = "fr" if fr else "en"
    evidence = build_evidence_register(profile)
    rows = build_requirement_responses(extract_requirements(tender, profile), profile, evidence, lang=lang)
    return render_requirement_sections(tender, profile, rows, evidence, lang=lang)["compliance_matrix"]


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
    analysis = analyse_tender(tender, profile)
    # Stored analyses may predate source enrichment or the extraction contract.
    # Rebuild from the current notice so late CDC clauses cannot disappear.
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
    response = {
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
    evidence = build_evidence_register(profile)
    requirements = build_requirement_responses(analysis["requirements"], profile, evidence, lang=lang)
    response.update({"requirement_responses": requirements, "evidence_register": evidence})
    response = assemble_requirement_response(tender, profile, response)
    warning_labels = {
        "budget not published": "Budget de l'acheteur non publie.",
        "deadline missing": "Date limite de depot absente.",
        "company registry extract (Kbis/RNE) not in the knowledge base": "Extrait Kbis/RNE demande mais absent du dossier societe.",
        "certification requirement needs clarification - none on file": "Certification demandee a preciser; aucune certification declaree.",
    }
    warnings = [
        warning_labels.get(warning, warning.replace("required certification absent from company file: ", "Certification demandee absente du dossier societe: ")) if fr else warning
        for warning in analysis.get("gaps") or []
    ]
    if need_is_thin(tender, analysis, missing):
        warnings.append("Cahier des charges incomplet: faire preciser le perimetre, les livrables, les volumes et la recette." if fr else "Specification incomplete: validate scope, deliverables, volumes and acceptance with the buyer.")
    if not evidence:
        warnings.append("Aucune preuve justificative disponible dans le profil societe." if fr else "No supporting evidence available in the company file.")
    warnings.extend(f"{row['id']}: justificatif manquant ou insuffisant." if fr else f"{row['id']}: missing or insufficient supporting evidence." for row in requirements if row["status"] in {"missing_evidence", "insufficient_evidence"})
    from navin.agent.skill_routing import build_action_skill_context

    skills = build_action_skill_context("tenders", "write")
    response.update({
        "generation": {"mode": "deterministic", "status": "needs_review", "warnings": list(dict.fromkeys(warnings)), "skills": skills.metadata},
        "review_needed": True,
        "submission_ready": False,
        "requirements_count": len(requirements),
        "coverage": {"extracted": len(requirements), "drafted": len(requirements), "omitted": 0,
                     "source_fields": [key for key in ("title", "description", "eligibility", "cdc_text", "submission_method", "documents") if tender.get(key)]},
        "template_material": [{"name": row.get("name") or row.get("title"), "bucket": row.get("bucket"), "excerpt": row.get("excerpt") or ""} for row in style_docs],
    })
    return response


def assemble_requirement_response(tender: dict[str, Any], profile: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """Keep facts and source constraints canonical while rendering proposed methods."""
    out = dict(response)
    lang = str(out.get("language") or response_language(tender, profile))
    fr = lang == "fr"
    rows = list(out.get("requirement_responses") or [])
    evidence = list(out.get("evidence_register") or [])
    sections = render_requirement_sections(tender, profile, rows, evidence, lang=lang)
    risk_register = sections.pop("risk_register")
    out.update(sections)
    company = str(profile.get("name") or ("Le candidat" if fr else "The bidder"))
    title = str(tender.get("title") or ("l'avis" if fr else "the notice"))
    buyer = str(tender.get("buyer") or ("l'acheteur" if fr else "the buyer"))
    specialty = str(profile.get("specialty") or "").strip()
    declared_crafts = ", ".join(str(item) for item in profile.get("crafts") or [])
    cdc = str(tender.get("cdc_text") or "")
    context = str(tender.get("description") or "")
    if fetch_text_is_noise(context):
        context = ""
    if fetch_text_is_noise(cdc):
        cdc = ""
    # A submission letter states the bid's intent, not the generator's internal workflow.
    if fr:
        out["letter"] = (
            f"Objet : Candidature - {title}\n\nMadame, Monsieur,\n\n"
            f"{company} vous presente sa proposition pour {title}. "
            + (f"Notre domaine declare est {specialty}. " if specialty else "")
            + (f"Competences declarees: {declared_crafts}. " if declared_crafts else "")
            + "Le memoire joint expose notre comprehension du besoin, la mise en oeuvre proposee et les conditions de verification des livrables.\n\n"
            "La reponse suit les exigences de l'avis et du cahier des charges disponibles. La matrice associe chaque point a sa fiche de reponse et aux pieces justificatives identifiees. "
            "Les choix soumis a votre validation et les reserves sont regroupes dans le registre correspondant.\n\n"
            "Nous restons a votre disposition pour preciser cette proposition et convenir des conditions de sa mise en oeuvre.\n\n"
            f"Veuillez agreer, Madame, Monsieur, nos salutations distinguees.\n{company}"
        )
    else:
        out["letter"] = (
            f"Subject: Submission - {title}\n\nDear {buyer},\n\n"
            f"{company} presents its proposal for {title}. "
            + (f"Our declared specialty is {specialty}. " if specialty else "")
            + (f"Declared competencies: {declared_crafts}. " if declared_crafts else "")
            + "The enclosed memorandum sets out our understanding, proposed implementation and deliverable verification conditions.\n\n"
            "The response follows the available notice and specification. The matrix links each item to its response and identified supporting evidence. "
            "Choices requiring your approval and qualifications are collected in the validation register.\n\n"
            f"We remain available to clarify the proposal and agree its implementation conditions.\n\nYours faithfully,\n{company}"
        )
    missing = "non renseigne" if fr else NOT_ON_FILE
    analysis = analyse_tender(tender, profile)
    thin = need_is_thin(tender, analysis, missing)
    constraints = []
    for label, key in (("Acheteur" if fr else "Buyer", "buyer"), ("Date limite de depot" if fr else "Submission deadline", "deadline"), ("Budget publie, distinct du prix propose" if fr else "Published budget, separate from the proposed price", "budget")):
        if tender.get(key):
            constraints.append(f"- {label}: {tender[key]}" + (f" {tender.get('currency') or profile.get('currency') or ''}" if key == "budget" else ""))
    opening = (f"{company} propose de traiter {title}." if fr else f"{company} proposes to deliver {title}.")
    if thin:
        opening += " Le CDC n'est pas lisible: les livrables, volumes et conditions de recette doivent etre precises avant engagement." if fr else " The specification is unreadable: deliverables, volumes and acceptance conditions need clarification before commitment."
    else:
        opening += " " + context
    out["executive_summary"] = "\n\n".join([opening, (f"La matrice couvre {len(rows)} points source, sans conclure automatiquement a la conformite. Les fiches precisent la mise en oeuvre, les livrables, les tests et les dependances; les references et certifications restent soumises au controle des pieces." if fr else f"The matrix covers {len(rows)} source items without automatically establishing compliance. Responses define implementation, deliverables, tests and dependencies; references and certifications still require evidence checks."), "\n".join(constraints), ("Points d'admissibilite a lever: " if fr else "Eligibility issues to resolve: ") + ("; ".join(missing_certifications(tender, profile)) if missing_certifications(tender, profile) else ("verifier les justificatifs et les conditions completes de la consultation." if fr else "verify supporting evidence and the complete tender rules."))])
    out["need"] = "\n\n".join([opening, ("Contraintes publiees" if fr else "Published constraints") + ":\n" + "\n".join(constraints), ("Les fiches de reponse conservent le texte integral de chaque point extrait et sa source. Les titres et pieces documentaires donnent le contexte; ils ne suffisent pas a prouver une exigence satisfaite." if fr else "Response sheets preserve each extracted source item in full. Titles and document names provide context; they do not prove that a requirement is met.")])
    out["approach"] = ("Demarche de realisation: faire approuver les donnees d'entree et les criteres de recette, concevoir les contrats et les flux, produire les livrables verifies, puis organiser la recette et le transfert. Chaque passage de phase utilise les preuves definies dans les fiches de reponse; les decisions bloquantes sont tracees dans le registre de risques." if fr else "Delivery approach: approve input data and acceptance criteria, design contracts and flows, produce verified deliverables, then arrange acceptance and handover. Each phase gate uses evidence defined in the response sheets; blocking decisions are tracked in the risk register.")
    out["vision"] = ("Vision cible: un resultat exploitable et verifiable pour " + buyer + ". Les priorites techniques proviennent des exigences citees, et les choix absents du cahier restent des propositions. Le transfert s'appuie sur la documentation de la solution effectivement livree et sur une mise en situation des destinataires." if fr else "Target outcome: an operable, verifiable result for " + buyer + ". Technical priorities follow the cited requirements; choices absent from the specification remain proposals. Handover uses documentation of the delivered solution and practical exercises by its recipients.")
    # Keep role assignments separate from names declared in the company file.
    out["raci_risks"] = ("Roles proposes, affectation et disponibilite a faire confirmer.\n| Activite | Responsable | Approbateur | Consultes |\n| --- | --- | --- | --- |\n| Perimetre et recette | Chef de projet | Referent acheteur habilite | Equipe metier et technique |\n| Conception et realisation | Responsable technique a designer | Chef de projet | Exploitation et securite |\n| Mise en service | Responsable exploitation a designer | Referent acheteur habilite | Chef de projet |\n\n" if fr else "Proposed roles; assignments and availability require confirmation.\n| Activity | Responsible | Accountable | Consulted |\n| --- | --- | --- | --- |\n| Scope and acceptance | Project manager | Authorised buyer owner | Business and technical team |\n| Design and implementation | Technical lead to appoint | Project manager | Operations and security |\n| Release | Operations owner to appoint | Authorised buyer owner | Project manager |\n\n") + risk_register
    out["raci_risks"] = "RACI\n" + out["raci_risks"]
    out["architecture"] = title + "\n\n" + out["architecture"]
    if thin:
        out["architecture"] += ("\n\nLe CDC n'est pas lisible. Chaque metier du profil n'est pas un lot de ce marche; aucun metier ne cree un engagement technique absent de l'avis." if fr else "\n\nThe specification is unreadable. A company craft is not a contract work package and does not create a technical commitment absent from the notice.")
    source_terms = list(dict.fromkeys(re.findall(r"\b(?:PostgreSQL|API REST|SQL|Kubernetes|Python|Oracle|SAP|RPO[^.;\n]{0,60}|RTO[^.;\n]{0,60}|\d+\s+(?:sources?|mois|jours-homme|heures?|months?|hours?))\b", context + "\n" + cdc, re.I)))
    if source_terms:
        out["architecture"] += ("\n\nContraintes et technologies citees par l'acheteur: " if fr else "\n\nBuyer-stated constraints and technologies: ") + "; ".join(source_terms) + "."
    out["planning"] = draft_planning(tender, profile, fr=fr, missing=missing, excerpts=[], methodology=str(profile.get("methodology") or ""), staffing=str(out.get("staffing") or "")) + ("\n\nLes jalons de realisation sont soumis aux conditions de passage de la methodologie: cadrage approuve, conception validee, preuves de tests, decision de recette, transfert. Les dates intermediaires et la charge sont a estimer apres validation des acces et des ressources; aucune repartition de la duree totale n'est supposee." if fr else "\n\nDelivery milestones follow the methodology gates: approved scope, approved design, test evidence, acceptance decision and handover. Intermediate dates and effort must be estimated after access and staffing approval; no allocation of the overall duration is assumed.")
    return out


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
