# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Skill-guided Career revisions validated against candidate facts before export."""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

from navin.agent.skill_routing import build_action_skill_context
from navin.career.writer import (
    _fold,
    _on_file_text,
    _strings,
    clean_text,
    has_term,
    job_keywords,
    supports_term,
    synchronize_pack,
)
from navin.tenders.ai import ask, task_role

_OFF = {"0", "off", "false", "no"}
_MAX_MATERIAL_CHARS = 160_000
_STYLE = """
You edit one recruiter-facing CV and its cover letter in the supplied document_language.
Apply the supplied Career skills to the candidate's actual evidence. The offer is a target,
never evidence of the candidate's experience. Do not invent employers, dates, diplomas,
tools, certifications, seniority, numbers, results or language levels.
Keep the master CV and every role intact. Prioritize relevant achievements within each role.
Use concise, natural language. No process commentary, ATS instructions, 'on-file facts',
missing-field markers, invented claims or padding in the CV or letter.
The output contract below is mandatory, even if a skill illustrates another output format.

Return a JSON object with optional keys summary, experiences, skill_order and cover:
{
  "summary": {"text": "Concise candidate profile", "source_ids": ["profile"]},
  "experiences": [
    {"id": "experience-0", "bullets": [
      {"text": "Faithful achievement wording", "source_ids": ["experience-0:bullet-0"]}
    ]}
  ],
  "skill_order": ["exact skill strings from the canonical CV"],
  "cover": [
    {"text": "One complete letter paragraph", "source_ids": ["profile"]}
  ]
}
Only propose changed fields. Do not return a replacement CV, name, job title, company,
dates, education or language. These remain immutable. Experience source_ids must belong
to that role, cover every original bullet exactly once, and preserve its figures and tools.
Keep all supplied skill strings when reordering. Source IDs must exist in the evidence
catalog. Summary and cover may cite profile, master, or specific evidence IDs.
Paraphrase closely: change wording and order, not scope or factual meaning.
Keep greetings and sign-off in the cover when proposing it. No Markdown fences.
Only a pure greeting, polite closing without factual claims, or the candidate's exact
signature may use source_ids: []. Every claim about experience or skills needs sources.
""".strip()

# These words join facts into prose. Substantive terms still need source evidence.
_EDITORIAL = frozenset("""
a an and are as at be been by for from in into is it of on or our that the their
these this to we will with you your i my me have has am can would could about
de du des le la les un une d l et en au aux avec pour par sur dans chez qui que
nous vous votre vos notre nos ses son est sont etre avoir ce ces cette je mon ma
mes me m j ai a une ainsi notamment afin vers the role poste candidature application
candidate profile profil parcours competences skills experience equipe team
work travail travaux contribution contribuer contribute contributions professional
professionnel professionnelle professionnelles professionnels recent recente recents
recentes avec notamment utilisation utiliser using
used use usage pratique pratiques practical projet projets project projects
madame monsieur dear hiring cordialement regards kind sincerely bonjour hello
interesse interested interessees interesse occasion opportunity priorites priorities
echange discuter discussion discuss conversation rencontrer rencontre welcome propose
proposer heureux heureuse pleased disponible disponibilite available availability
apporter bring bringing service rejoindre join rejoindre precisions preciser precis
precise precision ensemble together further thank thanks merci bien best vous votre
faisant faits achievements realisations objectif objectifs souhaite souhaitons wish
suivants suivantes following including includes comprend comprennent illustre
""".split())
_PROSE_EDITORIAL = frozenset("""
suis serais serions serais serait etre ravie ravi ravis postule postuler postulerai
besoin besoins lien correspond correspondre directement dont maniere peut pourrais mettre
pourrions attente retour prie agreer expression salutations distinguees distingue how
echanger egalement egal serve serving believe apply applying look forward sincerely
demontre demontrer demontrant demonstration capacite capability
""".split())
_ROOT_GROUPS = (
    ("developpe", "developper", "developpement", "developed", "developing", "development", "built", "build"),
    ("concu", "concevoir", "conception", "designed", "design"),
    ("automatise", "automatiser", "automatisation", "automated", "automating", "automation"),
    ("optimise", "optimiser", "optimisation", "optimized", "optimizing", "optimization"),
    ("reduit", "reduire", "reduisant", "reduction", "reduced", "reducing", "reduction"),
    ("augmente", "augmenter", "augmentation", "increased", "increasing", "increase"),
    ("ameliore", "ameliorer", "amelioration", "improved", "improving", "improvement"),
    ("migre", "migrer", "migration", "migrated", "migrating"),
    ("pilote", "piloter", "pilotage", "managed", "managing", "management"),
    ("maintenu", "maintenir", "maintenance", "maintained", "maintaining"),
    ("deploie", "deployer", "deploiement", "deployed", "deploying", "deployment"),
    ("forme", "former", "formation", "trained", "training"),
    ("analyse", "analyser", "analysis", "analyzed", "analysed"),
    ("documente", "documenter", "documentation", "documented", "documenting"),
    ("donnee", "donnees", "data"), ("ingenieur", "ingenieure", "engineer", "engineering"),
)
_ROOT_LOOKUP = {word: group[0] for group in _ROOT_GROUPS for word in group}
_POLITE_WORDS = frozenset("""
madame monsieur bonjour cordialement bien sinceres salutations distinguees distinguee
je vous propose un une echange echanger pour preciser vos votre priorites priorite et
la le les contribution que pourrais apporter a equipe de du des dans l attente retour
prie agreer expression mes remerciements merci attention reste disposition tout toute
tous toutes precision precisions complementaires complementaire information informations
serais heureux heureuse rencontre rencontrer afin discuter avec en souhaitant bonne
reception d mon ma me suis vous remercie
dear hiring recruitment team manager hello kind best regards sincerely yours thank
thanks you for your time consideration i would welcome a conversation about priorities
and how could contribute to look forward hearing from further information discuss
opportunity discuss available any additional
""".split())


def career_ai_enabled(profile: dict[str, Any] | None = None) -> bool:
    if str(os.environ.get("NAVIN_CAREER_AI") or os.environ.get("NAVIN_TENDERS_AI") or "").strip().lower() in _OFF:
        return False
    return (profile or {}).get("ai_assist") is True


def _facts(job: dict[str, Any], profile: dict[str, Any]) -> str:
    """Complete facts, separated from requirements; no silent master-CV truncation."""
    candidate_fields = (
        "display_name", "headline", "summary", "master_cv", "stack", "experiences",
        "education", "projects", "certifications", "strengths", "highlights", "languages",
        "email", "phone", "city", "location", "linkedin",
    )
    offer_fields = ("title", "company", "description", "stack", "requirements", "must_haves", "nice_to_haves")
    return json.dumps({
        "candidate": {key: profile[key] for key in candidate_fields if key in profile},
        "offer_requirements_not_candidate_facts": {key: job[key] for key in offer_fields if key in job},
    }, ensure_ascii=False, default=str)


def evidence_catalog(profile: dict[str, Any], pack: dict[str, Any]) -> dict[str, str]:
    facts = {"profile": _on_file_text(profile)}
    if profile.get("master_cv"):
        facts["master"] = clean_text(profile["master_cv"])
    cv = pack.get("cv") or {}
    for row in cv.get("experiences") or []:
        identifier = row["id"]
        facts[f"{identifier}:role"] = " | ".join(row[key] for key in ("title", "company", "period") if row.get(key))
        for index, bullet in enumerate(row.get("bullets") or []):
            facts[f"{identifier}:bullet-{index}"] = bullet
    for index, item in enumerate(cv.get("highlights") or []):
        facts[f"highlight-{index}"] = item
    for index, item in enumerate(cv.get("education") or []):
        facts[f"education-{index}"] = " | ".join(clean_text(item.get(key)) for key in ("diploma", "school", "year") if item.get(key))
    return facts


def _numbers(text: str) -> set[str]:
    # Normalize decimal commas and thousands grouping without losing single digits.
    text = re.sub(r"(?<=\d)[ \u00a0\u202f](?=\d{3}(?!\d))", "", text)
    return {value.replace(",", ".").lstrip("0") or "0" for value in re.findall(r"\d+(?:[.,]\d+)?", text)}


def _figures(text: str) -> list[str]:
    text = re.sub(r"(?<=\d)[ \u00a0\u202f](?=\d{3}(?!\d))", "", text)
    return [re.sub(r"\s+", "", value).replace(",", ".") for value in re.findall(r"\d+(?:[.,]\d+)?(?:\s*[%€$])?", text)]


def _transitions(text: str) -> set[tuple[str, str]]:
    folded = _fold(text)
    return {
        (start.replace(",", "."), end.replace(",", "."))
        for start, end in re.findall(r"\b(?:de|from)\s+(\d+(?:[.,]\d+)?)[%\s]*(?:a|to)\s+(\d+(?:[.,]\d+)?)", folded)
    }


def _roots(text: str) -> set[str]:
    roots = set()
    for word in re.findall(r"[a-z][a-z0-9]*", _fold(text)):
        if word in _EDITORIAL:
            continue
        if word in _ROOT_LOOKUP:
            roots.add(_ROOT_LOOKUP[word])
            continue
        stem = word[:-1] if len(word) > 4 and word.endswith("s") else word
        if stem in _ROOT_LOOKUP:
            roots.add(_ROOT_LOOKUP[stem])
            continue
        stem = stem[:-1] if len(stem) > 5 and stem.endswith("e") else stem
        roots.add(_ROOT_LOOKUP.get(stem, stem))
    return roots


def _novel_roots(text: str, source: str, *, prose: bool = False) -> set[str]:
    novel = _roots(text) - _roots(source)
    if not prose:
        return novel
    novel -= _roots(" ".join(_PROSE_EDITORIAL))
    folded = _fold(source)
    if re.search(r"\b(?:senior|confirme)\b|\b(?:19|20)\d{2}\s*-\s*(?:(?:19|20)\d{2}|present|en cours)", folded):
        novel -= _roots("expérimenté expérimentée experienced solide strong")
    if re.search(r"\b(?:avec|with)\s+(?:(?:les|des|l|the|my|product|operations)\W+)*\b(?:equipe|equipes|team|teams)\b", folded):
        novel -= _roots("collaboration collaborer inter-équipes collaboration cross-team")
    if re.search(r"\b(?:heure|heures|minute|minutes|second|seconds|hour|hours)\b", folded):
        novel -= _roots("temps time durée duration")
    return novel


def _grounded(text: str, source: str, *, preserve_figures: bool = False, prose: bool = False) -> bool:
    if not text.strip() or re.search(r"on.file facts|facts (?:already )?on file|not on file|do not invent|absent du (?:cv|dossier)|faits deja etablis", _fold(text)):
        return False
    if _numbers(text) - _numbers(source):
        return False
    if _transitions(text) - _transitions(source):
        return False
    if preserve_figures and _figures(source) != _figures(text):
        return False
    if _novel_roots(text, source, prose=prose):
        return False
    for term in job_keywords({"description": text}):
        if supports_term(text, term) and not supports_term(source, term):
            return False
    if preserve_figures:
        for term in job_keywords({"description": source}):
            if supports_term(source, term) and not supports_term(text, term):
                return False
    return True


def _statement(raw: Any, catalog: dict[str, str], *, allowed: set[str] | None = None, context: str = "", preserve_figures: bool = False, prose: bool = False) -> tuple[str, list[str]]:
    if not isinstance(raw, dict) or set(raw) != {"text", "source_ids"}:
        raise ValueError("Révision non structurée ou champs inattendus.")
    text, ids = raw.get("text"), raw.get("source_ids")
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("Texte de révision vide ou trop long.")
    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or item not in catalog for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("Référence de fait absente ou inconnue.")
    if allowed is not None and not set(ids) <= allowed:
        raise ValueError("Une réalisation cite les faits d'un autre poste.")
    role_context = []
    if prose:
        for identifier in ids:
            role_id = identifier.split(":bullet-", 1)[0] + ":role"
            if ":bullet-" in identifier and role_id in catalog:
                role_context.append(catalog[role_id])
    source = "\n".join([*(catalog[item] for item in ids), *role_context, context])
    text = clean_text(text)
    if not _grounded(text, source, preserve_figures=preserve_figures, prose=prose):
        details = sorted(_novel_roots(text, source, prose=prose) | (_numbers(text) - _numbers(source)))
        reason = "La révision ajoute ou modifie un fait non étayé."
        if details:
            reason += " Termes ou chiffres sans preuve : " + ", ".join(details[:12]) + "."
        raise ValueError(reason)
    return text, ids


def _validate_employer_attribution(text: str, cv: dict[str, Any]) -> None:
    roles = [row for row in cv.get("experiences") or [] if row.get("company")]
    companies = {_fold(row["company"]) for row in roles}
    if not companies:
        return
    alternatives = "|".join(re.escape(value) for value in sorted(companies, key=len, reverse=True))
    pattern = re.compile(r"\b(?:chez|at|for|pour)\s+(" + alternatives + r")(?!\w)|\b(" + alternatives + r")\s*[:|]")
    folded = _fold(text)
    mentions = list(pattern.finditer(folded))
    for index, match in enumerate(mentions):
        company = match.group(1) or match.group(2)
        end = mentions[index + 1].start() if index + 1 < len(mentions) else len(folded)
        claim = folded[match.start():end]
        evidence = "\n".join(
            " | ".join([row.get("title") or "", row["company"], row.get("period") or "", *(row.get("bullets") or [])])
            for row in roles if _fold(row["company"]) == company
        )
        if _numbers(claim) - _numbers(evidence) or _transitions(claim) - _transitions(evidence):
            raise ValueError("Un résultat chiffré est attribué au mauvais employeur ou modifié.")
        if any(supports_term(claim, term) and not supports_term(evidence, term) for term in job_keywords({"description": claim})):
            raise ValueError("Une compétence est attribuée à un employeur sans preuve dans ce poste.")


def _editorial_cover_paragraph(text: str, name: str) -> bool:
    """Only conventional courtesy or the exact signature can omit evidence IDs."""
    if not text.strip() or len(text) > 650 or _numbers(text):
        return False
    courtesy = []
    for line in text.splitlines():
        line = line.strip()
        if not line or (name and line == clean_text(name)):
            continue
        courtesy.append(line)
    if not courtesy:
        return True
    words = re.findall(r"[a-z][a-z0-9]*", _fold(" ".join(courtesy)))
    if not words or any(word not in _POLITE_WORDS for word in words):
        return False
    phrase = " ".join(words)
    if re.fullmatch(r"madame(?: monsieur)?|monsieur|bonjour|(?:bien )?cordialement|sinceres salutations|salutations distinguees|dear (?:hiring|recruitment) (?:team|manager)|hello|(?:kind|best) regards|(?:yours )?sincerely", phrase):
        return True
    return bool(re.match(r"je vous (?:propose|prie|remercie)\b|je reste a votre disposition\b|je serais (?:heureux|heureuse)\b|dans l attente\b|en vous remerciant\b|i would welcome\b|i look forward\b|thank you\b", phrase))


def _cover_statement(raw: Any, catalog: dict[str, str], cv: dict[str, Any], context: str) -> str:
    if isinstance(raw, dict) and set(raw) == {"text", "source_ids"} and raw.get("source_ids") == []:
        text = raw.get("text")
        if isinstance(text, str) and _editorial_cover_paragraph(clean_text(text), clean_text(cv.get("name"))):
            return clean_text(text)
        raise ValueError("Les faits d'une lettre doivent citer une preuve; seules les formules de politesse et la signature exacte en sont dispensées.")
    return _statement(raw, catalog, context=context, prose=True)[0]


def apply_revision(job: dict[str, Any], profile: dict[str, Any], pack: dict[str, Any], revision: Any) -> dict[str, Any]:
    """Accept only scoped edits; identities and all other source sections stay intact."""
    if not isinstance(revision, dict) or not revision or set(revision) - {"summary", "experiences", "skill_order", "cover"}:
        raise ValueError("Le modèle doit retourner une révision JSON du CV.")
    catalog = evidence_catalog(profile, pack)
    out = copy.deepcopy(pack)
    cv = out["cv"]
    if "summary" in revision:
        summary, _ids = _statement(revision["summary"], catalog, prose=True)
        if len(summary) > 650 or "\n" in summary:
            raise ValueError("Le résumé doit tenir dans un court paragraphe.")
        _validate_employer_attribution(summary, pack["cv"])
        cv["summary"] = summary
    if "skill_order" in revision:
        ordered = revision["skill_order"]
        original = _strings(cv.get("skills"))
        if not isinstance(ordered, list) or any(not isinstance(item, str) for item in ordered) or len(ordered) != len(original) or set(ordered) != set(original):
            raise ValueError("La liste de compétences doit conserver les compétences fournies.")
        cv["skills"] = " · ".join(ordered)
    if "experiences" in revision:
        rows = revision["experiences"]
        if not isinstance(rows, list):
            raise ValueError("Les expériences doivent être une liste.")
        by_id = {row["id"]: row for row in cv.get("experiences") or []}
        seen = set()
        for proposed in rows:
            if not isinstance(proposed, dict) or set(proposed) != {"id", "bullets"}:
                raise ValueError("Les identités, employeurs et dates ne peuvent pas être réécrits.")
            identifier, bullets = proposed.get("id"), proposed.get("bullets")
            if not isinstance(identifier, str) or identifier not in by_id or identifier in seen:
                raise ValueError("Expérience inconnue ou répétée.")
            seen.add(identifier)
            if not isinstance(bullets, list) or not bullets:
                raise ValueError("Une expérience doit conserver ses réalisations.")
            original = by_id[identifier]
            required = {f"{identifier}:bullet-{index}" for index in range(len(original.get("bullets") or []))}
            covered: list[str] = []
            rewritten = []
            for bullet in bullets:
                text, ids = _statement(bullet, catalog, allowed=required, preserve_figures=True)
                covered.extend(ids)
                rewritten.append(text)
            if set(covered) != required or len(covered) != len(set(covered)):
                raise ValueError("Une réalisation originale est absente ou dupliquée.")
            original["bullets"] = rewritten
    if "cover" in revision:
        paragraphs = revision["cover"]
        if not isinstance(paragraphs, list) or not 3 <= len(paragraphs) <= 8:
            raise ValueError("La lettre doit être constituée de paragraphes complets.")
        target_context = " ".join(clean_text(job.get(key)) for key in ("title", "company"))
        rewritten, issues = [], []
        for index, item in enumerate(paragraphs):
            try:
                paragraph = _cover_statement(item, catalog, pack["cv"], target_context)
                _validate_employer_attribution(paragraph, pack["cv"])
                rewritten.append(paragraph)
            except ValueError as exc:
                issues.append(f"Paragraphe {index + 1} : {exc}")
        if issues:
            raise ValueError(" ".join(issues))
        cover = "\n\n".join(rewritten)
        employer = clean_text(job.get("company"))
        if employer and not has_term(_on_file_text(profile), employer):
            claim = r"(?:experience (?:chez|at)|worked (?:at|for)|travaille chez)\s+" + re.escape(_fold(employer))
            if re.search(claim, _fold(cover)):
                raise ValueError("La lettre attribue une expérience chez l'employeur cible.")
        if len(cover) > 3500:
            raise ValueError("La lettre est trop longue.")
        out["cover"] = cover
    return synchronize_pack(out)


def apply_valid_revisions(
    job: dict[str, Any], profile: dict[str, Any], pack: dict[str, Any], revision: Any,
    *, destination: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    """Validate independent fields against the original evidence, retaining good edits."""
    if not isinstance(revision, dict) or not revision:
        raise ValueError("Le modèle doit retourner une révision JSON du CV.")
    out = copy.deepcopy(destination if destination is not None else pack)
    accepted, rejected = [], []
    known = {"summary", "skill_order", "experiences", "cover"}
    for field in sorted(set(revision) - known):
        rejected.append({"field": field, "reason": "Champ immuable ou inconnu."})
    units = [(key, {key: revision[key]}) for key in ("summary", "skill_order", "cover") if key in revision]
    if "experiences" in revision:
        rows = revision["experiences"]
        if not isinstance(rows, list):
            rejected.append({"field": "experiences", "reason": "Les expériences doivent être une liste."})
        else:
            ids = [row.get("id") for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]
            for index, row in enumerate(rows):
                identifier = row.get("id") if isinstance(row, dict) else None
                field = f"experiences.{identifier}" if isinstance(identifier, str) else f"experiences[{index}]"
                if isinstance(identifier, str) and ids.count(identifier) > 1:
                    rejected.append({"field": field, "reason": "Expérience répétée."})
                else:
                    units.append((field, {"experiences": [row]}))
    for field, proposed in units:
        try:
            validated = apply_revision(job, profile, pack, proposed)
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({"field": field, "reason": str(exc)})
            continue
        accepted.append(field)
        if field == "cover":
            out["cover"] = validated["cover"]
        elif field == "summary":
            out["cv"]["summary"] = validated["cv"]["summary"]
        elif field == "skill_order":
            out["cv"]["skills"] = validated["cv"]["skills"]
        else:
            identifier = proposed["experiences"][0]["id"]
            replacement = next(row for row in validated["cv"]["experiences"] if row["id"] == identifier)
            out["cv"]["experiences"] = [replacement if row["id"] == identifier else row for row in out["cv"]["experiences"]]
    return synchronize_pack(out), accepted, rejected


def _parse_revision(text: str) -> Any:
    body = text.strip()
    fence = chr(96) * 3
    if body.startswith(fence) and body.endswith(fence):
        body = "\n".join(body.splitlines()[1:-1])

    def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("Champ JSON dupliqué.")
            data[key] = value
        return data

    return json.loads(body, object_pairs_hook=unique_fields)


def polish_pack(job: dict[str, Any], profile: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    out = synchronize_pack(pack)
    route = build_action_skill_context("career", "prepare")
    generation = dict(out.get("generation") or {})
    generation["skills"] = route.metadata
    out["generation"] = generation
    if not career_ai_enabled(profile) or not out.get("pack_ready"):
        return out

    def fallback(message: str, english: str) -> dict[str, Any]:
        if out.get("language") != "fr":
            message = english
        out["generation"] = {**generation, "mode": "deterministic", "status": "needs_review", "warnings": [*(generation.get("warnings") or []), message]}
        return out

    material = json.dumps({
        "document_language": out.get("language") or "en",
        "facts": json.loads(_facts(job, profile)),
        "canonical_cv": out["cv"],
        "evidence": evidence_catalog(profile, out),
        "draft_cover": out.get("cover") or "",
    }, ensure_ascii=False)
    if len(material) > _MAX_MATERIAL_CHARS:
        return fallback("Le dossier dépasse la taille de révision automatique; le CV complet est conservé.", "The source exceeds the automatic revision limit; the complete CV is retained.")
    text, model = ask(
        "write", route.augment_system(_STYLE), material,
        profile=profile, max_tokens=6000, temperature=0.2,
    )
    if not text:
        return fallback("Le modèle n'a pas fourni de révision; la version issue des faits est conservée.", "The model returned no revision; the version built from your facts is retained.")
    try:
        proposed = _parse_revision(text)
        revised, accepted, rejected = apply_valid_revisions(job, profile, out, proposed)
    except (ValueError, TypeError, KeyError) as exc:
        return fallback(f"Révision du modèle écartée : {exc}", "The model revision failed factual or structural checks; the original CV is retained.")

    repair_attempted = False
    repair_keys = {item["field"].split(".", 1)[0].split("[", 1)[0] for item in rejected} & {"summary", "experiences", "skill_order", "cover"}
    if repair_keys:
        # One repair can remove unsupported claims without discarding valid CV edits.
        repair_request = json.loads(material)
        repair_request["repair"] = {
            "fields": sorted(repair_keys), "validation_errors": rejected,
            "previous_revision": {key: proposed[key] for key in repair_keys if key in proposed},
            "instruction": (
                "Return only repaired fields in the same JSON schema. Use the source wording "
                "when a synonym cannot be verified. Remove unsupported outcomes, guarantees, "
                "seniority and capabilities. Do not add requirements from the offer as candidate "
                "facts. Keep the original figures, tools and role attribution. Other accepted "
                "fields are already saved. Returning the factual draft cover is valid."
            ),
        }
        repair_material = json.dumps(repair_request, ensure_ascii=False)
        if len(repair_material) <= _MAX_MATERIAL_CHARS:
            repair_attempted = True
            repair_text, repair_model = ask(
                "write", route.augment_system(_STYLE), repair_material,
                profile=profile, max_tokens=4500, temperature=0.1,
            )
            if repair_text:
                try:
                    repair = _parse_revision(repair_text)
                    if not isinstance(repair, dict):
                        raise ValueError("Réparation JSON attendue.")
                    repair = {key: value for key, value in repair.items() if key in repair_keys}
                    if "experiences" in repair and isinstance(repair["experiences"], list) and "experiences" not in {item["field"] for item in rejected}:
                        pending = {item["field"] for item in rejected if item["field"].startswith("experiences.")}
                        repair["experiences"] = [row for row in repair["experiences"] if isinstance(row, dict) and f"experiences.{row.get('id')}" in pending]
                    candidate, repaired_fields, repair_issues = apply_valid_revisions(job, profile, out, repair, destination=revised)
                    resolved = set(repaired_fields)
                    if "experiences" in repair and any(item.startswith("experiences.") for item in repaired_fields) and not any(item["field"].startswith("experiences") for item in repair_issues):
                        resolved.add("experiences")
                    remaining = [item for item in rejected if item["field"] not in resolved]
                    revised, accepted = candidate, [*accepted, *repaired_fields]
                    rejected = list({item["field"]: item for item in [*remaining, *repair_issues]}.values())
                    if repaired_fields and repair_model:
                        model = repair_model
                except (ValueError, TypeError, KeyError):
                    pass
    if not accepted:
        result = fallback("Révision du modèle écartée; le CV complet issu des faits est conservé.", "No model edits passed validation; the complete factual CV is retained.")
        result["generation"].update({"accepted_fields": [], "rejected_fields": rejected, "repair_attempted": repair_attempted})
        return result
    revised["model"] = model
    revised["route"] = task_role("write")
    warnings = list(generation.get("warnings") or [])
    if rejected:
        labels = {"summary": ("résumé", "summary"), "experiences": ("expériences", "experience"), "skill_order": ("compétences", "skills"), "cover": ("lettre de motivation", "cover letter")}
        fields = sorted({item["field"].split(".", 1)[0].split("[", 1)[0] for item in rejected})
        names = ", ".join(labels.get(field, (field, field))[0 if out.get("language") == "fr" else 1] for field in fields)
        warnings.append(
            "Certaines propositions n'ont pas passé la vérification des faits. La version précédente est conservée pour : " + names + "."
            if out.get("language") == "fr" else
            "Some proposed edits did not pass factual checks. The previous version is retained for: " + names + "."
        )
    revised["generation"] = {
        "mode": "model", "status": "needs_review" if warnings else "complete", "warnings": warnings,
        "skills": route.metadata, "accepted_fields": list(dict.fromkeys(accepted)),
        "rejected_fields": rejected, "repair_attempted": repair_attempted,
    }
    return revised
