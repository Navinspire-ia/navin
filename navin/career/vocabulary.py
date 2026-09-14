# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Evidence matching using the same bilingual vocabulary as company setup."""

from __future__ import annotations

import json
import re
import unicodedata
from importlib.resources import files

# A catalog parity test keeps this packaged snapshot aligned with the UI.
_CATALOG = json.loads(files("navin.career").joinpath("role_catalog.json").read_text())


def fold(value: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[-_/]+", " ", text)).strip()


def has_term(text: str, term: str) -> bool:
    term = fold(term)
    return bool(term) and bool(re.search(r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])", fold(text)))


_SKILLS: dict[str, set[str]] = {}
for _row in _CATALOG:
    for _labels in _row["skills"]:
        _aliases = {part.strip() for label in _labels for part in [label, *label.split(" / ")]}
        for _label in _aliases:
            _SKILLS.setdefault(fold(_label), set()).update(_aliases)
for _aliases in (("Apache Spark", "Spark"), ("Node.js", "NodeJS", "Node JS"), ("Kubernetes", "K8s"),
                 ("LLM", "LLMs", "large language models"), ("RAG", "retrieval augmented generation"),
                 ("REST API", "API REST", "RESTful"), ("PostgreSQL", "Postgres")):
    for _label in _aliases:
        _SKILLS.setdefault(fold(_label), set()).update(_aliases)


def skill_present(text: str, skill: str) -> bool:
    return any(has_term(text, alias) for alias in _SKILLS.get(fold(skill), {skill}))


def _role_words(value: str) -> set[str]:
    text = fold(value)
    replacements = {"ingenieur": "engineer", "ingenierie": "engineer", "engineering": "engineer",
                    "developpeur": "developer", "development": "developer", "architecte": "architect",
                    "analyste": "analyst", "donnees": "data", "projet": "project", "directeur": "director", "ia": "ai"}
    ignored = {"de", "du", "des", "en", "et", "the", "of", "and", "d", "senior", "junior", "freelance", "contract"}
    return {replacements.get(word, word) for word in re.findall(r"[\w+#.]+", text) if word not in ignored}


def role_present(title: str, role: str) -> bool:
    wanted, actual = _role_words(role), _role_words(title)
    functions = {"engineer", "developer", "architect", "analyst", "scientist", "director", "manager", "owner", "consultant"}
    # A shared domain alone (Data, AI, SAP) does not establish the requested job.
    if wanted & functions and not wanted & functions & actual:
        return False
    entry = next((row for row in _CATALOG if any(fold(role) == fold(label) for label in [*row["label"], *row["aliases"]])), None)
    if entry and any(has_term(title, label) for label in [*entry["label"], *entry["aliases"]]):
        return True
    return bool(wanted) and wanted <= actual
