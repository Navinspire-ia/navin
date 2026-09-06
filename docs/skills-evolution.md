# Skills evolution (AGI panel)

Navin can draft, examine and correct its own skills. Three sentences say everything that matters:

1. **Off by default.** With the flag off there is no draft folder, no exam, no new skill: a chat turn costs exactly what it costs today.
2. **Drafts are automatic, and invisible until they pass.** When the same tool failure repeats, Navin writes a skill in `.navin/skills-draft/` *after* the turn, runs a frozen exam battery (code, browser, desk) and only lets the skill steer the project when the score goes up with no suite down.
3. **Publishing is you.** Copying a skill to `~/.navin/skills` (every project) is always a human click in the AGI panel or `navin agi publish`; the engine cannot do it.

Everything lives in the **AGI** entry of the Code workbench rail, right after **Terminal**. The episodic memory switches (journal each turn, `recall` tool) moved there too; **Guardrails** keeps autonomy and machine-wide permissions.

## The project flag

```json
// <project>/.navin/skills-evolve.json
{
  "schema_version": 1,
  "enabled": false,
  "draft": true,
  "promote_project": true,
  "publish_harness": false,
  "failure_threshold": 3,
  "max_attempts": 3,
  "exam_model": "lexical",
  "author": "auto"
}
```

| Field | Meaning |
|---|---|
| `enabled` | Master switch. Off: nothing is drafted, examined or loaded. |
| `draft` | The same tool failure `failure_threshold` times in a row queues a draft job (after the turn, never inside it). |
| `promote_project` | An eligible draft (up overall, no suite down) enters `.navin/skills` without a click, is verified once more and rolls back by itself if that check fails. Off: eligible drafts wait for your **Promote**. |
| `publish_harness` | Shows the **Publish for every project** button on promoted skills. Still a human click. |
| `max_attempts` | Correction passes on the draft when the exam says flat or down (best version kept, then give up). |
| `exam_model` | `lexical` (deterministic, offline) or `llm` (the configured model answers the battery). |
| `author` | `auto` (LLM with template fallback), `llm` or `template`. |

## The exam

The battery is frozen and versioned: 20 cases across three suites (`code`, `browser`, `desk`), hashed into a version such as `s2-r1-149178ff90bd`. Changing one case gives a new version; a draft can never be corrected by rewriting the cases, and a run detects a battery edited mid-exam and fails it. Each run publishes a score out of 20 plus a verdict (`up` / `flat` / `down`) against the project baseline, per suite and overall, and is bounded by a wall-clock and memory budget: over budget means failed, never promoted.

Exams never run inside a chat turn. They run on the job runner after the turn, from `navin agi run`, or from a cron you schedule. A periodic guard (`navin agi guard`) re-examines promoted skills and retires any that regress.

## Where things go

| Path | Content |
|---|---|
| `.navin/skills-draft/<name>/SKILL.md` | The draft itself. The live skill loader never reads this folder. |
| `.navin/skills-draft/<name>/draft.json` | Status, score, verdict, attempts, history. |
| `.navin/skills-draft/journal.jsonl` | created / examined / kept / promoted / retired / discarded, with actor and scores. |
| `.navin/skills/<name>/SKILL.md` | A promoted skill: the project now uses it. |
| `~/.navin/skills/<name>/SKILL.md` | A published skill: every project uses it. Human click only. |

When episodic memory is on, a promotion also writes `skill X promoted, score A -> B` to `.navin/memory/episodes.jsonl`, so `recall` can find it.

## From the terminal

```bash
navin agi status              # flag, battery version, drafts, jobs
navin agi on | off            # master switch
navin agi set promote_project off
navin agi memory on --episodes on --recall on   # episodic memory switches
navin agi battery             # frozen battery: version, suites, cases
navin agi drafts              # table: status, score /20, verdict, suites
navin agi show <name>         # the draft SKILL.md
navin agi journal -n 30       # created / examined / promoted / retired
navin agi draft <name> -d "What the skill should teach"   # --now runs it right away
navin agi run                 # drain queued jobs (exam + correction + promotion)
navin agi exam <name>         # re-run the same battery
navin agi guard               # retire promoted skills that regressed
navin agi promote <name>      # eligible draft -> project skill
navin agi force <name>        # flat draft -> project skill (traced, reversible)
navin agi publish <name>      # project skill -> ~/.navin/skills (human only)
navin agi rollback <name>     # remove the project skill, keep the draft
navin agi discard <name>      # remove the draft folder, keep the journal
```

Every command takes `-p/--project <folder>` (default: current directory).
