# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin-specific canvas templates: Navin data in, canvas blocks out.

Each template is a pure function returning ``(title, blocks)`` from data shaped
like the producing Navin module (documented per function). ``TEMPLATES`` maps
the public name used by ``present_artifact(type="canvas", template=...)``.
``load_template_data`` fills in data Navin can read locally (board, token
usage) when the caller passes none.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from navin.artifacts.canvas import CanvasError

Blocks = list[dict[str, Any]]

_SEVERITIES = ("critical", "high", "medium", "low", "info")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def _pct_change(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before == 0:
        return None
    return round((after - before) / before * 100.0, 1)


# --------------------------------------------------------------------------
# a. Performance benchmark before/after
# --------------------------------------------------------------------------

#: p95 budgets enforced by ``scripts/bench_tui.py --check`` (ms).
TUI_P95_BUDGET_MS = {"input": 50.0, "scheduled_input": 50.0, "open": 100.0, "close": 100.0}


def _bench_metrics(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(report, dict):
        return {}
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        metrics = report.get("results")  # scripts/bench_agent_hotpath.py shape
    if not isinstance(metrics, dict):
        return {}
    return {str(k): v for k, v in metrics.items() if isinstance(v, dict)}


def benchmark_blocks(data: dict[str, Any]) -> tuple[str, Blocks]:
    """Before/after latency benchmark.

    *data* is ``{"after": report, "before": report?, "label_before"?, "label_after"?,
    "budgets"?: {metric: p95_ms}}`` where a report is the JSON written by
    ``scripts/bench_tui.py --json`` (``metrics: {name: {samples, p50_ms, p95_ms,
    max_ms}}``) or by ``scripts/bench_agent_hotpath.py`` (``results: {...}``).
    """
    after = _bench_metrics(data.get("after"))
    before = _bench_metrics(data.get("before"))
    if not after:
        raise CanvasError(["benchmark: 'after' report has no metrics"])
    label_b = str(data.get("label_before") or "Before")
    label_a = str(data.get("label_after") or "After")
    budgets = data.get("budgets")
    if not isinstance(budgets, dict):
        budgets = TUI_P95_BUDGET_MS if "input" in after else {}
    names = list(after)
    has_before = any(n in before for n in names)

    rows: list[dict[str, Any]] = []
    over_budget = 0
    improved = 0
    for name in names:
        a = after[name]
        b = before.get(name, {})
        p95_a, p95_b = _num(a.get("p95_ms")), _num(b.get("p95_ms"))
        p50_a, p50_b = _num(a.get("p50_ms")), _num(b.get("p50_ms"))
        row: dict[str, Any] = {
            "metric": name,
            "p50_after": p50_a,
            "p95_after": p95_a,
            "max_after": _num(a.get("max_ms")),
            "samples": a.get("samples") if isinstance(a.get("samples"), int) else a.get("runs"),
        }
        if has_before:
            row.update(
                p50_before=p50_b,
                p95_before=p95_b,
                p95_change=_pct_change(p95_b, p95_a),
            )
            if p95_a is not None and p95_b is not None and p95_a < p95_b:
                improved += 1
        budget = _num(budgets.get(name))
        if budget is not None and p95_a is not None:
            ok = p95_a <= budget
            over_budget += 0 if ok else 1
            row["budget"] = {"badge": f"{'within' if ok else 'over'} {budget:g} ms", "tone": "good" if ok else "bad", "sort": p95_a / budget}
        rows.append(row)

    stats: list[dict[str, Any]] = [{"label": "Metrics", "value": len(names)}]
    worst = max(rows, key=lambda r: r["p95_after"] or 0)
    stats.append({"label": f"Worst p95 ({worst['metric']})", "value": worst["p95_after"] or 0, "unit": "ms"})
    if has_before:
        stats.append({"label": "p95 improved", "value": f"{improved} / {len(names)}", "tone": "good" if improved else "neutral"})
        changes = [r["p95_change"] for r in rows if r.get("p95_change") is not None]
        if changes:
            mean = round(sum(changes) / len(changes), 1)
            stats.append({"label": "Mean p95 change", "value": mean, "unit": "%", "tone": "good" if mean < 0 else "bad"})
    if budgets:
        stats.append({"label": "Over budget", "value": over_budget, "tone": "bad" if over_budget else "good",
                      "hint": "p95 budgets from bench_tui --check"})

    plotted = [r for r in rows if r["p95_after"] is not None]
    def series(key: str, name: str, baseline: bool = False) -> dict[str, Any]:
        s: dict[str, Any] = {"name": name, "values": [r.get(key) or 0 for r in plotted]}
        if baseline:
            s["role"] = "baseline"
        return s

    source = str(data.get("source") or "scripts/bench_tui.py")
    p95_series = ([series("p95_before", label_b, True)] if has_before else []) + [series("p95_after", label_a)]
    p50_series = ([series("p50_before", label_b, True)] if has_before else []) + [series("p50_after", label_a)]
    cats = [r["metric"] for r in plotted]
    columns: list[dict[str, Any]] = [{"key": "metric", "label": "Metric", "mono": True}]
    if has_before:
        columns += [
            {"key": "p50_before", "label": f"p50 {label_b}", "unit": "ms"},
            {"key": "p50_after", "label": f"p50 {label_a}", "unit": "ms"},
            {"key": "p95_before", "label": f"p95 {label_b}", "unit": "ms"},
            {"key": "p95_after", "label": f"p95 {label_a}", "unit": "ms"},
            {"key": "p95_change", "label": "p95 change", "format": "delta_pct", "better": "lower"},
        ]
    else:
        columns += [{"key": "p50_after", "label": "p50", "unit": "ms"}, {"key": "p95_after", "label": "p95", "unit": "ms"}]
    columns += [{"key": "max_after", "label": "max", "unit": "ms"}, {"key": "samples", "label": "Samples"}]
    if budgets:
        columns.append({"key": "budget", "label": "Budget"})

    blocks: Blocks = [
        {"type": "stats", "items": stats},
        {"type": "bar_chart", "title": "p95 latency by metric", "x_label": "Metric", "y_label": "p95 latency",
         "unit": "ms", "categories": cats, "series": p95_series,
         "caption": f"Source: {source}. Lower is better. {label_b + ' vs ' + label_a if has_before else label_a} run."},
        {"type": "bar_chart", "title": "p50 latency by metric", "x_label": "Metric", "y_label": "p50 latency",
         "unit": "ms", "categories": cats, "series": p50_series,
         "caption": f"Source: {source}. Median of all samples per metric."},
        {"type": "table", "title": "All metrics", "columns": columns, "rows": rows,
         "sort": {"key": "p95_after", "dir": "desc"}, "caption": "Click a column header to sort."},
    ]
    return str(data.get("title") or "Performance benchmark"), blocks


# --------------------------------------------------------------------------
# b. Audit / code review findings
# --------------------------------------------------------------------------


def _clean_finding(raw: dict[str, Any]) -> dict[str, Any] | None:
    title = str(raw.get("summary") or raw.get("title") or raw.get("issue_header") or "").strip()
    if not title:
        return None
    severity = str(raw.get("severity") or "info").strip().lower()
    if severity == "nit":
        severity = "info"
    if severity not in _SEVERITIES:
        severity = "info"
    item: dict[str, Any] = {"severity": severity, "title": title[:300]}
    path = str(raw.get("file_path") or raw.get("file") or raw.get("path") or "").strip()
    if path:
        item["file"] = path
    line = raw.get("start_line") if raw.get("start_line") is not None else raw.get("line")
    if isinstance(line, int) and not isinstance(line, bool) and line > 0:
        item["line"] = line
    detail = str(raw.get("explanation") or raw.get("detail") or "").strip()
    if detail:
        item["detail"] = detail[:2000]
    evidence = raw.get("existing_code") or raw.get("evidence") or raw.get("malicious_input_example")
    if isinstance(evidence, list):
        evidence = "\n".join(str(e) for e in evidence)
    if isinstance(evidence, str) and evidence.strip():
        item["evidence"] = evidence.strip()[:4000]
    fix = str(raw.get("recommendation") or raw.get("fix") or "").strip()
    if fix:
        item["fix"] = fix[:1000]
    status = raw.get("status")
    if raw.get("fixed") is True:
        status = "fixed"
    item["status"] = str(status).strip().lower() if status else "open"
    return item


def findings_blocks(data: Any) -> tuple[str, Blocks]:
    """Audit / review findings by severity.

    *data* is a list of findings, or a dict with ``findings`` (the
    ``security_scan`` JSON or ``run_security_scan`` result, or a ``code_review``
    filter result with ``kept``). Accepts both shapes:
    ``navin.review.schema.normalize_review_finding`` (``file_path``,
    ``start_line``, ``existing_code``) and ``navin.security.findings`` (``line``,
    ``malicious_input_example``). Optional ``status`` / ``fixed`` per finding.
    """
    title = "Audit findings"
    meta: dict[str, Any] = {}
    raw: Any = data
    if isinstance(data, dict):
        meta = data
        raw = data.get("findings") if isinstance(data.get("findings"), list) else data.get("kept")
        title = str(data.get("title") or ("Security audit" if data.get("kind") or data.get("tools") else title))
    items = [f for f in (_clean_finding(r) for r in (raw or []) if isinstance(r, dict)) if f]
    if not items:
        raise CanvasError(["findings: no findings to show (report a clean result in the reply instead)"])

    counts = Counter(f["severity"] for f in items)
    files = Counter(f.get("file", "(no file)") for f in items)
    fixed = sum(1 for f in items if f["status"] in {"fixed", "resolved", "done"})
    urgent = counts["critical"] + counts["high"]
    stats = [
        {"label": "Findings", "value": len(items)},
        {"label": "Critical + high", "value": urgent, "tone": "bad" if urgent else "good"},
        {"label": "Files affected", "value": len([k for k in files if k != "(no file)"])},
        {"label": "Fixed", "value": f"{fixed} / {len(items)}", "tone": "good" if fixed == len(items) else "neutral"},
    ]
    sevs = [s for s in _SEVERITIES if counts[s]]
    blocks: Blocks = [
        {"type": "stats", "items": stats},
        {"type": "bar_chart", "title": "Findings by severity", "x_label": "Severity", "y_label": "Findings",
         "unit": "count", "categories": sevs,
         "series": [{"name": "Findings", "values": [counts[s] for s in sevs]}],
         "caption": f"Source: {meta.get('source') or meta.get('kind') or 'code review'} findings, deduplicated."},
    ]
    tools = meta.get("tools")
    if isinstance(tools, list) and tools:
        blocks.append({"type": "badges", "title": "Scanners", "items": [
            {"label": str(t.get("tool")), "tone": "good" if t.get("available") else "neutral"}
            for t in tools if isinstance(t, dict) and t.get("tool")
        ] or [{"label": "heuristics", "tone": "neutral"}]})
    blocks.append({"type": "findings", "title": "Findings", "items": items})
    rank = {s: i for i, s in enumerate(_SEVERITIES)}
    by_file: dict[str, dict[str, Any]] = {}
    for f in items:
        key = f.get("file", "(no file)")
        row = by_file.setdefault(key, {"file": key, "count": 0, "worst": "info", "open": 0})
        row["count"] += 1
        row["open"] += 0 if f["status"] in {"fixed", "resolved", "done"} else 1
        if rank[f["severity"]] < rank[row["worst"]]:
            row["worst"] = f["severity"]
    file_rows = [
        {**r, "worst": {"badge": r["worst"], "tone": {"critical": "bad", "high": "bad", "medium": "warn", "low": "accent"}.get(r["worst"], "neutral"), "sort": 5 - rank[r["worst"]]}}
        for r in by_file.values()
    ]
    blocks.append({"type": "table", "title": "By file", "columns": [
        {"key": "file", "label": "File", "mono": True},
        {"key": "count", "label": "Findings"},
        {"key": "open", "label": "Open"},
        {"key": "worst", "label": "Highest severity"},
    ], "rows": file_rows, "sort": {"key": "count", "dir": "desc"}})
    return title, blocks


# --------------------------------------------------------------------------
# c. PR / CI status board
# --------------------------------------------------------------------------

_PASSING = {"success", "successful", "skipped", "neutral", "manual", "passed"}
_FAILING = {"failure", "failed", "error", "errored", "cancelled", "canceled", "timed_out", "action_required"}


def pr_status_blocks(data: dict[str, Any]) -> tuple[str, Blocks]:
    """PR checks, review threads and merge readiness.

    *data* is ``{"pr": forge pr_view result (``{"request": {...}}`` or the
    request), "ci": forge ci_status result (``{"branch", "checks": {state,
    total, passing, failing, pending, items[{name, state, url}]}}``),
    "threads"?: [{path, line, resolved, author?, body?}],
    "comments"?: pr_comments plan ``{comments: [...]}``}``. Navin has no review
    thread reader yet, so ``threads`` is whatever the caller collected.
    """
    pr = data.get("pr") or {}
    if isinstance(pr.get("request"), dict):
        pr = pr["request"]
    ci = data.get("ci") or {}
    checks = ci.get("checks") if isinstance(ci.get("checks"), dict) else ci
    items = [c for c in (checks.get("items") or []) if isinstance(c, dict) and c.get("name")]
    threads = [t for t in (data.get("threads") or []) if isinstance(t, dict)]
    if not pr and not items and not threads:
        raise CanvasError(["pr_status: pass pr (forge pr_view), ci (forge ci_status) or threads"])

    def state_of(c: dict[str, Any]) -> str:
        s = str(c.get("state") or "").lower()
        return "pass" if s in _PASSING else "fail" if s in _FAILING else "pending"

    passing = sum(1 for c in items if state_of(c) == "pass")
    failing = sum(1 for c in items if state_of(c) == "fail")
    pending = len(items) - passing - failing
    unresolved = [t for t in threads if not t.get("resolved")]
    pr_state = str(pr.get("state") or "unknown").lower()
    if pr.get("merged"):
        pr_state = "merged"
    blockers: list[str] = []
    if pr.get("is_draft"):
        blockers.append("draft")
    if failing:
        blockers.append(f"{failing} failing check(s)")
    if pending:
        blockers.append(f"{pending} pending check(s)")
    if unresolved:
        blockers.append(f"{len(unresolved)} unresolved thread(s)")
    if pr_state == "merged":
        readiness = ("merged", "good")
    elif pr_state != "open" and pr:
        readiness = (pr_state, "neutral")
    elif blockers:
        readiness = ("blocked", "bad" if failing or unresolved else "warn")
    else:
        readiness = ("ready to merge", "good")

    number = pr.get("number")
    title = f"PR #{number}: {pr.get('title')}" if number and pr.get("title") else "PR status"
    stats: list[dict[str, Any]] = [
        {"label": "Merge readiness", "value": readiness[0], "tone": readiness[1],
         "hint": ", ".join(blockers) if blockers and readiness[0] == "blocked" else None},
    ]
    if items:
        stats.append({"label": "Checks passing", "value": f"{passing} / {len(items)}",
                      "tone": "good" if passing == len(items) else "neutral"})
        stats.append({"label": "Failing checks", "value": failing, "tone": "bad" if failing else "good"})
    if threads:
        stats.append({"label": "Unresolved threads", "value": len(unresolved), "tone": "warn" if unresolved else "good"})
    stats = [{k: v for k, v in s.items() if v is not None} for s in stats]
    blocks: Blocks = [{"type": "stats", "items": stats}]
    badges = [{"label": pr_state, "tone": readiness[1] if pr_state in {"merged", "open"} else "neutral"}]
    if pr.get("is_draft"):
        badges.append({"label": "draft", "tone": "warn"})
    if pr.get("base") and pr.get("head"):
        badges.append({"label": f"{pr['head']} into {pr['base']}", "tone": "neutral"})
    if ci.get("branch"):
        badges.append({"label": f"CI on {ci['branch']}", "tone": "neutral"})
    if pr:
        blocks.append({"type": "badges", "items": badges})
    if items:
        blocks.append({"type": "bar_chart", "orientation": "horizontal", "title": "Checks by state",
                       "x_label": "State", "y_label": "Checks", "unit": "count",
                       "categories": ["failing", "pending", "passing"],
                       "series": [{"name": "Checks", "values": [failing, pending, passing]}],
                       "caption": f"Source: forge ci_status for {ci.get('branch') or 'the PR head'}, latest run per check."})
        tone = {"pass": "good", "fail": "bad", "pending": "warn"}
        order = {"fail": 0, "pending": 1, "pass": 2}
        blocks.append({"type": "table", "title": "Checks", "columns": [
            {"key": "name", "label": "Check"},
            {"key": "state", "label": "State"},
            {"key": "raw", "label": "Reported as", "mono": True},
        ], "rows": [
            {"name": str(c["name"]), "state": {"badge": state_of(c), "tone": tone[state_of(c)], "sort": order[state_of(c)]},
             "raw": str(c.get("state") or "")}
            for c in items
        ], "sort": {"key": "state", "dir": "asc"},
            "caption": f"{len(items)} of {checks.get('total') or len(items)} checks shown (forge ci_status caps at 40)."})
    if unresolved:
        blocks.append({"type": "findings", "title": "Unresolved review threads", "items": [
            {"severity": "medium", "title": str(t.get("body") or "Review thread").strip()[:200],
             **({"file": str(t["path"])} if t.get("path") else {}),
             **({"line": t["line"]} if isinstance(t.get("line"), int) and t["line"] > 0 else {}),
             "status": "unresolved",
             **({"detail": f"by {t['author']}"} if t.get("author") else {})}
            for t in unresolved
        ]})
    comments = (data.get("comments") or {}).get("comments") if isinstance(data.get("comments"), dict) else None
    if comments:
        blocks.append({"type": "table", "title": "Planned review comments", "columns": [
            {"key": "path", "label": "File", "mono": True},
            {"key": "line", "label": "Line"},
            {"key": "severity", "label": "Severity"},
            {"key": "summary", "label": "Summary"},
        ], "rows": [{k: c.get(k) for k in ("path", "line", "severity", "summary")} for c in comments if isinstance(c, dict)]})
    return title, blocks


# --------------------------------------------------------------------------
# d. Project board and timeline
# --------------------------------------------------------------------------


def board_blocks(payload: dict[str, Any]) -> tuple[str, Blocks]:
    """Tasks by status, critical path and milestones.

    *payload* is ``navin.board.store.ProjectBoardStore(project).payload()``:
    ``tasks``, ``milestones``, ``statuses``, ``plan`` (``critical_path``,
    ``blocked``, ``ready_queue``) and ``milestone_progress``.
    """
    tasks = [t for t in payload.get("tasks") or [] if isinstance(t, dict) and t.get("id")]
    if not tasks:
        raise CanvasError(["board: the project board has no tasks"])
    statuses = list(payload.get("statuses") or [])
    counts = Counter(str(t.get("status") or "backlog") for t in tasks)
    for s in counts:
        if s not in statuses:
            statuses.append(s)
    plan = payload.get("plan") or {}
    critical = [i for i in plan.get("critical_path") or [] if isinstance(i, str)]
    blocked = plan.get("blocked") or []
    by_id = {t["id"]: t for t in tasks}
    milestones = [m for m in payload.get("milestones") or [] if isinstance(m, dict) and m.get("id")]
    progress = payload.get("milestone_progress") or {}
    done = counts.get("done", 0)
    open_count = len(tasks) - done - counts.get("cancelled", 0)

    blocks: Blocks = [{"type": "stats", "items": [
        {"label": "Open tasks", "value": open_count},
        {"label": "Done", "value": f"{done} / {len(tasks)}", "tone": "good" if done else "neutral"},
        {"label": "In progress", "value": counts.get("in_progress", 0)},
        {"label": "Blocked", "value": len(blocked) or counts.get("blocked", 0),
         "tone": "bad" if (blocked or counts.get("blocked")) else "good"},
        {"label": "Critical path", "value": len(critical), "unit": "tasks"},
    ]}]
    shown = [s for s in statuses if counts.get(s)]
    blocks.append({"type": "bar_chart", "title": "Tasks by status", "x_label": "Status", "y_label": "Tasks",
                   "unit": "count", "categories": shown,
                   "series": [{"name": "Tasks", "values": [counts[s] for s in shown]}],
                   "caption": f"Source: project board ({len(tasks)} tasks), as of the last board update."})
    if critical:
        blocks.append({"type": "timeline", "title": "Critical path", "items": [
            {"date": str(by_id[i].get("updated_at") or "")[:10] or "pending",
             "label": str(by_id[i].get("title") or i), "status": str(by_id[i].get("status") or ""),
             "critical": True, "detail": f"{i}" + (f", after {', '.join(by_id[i].get('depends_on') or [])}" if by_id[i].get("depends_on") else "")}
            for i in critical if i in by_id
        ], "caption": "Longest dependency chain among open tasks; a delay here delays the board."})
    if milestones:
        ms = sorted(milestones, key=lambda m: (m.get("target_date") is None, str(m.get("target_date") or "")))
        items = []
        for m in ms:
            p = progress.get(m["id"]) or {}
            detail = f"{p.get('done', 0)} of {p.get('total', 0)} tasks done" if p.get("total") else "no tasks yet"
            if p.get("blocked"):
                detail += f", {p['blocked']} blocked"
            date = str(m["target_date"])[:10] if m.get("target_date") else "unscheduled"
            items.append({"date": date, "label": str(m.get("title") or m["id"]),
                          "status": str(m.get("status") or "planned"), "milestone": True, "detail": detail})
        blocks.append({"type": "timeline", "title": "Milestones", "items": items})
    tone = {"done": "good", "blocked": "bad", "in_progress": "accent", "review": "info", "audit": "info", "fix": "warn"}
    ms_title = {m["id"]: str(m.get("title") or m["id"]) for m in milestones}
    blocks.append({"type": "table", "title": "Tasks", "columns": [
        {"key": "id", "label": "ID", "mono": True},
        {"key": "title", "label": "Title"},
        {"key": "status", "label": "Status"},
        {"key": "priority", "label": "Priority"},
        {"key": "milestone", "label": "Milestone"},
        {"key": "depends_on", "label": "Depends on", "mono": True},
    ], "rows": [{
        "id": t["id"], "title": str(t.get("title") or ""),
        "status": {"badge": str(t.get("status") or "backlog"), "tone": tone.get(str(t.get("status")), "neutral"),
                   "sort": statuses.index(t.get("status")) if t.get("status") in statuses else 0},
        "priority": str(t.get("priority") or ""),
        "milestone": ms_title.get(t.get("milestone_id"), ""),
        "depends_on": ", ".join(t.get("depends_on") or []),
    } for t in tasks], "sort": {"key": "status", "dir": "asc"}})
    name = Path(str(payload.get("project_path") or "")).name
    return (f"Project board: {name}" if name else "Project board"), blocks


# --------------------------------------------------------------------------
# e. Token usage and cost
# --------------------------------------------------------------------------


def _cost(row: dict[str, Any], price: dict[str, Any] | None) -> float | None:
    if not isinstance(price, dict):
        return None
    total = 0.0
    for key, field in (("prompt", "prompt_tokens"), ("completion", "completion_tokens"), ("cached", "cached_tokens")):
        rate = _num(price.get(key))
        tokens = _num(row.get(field)) or 0.0
        if key == "prompt" and _num(price.get("cached")) is not None:
            tokens = max(0.0, tokens - (_num(row.get("cached_tokens")) or 0.0))
        if rate is not None:
            total += tokens / 1_000_000 * rate
    return round(total, 4)


def token_usage_blocks(payload: dict[str, Any], *, window_days: int = 30) -> tuple[str, Blocks]:
    """Token usage by day and by model.

    *payload* is ``navin.webui.token_usage.token_usage_payload()`` (what
    ``GET /api/settings/usage`` serves): ``days[{date, prompt_tokens,
    completion_tokens, cached_tokens, total_tokens, requests, ...}]``,
    ``models_month[{model, requests, prompt_tokens, completion_tokens,
    cached_tokens, total_tokens}]``, ``total_tokens_30d``, ``requests_30d``,
    ``active_days_30d``, ``current_streak_days``, ``peak_day_tokens``, ``month``.
    Navin records tokens, not money: pass ``prices`` ``{model: {prompt,
    completion, cached}}`` in USD per million tokens to add estimated cost.
    """
    days = [d for d in payload.get("days") or [] if isinstance(d, dict) and d.get("date")]
    days = sorted(days, key=lambda d: d["date"])[-window_days:]
    models = [m for m in payload.get("models_month") or [] if isinstance(m, dict) and m.get("model")]
    if not any((d.get("total_tokens") or 0) for d in days) and not models:
        raise CanvasError(["token_usage: no recorded token usage yet"])
    prices = payload.get("prices") if isinstance(payload.get("prices"), dict) else {}
    month = str(payload.get("month") or "this month")
    stats: list[dict[str, Any]] = [
        {"label": f"Tokens, last {window_days} days", "value": int(payload.get("total_tokens_30d") or sum(d.get("total_tokens") or 0 for d in days))},
        {"label": f"Requests, last {window_days} days", "value": int(payload.get("requests_30d") or sum(d.get("requests") or 0 for d in days))},
        {"label": "Active days", "value": int(payload.get("active_days_30d") or 0), "unit": f"/ {window_days}"},
        {"label": "Peak day", "value": int(payload.get("peak_day_tokens") or 0), "unit": "tokens"},
        {"label": "Current streak", "value": int(payload.get("current_streak_days") or 0), "unit": "days"},
    ]
    model_rows = []
    month_total = sum(int(m.get("total_tokens") or 0) for m in models) or 1
    total_cost = 0.0
    priced = False
    for m in models:
        row = {k: m.get(k) for k in ("model", "requests", "prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens")}
        row["share"] = round((m.get("total_tokens") or 0) / month_total * 100, 1)
        cost = _cost(m, prices.get(m["model"]))
        if cost is not None:
            row["cost"] = cost
            total_cost += cost
            priced = True
        model_rows.append(row)
    if priced:
        stats.append({"label": f"Estimated cost, {month}", "value": round(total_cost, 2), "unit": "USD",
                      "hint": "from the prices passed in, not billed amounts"})
    blocks: Blocks = [{"type": "stats", "items": stats}]
    if days:
        blocks.append({"type": "line_chart", "title": "Daily tokens", "x_label": "Day (local time)",
                       "y_label": "Tokens", "unit": "tokens", "x": [d["date"][5:] for d in days],
                       "series": [
                           {"name": "Prompt", "values": [int(d.get("prompt_tokens") or 0) for d in days]},
                           {"name": "Completion", "values": [int(d.get("completion_tokens") or 0) for d in days]},
                           {"name": "Cached", "values": [int(d.get("cached_tokens") or 0) for d in days], "role": "baseline"},
                       ],
                       "caption": f"Source: Navin token usage log, {days[0]['date']} to {days[-1]['date']}. Provider counts where reported, estimates otherwise."})
    if models:
        top = models[:12]
        blocks.append({"type": "bar_chart", "orientation": "horizontal", "title": f"Tokens by model, {month}",
                       "x_label": "Model", "y_label": "Tokens", "unit": "tokens",
                       "categories": [str(m["model"]) for m in top],
                       "series": [{"name": "Total tokens", "values": [int(m.get("total_tokens") or 0) for m in top]}],
                       "caption": f"Source: Navin token usage log, calendar month {month}" + (f", top 12 of {len(models)} models." if len(models) > 12 else ".")})
        columns: list[dict[str, Any]] = [
            {"key": "model", "label": "Model", "mono": True},
            {"key": "requests", "label": "Requests"},
            {"key": "prompt_tokens", "label": "Prompt", "unit": "tokens"},
            {"key": "completion_tokens", "label": "Completion", "unit": "tokens"},
            {"key": "cached_tokens", "label": "Cached", "unit": "tokens"},
            {"key": "total_tokens", "label": "Total", "unit": "tokens"},
            {"key": "share", "label": "Share", "unit": "%"},
        ]
        if priced:
            columns.append({"key": "cost", "label": "Est. cost", "unit": "USD"})
        blocks.append({"type": "table", "title": "Models this month", "columns": columns, "rows": model_rows,
                       "sort": {"key": "total_tokens", "dir": "desc"}})
    return "Token usage", blocks


# --------------------------------------------------------------------------
# f. Marketing video QA report
# --------------------------------------------------------------------------


def _ts(seconds: Any) -> str:
    value = _num(seconds)
    if value is None:
        return ""
    minutes, secs = divmod(value, 60)
    return f"{int(minutes)}:{secs:05.2f}"


def video_qa_blocks(report: dict[str, Any]) -> tuple[str, Blocks]:
    """Marketing video QA (``navin.marketing.video_qa.run_video_qa`` /
    ``video-qa.json``): verdict, scores, loudness, timed findings, events, frames."""
    if not isinstance(report, dict) or not report.get("verdict"):
        raise CanvasError(["video_qa: not a video-qa.json report"])
    if report["verdict"] == "SKIPPED":
        raise CanvasError([f"video_qa: QA was skipped ({report.get('reason') or 'no reason given'})"])
    video = report.get("video") or {}
    scores = report.get("scores") or {}
    policy = report.get("policy") or {}
    events = report.get("events") or {}
    loud = events.get("loudness") or {}
    verdict = str(report["verdict"])
    vtone = {"PASS": "good", "WARN": "warn", "BLOCK": "bad"}.get(verdict, "neutral")
    name = Path(str(video.get("path") or "video")).name

    stats: list[dict[str, Any]] = [{"label": "Verdict", "value": verdict, "tone": vtone}]
    if _num(scores.get("overall")) is not None:
        stats.append({"label": "Overall score", "value": scores["overall"], "unit": "/ 100",
                      "hint": f"pass at {policy.get('pass_score', 85)}"})
    if _num(video.get("duration")) is not None:
        stats.append({"label": "Duration", "value": round(video["duration"], 2), "unit": "s"})
    integrated = _num(loud.get("integrated"))
    if integrated is not None:
        target = _num(policy.get("target_lufs"))
        delta = round(integrated - target, 1) if target is not None else None
        tol = _num(policy.get("lufs_tolerance")) or 2.0
        stats.append({"label": "Integrated loudness", "value": round(integrated, 1), "unit": "LUFS",
                      **({"delta": delta, "delta_unit": " LU vs target", "tone": "good" if abs(delta) <= tol else "warn"} if delta is not None else {})})
    findings = [f for f in report.get("findings") or [] if isinstance(f, dict)]
    stats.append({"label": "Scenes to fix", "value": len(report.get("scenes_to_fix") or {}),
                  "tone": "warn" if report.get("scenes_to_fix") else "good"})
    blocks: Blocks = [{"type": "stats", "items": stats}]
    badges = [{"label": verdict, "tone": vtone}]
    if video.get("width") and video.get("height"):
        badges.append({"label": f"{video['width']}x{video['height']}", "tone": "neutral"})
    if video.get("fps"):
        badges.append({"label": f"{video['fps']:g} fps" if _num(video['fps']) is not None else f"{video['fps']} fps", "tone": "neutral"})
    for key in ("video_codec", "audio_codec"):
        if video.get(key):
            badges.append({"label": str(video[key]), "tone": "neutral"})
    if video.get("has_audio") is False:
        badges.append({"label": "no audio track", "tone": "warn"})
    blocks.append({"type": "badges", "items": badges})

    score_keys = [k for k in ("objective", "vision", "overall") if _num(scores.get(k)) is not None]
    if score_keys:
        blocks.append({"type": "bar_chart", "title": "QA scores", "x_label": "Score", "y_label": "Score",
                       "unit": "points of 100", "categories": score_keys,
                       "series": [{"name": "Score", "values": [scores[k] for k in score_keys]}],
                       "caption": f"Pass at {policy.get('pass_score', 85)}, warn below {policy.get('warn_score', 65)}. Vision by {(report.get('provider') or {}).get('model') or 'the configured model'}."})

    sev = {"BLOCK": "high", "WARN": "medium", "PASS": "info"}
    issues = [f for f in findings if str(f.get("status")).upper() in {"BLOCK", "WARN"}]
    if issues:
        items = []
        for f in issues:
            loc = []
            if _num(f.get("timestamp")) is not None:
                loc.append(_ts(f["timestamp"]) + (f" - {_ts(f['end'])}" if _num(f.get("end")) is not None else ""))
            if f.get("scene_index") is not None:
                loc.append(f"scene {f['scene_index']}")
            ev = f.get("evidence")
            item = {"severity": sev[str(f.get("status")).upper()], "title": str(f.get("check") or "check").replace("_", " "),
                    "status": str(f.get("status")).lower(),
                    **({"location": ", ".join(loc)} if loc else {}),
                    **({"evidence": "\n".join(str(e) for e in ev)} if isinstance(ev, list) and ev else {}),
                    **({"fix": str(f["recommendation"])} if f.get("recommendation") else {}),
                    **({"detail": f"{f.get('source')} check, score {f.get('score')}"} if f.get("source") else {})}
            items.append(item)
        blocks.append({"type": "findings", "title": "Issues", "items": items})
    if findings:
        blocks.append({"type": "table", "title": "All checks", "columns": [
            {"key": "check", "label": "Check", "mono": True},
            {"key": "source", "label": "Source"},
            {"key": "status", "label": "Status"},
            {"key": "score", "label": "Score"},
            {"key": "at", "label": "At", "mono": True},
        ], "rows": [{
            "check": str(f.get("check") or ""), "source": str(f.get("source") or ""),
            "status": {"badge": str(f.get("status") or ""), "tone": {"PASS": "good", "WARN": "warn", "BLOCK": "bad"}.get(str(f.get("status")).upper(), "neutral")},
            "score": f.get("score") if _num(f.get("score")) is not None else None,
            "at": _ts(f.get("timestamp")),
        } for f in findings], "sort": {"key": "score", "dir": "asc"}})

    timeline = []
    for kind, label in (("black", "Black frames"), ("freezes", "Frozen frames"), ("silences", "Silence")):
        for e in events.get(kind) or []:
            if isinstance(e, dict) and _num(e.get("start")) is not None:
                timeline.append((e["start"], {"date": _ts(e["start"]), "end": _ts(e.get("end")) or None, "label": label,
                                              "detail": f"{_num(e.get('duration')) or 0:.2f} s"}))
    for f in report.get("frames") or []:
        if isinstance(f, dict) and _num(f.get("timestamp")) is not None:
            timeline.append((f["timestamp"], {"date": _ts(f["timestamp"]), "label": "Frame sampled",
                                              "detail": f"scene {f.get('scene_index')}" if f.get("scene_index") is not None else Path(str(f.get("path") or "")).name}))
    if timeline:
        timeline.sort(key=lambda p: p[0])
        blocks.append({"type": "timeline", "title": "Events and sampled frames",
                       "items": [{k: v for k, v in item.items() if v} for _, item in timeline],
                       "caption": "Times are minutes:seconds from the start of the video."})
    peak = _num(loud.get("true_peak"))
    if integrated is not None or peak is not None:
        rows = []
        if integrated is not None:
            rows.append({"measure": "Integrated", "value": round(integrated, 1), "unit": "LUFS", "target": f"{policy.get('target_lufs', -14)} +/- {policy.get('lufs_tolerance', 2)}"})
        if _num(loud.get("lra")) is not None:
            rows.append({"measure": "Loudness range", "value": round(loud["lra"], 1), "unit": "LU", "target": "-"})
        if peak is not None:
            limit = _num(policy.get("max_true_peak_db"))
            rows.append({"measure": "True peak", "value": round(peak, 1), "unit": "dBTP",
                         "target": f"<= {limit if limit is not None else -1}",
                         })
        blocks.append({"type": "table", "title": "Loudness", "columns": [
            {"key": "measure", "label": "Measure"}, {"key": "value", "label": "Value", "align": "right"},
            {"key": "unit", "label": "Unit"}, {"key": "target", "label": "Target"},
        ], "rows": rows})
    return f"Video QA: {name}", blocks


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

TEMPLATES: dict[str, Callable[[Any], tuple[str, Blocks]]] = {
    "benchmark": benchmark_blocks,
    "findings": findings_blocks,
    "pr_status": pr_status_blocks,
    "board": board_blocks,
    "token_usage": token_usage_blocks,
    "video_qa": video_qa_blocks,
}


def load_template_data(name: str, data: Any, *, workspace: str | None = None) -> Any:
    """Return *data*, or Navin's local data for templates that can read it."""
    if data not in (None, {}, []):
        return data
    if name == "token_usage":
        from navin.webui.token_usage import token_usage_payload

        return token_usage_payload()
    if name == "board":
        from navin.board.store import ProjectBoardStore

        return ProjectBoardStore(Path(workspace or Path.cwd())).payload(activity_limit=0)
    raise CanvasError([f"template {name!r} needs data (see the canvas skill for its shape)"])


def build_template(name: str, data: Any = None, *, workspace: str | None = None) -> tuple[str, Blocks]:
    """Run template *name* and return ``(title, blocks)``."""
    fn = TEMPLATES.get(name)
    if fn is None:
        raise CanvasError([f"unknown template {name!r} (allowed: {', '.join(TEMPLATES)})"])
    return fn(load_template_data(name, data, workspace=workspace))
