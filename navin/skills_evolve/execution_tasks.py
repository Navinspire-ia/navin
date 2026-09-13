# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Executable, parameterized tasks. Expected answers stay outside agent tools."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExecutionTask:
    id: str
    suite: str
    prompt: str
    files: dict[str, str]
    expected: Any
    inputs: list[Any] | None = None
    required_tool: str = ""

    def prepare(self, root: Path) -> None:
        for name, content in self.files.items():
            (root / name).write_text(content, encoding="utf-8")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def tasks(seed: str, *, repetitions: int = 2, modules=None) -> list[ExecutionTask]:
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
    result = []
    for repeat in range(repetitions):
        values = [[rng.randint(-9, 9) for _ in range(rng.randint(2, 15))] for _ in range(10)]
        values.extend([[], [0, 0], [2, 1, 2, 3, 1]])
        result.append(ExecutionTask(
            f"dedupe-{repeat}", "code",
            "Fix solve(values) in solution.py. Return each distinct value once, in its original order. "
            "Handle empty lists and negative numbers. Run verify after the final edit.",
            {"solution.py": "def solve(values):\n    return sorted(set(values))\n"},
            [list(dict.fromkeys(row)) for row in values], values, "verify"))
        inputs = [[rng.randint(-15, 20) for _ in range(rng.randint(1, 14))] for _ in range(10)] + [[], [0], [-2, 2]]
        result.append(ExecutionTask(
            f"total-{repeat}", "code",
            "Fix solve(values) in solution.py. Sum every strictly positive value, including the last element. "
            "Return zero when there are none. Run verify after the final edit.",
            {"solution.py": "def solve(values):\n    return sum(value for value in values[:-1] if value >= 0)\n"},
            [sum(value for value in row if value > 0) for row in inputs], inputs, "verify"))
        country = rng.choice(["FR", "MA", "SA", "AE", "QA", "OM", "BH", "KW"])
        budget = rng.choice([400, 500, 650])
        ids = [f"candidate-{value}" for value in rng.sample(range(10000, 99999), 4)]
        candidates = [
            {"id": ids[0], "headline": "Generative AI engineer", "skills": ["Python", "RAG"],
             "country": country, "daily_rate": budget - 20, "currency": "EUR", "signal": "available"},
            {"id": ids[1], "headline": "Generative AI engineer", "skills": ["Python", "RAG"],
             "country": country, "daily_rate": budget + 100, "currency": "EUR", "signal": "available"},
            {"id": ids[2], "headline": "Generative AI engineer", "skills": ["Python", "RAG"],
             "country": country, "daily_rate": budget - 50, "currency": "EUR", "signal": "unavailable"},
            {"id": ids[3], "headline": "Accountant", "skills": ["Accounting"],
             "country": country, "daily_rate": budget - 30, "currency": "EUR", "signal": "available"},
        ]
        rng.shuffle(candidates)
        mission = {"title": "Generative AI engineer", "stack": ["Python", "RAG"], "country": country}
        criteria = {"roles": [mission["title"]], "skills": mission["stack"], "countries": [country],
                    "profile_countries": [country], "city": "", "profile_city": "", "buy_rate_max": budget,
                    "currency": "EUR", "min_score": 70}
        result.append(ExecutionTask(
            f"matching-{repeat}", "career",
            "Read the mission and candidates in task.json. Use career to score the candidates. "
            "Select only available candidates in the required country, within the purchase rate and at least 70/100. "
            "Save the selected candidate IDs as a JSON array in result.json. Do not contact anyone.",
            {"task.json": _json({"mission": mission, "criteria": criteria, "candidates": candidates})},
            [ids[0]], required_tool="career"))
        notice_ids = [f"notice-{value}" for value in rng.sample(range(10000, 99999), 3)]
        notices = [
            {"id": notice_ids[0], "title": "Data platform engineering", "description": "Data and ETL services",
             "country": country, "deadline": "2099-12-31", "budget": 90000},
            {"id": notice_ids[1], "title": "Data platform engineering", "description": "Data and ETL services",
             "country": country, "deadline": "2020-01-01", "budget": 90000},
            {"id": notice_ids[2], "title": "Garden maintenance", "description": "Mowing lawns and planting trees",
             "country": country, "deadline": "2099-12-31", "budget": 90000},
        ]
        rng.shuffle(notices)
        profile = {"crafts": ["data"], "countries": [country], "min_score": 60, "min_deadline_days": 10,
                   "min_budget": 10000, "max_budget": 200000, "turnover": 1000000, "references": [{}, {}, {}]}
        result.append(ExecutionTask(
            f"qualification-{repeat}", "tenders",
            "Read the company and notices in task.json. Use tenders to qualify the notices with this company profile. "
            "Exclude expired notices and notices outside the company's expertise. Save only the eligible notice IDs "
            "as a JSON array in result.json. Do not submit bids or send messages.",
            {"task.json": _json({"profile": profile, "notices": notices})}, [notice_ids[0]], required_tool="tenders"))
        from navin.skills_evolve.module_tasks import module_tasks
        result.extend(module_tasks(rng, repeat))
    return [case for case in result if modules is None or case.suite in modules]


def task_fingerprint(cases: list[ExecutionTask]) -> str:
    from dataclasses import asdict
    return hashlib.sha256(_json([asdict(case) for case in cases]).encode()).hexdigest()
