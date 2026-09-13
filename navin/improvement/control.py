# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from pathlib import Path

from navin.config.loader import get_config_path
from navin.improvement.engine import ImprovementEngine
from navin.improvement.policies import POLICIES


def module_engine(workspace: Path, module: str) -> ImprovementEngine:
    root = workspace / ".navin" / "improvement" if module == "code" else get_config_path().parent / module / "improvement"
    return ImprovementEngine(root, module)


def control(workspace: Path, module: str = "all", action: str = "status", context: str = "") -> dict:
    modules = list(POLICIES) if module == "all" else [module]
    if any(name not in POLICIES for name in modules) or action not in {"status", "enable", "pause", "rollback"}:
        raise ValueError("Invalid improvement module or action.")
    result = {}
    for name in modules:
        engine = module_engine(workspace, name)
        if action in {"enable", "pause"}:
            result[name] = engine.configure(enabled=action == "enable")
        elif action == "rollback":
            result[name] = engine.rollback(context)
        else:
            try:
                result[name] = engine.status()
            except (ValueError, OSError, KeyError, TypeError) as exc:
                result[name] = {"module": name, "enabled": False, "available": False,
                                "error": f"Improvement state unavailable ({type(exc).__name__}). Original strategy remains active."}
    return result
