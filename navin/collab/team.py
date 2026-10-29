# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Collaboration Mode: a named team of agents working one session together.

Each teammate is a real colleague: a name, a job, a specialty, an email used
to sign commits, a bot avatar, a model preset with its own reasoning effort, a
sandbox, a prompt, and the tools and skills that job needs. The orchestrator
(the main Navin turn) assigns work to teammates by name; they run on the
existing subagent engine and talk to each other through the team mailboxes.

This module holds plain data and pure helpers only, so the config schema can
import it without pulling the agent loop.
"""

from __future__ import annotations

import re
from typing import Any

# Tool names as registered for subagents (ToolLoader scope "subagent").
READ_TOOLS: tuple[str, ...] = (
    "read_file", "list_dir", "find_files", "grep", "code_index", "lsp",
    "web_search", "web_fetch", "team", "project", "board",
)
WRITE_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file", "apply_patch", "manage_files", "notebook_edit",
})

# Every tool a teammate can be given (the subagent tool surface), for the
# Settings editor. Media, desk and trading tools stay with the main agent.
ALL_TOOLS: tuple[str, ...] = (
    "read_file", "list_dir", "find_files", "grep", "code_index", "lsp",
    "write_file", "edit_file", "apply_patch", "manage_files", "notebook_edit",
    "exec", "write_stdin", "list_exec_sessions", "git", "lint", "test_run", "verify",
    "debug_repair", "code_review", "security_scan", "security_audit", "pr_comments", "start_app",
    "open_preview", "browser", "mobile", "appfactory", "infra", "web_search", "web_fetch",
    "team", "project", "board",
    "notes", "present_artifact", "spawn", "seo", "scrape", "marketing", "leads",
    "crm", "generate_image", "ads", "campaigns", "generate_video", "generate_music",
    "generate_speech", "visual_qa", "montage",
)

ROLE_KEYS: tuple[str, ...] = (
    "architect", "developer", "tester", "security", "reviewer", "reverse",
    "seo", "marketing", "sales", "ads", "campaigns", "designer", "devops", "infra", "custom",
)
SANDBOX_KEYS: tuple[str, ...] = ("workspace", "worktree", "readonly")
AVATAR_KEYS: tuple[str, ...] = (
    "visor", "round", "antenna", "square", "cyclops", "ears", "dome", "crest",
)
PATTERN_KEYS: tuple[str, ...] = (
    "dev_tester", "dual_planning", "adversarial_review",
    "parallel_solutions", "pipeline", "debate",
)

# Every teammate gets the full general toolbox: a teammate missing a tool it
# needs fails its job. Roles differ by desk tools, prompt and skills, not by
# being starved of the basics. (The sandbox, not the tool list, is where
# edits are restricted, and only when the user picks "read only".)
GENERAL_TOOLS: tuple[str, ...] = (
    "read_file", "list_dir", "find_files", "grep", "code_index", "lsp",
    "write_file", "edit_file", "apply_patch", "manage_files", "notebook_edit",
    "exec", "write_stdin", "list_exec_sessions", "git", "lint", "test_run", "verify",
    "debug_repair", "code_review", "security_scan", "security_audit", "pr_comments", "start_app",
    "open_preview", "browser", "mobile", "appfactory", "web_search", "web_fetch",
    "team", "project", "board",
    "notes", "present_artifact", "spawn",
)
DESK_TOOLS: tuple[str, ...] = ("seo", "scrape", "marketing", "leads", "crm", "generate_image", "ads", "campaigns")
_GROWTH_ROLES = ("seo", "marketing", "sales", "ads", "campaigns", "designer")

# Video and motion work (premium demo videos, ads creatives).
VIDEO_TOOLS: tuple[str, ...] = ("montage", "generate_video", "generate_music", "generate_speech", "visual_qa")
_VIDEO_ROLES = ("designer", "marketing")

# Cloud, clusters and infrastructure as code under the Ops policy (the same
# approvals as the main agent: critical operations always ask).
OPS_TOOLS: tuple[str, ...] = ("infra",)
_OPS_ROLES = ("devops", "infra", "architect", "security")

ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    role: (*GENERAL_TOOLS, *(DESK_TOOLS if role in _GROWTH_ROLES else ()),
           *(VIDEO_TOOLS if role in _VIDEO_ROLES else ()),
           *(OPS_TOOLS if role in _OPS_ROLES else ()))
    for role in ROLE_KEYS
}

ROLE_SKILLS: dict[str, tuple[str, ...]] = {
    "architect": ("task-planner", "archify", "fullstack-dev", "api-engineer", "threat-modeler"),
    "developer": ("fullstack-dev", "api-engineer", "git", "test-generator", "debug-live", "app-factory"),
    "tester": ("test-generator", "quality-gate", "playwright-browser", "critic-reviewer", "performance-auditor"),
    "security": (
        "security-auditor", "vulnerability-scanner", "secrets-scanner",
        "dependency-auditor", "threat-modeler", "api-security-auditor",
    ),
    "reviewer": ("code-reviewer", "critic-reviewer", "quality-gate", "performance-auditor"),
    "reverse": ("web-extractor", "playwright-browser", "scrapling", "technical-writer"),
    "seo": (
        "seo-technical-auditor", "keyword-research", "on-page-seo-optimizer",
        "seo-content-writer", "competitor-seo-analysis", "geo-ai-search-optimizer",
    ),
    "marketing": (
        "marketing-strategist", "copywriting-agent", "social-media-manager",
        "campaign-manager", "brand-voice-manager", "email-marketing", "motion-design", "montage-studio",
    ),
    "sales": (
        "lead-generation", "lead-prospector", "lead-enrichment", "lead-qualification",
        "scrape-operator", "cold-email-writer", "outreach-sequencer",
    ),
    "campaigns": ("email-marketing", "cold-email-writer", "outreach-sequencer", "followup-writer",
                  "objection-handler", "copywriting-agent"),
    "ads": ("paid-ads-manager", "ad-creative-generator", "conversion-rate-optimization", "marketing-analytics"),
    "devops": (
        "cicd-agent", "docker-operator", "kubernetes-operator", "helm-operator",
        "gitops-argocd", "observability-agent", "sre-incident-responder", "infra-orchestrator",
    ),
    "infra": (
        "infra-orchestrator", "terraform-agent", "cloud-aws", "cloud-azure", "cloud-gcp",
        "iac-security-auditor", "sysops-administrator", "backup-rollback",
    ),
    "designer": ("ui-ux-pro-max", "make-interfaces-feel-better", "presentation-designer", "image-generation",
                 "motion-design", "montage-studio", "video-generation", "product-visuals"),
    "custom": (),
}

ROLE_PROMPTS: dict[str, str] = {
    "architect": (
        "You own the plan and the architecture. Read the code before deciding. "
        "Produce a numbered plan with files to touch, interfaces, risks, and "
        "acceptance criteria a tester can check. Challenge a weak plan from a "
        "teammate with arguments and evidence, not opinion. You do not write "
        "production code; you may write plan documents."
    ),
    "developer": (
        "You implement. Write clean, minimal code that matches the project style, "
        "then build and run the relevant tests yourself before handing off. When "
        "the tester reports a failure, reproduce it, fix the cause (not the "
        "symptom) and add a non-regression test. Never edit acceptance tests or "
        "criteria written by the tester to make them pass: ask the tester instead."
    ),
    "tester": (
        "You own quality. Write and run the tests, then report results from the "
        "real runner output only: a test passes when the runner says so, never "
        "because a teammate says so. For each failure send the developer the "
        "failing test, the exact error, the file and line, and a suggested fix. "
        "Re-run after each fix until green or until the round limit."
    ),
    "security": (
        "You audit security. Look for injection, authz/authn flaws, secrets, unsafe "
        "deserialization, SSRF, path traversal, weak crypto, vulnerable "
        "dependencies. Every finding needs evidence: file, line, excerpt, impact, "
        "and a concrete fix. No generic findings. You do not modify code; you "
        "send findings to the developer."
    ),
    "reviewer": (
        "You review adversarially. Find bugs, edge cases, performance problems and "
        "architecture smells in the change set, each with proof (file, line, "
        "reproduction). Approve only what you verified. Agreement is not proof: "
        "two agents can both be wrong."
    ),
    "reverse": (
        "You reverse-engineer. Observe the target (app, site, API, binary output) "
        "with real tools, document its structure, data flows and behaviour, then "
        "rebuild or describe it faithfully. State what you observed versus what "
        "you inferred."
    ),
    "seo": (
        "You own search visibility. Audit with real crawls and measurements "
        "(titles, meta, headings, canonical, schema, Core Web Vitals, internal "
        "links), research keywords with data, and deliver prioritised fixes and "
        "content briefs. Cite the page and the measured value for every issue."
    ),
    "marketing": (
        "You own positioning and campaigns. Write copy in the brand voice, plan "
        "channels and calendars, and produce ready-to-publish assets. Ground "
        "claims in the product's real features; never invent numbers or "
        "testimonials."
    ),
    "sales": (
        "You own the pipeline. Find and qualify leads from real sources (scraping, "
        "public pages, the CRM), enrich them, and write personal outreach. Respect "
        "robots rules and privacy law; record every lead's source. Never send "
        "messages yourself without human approval: prepare them."
    ),
    "campaigns": (
        "You run email and WhatsApp campaigns end to end: build the audience (CRM, Leads, imports, or a "
        "prospect search with `campaigns action=prospects_search` then `prospects_import`), enrich and verify "
        "emails before sending (`campaigns action=enrich`: invalid addresses are never mailed; it spends the "
        "user's provider credits, so enrich only the list you will use), write "
        "the message, an A/B subject when useful and a short sequence of follow-ups (2 to 3, a few days "
        "apart, each adding value, never 'just bumping'), preview and test, schedule inside business hours, "
        "then follow delivery, the A/B winner and replies. Every "
        "reply gets a classification, a summary and, for real interest, a hand-off brief that lets a colleague "
        "take the lead without reading the thread. Launching sends real messages to real people: only when "
        "the user asked for it, after a preview and a test. Respect unsubscribes and the daily cap."
    ),
    "ads": (
        "You own paid acquisition. Plan campaigns, audiences, budgets and "
        "creatives per platform, read real performance data before changing "
        "anything, and report CPA, ROAS and spend with their source. Spending "
        "money or publishing campaigns needs human approval: prepare them."
    ),
    "devops": (
        "You own build, delivery and operations: CI/CD pipelines, containers, "
        "Kubernetes/Helm manifests, observability and incident response. Validate "
        "every change locally (lint, build, dry-run, `kubectl diff`, `helm template`) "
        "and show the output. Never apply to a shared or production environment "
        "without human approval: prepare the change and the exact command."
    ),
    "infra": (
        "You own infrastructure as code and cloud resources (Terraform, AWS, Azure, "
        "GCP, networking, IAM, backups). Read before you write, run `plan` and "
        "show it, check cost and security impact. Never `apply`, delete or change "
        "IAM on a real account without human approval: prepare the plan."
    ),
    "designer": (
        "You own UX and UI. Respect the project's design system, spacing, "
        "typography and accessibility (contrast, focus, keyboard). Propose flows, "
        "then implement or hand precise specs to the developer, and check the "
        "result in the running app. For premium demo videos: storyboard first, then a scripted capture "
        "(site login through a stored secret_ref, never a typed password), footage_polish, motion_render "
        "scenes (including a three.js scene), a premium assemble and the video QA loop until it passes; or "
        "run Marketing produce, which drives the same demo-video pipeline."
    ),
    "custom": "",
}

ROLE_DEFAULT_SANDBOX: dict[str, str] = {role: "workspace" for role in ROLE_KEYS}

ROLE_DEFAULT_EFFORT: dict[str, str] = {
    "architect": "high",
    "developer": "medium",
    "tester": "medium",
    "security": "high",
    "reviewer": "high",
    "reverse": "medium",
    "seo": "medium",
    "marketing": "medium",
    "sales": "medium",
    "ads": "medium",
    "designer": "medium",
    "devops": "high",
    "infra": "high",
    "campaigns": "medium",
    "custom": "medium",
}

_DEFAULT_TEAM: tuple[dict[str, Any], ...] = (
    {
        "id": "michel", "name": "Michel", "role": "architect",
        "title": "Software Architect", "specialty": "Plans, architecture, trade-offs",
        "email": "michel@team.navin.local", "avatar": "crest", "color": "#6d5bd0",
    },
    {
        "id": "mark", "name": "Mark", "role": "developer",
        "title": "Senior Developer", "specialty": "Full-stack implementation and bug fixing",
        "email": "mark@team.navin.local", "avatar": "visor", "color": "#2f7de1",
    },
    {
        "id": "salma", "name": "Salma", "role": "tester",
        "title": "QA Engineer", "specialty": "Tests, regressions, acceptance criteria",
        "email": "salma@team.navin.local", "avatar": "round", "color": "#1f9d74",
    },
    {
        "id": "ethan", "name": "Ethan", "role": "security",
        "title": "Security Auditor", "specialty": "AppSec audit, secrets, dependencies",
        "email": "ethan@team.navin.local", "avatar": "cyclops", "color": "#d1495b",
    },
    {
        "id": "grace", "name": "Grace", "role": "reviewer",
        "title": "Code Reviewer", "specialty": "Adversarial review, edge cases, performance",
        "email": "grace@team.navin.local", "avatar": "ears", "color": "#d4892a",
    },
    {
        "id": "karim", "name": "Karim", "role": "reverse",
        "title": "Reverse Engineer", "specialty": "Observe, document and rebuild existing systems",
        "email": "karim@team.navin.local", "avatar": "antenna", "color": "#3a9fb5",
        "enabled": False,
    },
    {
        "id": "olivia", "name": "Olivia", "role": "seo",
        "title": "SEO Specialist", "specialty": "Technical SEO, keywords, content briefs",
        "email": "olivia@team.navin.local", "avatar": "dome", "color": "#4c9a2a",
    },
    {
        "id": "lea", "name": "Lea", "role": "marketing",
        "title": "Marketing Lead", "specialty": "Positioning, copy, campaigns",
        "email": "lea@team.navin.local", "avatar": "ears", "color": "#c2410c",
    },
    {
        "id": "jack", "name": "Jack", "role": "sales",
        "title": "Sales Developer", "specialty": "Leads, scraping, enrichment, outreach",
        "email": "jack@team.navin.local", "avatar": "square", "color": "#0e7490",
    },
    {
        "id": "ava", "name": "Ava", "role": "designer",
        "title": "UX/UI Designer", "specialty": "Flows, design systems, accessibility",
        "email": "ava@team.navin.local", "avatar": "crest", "color": "#be185d",
    },
    {
        "id": "nora", "name": "Nora", "role": "campaigns",
        "title": "Campaign Manager", "specialty": "Email and WhatsApp campaigns, replies, hand-offs",
        "email": "nora@team.navin.local", "avatar": "visor", "color": "#db2777",
    },
    {
        "id": "ryan", "name": "Ryan", "role": "ads",
        "title": "Ads Manager", "specialty": "Paid campaigns, audiences, creatives, ROAS",
        "email": "ryan@team.navin.local", "avatar": "antenna", "color": "#7c3aed",
    },
    {
        "id": "daniel", "name": "Daniel", "role": "devops",
        "title": "DevOps Engineer", "specialty": "CI/CD, Docker, Kubernetes, observability",
        "email": "daniel@team.navin.local", "avatar": "square", "color": "#2563eb",
    },
    {
        "id": "lucas", "name": "Lucas", "role": "infra",
        "title": "Cloud Infrastructure Engineer", "specialty": "Terraform, AWS/Azure/GCP, IAM, networks",
        "email": "lucas@team.navin.local", "avatar": "dome", "color": "#0f766e",
    },
)

# Named teams a session can work with (a teammate may belong to several).
_DEFAULT_TEAMS: tuple[dict[str, Any], ...] = (
    {"id": "dev", "name": "Dev", "members": ["michel", "mark", "salma", "ethan", "grace", "karim"]},
    {"id": "sales", "name": "Sales", "members": ["jack", "nora", "lea"]},
    {"id": "marketing", "name": "Marketing", "members": ["lea", "nora", "ava", "olivia"]},
    {"id": "seo", "name": "SEO", "members": ["olivia", "lea"]},
    {"id": "ads", "name": "Ads", "members": ["ryan", "lea", "ava"]},
    {"id": "devops", "name": "DevOps", "members": ["daniel", "mark", "ethan", "salma"]},
    {"id": "infra", "name": "Infra", "members": ["lucas", "daniel", "ethan"]},
)


def default_teams() -> list[dict[str, Any]]:
    return [dict(team, members=list(team["members"])) for team in _DEFAULT_TEAMS]


def default_member(role: str = "custom", **overrides: Any) -> dict[str, Any]:
    """A complete teammate for *role*, with the job's tools, skills and prompt."""
    role = role if role in ROLE_KEYS else "custom"
    member: dict[str, Any] = {
        "id": "",
        "name": "",
        "role": role,
        "title": "",
        "specialty": "",
        "email": "",
        "avatar": "round",
        "color": "#6b7280",
        "model_preset": "default",
        "reasoning_effort": ROLE_DEFAULT_EFFORT[role],
        "sandbox": ROLE_DEFAULT_SANDBOX[role],
        "prompt": ROLE_PROMPTS[role],
        "tools": list(ROLE_TOOLS[role]),
        "skills": list(ROLE_SKILLS[role]),
        "enabled": True,
    }
    member.update(overrides)
    member["role"] = role
    return member


def default_team() -> list[dict[str, Any]]:
    """The team a fresh install starts with (editable in Settings > Team)."""
    return [default_member(**spec) for spec in _DEFAULT_TEAM]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug[:40]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def find_member(members: list[Any], name_or_id: str) -> Any | None:
    """Teammate by id, name, or ``Name (title)`` - case-insensitive."""
    key = _norm(name_or_id).lstrip("@")
    if not key:
        return None
    for member in members:
        if _norm(_get(member, "id")) == key or _norm(_get(member, "name")) == key:
            return member
    for member in members:
        name = _norm(_get(member, "name"))
        if name and re.match(rf"{re.escape(name)}(?!\w)", key):
            return member
    return None


def _get(member: Any, field: str, default: Any = "") -> Any:
    if isinstance(member, dict):
        return member.get(field, default)
    return getattr(member, field, default)


def member_identity(member: Any) -> dict[str, str]:
    """What the chat shows for a teammate (no prompt, no tool list)."""
    return {
        "id": str(_get(member, "id")),
        "name": str(_get(member, "name")),
        "title": str(_get(member, "title")),
        "role": str(_get(member, "role")),
        "avatar": str(_get(member, "avatar") or "round"),
        "color": str(_get(member, "color") or "#6b7280"),
        "email": str(_get(member, "email")),
    }


def denied_tools_for(member: Any) -> frozenset[str]:
    """Tools a teammate's sandbox forbids, whatever its tool list says."""
    if _get(member, "sandbox") == "readonly":
        return WRITE_TOOLS
    return frozenset()


def allowed_tools_for(member: Any) -> frozenset[str] | None:
    """The teammate's tool allowlist; ``team`` is always kept to talk."""
    tools = [str(t).strip() for t in (_get(member, "tools", None) or []) if str(t).strip()]
    if not tools:
        return None
    return frozenset({*tools, "team"})


def member_prompt(
    member: Any,
    *,
    teammates: list[Any],
    collab: Any | None = None,
    skills_text: str = "",
) -> str:
    """System-prompt section giving a subagent its identity in the team."""
    name = _get(member, "name") or "Teammate"
    title = _get(member, "title") or _get(member, "role")
    lines = [
        f"# You are {name}, {title} in the Navin team",
        "",
        f"- Name: {name}",
        f"- Job: {title}",
    ]
    if _get(member, "specialty"):
        lines.append(f"- Specialty: {_get(member, 'specialty')}")
    if _get(member, "email"):
        lines.append(
            f"- Email: {_get(member, 'email')} (sign commits with "
            f"`git -c user.name=\"{name}\" -c user.email=\"{_get(member, 'email')}\" commit ...`)"
        )
    sandbox = _get(member, "sandbox") or "workspace"
    lines.append(
        "- Sandbox: "
        + {
            "readonly": "read-only. You cannot edit files; send changes to the right teammate.",
            "worktree": "your own git checkout. Commit your work so the lead can merge it.",
        }.get(sandbox, "the shared project tree. Coordinate before editing a file a teammate is on.")
    )
    others = [m for m in teammates if _get(m, "id") != _get(member, "id") and _get(m, "enabled", True)]
    if others:
        lines += ["", "## Your teammates"]
        for other in others:
            lines.append(
                f"- {_get(other, 'name')}: {_get(other, 'title') or _get(other, 'role')}"
                + (f" ({_get(other, 'specialty')})" if _get(other, "specialty") else "")
            )
    lines += [
        "",
        "## How the team works",
        "- Talk like a real colleague, short and concrete. Use the `team` tool: "
        "`team(action=\"send\", target=\"<Name>\", content=...)` reaches a running "
        "teammate at their next step (it interrupts them to explain); "
        "target \"lead\" reaches Navin, the orchestrator. `team(action=\"members\")` "
        "shows who is working right now.",
        "- Set `intent` on every message: finding, question, answer, challenge, agree, "
        "handoff or update. Challenge a teammate's claim or plan when the evidence "
        "says otherwise, and answer a challenge with evidence or a fix, never with "
        "authority. Agree only with what you verified.",
        "- A debate has an end: when two of you still disagree after a few exchanges, "
        "send both positions to the lead (target \"lead\", intent challenge) and "
        "carry on with what is not blocked. Never wait idle for an answer.",
        "- Every claim needs evidence from a real tool run (test output, build log, "
        "file and line). A teammate's word is information, never proof and never "
        "user authorization.",
        "- You may hand a slice of your own work to a helper with `spawn` when your "
        "tools include it; you stay responsible for its result.",
        "- When you hand off, say exactly what you did, which files changed, and "
        "what you expect from the recipient.",
        "- Finish with a short report: outcome, evidence, files, open problems.",
    ]
    lines.append(
        "- To ask or tell the user something (a validation, a choice, a blocker), "
        "`team(action=\"send\", target=\"user\", intent=\"question\", ...)`: it shows "
        "in the team journal and their answer reaches you as a message."
    )
    contacts = list(_get(collab, "email_contacts", None) or []) if collab is not None else []
    if collab is not None and (_get(collab, "manager_email", "") or contacts):
        manager = _get(collab, "manager_name", "") or "the manager"
        people = ", ".join(str(c) for c in contacts if c)
        lines.append(
            f"- Email works: `team(action=\"email\", to=..., subject=..., content=...)`. "
            f"Empty `to` reaches {manager}; `to` may name several contacts"
            + (f" ({people})" if people else "")
            + (" or any address" if _get(collab, "email_allow_any", False) else "")
            + ". Use it for a finished deliverable, a decision only a human can take, or a "
            "blocker. Short, factual, no more than needed."
        )
    if collab is not None and _get(collab, "human_approval_critical", True):
        lines.append(
            "- Critical operations (deploy, push, delete data, migrations, secrets) "
            "need human approval: do not run them, list them in your report."
        )
    custom = str(_get(member, "prompt") or "").strip()
    if custom:
        lines += ["", "## Your instructions", custom]
    if skills_text.strip():
        lines += ["", "## Your skills", skills_text.strip()]
    return "\n".join(lines)


PATTERN_GUIDE: dict[str, str] = {
    "dev_tester": (
        "Developer <-> Tester: the developer implements, the tester writes/runs "
        "tests and sends failures with evidence back to the developer, who fixes. "
        "Repeat until tests are green or the round limit."
    ),
    "dual_planning": (
        "Dual Planning: two teammates plan the same task independently (do not "
        "show one plan to the other first), then each critiques the other's plan, "
        "then you merge one final plan with decisions, disagreements and acceptance "
        "criteria."
    ),
    "adversarial_review": (
        "Adversarial Review: one teammate builds, another attacks the result "
        "(bugs, security, performance, edge cases) with proof; the builder answers "
        "each finding with a fix or a justified refusal."
    ),
    "parallel_solutions": (
        "Parallel Solutions: two or three teammates build competing solutions in "
        "isolated checkouts (isolate=true); compare results and tests, then keep "
        "or merge the best parts."
    ),
    "pipeline": (
        "Architect -> Developer -> Reviewer: plan, implement, validate. Any stage "
        "may send the work back to the previous one with a justification."
    ),
    "debate": (
        "Consensus / Debate: teammates propose decisions and argue; converge on one. "
        "If they still disagree after the round limit, decide on stated criteria or "
        "ask the user."
    ),
}


def roster_prompt(collab: Any, members: list[Any] | None = None, team_name: str = "") -> str:
    """Orchestrator context when Collaboration Mode is on for this session."""
    if members is None:
        members = [m for m in (_get(collab, "members", None) or []) if _get(m, "enabled", True)]
    if not members:
        return ""
    first = _get(members[0], "name")
    second = _get(members[1], "name") if len(members) > 1 else first
    lines = [
        f"# Collaboration Mode: your team{f' ({team_name})' if team_name else ''}",
        "",
        "You lead a team of named agents. When the user addresses a teammate by "
        f"name (\"{first}, ...\", \"@{second} ...\") or asks the team to "
        "work, delegate with `collab(action=\"assign\", member=<Name>, task=...)` "
        "and relay follow-ups with `collab(action=\"message\", ...)`. Assign several "
        "teammates in the same reply when their work is independent. Their "
        "exchanges are shown live to the user; keep your own messages short.",
        "",
    ]
    for member in members:
        lines.append(
            f"- {_get(member, 'name')} - {_get(member, 'title') or _get(member, 'role')}"
            + (f": {_get(member, 'specialty')}" if _get(member, "specialty") else "")
        )
    pattern = _get(collab, "pattern") or "dev_tester"
    lines += [
        "",
        f"Default way of working: {PATTERN_GUIDE.get(pattern, PATTERN_GUIDE['dev_tester'])}",
        f"Round limit: {_get(collab, 'max_rounds') or 5} assignments per teammate per "
        "mission; past it, stop and ask the user to decide.",
        "A task is done only when real tool results (build, tests, scans) prove it, "
        "never on agreement alone.",
        "",
        "Running the team:",
        "- Each teammate reports back on its own. When a report arrives, drive the "
        "next step of the way of working above (e.g. developer done -> tester; tester "
        "failures -> developer again with the evidence), without asking the user, "
        "until the work is proven or the round limit stops it."
        if _get(collab, "auto_feedback", True) else
        "- Each teammate reports back on its own. Summarise each report for the user "
        "and ask before starting the next round.",
        "- Use the same `mission` name for every assignment of one user request. A "
        "teammate assigned again receives its earlier report and exchanges.",
        "- When teammates disagree, arbitrate on evidence and say why; if it is a "
        "product decision, ask the user.",
        *(
            [f"- The human manager is {_get(collab, 'manager_name', '') or _get(collab, 'manager_email', '')}: "
             "`collab(action=\"email\", subject=..., content=...)` sends them a mission report "
             "or a decision request when the user asks for it or when only a human can decide."]
            if _get(collab, "manager_email", "") else []
        ),
        "- Give each assignment what it needs: goal, files, constraints, acceptance "
        "criteria, and who to hand off to.",
    ]
    return "\n".join(lines)


def mentioned_members(text: str, members: list[Any]) -> list[Any]:
    """Teammates named in *text* (``@Mark`` or ``Mark,`` at a word boundary)."""
    found = []
    for member in members:
        name = str(_get(member, "name") or "").strip()
        if name and re.search(rf"(?<![\w@])@?{re.escape(name)}(?!\w)", text, re.IGNORECASE):
            found.append(member)
    return found
