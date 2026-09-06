# World model (AGI panel)

Navin learns to anticipate what a tool will answer before calling it: "in this repo, `npm test` fails", "`git push` here is denied", "that API answers 404". Not to talk better, not to write a skill, not to train the chat LLM. Three sentences say everything that matters:

1. **Off by default.** With the flag off there is no tool journal, no training job, no advice in any prompt: a chat turn costs exactly what it costs today.
2. **Journal and training are automatic.** With the flag on, every tool call leaves one secret-free line in `.navin/world/trajectories.jsonl` *after* the call, on a background thread, and a small local head (counts, not a neural network, never the chat model) trains outside the chat once enough new lines arrived. Its judge is a number: the prediction error on a frozen set of calls it never saw. Down means the checkpoint is kept as evidence and nothing serves it.
3. **Live advice is a gate plus you.** `advise` cannot be switched on until the frozen exam says `up` and an offline A/B on that same set shows a gain (useless calls spared, no false alarm). Even then the advice is three lines at most and never replaces a call; it switches itself off and rolls back the head if its live precision drops.

Everything lives in the **AGI** entry of the Code workbench rail, right after **Terminal**, between skills evolution and memory. S1 (episodic memory) is useful, not required: a trajectory links to the episode of its turn when S1 runs, and logs anyway when it does not.

## The project flag

```json
// <project>/.navin/world-model.json
{
  "schema_version": 1,
  "enabled": false,
  "log": true,
  "train": true,
  "advise": false,
  "beliefs": false,
  "skip_hint": false,
  "confidence_threshold": 0.8,
  "train_every": 200,
  "min_rows": 40
}
```

| Field | Meaning |
|---|---|
| `enabled` | Master switch. Off: no line, no job, no advice; the per-turn hook factory answers `None` after one `os.stat`. |
| `log` | One line per tool call: tool, hashed arguments (salted per project, scrubbed of secrets first), normalized shape (program, sub-command, host, extension, argument names; no values, no paths), observation class, short fingerprint, 96-character head, exit code, duration, the class of the last three calls of the session. Never the raw output. |
| `train` | When `train_every` new lines arrived (and at least `min_rows`), the job runner fits a head after the turn, scores it and activates it only if it learned. `navin agi world train` does the same by hand; it may run with `train` off, never with `enabled` off. |
| `advise` | Live advice. Refused (409) while the gate is closed. When on: a runtime-context block of at most three confident beliefs before the agent picks its tools, plus the `world_predict` tool. Advisory only: it never skips a write, delete, mail or payment call. |
| `beliefs` | Refresh `.navin/BELIEFS.md` after each checkpoint: ten lines at most, a readable summary for you. The prompt never reads this file while `advise` is off. |
| `skip_hint` | Later human mode: with `advise` on, a repeated read-only call that just gave the same answer may be flagged "already seen". Shell, write, delete, mail and payment tools are never concerned. |
| `confidence_threshold` | A prediction under it says nothing (0.5 to 0.99). |

## Classes

Every observation is one of `ok`, `changed` (a write succeeded), `empty`, `not_found`, `denied`, `timeout`, `error`. `not_found`, `denied` and `timeout` are the *useless* calls: the world did not move and the answer was a refusal. Those are what advice tries to spare.

## The exam

About one call in five is held out by the hash of its id, never trained on. The first training freezes those rows into `.navin/world/heldout.jsonl` with a version (hash of the row ids). Every score is computed on that file and re-hashes it first: an edited, trimmed or reordered set fails the run, the exam and the advice gate with `tampered`. Lowering the number by reworking the exam is the cheat S2 forbids for skills; the same rule holds here. `navin agi world freeze` makes a new version (a human action); scores of different versions are never compared.

Metrics: log-loss (the error of the predicted distribution), wrong-class rate, calibration (ECE). The head is compared with the stronger of two baselines, "always ok" and "majority class": `up` when its log-loss is at least 2% lower without more wrong classes, `down` when it is worse, `flat` in between. A new checkpoint is activated only when it is `up` vs the baseline and not `down` vs the head that serves. Rollback goes back to N-1; the newer checkpoint stays on disk as evidence.

Training runs under a budget (wall clock, memory growth, rows). Over budget means the run fails and nothing on disk moves.

## Advice, the A/B and the kill switch

The offline A/B replays the frozen set: A makes every call and pays the useless ones in full; B is told "useless" when the head is confident, spares the call when it really was useless and is charged a false alarm otherwise. `gain` needs at least one useless call in ten spared with 90% precision; under 80% precision it is `regress`.

Live, every suggestion is compared with the observed class after the real call (`.navin/world/live.jsonl`). When the rolling precision over the last 30 advices falls under 80%, the advisor cuts itself: `advise` goes back to off, the head rolls back to N-1, the journal says why, the `world_predict` tool leaves at the next sync and the prompt is exactly what it was. A human has to switch it on again, and only once the gate reopens.

The running gateway aligns the `world_predict` tool with the gate at boot, on every AGI panel read or write, and after every action. A flag flipped from the CLI (`navin agi world set advise on`) therefore reaches the serving agent the next time the panel is opened; the panel says "world_predict tool live in the agent" when it did, and shows the gate's reasons when it is closed.

## Where things go

| Path | Content |
|---|---|
| `.navin/world-model.json` | The flag and the knobs. The only file with the world model off. |
| `.navin/world/trajectories.jsonl` | One line per tool call (rotated at 24 MB, previous generation kept as `.1`). |
| `.navin/world/salt` | The random per-project salt of the argument hashes. |
| `.navin/world/heldout.jsonl` | The frozen exam set: a header with the version, then the rows. |
| `.navin/world/checkpoints/ckpt-NNNN.json` | One head per training run with its metrics and verdicts. Never rewritten. |
| `.navin/world/active.json` | Which checkpoint serves, which one served before. |
| `.navin/world/scoreboard.jsonl` | One line per train or exam: baseline -> head, verdict. |
| `.navin/world/ab.jsonl`, `live.jsonl` | Offline A/B results, live advice outcomes. |
| `.navin/world/journal.jsonl` | heldout_frozen / trained / examined / rollback / ab / advise_on / advise_cut, with actor. |
| `.navin/BELIEFS.md` | The readable summary (managed block between markers; your notes around it survive). |

## From the terminal

```bash
navin agi status                     # skills evolution, world model and memory switches
navin agi world status               # flag, journal size, frozen set, active head, score, gate
navin agi world on | off             # master switch (advice stays off)
navin agi world set beliefs on       # log | train | advise | beliefs | skip_hint | knobs
navin agi world set advise on        # refused while the gate is closed
navin agi world log -n 20            # tail of the tool journal: class, key, duration
navin agi world train                # human: train a checkpoint now
navin agi world run                  # what the runner does after a turn, if due
navin agi world exam                 # re-score the active head on the same frozen set
navin agi world ab                   # offline A/B of the active head
navin agi world freeze --yes         # human: new held-out version
navin agi world checkpoints          # every checkpoint, the active and the previous one
navin agi world scoreboard           # baseline -> head, verdict, per run
navin agi world rollback             # back to N-1
navin agi world beliefs [--render | --discard <key> | --restore]
navin agi world journal -n 30        # trained / rollback / advise_cut ...
```

Every command takes `-p/--project <folder>` (default: current directory) and the readers accept `--json`.
