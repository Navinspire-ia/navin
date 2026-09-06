"""Official need catalogs for wizard step 5 (TED / BOAMP / CPV).

Stored values stay human labels. Scoring, collect and the writer expand them
to official aliases so a typed or picked term is actually used.
"""

from __future__ import annotations

from typing import Any

# Extra aliases kept so existing profiles ("AI", "Data", "Cloud", "Digital")
# keep matching the same notices as before the catalog.
_LEGACY_CRAFT_ALIASES: dict[str, tuple[str, ...]] = {
    "ai": ("ai", "intelligence artificielle", "machine learning", "llm", "genai"),
    "data": (
        "data",
        "decisionnel",
        "bi ",
        "business intelligence",
        "data platform",
        "entrepot",
        "warehouse",
        "analytics",
        "si decisionnel",
    ),
    "cloud": ("cloud", "aws", "azure", "gcp", "saas", "hebergement"),
    "digital": ("digital", "numerique", "transformation", "si ", "information system"),
}


def _row(
    sid: str,
    label: str,
    label_fr: str,
    aliases: tuple[str, ...] = (),
    cpv: str = "",
) -> dict[str, Any]:
    extra = _LEGACY_CRAFT_ALIASES.get(sid, ())
    seen: set[str] = set()
    bag: list[str] = []
    for item in (sid, label, label_fr, *aliases, *extra, cpv):
        token = str(item or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        bag.append(token)
    return {
        "id": sid,
        "label": label,
        "label_fr": label_fr,
        "aliases": tuple(bag),
        "cpv": cpv,
        "keywords": " ".join(bag),
    }


# TED / directive 2014/24 + BOAMP procedure and contract forms.
TENDER_TYPES: tuple[dict[str, Any], ...] = (
    _row("works", "Works", "Travaux", ("works", "travaux", "construction work", "marche de travaux")),
    _row("supplies", "Supplies", "Fournitures", ("supplies", "fournitures", "goods", "achat de materiel")),
    _row("services", "Services", "Services", ("services", "prestations", "marche de services")),
    _row("open", "Open procedure", "Procedure ouverte", ("open procedure", "procedure ouverte", "appel d'offres ouvert")),
    _row("restricted", "Restricted procedure", "Procedure restreinte", ("restricted procedure", "procedure restreinte")),
    _row(
        "negotiation",
        "Competitive negotiation",
        "Procedure concurrentielle avec negociation",
        ("competitive procedure with negotiation", "negociation", "procedure negociee"),
    ),
    _row("dialogue", "Competitive dialogue", "Dialogue competitif", ("competitive dialogue", "dialogue competitif")),
    _row(
        "innovation",
        "Innovation partnership",
        "Partenariat d'innovation",
        ("innovation partnership", "partenariat d'innovation"),
    ),
    _row("framework", "Framework agreement", "Accord-cadre", ("framework", "accord-cadre", "accord cadre", "framework agreement")),
    _row("dps", "Dynamic purchasing system", "Systeme d'acquisition dynamique", ("dps", "dynamic purchasing", "sad")),
    _row("concession", "Concession", "Concession", ("concession", "concession de service", "dsp")),
    _row("design-contest", "Design contest", "Concours", ("design contest", "concours", "concours de maitrise d'oeuvre")),
    _row("pin", "Prior information notice", "Avis de preinformation", ("prior information", "avis de preinformation", "pin")),
    _row(
        "expression",
        "Call for expression of interest",
        "Manifestation d'interet",
        ("expression of interest", "manifestation d'interet", "ami"),
    ),
    _row("mapa", "Adapted procedure", "Procedure adaptee", ("mapa", "procedure adaptee", "adapted procedure")),
    _row("ppp", "Public-private partnership", "Partenariat public-prive", ("ppp", "partenariat public-prive", "public-private")),
    _row(
        "below-threshold",
        "Below EU threshold",
        "Marche sous-seuil",
        ("below threshold", "sous-seuil", "hors directive"),
    ),
    _row("call-off", "Call-off / specific contract", "Bon de commande", ("call-off", "bon de commande", "marche subsequent")),
)

# CPV main divisions used on TED / BOAMP / national portals, plus digital crafts.
DOMAINS: tuple[dict[str, Any], ...] = (
    _row("agriculture", "Agriculture and farming", "Agriculture et elevage", ("agriculture", "farming", "elevage", "peche"), "03000000"),
    _row("energy-fuel", "Energy, fuel and electricity", "Energie, carburants et electricite", ("petroleum", "fuel", "electricite", "carburant", "hydrocarbure"), "09000000"),
    _row("mining", "Mining and basic metals", "Mines et metaux", ("mining", "metaux", "minerai"), "14000000"),
    _row("food", "Food and beverages", "Denrees alimentaires", ("food", "boissons", "denrees", "restauration produits"), "15000000"),
    _row("agri-machinery", "Agricultural machinery", "Machines agricoles", ("agricultural machinery", "machines agricoles"), "16000000"),
    _row("clothing", "Clothing and footwear", "Habillement et chaussures", ("clothing", "habillement", "chaussures", "vetements"), "18000000"),
    _row("textile", "Leather and textile", "Cuir et textile", ("textile", "cuir", "tissu"), "19000000"),
    _row("print", "Printed matter and publishing", "Imprimes et edition", ("printed matter", "imprimerie", "edition"), "22000000"),
    _row("chemicals", "Chemical products", "Produits chimiques", ("chemical", "chimique", "reactif"), "24000000"),
    _row("office-it", "Office and computing machinery", "Materiel informatique et bureautique", ("computing machinery", "ordinateur", "bureautique", "pc"), "30000000"),
    _row("electrical", "Electrical machinery", "Materiel electrique", ("electrical machinery", "electrique", "tableau electrique"), "31000000"),
    _row("telecom-hw", "Radio, TV and communication", "Radio, TV et communication", ("telecommunication equipment", "radio", "telecom materiel"), "32000000"),
    _row("medical", "Medical equipment", "Equipements medicaux", ("medical equipment", "medical", "biomedical", "hopital materiel"), "33000000"),
    _row("transport-eq", "Transport equipment", "Materiel de transport", ("transport equipment", "vehicule", "flotte", "autobus"), "34000000"),
    _row("security", "Security and fire-fighting", "Securite et incendie", ("security equipment", "incendie", "surete", "videosurveillance"), "35000000"),
    _row("sport", "Musical and sport goods", "Sport et instruments de musique", ("sport", "musical instruments"), "37000000"),
    _row("lab", "Laboratory and optical", "Laboratoire et optique", ("laboratory", "laboratoire", "optique", "mesure"), "38000000"),
    _row("furniture", "Furniture and furnishings", "Mobilier et ameublement", ("furniture", "mobilier", "ameublement"), "39000000"),
    _row("water", "Collected and purified water", "Eau collectee et potabilisee", ("water", "eau potable", "potabilisation"), "41000000"),
    _row("industry-machines", "Industrial machinery", "Machines industrielles", ("industrial machinery", "machine-outil", "industriel"), "42000000"),
    _row("works-machines", "Mining and construction machinery", "Engins de chantier", ("construction machinery", "engin de chantier"), "43000000"),
    _row("structures", "Construction structures and materials", "Structures et materiaux de construction", ("construction materials", "materiaux", "charpente"), "44000000"),
    _row("construction", "Construction work", "Travaux de construction", ("construction work", "batiment", "genie civil", "renovation"), "45000000"),
    _row("software", "Software packages", "Logiciels", ("software", "logiciel", "progiciel", "erp", "saas"), "48000000"),
    _row("maintenance", "Repair and maintenance", "Reparation et maintenance", ("maintenance", "reparation", "entretien", "tma"), "50000000"),
    _row("installation", "Installation services", "Services d'installation", ("installation services", "installation", "mise en service"), "51000000"),
    _row("hospitality", "Hotel, restaurant and travel", "Hotellerie, restauration et voyages", ("hotel", "restaurant", "voyage", "hebergement touristique"), "55000000"),
    _row("transport", "Transport services", "Services de transport", ("transport services", "transport", "logistique"), "60000000"),
    _row("transport-support", "Supporting transport services", "Services auxiliaires de transport", ("supporting transport", "manutention", "entreposage"), "63000000"),
    _row("postal-telecom", "Postal and telecommunications", "Poste et telecommunications", ("postal", "telecommunications", "reseau telecom"), "64000000"),
    _row("utilities", "Public utilities", "Services d'utilite publique", ("public utilities", "utilite publique", "reseau urbain"), "65000000"),
    _row("finance", "Financial and insurance", "Finance et assurance", ("insurance", "assurance", "banque", "finance"), "66000000"),
    _row("real-estate", "Real estate", "Immobilier", ("real estate", "immobilier", "foncier"), "70000000"),
    _row(
        "architecture",
        "Architecture and engineering",
        "Architecture et ingenierie",
        ("architecture", "ingenierie", "maitrise d'oeuvre", "bet", "amo"),
        "71000000",
    ),
    _row(
        "it-services",
        "IT services",
        "Services informatiques",
        (
            "it services",
            "informatique",
            "informatiques",
            "si ",
            "information system",
            "systeme d'information",
            "digital workplace",
        ),
        "72000000",
    ),
    _row("ai", "AI", "Intelligence artificielle", ("ai", "intelligence artificielle", "machine learning", "llm", "genai"), "72000000"),
    _row("data", "Data", "Data et decisionnel", (), "72000000"),
    _row("cloud", "Cloud", "Cloud et hebergement", (), "72000000"),
    _row("digital", "Digital", "Numerique", (), "72000000"),
    _row("cyber", "Cybersecurity", "Cybersecurite", ("cyber", "cybersecurite", "securite informatique", "soc", "iso 27001"), "72000000"),
    _row("research", "Research and development", "Recherche et developpement", ("research", "r&d", "innovation", "etude scientifique"), "73000000"),
    _row("admin", "Administration and defence", "Administration et defense", ("administration", "defense", "securite sociale"), "75000000"),
    _row("oil-gas", "Oil and gas services", "Services petroliers et gaziers", ("oil and gas", "petrole", "gaz", "offshore"), "76000000"),
    _row("agri-services", "Agricultural services", "Services agricoles", ("agricultural services", "espaces verts", "sylviculture"), "77000000"),
    _row("business", "Business services", "Services aux entreprises", ("business services", "conseil", "audit", "recrutement"), "79000000"),
    _row("education", "Education and training", "Education et formation", ("education", "formation", "enseignement"), "80000000"),
    _row("health", "Health and social work", "Sante et action sociale", ("health", "sante", "medico-social", "ehpad"), "85000000"),
    _row(
        "environment",
        "Sewage, waste and environment",
        "Assainissement, dechets et environnement",
        ("sewage", "dechets", "assainissement", "environnement", "voirie"),
        "90000000",
    ),
    _row("culture", "Recreational, cultural and sporting", "Culture, sport et loisirs", ("cultural", "culture", "sport", "loisirs"), "92000000"),
    _row("community", "Other community services", "Autres services collectifs", ("community services", "services collectifs"), "98000000"),
)

PROJECT_TYPES: tuple[dict[str, Any], ...] = (
    _row("erp", "ERP / core information system", "ERP / SI coeur", ("erp", "si coeur", "core system", "sap", "oracle")),
    _row("data-platform", "Data platform / BI", "Plateforme data / BI", ("data platform", "decisionnel", "entrepot", "analytics")),
    _row("cloud-migration", "Cloud migration", "Migration cloud", ("cloud migration", "infogerance cloud", "hebergement cloud")),
    _row("cyber-audit", "Cybersecurity audit", "Audit cybersecurite", ("audit cyber", "pentest", "soc", "iso 27001")),
    _row("software-build", "Custom software build", "Developpement logiciel", ("software development", "developpement", "applicatif")),
    _row("tma", "Application maintenance (TMA)", "Tierce maintenance applicative", ("tma", "run", "maintenance applicative")),
    _row("network", "Network and telecom", "Reseau et telecom", ("reseau", "lan", "wan", "fibre", "telecom")),
    _row("web", "Website / digital service", "Site web / service numerique", ("site web", "portail", "e-service")),
    _row("training-delivery", "Training delivery", "Prestation de formation", ("formation", "training")),
    _row("amo-study", "Studies / project management support", "Etudes / AMO", ("amo", "etude", "assistance a maitrise d'ouvrage")),
    _row("construction-delivery", "Building construction / renovation", "Construction / renovation de batiment", ("construction", "renovation", "batiment")),
    _row("civil-works", "Civil engineering / roads", "Genie civil / voiries", ("genie civil", "voirie", "chaussee", "road")),
    _row("water-works", "Water and sanitation works", "Travaux eau et assainissement", ("assainissement", "station d'epuration", "eau")),
    _row("energy-works", "Energy / HVAC", "Energie / CVC", ("cvc", "chauffage", "photovoltaique", "efficacite energetique")),
    _row("medical-fitout", "Medical fit-out / equipment", "Equipement / amenagement medical", ("equipement medical", "plateau technique")),
    _row("fleet", "Fleet and vehicles", "Flotte et vehicules", ("flotte", "vehicules", "autobus")),
    _row("facility", "Facility management", "Facility management", ("facility", "multiservice", "exploitation de site")),
    _row("cleaning", "Cleaning and waste", "Proprete et dechets", ("proprete", "nettoyage", "dechets", "collecte")),
    _row("catering", "Catering / food service", "Restauration collective", ("restauration collective", "catering")),
    _row("security-ops", "Security operations", "Exploitation de la surete", ("surete", "gardiennage", "videosurveillance")),
    _row("consulting", "Consulting / audit", "Conseil / audit", ("conseil", "audit", "accompagnement")),
)


def _all_rows() -> tuple[dict[str, Any], ...]:
    return TENDER_TYPES + DOMAINS + PROJECT_TYPES


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "label": row["label"],
        "label_fr": row["label_fr"],
        "keywords": row.get("keywords") or "",
        "cpv": row.get("cpv") or "",
    }


def public_needs_catalog() -> dict[str, list[dict[str, Any]]]:
    return {
        "tender_types": [_public(row) for row in TENDER_TYPES],
        "domains": [_public(row) for row in DOMAINS],
        "project_types": [_public(row) for row in PROJECT_TYPES],
    }


def lookup_need(value: str) -> dict[str, Any] | None:
    needle = str(value or "").strip().lower()
    if not needle:
        return None
    for row in _all_rows():
        if needle == row["id"] or needle == str(row["label"]).lower() or needle == str(row["label_fr"]).lower():
            return row
        if needle in (row.get("aliases") or ()):
            return row
    return None


def expand_need_terms(values: Any) -> list[str]:
    """Aliases the scorer and collect must use for each stored label."""
    raw = values if isinstance(values, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        row = lookup_need(text)
        aliases = row["aliases"] if row else (text.lower(),)
        for alias in aliases:
            key = str(alias or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def official_cpv_divisions() -> tuple[str, ...]:
    return tuple(sorted({row["cpv"] for row in DOMAINS if row.get("cpv")}))


def aliases_for_need(value: str) -> tuple[str, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    row = lookup_need(text)
    if row:
        return row["aliases"]
    return (text.lower(),)


def need_family_ids(values: Any) -> set[str]:
    ids: set[str] = set()
    raw = values if isinstance(values, list) else []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        row = lookup_need(text)
        ids.add(str(row["id"]) if row else text.lower())
    return ids


def query_need_terms(*groups: Any, limit: int = 8) -> list[str]:
    """Human labels for collect queries. Do not dump every alias into the search."""
    out: list[str] = []
    seen: set[str] = set()
    for values in groups:
        raw = values if isinstance(values, list) else []
        for item in raw:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= limit:
                return out
    return out
