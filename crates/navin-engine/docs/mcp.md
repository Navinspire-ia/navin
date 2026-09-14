# The MCP protocol, in detail

`navin-engine mcp` is a Model Context Protocol server on stdin/stdout. This
page is the wire-level reference: what each tool takes, what it returns, and
how a candidate patch is shaped. For getting the server registered in Cursor,
Claude Code, Codex and the rest, see [hosts.md](hosts.md).

Every tool accepts an optional `path` (the project root, defaulting to the
directory the host launched the server in). Results are compact summaries
plus the path of the full artefact written under `.navin/`, so the agent reads
the details only when it needs them.

## The nine tools

| Tool | Required arguments | Optional arguments |
| --- | --- | --- |
| `inspect_project` | - | `path` |
| `prove` | - | `profile`, `start`, `url` |
| `diagnose` | - | `profile`, `start`, `url` |
| `fix` | `finding`, `candidates` | `profile`, `test`, `start`, `url` |
| `optimize` | - | `candidates`, `objective`, `duration`, `repeats`, `concurrency`, `max_variants`, `min_gain`, `test`, `start`, `url` |
| `evolve` | - | `profile`, `max_findings`, `test`, `start`, `url` |
| `promotions` | - | `path` |
| `open_pull_request` | `id` | `path` |
| `verify_certificate` | `id` | `path` |

`profile` is `quick`, `standard` or `deep`. `objective` is `p95` or
`throughput`. `start`, `url` and `test` override detection and should stay
unset unless the engine picked the wrong program in a monorepo.

## A candidate patch

The same shape everywhere. Whole-file contents rather than a diff, because a
model producing valid unified-diff offsets is a coin flip; the engine computes
the diff itself once the file lands in the shadow.

```json
{
  "id": "cache-the-payload",
  "rationale": "the catalogue never changes between requests, so serialise it once",
  "family": "performance",
  "patch": {
    "kind": "files",
    "edits": [{ "path": "server.py", "contents": "...the whole new file..." }]
  }
}
```

`id` must be unique in the call and is what the report refers to. `family` is
free-form and only used for grouping. The finding id is stamped by the tool,
not by the model: which finding a run is about is the run's business.

The shell subcommands read the same objects from a JSON array, with one extra
field, since there is no call context to stamp: `"target_finding":
"crash.load"`, or `"optimize.p95"` for an optimize run.

## Calling `optimize`

```json
{ "method": "tools/call",
  "params": { "name": "optimize",
    "arguments": {
      "objective": "p95",
      "repeats": 2,
      "candidates": [ {
        "id": "cache-the-payload",
        "rationale": "the payload never changes between requests",
        "patch": { "kind": "files",
                   "edits": [ { "path": "server.py", "contents": "..." } ] } } ] } } }
```

What comes back, from the run recorded in the README:

```json
{ "objective": "p95",
  "baseline": { "p95_ms": 55.8, "rps": 277.5, "robustness_score": 100 },
  "baseline_p95_std_ms": 1.3,
  "variants": [
    { "candidate": "cache-the-payload", "gain_percent": 85.7,
      "significant": true, "behavior_equivalent": true, "tests_passed": true,
      "eligible": true, "note": "P95 8.0 ± 2.7 ms, 1502.2 ± 276.5 RPS over 2 windows",
      "diff": "diff --git a/server.py b/server.py\n..." },
    { "candidate": "drop-half-the-catalogue", "gain_percent": 85.7,
      "significant": true, "tests_passed": false, "eligible": false,
      "note": "project test suite fails with this variant" } ],
  "winner": "cache-the-payload", "winner_gain_percent": 85.7,
  "promotion_id": "promo-optimize-p95-1787059566",
  "promotion_outcome": "BranchOnly",
  "report_file": ".navin/optimize/<commit>.json",
  "report_md": ".navin/optimize/<commit>.md" }
```

The winner's `diff` travels with the summary; every candidate's diff, rejected
ones included, is in the artefact at `report_file`. Open `report_md` for the
same measurement as a page: baseline, table of candidates, winner diff, and
the commands to open the pull request. A rejected variant is exactly the one
worth reading.

## Calling `fix`

```json
{ "method": "tools/call",
  "params": { "name": "fix",
    "arguments": { "finding": "crash.load", "candidates": [ ... ] } } }
```

The reply carries one entry per attempt with the before/after robustness
score, the proof verdict, whether the targeted finding was resolved, whether
any new high-severity finding appeared, the test result, and the gate decision
with its reasons. `accepted` names the candidate that passed, if any.

## Progress notifications

Proofs and benchmarks take minutes. When the host sends a `progressToken`,
the server emits `notifications/progress` as it goes:

```json
{ "method": "notifications/progress",
  "params": { "progressToken": "...", "progress": 3, "total": 6,
              "message": "measuring candidate 2 of 4" } }
```

Hosts that do not send a token get the same run without the running commentary.
Either way, set a generous per-call timeout: progress does not extend it.

## Errors

A tool that cannot run returns an MCP error with a sentence you can act on,
not a stack trace. The two you will meet:

- **cannot find where the app answers.** Every detected start command was
  booted and none opened a port. Call `inspect_project`, then pass `start`.
- **not a git repository.** Shadow worktrees are git worktrees. `git init` is
  enough.

Logs go to stderr (`NAVIN_ENGINE_LOG=debug` for more); stdout stays a clean
protocol channel.
