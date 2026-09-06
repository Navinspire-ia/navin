# Expert tools (Review / Security / Debug)

This page documents the agent tools that power **Review**, **Security**, and **Debug** end to end: scope, scan, filters, HTML reports, File Preview, DebugMCP, and PR comments.

For UI posture and user flows, see [Modes](./modes.md). For slash commands, see [Commands](./commands.md).

## Overview

| Tool | Mode | Role |
| --- | --- | --- |
| `code_review` | Review (`/inspect`) | Change-set scope, precision filter, `review-report-*.html` |
| `security_scan` | Security (`/fortify`, `/probe`, …) | Structured AppSec scan + optional `security-report-*.html` |
| `debug_repair` | Debug (`/debug`) | DebugMCP status, isolated branch, `debug-report-*.html` |
| `pr_comments` | Review / Security (optional) | Preview then post inline GitHub PR comments via `gh` |
| `set_composer_mode` | All | Sync Mode menu + composer accent (WebUI) |
| `open_file_preview` | All (studios) | Open a file in File Preview (fallback; expert reports also auto-open) |

Auto-discovery: these tools load with the rest of the agent catalog (`navin/agent/tools/`).

## Automatic File Preview

When an expert report is written **from the WebUI** (websocket channel):

1. The tool emits a `file_preview_open_request` event with the HTML path.
2. The **File Preview** panel opens (Download HTML / Export PDF).
3. The tool response includes `preview_opened: true` (otherwise `false` outside WebUI, e.g. CLI).

Names also recognized on the UI side (`file_edit`):

- `review-report-*.html`
- `security-report-*.html`
- `debug-report-*.html`

(plus studios: `risklens-`, `seo-`, `marketing-`, `campaign-`, `leads-`, `scrape-`).

On CLI (`navin agent`), the report path is returned in the tool output; File Preview does not exist on that channel.

---

## `code_review`

**Actions:**

| `action` | Effect |
| --- | --- |
| `scope` | Change-set to review: dirty git, tracked files if clean tree, or filesystem walk outside git; OCR gates (extensions / excludes); `.navin/review-rules.json` when present |
| `filter` | Precision filter on `findings_json` (confidence, path/line, theoretical noise) |
| `report` | Filter then write `review-report-<timestamp>.html` and open File Preview in WebUI |

**Useful `report` params:** `findings_json`, `verdict` (`approve` / `request_changes` / `comment`), `effort` (1-5), `summary` (bullets).

**Python modules:** `navin/review/` (`scope`, `gates`, `rules`, `schema`, `report_html`).

---

## `security_scan`

**Kinds:** `secrets` | `sast` | `sca` | `quick` | `full`.

Always on: built-in heuristics (secrets, injection, XSS, weak JWT, CORS, `shell=True`, pickle, …) with a PoC sketch.

Optional: host CLIs when installed (`gitleaks`, `bandit`, `semgrep`, `npm audit`, `pip-audit`, …). Missing CLIs are reported honestly (`available: false`) without inventing findings.

`write_report=true` writes `security-report-<timestamp>.html` and opens File Preview in WebUI.

**Python modules:** `navin/security/` (`scan`, `fp_filter`, `poc`, `report_html`).

---

## `debug_repair`

| `action` | Effect |
| --- | --- |
| `mcp_status` | Real MCP handshake (`initialize` + `tools/list`) to DebugMCP (default `http://127.0.0.1:3001/mcp`, localhost only) |
| `start_branch` | Create isolated `navin/debug-*` branch (refuses non-git, in-progress merge/rebase/cherry-pick; preserves dirty tree) |
| `status` | Current branch |
| `report` | Write `debug-report-<timestamp>.html` (+ JSON twin) from `payload_json`; auto File Preview in WebUI |

**Typical `report` payload:** `signal`, `repro_steps`, `root_cause`, `hypotheses`, `before`, `after`, `stack`, `variables`, `ask_log`, `findings`, `latent_bugs`. Mis-typed fields (e.g. string instead of list) are coerced to avoid crashes.

If DebugMCP is down: `mcp_status` returns `reachable: false` + a clear hint; the agent should fall back to logs / `pdb` / temporary instrumentation.

**Python modules:** `navin/debug/` (`repair`, `mcp_client`, `mcp_status`).

---

## DebugMCP (preset `debugmcp`)

1. Install the **DebugMCP** extension (VS Code / Cursor) and open a debug-capable workspace.
2. Navin **auto-enables the `debugmcp` preset at startup** (`http://127.0.0.1:3001/mcp` in `tools.mcp_servers`) - no manual Apps toggle required. Opt out: `"autoEnableMcpPresets": false` under `tools`.
3. Under `/debug`, the agent calls `debug_repair(action=mcp_status)` then MCP tools (`add_breakpoint`, `start_debugging`, `list_variable_names`, `get_variables_values`, `evaluate_expression`, `step_*`, `continue_execution`, …).

Without the extension: Debug mode still works (repro + logs + branch + report), without live breakpoints. Once the extension is running, MCP reconnect can load the tools.

General MCP guide: [Configure MCP Tools](../../guides/configure-mcp-tools.md).

---

## `pr_comments` (optional)

| `action` | Effect |
| --- | --- |
| `preview` | Resolve PR + list `path:line` payloads (no network write) |
| `post` | Create a real GitHub review with inline comments (`kind=review` or `security`) - asks for approval |

**Requires:** `gh` installed and authenticated, an open PR whose files/lines match the diff, findings with `file_path` + `start_line` / `line`.

Without a PR or `gh`: `preview` stays honest (error / empty list); do not invent posted comments.

**Python module:** `navin/github/pr_comments.py`.

---

## Related skills

| Skill | Usage |
| --- | --- |
| `code-reviewer` | `/inspect` methodology |
| `security-auditor` | `/fortify` methodology |
| `debug-live` | `/debug` workflow + DebugMCP |
| `studio-html-report` | Expert HTML contract (Real example, Deliverables, Start with #N, print CSS) |

Details: [Skills](./skills.md).

---

## End-to-end flow

```text
WebUI → Mode Review / Security / Debug  (or Code Actions)
      → slash /inspect | /fortify | /debug
      → tools above
      → HTML report + File Preview open
      → you: "Start with #1"
      → Agent /forge to implement
```

```text
Optional Review/Security:
  pr_comments(preview) → pr_comments(post)  [PR + gh]
```

```text
Optional Debug:
  debug_repair(mcp_status) → DebugMCP MCP tools  [extension :3001]
                 └─ else logs / pdb
  debug_repair(start_branch) → minimal patch → report
```

---

## Related pages

- [Modes](./modes.md)
- [Commands](./commands.md)
- [Actions](./actions.md)
- [Skills](./skills.md)
- [Workbench](./workbench.md) - File Preview
- [Configure MCP Tools](../../guides/configure-mcp-tools.md)
- [Configuration](../../configuration.md) - model routing (`review`, `security`, `deep`)
