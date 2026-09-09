# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Professional bid chapters. Facts stay on file. Missing facts stay visible."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from navin.tenders.enrich import fetch_text_is_noise
from navin.tenders.profile import normalize_references, stated

_FR_HINT = re.compile(
    r"\b(le|la|les|des|une|pour|avec|dans|sur|objet|avis|marche|marche|"
    r"prestataire|cahier|fourniture|travaux|modernisation|refonte|"
    r"plateforme|decisionnel|acheteur|depot)\b|[àâäéèêëïîôùûüç]",
    re.I,
)
_EN_HINT = re.compile(
    r"\b(the|and|for|with|this|shall|must|tender|procurement|scope|"
    r"services|please|rebuild|platform|authority|deadline|submission)\b",
    re.I,
)
_AR_HINT = re.compile(r"[\u0600-\u06FF]")

SECTION_KEYS = (
    "cover",
    "toc",
    "letter",
    "executive_summary",
    "company",
    "need",
    "approach",
    "vision",
    "functional",
    "architecture",
    "methodology",
    "followup_kpi",
    "raci_risks",
    "governance",
    "planning",
    "staffing",
    "financial_schedule",
    "references",
    "compliance_matrix",
    "evidence",
    "assumptions",
    "revision_notes",
)

SECTION_TITLES = {
    "fr": {
        "cover": "Page de garde",
        "toc": "Sommaire",
        "letter": "Lettre de candidature",
        "executive_summary": "Resume executif",
        "company": "Presentation de la societe",
        "need": "Comprehension du besoin",
        "approach": "Demarche",
        "vision": "Vision",
        "functional": "Reponse fonctionnelle",
        "architecture": "Reponse technique",
        "methodology": "Methodologie",
        "followup_kpi": "Suivi et indicateurs",
        "raci_risks": "RACI et risques",
        "governance": "Gouvernance projet",
        "planning": "Planning et jalons",
        "staffing": "Equipe",
        "financial_schedule": "Budget et bordereau",
        "references": "References",
        "compliance_matrix": "Matrice de conformite",
        "evidence": "Sources et pieces justificatives",
        "assumptions": "Reserves et points a valider",
        "revision_notes": "Remarques internes",
    },
    "en": {
        "cover": "Cover page",
        "toc": "Contents",
        "letter": "Submission letter",
        "executive_summary": "Executive summary",
        "company": "Company presentation",
        "need": "Understanding of the need",
        "approach": "Approach",
        "vision": "Vision",
        "functional": "Functional response",
        "architecture": "Technical response",
        "methodology": "Methodology",
        "followup_kpi": "Follow-up and KPIs",
        "raci_risks": "RACI and risks",
        "governance": "Project governance",
        "planning": "Schedule and milestones",
        "staffing": "Team",
        "financial_schedule": "Budget and price schedule",
        "references": "References",
        "compliance_matrix": "Compliance matrix",
        "evidence": "Sources and supporting evidence",
        "assumptions": "Qualifications and validation points",
        "revision_notes": "Internal remarks",
    },
}


def detect_notice_language(tender: dict[str, Any] | None) -> str | None:
    parts: list[str] = []
    for key in ("title", "description", "eligibility", "cdc_text", "submission_method"):
        text = str((tender or {}).get(key) or "")
        if key in {"description", "cdc_text"} and fetch_text_is_noise(text):
            continue
        parts.append(text)
    blob = " ".join(parts)
    if len(_AR_HINT.findall(blob)) >= 10:
        return "ar"
    fr = len(_FR_HINT.findall(blob))
    en = len(_EN_HINT.findall(blob))
    if fr >= 3 and fr > en * 1.15:
        return "fr"
    if en >= 3 and en > fr * 1.15:
        return "en"
    return None


def section_title(key: str, lang: str) -> str:
    pack = SECTION_TITLES.get(lang) or SECTION_TITLES["en"]
    return pack.get(key) or SECTION_TITLES["en"].get(key) or key


def _normalized(value: Any) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(value or "").lower()) if not unicodedata.combining(c))


_CERTIFICATE = re.compile(r"\b(?:ISO\s*(?:/\s*IEC\s*)?\d{4,5}(?::\d{4})?|SOC\s*2(?:\s*type\s*(?:II|2))?|HDS|SecNumCloud)\b", re.I)


def _certificate_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _normalized(text).split(":")[0]).replace("iec", "")


def required_certifications(tender: dict[str, Any]) -> list[str]:
    """Recognise named requirements, without treating an optional example as a gate."""
    found: dict[str, str] = {}
    for field in ("eligibility", "cdc_text", "description"):
        for clause in re.split(r"[\n.;]", str(tender.get(field) or "")):
            low = _normalized(clause)
            if re.search(r"optionnel|facultati(?:f|ve)|non obligatoire|not required|optional|par exemple|for example", low):
                continue
            if field != "eligibility" and not re.search(r"obligatoire|exige|requis|required|shall|must|doit", low):
                continue
            for match in _CERTIFICATE.finditer(clause):
                name = " ".join(match.group(0).split())
                found.setdefault(_certificate_key(name), name)
    return list(found.values())


def missing_certifications(tender: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    declared = {_certificate_key(match.group(0)) for item in profile.get("certifications") or [] for match in _CERTIFICATE.finditer(str(item))}
    return [cert for cert in required_certifications(tender) if _certificate_key(cert) not in declared]


def build_evidence_register(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Canonical evidence; a declared skill or a slide template is not proof of delivery."""
    rows: list[dict[str, Any]] = []

    def add(kind: str, source: str, label: str, text: str, *, attachment: bool = False) -> None:
        if text.strip():
            rows.append({"id": f"E{len(rows) + 1:03d}", "kind": kind, "source": source,
                         "label": label, "text": text.strip(), "attachment": attachment,
                         "verification": "attachment_to_verify" if attachment else "profile_declaration"})

    if profile.get("methodology"):
        add("method", "profile.methodology", "Methode declaree / Declared method", str(profile["methodology"]))
    for index, item in enumerate(profile.get("certifications") or []):
        add("certification", f"profile.certifications[{index}]", str(item), str(item))
    for index, item in enumerate(normalize_references(profile.get("references"))):
        facts = [str(item.get(key)) for key in ("title", "client", "year", "country", "amount", "excerpt") if item.get(key) not in (None, "")]
        add("reference", f"profile.references[{index}]", str(item.get("title") or "Reference"), " | ".join(facts), attachment=bool(item.get("path") or item.get("file_id")))
    for index, item in enumerate(profile.get("team") or []):
        text = " | ".join(str(item.get(key)) for key in ("name", "role", "skills", "experience") if item.get(key)) if isinstance(item, dict) else str(item)
        add("team", f"profile.team[{index}]", str(item.get("name") or "Equipe") if isinstance(item, dict) else text, text)
    for index, item in enumerate(profile.get("price_book") or []):
        text = " | ".join(f"{key}: {value}" for key, value in item.items() if value not in (None, "")) if isinstance(item, dict) else str(item)
        add("price", f"profile.price_book[{index}]", "Bordereau / Price book", text)
    for index, item in enumerate(profile.get("documents") or []):
        if isinstance(item, dict):
            label = str(item.get("name") or item.get("title") or item.get("label") or "Document")
            add("document", f"profile.documents[{index}]", label, str(item.get("excerpt") or label), attachment=bool(item.get("path") or item.get("file_id")))
        elif str(item).strip():
            add("document", f"profile.documents[{index}]", str(item), str(item))
    return rows


# Each recipe is a proposed execution method, never a claim about a past project.
# Selection follows the actual requirement; unrelated company crafts add no scope.
_WORK_METHODS = {
    "data": (r"ingest|integr(?:er|ation)[^.]{0,65}(?:sources?|donnees?)|\bsources?\b|migration|migrer|reprise (?:de|des) donnees|lac de donnees|data (?:lake|warehouse|pipeline)|chargement|traitement[^.]{0,30}increment",
        ("Integration et reprise", "Data integration and migration"),
        ("Cartographier les sources, cles et regles de transformation; isoler les rejets, rendre les reprises rejouables et rapprocher les donnees chargees des sources.", "Map sources, keys and transformation rules; isolate rejected records, make recovery repeatable and reconcile loaded records with source data."),
        ("Matrice source-cible, traitements versionnes, journal de rejets et rapport de rapprochement.", "Source-to-target mapping, versioned transformations, rejection log and reconciliation report."),
        ("Executer un jeu de donnees representatif avec doublons, valeurs absentes et interruption; comparer le resultat attendu, les rejets et le resultat d'une reprise.", "Run a representative dataset containing duplicates, missing values and an interrupted load; compare expected outputs, rejections and the recovered result."),
        ("Acces aux sources et dictionnaire metier valides par leurs proprietaires.", "Source access and a business data dictionary approved by their owners."),
        ("Ecart de qualite ou de structure des sources", "Source quality or schema mismatch"),
        ("Profiler les donnees en cadrage; faire arbitrer les exceptions avant la migration.", "Profile the data during discovery and resolve exceptions before migration.")),
    "api": (r"\bapi\b|interface|interoperab|integration|connecteur",
        ("Interfaces et contrats", "Interfaces and contracts"),
        ("Decrire les contrats d'interface, les versions et les erreurs; appliquer les droits par usage et tracer les appels sans exposer les donnees sensibles.", "Define interface contracts, versions and errors; apply use-specific permissions and trace requests without exposing sensitive data."),
        ("Contrats d'API editables, catalogue des erreurs, matrice des droits et tests de contrat.", "Editable API contracts, error catalogue, access matrix and contract tests."),
        ("Tester les cas nominaux, les donnees invalides, les acces refuses et la compatibilite avec les applications consommatrices.", "Test successful requests, invalid data, denied access and compatibility with consuming applications."),
        ("Contrats des applications consommatrices et identites de test accessibles.", "Consumer application contracts and test identities available."),
        ("Incompatibilite ou exposition involontaire de donnees", "Incompatibility or unintended data exposure"),
        ("Valider les contrats avec les consommateurs et tester les autorisations avant integration.", "Validate contracts with consumers and test permissions before integration.")),
    "security": (r"secur|securit|chiffr|encrypt|role|habilitation|sensible|\brgpd\b|\bgdpr\b|confidential",
        ("Securite et acces", "Security and access"),
        ("Inventorier les donnees sensibles et les flux; traduire les usages en habilitations, definir la gestion des secrets et verifier la protection des traces et sauvegardes.", "Inventory sensitive data and flows; translate uses into permissions, define secret management and check protection of logs and backups."),
        ("Matrice des habilitations, analyse des flux sensibles, procedures de gestion des secrets et rapport des controles.", "Access matrix, sensitive-flow analysis, secret management procedures and control report."),
        ("Verifier les acces autorises et refuses, la revocation d'un compte et la protection des echanges; conserver les preuves de test sans donnees sensibles.", "Verify allowed and denied access, account revocation and protected transport; retain test evidence without sensitive data."),
        ("Validation des regles de securite par le responsable habilite de l'acheteur.", "Buyer security rules approved by the authorised owner."),
        ("Habilitations excessives ou secrets exposes", "Excessive permissions or exposed secrets"),
        ("Revoir les droits avec le responsable securite et lever les ecarts avant mise en service.", "Review permissions with the security owner and resolve findings before release.")),
    "hosting": (r"heberg|hosting|cloud|infrastructure|environnements?|deployment|deploi|production",
        ("Environnements et deploiement", "Environments and deployment"),
        ("Documenter les environnements et flux autorises; preparer le deploiement reproductible, la configuration, les controles de sante et le retour a la version precedente.", "Document environments and allowed flows; prepare repeatable deployment, configuration, health checks and rollback to the previous version."),
        ("Dossier d'environnement, configurations versionnees, procedure de deploiement et procedure de retour arriere.", "Environment dossier, versioned configuration, deployment procedure and rollback procedure."),
        ("Deployer en environnement de recette, verifier la separation des acces et rejouer un retour arriere; faire approuver les caracteristiques d'hebergement exigees dans la source.", "Deploy to acceptance, verify access separation and rehearse rollback; obtain approval of hosting characteristics required by the source."),
        ("Hebergeur, localisation, capacite et responsabilites d'exploitation a faire approuver.", "Hosting provider, location, capacity and operational responsibilities to be approved."),
        ("Environnement indisponible ou hebergement non admissible", "Unavailable environment or ineligible hosting"),
        ("Valider les preuves de localisation et les acces avant de figer le choix d'hebergement.", "Validate location evidence and access before committing to a hosting choice.")),
    "continuity": (r"restaur|recovery|reprise d.activite|\brpo\b|\brto\b|disponibil|sauvegarder|(?:plan|politique) de sauvegarde|backup (?:plan|policy)",
        ("Continuite et restauration", "Continuity and recovery"),
        ("Definir le perimetre sauvegarde, la retention et les responsabilites; executer une restauration isolee et mesurer la perte de donnees et le temps de reprise.", "Define backup scope, retention and responsibilities; perform an isolated restore and measure data loss and recovery time."),
        ("Plan de sauvegarde, procedure de restauration et proces-verbal avec horodatages et rapprochement des donnees.", "Backup plan, restore procedure and evidence report with timestamps and data reconciliation."),
        ("Simuler une indisponibilite, restaurer et verifier les donnees; confronter les mesures aux objectifs explicitement cites dans l'exigence, sans substituer une cible supposee.", "Simulate an outage, restore and check the data; compare measurements with objectives explicitly stated in the requirement, without substituting an assumed target."),
        ("Perimetre des donnees, acces de restauration et objectifs de reprise confirmes.", "Data scope, restore access and recovery objectives confirmed."),
        ("Sauvegarde presente mais non restaurable", "Backup exists but cannot be restored"),
        ("Tester la restauration sur un environnement isole et conserver les mesures contradictoires.", "Test restoration in an isolated environment and retain jointly reviewed measurements.")),
    "quality": (r"qualite|quality|indicateur|tableau de bord|dashboard|analytics|recette|acceptance|completude|coherence|unicite|tests? (?:fonctionnels?|metier|de non.regression)",
        ("Qualite et recette", "Quality and acceptance"),
        ("Faire valider les regles metier et les resultats attendus; relier chaque test a l'exigence, versionner les jeux de recette et qualifier les anomalies avant correction.", "Approve business rules and expected outcomes; link each test to its requirement, version acceptance datasets and triage defects before correction."),
        ("Catalogue des regles, cahier de recette trace aux exigences, registre des anomalies et proces-verbal de recette.", "Rule catalogue, requirement-linked acceptance plan, defect register and acceptance report."),
        ("Demonstrer les cas nominaux et limites avec l'acheteur; comparer valeurs calculees et attendues, documenter les ecarts et faire statuer sur les reserves.", "Demonstrate normal and boundary cases with the buyer; compare computed and expected results, document deviations and decide on open qualifications."),
        ("Jeux de donnees representatifs, regles et seuils de recette approuves par le metier.", "Representative datasets, rules and acceptance thresholds approved by business owners."),
        ("Recette bloquee par des regles ou seuils non arbitres", "Acceptance blocked by unresolved rules or thresholds"),
        ("Faire approuver les exemples de resultat avant realisation et organiser une revue des ecarts.", "Approve expected-result examples before implementation and review deviations jointly.")),
    "documentation": (r"documentation|dossier d.architecture|manuel|guide|dictionnaire de donnees",
        ("Documentation livrable", "Deliverable documentation"),
        ("Constituer les documents demandes a partir des versions effectivement livrees; identifier leurs auteurs, leur version, leurs destinataires et les renvois entre contrats, donnees et procedures.", "Build requested documents from the delivered versions; identify authors, versions, recipients and cross-references between contracts, data and procedures."),
        ("Documents editables demandes par l'exigence, index des versions et liste des liens vers les livrables correspondants.", "Editable documents requested by the requirement, version index and links to the corresponding deliverables."),
        ("Ouvrir chaque document dans le format editable demande; verifier sa completude, l'exactitude des liens et la concordance avec la version livree, puis faire approuver l'ensemble par les destinataires.", "Open every document in the requested editable format; check completeness, link accuracy and correspondence with the delivered version, then obtain recipient approval."),
        ("Liste des formats et responsables de validation documentaire a confirmer.", "Document formats and approval owners to confirm."),
        ("Documentation decalee de la version livree", "Documentation differs from the delivered version"),
        ("Inclure les documents dans le controle de version et les verifier lors de la recette.", "Version-control documents and check them during acceptance.")),
    "transfer": (r"formation|transfert|training|handover|mise en situation|autonom",
        ("Documentation et transfert", "Documentation and handover"),
        ("Rediger les procedures sur la solution livree; preparer des exercices par role et faire executer les operations par les destinataires a partir des supports editables.", "Write procedures against the delivered solution; prepare role-specific exercises and have recipients perform operations using editable materials."),
        ("Guides editables, supports de transfert, exercices pratiques et registre des questions et actions ouvertes.", "Editable guides, handover materials, practical exercises and a log of questions and outstanding actions."),
        ("Observer une mise en situation autonome; verifier que les destinataires retrouvent la procedure, executent l'operation et savent traiter une erreur courante.", "Observe an independent exercise; check that recipients can locate the procedure, complete the task and handle a common error."),
        ("Participants, droits de test et perimetre des operations a transferer confirmes.", "Participants, test permissions and scope of operations to transfer confirmed."),
        ("Dependance persistante a l'equipe de realisation", "Ongoing dependence on the delivery team"),
        ("Valider les supports par exercice autonome et traiter les questions avant transfert de responsabilite.", "Validate materials through independent exercises and resolve questions before transferring responsibility.")),
    "governance": (r"pilotage|comite|reporting|gouvernance|planning|calendrier|mois|month|delai|jalon|milestone|charge (?:de|:)|jours.homme|duree|duration",
        ("Pilotage et jalons", "Governance and milestones"),
        ("Construire le calendrier a partir des livrables et dependances; suivre avancement constate, risques et decisions attendues, puis faire approuver tout changement de perimetre.", "Build the schedule from deliverables and dependencies; track evidenced progress, risks and required decisions, then obtain approval for scope changes."),
        ("Planning de reference, registre des decisions, suivi des risques et rapport d'avancement relie aux livrables.", "Baseline schedule, decision log, risk register and deliverable-linked progress report."),
        ("Verifier qu'un livrable declare termine possede une preuve acceptee; tracer les arbitrages, les responsabilites et l'incidence de chaque changement sur le calendrier.", "Verify that a deliverable reported complete has accepted evidence; trace decisions, responsibilities and the schedule impact of each change."),
        ("Date de notification, disponibilite des interlocuteurs et delais de validation a confirmer.", "Award date, stakeholder availability and approval lead times to be confirmed."),
        ("Retard d'acces ou de decision repercute sur les jalons", "Access or decision delay affects milestones"),
        ("Suivre les dependances critiques et faire arbitrer leur impact avant de promettre une date.", "Track critical dependencies and resolve their impact before committing to a date.")),
    "traceability": (r"matrice|tracabilite|traceability",
        ("Tracabilite de la reponse", "Response traceability"),
        ("Conserver les identifiants et libelles de l'acheteur; relier chaque exigence a la fiche de reponse, au livrable attendu et a la preuve de recette, avec un statut distinct pour les reserves.", "Preserve buyer identifiers and wording; link each requirement to its response, expected deliverable and acceptance evidence, with a separate status for qualifications."),
        ("Matrice editable des exigences, reponses, livrables, pieces et reserves; index des justificatifs.", "Editable matrix of requirements, responses, deliverables, evidence and qualifications; evidence index."),
        ("Rapprocher la liste source et la matrice ligne par ligne; ouvrir les pieces citees et verifier qu'aucun point n'est declare conforme sans preuve examinee.", "Reconcile the source list and matrix line by line; open cited evidence and check that no item is declared compliant without reviewed proof."),
        ("Version complete du cahier et acces aux annexes cites necessaires.", "Complete specification version and access to cited annexes required."),
        ("Exigence oubliee ou preuve citee a tort", "Omitted requirement or incorrectly cited evidence"),
        ("Faire relire la matrice par une personne distincte du redacteur et tracer les points ouverts.", "Have a separate reviewer check the matrix and track open items.")),
}


def _row_facets(text: str) -> list[str]:
    low = _normalized(text)
    # Classify the obligation before its vocabulary. A document mentioning an
    # API is a documentation deliverable, not another API implementation lot.
    if re.search(r"(?:joindre|fournir|livrer|provide|deliver)[^.]{0,30}matrice|matrice reliant|matrix linking", low):
        return ["traceability"]
    if re.search(r"^(?:exg[- .]?\d+[.)]?\s*)?(?:livrer|fournir|remettre|deliver|provide)[^.]{0,35}(?:dossier|document|manuel|guide|contrat|cahier)", low):
        return ["documentation"]
    if re.search(r"achever la mission|duree (?:de mission|contractuelle)|calendrier contractuel|complete the (?:project|assignment)|contract duration", low):
        return ["governance"]
    return [key for key, recipe in _WORK_METHODS.items() if re.search(recipe[0], low)]


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z][a-z0-9]{2,}", _normalized(text)) if word not in _STOP and word not in {"projet", "projets", "marche", "offre", "pour", "avec", "candidat", "requirement", "service", "services", "audit", "demonstration"}}


def _support_for(item: dict[str, Any], evidence: list[dict[str, Any]], admin: str) -> list[dict[str, Any]]:
    text = str(item.get("text") or "")
    if admin == "certification":
        keys = {_certificate_key(match.group(0)) for match in _CERTIFICATE.finditer(text)}
        return [row for row in evidence if row["kind"] == "certification" and _certificate_key(row["text"]) in keys]
    kinds = {"reference": {"reference"}, "price": {"price"}, "cv": {"document"}, "document": {"document"}}.get(admin)
    if kinds:
        candidates = [row for row in evidence if row["kind"] in kinds]
        if admin == "cv":
            return [row for row in candidates if re.search(r"\bcv\b|curriculum|resume", row["label"], re.I)]
        return candidates if admin != "document" else [row for row in candidates if _words(text) & _words(row["label"])]
    req_words = _words(text)
    return [row for row in evidence if row["kind"] == "method" or (row["kind"] == "reference" and len(req_words & _words(row["text"])) >= 2)]


def _source_driven_checks(text: str, *, fr: bool) -> tuple[list[str], list[str]]:
    """Turn explicit quantities into an operation and its measurement, without guessed targets."""
    methods: list[str] = []
    checks: list[str] = []
    low = _normalized(text)
    sources = re.search(r"\b(\d+)\s+sources?\b", text, re.I)
    if sources:
        count = sources.group(1)
        methods.append(f"Etablir une fiche de connexion et de correspondance pour chacune des {count} sources; tenir un etat des acces, transformations, controles et anomalies par source." if fr else f"Create a connection and mapping sheet for each of the {count} sources; track access, transformations, checks and defects per source.")
        checks.append(f"Rapprocher le resultat et les rejets pour chacune des {count} sources avec les donnees d'origine; consigner les ecarts et leur traitement dans le rapport de recette." if fr else f"Reconcile results and rejected records for each of the {count} sources against source data; record deviations and their resolution in the acceptance report.")
    if re.search(r"increment|quotidien|daily", low):
        checks.append("Executer une charge initiale puis une charge incrementale sur des donnees modifiees; verifier l'absence de doublons et la reprise apres interruption." if fr else "Run an initial load and then an incremental load on changed records; check for duplicates and verify recovery after interruption.")
        if re.search(r"quotidien|daily", low):
            methods.append("Planifier la cadence quotidienne demandee et superviser la fin du traitement, les rejets et les reprises; faire valider l'heure de coupure avec les utilisateurs." if fr else "Schedule the required daily run and monitor completion, rejected records and recovery; agree the cut-off time with users.")
    for label, value, unit in re.findall(r"\b(RPO|RTO)\s*(?:de|of|:|=)?\s*(\d+(?:[.,]\d+)?)\s*(heures?|hours?|minutes?|jours?|days?)", text, re.I):
        target = f"{label.upper()} {value} {unit}"
        if label.upper() == "RPO":
            methods.append(f"Definir le point de sauvegarde et les controles de fraicheur permettant de tenir l'objectif source {target}." if fr else f"Define backup points and freshness checks to meet the source objective {target}.")
            checks.append(f"Comparer l'horodatage de la derniere donnee restauree a celui de l'incident; la perte mesuree doit respecter {target}, avec preuves dans le proces-verbal." if fr else f"Compare the last restored record timestamp with the incident timestamp; measured data loss must meet {target}, evidenced in the report.")
        else:
            methods.append(f"Preparer et chronometrer la procedure de restauration avec les acces et dependances necessaires pour l'objectif source {target}." if fr else f"Prepare and time the restore procedure, including required access and dependencies, against the source objective {target}.")
            checks.append(f"Mesurer l'intervalle entre le debut de l'incident et le service restaure verifie; confronter ce temps a {target} et conserver les horodatages." if fr else f"Measure the interval between incident start and verified service recovery; compare it with {target} and retain timestamps.")
    if re.search(r"heberg|hosting|hosted", low) and re.search(r"\bfrance\b", low):
        methods.append("Obtenir les pieces de localisation de l'hebergement en France avant de soumettre le choix du fournisseur a l'acheteur." if fr else "Obtain evidence of hosting location in France before submitting the provider choice for buyer approval.")
        checks.append("Verifier que les pieces de localisation concernent bien le service et les donnees du marche, pas seulement le siege du fournisseur." if fr else "Verify that location evidence covers the contract's service and data, rather than only the provider's registered office.")
    duration = re.search(r"\b(\d+\s+(?:mois|months?|semaines?|weeks?))\b", text, re.I)
    if duration:
        stated_duration = duration.group(1)
        methods.append(f"Construire le calendrier dans la duree source de {stated_duration}, en explicitant la date de depart, les dependances et les delais d'approbation a confirmer." if fr else f"Build the schedule within the source duration of {stated_duration}, identifying the start date, dependencies and approval lead times to confirm.")
        checks.append(f"Faire verifier que le calendrier approuve respecte {stated_duration} a partir du point de depart contractuel; tracer l'impact des decisions et changements." if fr else f"Check that the approved schedule meets {stated_duration} from the contractual starting point; trace the impact of decisions and changes.")
    return methods, checks


def build_requirement_responses(requirements: list[dict[str, Any]], profile: dict[str, Any], evidence: list[dict[str, Any]], *, lang: str) -> list[dict[str, Any]]:
    """A complete, reviewable response for every source row, including sparse notices."""
    fr = lang == "fr"
    ix = 0 if fr else 1
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(requirements):
        item = dict(raw) if isinstance(raw, dict) else {"text": str(raw), "source": "notice"}
        text = str(item.get("text") or "")
        low = _normalized(text)
        admin = ""
        if item.get("source") == "submission":
            admin = "submission"
        elif _CERTIFICATE.search(text) or "certification" in low:
            admin = "certification"
        elif re.search(r"references?\b", low) and item.get("source") == "eligibility":
            admin = "reference"
        elif re.search(r"\bcv\b|curriculum|staff resume", low):
            admin = "cv"
        elif re.search(r"bordereau|offre financiere|price schedule|financial offer", low):
            admin = "price"
        elif item.get("source") == "document":
            admin = "document"
        facets = [] if admin else _row_facets(text)
        support = _support_for(item, evidence, admin)
        method: list[str] = []
        deliverables: list[str] = []
        acceptance: list[str] = []
        dependencies: list[str] = []
        risks: list[str] = []
        mitigations: list[str] = []
        for facet in facets:
            recipe = _WORK_METHODS[facet]
            method.append(recipe[2][ix])
            deliverables.append(recipe[3][ix])
            acceptance.append(recipe[4][ix])
            dependencies.append(recipe[5][ix])
            risks.append(recipe[6][ix])
            mitigations.append(recipe[7][ix])
        if admin:
            method = [("Controler l'intitule exact, le titulaire, le perimetre, la validite et l'admissibilite de la piece au regard de l'exigence; joindre uniquement une piece effectivement disponible." if fr else "Check the exact designation, holder, scope, validity and eligibility against the requirement; attach only evidence actually available.")]
            if admin == "price":
                method = [("Rapprocher les lignes du bordereau au dossier avec les unites et postes demandes; faire valider quantites, taxes, hypotheses et total avant de signer l'offre financiere." if fr else "Map price-book lines to requested units and items; approve quantities, taxes, assumptions and the total before signing the financial offer.")]
            if admin == "reference":
                method = [("Verifier pour chaque reference le perimetre comparable, le role exact du candidat et l'autorisation de citer le client; obtenir les attestations demandees et verifier le nombre de references admissibles." if fr else "Check comparable scope, the bidder's exact role and permission to name each client; obtain requested attestations and verify the count of eligible references.")]
            if admin == "submission":
                method = [("Verifier le compte, les formats et la separation des pieces sur le portail indique dans la source; preparer l'archive et faire controler son contenu et sa signature avant tout depot autorise." if fr else "Verify account access, formats and separation of documents on the source portal; prepare the archive and check its contents and signature before any authorised submission.")]
            deliverables = [("Piece justificative controlee et bordereau des annexes, avec reserve explicite si absente." if fr else "Checked supporting evidence and annex index, with an explicit qualification when missing.")]
            acceptance = [("Controle contradictoire du libelle et des conditions de l'exigence sur la piece jointe. Une declaration de profil ne vaut pas certificat ni attestation." if fr else "Jointly verify the requirement wording and conditions against the attachment. A profile declaration is not a certificate or attestation.")]
            dependencies = [("Piece source et validation du responsable habilite avant depot." if fr else "Source attachment and approval by the authorised owner before submission.")]
            risks = [("Irrecevabilite ou preuve insuffisante" if fr else "Ineligible submission or insufficient evidence")]
            mitigations = [("Obtenir la piece recevable ou faire arbitrer la reserve avant toute decision de depot." if fr else "Obtain eligible evidence or resolve the qualification before deciding to submit.")]
        elif not facets:
            method = [("Faire preciser l'objet, les utilisateurs, les entrees attendues et les limites; decrire le traitement avec le responsable metier avant d'engager la realisation." if fr else "Clarify scope, users, expected inputs and boundaries; specify the work with the business owner before implementation.")]
            deliverables = [("Fiche de besoin validee, description de la solution proposee et scenario de verification relie a l'exigence." if fr else "Approved requirement sheet, proposed solution description and a verification scenario linked to the requirement.")]
            acceptance = [("Faire approuver un exemple de resultat attendu et ses conditions d'observation; aucune conformite technique ne peut etre conclue avec ce seul libelle." if fr else "Approve an expected-result example and observation conditions; this wording alone cannot establish technical compliance.")]
            dependencies = [("Specification detaillee et interlocuteur metier necessaires." if fr else "Detailed specification and a business owner are required.")]
            risks = [("Perimetre insuffisamment defini" if fr else "Insufficiently defined scope")]
            mitigations = [("Demander les precisions a l'acheteur et geler les hypotheses avant engagement." if fr else "Obtain clarification from the buyer and approve assumptions before commitment.")]
        if not admin:
            source_methods, source_checks = _source_driven_checks(text, fr=fr)
            method = source_methods + method
            acceptance = source_checks + acceptance
        status = "evidence_to_verify" if admin and support else "missing_evidence" if admin else "proposed"
        if admin == "reference":
            count_match = re.search(r"\b(\d+|deux|trois|quatre|cinq|two|three|four|five)\s+references?", low)
            if count_match:
                token = count_match.group(1)
                requested = int(token) if token.isdigit() else {"deux": 2, "two": 2, "trois": 3, "three": 3, "quatre": 4, "four": 4, "cinq": 5, "five": 5}[token]
                if len(support) < requested:
                    status = "insufficient_evidence"
                    dependencies.append(f"References demandees: {requested}; declarations disponibles: {len(support)}. Verifier comparabilite et attestations." if fr else f"References requested: {requested}; available declarations: {len(support)}. Verify comparability and attestations.")
        rows.append({**item, "id": str(item.get("id") or f"R{index + 1:03d}"), "category": admin or "technical",
                     "facets": facets, "method": method, "deliverables": deliverables, "acceptance": acceptance,
                     "dependencies": dependencies, "risks": risks, "mitigations": mitigations,
                     "evidence_ids": [row["id"] for row in support], "status": status,
                     "mandatory": bool(item.get("source") == "eligibility" or re.search(r"obligatoire|doit|shall|must|required", low)),
                     "draft_origin": "deterministic"})
    return rows


def _table_cell(value: Any) -> str:
    return " ".join(str(value or "").replace("|", "/").split())


def render_requirement_sections(tender: dict[str, Any], profile: dict[str, Any], rows: list[dict[str, Any]], evidence: list[dict[str, Any]], *, lang: str) -> dict[str, str]:
    """Render immutable source facts around proposed methods, including model additions."""
    fr = lang == "fr"
    ix = 0 if fr else 1
    status_names = {"proposed": ("Proposition a valider", "Proposal to validate"), "missing_evidence": ("Piece manquante", "Missing evidence"), "insufficient_evidence": ("Preuves insuffisantes", "Insufficient evidence"), "evidence_to_verify": ("Declaration a justifier", "Declaration to substantiate")}
    details: list[str] = []
    matrix = [("| ID | Exigence source | Reponse et statut | Pieces |" if fr else "| ID | Source requirement | Response and status | Evidence |"), "| --- | --- | --- | --- |"]
    reservations: list[str] = []
    for row in rows:
        rid = row["id"]
        ref = f"{rid} / {row['buyer_id']}" if row.get("buyer_id") else rid
        status = status_names.get(row["status"], status_names["proposed"])[ix]
        pieces = ", ".join(row["evidence_ids"])
        if row["category"] == "price" and pieces:
            pieces += " (bordereau au dossier)" if fr else " (price book on file)"
        if not pieces:
            pieces = "Aucune preuve jointe" if fr else "No attached evidence"
        details.extend([f"### {ref}", f"{'Exigence source' if fr else 'Source requirement'} ({row.get('source')}): {row.get('text')}", f"{'Statut' if fr else 'Status'}: {status}."])
        for key, labels in (("method", ("Mise en oeuvre proposee", "Proposed implementation")), ("deliverables", ("Livrables proposes", "Proposed deliverables")), ("acceptance", ("Recette proposee", "Proposed acceptance")), ("dependencies", ("Dependances et validations", "Dependencies and approvals"))):
            details.append(labels[ix] + ":")
            details.extend(f"- {text}" for text in row[key])
        details.append(("Pieces a verifier: " if fr else "Evidence to verify: ") + pieces + ".")
        details.append("")
        matrix.append(f"| {_table_cell(ref)} | {_table_cell(row.get('text'))} | {_table_cell(status)}; {'voir fiche' if fr else 'see response'} {rid} | {_table_cell(pieces)} |")
        if row["status"] != "proposed":
            reservations.append(f"- {ref}: {status}. {row['text']} ({pieces}).")
    architecture = [("Architecture proposee a partir des exigences source. Les choix ci-dessous restent soumis a validation; aucun fournisseur ou composant absent de l'avis n'est declare acquis." if fr else "Architecture proposed from the source requirements. The choices below require approval; no provider or component absent from the notice is treated as selected.")]
    for facet, recipe in _WORK_METHODS.items():
        linked = [row["id"] for row in rows if facet in row["facets"]]
        if not linked:
            continue
        architecture += [f"### {recipe[1][ix]} ({', '.join(linked)})", recipe[2][ix], recipe[3][ix], ("Decision a valider: " if fr else "Decision to approve: ") + recipe[5][ix]]
    if len(architecture) == 1:
        architecture.append("Le CDC n'est pas lisible ou ne decrit pas de composants. Les metiers du profil ne sont pas un lot de ce marche. L'architecture doit etre instruite avec l'acheteur." if fr else "The specification is unreadable or describes no components. Company crafts do not establish a contract work package. Architecture needs buyer clarification.")
    phases = [
        (("Cadrage", "Discovery"), ("Acces, perimetre, exigences et preuves", "Access, scope, requirements and evidence"), ("Cartographie, decisions et matrice source-cible", "Mapping, decisions and source-to-target matrix"), ("Perimetre et criteres approuves par l'acheteur", "Scope and criteria approved by the buyer")),
        (("Conception", "Design"), ("Cadrage approuve et contraintes source", "Approved scope and source constraints"), ("Dossier d'architecture, contrats, plan de recette", "Architecture dossier, contracts and acceptance plan"), ("Choix techniques et scenarios de recette approuves", "Technical choices and acceptance scenarios approved")),
        (("Realisation et verification", "Implementation and verification"), ("Conception validee et environnements accessibles", "Approved design and accessible environments"), ("Livrables versionnes, tests et traces d'execution", "Versioned deliverables, tests and execution evidence"), ("Tests relies aux exigences examines et ecarts qualifies", "Requirement-linked tests reviewed and defects triaged")),
        (("Recette et mise en service", "Acceptance and release"), ("Livrables verifies et jeux de recette disponibles", "Verified deliverables and acceptance datasets"), ("Proces-verbal, reserves et plan de retour arriere", "Acceptance report, qualifications and rollback plan"), ("Decision de mise en service prise par l'acheteur", "Buyer release decision recorded")),
        (("Transfert et cloture", "Handover and closure"), ("Solution acceptee et exploitants disponibles", "Accepted solution and available operators"), ("Documentation editable, exercices et registre des actions", "Editable documentation, exercises and action register"), ("Autonomie observee et responsabilites transferees", "Independence observed and responsibilities transferred")),
    ]
    methodology = [("Methode declaree par le candidat: " if fr else "Bidder's declared method: ") + str(profile.get("methodology") or ("a documenter dans le profil" if fr else "to document in the profile")), "", ("La sequence proposee ci-dessous organise les reponses detaillees; le passage au jalon suivant depend d'une preuve acceptee." if fr else "The proposed sequence below organises the detailed responses; each gate requires accepted evidence."), "", ("| Phase | Entree | Livrable et preuve | Condition de passage |" if fr else "| Phase | Input | Deliverable and evidence | Gate |"), "| --- | --- | --- | --- |"]
    methodology.extend("| " + " | ".join(_table_cell(part[ix]) for part in phase) + " |" for phase in phases)
    methodology += ["", ("Trace de recette: chaque fiche R porte son texte source, la proposition, les livrables, les scenarios de verification et les dependances. L'acheteur valide les seuils absents avant execution des essais." if fr else "Acceptance trace: every R response retains its source text, proposal, deliverables, verification scenarios and dependencies. The buyer approves missing thresholds before tests run.")]
    risk_rows = [("| Risque / exigences | Effet | Prevention et traitement | Responsable propose |" if fr else "| Risk / requirements | Impact | Prevention and response | Proposed owner |"), "| --- | --- | --- | --- |"]
    seen: set[str] = set()
    for row in rows:
        for risk, mitigation in zip(row["risks"], row["mitigations"]):
            if risk in seen:
                continue
            seen.add(risk)
            ids = ", ".join(item["id"] for item in rows if risk in item["risks"])
            risk_rows.append(f"| {_table_cell(risk)} ({ids}) | {'Recette ou admissibilite bloquee' if fr else 'Acceptance or eligibility blocked'} | {_table_cell(mitigation)} | {'Chef de projet avec referent acheteur' if fr else 'Project manager with buyer owner'} |")
    evidence_text: list[str] = []
    for row in evidence:
        used = ", ".join(item["id"] for item in rows if row["id"] in item["evidence_ids"])
        evidence_text += [f"### {row['id']} - {row['label']}", f"Source: {row['source']}", str(row["text"]), ("Statut: piece a ouvrir et verifier." if row["attachment"] else "Statut: declaration du profil, justificatif a verifier.") if fr else ("Status: attachment to open and verify." if row["attachment"] else "Status: profile declaration, supporting evidence to verify."), ("Rattachement: " if fr else "Linked responses: ") + (used or ("contexte societe" if fr else "company context")), ""]
    if not evidence_text:
        evidence_text = ["Aucune piece justificative exploitable dans le profil. Les propositions techniques ne demontrent pas une experience anterieure." if fr else "No usable supporting evidence in the profile. Technical proposals do not demonstrate previous delivery."]
    unique_dependencies = list(dict.fromkeys(item for row in rows for item in row["dependencies"]))
    assumptions = [("Registre de revue avant engagement. Les recettes et les livrables sont des propositions; leur presence dans ce dossier ne vaut pas acceptation ni preuve de conformite." if fr else "Review register before commitment. Acceptance scenarios and deliverables are proposals; inclusion does not establish acceptance or compliance."), *reservations, "", ("Dependances a lever:" if fr else "Dependencies to resolve:"), *(f"- {text}" for text in unique_dependencies), "", ("Avant depot: rapprocher le dossier du reglement de consultation complet, faire approuver la charge et le prix, verifier toutes les pieces et obtenir la signature habilitee." if fr else "Before submission: reconcile with the full tender rules, approve effort and price, verify all attachments and obtain the authorised signature.")]
    return {"functional": "\n".join(details), "compliance_matrix": "\n".join(matrix), "architecture": "\n\n".join(architecture), "methodology": "\n".join(methodology), "risk_register": "\n".join(risk_rows), "evidence": "\n".join(evidence_text), "assumptions": "\n".join(assumptions)}


def _t(lang: str, fr: str, en: str) -> str:
    return fr if lang == "fr" else en


def _lines(*rows: str) -> str:
    return "\n".join(row for row in rows if row is not None and str(row).strip() != "")


def _bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if str(item).strip())


_STOP = frozenset(
    {
        "les", "des", "une", "pour", "avec", "dans", "sur", "aux", "par", "est",
        "the", "and", "for", "with", "this", "that", "from", "are", "du", "de",
        "la", "le", "un", "et", "au", "en", "ou", "si", "it", "to", "of", "an",
    }
)
_DEADLINE_REQ = re.compile(
    r"^(date limite|deadline)\b|\b(echeance de depot|submission deadline)\b",
    re.I,
)
_DURATION_FACT = re.compile(
    r"(?:duree|duration|charge)\s*(?:de mission)?\s*[:\-]?\s*[^\n.]{0,40}"
    r"|\b\d+\s*(?:mois|semaines?|jours?(?:[ -]homme)?|months?|weeks?|man[ -]?days?)\b",
    re.I,
)
_REPORT_FACT = re.compile(
    r"(?:reporting|comit[eé]|cadence|frequence)[^\n.]{0,80}"
    r"|(?:hebdomadaire|mensuel|weekly|monthly|bi[ -]?weekly)",
    re.I,
)


def notice_blobs(tender: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for key in ("description", "cdc_text", "eligibility"):
        text = str(tender.get(key) or "").strip()
        if text and not fetch_text_is_noise(text):
            rows.append(text)
    return rows


def pick_notice_fact(tender: dict[str, Any], pattern: re.Pattern[str]) -> str:
    for blob in notice_blobs(tender):
        match = pattern.search(blob)
        if match:
            return " ".join(match.group(0).split())[:180]
    return ""


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip(" .:;-")


def _is_blank(value: Any, missing: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return _norm(text) in {_norm(missing), "n/a", "not on file", "non renseigne"}


def _is_deadline_req(text: str) -> bool:
    return bool(_DEADLINE_REQ.search(str(text or "").strip()))


def _requirement_kind(text: str) -> str:
    low = str(text or "").lower()
    if _is_deadline_req(low):
        return "milestone"
    if re.search(
        r"kbis|rne|attestation|certif|iso|reference|cv |justificatif|portail|"
        r"caution|bond|lettre de|offre financiere|financial offer|eligib",
        low,
    ):
        return "admin"
    if re.search(
        r"architect|stack|api|cloud|securit|heberg|integr|schema|technique|infra",
        low,
    ):
        return "tech"
    return "func"


def need_is_thin(tender: dict[str, Any], analysis: dict[str, Any], missing: str = "") -> bool:
    need = _need_text(tender, analysis, missing)
    title = str(tender.get("title") or "").strip()
    if _is_blank(need, missing):
        return True
    if title and _norm(need) == _norm(title):
        return True
    desc = str(tender.get("description") or "")
    if desc and not fetch_text_is_noise(desc) and _norm(desc) != _norm(title) and len(desc.strip()) >= 40:
        return False
    cdc = str(tender.get("cdc_text") or "")
    if cdc and not fetch_text_is_noise(cdc) and len(cdc.strip()) >= 40:
        return False
    return True


def crafts_cover_notice(tender: dict[str, Any], crafts: str, profile: dict[str, Any] | None = None) -> bool:
    parts = [
        str(tender.get(key) or "")
        for key in ("title", "description", "cdc_text", "eligibility", "sector")
    ]
    if fetch_text_is_noise(str(tender.get("description") or "")):
        parts = [str(tender.get(key) or "") for key in ("title", "eligibility", "sector")]
    blob = " ".join(parts).lower()
    hay = set(re.findall(r"[a-zà-ÿ]{2,}", blob))
    tokens: list[str] = []
    tokens.extend(part.strip() for part in re.split(r"[,;/|]", crafts or "") if part.strip())
    if profile:
        tokens.extend(str(item).strip() for item in (profile.get("crafts") or []) if str(item).strip())
        tokens.extend(str(item).strip() for item in (profile.get("project_types") or []) if str(item).strip())
        if str(profile.get("specialty") or "").strip():
            tokens.append(str(profile.get("specialty")).strip())
    for token in tokens:
        low = token.lower().strip()
        if len(low) < 2 or _is_blank(low, ""):
            continue
        if len(low) <= 3:
            if re.search(rf"(?<![a-zà-ÿ]){re.escape(low)}(?![a-zà-ÿ])", blob):
                return True
            continue
        if low in blob:
            return True
        words = [part for part in re.findall(r"[a-zà-ÿ]{3,}", low) if part not in _STOP]
        if any(word in hay for word in words if len(word) >= 4):
            return True
    return False


def _raci_marks(role: str) -> tuple[str, str, str, str]:
    low = str(role or "").lower()
    if re.search(r"chef|directeur|director|lead|pm|projet|project", low):
        return ("", "X", "", "X")
    if re.search(r"tech|architect|data|dev|ingeni", low):
        return ("X", "", "X", "")
    if re.search(r"qualit|qa|revue|review", low):
        return ("", "X", "X", "")
    if re.search(r"commerc|sales|bid|offre", low):
        return ("", "", "X", "X")
    return ("X", "", "", "")


def _need_text(tender: dict[str, Any], analysis: dict[str, Any], missing: str) -> str:
    title = str(tender.get("title") or "").strip()
    for raw in (analysis.get("need"), tender.get("description"), title):
        text = str(raw or "").strip()
        if text and not fetch_text_is_noise(text):
            return text
    return title or missing


def _req_texts(analysis: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for item in analysis.get("requirements") or []:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
        else:
            text = str(item).strip()
        if text:
            rows.append(text)
    return rows


def _price_book_lines(profile: dict[str, Any], missing: str) -> str:
    _ = missing
    book = profile.get("price_book")
    if isinstance(book, str) and book.strip():
        return book.strip()
    if not isinstance(book, list) or not book:
        return ""
    lines: list[str] = []
    for item in book[:40]:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or item.get("item") or "").strip() or "poste a completer"
            price = str(item.get("price") or item.get("amount") or item.get("unit_price") or "").strip() or "montant a completer"
            unit = str(item.get("unit") or item.get("uom") or "").strip()
            lines.append(f"- {label}: {price}" + (f" / {unit}" if unit else ""))
        elif str(item).strip():
            lines.append(f"- {item}")
    return "\n".join(lines)


def draft_cover(
    tender: dict[str, Any],
    profile: dict[str, Any],
    *,
    lang: str,
    missing: str,
    company: str,
) -> str:
    title = stated(tender.get("title"), missing)
    buyer = stated(tender.get("buyer"), missing)
    deadline = stated(tender.get("deadline"), missing)
    ref = stated(tender.get("reference") or tender.get("notice_id"), missing)
    country = stated(tender.get("country"), missing)
    if lang == "fr":
        return _lines(
            "DOSSIER DE CANDIDATURE",
            company,
            title,
            "",
            f"Acheteur : {buyer}",
            f"Reference : {ref}",
            f"Pays : {country}",
            f"Date limite : {deadline}",
            "",
            "Memoire technique et financier - usage exclusif de la commission d'analyse.",
            "Document confidentiel. Les elements absents du dossier societe restent marques.",
        )
    return _lines(
        "SUBMISSION DOSSIER",
        company,
        title,
        "",
        f"Contracting authority: {buyer}",
        f"Reference: {ref}",
        f"Country: {country}",
        f"Deadline: {deadline}",
        "",
        "Technical and financial memorandum - for the evaluation committee only.",
        "Confidential. Facts absent from the company file stay marked.",
    )


def draft_toc(lang: str) -> str:
    skip = {"cover", "toc", "revision_notes"}
    rows = [
        f"{index}. {section_title(key, lang)}"
        for index, key in enumerate((k for k in SECTION_KEYS if k not in skip), start=1)
    ]
    head = "Sommaire du memoire" if lang == "fr" else "Dossier contents"
    return _lines(head, "", *rows)


def _partner_label(item: Any) -> str:
    if isinstance(item, dict):
        return " / ".join(
            str(item.get(key) or "").strip() for key in ("name", "role", "country") if str(item.get(key) or "").strip()
        )
    return str(item or "").strip()


def draft_company(
    tender: dict[str, Any],
    profile: dict[str, Any],
    *,
    lang: str,
    missing: str,
    company: str,
    crafts: str,
    tender_types: str,
    project_types: str,
    specialty: str,
    strengths: str,
) -> str:
    legal = stated(profile.get("legal_name"), company)
    certs = ", ".join(str(item) for item in (profile.get("certifications") or []) if str(item).strip()) or missing
    countries = ", ".join(str(item) for item in (profile.get("countries") or []) if str(item).strip()) or missing
    partners = ", ".join(
        part
        for part in (_partner_label(item) for item in (profile.get("partners") or []))
        if part
    ) or missing
    headcount = profile.get("headcount")
    turnover = profile.get("turnover")
    email = stated(profile.get("email"), missing)
    phone = stated(profile.get("phone"), missing)
    website = stated(profile.get("website"), missing)
    sites = []
    for row in profile.get("sites") or []:
        if isinstance(row, dict):
            label = " ".join(
                str(row.get(key) or "").strip() for key in ("kind", "city", "country", "label", "name") if row.get(key)
            )
            if label:
                sites.append(label)
        elif str(row).strip():
            sites.append(str(row).strip())
    notice_country = stated(tender.get("country"), missing)
    contact_bits = [part for part in (email, phone, website) if not _is_blank(part, missing)]
    identity = [
        ("Enseigne" if lang == "fr" else "Trading name", company),
        ("Raison sociale" if lang == "fr" else "Legal name", legal),
        ("Specialite" if lang == "fr" else "Specialty", specialty),
        ("Metiers" if lang == "fr" else "Crafts", crafts),
        ("Types de marches" if lang == "fr" else "Tender types", tender_types),
        ("Types de projets" if lang == "fr" else "Project types", project_types),
        ("Certifications au dossier" if lang == "fr" else "Certifications on file", certs),
        ("Pays d'intervention" if lang == "fr" else "Operating countries", countries),
        ("Partenaires" if lang == "fr" else "Partners", partners),
        ("Effectif" if lang == "fr" else "Headcount", headcount),
        ("Chiffre d'affaires" if lang == "fr" else "Turnover", turnover),
        ("Implantations" if lang == "fr" else "Sites", ", ".join(sites)),
        ("Contact", " / ".join(contact_bits)),
        ("Pays de l'avis" if lang == "fr" else "Notice country", notice_country),
    ]
    filled: list[str] = []
    gaps: list[str] = []
    for label, value in identity:
        if _is_blank(value, missing):
            gaps.append(label)
        else:
            filled.append(f"- {label} : {value}" if lang == "fr" else f"- {label}: {value}")
    capacity: list[str] = []
    if not _is_blank(specialty, missing):
        capacity.append(specialty)
    if not _is_blank(crafts, missing):
        capacity.append(crafts)
    if lang == "fr":
        intro = [
            f"{legal} presente ici sa capacite a tenir {stated(tender.get('title'), missing)}, "
            "avec les seuls elements deja inscrits au dossier societe.",
        ]
        if capacity:
            intro.append(f"{legal} intervient comme {' / '.join(capacity)}.")
        if not _is_blank(tender_types, missing):
            intro.append(f"Marches deja couverts au dossier : {tender_types}.")
        if not _is_blank(project_types, missing):
            intro.append(f"Projets deja realises : {project_types}.")
        if not _is_blank(strengths, missing):
            intro.append(f"Ce que le dossier met en avant, sans ajout : {strengths}.")
        lines = [*intro, "", "Fiche d'identite", *filled]
        if gaps:
            lines.extend(
                [
                    "",
                    "A completer au dossier societe (aucune invention)",
                    _bullet_list(gaps),
                ]
            )
        return _lines(*lines)
    intro = [
        f"{legal} presents its capacity to deliver {stated(tender.get('title'), missing)}, "
        "using on-file company facts only.",
    ]
    if capacity:
        intro.append(f"{legal} acts as {' / '.join(capacity)}.")
    if not _is_blank(tender_types, missing):
        intro.append(f"Tender types already on file: {tender_types}.")
    if not _is_blank(project_types, missing):
        intro.append(f"Completed project types: {project_types}.")
    if not _is_blank(strengths, missing):
        intro.append(f"Differentiators on file, nothing added: {strengths}.")
    lines = [*intro, "", "Identity card", *filled]
    if gaps:
        lines.extend(["", "Still to complete on the company file (nothing invented)", _bullet_list(gaps)])
    return _lines(*lines)


def draft_need(
    tender: dict[str, Any],
    analysis: dict[str, Any],
    *,
    lang: str,
    missing: str,
    buyer: str,
    criteria: str,
) -> str:
    title = stated(tender.get("title"), missing)
    need = _need_text(tender, analysis, missing)
    thin = need_is_thin(tender, analysis, missing)
    eligibility = stated(tender.get("eligibility") or analysis.get("eligibility"), "")
    if _is_blank(eligibility, missing):
        eligibility = "absente de l'avis" if lang == "fr" else "absent from the notice"
    deadline = stated(tender.get("deadline") or analysis.get("deadline"), "")
    if _is_blank(deadline, missing):
        deadline = "absente de l'avis" if lang == "fr" else "absent from the notice"
    budget = tender.get("budget")
    budget_txt = str(budget) if budget not in (None, "") else (
        "absent de l'avis" if lang == "fr" else "absent from the notice"
    )
    reqs = [
        item
        for item in _req_texts(analysis)[:12]
        if not _is_deadline_req(item) and _norm(item) != _norm(title)
    ]
    gaps = [str(item) for item in (analysis.get("gaps") or analysis.get("risks") or []) if str(item).strip()]
    reqs_header = (
        "3. Exigences extraites (sans ajout)"
        if reqs
        else (
            "3. Exigences detaillees : CDC non lu. Rien n'est invente."
            if thin
            else "3. Exigences detaillees : aucune ligne discrete extraite. Rien n'est invente."
        )
    ) if lang == "fr" else (
        "3. Requirements extracted (nothing added)"
        if reqs
        else (
            "3. Detailed requirements: specification unread. Nothing is invented."
            if thin
            else "3. Detailed requirements: no discrete line extracted. Nothing is invented."
        )
    )
    if lang == "fr":
        need_block = (
            [
                "L'avis ne livre que l'intitule. Le cahier des charges n'a pas pu etre lu "
                "(page officielle absente ou inutilisable). Aucun perimetre n'est reconstitue.",
                f"Intitule publie : {title}.",
            ]
            if thin
            else [need]
        )
        cdc_open = [
            "livrables, volumes et modalites de recette",
            "criteres d'attribution ponderes",
            "stack / architecture acheteur",
            "SLA et rythme de reporting",
            "pieces administratives exigees",
        ]
        return _lines(
            f"Lecture structuree de l'avis emis par {buyer}.",
            f"Intitule : {title}.",
            "",
            "1. Besoin exprime",
            *need_block,
            "",
            "2. Contraintes lues dans l'avis",
            f"- Eligibilite : {eligibility}",
            f"- Criteres d'attribution : {criteria}",
            f"- Budget publie : {budget_txt}",
            f"- Echeance de depot : {deadline}",
            "",
            reqs_header,
            _bullet_list(reqs),
            "",
            "4. Points ouverts a la revue" if (gaps or thin) else "",
            _bullet_list(gaps + (cdc_open if thin else [])),
        )
    need_block = (
        [
            "The notice only states the title. The specification could not be read "
            "(official page missing or unusable). No scope is reconstructed.",
            f"Published title: {title}.",
        ]
        if thin
        else [need]
    )
    cdc_open = [
        "deliverables, volumes and acceptance rules",
        "weighted award criteria",
        "buyer stack / architecture",
        "SLAs and reporting cadence",
        "required administrative exhibits",
    ]
    return _lines(
        f"Structured reading of the notice issued by {buyer}.",
        f"Title: {title}.",
        "",
        "1. Stated need",
        *need_block,
        "",
        "2. Constraints as read",
        f"- Eligibility: {eligibility}",
        f"- Award criteria: {criteria}",
        f"- Published budget: {budget_txt}",
        f"- Submission deadline: {deadline}",
        "",
        reqs_header,
        _bullet_list(reqs),
        "",
        "4. Open points for review" if (gaps or thin) else "",
        _bullet_list(gaps + (cdc_open if thin else [])),
    )


def draft_approach(
    tender: dict[str, Any],
    profile: dict[str, Any],
    *,
    lang: str,
    missing: str,
    methodology: str,
    crafts: str,
) -> str:
    title = stated(tender.get("title"), missing)
    aligned = crafts_cover_notice(tender, crafts)
    method_filled = not _is_blank(methodology, missing)
    if lang == "fr":
        method_line = (
            f"Methode au dossier : {methodology}"
            if method_filled
            else "Methode au dossier : a completer. Les phases ci-dessous restent le cadre, sans invention."
        )
        deliver = (
            f"Executer selon les metiers au dossier ({crafts}). Aucune stack n'est ajoutee hors dossier."
            if aligned and not _is_blank(crafts, missing)
            else (
                f"Les metiers au dossier ({crafts}) restent au fichier societe. "
                "Ils ne sont pas poses comme lots de ce marche tant que le CDC ne les recouvre pas."
                if not _is_blank(crafts, missing)
                else "Aucun metier au dossier. La realisation reste a cadrer apres lecture du CDC."
            )
        )
        return _lines(
            f"Demarche proposee pour {title}.",
            "Nous construisons une offre executable : perimetre fige, exigences tracees, lots tenus seulement s'ils recouvrent l'avis.",
            "",
            "1. Cadrage",
            "Relire l'avis et le CDC, figer le perimetre, lister les pieces, les questions et les exclusions.",
            "2. Conception",
            "Ecrire la reponse fonctionnelle, la reponse technique et la matrice de conformite. Chaque exigence lue recoit une reponse.",
            "3. Realisation",
            deliver,
            "4. Transfert",
            "Recette, documentation et conduite du changement si l'avis les demande. Le rythme de reporting est pose au chapitre suivi.",
            "",
            method_line,
        )
    method_line = (
        f"On-file method: {methodology}"
        if method_filled
        else "On-file method: still to complete. The phases below stay the frame, nothing invented."
    )
    deliver = (
        f"Execute with on-file crafts ({crafts}). No stack is added off file."
        if aligned and not _is_blank(crafts, missing)
        else (
            f"On-file crafts ({crafts}) stay on the company file. "
            "They are not presented as lots for this notice until the specification covers them."
            if not _is_blank(crafts, missing)
            else "No craft on file. Delivery stays to be framed after the specification is read."
        )
    )
    return _lines(
        f"Proposed approach for {title}.",
        "We build an executable offer: frozen scope, traced requirements, lots held only when they cover the notice.",
        "",
        "1. Frame",
        "Reread the notice and specification, freeze scope, list exhibits, questions and exclusions.",
        "2. Design",
        "Write the functional response, the technical response and the compliance matrix. Every extracted requirement gets an answer.",
        "3. Deliver",
        deliver,
        "4. Transfer",
        "Acceptance, documentation and change support if the notice asks for them. Reporting sits in the follow-up chapter.",
        "",
        method_line,
    )


def draft_vision(
    tender: dict[str, Any],
    analysis: dict[str, Any],
    *,
    lang: str,
    missing: str,
    company: str,
    criteria: str,
    strengths: str,
) -> str:
    title = stated(tender.get("title"), missing)
    need = _need_text(tender, analysis, missing)
    thin = need_is_thin(tender, analysis, missing)
    if lang == "fr":
        thread = (
            f"Le CDC n'est pas lisible a ce stade. Nous ne formulons pas une vision de solution a partir du seul intitule {title}."
            if thin
            else f"Le fil conducteur reste le besoin publie : {need}"
        )
        strength_line = (
            ""
            if _is_blank(strengths, missing)
            else f"Les differenciateurs que nous mettons en avant, et seulement ceux-la : {strengths}."
        )
        return _lines(
            f"{company} lit {title} comme un projet a livrer, pas comme une brochure.",
            thread,
            "La cible de cette offre est une solution conforme, mesurable et tenable avec les moyens deja au dossier.",
            f"Les criteres d'attribution lus orientent l'effort de redaction : {criteria}.",
            strength_line,
            "Aucun certificat, prix ou reference n'est ajoute pour forcer un avantage.",
        )
    thread = (
        f"The specification is not readable at this stage. We do not invent a solution vision from the title {title} alone."
        if thin
        else f"The thread remains the published need: {need}"
    )
    strength_line = (
        ""
        if _is_blank(strengths, missing)
        else f"Differentiators we put forward, and only those: {strengths}."
    )
    return _lines(
        f"{company} reads {title} as a project to deliver, not as a brochure.",
        thread,
        "This offer aims at a compliant, measurable solution that the on-file means can hold.",
        f"Award criteria as read steer the writing effort: {criteria}.",
        strength_line,
        "No certificate, price or reference is added to force an advantage.",
    )


def draft_functional(
    tender: dict[str, Any],
    analysis: dict[str, Any],
    *,
    lang: str,
    missing: str,
    crafts: str,
    methodology: str,
) -> str:
    title = str(tender.get("title") or "")
    reqs = [
        item
        for item in _req_texts(analysis)
        if not _is_deadline_req(item) and _norm(item) != _norm(title)
    ]
    aligned = crafts_cover_notice(tender, crafts)
    functional = [
        item
        for item in reqs
        if re.search(
            r"fournir|livrable|service|module|fonction|besoin|shall|must|deliver|scope|requirement|"
            r"refonte|modernisation|plateforme|decisionnel|analytics|fourniture|travaux",
            item,
            re.I,
        )
    ] or reqs[:16]
    if len(functional) < 4 and reqs:
        seen = {item.lower() for item in functional}
        for item in reqs:
            if item.lower() not in seen:
                functional.append(item)
            if len(functional) >= 16:
                break
    method = methodology[:400] if methodology != missing and not _is_blank(methodology, missing) else missing

    def _cover(item: str, *, fr: bool) -> str:
        kind = _requirement_kind(item)
        if kind == "milestone":
            return (
                f"- {item} : jalon de depot, pas une piece a produire."
                if fr
                else f"- {item}: filing milestone, not an exhibit to produce."
            )
        if kind == "admin":
            return (
                f"- {item} : piece a produire depuis le dossier societe, sans document invente."
                if fr
                else f"- {item}: exhibit to produce from the company file, no invented document."
            )
        if kind == "tech":
            if aligned:
                return (
                    f"- {item} : traite dans la reponse technique, sous les metiers {crafts}."
                    if fr
                    else f"- {item}: handled in the technical response, under crafts {crafts}."
                )
            return (
                f"- {item} : exigence technique lue. Les metiers au dossier ({crafts}) ne recouvrent pas cet avis."
                if fr
                else f"- {item}: technical requirement as read. On-file crafts ({crafts}) do not cover this notice."
            )
        if aligned:
            return (
                f"- {item} : service / livrable tenu dans le perimetre {crafts}."
                if fr
                else f"- {item}: service / deliverable held within {crafts}."
            )
        return (
            f"- {item} : exigence lue. Les metiers au dossier ({crafts}) ne recouvrent pas cet avis ; aucune couverture inventee."
            if fr
            else f"- {item}: requirement as read. On-file crafts ({crafts}) do not cover this notice; no coverage is invented."
        )

    craft_line = (
        f"Couverture metier au dossier : {crafts}."
        if aligned
        else f"Metiers au dossier ({crafts}) : non alignes sur cet avis. Ils ne sont pas presentes comme couverture."
        if lang == "fr"
        else (
            f"On-file craft coverage: {crafts}."
            if aligned
            else f"On-file crafts ({crafts}): not aligned on this notice. They are not presented as coverage."
        )
    )
    if lang == "fr":
        head = [
            "Reponse fonctionnelle alignee sur l'avis. Aucun usage metier n'est invente.",
            craft_line,
            f"Ancrage methodologique : {method}." if not _is_blank(method, missing) else "Ancrage methodologique : a completer au dossier.",
            "",
            "Chaque exigence extraite recoit une lecture honnete. "
            "Les modalites de recette et les volumes absents de l'avis restent a figer a la revue.",
        ]
        if functional:
            head.append("")
            head.append("Couverture des exigences lues")
            head.extend(_cover(item, fr=True) for item in functional[:16])
        else:
            head.append("Exigences fonctionnelles detaillees : CDC non lu ou absent. Rien n'est invente.")
        return _lines(*head)
    craft_line = (
        f"On-file craft coverage: {crafts}."
        if aligned
        else f"On-file crafts ({crafts}): not aligned on this notice. They are not presented as coverage."
    )
    head = [
        "Functional response aligned on the notice. No use case is invented.",
        craft_line,
        f"Method anchor: {method}." if not _is_blank(method, missing) else "Method anchor: still to complete on file.",
        "",
        "Each extracted requirement gets an honest reading. "
        "Acceptance rules and volumes missing from the notice stay for review.",
    ]
    if functional:
        head.append("")
        head.append("Coverage of requirements as read")
        head.extend(_cover(item, fr=False) for item in functional[:16])
    else:
        head.append("Detailed functional requirements: specification unread or missing. Nothing is invented.")
    return _lines(*head)


def draft_followup_kpi(
    tender: dict[str, Any],
    analysis: dict[str, Any],
    *,
    lang: str,
    missing: str,
    deadline: str,
) -> str:
    _ = (analysis, missing)
    days = (tender.get("score_breakdown") or {}).get("days_left")
    cadence = pick_notice_fact(tender, _REPORT_FACT)
    if lang == "fr":
        days_line = (
            f"Jours restants lus dans l'avis : {days}."
            if days not in (None, "")
            else "Jours restants : non calcules (avis sans score)."
        )
        freq_line = (
            f"Frequence de reporting lue dans l'avis : {cadence}."
            if cadence
            else (
                "Frequence de reporting : non lue dans l'avis. "
                "Proposition : comite hebdomadaire jusqu'au depot, puis le rythme du CDC s'il est precise."
            )
        )
        return _lines(
            "Dispositif de suivi propose a la commission. Les cibles chiffrees absentes du dossier restent ouvertes : elles ne sont pas inventees comme SLA.",
            f"Date limite de depot : {deadline}.",
            days_line,
            "",
            "Indicateurs proposes (a contractualiser)",
            "| Indicateur | Lecture | Cible |",
            "| --- | --- | --- |",
            "| Avancement | % de jalons tenus vs planning valide | a figer |",
            "| Qualite | anomalies ouvertes / fermees a chaque comite | a figer |",
            "| Delai | ecart vs date limite et vs jalons internes | depot avant l'echeance |",
            "| Conformite | lignes de la matrice encore ouvertes | 0 ligne ouverte au depot |",
            "| Risques | risques ouverts, proprietaire, echeance de mitigation | revue a chaque comite |",
            "",
            freq_line,
        )
    days_line = (
        f"Days left as read: {days}."
        if days not in (None, "")
        else "Days left: not computed (notice without a score)."
    )
    freq_line = (
        f"Reporting cadence as read: {cadence}."
        if cadence
        else (
            "Reporting cadence: not read in the notice. "
            "Proposal: weekly until filing, then the specification rhythm if stated."
        )
    )
    return _lines(
        "Proposed steering for the committee. Numeric targets missing from the file stay open: they are not invented as SLAs.",
        f"Submission deadline: {deadline}.",
        days_line,
        "",
        "Proposed indicators (to be contracted)",
        "| Indicator | Reading | Target |",
        "| --- | --- | --- |",
        "| Progress | % of milestones met vs the agreed plan | to be set |",
        "| Quality | open / closed defects at each steering meeting | to be set |",
        "| Time | gap vs the deadline and vs internal milestones | file before the deadline |",
        "| Compliance | matrix rows still open | 0 open rows at filing |",
        "| Risk | open risks, owner, mitigation date | reviewed at each meeting |",
        "",
        freq_line,
    )


def draft_raci_risks(
    tender: dict[str, Any],
    profile: dict[str, Any],
    analysis: dict[str, Any],
    *,
    lang: str,
    missing: str,
    staffing: str,
) -> str:
    _ = (staffing, missing)
    team = [row for row in (profile.get("team") or []) if isinstance(row, dict)][:8]
    defaults = (
        ("Chef de projet", "Project lead"),
        ("Referent technique", "Technical lead"),
        ("Qualite / revue", "Quality / review"),
        ("Commercial", "Commercial"),
    )
    if lang == "fr":
        lines = [
            "Matrice RACI proposee a partir de l'equipe au dossier. Les noms absents restent a pourvoir.",
            "R = realise, A = approuve, C = consulte, I = informe.",
            "",
            "| Role | R | A | C | I | Nom |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        rows = team or [{"role": fr_role, "name": "a pourvoir"} for fr_role, _en in defaults]
        for row in rows:
            role = stated(row.get("role"), "role a pourvoir")
            name = stated(row.get("name"), "a pourvoir")
            r, a, c, i = _raci_marks(role)
            lines.append(f"| {role} | {r} | {a} | {c} | {i} | {name} |")
        lines.extend(["", "Risques lus (avis + dossier), sans ajout :"])
        risks = [str(item) for item in (analysis.get("risks") or analysis.get("gaps") or []) if str(item).strip()]
        lines.append(_bullet_list(risks) if risks else "Aucun risque explicite dans l'avis.")
        return _lines(*lines)
    lines = [
        "Proposed RACI from the on-file team. Missing names stay unnamed.",
        "R = responsible, A = accountable, C = consulted, I = informed.",
        "",
        "| Role | R | A | C | I | Name |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    rows = team or [{"role": en, "name": "to be staffed"} for _fr, en in defaults]
    for row in rows:
        role = stated(row.get("role"), "role to staff")
        name = stated(row.get("name"), "to be staffed")
        r, a, c, i = _raci_marks(role)
        lines.append(f"| {role} | {r} | {a} | {c} | {i} | {name} |")
    lines.extend(["", "Risks as read (notice + file), nothing added:"])
    risks = [str(item) for item in (analysis.get("risks") or analysis.get("gaps") or []) if str(item).strip()]
    lines.append(_bullet_list(risks) if risks else "No explicit risk in the notice.")
    return _lines(*lines)


def draft_governance(
    tender: dict[str, Any],
    profile: dict[str, Any],
    *,
    lang: str,
    missing: str,
    buyer: str,
    staffing: str,
) -> str:
    clauses = stated(profile.get("legal_clauses"), "")
    clause_line = (
        f"Clauses legales au dossier : {clauses}."
        if lang == "fr" and not _is_blank(clauses, missing)
        else (
            f"Legal clauses on file: {clauses}."
            if lang != "fr" and not _is_blank(clauses, missing)
            else (
                "Clauses legales : a completer au dossier societe."
                if lang == "fr"
                else "Legal clauses: still to complete on the company file."
            )
        )
    )
    if lang == "fr":
        return _lines(
            f"Gouvernance proposee pour le marche de {buyer}.",
            "Les instances acheteur non decrites dans l'avis seront nommees a la reunion de lancement.",
            "",
            "Instances",
            "- Comite de pilotage : arbitrage de perimetre, risques majeurs, jalons. Frequence a figer avec l'acheteur.",
            "- Comite projet : avancement, livrables, questions ouvertes. Frequence proposee : hebdomadaire jusqu'au depot, puis selon le CDC.",
            "- Point ecrit : compte rendu court (decisions, actions, ecarts, risques).",
            "- Escalade : chef de projet candidat vers le representant acheteur designe dans l'avis.",
            "",
            f"Equipe candidate :\n{staffing}",
            clause_line,
        )
    return _lines(
        f"Proposed governance for the {buyer} contract.",
        "Buyer bodies not described in the notice will be named at the kick-off.",
        "",
        "Bodies",
        "- Steering committee: scope, major risks, milestones. Cadence to be agreed with the buyer.",
        "- Project committee: progress, deliverables, open questions. Proposed cadence: weekly until filing, then as specified.",
        "- Written point: short minutes (decisions, actions, gaps, risks).",
        "- Escalation: bidder project lead to the buyer representative named in the notice.",
        "",
        f"Bidder team:\n{staffing}",
        clause_line,
    )


def draft_budget(
    tender: dict[str, Any],
    profile: dict[str, Any],
    *,
    lang: str,
    missing: str,
) -> str:
    published = tender.get("budget")
    published_txt = str(published) if published not in (None, "") else (
        "absent de l'avis" if lang == "fr" else "absent from the notice"
    )
    currency_raw = stated(profile.get("currency") or tender.get("currency"), "")
    currency = currency_raw if not _is_blank(currency_raw, missing) else (
        "a completer au dossier societe" if lang == "fr" else "still to complete on file"
    )
    book = _price_book_lines(profile, missing)
    if lang == "fr":
        lines = [
            "Volet financier. Aucun prix n'est invente.",
            f"Budget publie dans l'avis : {published_txt}"
            + (f" {currency_raw}" if published not in (None, "") and currency_raw else ""),
            f"Devise au dossier : {currency}.",
        ]
        if book:
            lines.append("Bordereau au dossier :")
            lines.append(book)
        else:
            lines.append("Prix unitaires : a completer au dossier societe.")
            lines.append("Le bordereau sera complete a la revue a partir des pieces au dossier, pas par estimation libre.")
        return _lines(*lines)
    lines = [
        "Financial chapter. No price is invented.",
        f"Budget published in the notice: {published_txt}"
        + (f" {currency_raw}" if published not in (None, "") and currency_raw else ""),
        f"Currency on file: {currency}.",
    ]
    if book:
        lines.append("On-file price book:")
        lines.append(book)
    else:
        lines.append("Unit prices: still to complete on the company file.")
        lines.append("The schedule will be completed at review from on-file exhibits, not from a free estimate.")
    return _lines(*lines)


def draft_letter(
    tender: dict[str, Any],
    profile: dict[str, Any],
    *,
    lang: str,
    missing: str,
    company: str,
    specialty: str,
    crafts: str,
    tender_types: str,
    project_types: str,
    strengths: str,
    buyer: str,
    deadline: str,
    portal: str,
    title: str,
    need: str,
    style_docs: list[dict[str, Any]],
    style_names: str,
    style_quote: str,
    excerpts: list[str],
) -> str:
    def _fact(label: str, value: str) -> str:
        return f"{label} {value}" if not _is_blank(value, missing) else ""

    if lang == "fr":
        facts = " ".join(
            part
            for part in (
                _fact("Specialite au dossier :", specialty),
                _fact("Metiers mobilises :", crafts),
                _fact("Types d'AO deja tenus :", tender_types),
                _fact("Projets deja realises :", project_types),
                _fact("Points forts declares :", strengths),
            )
            if part
        ) or "Le dossier societe ne porte encore aucun metier, type d'AO ni point fort declare."
        letter = (
            f"Objet : Candidature - {title}\n\n"
            f"Madame, Monsieur,\n\n"
            f"{company} depose une offre pour {title}, a l'attention de {buyer}. "
            f"Nous avons lu le besoin publie et nous y repondons avec les moyens deja au dossier.\n\n"
            f"Lecture du besoin : {need}\n"
            f"{facts}\n"
            f"Les faits de ce memoire viennent du dossier societe et de l'avis. "
            f"Les absents sont listes au registre societe. Rien n'est invente.\n\n"
            f"Nous tenons l'echeance du {deadline} et restons disponibles pour toute precision. "
            f"Portail de depot : {portal}.\n\n"
            f"Veuillez agreer, Madame, Monsieur, l'expression de nos salutations distinguees.\n"
            f"{company}\n"
        )
        if style_docs:
            letter += (
                f"\nNote interne : ce brouillon reprend les modeles au dossier ({style_names}). "
                "Formulation amelioree, jamais inventee.\n"
            )
            if style_quote:
                letter += f"Extrait conserve : {style_quote[:400]}\n"
        elif excerpts:
            letter += "\nNote interne : le style et les slides du dossier ont ete reutilises.\n"
        return letter
    facts = " ".join(
        part
        for part in (
            _fact("On-file specialty:", specialty),
            _fact("Crafts mobilised:", crafts),
            _fact("Tender types already held:", tender_types),
            _fact("Completed projects:", project_types),
            _fact("Declared strengths:", strengths),
        )
        if part
    ) or "The company file does not yet declare a craft, tender type or strength."
    letter = (
        f"Subject: Submission - {title}\n\n"
        f"Dear {buyer},\n\n"
        f"{company} submits an offer for {title}. "
        f"We have read the published need and we answer it with means already on file.\n\n"
        f"Reading of the need: {need}\n"
        f"{facts}\n"
        f"Facts in this memorandum come from the company file and the notice. "
        f"Absents are listed in the company register. Nothing is invented.\n\n"
        f"We will meet the {deadline} deadline and remain available for any clarification. "
        f"Submission portal: {portal}.\n\n"
        f"Yours faithfully,\n"
        f"{company}\n"
    )
    if style_docs:
        letter += (
            f"\nInternal note: this draft reuses the on-file models ({style_names}). "
            "Wording was improved, not invented.\n"
        )
        if style_quote:
            letter += f"Kept extract: {style_quote[:400]}\n"
    elif excerpts:
        letter += "\nInternal note: style and slides on file were reused.\n"
    return letter


def draft_summary(
    tender: dict[str, Any],
    analysis: dict[str, Any],
    *,
    lang: str,
    company: str,
    title: str,
    criteria: str,
) -> str:
    need = str(analysis.get("need") or title).rstrip(" .")
    thin = need_is_thin(tender, analysis, "non renseigne" if lang == "fr" else "not on file")
    score = tender.get("score") or 0
    buyer = stated(tender.get("buyer"), "l'acheteur" if lang == "fr" else "the buyer")
    deadline = stated(tender.get("deadline"), "voir l'avis" if lang == "fr" else "see notice")
    if lang == "fr":
        opening = (
            f"{company} a lu l'avis {title}. Le CDC n'est pas lisible : le besoin n'est pas reconstitue. "
            if thin
            else f"{company} comprend le besoin ainsi : {need}. "
        )
        return _lines(
            f"{opening}"
            f"Score de pertinence {score}/100. "
            f"Lecture des criteres : {criteria}.",
            "",
            f"Ce memoire repond a l'avis de {buyer} intitule {title}. "
            "Il pose la comprehension du besoin, la capacite de la societe, la reponse fonctionnelle, "
            "la reponse technique, la methode, le suivi, la gouvernance et le calendrier de depot.",
            f"Echeance tenue : {deadline}. "
            "Les prix, certificats et references absents du dossier restent ouverts. "
            "Rien n'est invente pour forcer un avantage.",
        )
    opening = (
        f"{company} has read the notice {title}. The specification is not readable: the need is not reconstructed. "
        if thin
        else f"{company} understands the need as: {need}. "
    )
    return _lines(
        f"{opening}"
        f"Fit score {score}/100. "
        f"Award reading: {criteria}.",
        "",
        f"This memorandum answers the {buyer} notice titled {title}. "
        "It sets out the need, the company capacity, the functional response, "
        "the technical response, the method, follow-up, governance and the filing calendar.",
        f"Deadline held: {deadline}. "
        "Prices, certificates and references absent from the file stay open. "
        "Nothing is invented to force an advantage.",
    )


def draft_methodology(
    tender: dict[str, Any],
    *,
    lang: str,
    missing: str,
    methodology: str,
    crafts: str,
) -> str:
    title = stated(tender.get("title"), missing)
    body = methodology.rstrip(" .") if methodology and methodology != missing else ""
    if lang == "fr":
        return _lines(
            f"Methodologie de conduite pour {title}.",
            "La methode reprend le dossier societe. Elle n'invente ni stack, ni charge, ni duree.",
            "",
            "Principes",
            "- Cadrer avant de concevoir.",
            "- Tracer chaque exigence jusqu'a un livrable et une preuve.",
            "- Rendre compte a un rythme contractuel, sans indicateur invente.",
            "",
            "Methode au dossier",
            body or "Methode detaillee : a completer au dossier. Les phases ci-dessous restent le cadre de travail.",
            "",
            "Phases types (ordre de travail, sans dates inventees)",
            "1. Cadrage et lecture du CDC / de l'avis",
            "2. Conception fonctionnelle et technique",
            "3. Realisation, integrations et tests",
            "4. Recette, transfert, documentation",
            f"Metiers mobilises : {crafts}.",
        )
    return _lines(
        f"Delivery methodology for {title}.",
        "The method reuses the company file. It invents no stack, load or duration.",
        "",
        "Principles",
        "- Frame before design.",
        "- Trace every requirement to a deliverable and a proof.",
        "- Report on a contractual rhythm, with no invented indicator.",
        "",
        "On-file method",
        body or "Detailed method: still to complete on file. The phases below remain the working frame.",
        "",
        "Typical phases (work order only, no invented dates)",
        "1. Frame the notice / specification",
        "2. Functional and technical design",
        "3. Build, integrate and test",
        "4. Acceptance, transfer, documentation",
        f"Crafts mobilised: {crafts}.",
    )


def draft_references(
    ref_lines: str,
    *,
    lang: str,
    missing: str,
    title: str,
) -> str:
    body = str(ref_lines or "").strip()
    if not body:
        body = (
            "Aucune reference au dossier societe."
            if lang == "fr"
            else "No reference on the company file."
        )
    if lang == "fr":
        return _lines(
            f"References au dossier, lues au regard de {title}.",
            "Aucune reference n'est ajoutee hors dossier. Les montants ou attestations absents restent ouverts.",
            "",
            body,
        )
    return _lines(
        f"References on file, read against {title}.",
        "No reference is added off file. Missing amounts or attestations stay open.",
        "",
        body,
    )
