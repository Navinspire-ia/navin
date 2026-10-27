# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool ``appfactory``: turn the current project into native apps.

Desktop is always Tauri 2 + Rust, mobile always Capacitor 8; everything is
written in ``os/<target>``. The tool analyses, plans, checks the toolchain,
scaffolds, audits, finds packages and writes the honest delivery report. Builds
and installs run through ``exec`` (long builds in the background), installs
only after the user agreed. Skill ``app-factory``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.base import ToolResult
from navin.agent.tools.quality import _QualityTool

_READ_ACTIONS = frozenset({"targets", "analyze", "plan", "doctor", "audit", "artifacts"})
_WRITE_ACTIONS = frozenset({"scaffold", "report"})
_ACTIONS = tuple(sorted(_READ_ACTIONS | _WRITE_ACTIONS))
_NEEDS_TARGET = frozenset({"plan", "doctor", "scaffold", "audit", "artifacts", "report"})
_MAX_CHARS = 40_000


def _dump(payload: Any) -> str:
    text = json.dumps(payload, indent=1, ensure_ascii=False, default=str)
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n... [{len(text) - _MAX_CHARS} characters cut]"
    return text


class AppFactoryTool(_QualityTool):
    """Analyse, plan, scaffold, audit and deliver os/<target> native apps."""

    @property
    def name(self) -> str:
        return "appfactory"

    @property
    def description(self) -> str:
        return (
            "App Factory: turn the current web or full-stack project into native apps without touching the website or "
            "the backend. Imposed engines, never ask the user to choose: desktop = Tauri 2 + Rust (windows, macos, "
            "ubuntu .deb, centos .rpm, appimage), mobile = Capacitor 8 (android .apk/.aab, ios .ipa). Everything goes "
            "in os/<target> at the project root. targets: the 7 targets. analyze: frontend framework and version, "
            "package manager, build script and output, backend, server features with files (Server Actions, route "
            "handlers, SSR, middleware), auth, API URL env names, icon, existing native folders. plan target=: "
            "strategy (static, next-export in a staging copy, client-shell = client frontend in os/<target>/web "
            "calling the existing backend; never output: \"export\" on an app with server features), ordered "
            "commands, tests, risks, CI runner when this machine cannot build. doctor target=: toolchain present or "
            "missing with the official install command (never installs; ask the user first, then exec). scaffold "
            "target= [app_name, app_id, backend_url, dry_run]: writes os/<target> from the templates, keeps existing "
            "files, writes nowhere else. audit target=: permissions, CSP, HTTPS, secrets. artifacts target=: built "
            "packages. report target= steps=[{step,status,note}] tests=[{name,status passed|failed|not_run,"
            "evidence}]: delivered only with a package and every required test passed. Skill app-factory."
        )

    def call_read_only(self, arguments: Any) -> bool:
        if not isinstance(arguments, dict):
            return False
        if arguments.get("action") == "scaffold":
            return bool(arguments.get("dry_run"))
        return arguments.get("action") in _READ_ACTIONS

    def call_concurrency_safe(self, arguments: Any) -> bool:
        return isinstance(arguments, dict) and arguments.get("action") in {"targets", "analyze", "plan", "doctor", "artifacts"}

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "target": {"type": "string", "description": "android, ios, windows, macos, ubuntu, centos or appimage (aliases: apk, msi, linux, rpm...)"},
                "app_name": {"type": "string", "description": "scaffold: name shown under the icon (default: from package.json)"},
                "app_id": {"type": "string", "description": "scaffold: reverse-DNS id such as com.company.app"},
                "backend_url": {"type": "string", "description": "plan / scaffold: public HTTPS address of the existing backend"},
                "dry_run": {"type": "boolean", "description": "scaffold: list the files without writing"},
                "steps": {
                    "type": "array",
                    "description": "report: one entry per phase",
                    "items": {"type": "object", "properties": {
                        "step": {"type": "string", "enum": ["analyze", "compat", "toolchain", "transform", "build", "test", "deliver"]},
                        "status": {"type": "string", "enum": ["done", "failed", "skipped", "pending"]},
                        "note": {"type": "string"},
                    }, "required": ["step", "status"]},
                },
                "tests": {
                    "type": "array",
                    "description": "report: every test, run or not (install, start, auth, navigation, api, ...)",
                    "items": {"type": "object", "properties": {
                        "name": {"type": "string"},
                        "status": {"type": "string", "enum": ["passed", "failed", "not_run"]},
                        "evidence": {"type": "string", "description": "command output or observation; required for passed"},
                    }, "required": ["name", "status"]},
                },
                "limitations": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
            "required": ["action"],
        }

    async def execute(self, action: str = "", **kwargs: Any) -> Any:
        act = (action or "").strip().lower()
        if act not in _ACTIONS:
            return ToolResult.error(f"Error: action must be one of {', '.join(_ACTIONS)}")
        target = str(kwargs.get("target") or "").strip()
        if act in _NEEDS_TARGET and not target:
            return ToolResult.error(f"Error: {act} needs target= (android, ios, windows, macos, ubuntu, centos, appimage)")
        try:
            return _dump(await asyncio.to_thread(self._run, act, target, kwargs))
        except (ValueError, KeyError, OSError) as exc:
            return ToolResult.error(f"Error: {exc}")

    def _run(self, act: str, target: str, kw: dict[str, Any]) -> dict[str, Any]:
        from navin.appfactory import analyze as analyze_mod
        from navin.appfactory import compat, deliver, scaffold, toolchain
        from navin.appfactory.targets import get_target, targets_payload

        root = self._project_root()
        if act == "targets":
            return targets_payload()
        if target:
            target = get_target(target).id
        if act == "doctor":
            return toolchain.doctor(target)
        if act == "artifacts":
            return deliver.artifacts(root, target)
        if act == "audit":
            return scaffold.audit(root, target)
        analysis = analyze_mod.analyze(root)
        if act == "analyze":
            return analysis
        backend_url = str(kw.get("backend_url") or "")
        plan = compat.plan(analysis, target, backend_url=backend_url, app_id=str(kw.get("app_id") or ""))
        if act == "plan":
            plan["next"] = (
                "Check the toolchain with appfactory action=doctor target=" + target
                + (", ask the user for: " + "; ".join(plan["questions"]) if plan["questions"] else "")
                + ", then appfactory action=scaffold target=" + target
            )
            return plan
        if act == "scaffold":
            result = scaffold.scaffold(root, target, analysis, plan, app_name=str(kw.get("app_name") or ""),
                                       app_id=str(kw.get("app_id") or ""), backend_url=backend_url,
                                       dry_run=bool(kw.get("dry_run")))
            result["strategy"] = plan["strategy"]
            result["questions"] = [] if backend_url else plan["questions"]
            return result
        has_auth = bool(analysis.get("auth", {}).get("server") or analysis.get("auth", {}).get("client"))
        return deliver.report(root, target, list(kw.get("steps") or []), list(kw.get("tests") or []), has_auth=has_auth,
                              notes=str(kw.get("notes") or ""), limitations=list(kw.get("limitations") or []))
