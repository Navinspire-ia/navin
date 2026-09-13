# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Retrieve adopted skills for the current task without running learning on chat."""

import re


def resume_learning(workspace, sessions=None):
    """Resume persisted work for this host and its known project sessions."""
    from pathlib import Path

    from navin.skills_evolve.jobs import resume_jobs
    roots = {Path(workspace)}
    if sessions is not None:
        for row in sessions.list_sessions():
            saved = sessions.read_session_metadata(row["key"]) or {}
            scope = saved.get("metadata", {}).get("workspace_scope", {})
            path = scope.get("project_path") if isinstance(scope, dict) else None
            if isinstance(path, str) and path:
                roots.add(Path(path))
    for root in roots:
        if root.is_dir():
            resume_jobs(root)


def resume_learning_in_background(workspace, sessions=None):
    import threading

    def resume():
        try:
            resume_learning(workspace, sessions)
        except (OSError, ValueError, TypeError, KeyError):
            pass

    threading.Thread(target=resume, name="navin-learning-resume", daemon=True).start()


def applicable_skills(workspace, message, metadata=None):
    from navin.command.modules import normalize_product_module
    from navin.skills_evolve.drafts import list_drafts
    from navin.skills_evolve.execution_pipeline import digest
    from navin.skills_evolve.paths import project_skill_file
    from navin.skills_evolve.settings import evolve_enabled
    if not evolve_enabled(workspace):
        return []
    text = f"{message or ''} {metadata or {}}".casefold()
    words = set(re.findall(r"[\w]+", text))
    module = normalize_product_module((metadata or {}).get("product_module"))
    selected = []
    for record in list_drafts(workspace):
        if record.status != "promoted" or not record.execution_evidence:
            continue
        coverage = record.execution_evidence.get("modules", [])
        if module and module not in coverage:
            continue
        family = str(record.origin.get("family") or "")
        family_words = {"code": {"code", "bug", "fix", "tests", "python", "corrige", "erreur", "repair"},
                        "desk": {"career", "carrière", "tenders", "missions", "profils", "candidat", "offres", "projet"}}.get(family, set())
        relevance = (10 if module in coverage else 0) + len(words & (family_words | set(re.findall(r"[\w]+", record.name))))
        if not relevance:
            continue
        path = project_skill_file(workspace, record.name)
        if path.is_file() and digest(path.read_text(encoding="utf-8")) == record.execution_evidence.get("candidate_digest"):
            selected.append((relevance, record.updated_at, record.name))
    return [name for _, _, name in sorted(selected, reverse=True)[:3]]


def excluded_skills(workspace, metadata=None):
    """Do not implicitly load a learned skill outside its measured module scope."""
    from navin.command.modules import normalize_product_module
    from navin.skills_evolve.drafts import list_drafts
    module = normalize_product_module((metadata or {}).get("product_module"))
    if not module:
        return set()
    return {row.name for row in list_drafts(workspace) if row.execution_evidence
            and module not in row.execution_evidence.get("modules", [])}
