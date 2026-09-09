# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Grounded Career documents with one canonical CV for text and Word exports."""

from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

_TOKEN = re.compile(r"[^\W_][\w+#./-]*", re.UNICODE)
_STOP = frozenset("""
    a an and are as at be been by for from in into is it of on or our that the their
    these this to we will with you your role job position candidate team company
    experience experienced years year work working opportunity required must have
    le la les un une des de du d l et en au aux avec pour par sur dans chez qui que
    nous vous votre vos notre nos ses son est sont etre avoir ce ces cette poste
    mission candidat candidate equipe entreprise recherche rejoindre profil ans an
    notamment ainsi aussi dont plus moins tres bon bonne nouveau nouvelle travail
    experiences competences maitrise requis obligatoire indispensable connaissance
    connaissances souhaitable francais anglais french english remote hybride freelance
    contrat salaire cdi cdd temps plein partiel senior junior confirme confirmees
""".split())

# Explicit offer skills are supplemented by a conservative phrase vocabulary.
_SKILLS: dict[str, tuple[str, ...]] = {
    "Python": (), "SQL": (), "Java": (), "JavaScript": ("JS",),
    "TypeScript": ("TS",), "C++": (), "C#": (), ".NET": ("dotnet",),
    "PHP": (), "Ruby": (), "Rust": (), "Golang": (),
    "React": ("React.js", "ReactJS"), "Angular": (), "Vue.js": ("VueJS",),
    "Node.js": ("NodeJS",), "Django": (), "FastAPI": (), "Spring": (),
    "HTML": ("HTML5",), "CSS": ("CSS3",), "REST": ("REST API",),
    "GraphQL": (), "PostgreSQL": ("Postgres",), "MySQL": (), "MongoDB": (),
    "Redis": (), "Oracle": (), "SQL Server": (), "Spark": ("Apache Spark",),
    "PySpark": (), "Hadoop": (), "Kafka": ("Apache Kafka",), "Airflow": (),
    "dbt": (), "Databricks": (), "Snowflake": (), "BigQuery": (),
    "ETL": (), "ELT": (), "Power BI": ("PowerBI",), "Tableau": (),
    "Excel": ("Microsoft Excel",), "Pandas": (), "NumPy": (),
    "AWS": ("Amazon Web Services",), "Azure": ("Microsoft Azure",),
    "GCP": ("Google Cloud", "Google Cloud Platform"), "Docker": (),
    "Kubernetes": ("K8s",), "Terraform": (), "Ansible": (), "Linux": (),
    "Git": (), "GitHub Actions": (), "GitLab CI": (), "Jenkins": (),
    "CI/CD": ("CI CD", "continuous integration", "integration continue"),
    "DevOps": (), "SRE": (), "MLOps": (), "Machine learning": ("apprentissage automatique",),
    "Deep learning": ("apprentissage profond",), "NLP": (), "LLM": (),
    "TensorFlow": (), "PyTorch": (), "scikit-learn": (), "RAG": (),
    "Scrum": (), "Kanban": (), "Agile": (), "SAFe": (), "ITIL": (),
    "PMP": (), "PRINCE2": (), "TOGAF": (), "BPMN": (), "UML": (),
    "gestion de projet": ("project management", "gestion de projets"),
    "gestion des risques": ("risk management",),
    "gestion du changement": ("change management", "conduite du changement"),
    "gestion des parties prenantes": ("stakeholder management",),
    "analyse de donnees": ("data analysis", "analyse des donnees"),
    "gouvernance des donnees": ("data governance",),
    "qualite des donnees": ("data quality",),
    "cybersecurite": ("cybersecurity",), "ISO 27001": (), "RGPD": ("GDPR",),
    "SAP": (), "Salesforce": (), "HubSpot": (), "CRM": (), "ERP": (),
    "SEO": ("referencement naturel", "search engine optimization"),
    "SEA": (), "Google Ads": (), "Google Analytics": (), "GA4": (),
    "Figma": (), "Adobe Photoshop": ("Photoshop",), "Illustrator": (),
    "InDesign": (), "UX": (), "UI": (), "WordPress": (),
    "comptabilite": ("accounting",), "controle de gestion": ("management accounting",),
    "IFRS": (), "audit financier": ("financial audit",), "paie": ("payroll",),
    "recrutement": ("recruitment",), "negociation": ("negotiation",),
    "relation client": ("customer relations",), "supply chain": ("chaine logistique",),
    "gestion des stocks": ("inventory management",), "Lean": (), "Six Sigma": (),
    "AutoCAD": (), "Revit": (), "BIM": (), "SolidWorks": (),
}
_SECTIONS = {
    "profil": "profile", "profile": "profile", "resume": "profile", "summary": "profile",
    "professional summary": "profile", "a propos": "profile",
    "experience": "experience", "experiences": "experience",
    "experience professionnelle": "experience", "experiences professionnelles": "experience",
    "professional experience": "experience", "work experience": "experience",
    "employment history": "experience", "parcours professionnel": "experience",
    "formation": "education", "formations": "education", "education": "education",
    "competences": "skills", "skills": "skills", "technical skills": "skills",
    "competences techniques": "skills", "langues": "languages", "languages": "languages",
    "projets": "projects", "projects": "projects", "certifications": "certifications",
    "certificats": "certifications", "publications": "publications",
    "benevolat": "volunteering", "volunteering": "volunteering",
}


def clean_text(value: Any) -> str:
    return str(value or "").replace("\u2014", " - ").replace("\u2013", "-").strip()


def _fold(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", clean_text(text)).casefold()
        if not unicodedata.combining(char)
    )


def _tokens(text: str) -> set[str]:
    return {item for item in _TOKEN.findall(_fold(text)) if len(item) > 1 and item not in _STOP}


def _strings(value: Any) -> list[str]:
    values = (
        re.split(r"[,;\n]|\s+·\s+", value) if isinstance(value, str)
        else value if isinstance(value, (list, tuple)) else []
    )
    return list(dict.fromkeys(
        clean_text(item) for item in values if isinstance(item, str) and clean_text(item)
    ))


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", _fold(value)).strip("_")
    return cleaned[:100] or "CV"


def _contains(text: str, term: str) -> bool:
    return bool(term and re.search(
        r"(?<![\w+#])" + re.escape(_fold(term)) + r"(?![\w+#])", _fold(text)
    ))


def _aliases(term: str) -> tuple[str, ...]:
    for canonical, aliases in _SKILLS.items():
        if _fold(term) in {_fold(canonical), *(_fold(item) for item in aliases)}:
            return (canonical, *aliases)
    return (term,)


def has_term(text: str, term: str) -> bool:
    return any(_contains(text, alias) for alias in _aliases(term))


def supports_term(text: str, term: str) -> bool:
    """A stated absence of a skill is not evidence that the candidate has it."""
    folded = _fold(text)
    negative = r"\b(?:no|not|never|without|sans|pas|aucun|aucune|jamais|non)\b"
    aliases = _aliases(term)
    patterns = [r"(?<![\w+#])" + re.escape(_fold(alias)) + r"(?![\w+#])" for alias in aliases]
    if any(_fold(alias) == "qualite des donnees" for alias in aliases):
        # "Tests de qualité SQL sur les données" expresses the same actual work.
        patterns.append(r"\bqualite\b[^\n.!?]{0,55}\b(?:donnees|data)\b")
    for pattern in patterns:
        for match in re.finditer(pattern, folded):
            before = re.split(r"[.;\n]|\b(?:but|mais)\b", folded[max(0, match.start() - 80):match.start()])[-1]
            before = " ".join(before.split()[-7:])
            before = re.sub(r"\b(?:not only|non seulement)\b", "", before)
            after = folded[match.end():match.end() + 45]
            if re.search(negative, before):
                continue
            if re.match(r"\s*:?\s*(?:not (?:known|used|mastered)|non (?:maitris|utilis)|a apprendre|inconnu)", after):
                continue
            return True
    return False


def job_keywords(job: dict[str, Any]) -> list[str]:
    """Keep actual requirements and multiword skills, never ordinary offer words."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        key = _fold(term)
        if not key or key in seen or (not _tokens(term) and term not in {"R", "C"}) or len(term) > 90:
            return
        if any(has_term(known, term) and has_term(term, known) for known in ordered):
            return
        seen.add(key)
        ordered.append(clean_text(term))

    for field in ("stack", "skills", "certifications"):
        for term in _strings(job.get(field)):
            add(term)
    description = "\n".join(clean_text(job.get(field)) for field in ("title", "description"))
    for field in ("must_haves", "nice_to_haves", "requirements"):
        description += "\n" + "\n".join(_strings(job.get(field)))
    description = description.strip()
    hits = []
    for canonical, aliases in _SKILLS.items():
        for term in sorted((canonical, *aliases), key=len, reverse=True):
            match = re.search(
                r"(?<![\w+#])" + re.escape(_fold(term)) + r"(?![\w+#])", _fold(description)
            )
            if match:
                label = description[match.start():match.end()]
                hits.append((match.start(), label if _fold(label) == _fold(term) else canonical))
                break
    for _position, term in sorted(hits, key=lambda item: item[0]):
        add(term)
    return ordered


def _score(text: str, keys: list[str] | set[str]) -> int:
    return sum(has_term(text, term) for term in keys)


def _exp_bullets(row: dict[str, Any]) -> list[str]:
    values = []
    for field in ("bullets", "achievements", "facts", "detail", "summary"):
        raw = row.get(field)
        if isinstance(raw, list):
            values.extend(clean_text(item) for item in raw if isinstance(item, str))
        elif isinstance(raw, str):
            values.extend(re.split(r"\n+|[;•●]|(?<=[.!?])\s+(?=[A-ZÀ-Ö])", clean_text(raw)))
    return list(dict.fromkeys(
        value.strip(" -*\t;") for value in values if value.strip(" -*\t;")
    ))


def _line_exp(row: dict[str, Any]) -> str:
    head = " | ".join(
        clean_text(row.get(key)) for key in ("title", "company", "period")
        if clean_text(row.get(key))
    )
    return " ".join([head, *_exp_bullets(row)]).strip()


def _date_key(row: dict[str, Any]) -> tuple[int, int, int]:
    period = clean_text(row.get("period") or row.get("year"))
    ongoing = bool(re.search(r"present|current|aujourd|ce jour|en cours|depuis", _fold(period)))
    months = {
        "jan": 1, "fev": 2, "feb": 2, "mar": 3, "avr": 4, "apr": 4,
        "mai": 5, "may": 5, "jui": 6, "jun": 6, "juillet": 7, "jul": 7,
        "aou": 8, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    dates = []
    folded = _fold(period)
    for match in re.finditer(r"\b(?:19|20)\d{2}\b", folded):
        before, after = folded[:match.start()], folded[match.end():]
        numeric = re.search(r"\b(0?[1-9]|1[0-2])[/.-]$", before)
        named = re.search(r"([a-z]+)\s+$", before)
        iso_month = re.match(r"[/.-](0?[1-9]|1[0-2])(?!\d)", after)
        month = int(numeric.group(1)) if numeric else int(iso_month.group(1)) if iso_month else 0
        if not month and named:
            month = months.get(named.group(1), months.get(named.group(1)[:3], 0))
        dates.append(int(match.group()) * 100 + month)
    return (int(ongoing), dates[-1] if dates else 0, dates[0] if dates else 0)


def _job_language(job: dict[str, Any], profile: dict[str, Any]) -> str:
    explicit = clean_text(job.get("document_language") or profile.get("document_language"))
    if explicit.lower().split("-")[0] in {"fr", "en"}:
        return explicit.lower().split("-")[0]
    blob = " ".join(clean_text(job.get(key)) for key in ("title", "description"))
    fr = len(re.findall(r"\b(le|la|les|des|une|pour|avec|poste|mission|candidature)\b|[àâéèêëïôùç]", blob, re.I))
    en = len(re.findall(r"\b(the|and|for|with|this|role|engineer|remote|please)\b", blob, re.I))
    if fr >= 3 and fr > en:
        return "fr"
    if en >= 3 and en > fr:
        return "en"
    langs = _strings(profile.get("languages"))
    if langs and langs[0].lower().startswith("fr"):
        return "fr"
    country = clean_text(job.get("country") or profile.get("residence_country")).upper()
    return "fr" if country in {"FR", "BE", "LU", "MC", "CH"} else "en"


def _on_file_text(profile: dict[str, Any]) -> str:
    values = [clean_text(profile.get(field)) for field in ("master_cv", "headline", "display_name", "summary")]
    for field in ("stack", "strengths", "highlights", "languages", "certifications"):
        values.extend(_strings(profile.get(field)))
    values.extend(_line_exp(row) for row in _rows(profile.get("experiences")))
    for field in ("education", "projects"):
        for row in _rows(profile.get(field)):
            values.extend(clean_text(value) for value in row.values() if isinstance(value, str))
    values.extend(_strings(profile.get("projects")))
    return "\n".join(value for value in values if value)


def _master_sections(master: str, *, profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Preserve uncertain source structure instead of dropping or guessing CV facts."""
    sections: list[dict[str, Any]] = []
    kind, heading, lines = "profile", "", []
    headers = {
        _fold(profile.get(key) or "")
        for key in ("display_name", "headline", "email", "phone", "city", "location", "linkedin")
    }

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            sections.append({
                "kind": kind, "heading": heading,
                "paragraphs": [part.strip() for part in re.split(r"\n{2,}", body) if part.strip()],
            })

    for raw in clean_text(master).splitlines():
        line = raw.strip()
        label = line.strip("#* :\t")
        known = _SECTIONS.get(_fold(label))
        if known:
            flush()
            kind, heading, lines = known, label, []
        elif _fold(line) not in headers or not line:
            lines.append(line)
    flush()
    return sections


def section_label(kind: str, language: str) -> str:
    labels = {
        "profile": ("Profil", "Profile"), "experience": ("Expérience professionnelle", "Experience"),
        "education": ("Formation", "Education"), "skills": ("Compétences", "Skills"),
        "languages": ("Langues", "Languages"), "projects": ("Projets", "Projects"),
        "certifications": ("Certifications", "Certifications"), "publications": ("Publications", "Publications"),
        "volunteering": ("Bénévolat", "Volunteering"), "highlights": ("Réalisations clés", "Selected achievements"),
        "additional": ("Parcours complémentaire", "Additional experience"),
    }
    return labels.get(kind, labels["additional"])[0 if language == "fr" else 1]


def cv_to_text(cv: dict[str, Any], language: str = "en") -> str:
    """The text preview is a serialization of exactly the CV sent to Word."""
    lines = [clean_text(cv.get("name")), clean_text(cv.get("headline"))]
    contacts = _strings(cv.get("contacts"))
    if contacts:
        lines.append(" | ".join(contacts))
    if cv.get("target"):
        lines.append(("Candidature : " if language == "fr" else "Application: ") + clean_text(cv["target"]))

    def section(kind: str, content: list[str]) -> None:
        content = [clean_text(value) for value in content if clean_text(value)]
        if content:
            lines.extend(["", section_label(kind, language), *content])

    section("profile", [cv.get("summary") or ""])
    section("highlights", [f"- {value}" for value in _strings(cv.get("highlights"))])
    experience_lines = []
    for row in _rows(cv.get("experiences")):
        experience_lines.append(" | ".join(
            clean_text(row.get(key)) for key in ("title", "company", "period")
            if clean_text(row.get(key))
        ))
        experience_lines.extend(f"- {value}" for value in _strings(row.get("bullets")))
    section("experience", experience_lines)
    section("education", [" | ".join(
        clean_text(row.get(key)) for key in ("diploma", "school", "year")
        if clean_text(row.get(key))
    ) for row in _rows(cv.get("education"))])
    section("skills", [clean_text(cv.get("skills"))])
    section("languages", [" | ".join(_strings(cv.get("languages")))])
    for extra in _rows(cv.get("sections")):
        section(str(extra.get("kind") or "additional"), _strings(extra.get("paragraphs")))
    return "\n".join(value for value in lines if value is not None).strip() + "\n"


def synchronize_pack(pack: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(pack)
    if isinstance(out.get("cv"), dict):
        out["cv_text"] = cv_to_text(out["cv"], str(out.get("language") or "en"))
        out["summary"] = clean_text(out["cv"].get("summary"))
    return out


def _join_terms(terms: list[str], french: bool) -> str:
    if len(terms) < 2:
        return ", ".join(terms)
    return ", ".join(terms[:-1]) + (" et " if french else " and ") + terms[-1]


def _cover(cv: dict[str, Any], matched: list[str], stack: list[str], french: bool) -> str:
    target = cv["target"]
    expertise = _join_terms(matched[:3] or stack[:3], french)
    if french:
        opening = f"Le poste de {target} m'intéresse" if target else "Je souhaite rejoindre votre équipe"
        opening += f" pour mettre mes compétences en {expertise} au service de votre équipe." if expertise else "."
    else:
        opening = f"I am interested in the {target} role" if target else "I would like to join your team"
        opening += f", bringing skills in {expertise}." if expertise else "."
    proof = []
    for row in cv["experiences"][:2]:
        bullets = row["bullets"][:2]
        if not bullets:
            continue
        company = row["company"]
        if french:
            prefix = f"Mon expérience chez {company} illustre cette contribution : " if company else "Mes réalisations comprennent : "
        else:
            prefix = f"My experience at {company} includes the following work: " if company else "My work includes: "
        proof.append(prefix + " ".join(bullets))
    if not proof:
        proof = cv["highlights"][:2] or [cv["summary"]]
    closing = (
        "Je vous propose un échange pour préciser vos priorités et la contribution que je pourrais apporter à votre équipe."
        if french else
        "I would welcome a conversation about your priorities and how I could contribute to your team."
    )
    return "\n\n".join(part for part in [
        "Madame, Monsieur," if french else "Dear hiring team,", opening, *proof, closing,
        ("Cordialement," if french else "Kind regards,") + (f"\n{cv['name']}" if cv["name"] else ""),
    ] if part)


def build_pack(job: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Build a usable application from candidate facts, without a model dependency."""
    language = _job_language(job, profile)
    french = language == "fr"
    person = clean_text(profile.get("display_name"))
    title, company = clean_text(job.get("title")), clean_text(job.get("company"))
    headline = clean_text(profile.get("headline"))
    master = clean_text(profile.get("master_cv"))
    keywords = job_keywords(job)
    source = _on_file_text(profile)
    matched = [term for term in keywords if supports_term(source, term)]
    missing = [term for term in keywords if not supports_term(source, term)]
    stack = [term for term in _strings(profile.get("stack")) if _tokens(term) or term in {"R", "C"}]
    skills = list(dict.fromkeys([*matched, *sorted(stack, key=lambda term: -_score(term, keywords))]))
    strengths = [term for term in _strings(profile.get("strengths")) if _tokens(term)]
    highlights = sorted(_strings(profile.get("highlights")), key=lambda line: -_score(line, keywords))
    experiences = []
    for index, row in enumerate(_rows(profile.get("experiences"))):
        experiences.append({
            "id": f"experience-{index}",
            "title": clean_text(row.get("title") or row.get("role")),
            "company": clean_text(row.get("company") or row.get("employer")),
            "period": clean_text(row.get("period") or row.get("year")),
            "bullets": sorted(_exp_bullets(row), key=lambda line: -_score(line, keywords)),
        })
    experiences.sort(key=_date_key, reverse=True)
    education = sorted(_rows(profile.get("education")), key=_date_key, reverse=True)
    education = [{key: clean_text(row.get(key)) for key in ("diploma", "school", "year")} for row in education]
    sections = _master_sections(master, profile=profile)
    master_summary = next(("\n\n".join(row["paragraphs"]) for row in sections if row["kind"] == "profile"), "")
    supplied_summary = clean_text(profile.get("summary"))
    summary = supplied_summary or (master_summary if 0 < len(master_summary) <= 480 and "\n" not in master_summary else "")
    if not summary:
        summary = headline.rstrip(".")
        if skills:
            summary += (". " if summary else "") + ("Compétences en " if french else "Skills in ") + _join_terms(skills[:5], french) + "."
    if summary == headline:
        summary = ""
    # Remove only source paragraphs whose content is already represented. A partial
    # structured profile must not erase earlier jobs, education or other CV facts.
    represented = "\n".join([
        person, headline, summary, *skills, *strengths, *highlights,
        *(_line_exp(row) for row in experiences),
        *(" ".join(row.values()) for row in education),
    ])
    represented_terms = _tokens(represented)
    retained = []
    for section in sections:
        paragraphs = [paragraph for paragraph in section["paragraphs"] if not (
            _tokens(paragraph) and _tokens(paragraph) <= represented_terms
        )]
        if paragraphs:
            retained.append({**section, "paragraphs": paragraphs})
    sections = retained
    for field, kind in (("projects", "projects"), ("certifications", "certifications")):
        values = _strings(profile.get(field))
        for row in _rows(profile.get(field)):
            values.append(" | ".join(
                clean_text(value) for value in row.values() if isinstance(value, str) and clean_text(value)
            ))
        if values:
            sections.append({"kind": kind, "heading": section_label(kind, language), "paragraphs": values})
    skills = list(dict.fromkeys([*skills, *strengths]))
    language_names = {
        "fr": ("Français", "French"), "en": ("Anglais", "English"), "ar": ("Arabe", "Arabic"),
        "es": ("Espagnol", "Spanish"), "de": ("Allemand", "German"),
    }
    languages = [
        language_names.get(value.lower(), (value, value))[0 if french else 1]
        for value in _strings(profile.get("languages"))
    ]
    target = (f"{title} chez {company}" if french else f"{title} at {company}") if title and company else title
    cv = {
        "schema_version": 2, "name": person, "language": language, "headline": headline or title,
        "contacts": [clean_text(profile.get(key)) for key in ("email", "phone", "city", "linkedin") if clean_text(profile.get(key))],
        "target": target, "summary": summary,
        "skills": " · ".join(skills), "strengths": strengths,
        "highlights": [item for item in highlights if not any(item in row["bullets"] for row in experiences)],
        "experiences": experiences, "education": education, "languages": languages, "sections": sections,
    }
    if not profile.get("city") and profile.get("location"):
        cv["contacts"].append(clean_text(profile["location"]))
    requirements = []
    must = " ".join(_strings(job.get("must_haves")))
    sentences = re.split(r"\n+|(?<=[.!?])\s+", clean_text(job.get("description")))
    for term in keywords:
        required = has_term(must, term) or any(
            has_term(sentence, term) and re.search(r"must|required|essential|obligatoire|indispensable|requis", _fold(sentence))
            for sentence in sentences
        )
        requirements.append({"term": term, "weight": 3 if required else 1, "status": "present" if term in matched else "missing"})
    total = sum(row["weight"] for row in requirements)
    score = round(100 * sum(row["weight"] for row in requirements if row["status"] == "present") / total) if total else None
    ready = bool(master or experiences or stack or headline)
    notes = []
    if matched:
        notes.append(("Compétences présentes : " if french else "Present skills: ") + ", ".join(matched))
    if missing:
        notes.append(("Compétences à vérifier, non ajoutées au CV : " if french else "Skills to verify, not added to the CV: ") + ", ".join(missing))
    warnings = []
    if not ready:
        warnings.append("Aucun parcours ou compétence candidat fourni." if french else "No candidate experience or skills supplied.")
    elif len(source) < 350 and sum(len(row["bullets"]) for row in experiences) < 3:
        warnings.append(
            "Le parcours fourni est court. Ajoutez les responsabilités et réalisations réelles pour enrichir le CV."
            if french else
            "The supplied career history is brief. Add actual responsibilities and achievements to strengthen the CV."
        )
    if ready and not person:
        warnings.append("Le nom du candidat manque dans le profil." if french else "The candidate name is missing from the profile.")
    if ready and not cv["contacts"]:
        warnings.append("Ajoutez une coordonnée de contact au profil." if french else "Add contact information to the profile.")
    return synchronize_pack({
        "cv_name": f"{_safe_filename(person or 'candidat')}_CV_{_safe_filename(company or title or 'candidature')}.docx",
        "cv": cv, "cover": clean_text(_cover(cv, matched, stack, french)), "language": language,
        "ats_notes": "\n".join(notes), "ats_requirements": requirements, "ats_score": score,
        "keywords_matched": matched, "keywords_missing": missing,
        "pack_ready": ready, "from_master": bool(master),
        "generation": {
            "mode": "deterministic", "status": "needs_review" if warnings else "complete",
            "warnings": warnings, "skills": {},
        },
    })


def _french(profile: dict[str, Any], job: dict[str, Any]) -> bool:
    return _job_language(job, profile) == "fr"
