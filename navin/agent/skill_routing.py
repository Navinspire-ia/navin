"""Load the actual specialist playbooks for one desk action.

The agent and the tool-less desk model calls share these routes. Selection is
small and deterministic; selected SKILL.md bodies are never shortened. Normal
SkillsLoader precedence, dependency checks and disabled settings still apply.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.agent.skills import SkillsLoader

SKILL_ACTION_METADATA_KEY = "skill_action"
SKILL_CONTEXT_METADATA_KEY = "skill_context"

_BASE_SKILLS: dict[str, tuple[str, ...]] = {
    "tenders": ("tender-agent",),
    "career": ("career-agent",),
    "montage": ("montage-studio",),
    "marketing": ("studio-expert-contract", "marketing-strategist"),
    "meeting": ("meeting-studio",),
}

_TENDER_WRITE = (
    "rfp-writer", "proposal-writer", "compliance-mapper", "professional-writer",
    "docx-generator", "archify", "critic-reviewer",
)
_CAREER_PREPARE = (
    "cv-builder", "cv-tailoring", "ats-analyzer", "cover-letter-writer",
    "professional-writer", "docx-generator", "proofreader", "critic-reviewer",
)
_ACTION_SKILLS: dict[str, dict[str, tuple[str, ...]]] = {
    "tenders": {
        "collect": ("tender-monitor", "web-extractor"),
        "profile": ("proposal-writer", "professional-writer"),
        "qualify": ("rfp-writer", "contract-reviewer", "compliance-mapper", "critic-reviewer"),
        "write": _TENDER_WRITE,
        "revise": (*_TENDER_WRITE, "proofreader"),
        "mail": ("email-writer", "followup-writer"),
        "export": ("docx-generator", "pptx-generator", "archify", "proofreader"),
        "follow": ("tender-monitor", "followup-writer"),
    },
    "career": {
        "search": ("job-search-agent", "web-extractor"),
        "profile": ("career-advisor", "cv-builder"),
        "match": ("job-search-agent", "ats-analyzer", "offer-analyzer"),
        "analyze-ats": ("ats-analyzer", "cv-tailoring"),
        "prepare": _CAREER_PREPARE,
        "cover": ("cover-letter-writer", "professional-writer", "proofreader"),
        "followup": ("application-tracker", "followup-writer"),
        "apply": ("application-tracker",),
        "mail-draft": ("cv-tailoring", "cover-letter-writer", "email-writer", "application-tracker", "proofreader"),
        "send-email": ("cv-tailoring", "cover-letter-writer", "email-writer", "application-tracker", "proofreader"),
        "sync-mail": ("application-tracker", "followup-writer", "fact-checker"),
        "mail-config": (),
        "mail-test": (),
        "interview": ("interview-coach", "career-advisor"),
        "offer": ("offer-analyzer", "salary-negotiator", "freelance-rate-card"),
        "export": ("cv-builder", "docx-generator", "proofreader"),
    },
    "montage": {
        "analyze": ("product-visuals",),
        "calendar": ("content-generation",),
        "image": ("image-generation", "product-visuals"),
        "video": ("video-generation", "product-visuals"),
        "assemble": ("critic-reviewer",),
        "localize": ("translation-localization",),
        "report": ("archify", "studio-html-report", "critic-reviewer"),
    },
    "marketing": {
        "understand": ("customer-persona-builder", "market-research"),
        "positioning": ("brand-voice-manager", "customer-persona-builder"),
        "research": ("market-research", "competitor-intelligence"),
        "plan": ("campaign-manager", "go-to-market-planner"),
        "copy": ("copywriting-agent", "brand-voice-manager", "social-media-manager"),
        "channel-set": (
            "copywriting-agent", "brand-voice-manager", "social-media-manager",
            "email-marketing", "email-writer", "blog-writer", "seo-content-writer",
        ),
        "email": ("email-marketing", "email-writer", "brand-voice-manager"),
        "blog": ("blog-writer", "seo-content-writer", "brand-voice-manager"),
        "variants": ("growth-marketing", "copywriting-agent"),
        "produce": ("ad-creative-generator", "product-visuals", "critic-reviewer"),
        "visual-qa": ("product-visuals", "critic-reviewer", "fact-checker"),
        "publish": ("social-media-manager", "campaign-manager"),
        "measure": ("marketing-analytics", "kpi-reporter"),
        "launch": ("go-to-market-planner", "copywriting-agent", "professional-writer"),
        "report": ("archify", "studio-html-report", "critic-reviewer"),
    },
    "meeting": {
        "report": ("meeting-followup", "professional-writer", "proofreader", "critic-reviewer"),
        "speakers": ("fact-checker",),
        "answer": ("fact-checker",),
        "translate": ("translation-localization", "fact-checker"),
        "cleanup": ("proofreader", "fact-checker"),
        "followup": ("meeting-followup", "email-writer", "crm-update-agent"),
        "export": ("professional-writer", "docx-generator", "proofreader"),
    },
}

_ACTION_ALIASES: dict[str, dict[str, str]] = {
    "tenders": {
        "search": "collect", "list": "status", "snapshot": "status",
        "score": "qualify", "analyse": "qualify", "analyze": "qualify",
        "gonogo": "qualify", "go-nogo": "qualify", "rescore": "qualify",
        "draft": "write", "review": "revise", "remark": "revise", "remarks": "revise",
        "download": "export", "pack": "export", "followup": "mail", "send": "mail",
        "follow-up": "follow", "watch": "follow", "knowledge": "profile",
    },
    "career": {
        "collect": "search", "find": "search", "mission": "search",
        "qualify": "match", "rescore": "match", "ats": "analyze-ats",
        "tailor": "prepare", "write": "prepare", "cv": "prepare", "tailoring": "prepare",
        "cover-letter": "cover", "coverletter": "cover", "download": "export",
        "salary": "offer", "negotiate": "offer", "inbox": "followup", "watch": "followup",
    },
    "montage": {
        "analyse": "analyze", "generate-image": "image", "generate-video": "video",
        "timeline": "assemble", "timeline-save": "assemble", "timeline-render": "assemble",
        "render": "assemble", "package": "assemble", "edit": "assemble",
        "transcribe": "localize", "translate": "localize", "voicetrack": "localize",
        "dub": "localize", "lipsync": "localize", "subtitles": "localize",
    },
    "marketing": {
        "product": "understand", "brand": "positioning", "position": "positioning",
        "campaign": "plan", "pipeline": "plan", "content": "copy", "posts": "copy",
        "social": "copy", "calendar": "copy", "creative": "produce", "generate": "produce",
        "post": "publish", "metrics": "measure", "analytics": "measure", "sync-metrics": "measure",
        "improve": "variants", "optimize": "variants",
        "vision": "visual-qa", "qa": "visual-qa",
    },
    "meeting": {
        "minutes": "report", "summary": "report", "summarize": "report", "synthesis": "report",
        "diarize": "speakers", "diarization": "speakers", "chat": "answer", "qa": "answer",
        "follow-up": "followup", "mail": "followup", "download": "export", "docx": "export",
        "translation": "translate", "clean": "cleanup", "correct": "cleanup",
    },
}


def normalize_skill_module(value: object, *, composer_mode: object = None) -> str | None:
    """Preserve Montage's playbook identity inside its legacy Marketing scope."""
    raw = str(value or "").strip().lower()
    if raw in {"", "marketing", "montage"} and str(composer_mode or "").strip().lower() == "montage":
        return "montage"
    raw = {"tender": "tenders", "carriere": "career", "carrière": "career", "meetings": "meeting"}.get(raw, raw)
    return raw if raw in _BASE_SKILLS else None


def normalize_skill_action(module: str, action: object) -> str:
    raw = str(action or "status").strip().lower().replace("_", "-") or "status"
    return _ACTION_ALIASES.get(module, {}).get(raw, raw)


def action_skill_names(module: str, action: str) -> tuple[str, ...]:
    module = normalize_skill_module(module) or ""
    action = normalize_skill_action(module, action)
    return tuple(dict.fromkeys((*_BASE_SKILLS.get(module, ()), *_ACTION_SKILLS.get(module, {}).get(action, ()))))


def module_skill_names(module: str) -> set[str]:
    """All skills this desk may need, for the existing module visibility gate."""
    normalized = normalize_skill_module(module) or ""
    names = set(_BASE_SKILLS.get(normalized, ()))
    for group in _ACTION_SKILLS.get(normalized, {}).values():
        names.update(group)
    if normalized == "marketing":
        names.update(module_skill_names("montage"))
    return names


@dataclass(frozen=True)
class ActionSkillContext:
    module: str
    action: str
    workspace: Path
    requested: tuple[str, ...]
    loaded: tuple[str, ...]
    unavailable: tuple[dict[str, str], ...]
    sources: tuple[dict[str, str], ...]
    prompt: str

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "action": self.action,
            "workspace": self.workspace.as_posix(),
            "requested": list(self.requested),
            "loaded": list(self.loaded),
            "unavailable": [dict(row) for row in self.unavailable],
            "sources": [dict(row) for row in self.sources],
        }

    def augment_system(self, system: str) -> str:
        if not self.prompt:
            return system
        return (
            self.prompt
            + "\n\n# Current action contract\n\n"
            + "Apply the playbooks to this step of the workflow. The response format and factual "
            "constraints below govern this call. Use tools only when exposed by this runtime; "
            "never claim a file, verification, publication or other workflow step was completed "
            "without its execution evidence.\n\n"
            + system
        )


def _names(value: Any) -> set[str]:
    if isinstance(value, str):
        return {part.strip() for part in value.split(",") if part.strip()}
    if isinstance(value, Iterable):
        return {name.strip() for name in value if isinstance(name, str) and name.strip()}
    return set()


def build_action_skill_context(
    module: str,
    action: str,
    *,
    workspace: Path | str | None = None,
    disabled_skills: Iterable[str] | None = None,
    loader: SkillsLoader | None = None,
) -> ActionSkillContext:
    """Load full available bodies within the active request's project.

    An existing loader lets ContextBuilder share its project discovery and
    configured exclusions. Direct desk calls resolve the active config and
    request context themselves, including across ``asyncio.to_thread``.
    """
    from navin.agent.tools.context import current_request_context
    from navin.command.modules import disabled_skills_for_module

    request = current_request_context()
    normalized = normalize_skill_module(module) or ""
    metadata = request.metadata if request and isinstance(request.metadata, dict) else {}
    disabled = _names(disabled_skills) | _names(metadata.get("disabled_skills"))
    disabled.update(_names(metadata.get("extra_disabled_skills")))
    if loader is None:
        from navin.config.loader import load_config

        config = load_config()
        defaults = getattr(getattr(config, "agents", None), "defaults", None)
        disabled.update(_names(getattr(defaults, "disabled_skills", ())))
        selected_root = (request.workspace if request else None) or workspace or getattr(config, "workspace_path", None)
        root = Path(selected_root or Path.cwd()).expanduser().resolve(strict=False)
        disabled = disabled_skills_for_module(normalized, config_disabled=disabled)
        loader = SkillsLoader(root, disabled_skills=disabled)
    else:
        root = Path(workspace or loader.workspace).expanduser().resolve(strict=False)
        disabled.update(loader.disabled_skills)
        disabled = disabled_skills_for_module(normalized, config_disabled=disabled)
        if root != loader.workspace.expanduser().resolve(strict=False):
            loader = SkillsLoader(root, builtin_skills_dir=loader.builtin_skills, disabled_skills=disabled)
    action = normalize_skill_action(normalized, action)
    requested = action_skill_names(normalized, action)
    entries = {entry["name"]: entry for entry in loader.list_skills(filter_unavailable=False)} if requested else {}
    loaded: list[str] = []
    unavailable: list[dict[str, str]] = []
    sources: list[dict[str, str]] = []
    bodies: list[str] = []
    for name in requested:
        if name in disabled:
            unavailable.append({"name": name, "status": "disabled", "reason": "disabled for this request"})
            continue
        entry = entries.get(name)
        if entry is None:
            unavailable.append({"name": name, "status": "missing", "reason": "skill is not installed"})
            continue
        available, reason = loader.get_skill_availability(name)
        if not available:
            unavailable.append({"name": name, "status": "unavailable", "reason": reason or "requirements not met"})
            continue
        body = loader.load_skills_for_context([name], slim=False)
        if not body.strip():
            unavailable.append({"name": name, "status": "unreadable", "reason": "skill body could not be loaded"})
            continue
        loaded.append(name)
        sources.append({"name": name, "path": entry["path"], "source": entry["source"]})
        bodies.append(body)
    parts = []
    if requested:
        parts.append(f"# Specialist skills for {normalized} / {action}")
    if bodies:
        parts.append("The following locally loaded playbooks apply to this action. Follow their complete workflows and factual safeguards.")
        parts.extend(bodies)
    if unavailable:
        parts.append(
            "Requested skills that were NOT loaded (do not claim they were applied):\n"
            + "\n".join(f"- {item['name']}: {item['reason']}" for item in unavailable)
        )
    return ActionSkillContext(
        normalized, action, root, requested, tuple(loaded), tuple(unavailable),
        tuple(sources), "\n\n".join(parts),
    )


_ACTION_INTENT: dict[str, tuple[tuple[str, str], ...]] = {
    "tenders": (
        ("revise", r"\b(revis\w*|corrig\w*|review|remark\w*)\b"),
        ("mail", r"\b(mail|email|relance\w*|follow.?up)\b"),
        ("write", r"\b(redig\w*|ecri\w*|write|draft|dossier|proposition|reponse)\b"),
        ("qualify", r"\b(qualif\w*|analys\w*|scor\w*|go.?no.?go)\b"),
        ("collect", r"\b(collect\w*|cherch\w*|recherch\w*|search|find)\b"),
    ),
    "career": (
        ("interview", r"\b(interview|entretien)\b"),
        ("offer", r"\b(salaire|salary|negoci\w*|negotia\w*|tjm)\b"),
        ("followup", r"\b(follow.?up|relance\w*)\b"),
        ("analyze-ats", r"\b(ats)\b"),
        ("prepare", r"\b(cv|resume|tailor\w*|candidature|prepar\w*)\b"),
        ("cover", r"\b(cover.?letter|lettre|motivation)\b"),
        ("match", r"\b(match\w*|qualif\w*|scor\w*)\b"),
        ("search", r"\b(collect\w*|cherch\w*|recherch\w*|search|find|mission)\b"),
    ),
    "montage": (
        ("localize", r"\b(dub\w*|doubl\w*|tradui\w*|translat\w*|sous.titr\w*|subtitle\w*|transcri\w*)\b"),
        ("assemble", r"\b(timeline|assembl\w*|montage|cut|trim|render|export\w*)\b"),
        ("image", r"\b(image\w*|still\w*)\b"),
        ("video", r"\b(video\w*|clip\w*)\b"),
        ("analyze", r"\b(analys\w*|analyz\w*)\b"),
    ),
    "marketing": (
        ("email", r"\b(email|mail|newsletter)\b"),
        ("blog", r"\b(blog|article)\b"),
        ("measure", r"\b(metric\w*|analytics|kpi|mesur\w*)\b"),
        ("positioning", r"\b(position\w*|brand|marque|persona\w*)\b"),
        ("research", r"\b(research|recherch\w*|concurren\w*|competitor\w*)\b"),
        ("launch", r"\b(launch|lanc\w*)\b"),
        ("plan", r"\b(campagn\w*|campaign|plan\w*)\b"),
        ("copy", r"\b(post\w*|content|contenu|copy|redig\w*|ecri\w*)\b"),
    ),
    "meeting": (
        ("translate", r"\b(tradui\w*|translat\w*)\b"),
        ("cleanup", r"\b(corrig\w*|correct\w*|nettoi\w*|nettoy\w*|clean\w*)\b"),
        ("speakers", r"\b(speaker\w*|diari\w*|locuteur\w*)\b"),
        ("followup", r"\b(follow.?up|relance\w*|email|mail)\b"),
        ("report", r"\b(report|rapport|compte.rendu|minutes|synthes\w*|resum\w*|summary)\b"),
        ("answer", r"\b(question\w*|answer|repond\w*)\b"),
    ),
}


def skill_route_for_turn(
    metadata: Mapping[str, Any] | None,
    current_message: str,
    *,
    skill_names: Iterable[str] | None = None,
) -> tuple[str, str] | None:
    """Use explicit module/action metadata first, then the user's current intent."""
    from navin.agent.tools.context import current_request_context

    request = current_request_context()
    meta = dict(metadata or {})
    request_metadata = request.metadata if request and isinstance(request.metadata, dict) else {}
    meta.update(request_metadata)
    text = str(
        request_metadata.get("original_content")
        or (request.original_user_text if request else None)
        or meta.get("original_content")
        or current_message
    )
    module = normalize_skill_module(meta.get("product_module"), composer_mode=meta.get("composer_mode"))
    slash = re.match(r"\s*/(tenders|career|montage|marketing|campaign|meeting)\b\s*(.*)", text, re.DOTALL | re.IGNORECASE)
    if slash:
        command = slash[1].lower()
        module = normalize_skill_module("marketing" if command == "campaign" else command)
        text = slash[2]
    if module is None:
        present = _names(skill_names)
        for candidate, primary in (
            ("tenders", "tender-agent"), ("career", "career-agent"),
            ("montage", "montage-studio"), ("meeting", "meeting-studio"),
            ("marketing", "marketing-strategist"),
        ):
            if primary in present:
                module = candidate
                break
    if module is None:
        return None
    # Session metadata may describe an earlier action. A new inbound request
    # supplies its own action or is routed from its current user text.
    action_metadata = request_metadata if request else meta
    action = (
        action_metadata.get(SKILL_ACTION_METADATA_KEY)
        or action_metadata.get("module_action")
        or action_metadata.get("action")
    )
    if action:
        return module, normalize_skill_action(module, action)
    folded = "".join(char for char in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(char))
    for candidate, pattern in _ACTION_INTENT.get(module, ()):
        if re.search(pattern, folded):
            return module, candidate
    return module, "status"
