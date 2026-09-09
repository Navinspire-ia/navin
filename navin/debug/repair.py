# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Debug repair helpers: isolated branch + before/after notes + report HTML.

Patterns inspired by DebugMCP (inspect workflow), mini-swe-agent (repro first),
and ChatDBG-style conversational state questions - reimplemented under Navin.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navin.report.html_common import EXPERT_CSS, deliverables_table, esc
from navin.utils.proc import no_window_kwargs


def _git(root: Path, *args: str) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def start_repair_branch(root: Path, *, slug: str = "debug-repair") -> dict[str, Any]:
    """Create and checkout an isolated repair branch from HEAD.

    Preserves a dirty working tree when possible. Fails clearly if the repo is
    missing, mid-merge/rebase, or checkout cannot proceed.
    """
    root = root.expanduser().resolve(strict=False)
    code, inside, _ = _git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0 or inside.strip() != "true":
        return {"ok": False, "error": "Not a git repository", "branch": None}

    for lock_name, label in (
        (".git/MERGE_HEAD", "merge in progress"),
        (".git/rebase-merge", "rebase in progress"),
        (".git/rebase-apply", "rebase in progress"),
        (".git/CHERRY_PICK_HEAD", "cherry-pick in progress"),
    ):
        if (root / lock_name).exists():
            return {
                "ok": False,
                "error": f"Cannot create repair branch while {label}. Finish or abort first.",
                "branch": None,
            }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in slug)[:40] or "debug-repair"
    branch = f"navin/{safe}-{stamp}"

    _, dirty, _ = _git(root, "status", "--porcelain")
    dirty_lines = [line for line in dirty.splitlines() if line.strip()]

    # Create + switch in one step; dirty unconflicted trees are preserved.
    code, out, err = _git(root, "switch", "-c", branch)
    if code != 0:
        # Older git without `switch`: fall back to checkout -b
        code, out, err = _git(root, "checkout", "-b", branch)
    if code != 0:
        return {
            "ok": False,
            "error": (err or out or "git switch/checkout -b failed").strip()[:500],
            "branch": branch,
            "dirty_files": dirty_lines[:40],
            "hint": (
                "Resolve local conflicts or commit/stash conflicting changes, "
                "then retry debug_repair(action=start_branch)."
            ),
        }

    _, current, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return {
        "ok": True,
        "branch": current.strip() or branch,
        "dirty_preserved": len(dirty_lines),
        "dirty_files": dirty_lines[:40],
        "message": (
            f"On isolated branch {current.strip() or branch}"
            + (f" ({len(dirty_lines)} local change(s) preserved)" if dirty_lines else "")
        ),
    }


def current_branch(root: Path) -> str | None:
    code, out, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        return None
    name = out.strip()
    return name or None


def render_debug_report_html(
    *,
    root: str | Path,
    signal: str,
    repro_steps: str,
    root_cause: str,
    hypotheses: list[str] | None = None,
    before: str | None = None,
    after: str | None = None,
    branch: str | None = None,
    stack: list[str] | None = None,
    variables: dict[str, Any] | None = None,
    ask_log: list[dict[str, str]] | None = None,
    findings: list[dict[str, Any]] | None = None,
    latent_bugs: list[dict[str, Any]] | None = None,
    title: str = "Debug repair report",
    report_name: str | None = None,
    json_name: str | None = None,
) -> str:
    hyps = "".join(f"<li>{esc(h)}</li>" for h in (hypotheses or []))
    stack_html = "".join(f"<li><code>{esc(s)}</code></li>" for s in (stack or []))
    vars_html = ""
    if variables:
        rows = "".join(
            f"<tr><td><code>{esc(k)}</code></td><td><code>{esc(v)}</code></td></tr>"
            for k, v in list(variables.items())[:40]
        )
        vars_html = f"<table class='deliv'><tbody>{rows}</tbody></table>"
    asks = ""
    for item in ask_log or []:
        if not isinstance(item, dict):
            continue
        asks += (
            f"<div class='card'><p><strong>Q:</strong> {esc(item.get('q'))}</p>"
            f"<p><strong>A:</strong> {esc(item.get('a'))}</p></div>"
        )

    from navin.review.evidence import has_grounded_evidence, is_speculative_claim

    primary = []
    for f in list(findings or []):
        if not isinstance(f, dict):
            continue
        # Drop invented cards: need a real excerpt/stack/PoC, not hedging text.
        if is_speculative_claim(f) and not has_grounded_evidence(f, min_chars=6):
            continue
        if not has_grounded_evidence(f, min_chars=6) and not (
            f.get("file_path") or f.get("path")
        ):
            continue
        primary.append(f)
    if not primary and root_cause:
        primary = [
            {
                "severity": "high",
                "summary": "Root cause",
                "file_path": "",
                "start_line": None,
                "explanation": root_cause,
                "evidence": before or signal,
                "recommendation": "Apply the minimal fix on the repair branch and re-run the repro.",
            }
        ]
    latent = [f for f in list(latent_bugs or []) if isinstance(f, dict)]

    def _loc(item: dict[str, Any]) -> str:
        path = str(item.get("file_path") or item.get("path") or "")
        line = item.get("start_line", item.get("line"))
        if path and line:
            return f"{path}:{line}"
        return path or "runtime / stack"

    def _cards(items: list[dict[str, Any]], *, offset: int = 0) -> str:
        out = []
        for idx, item in enumerate(items, start=1 + offset):
            evidence = (
                item.get("evidence")
                or item.get("existing_code")
                or item.get("stack")
                or item.get("explanation")
                or ""
            )
            sev = str(item.get("severity") or "high")
            out.append(
                f"""
<article class="card">
  <header><span class="verdict">{esc(sev)}</span>
    <h3>#{idx} {esc(item.get('summary') or 'Finding')}</h3></header>
  <p class="muted"><code>{esc(_loc(item))}</code></p>
  <p><strong>Impact:</strong> {esc(item.get('explanation') or item.get('impact') or '')}</p>
  <div class="proof">
    <p><strong>Real example</strong></p>
    <pre>{esc(evidence) or esc(signal) or '(stack / failing output)'}</pre>
  </div>
  <p><strong>Fix:</strong> {esc(item.get('recommendation') or item.get('fix') or '')}</p>
</article>"""
            )
        return "".join(out)

    choices = []
    plan_source = primary + latent
    if not plan_source:
        plan_source = [
            {
                "summary": "Keep the fix on the repair branch and open a PR",
                "severity": "medium",
                "recommendation": "Push the repair branch and open a PR.",
                "explanation": "Primary fix verified by before/after.",
            },
            {
                "summary": "Expand tests for the regression",
                "severity": "medium",
                "recommendation": "Add a focused regression test for the failing signal.",
                "explanation": "Prevents silent reintroduction.",
            },
            {
                "summary": "Revert the branch if the after signal still fails",
                "severity": "low",
                "recommendation": "git switch back and discard the repair branch.",
                "explanation": "After output still red.",
            },
        ]
    for i, item in enumerate(plan_source[:8], start=1):
        choices.append(
            f"""
<div class="choice">
  <h3>#{i} - {esc(item.get('summary') or 'Next step')}</h3>
  <p><span class="verdict">{esc(item.get('severity') or 'medium')}</span>
    · effort M · {esc(_loc(item))}</p>
  <p><strong>Risk if delayed:</strong> {esc((item.get('explanation') or '')[:220])}</p>
  <p><strong>First step:</strong> {esc(item.get('recommendation') or 'Continue from evidence above.')}</p>
</div>"""
        )

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_file = report_name or "debug-report-*.html"
    side_file = json_name or "debug-report-*.json"
    deliv = deliverables_table(
        [
            (report_file, "HTML", "Debug repair report with evidence and plan"),
            (side_file, "JSON", "Machine-readable debug payload sidecar"),
        ]
    )
    sev_summary = (
        f"{len(primary)} primary finding(s) · {len(latent)} related latent bug(s)"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{esc(title)}</title>
<style>{EXPERT_CSS}</style>
</head>
<body>
<div class="wrap">
  <p class="muted">Navin Debug · repair workflow</p>
  <h1>{esc(title)}</h1>
  <p class="muted">{esc(root)} · {esc(ts)}
    {" · branch " + esc(branch) if branch else ""}</p>

  <h2>Executive summary</h2>
  <div class="panel">
    <p><strong>{esc(sev_summary)}</strong></p>
    <ul>
      <li>Signal locked and reproduced</li>
      <li>Root cause: {esc((root_cause or '')[:240] or 'see findings')}</li>
      <li>Before/after compared on the repair branch</li>
    </ul>
  </div>

  <h2>1. Signal</h2>
  <div class="panel"><pre>{esc(signal)}</pre></div>

  <h2>2. Reproduce</h2>
  <div class="panel"><pre>{esc(repro_steps)}</pre></div>

  <h2>3. Stack / breakpoints (REAL evidence)</h2>
  <div class="panel"><ul>{stack_html or '<li class="muted">No stack captured - use DebugMCP or pdb/evaluate if available.</li>'}</ul></div>

  <h2>4. Variables inspected</h2>
  <div class="panel">{vars_html or '<p class="muted">No variables recorded.</p>'}</div>

  <h2>5. Hypotheses (ChatDBG-style questions welcome)</h2>
  <div class="panel"><ul>{hyps or '<li class="muted">None listed</li>'}</ul>
  <p class="muted">Ask conversationally: why is X None? what called this frame? which value diverges from the test fixture?</p></div>

  <h2>6. Ask log</h2>
  {asks or '<p class="muted">No Q&amp;A recorded.</p>'}

  <h2>7. Findings</h2>
  {_cards(primary) or '<p class="muted">No structured findings.</p>'}

  <h2>8. Related latent bugs</h2>
  {_cards(latent, offset=len(primary)) or '<p class="muted">None recorded - call out nearby risks if found during inspect.</p>'}

  <h2>9. Root cause</h2>
  <div class="panel"><pre>{esc(root_cause)}</pre></div>

  <h2>10. Before / after (Real example)</h2>
  <div class="panel">
    <p><strong>Before</strong></p><pre>{esc(before or '(missing)')}</pre>
    <p><strong>After</strong></p><pre>{esc(after or '(missing)')}</pre>
  </div>

  <h2>Remediation plan - choose where to start</h2>
  <p class="muted">Reply in chat with <strong>Start with #N</strong> (e.g. Start with #1).</p>
  {''.join(choices)}

  <h2>Deliverables</h2>
  <div class="panel">{deliv}</div>

  <footer class="muted">Workflow: reproduce → breakpoint → inspect → hypothesize → isolated branch → tests → before/after → report.</footer>
</div>
</body>
</html>
"""


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item is not None and str(item).strip()]
    return [str(value)]


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        text = value.strip()
        return [{"summary": text, "explanation": text}] if text else []
    if isinstance(value, (list, tuple)):
        out: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str) and item.strip():
                out.append({"summary": item.strip(), "explanation": item.strip()})
        return out
    return []


def _as_str_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        return {"value": text} if text else {}
    if isinstance(value, (list, tuple)):
        return {str(i): v for i, v in enumerate(value)}
    return {"value": str(value)}


def write_debug_report(root: Path, payload: dict[str, Any]) -> Path:
    root = root.expanduser().resolve(strict=False)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"debug-report-{stamp}.html"
    json_name = f"debug-report-{stamp}.json"
    path = root / name
    path.write_text(
        render_debug_report_html(
            root=root,
            signal=str(payload.get("signal") or ""),
            repro_steps=str(payload.get("repro_steps") or ""),
            root_cause=str(payload.get("root_cause") or ""),
            hypotheses=_as_str_list(payload.get("hypotheses")),
            before=payload.get("before"),
            after=payload.get("after"),
            branch=payload.get("branch"),
            stack=_as_str_list(payload.get("stack")),
            variables=_as_str_dict(payload.get("variables")),
            ask_log=_as_dict_list(payload.get("ask_log")),
            findings=_as_dict_list(payload.get("findings")),
            latent_bugs=_as_dict_list(payload.get("latent_bugs")),
            title=str(payload.get("title") or "Debug repair report"),
            report_name=name,
            json_name=json_name,
        ),
        encoding="utf-8",
    )
    side = root / json_name
    side.write_text(json.dumps(payload, ensure_ascii=False, indent=2)[:200_000], encoding="utf-8")
    return path
