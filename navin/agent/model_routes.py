"""Task → model-preset routing.

Settings stores ``model_routes`` as ``{role: preset_name}``. Workflow slash
commands map onto those roles so ``/forge`` / ``/blueprint`` / studio missions
pick the configured model for the turn without a manual ``/pilot``.

The ``vision`` role is selected automatically when a turn carries image or
video attachments (multimodal analysis, typically Nemotron Nano Omni free).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Mapping

# Slash command → route role (must match Settings → Models → Task routing keys).
WORKFLOW_ROUTE_ROLES: dict[str, str] = {
    "/ask": "fast",
    "/blueprint": "plan",
    "/forge": "dev",
    "/cruise": "dev",
    "/mission": "deep",
    "/mobile": "dev",
    "/atlas": "docs",
    "/inspect": "review",
    "/fortify": "security",
    "/debug": "deep",
    "/probe": "security",
    "/unmask": "security",
    "/lineage": "security",
    "/xray": "security",
    "/gatekeeper": "security",
    "/perimeter": "security",
    "/bastion": "security",
    "/vault": "security",
    "/threatmap": "security",
    "/redteam": "security",
    "/comply": "security",
    "/recon": "security",
    "/dast": "security",
    "/pentest": "security",
    "/report": "docs",
    "/turbo": "review",
    "/pulse": "fast",
    "/board": "plan",
    "/studio": "docs",
    "/risklens": "deep",
    # A bid decision commits weeks of work and a bid bond: same tier as RiskLens.
    "/tenders": "deep",
    # Career hunts live boards and scores them. Same cheap browsing tier as
    # /scrape. A pin in the picker or /pilot <task> still wins for that turn.
    "/career": "search",
    "/marketing": "docs",
    "/campaign": "docs",
    "/montage": "docs",
    "/ads": "docs",
    "/leads": "search",
    "/meeting": "docs",
    "/ops": "dev",
    "/seo": "docs",
    "/scrape": "search",
}


# Product module without a slash / pin: same Settings → Task routing as the
# studio command. Career chats must hit `search`, not a guessed `dev` role.
PRODUCT_MODULE_ROUTE_ROLES: dict[str, str] = {
    "code": "dev",
    "career": "search",
    "scraping": "search",
    "leads": "search",
    "tenders": "deep",
    "marketing": "docs",
}


def product_module_role(module: str | None) -> str | None:
    """Route role for a WebUI product module when no slash command is present."""
    from navin.command.modules import normalize_product_module

    normalized = normalize_product_module(module)
    if normalized is None:
        return None
    return PRODUCT_MODULE_ROUTE_ROLES.get(normalized)


def workflow_role_for_content(content: str) -> str | None:
    """Return the route role for a leading slash command, if mapped."""
    head = (content or "").lstrip().split(None, 1)[0].lower() if content else ""
    if not head.startswith("/"):
        return None
    return WORKFLOW_ROUTE_ROLES.get(head)


# Composer mode → route role, for turns that carry ``composer_mode`` metadata
# without a slash prefix. The WebUI prepends /blueprint, /inspect, ... itself
# (applyComposerTurnMode); Telegram / CLI / API clients send the mode alone,
# and used to silently fall through to the dev/default route. Kept aligned
# with the slash mapping above: /blueprint→plan, /inspect→review,
# /fortify→security, /debug→deep.
COMPOSER_MODE_ROUTE_ROLES: dict[str, str] = {
    "plan": "plan",
    "review": "review",
    "security": "security",
    "debug": "deep",
}


def composer_mode_role(mode: str | None) -> str | None:
    """Route role for a bare composer mode (plan/review/security/debug)."""
    if not isinstance(mode, str):
        return None
    return COMPOSER_MODE_ROUTE_ROLES.get(mode.strip().lower())


def load_model_routes() -> dict[str, str]:
    """Current role → preset map; empty dict when config is unavailable."""
    try:
        from navin.config.loader import load_config

        return dict(load_config().model_routes)
    except Exception:
        return {}


def resolve_model_route(
    role: str | None,
    *,
    routes: Mapping[str, str] | None = None,
    known_presets: set[str] | None = None,
) -> str | None:
    """Preset name for *role*, or None when unset / unknown.

    ``default`` is always accepted (agents.defaults). Other names must exist in
    ``known_presets`` when that set is provided.
    """
    if not role:
        return None
    mapping = dict(routes) if routes is not None else load_model_routes()
    preset = mapping.get(role)
    if not isinstance(preset, str):
        return None
    preset = preset.strip()
    if not preset:
        return None
    if preset == "default":
        return "default"
    if known_presets is not None and preset not in known_presets:
        return None
    return preset


def resolve_route_for_message(
    content: str,
    *,
    routes: Mapping[str, str] | None = None,
    known_presets: set[str] | None = None,
) -> str | None:
    """Preset implied by a workflow slash command in *content*, if any."""
    role = workflow_role_for_content(content)
    if role is None:
        return None
    return resolve_model_route(role, routes=routes, known_presets=known_presets)


_DEEP_RE = re.compile(
    r"\b("
    r"architect(?:ure|e|ural)?|refactor(?:ing)?|refacto|"
    r"migrat(?:e|ion|ions)|debug(?:ging)?|debogue|"
    r"redesign|rewrite|tricky|complex|large change|"
    r"gros refactor|architecture"
    r")\b",
    re.IGNORECASE,
)
_SECURITY_RE = re.compile(
    r"\b("
    r"securit(?:y|e)|secret|authz|permission|vulnerab|"
    r"xss|sqli|cve|owasp|audit"
    r")\b",
    re.IGNORECASE,
)
_REVIEW_RE = re.compile(
    r"\b("
    r"review|revue|relecture|code review|spot (?:bugs|regressions)|"
    r"relis|revoir le diff"
    r")\b",
    re.IGNORECASE,
)
_PLAN_RE = re.compile(
    r"\b("
    r"plan(?:ning)?|spec(?:s)?|blueprint|roadmap|"
    r"break(?:ing)? (?:this|it|the work) into|decoup"
    r")\b",
    re.IGNORECASE,
)
_SEARCH_RE = re.compile(
    r"\b("
    r"search the web|look(?:s)? up|google|recherche web|"
    r"compare online|docs en ligne"
    r")\b",
    re.IGNORECASE,
)
_DOCS_RE = re.compile(
    r"\b("
    r"readme|docstring|document(?:e|er|ation)|"
    r"write docs|redige"
    r")\b",
    re.IGNORECASE,
)
_FAST_RE = re.compile(
    r"\b("
    r"rename|renomm|commit message|quick question|"
    r"c.?est quoi|what is|what does"
    r")\b",
    re.IGNORECASE,
)
_DEV_RE = re.compile(
    r"\b("
    r"function|fonction|component|hook|bug|test|tests|"
    r"implement|code|fix|patch|api|endpoint"
    r")\b",
    re.IGNORECASE,
)


def infer_task_role(text: str | None) -> str | None:
    """Best task-routing role for a plain chat line (no slash / pin)."""
    body = (text or "").strip()
    if not body:
        return None
    if body.startswith("/"):
        return workflow_role_for_content(body)
    if _SECURITY_RE.search(body):
        return "security"
    if _DEEP_RE.search(body):
        return "deep"
    if _REVIEW_RE.search(body):
        return "review"
    if _PLAN_RE.search(body):
        return "plan"
    if _SEARCH_RE.search(body):
        return "search"
    if _DOCS_RE.search(body):
        return "docs"
    if _FAST_RE.search(body) or (len(body) < 72 and body.endswith("?")):
        return "fast"
    if _DEV_RE.search(body):
        return "dev"
    return "fast" if len(body) < 48 else "dev"


_VISION_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".mp4",
        ".webm",
        ".mov",
        ".mkv",
        ".avi",
        ".m4v",
    }
)


def media_needs_vision(media: Iterable[str] | None) -> bool:
    """True when *media* includes an image or video path for analysis."""
    if not media:
        return False
    for item in media:
        if not isinstance(item, str) or not item.strip():
            continue
        # Strip query fragments from data URLs / signed URLs when present.
        path = item.split("?", 1)[0].split("#", 1)[0]
        ext = Path(path).suffix.lower()
        if ext in _VISION_EXTENSIONS:
            return True
        lowered = path.lower()
        if lowered.startswith("data:image/") or lowered.startswith("data:video/"):
            return True
    return False


def text_needs_vision(text: str | None) -> bool:
    """True when *text* mentions an image or video link we will download.

    A message that is only a pasted YouTube link carries no media yet, so
    routing on attachments alone would send it to a text-only model and then
    hand that model a set of frames it cannot read.
    """
    if not text or "http" not in text:
        return False
    from navin.utils.media_urls import find_media_links

    return bool(find_media_links(text))


def resolve_vision_route(
    media: Iterable[str] | None,
    *,
    text: str | None = None,
    routes: Mapping[str, str] | None = None,
    known_presets: set[str] | None = None,
) -> str | None:
    """Preset for multimodal analysis when the turn carries image/video media."""
    if not media_needs_vision(media) and not text_needs_vision(text):
        return None
    return resolve_model_route("vision", routes=routes, known_presets=known_presets)


# Turns that ask the agent to drive the desktop (mouse / keyboard / native
# apps). French and English, matched on the user's own words: the tool being
# enabled is not enough, a "resume this PDF" turn must not pay for a grounding
# model. Kept deliberately narrow; browsing has its own tool and route.
_COMPUTER_RE = re.compile(
    r"(?ix)"
    r"\b(?:"
    r"computer[ -]use|desktop\s+control|"
    r"(?:on|from)\s+my\s+(?:screen|desktop|computer|pc|mac)|"
    r"(?:take|grab)\s+(?:control|over)\s+(?:of\s+)?my|"
    r"(?:click|double[ -]click|right[ -]click)\s+(?:on\s+)?(?:the|that|this)\b|"
    r"open\s+(?:the\s+)?(?:app(?:lication)?|program|software)\b|"
    r"in\s+(?:excel|word|outlook|powerpoint|photoshop|illustrator|blender|"
    r"autocad|solidworks|premiere|figma|notepad|finder|explorer|teams|slack|"
    r"the\s+file\s+explorer|the\s+finder|system\s+settings|the\s+control\s+panel)\b|"
    r"sur\s+mon\s+(?:écran|ecran|bureau|ordinateur|ordi|pc|mac)\b|"
    r"prends?\s+(?:le\s+)?contr[ôo]le\s+(?:de\s+)?(?:mon|ma|l')|"
    r"contr[ôo]le\s+(?:mon|l')\s*(?:ordinateur|ordi|pc|écran|ecran)|"
    r"(?:clique|double[ -]clique|clic\s+droit)\s+(?:sur|dans)\b|"
    r"ouvre\s+(?:l'|le\s+|la\s+)?(?:appli(?:cation)?|logiciel|programme)\b|"
    r"dans\s+(?:excel|word|outlook|powerpoint|photoshop|illustrator|blender|"
    r"autocad|solidworks|premiere|figma|le\s+bloc-notes|l'explorateur|"
    r"les\s+param[èe]tres\s+(?:syst[èe]me|windows)|le\s+panneau\s+de\s+configuration)\b|"
    r"pilote\s+(?:mon|l')\s*(?:ordinateur|ordi|pc|bureau)|"
    r"utilise\s+(?:ma\s+souris|mon\s+clavier|la\s+souris|le\s+clavier)|"
    r"use\s+(?:my|the)\s+(?:mouse|keyboard)"
    r")"
)


def text_needs_computer(text: str | None) -> bool:
    """True when the user asks for desktop control in plain words."""
    body = (text or "").strip()
    if not body or len(body) > 4000:
        return False
    return bool(_COMPUTER_RE.search(body))


def resolve_computer_route(
    text: str | None,
    *,
    routes: Mapping[str, str] | None = None,
    known_presets: set[str] | None = None,
    active_session: bool = False,
) -> str | None:
    """Preset for GUI grounding when the turn asks to drive the desktop.

    Only the ``computer`` role is consulted: the ``vision`` preset is tuned for
    describing images and is often a small model that cannot place a click.
    """
    if not active_session and not text_needs_computer(text):
        return None
    return resolve_model_route("computer", routes=routes, known_presets=known_presets)
