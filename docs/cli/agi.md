# `navin agi`

The same switches as the **AGI** entry of the Code workbench, from the terminal. All off by default. A locked switch is not broken: it becomes clickable by itself once the stage below has passed its exam. Nothing to edit.

```bash
navin agi status
```

`status` prints the ladder: Skills evolution → World model → Policy → Transfer protocol. Each locked rung says what it waits for.

Every subcommand takes `-p/--project <folder>` (default: current directory).

## Skills evolution

Navin drafts a skill after a repeated failure, examines it on a frozen battery, and only lets it steer this project when the score goes up with no suite down.

```bash
navin agi on                     # master switch (navin agi off to stop)
navin agi set promote_project off
navin agi drafts                 # status, score /20, verdict, suites
navin agi draft fix-apply-patch -d "Read the file before patching" --now
navin agi run                    # drain queued draft jobs
navin agi guard                  # retire promoted skills that regressed
navin agi publish <name>         # copy to ~/.navin/skills: your click only
```

Flag: `<project>/.navin/skills-evolve.json`. Details: [Skills evolution](../skills-evolution.md).

## Memory

```bash
navin agi memory on --episodes on --recall on
```

Flag: `<project>/.navin/cognition.json`. Details: [Memory](../memory.md).

## World model

Navin learns to predict what a tool will answer. Training runs outside any chat turn. Advice stays off until the exam says up and the offline A/B says gain. Passing that exam unlocks the policy switch.

```bash
navin agi world status
navin agi world on               # journal + train; advice stays off
navin agi world train            # train a checkpoint now
navin agi world exam
navin agi world ab
navin agi world set advise on    # refused while the gate is closed
navin agi world beliefs
navin agi world rollback
```

Flag: `<project>/.navin/world-model.json`. Details: [World model](../world-model.md).

## Policy

Navin learns which action to take, from eval trajectories. Locked until the world model has passed its exam. Steer proposes; it executes nothing. The chat model is never fine-tuned.

```bash
navin agi policy status
navin agi policy on              # refused until the world model exam is up
navin agi policy train           # child process, never the gateway's
navin agi policy exam
navin agi policy ab
navin agi policy set steer on    # refused while the gate is closed
navin agi policy rollback
navin agi policy publish --yes   # your click only
```

Flag: `<project>/.navin/policy.json`. Details: [Policy learning](../policy.md).

## Transfer protocol

A hidden exam plus a shutdown dossier, not a mode. Unlocks by itself once skills evolution, the world model and the policy have passed their exams. The claim stays forbidden until both proofs pass. Navin never writes a stronger word.

```bash
navin agi transfer status
navin agi transfer protocol
navin agi transfer on            # refused until the three exams above have passed
navin agi transfer freeze --author a --attester c
navin agi transfer campaign
navin agi transfer safety
navin agi transfer kill-drill
```

Flag: `<project>/.navin/transfer.json`. Details: [Transfer protocol](../transfer-protocol.md).
