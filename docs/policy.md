# Policy learning (AGI panel)

Navin learns which action to take: which tool, in which order, when to stop, when to ask. Not to remember (S1), not to grade a skill (S2), not to predict what a tool will answer (S3). S3 asks "if I call this, what will I see?"; S4 asks "then what do I call?", learned from trajectories, examined, reversible. Three sentences say everything that matters:

1. **Off by default.** With the flag off there is no trajectory, no training job, no suggestion in any prompt: a chat turn costs exactly what it costs today. The flag refuses to go on while the world model exam (S3.3) is not `up`: no policy without a radar.
2. **Trajectories and training are automatic, and never touch the chat.** With the flag on, a small local policy head (reward-weighted counts, never the chat model, never the model your subscription serves) trains in a **child process** on trajectories that come from **eval runs only**: the frozen S2-style batteries (code, browser, desk) replayed in a sandbox, with the pass / fail of the whole episode as the reward. A thank-you in the chat is not a reward; a chat turn is never a trajectory. Its judge is a number: N+1 must beat N on a frozen held-out split it never trained on, with no suite down. Flat keeps N. Down keeps N and the evidence.
3. **Live steer is a gate plus you.** `steer` cannot be switched on until the exam says `up` with no suite down and an offline A/B on the same frozen split shows a gain. Even then the head only proposes (a soft block of three lines at most and the `policy_next` tool); it never runs a tool, writes, deletes, mails or pays, and approvals are unchanged. It switches itself off and reloads N if its live precision drops.

Everything lives in the **AGI** entry of the Code workbench rail, in the **Policy** section right after **World model**. What is not S4: fine-tuning the model the gateway serves, learning on the live chat, replacing the LLM with the adapter, publishing an adapter to every project by itself, skipping S3, touching the agent loop.

## The project flag

```json
// <project>/.navin/policy.json
{
  "schema_version": 1,
  "enabled": false,
  "log": true,
  "train": true,
  "steer": false,
  "confidence_threshold": 0.7,
  "train_every": 40,
  "min_rows": 20
}
```

| Field | Meaning |
|---|---|
| `enabled` | Master switch. Off: no line, no job, no steer; the per-turn hook factory answers `None` after one `os.stat`. Refused (409) while the S3 radar is down. |
| `log` | The eval runs append their steps to `.navin/policy/trajectories.jsonl`: intent key, last actions, S3 class when available, action, action shape, observation class, reward of the episode. Never the prompt, never an argument, never a result. |
| `train` | Every `train_every` chat turns (with at least `min_rows` steps on disk, at most once per 15 minutes), the job runner spawns a child process after the turn: it replays the battery, freezes the held-out split on the first run, fits a head, scores it and activates it only if it is `up` with no suite down. `navin agi policy train` does the same by hand; it may run with `train` off, never with `enabled` off, never with the radar down. |
| `steer` | Live steer. Refused (409) while the gate is closed. When on: a runtime-context block before the agent picks its tools ("for a request like this, successful runs next used ...", "avoid ..."), plus the `policy_next` tool. Advisory only. |
| `confidence_threshold` | A suggestion under it says nothing (0.5 to 0.99). A suggestion also needs at least three successful runs of that kind of request. |

## Trajectories

One line per step of an eval episode, written by the training process after the run, on a background writer:

```json
{"v":1,"source":"eval","episode":"b7f3|code-01|r2","suite":"code","case":"code-01","split":"train","step":1,
 "intent":"fix","prev":["read_file:ok"],"s3":"ok","action":"edit_file","akey":"edit_file|.py","obs":"changed",
 "reward":1.0,"terminal":false,"ts":"...","id":"..."}
```

The state is the intent (a small vocabulary: `fix`, `add`, `find`, `test`, `fetch`, `mail`, `schedule`, `invoice`, ...; a URL, a file name or a host never becomes an intent), the class of the last three actions, and the S3 class of the planned call when the world model has one. The action is a tool name, `stop` (final answer) or `ask` (final question). The writer refuses anything that is not eval-sourced with a 0 / 1 reward: there is no field for a like and no way to add one.

## The battery and the exam

The bundled battery (`navin/policy/exams/battery.json`) has three suites: code (9 cases), browser (8) and desk (8). Each case is a prompt, a fixture workspace, a scripted sequence of real tool calls and the checks that decide pass / fail (tools that succeeded, tools that failed, files that contain, final answer that contains). Some cases fail on purpose: a blind edit, a search that was never run. A project adds its own cases in `.navin/policy/cases.jsonl` with the same shape; an unknown check key is refused, so a case cannot pass for nothing.

Every case carries a `split`: `train` or `heldout`. The first training run freezes the held-out episodes into `.navin/policy/heldout.jsonl` with a version (hash of the whole content of the rows). Every score is computed on that file and re-hashes it first: an edited, trimmed or reordered set fails the run, the exam and the steer gate with `tampered`. The battery itself is fingerprinted and re-checked before a checkpoint is written. Reworking the exam to make N+1 win is the cheat S2 forbids for skills; the same rule holds here. `navin agi policy freeze` makes a new version (a human action); scores of different versions are never compared and an adapter examined on an older version cannot open the gate.

Metrics: next-action accuracy and log-loss on the held-out steps of successful episodes, overall and per suite. `up` is at least five accuracy points better (or the same accuracy with a lower loss), `down` the mirror, `flat` in between. A new adapter is activated only when it is `up` vs the stronger baseline (uniform, majority action), not `down` vs the adapter that serves, and **no suite is down**. Rollback goes back to N; the newer adapter stays on disk as evidence. A human may force a `flat` adapter (traced, reversible); a `down` one is refused.

## Training in a sandbox

Training is a child process (`python -m navin.policy.train_job`), never the gateway's: CPU and address-space ceilings via `setrlimit`, a wall clock (120 s by default), a memory-growth cap (512 MB) and a case cap (400). Over budget or a crash means the run fails, N+1 is thrown away, N stays, the journal says why. The child imports neither the web UI nor the providers: the LLM of the gateway is never fine-tuned, never called.

## Steer, the A/B and the kill switch

The offline A/B replays the frozen split: the head suggests when confident; `gain` needs at least 20% coverage with 70% precision and 10 points over the majority-action baseline; under 50% precision it is `regress`. The gate is `exam eligible (up, no suite down) + A/B gain` on the same held-out version.

Live, every confident suggestion is compared with the tool the agent actually called (`.navin/policy/live.jsonl`). When the rolling precision over the last 30 suggestions (10 at least) falls under 60%, the steerer cuts itself: `steer` goes back to off, the active adapter rolls back to N, the journal says `steer_cut`, the `policy_next` tool leaves at the next sync and the prompt is exactly what it was. A human has to switch it on again, and only once the gate reopens.

The running gateway aligns the `policy_next` tool with the gate at boot, on every AGI panel read or write, and after every action.

## Publishing (a human click)

`navin agi policy publish` copies the active adapter to `~/.navin/policy/published/` with its scores; nothing adopts it by itself. `navin agi policy adopt <name>` in another project saves it as a new checkpoint there and examines it against that project's frozen split: it serves only if it wins the same exam, and never before that project has a frozen split of its own.

## Where things go

| Path | Content |
|---|---|
| `.navin/policy.json` | The flag and the knobs. The only file with the policy off. |
| `.navin/policy/trajectories.jsonl` | One line per eval step (rotated at 24 MB, previous generation kept as `.1`). |
| `.navin/policy/cases.jsonl` | Project cases added to the bundled battery (optional). |
| `.navin/policy/heldout.jsonl` | The frozen exam split: a header with the version, then the rows. |
| `.navin/policy/checkpoints/adapter-NNNN.json` | One adapter per training run with its metrics and verdicts (six kept, the active one always). |
| `.navin/policy/active.json` | Which adapter serves, which one served before, whether a human forced it. |
| `.navin/policy/scoreboard.jsonl` | One line per train or exam: baseline -> adapter, N -> N+1, verdict per suite. |
| `.navin/policy/ab.jsonl`, `live.jsonl` | Offline A/B results, live steer outcomes. |
| `.navin/policy/train-state.json` | Turns since the last run, last run time. |
| `.navin/policy/journal.jsonl` | eval_run / heldout_frozen / trained / activated / rollback / forced / ab / steer_on / steer_cut / published / adopted, with actor. |
| `~/.navin/policy/published/` | Adapters published on this machine (human click). |

## From the terminal

```bash
navin agi status                       # skills evolution, world model, policy and memory switches
navin agi policy status                # flag, radar, battery, trajectories, frozen split, adapters, score, gate
navin agi policy on | off              # master switch (refused while the S3 radar is down; steer stays off)
navin agi policy set train off         # log | train | steer | knobs
navin agi policy set steer on          # refused while the gate is closed
navin agi policy log -n 20             # tail of the trajectories: intent, prev, action, obs, reward
navin agi policy train                 # human: train an adapter now (child process)
navin agi policy run                   # what the runner does after a turn, if due
navin agi policy exam                  # re-score the active adapter on the same frozen split
navin agi policy ab                    # offline A/B of the active adapter
navin agi policy freeze --yes          # human: new held-out version
navin agi policy checkpoints           # every adapter, the active and the previous one
navin agi policy scoreboard            # baseline -> adapter, N -> N+1, verdicts
navin agi policy rollback              # back to N
navin agi policy force [--number N]    # human: serve a flat adapter (traced, reversible)
navin agi policy publish --yes         # human: copy the active adapter to ~/.navin/policy/published
navin agi policy published             # adapters published on this machine
navin agi policy adopt <name>          # examine a published adapter here; serves only if it wins
navin agi policy unpublish <name>
navin agi policy journal -n 30         # trained / activated / rollback / steer_cut ...
```

Every command takes `-p/--project <folder>` (default: current directory) and the readers accept `--json`.
