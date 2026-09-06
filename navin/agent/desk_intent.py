"""Which business desk a chat turn is actually about.

The desk tools (``tenders``, ``career``, ``leads``, ``marketing``,
``trading``) declare dozens of actions each, so their schemas cost ~9k
tokens together. Measured 2026-09-01: a 57k prompt is ~2.3s slower per
model call than a small one *even at a 100% cache hit*, and a multi-step
turn pays that on every step. Shipping all five desks on a turn about
Python was renting that penalty permanently.

Inside a product module the WebUI already scopes them (see
``extra_denied_tools_for_module``). This module covers the rest - CLI,
Telegram, and a plain chat with no module - by reading the user's text the
same rule-based way :mod:`navin.agent.media_intent` reads an image request:
name the desk (or its slash command) and it comes back for the turn.

Rule-based on purpose: the routing must hold when the primary model has
failed over to a smaller one, which is exactly when a model-based router
would drop it.
"""

from __future__ import annotations

import re
import unicodedata

TENDERS_TOOL = "tenders"
CAREER_TOOL = "career"
LEADS_TOOL = "leads"
MARKETING_TOOL = "marketing"
TRADING_TOOL = "trading"
MONTAGE_TOOL = "montage"
CRM_TOOL = "crm"

#: Desks the Code workbench keeps out of the schema until a turn names them.
#: Measured 2026-09-02: montage 1 633 + crm 372 tokens on every step of a
#: code turn that never edits a video or a contact.
CODE_WORKBENCH_LIFTABLE_DESKS: frozenset[str] = frozenset({MONTAGE_TOOL, CRM_TOOL})

#: Slash commands that own a desk. Present anywhere in the message, because a
#: workflow line often carries the brief after the command.
_DESK_COMMANDS: dict[str, tuple[str, ...]] = {
    "/tenders": (TENDERS_TOOL,),
    "/career": (CAREER_TOOL,),
    "/leads": (LEADS_TOOL,),
    "/marketing": (MARKETING_TOOL,),
    "/campaign": (MARKETING_TOOL,),
    "/montage": (MARKETING_TOOL, MONTAGE_TOOL),
    "/trading": (TRADING_TOOL,),
    "/crm": (CRM_TOOL,),
}

# Nouns that name the desk's subject. Accents are stripped before matching,
# so "marché" and "marche" are the same token. Kept deliberately narrow: a
# false positive only costs prompt tokens, but it costs them on every step.
_DESK_NOUNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        TENDERS_TOOL,
        re.compile(
            r"\b("
            r"appels? d offres?|appel d offre|"
            r"marches? publics?|marche public|"
            r"consultations?|dce|boamp|ted europa|"
            r"tenders?|tendering|rfp|rfps|rfq|rfi|"
            r"soumissions?|candidatures? (?:administrative|technique)|"
            r"memoires? techniques?|cctp|ccap|"
            r"procurement|bid|bids|bidding"
            r")\b"
        ),
    ),
    (
        CAREER_TOOL,
        re.compile(
            r"\b("
            r"cv|cvs|resume|resumes|curriculum vitae|"
            r"lettres? de motivation|cover letters?|"
            r"offres? d emploi|offre d emploi|recherche d emploi|"
            r"job (?:search|application|applications|offer|offers|board)|"
            r"candidatures? spontanees?|entretiens? d embauche|"
            r"job interviews?|ats|linkedin profile|"
            r"salaire|salary negotiation|negociation salariale"
            r")\b"
        ),
    ),
    (
        LEADS_TOOL,
        re.compile(
            r"\b("
            r"leads?|prospects?|prospection|prospecting|"
            r"icp|ideal customer profile|"
            r"cold (?:email|emails|outreach|call|calls)|"
            r"sequences? d outreach|outreach sequences?|"
            r"sirene|pappers|apollo|hunter io|opencorporates|"
            r"lead (?:gen|generation|enrichment|scoring|qualification)|"
            r"pipeline commercial"
            r")\b"
        ),
    ),
    (
        MARKETING_TOOL,
        re.compile(
            r"\b("
            r"campagnes? marketing|marketing campaigns?|"
            r"brand book|livre de marque|charte de marque|"
            r"calendriers? editoriaux?|editorial calendar|"
            r"creatives? publicitaires?|ad creatives?|"
            r"posts? (?:instagram|linkedin|facebook|tiktok|x)|"
            r"newsletters?|email marketing|"
            r"growth (?:loop|marketing)|"
            r"budget ads?|paid ads?|google ads|meta ads"
            r")\b"
        ),
    ),
    (
        TRADING_TOOL,
        re.compile(
            r"\b("
            r"trading|trade setups?|"
            r"portefeuilles? (?:boursier|d actions|titres)|"
            r"portfolios?|"
            r"actions? (?:cotees?|en bourse)|bourse|"
            r"tickers?|watchlists?|"
            r"backtests?|backtesting|"
            r"crypto|bitcoin|ethereum|altcoins?|"
            r"dividendes?|dividends?|"
            r"stop loss|take profit"
            r")\b"
        ),
    ),
    (
        MONTAGE_TOOL,
        re.compile(
            r"\b("
            r"montage|montages|"
            r"videos?|clips?|reels?|shorts?|"
            r"youtube|tiktok|"
            r"sous titres?|subtitles?|srt|captions?|"
            r"b roll|stock footage|footage|"
            r"voix off|voice ?over|"
            r"demo video|video demo|screencast|"
            r"content calendar|calendrier de contenu"
            r")\b"
        ),
    ),
    (
        CRM_TOOL,
        re.compile(
            r"\b("
            r"crm|"
            r"contacts?|fiches? (?:client|contact|societe|entreprise)|"
            r"opportunites?|opportunit(?:y|ies)|deals?|"
            r"pipeline (?:de ventes?|commercial)|sales pipeline|"
            r"devis|quotes?|line items?"
            r")\b"
        ),
    ),
)


def _normalize(text: str | None) -> str:
    """Casefold, strip accents and collapse separators for stable matching."""
    lowered = (text or "").casefold()
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # Keep "/" so slash commands survive; split the rest of the glue.
    cleaned = re.sub(r"[’'\-_]+", " ", stripped)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_intent_text(text: str | None) -> str:
    """The normalisation every rule-based intent reader shares (see :mod:`tool_demand`)."""
    return _normalize(text)


def desk_tools_for_text(text: str | None) -> frozenset[str]:
    """Desk tool names this turn asked for, empty when it named no desk."""
    normalized = _normalize(text)
    if not normalized:
        return frozenset()
    wanted: set[str] = set()
    for command, tools in _DESK_COMMANDS.items():
        if command in normalized:
            wanted.update(tools)
    for tool, pattern in _DESK_NOUNS:
        if pattern.search(normalized):
            wanted.add(tool)
    return frozenset(wanted)
