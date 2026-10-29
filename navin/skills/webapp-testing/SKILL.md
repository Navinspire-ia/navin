---
name: webapp-testing
description: Tests a local web application end to end - start the server under test, discover selectors in the rendered DOM, drive the pages with the built-in browser tool or Playwright scripts, capture console errors, failed requests and screenshots, then report findings. Use when the user asks to "test this web app", "check the login flow works", "find JavaScript errors on the page" or "verify the frontend after my change". In French "teste mon application web", "lance le serveur de dev puis les tests" or "vérifie le parcours de connexion".
metadata: {"navin":{"emoji":"🧪","category":"development"}}
---

# Web App Testing

Testing a local web application means proving, with evidence, that a page
renders, that its controls do what they claim and that nothing fails silently
in the console or on the network. The agent does the clicking; the user gets a
report with screenshots and exact failure locations.

Two instruments are available. The built-in `browser` tool (headless Chromium
with `navigate`, `snapshot`, `click`, `type`, `screenshot`, `console`,
`network`) is the fastest way to explore and run one-off interactive checks.
Playwright Python scripts are the right choice when the same flow must run
again (regression after each change, CI, several pages in a loop). The
`playwright-browser` skill covers the generic browser tool in depth; this skill
is about testing a project's own app.

## Workflow

### 1. Get the app running

- If the project has a dev server, start it with `start_app` or
  `open_preview`; both pick a free port and return the URL. Never hard-code
  the ports 5173-5176, 8765-8768 or 18790-18793: they belong to the Navin
  runtime.
- For a Playwright script that needs one or more servers alive only for the
  duration of the run, wrap it with `scripts/with_server.py`. Run
  `python3 scripts/with_server.py --help` first. Each `--server "cmd"` pairs
  with a `--port N`; the script waits until each port accepts a connection
  (default 30 s per server, `--timeout` to change), runs the command written
  after `--`, then stops every server (SIGTERM, then SIGKILL after 5 s) and
  exits with the inner command's exit code.

```bash
python3 scripts/with_server.py \
  --server "cd backend && python3 -m uvicorn app:api --port 8100" --port 8100 \
  --server "cd frontend && npm run dev -- --port 4300" --port 4300 \
  -- python3 tests/e2e/smoke.py http://127.0.0.1:4300
```

- Static HTML needs no server: open it with a `file://` URL. The file content
  can be read directly to find ids and labels before launching anything.

### 2. Reconnaissance before action

Never guess selectors. Load the page, wait for the network to go idle, then
look at what is actually rendered:

- With the `browser` tool: `navigate` to the URL, then `snapshot` to list the
  interactive elements with numeric refs, and `screenshot` to see the layout.
- With Playwright: run `examples/discover_elements.py <url>`. It prints JSON
  with every button, link, input and heading (text, role, id, name, type,
  visibility) plus the page title, so stable selectors can be chosen.

Prefer selectors in this order: `get_by_role` with an accessible name,
`get_by_label`, `get_by_text`, data-testid, then id, then CSS. Avoid
position-based or auto-generated class selectors; they break on the next
build.

### 3. Drive the flow

For each scenario (login, create an item, filter a list, submit a form):

- Perform the action with the discovered selector.
- Wait for an observable consequence (`wait_for_selector`, URL change, a
  toast, a row count), not for a fixed sleep. A `wait_for_timeout` is a last
  resort and must be justified in the report.
- Assert the outcome visibly: the text appeared, the input is empty, the
  table has N rows.
- Take a screenshot after each meaningful state (`page.screenshot(path=...,
  full_page=True)` or the `browser` tool's `screenshot`). Save screenshots in
  a dedicated folder such as `.navin/test-evidence/` and name them by step.

`examples/static_page.py <url>` shows a complete scripted flow against a
local HTML file: load, screenshot, fill a form, click submit, screenshot
again, print a JSON summary.

### 4. Capture what the user cannot see

A page can look fine while it is broken underneath. Always collect:

- Console messages of type `error` and `warning`, plus uncaught `pageerror`
  exceptions.
- Failed network requests: `requestfailed` events and responses with status
  400 or above, with method, URL and status.

`examples/console_errors.py <url>` wires all these listeners, loads the page,
optionally clicks an element given with `--click <selector>`, waits for the
network to settle and prints a JSON object with `console`, `page_errors` and
`failed_requests`. The `browser` tool offers the same data through its
`console` and `network` actions after a `navigate`.

Treat a 404 on a favicon as noise; treat a 500 on an API call or a React
"Cannot read properties of undefined" as a failure even when the UI looks
normal.

### 5. Report

Write the report for someone who was not watching. Include:

- Environment: URL, server command, browser, viewport, commit or branch.
- Steps performed, numbered, each with the selector used and the screenshot
  path.
- Findings: what passed, with the evidence that proves it.
- Failures: the step, the selector, the expected and observed state, the
  exact console line or failed request (method, URL, status), and the
  screenshot taken at that moment.
- Anything skipped and why (no test account, feature behind a flag, flaky
  third-party widget).

Never report a step as passed without evidence from the run. If a check could
not be executed, say so instead of inferring the result.

## Writing a Playwright script

Keep scripts small and parameterised by URL so they run under
`scripts/with_server.py` or against an already running server. Pattern:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Save").click()
    page.wait_for_selector("text=Saved")
    page.screenshot(path="evidence/after-save.png", full_page=True)
    browser.close()
```

Rules:

- Always headless, always close the browser, always exit non-zero on a
  failed assertion so `with_server.py` propagates it.
- Print machine-readable output (JSON) when another step will consume it;
  print prose only for the human.
- If `playwright` is not installed, the examples print a one-line
  installation hint (`pip install playwright && playwright install chromium`)
  and exit 2 instead of a stack trace. Do the same in new scripts.
- Do not store credentials in scripts; read them from environment variables
  or use the `browser` tool's secret references.

## Bundled files

- `scripts/with_server.py`: start servers, wait for ports, run a command,
  tear everything down, propagate the exit code.
- `examples/discover_elements.py`: list interactive elements of a page as
  JSON for selector discovery.
- `examples/console_errors.py`: capture console errors, page exceptions and
  failed requests as JSON.
- `examples/static_page.py`: scripted form flow against a `file://` page with
  screenshots.
- `examples/_playwright.py`: shared lazy import used by the three examples;
  prints the installation hint and exits 2 when Playwright is missing.

## Pitfalls

- Inspecting the DOM before `networkidle` on a client-rendered app returns an
  empty shell; wait first.
- A dev server that prints "ready" may still refuse connections for a second;
  `with_server.py` polls the port, do not rely on log lines.
- Headless viewports default to 1280x720; set the viewport explicitly when
  the layout is responsive, and test at least one narrow width.
- Screenshots of pages with animations can differ between runs; wait for the
  final state or disable animations with `reduced-motion` emulation.
