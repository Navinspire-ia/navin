"""Product-module scoping for slash commands and skills.

The WebUI shell has multiple modules (Tenders, Career, Trading, RiskLens, Code, Scraping, Documents, Marketing,
Ads, SEO, Leads, Meeting, Ops). Each turn can carry ``product_module`` in inbound
metadata so the agent only sees the skills (and the palette only lists the
commands) that belong to that module.

Skills are never deleted - they are disabled for the active module. Shared /
core skills stay available everywhere.
"""

from __future__ import annotations

from typing import Iterable, Literal

# Inbound / outbound metadata key (WebUI → gateway → AgentLoop).
PRODUCT_MODULE_METADATA_KEY = "product_module"

# Set by workflow briefs (/studio, /campaign, …) so the agent loop can preload
# SKILL.md bodies and nudge when a delivery turn finishes with zero tools.
PRELOAD_SKILLS_METADATA_KEY = "preload_skills"
REQUIRES_TOOL_DELIVERY_METADATA_KEY = "requires_tool_delivery"
# Build/Code turns (/forge, /cruise): nudge once if the model claims done
# without running verify / lint / test_run.
REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY = "requires_verify_before_done"
# How many times a red verify may be nudged back into a fix before the turn is
# allowed to end. Build workflows raise it (hard bugs need several cycles);
# absent means the runner default applies.
VERIFY_FAIL_NUDGE_LIMIT_METADATA_KEY = "verify_fail_nudge_limit"
# /forge, /cruise, /debug: build turns own the green bar, so they get more
# repair cycles than a plain chat turn. Shipping red is the expensive
# outcome, not the extra cycle.
CODE_VERIFY_FAIL_NUDGE_LIMIT = 3
# Ask (and similar) turns: runner refuses any tool that is not read_only.
READ_ONLY_TOOLS_METADATA_KEY = "read_only_tools"
# Legacy metadata key (prompt preference). Runner no longer hard-blocks
# write_file/edit_file; Agent modes may create and edit freely except Ask.
APPLY_PATCH_ONLY_METADATA_KEY = "apply_patch_only"
# Review / Security / Debug: inject evidence-only system rules; no invented findings.
EVIDENCE_ONLY_METADATA_KEY = "evidence_only"
EVIDENCE_ONLY_COMPOSER_MODES: frozenset[str] = frozenset(
    {"review", "security", "debug"}
)
# When set, Active Skills list names only. The model loads one SKILL.md per
# phase with `skill action=read`. Cuts 10-40k tokens/turn.
SLIM_SKILL_PRELOAD_METADATA_KEY = "slim_skill_preload"
# /forge /cruise: only these tool names reach the model (allowlist, not deny).
ALLOWED_TOOLS_METADATA_KEY = "allowed_tools"


def metadata_requests_evidence_only(metadata: object | None) -> bool:
    """True when turn metadata arms Review/Security/Debug ground-truth mode."""
    if not isinstance(metadata, dict):
        return False
    if metadata.get(EVIDENCE_ONLY_METADATA_KEY):
        return True
    mode = str(metadata.get("composer_mode") or "").strip().lower()
    return mode in EVIDENCE_ONLY_COMPOSER_MODES

# Tools that must never run in the Code product module (scrape/SEO surface).
CODE_DENIED_TOOLS: frozenset[str] = frozenset({"scrape"})

# Silent heartbeat must not sweep portals, open a browser, or schedule a
# collect+follow loop. exec stays: the code-board continuity task needs it.
# tenders / career / leads / crm stay: follow, watch, followups (each tool gates writes).
HEARTBEAT_DENIED_TOOLS: frozenset[str] = frozenset(
    {"scrape", "browser", "cron", "web_search", "trading"}
)

# Desk tools owned by one studio. Hidden in every other product module so the
# Career agent calls `career`, not `tenders`, `trading` or `leads`.
STUDIO_OWNED_TOOLS: dict[str, str] = {
    "tenders": "tenders",
    "career": "career",
    "trading": "trading",
    "leads": "leads",
    "marketing": "marketing",
}

# Desks a chat opened by naming them, kept for the rest of the session so a
# follow-up "continue" does not lose the desk the conversation is about.
# Outside a product module these schemas are otherwise off (~9k tokens).
ACTIVE_DESKS_METADATA_KEY = "active_desk_tools"

ProductModule = Literal[
    "tenders",
    "career",
    "trading",
    "risklens",
    "code",
    "scraping",
    "content",
    "marketing",
    "ads",
    "seo",
    "leads",
    "meeting",
    "ops",
    "notes",
    "crm",
]

VALID_PRODUCT_MODULES: frozenset[str] = frozenset(
    {
        "tenders",
        "career",
        "trading",
        "risklens",
        "code",
        "scraping",
        "content",
        "marketing",
        "ads",
        "seo",
        "leads",
        "meeting",
        "ops",
        "notes",
        "crm",
    }
)

# Studio workflow commands owned by a single module.
# Code keeps /studio (documents) and /ops (infra); it hides RiskLens and the
# commercial studios so the coding loop stays focused.
_MODULE_OWNED_COMMANDS: dict[str, frozenset[str]] = {
    "tenders": frozenset({"/tenders"}),
    "career": frozenset({"/career"}),
    "trading": frozenset({"/trading"}),
    "risklens": frozenset({"/risklens"}),
    "scraping": frozenset({"/scrape"}),
    "content": frozenset({"/studio"}),
    "marketing": frozenset({"/campaign", "/montage", "/marketing"}),
    "ads": frozenset({"/ads"}),
    "seo": frozenset({"/seo"}),
    "leads": frozenset({"/leads"}),
    "crm": frozenset({"/crm"}),
    "meeting": frozenset({"/meeting"}),
    "ops": frozenset({"/ops"}),
}

# Commands hidden from the Code module palette and rejected if typed there.
CODE_HIDDEN_COMMANDS: frozenset[str] = frozenset(
    {
        "/tenders",
        "/career",
        "/trading",
        "/risklens",
        "/campaign",
        "/marketing",
        "/montage",
        "/ads",
        "/seo",
        "/leads",
        "/crm",
        "/meeting",
        "/scrape",
    }
)

# Frontend ShellView "dev" maps to product module "code".
_VIEW_TO_MODULE: dict[str, str] = {
    "dev": "code",
    "code": "code",
    "tenders": "tenders",
    "career": "career",
    "trading": "trading",
    "risklens": "risklens",
    "premortem": "risklens",  # legacy alias
    "scraping": "scraping",
    "scrape": "scraping",
    "content": "content",
    "marketing": "marketing",
    # WebUI view `#/montage` sends product_module=montage; it is a Marketing
    # sub-desk (owns /campaign + /montage), not an unscoped module.
    "montage": "marketing",
    "ads": "ads",
    "seo": "seo",
    "leads": "leads",
    "meeting": "meeting",
    "ops": "ops",
    "notes": "notes",
    "crm": "crm",
}


def normalize_product_module(value: object | None) -> str | None:
    """Return a valid module id, or None when absent/unknown (no scoping)."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    mapped = _VIEW_TO_MODULE.get(cleaned, cleaned)
    if mapped in VALID_PRODUCT_MODULES:
        return mapped
    return None


# Skills always injected while the user is in that product module, even
# without a slash workflow. Scraping must see Scrapling the way Code sees
# the Dev stack and Leads sees prospecting skills.
_MODULE_DEFAULT_PRELOAD_SKILLS: dict[str, tuple[str, ...]] = {
    "scraping": ("scrapling", "scrape-operator"),
    "leads": ("lead-prospector", "lead-enrichment", "data-quality-agent"),
    "tenders": (
        # Keep in lockstep with navin.tenders.stack.TENDERS_SKILLS.
        "tender-agent",
        "rfp-writer",
        "tender-monitor",
        "proposal-writer",
        "sales-proposal-writer",
        "contract-reviewer",
        "critic-reviewer",
        "web-extractor",
        "scrape-operator",
        "scrapling",
        "archify",
    ),
    "content": ("archify",),
    "career": (
        "career-agent",
        "job-search-agent",
        "cv-builder",
        "cv-tailoring",
        "cover-letter-writer",
        "ats-analyzer",
        "application-tracker",
        "followup-writer",
        "interview-coach",
        "offer-analyzer",
        "salary-negotiator",
        "freelance-rate-card",
        "career-advisor",
        "linkedin-optimizer",
        "scrape-operator",
        "scrapling",
        "web-extractor",
    ),
    "trading": (
        "trading-agent",
        "momentum-trader",
        "value-investor",
        "crypto-swing",
        "dividend-portfolio",
        "earnings-trader",
        "macro-investor",
        "nft-collector",
        "real-estate-investor",
        "global-equities",
    ),
    "marketing": (
        "marketing-strategist",
        "growth-marketing",
        "digital-marketing",
        "email-marketing",
        "marketing-analytics",
    ),
    "montage": ("montage-studio",),
    "meeting": ("meeting-studio",),
}


def extra_denied_tools_for_module(module: str | None) -> frozenset[str]:
    """Studio desk tools that do not belong to this product module.

    No module (Telegram / CLI) keeps every desk tool so `/career` still works
    outside the WebUI. Career keeps `scrape` and `web_search`.
    """
    normalized = normalize_product_module(module)
    owned = set(STUDIO_OWNED_TOOLS.values())
    if normalized is None:
        return frozenset()
    keep = STUDIO_OWNED_TOOLS.get(normalized)
    if keep:
        return frozenset(owned - {keep})
    return frozenset(owned)


def default_preload_skills_for_module(module: str | None) -> list[str]:
    """Skills to preload from the active product module (may be empty)."""
    # Montage shares the Marketing tool scope, but has its own workflow.
    if isinstance(module, str) and module.strip().lower() == "montage":
        return list(_MODULE_DEFAULT_PRELOAD_SKILLS["montage"])
    normalized = normalize_product_module(module)
    if normalized is None:
        return []
    return list(_MODULE_DEFAULT_PRELOAD_SKILLS.get(normalized, ()))


def extra_preload_skills_for_module(module: str | None) -> list[str]:
    """Report / UI skills a desk chat needs even without a slash workflow."""
    if normalize_product_module(module) != "tenders":
        return []
    from navin.tenders.stack import TENDERS_REPORT_SKILLS

    return list(TENDERS_REPORT_SKILLS)


def kept_tools_for_module(module: str | None) -> frozenset[str]:
    """Companion tools a product module must never lose to a denylist."""
    if normalize_product_module(module) != "tenders":
        return frozenset()
    from navin.tenders.stack import TENDERS_TOOLS

    return frozenset(TENDERS_TOOLS)


def _skill_names_csv(skills: str) -> set[str]:
    return {name.strip() for name in skills.split(",") if name.strip()}


def _studio_brief_skills() -> dict[str, set[str]]:
    """Skills used by each studio's workflow brief and action prompts."""
    # Imported lazily to avoid circular import at module load (builtin imports us).
    from navin.command.builtin import _WORKFLOW_BRIEFS

    command_to_module = {
        command: module
        for module, commands in _MODULE_OWNED_COMMANDS.items()
        for command in commands
    }
    out: dict[str, set[str]] = {module: set() for module in _MODULE_OWNED_COMMANDS}
    for command, (_title, skills_csv, _brief) in _WORKFLOW_BRIEFS.items():
        module = command_to_module.get(command)
        if module is None:
            continue
        out[module].update(_skill_names_csv(skills_csv))
    from navin.agent.skill_routing import module_skill_names

    for module, names in out.items():
        # A specialist shared by real actions must remain loadable in both
        # desks, even when an older slash brief only names it in one desk.
        names.update(module_skill_names(module))
    return out


def _code_side_brief_skills() -> set[str]:
    """Skills named by the workflow briefs that are not owned by a studio.

    ``/forge``, ``/cruise``, ``/blueprint``, ``/report``... are the Code
    workbench's own workflows; whatever they ask for must stay loadable there.
    """
    from navin.command.builtin import _WORKFLOW_BRIEFS

    studio_commands = set().union(*_MODULE_OWNED_COMMANDS.values())
    out: set[str] = set()
    for command, (_title, skills_csv, _brief) in _WORKFLOW_BRIEFS.items():
        if command not in studio_commands:
            out.update(_skill_names_csv(skills_csv))
    return out


def exclusive_studio_skills() -> dict[str, set[str]]:
    """Skills that appear in exactly one studio module brief and nowhere else.

    Skills shared across briefs (e.g. ``image-generation``) stay available in
    every module so Documents/SEO/Marketing can all illustrate work. A skill a
    Code-side workflow also names (``ui-ux-pro-max`` in ``/forge`` and
    ``/marketing``, ``task-planner`` in ``/blueprint`` and ``/ops``) is shared
    as well: hiding it from Code would make ``/forge`` suggest a playbook that
    ``skill action=read`` then refuses to open.
    """
    by_module = _studio_brief_skills()
    shared_with_code = _code_side_brief_skills()
    counts: dict[str, int] = {}
    for skills in by_module.values():
        for name in skills:
            counts[name] = counts.get(name, 0) + 1
    return {
        module: {
            name
            for name in skills
            if counts.get(name, 0) == 1 and name not in shared_with_code
        }
        for module, skills in by_module.items()
    }


def allowed_commands_for_module(module: str | None) -> frozenset[str] | None:
    """Return None when every command is allowed (unknown/absent module).

    Otherwise return the set of *extra* studio commands that remain allowed on
    top of all non-studio commands. Callers should hide any studio-owned
    command not in this set.
    """
    normalized = normalize_product_module(module)
    if normalized is None:
        return None
    if normalized == "code":
        # Documents + Ops stay reachable from Code; commercial studios do not.
        return frozenset({"/studio", "/ops"})
    owned = _MODULE_OWNED_COMMANDS.get(normalized, frozenset())
    # Studio modules also keep /studio for document export when useful, except
    # we keep ownership strict: only the module's own command (+ shared utils).
    if normalized == "content":
        return frozenset({"/studio"})
    if normalized == "scraping":
        # Scraping keeps /studio so corpora can be turned into decks/reports.
        return frozenset({"/scrape", "/studio"})
    if normalized == "leads":
        # Leads may chain /scrape for public corpora → prospects CSV.
        return frozenset({"/leads", "/scrape"})
    if normalized == "tenders":
        # Tenders Collect already runs scrape + web_search; /scrape stays
        # available for a public listing the desk did not ingest.
        return frozenset({"/tenders", "/scrape"})
    if normalized == "career":
        # Career Collect already runs scrape + web_search on open hosts.
        # /scrape stays available for a public listing Search missed.
        return frozenset({"/career", "/scrape"})
    return owned


def is_command_allowed_for_module(command: str, module: str | None) -> bool:
    """Whether *command* may appear / run under *module*."""
    allowed_studio = allowed_commands_for_module(module)
    if allowed_studio is None:
        return True
    all_studio = set().union(*_MODULE_OWNED_COMMANDS.values())
    if command not in all_studio:
        return True
    return command in allowed_studio


def disabled_skills_for_module(
    module: str | None,
    *,
    config_disabled: Iterable[str] | None = None,
) -> set[str]:
    """Skills to hide for the active module (union config + other-studio exclusives)."""
    disabled = set(config_disabled or ())
    normalized = normalize_product_module(module)
    if normalized is None:
        return disabled

    exclusive = exclusive_studio_skills()
    if normalized == "code":
        # Code + Documents (+ Ops skills via shared/non-exclusive). Hide RiskLens
        # and the commercial studio exclusives (including Scraping).
        for other in (
            "tenders",
            "career",
            "trading",
            "risklens",
            "scraping",
            "marketing",
            "ads",
            "seo",
            "leads",
            "meeting",
        ):
            disabled.update(exclusive.get(other, ()))
        return disabled

    # Sub-module: keep that module's exclusives; hide other studio exclusives.
    for other, skills in exclusive.items():
        if other == normalized:
            continue
        # Documents skills stay available from every studio (export decks/PDFs).
        if other == "content":
            continue
        disabled.update(skills)
    return disabled


def module_mismatch_message(command: str, module: str) -> str:
    """User-facing hint when a studio command is used in the wrong module."""
    owners = [
        name
        for name, commands in _MODULE_OWNED_COMMANDS.items()
        if command in commands
    ]
    owner = owners[0] if owners else "the matching"
    labels = {
        "tenders": "Tenders",
        "career": "Career",
        "trading": "Trading",
        "risklens": "RiskLens",
        "scraping": "Scraping",
        "content": "Documents",
        "marketing": "Marketing",
        "ads": "Ads",
        "seo": "SEO",
        "leads": "Leads",
        "meeting": "Meeting",
        "ops": "Ops",
        "code": "Code",
        "notes": "Notes",
        "crm": "CRM",
    }
    return (
        f"{command} is not available in the {labels.get(module, module)} module. "
        f"Open the {labels.get(owner, owner)} module in the sidebar, then retry."
    )
