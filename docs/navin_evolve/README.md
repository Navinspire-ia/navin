# Evolve Engine

The Evolve Engine is a native Rust daemon that tests, breaks, diagnoses, fixes and optimizes your own projects, then proves each change with a signed certificate. The dashboard drives it from the Evolve tab of Navin Code in the WebUI (`#/code?panel=evolve`; the older `#/evolve` link still lands there); everything also works from the `navin-engine` CLI.

## Product principle

Break -> Diagnose -> Fix -> Prove -> Evolve -> Certify. The engine never edits your working tree directly: every experiment runs in an isolated Git worktree (`.navin/shadow/<run-id>`), and accepted changes land as a Git branch you merge explicitly. It does not trust the LLM's judgement either: only measurements decide, and every acceptance is backed by a cryptographic certificate.

## Quick start

1. Open the Evolve dashboard and select a project (it must be a Git repository with committed code).
2. Click "Start the daemon".
3. Leave the three fields empty (the engine detects your commands), pick **Prove**, and launch.
4. Read the robustness score (0-100) and the per-fault verdicts in the "Robustness proof" card. From there, run **Optimize** or **Evolve** to let the engine improve the code, and merge the resulting promotion if you like the numbers.

## The daemon

Each project has its own daemon: a background process listening on an ephemeral loopback port, published with an access token in `.navin/evolve/endpoint.json`. Nothing else on the machine can drive it without reading that file, and nothing outside the machine can reach it at all. The same transport is used on Windows, macOS and Linux.

A project opened from Windows at `\\wsl.localhost\<distro>\...` is a Linux project: the daemon is started inside the distribution, over `wsl.exe`, so proofs run with the toolchain the project actually uses. Its endpoint file is read straight through the Windows share, and WSL 2 forwards its loopback port to the host, so the dashboard reaches it like any other daemon. The distribution needs `navin-engine` on its own PATH.

- **Start**: click "Start the daemon" in the dashboard, or run `navin-engine daemon /path/to/project`.
- **Stop**: click the power icon next to the "daemon online" badge, or press Ctrl+C in its terminal.
- **Inspect**: `navin-engine status /path/to/project` prints the queue; `.navin/evolve/daemon.log` has the full output.
- It idles at roughly 5 MB of RAM, so leaving it running is fine. Jobs run one at a time; new launches queue behind the current one.
- **Stop a job**: "Stop this job" next to a queued or running job in the dashboard. A queued job is dropped before it starts; a running one is interrupted at its next step, its shadow worktree is destroyed and the app it started is killed with its whole process group, so nothing keeps running or occupies a port. Stopping a job leaves the daemon up and the next queued job starts right after.

## Launch fields (all three are optional)

| Field | Role | If left empty |
|---|---|---|
| Start command | How to launch your app (for example `npm run dev`, `flask run`, `cargo run`) | Resolved from the project, shown as "Auto: ..." in the field |
| Probe URL | The local HTTP address where your app answers once started. The engine sends requests to it to measure latency and test robustness | Observed: the engine boots the app once in a shadow and watches which port it opens |
| Test command | How to run your test suite (for example `pytest`, `npm test`) | The detected command is used; without one, the fix gate skips the project test step |

Explicit values always win over detection. The URL must be localhost: the engine refuses to benchmark or fault-inject anything else.

### How the start command is resolved

The engine builds a list of candidates, most credible first, and tries them until one serves traffic:

1. A `Procfile` `web:` line, the most explicit statement a project can make.
2. Every unit that knows how to run, outermost first, using its `start` script and falling back to its `dev` script. A command belonging to a sub-directory is run there (`cd web && npm run dev`).
3. A `Makefile` target named `run`, `start`, `serve`, `dev` or `up`.

A candidate that dies immediately (an uninstalled sub-project, a missing binary) hands over to the next one, so a monorepo whose outermost app is not usable still gets proved on the service that is. When you pass `--start` yourself, that command is the only one tried. If every candidate fails, the error lists each one with its reason and the log tail of the last attempt.

Per-unit detection covers Node (`package.json` scripts, then `server.js` / `index.js` / `app.js`), Python (Django `manage.py`, FastAPI and Flask entry points, using the project virtualenv when there is one), Rust, Go, Spring Boot, PHP (Laravel, Symfony, built-in server), Ruby (Rails, Rack) and .NET.

### How the probe URL is found

No static analysis can know for sure which port an app binds, so the engine measures instead of guessing. On the first launch it starts your app in a throwaway shadow, watches which TCP ports the process group opens (the kernel says which sockets belong to it, so a neighbouring server is never mistaken for yours), prefers a port the project pointed at, then one that answers HTTP, and stops the app. The answer is cached in `.navin/evolve/probe-url.json` for that exact start command, so later runs skip the extra boot. Delete that file to force a fresh discovery.

## Command examples

What the engine writes for you, and what to type when your project starts in an unusual way. Adapt the port to your app.

| Stack | Start command | Test command | Probe URL |
|---|---|---|---|
| Flask | `flask --app app run --port 5000` | `pytest` | `http://127.0.0.1:5000/` |
| Django | `python manage.py runserver 8000` | `python manage.py test` | `http://127.0.0.1:8000/` |
| FastAPI | `uvicorn main:app --port 8000` | `pytest` | `http://127.0.0.1:8000/` |
| Express / Node | `npm start` or `node server.js` | `npm test` | `http://127.0.0.1:3000/` |
| Next.js | `npm run dev` | `npm test` | `http://127.0.0.1:3000/` |
| Vite (React, Vue...) | `npm run dev` | `npx vitest run` | `http://127.0.0.1:5173/` |
| Rust | `cargo run` | `cargo test` | the port your app binds |
| Go | `go run .` | `go test ./...` | the port your app binds |
| Spring Boot | `./mvnw spring-boot:run` | `./mvnw test` | `http://127.0.0.1:8080/` |
| Custom script | `sh run.sh` | `sh test.sh` | whatever the script serves |

Tips:

- Commands run inside the shadow worktree, which only contains committed files. A virtualenv at the project root is used through its absolute path; other interpreters outside the repo need one too (for example `/home/me/venvs/app/bin/python -m pytest`).
- Installed dependencies are lent to the shadow rather than reinstalled: every `node_modules`, virtualenv (`.venv`, `venv`, `env`), Composer `vendor` and `vendor/bundle` your workspace already has is symlinked into the matching directory of the shadow. So the shadow starts in seconds, and a package your workspace never installed is still missing there - install it once in your workspace.
- The app must bind the port itself; the engine watches for it but cannot open it on your behalf.
- A wrapper script committed to the repo (`run.sh`, `test.sh`) is the most reliable option for multi-step startups (migrations, seeds, env vars).

## Operations

- **Prove**: starts your app in isolation, measures a baseline (latency, throughput), then injects real faults and reports which ones the app survives. Produces a robustness score (0-100) and a verdict (`pass`, `weak`, `fail`) saved under `.navin/proofs/`. By default a proof runs the last commit; the review panel's **Prove this change** button runs it with `dirty`, so the pending (uncommitted) agent edits are what gets proved - a proposed fix is validated under load before you accept or merge it.
- **Optimize (ASSE)**: asks the chosen LLM for several code variants targeting an objective (`p95` latency or `throughput`), benchmarks each one under the identical load, and promotes only the measured winner that clears every gate below. Report under `.navin/optimize/`.
- **Evolve**: full pipeline - prove, diagnose the weakest points from the logs, generate fixes with the LLM, verify each one, and record a promotion for every accepted fix. Report under `.navin/evolve-runs/`.

**Model**: the dropdown lists the text model presets from your Navin config, all providers included. It only matters for Optimize and Evolve (Prove never calls an LLM).

## Fault catalogue per profile

| Profile | Faults injected | Load window | Concurrency |
|---|---|---|---|
| `quick` | load, kill + recovery | 5 s | 16 |
| `standard` | load, malformed requests, connection flood (200 sockets), network chaos (latency, drops, resets via TCP proxy), kill + recovery | 12 s | 48 |
| `deep` | same as standard, harder (flood 512 sockets) | 30 s | 128 |

Pass criteria applied to every fault: error ratio at most 1%, no crash, and recovery within 15 seconds after a kill. Memory is bounded by the policy's `max_memory_mb` (rlimit on the child process).

## How a candidate is judged

A generated variant or fix is promoted only if **all** of the following hold, measured in its own shadow:

1. **Project tests**: a suite that was green must stay green (a suite already red before the patch is a pre-existing condition and is not held against the candidate).
2. **Business invariants**: every declared invariant must exit 0 under the same pre-existing-condition rule.
3. **Behavioural equivalence**: the differential verifier replays the same request vectors against baseline and candidate; responses must match (status and body hash) on every vector.
4. **Error ratio**: at most 1 percentage point worse than the baseline benchmark.
5. **Measured gain**: at least `min_gain` percent on the objective **and** statistically significant (see below). For fixes: the targeted finding is resolved, the robustness score did not drop, no new critical/high finding appeared, P95 did not regress beyond tolerance.
6. **Final proof**: the winner passes a fresh quick proof with a score at least equal to the baseline's.

Every rejection is recorded with its exact reason in the report and shown in the dashboard.

## Statistical confidence

A single benchmark proves nothing: the same code varies by several percent between runs. Every Optimize measurement therefore repeats the benchmark over several windows (3 by default, `--repeats` on the CLI, `repeats` in job params) after a short warmup, and reports mean ± standard deviation for P95 and RPS.

A variant only wins when its gain clears the combined noise of both distributions (Welch two-sample criterion at roughly 95% confidence). A "+3%" inside the noise band is marked "within noise" and never promoted; the engine trusts measurements, not luck.

## Business invariants

Tests prove the code; invariants prove the domain. Declare commands in `.navin/evolve.toml` that must exit 0 for any candidate to be promotable - order totals that add up, no duplicate payments, referential integrity:

```toml
[[invariants]]
name = "order_total_consistency"
command = "python verify_orders.py"

[[invariants]]
name = "no_duplicate_payments"
command = "python verify_payments.py"
timeout_secs = 60
```

They run inside every shadow (baseline, each variant, each fix candidate), after the test suite, with the shadow as working directory. Scripts referenced by relative path must therefore be **committed** to the repository. A candidate that breaks a previously green invariant is rejected, even if it is faster.

## Differential verifier

Before benchmarking, the engine crawls the baseline app for up to 24 GET vectors (`--diff-vectors`), fingerprints each response (status code + body hash), then replays the exact same vectors against every candidate. Any divergence - a changed status, a different body, a route that disappeared - disqualifies the candidate with the precise list of diverging vectors in the note. Set `--diff-vectors 0` to disable the check.

## Auto-run on commit

Enable the "Auto-run on commit" toggle in the launch card and the daemon watches your Git HEAD: each new commit automatically reruns the last launched operation (same kind, same parameters) in the background, while you keep working.

- The watcher checks HEAD every 5 seconds by reading two files under `.git/`; no process is spawned and the cost is negligible.
- If a job is already queued or running, the commit is skipped (no pile-up).
- The replayed operation is stored in `.navin/evolve/autorun.json`; launching a new operation from the dashboard updates it.
- If you enable the toggle before ever launching an operation, a quick robustness proof built from the detected commands is used.

## Promotions and certificates

Every accepted change produces a Git branch (`navin/evolve/...`), a promotion record and a signed certificate under `.navin/promotions/`. The certificate attests the finding, the candidate, the commit it was measured against, the scores before/after and the verdict; it carries an integrity checksum of its content and an **Ed25519 signature** from the engine's key (`.navin/evolve/identity.ed25519`, generated on first use, owner-only permissions). Tampering with any field invalidates the signature.

- **Verify**: re-checks checksum and signature (`authentic: true`).
- **Merge**: fast-forward merges the promotion branch into your current branch. Refused if the certificate does not verify, the working tree is dirty, or the merge is not a fast-forward.
- **Rollback**: reverts a merged promotion (or deletes the unmerged branch); records the rollback timestamp.

In `safe` mode (the default) nothing is ever auto-merged: the engine only prepares branches for you to review.

## Policy reference: `.navin/evolve.toml`

Everything is optional; a missing file means these defaults. A broken file is an error, never silently permissive.

```toml
[proof]
enabled = true
profile = "standard"        # quick | standard | deep

[evolve]
enabled = false             # opt in per project
mode = "safe"               # safe | trusted | autonomous

[evolve.allowed]            # remediation families the fix engine may touch
performance = true
memory = true
database = true
reliability = true
concurrency = false
security = false            # off: changes externally visible behaviour
dependencies = false

[evolve.promotion]
auto_merge = false          # safe mode never merges on its own

[evolve.resources]          # ceilings for child processes in shadows
max_cpu_percent = 15
max_memory_mb = 512
max_disk_mb = 4096
max_runtime_minutes = 30

[evolve.budget]
max_candidates = 100
max_runtime_minutes = 30
max_llm_cost_usd = 2.0

[evolve.generator]          # external LLM bridge (the engine never calls an LLM itself)
command = "python3 -m navin.evolve.bridge"
timeout_secs = 120

[[invariants]]              # optional, repeatable
name = "orders_consistent"
command = "python verify_orders.py"
timeout_secs = 120
```

## CLI reference

| Command | What it does |
|---|---|
| `navin-engine inspect <path>` | Discover how the project is built, tested and run |
| `navin-engine daemon <path>` | Run the daemon for a project |
| `navin-engine status <path>` | Job queue and daemon state |
| `navin-engine policy <path>` | Print the effective policy (defaults merged with evolve.toml) |
| `navin-engine baseline <path> --url ...` | Build time, startup, latency P50/P95/P99, CPU, RSS |
| `navin-engine proof <path> --url ... --profile quick` | Fault injection + robustness score |
| `navin-engine diagnose <path> ...` | Root-cause findings from a proof and its logs |
| `navin-engine fix <path> --finding ... --candidates c.json` | Verify candidate patches in shadows, propose the best |
| `navin-engine optimize <path> --url ... --repeats 3` | ASSE: benchmark variants, promote the measured winner |
| `navin-engine evolve <path> --url ...` | Full pipeline: prove, diagnose, fix, promote |
| `navin-engine promotions <path>` | List recorded promotions |
| `navin-engine verify-cert <path> --id ...` | Verify a certificate (integrity + signature) |
| `navin-engine merge <path> --id ...` / `rollback` | Apply or revert a promotion under policy |
| `navin-engine shadow list\|sweep <path>` | Inspect or clean leftover shadows |
| `navin-engine bench-loadgen` | Honest capacity of the built-in load generator |

## Artefacts on disk

| Path | Content |
|---|---|
| `.navin/evolve/endpoint.json` | Daemon loopback port and access token (0600 on Unix) |
| `.navin/evolve/daemon.log`, `daemon.pid` | Daemon output and PID |
| `.navin/evolve/autorun.json` | Operation replayed by auto-run on commit |
| `.navin/shadow/<run-id>/` | Isolated worktrees used by runs |
| `.navin/proofs/`, `.navin/diagnoses/` | Proof reports and findings |
| `.navin/optimize/`, `.navin/evolve-runs/`, `.navin/fixes/` | Campaign reports and fix proposals |
| `.navin/promotions/` | Promotion records and signed certificates |
| `.navin/evolve.toml` | Optional policy (see reference above) |

## Good to know

- **Shadows are built from Git HEAD.** Uncommitted changes are not part of the experiment: commit first, then launch.
- **Benchmarks are localhost-only** by design; the engine refuses any non-local URL.
- The app under test runs with a **memory rlimit** and bounded runtime; a runaway candidate kills its own shadow, never your machine.
- The chosen port must be free: the engine starts your app itself inside the shadow.
- Reports are plain JSON; everything in the dashboard can be read, diffed and archived from `.navin/`.
