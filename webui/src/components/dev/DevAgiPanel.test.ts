// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type {
  AgiDraft,
  PolicyCheckpoint,
  PolicyGate,
  PolicyRadar,
  PolicyVerdict,
  TransferCampaign,
  TransferPrereqs,
  TransferSuites,
  WorldGate,
  WorldState,
} from "@/lib/api";

import {
  CONFIRMED_ACTIONS,
  CONFIRMED_POLICY_ACTIONS,
  CONFIRMED_TRANSFER_ACTIONS,
  CONFIRMED_WORLD_ACTIONS,
  adviseSwitchLocked,
  claimTone,
  draftActions,
  familyScoresLabel,
  forceableAdapter,
  journalSizeLabel,
  ladderSteps,
  percentLabel,
  policyActions,
  policyScoreLabel,
  policySwitchLocked,
  recallLiveNoteVisible,
  recallRestartNoteVisible,
  scoreLabel,
  steerSwitchLocked,
  suiteVerdictsLabel,
  transferActions,
  transferSwitchLocked,
  worldActions,
  worldScoreLabel,
} from "./DevAgiPanel";

function draft(overrides: Partial<AgiDraft>): AgiDraft {
  return {
    name: "retry-flaky-tests",
    status: "eligible",
    score: 14,
    baseline_score: 11,
    verdict: "up",
    suites: {},
    eligible: true,
    attempts: 1,
    battery_version: "1",
    origin: "auto",
    trigger: "repeated_failure",
    created_at: "2026-09-05T00:00:00Z",
    updated_at: "2026-09-05T00:00:00Z",
    promoted_at: null,
    published_at: null,
    forced_by: null,
    note: null,
    in_project: false,
    in_harness: false,
    paths: { draft: ".navin/skills-draft/retry-flaky-tests", project: ".navin/skills/retry-flaky-tests" },
    ...overrides,
  };
}

describe("draft actions (the human's plus, never the engine)", () => {
  const auto = { promote_project: true, publish_harness: true };
  const manual = { promote_project: false, publish_harness: false };

  it("offers Promote only when the project does not promote by itself", () => {
    expect(draftActions(draft({ status: "eligible" }), manual)).toEqual(["promote", "exam", "discard"]);
    expect(draftActions(draft({ status: "eligible" }), auto)).toEqual(["exam", "discard"]);
  });

  it("lets a human force a flat or retired skill, traced and reversible", () => {
    expect(draftActions(draft({ status: "flat", verdict: "flat" }), manual)).toContain("force");
    expect(draftActions(draft({ status: "retired" }), manual)).toContain("force");
    // Never on a rejected (down) draft: the gate said no.
    expect(draftActions(draft({ status: "rejected", verdict: "down" }), manual)).not.toContain("force");
  });

  it("shows Publish on promoted skills only when the switch allows it", () => {
    expect(draftActions(draft({ status: "promoted" }), auto)).toEqual(["publish", "exam", "rollback"]);
    expect(draftActions(draft({ status: "promoted" }), manual)).toEqual(["exam", "rollback"]);
  });

  it("keeps hands off a draft under exam", () => {
    expect(draftActions(draft({ status: "examining" }), auto)).toEqual([]);
  });

  it("asks before publish, force, discard and rollback", () => {
    for (const action of ["publish", "force", "discard", "rollback"] as const) {
      expect(CONFIRMED_ACTIONS.has(action)).toBe(true);
    }
    for (const action of ["promote", "exam", "run", "guard", "draft"] as const) {
      expect(CONFIRMED_ACTIONS.has(action)).toBe(false);
    }
  });
});

describe("labels", () => {
  it("prints the score out of 20 and a dash before the first exam", () => {
    expect(scoreLabel(14)).toBe("14/20");
    expect(scoreLabel(0)).toBe("0/20");
    expect(scoreLabel(null)).toBe("-");
    expect(scoreLabel(undefined)).toBe("-");
  });
});

describe("memory card helpers (moved from Guardrails)", () => {
  const state = {
    enabled: true,
    episodes: true,
    recall: true,
    journal_bytes: 0,
    settings_file: ".navin/cognition.json",
    recall_registered: null,
    recall_requires_restart: true,
  };

  it("stays quiet under 1 KB and rounds above", () => {
    expect(journalSizeLabel(0)).toBe("");
    expect(journalSizeLabel(900)).toBe("");
    expect(journalSizeLabel(12 * 1024)).toBe("12 KB");
    expect(journalSizeLabel(2.5 * 1024 * 1024)).toBe("2.5 MB");
  });

  it("only mentions the restart when recall is wanted and missing", () => {
    expect(recallRestartNoteVisible(null)).toBe(false);
    expect(recallRestartNoteVisible(state)).toBe(true);
    expect(recallRestartNoteVisible({ ...state, recall: false })).toBe(false);
    expect(recallRestartNoteVisible({ ...state, enabled: false })).toBe(false);
    expect(recallRestartNoteVisible({ ...state, recall_requires_restart: false })).toBe(false);
  });

  it("confirms the live tool only when the gateway said so", () => {
    const live = { ...state, recall_registered: true, recall_requires_restart: false };
    expect(recallLiveNoteVisible(live)).toBe(true);
    expect(recallRestartNoteVisible(live)).toBe(false);
    expect(recallLiveNoteVisible(state)).toBe(false);
    expect(recallLiveNoteVisible({ ...live, recall: false })).toBe(false);
    expect(recallLiveNoteVisible({ ...live, recall_registered: false })).toBe(false);
  });
});

describe("world model card helpers (S3)", () => {
  const closed: WorldGate = {
    open: false,
    reasons: ["exam verdict is flat", "no offline A/B yet"],
    checkpoint: 1,
    heldout_version: "abc",
    exam_verdict: "flat",
    ab_verdict: null,
  };
  const open: WorldGate = { open: true, reasons: [], checkpoint: 1, heldout_version: "abc", exam_verdict: "up", ab_verdict: "gain" };
  const serving: WorldState["active"] = { checkpoint: 3, previous: 2, heldout_version: "abc", activated_at: "2026-09-05T00:00:00Z" };

  it("keeps the advise switch locked until the exam says up and the A/B says gain", () => {
    expect(adviseSwitchLocked(null)).toBe(true);
    expect(adviseSwitchLocked({ enabled: false, advise: false, gate: open })).toBe(true);
    expect(adviseSwitchLocked({ enabled: true, advise: false, gate: closed })).toBe(true);
    expect(adviseSwitchLocked({ enabled: true, advise: false, gate: open })).toBe(false);
    // Turning advice off is always allowed, even after the gate closed again.
    expect(adviseSwitchLocked({ enabled: true, advise: true, gate: closed })).toBe(false);
  });

  it("offers no button while off, train once enough rows, the rest once a head serves", () => {
    const base = { enabled: true, rows: 10, min_rows: 200, active: null, heldout: { version: null, rows: 0 } };
    expect(worldActions(null)).toEqual([]);
    expect(worldActions({ ...base, enabled: false, rows: 10_000 })).toEqual([]);
    expect(worldActions(base)).toEqual([]);
    expect(worldActions({ ...base, rows: 200 })).toEqual(["train", "freeze"]);
    expect(worldActions({ ...base, rows: 200, active: serving })).toEqual(["train", "exam", "ab", "beliefs", "rollback", "freeze"]);
  });

  it("asks before freezing a new exam set or rolling the head back", () => {
    expect(CONFIRMED_WORLD_ACTIONS.has("freeze")).toBe(true);
    expect(CONFIRMED_WORLD_ACTIONS.has("rollback")).toBe(true);
    for (const action of ["train", "exam", "ab", "beliefs", "run", "discard_belief", "restore_beliefs"] as const) {
      expect(CONFIRMED_WORLD_ACTIONS.has(action)).toBe(false);
    }
  });

  it("prints baseline -> model log-loss and percentages", () => {
    expect(worldScoreLabel({ baseline_log_loss: 1.0412, log_loss: 0.2519 })).toBe("1.04 -> 0.25");
    expect(worldScoreLabel(null)).toBe("-");
    expect(percentLabel(0.873)).toBe("87%");
    expect(percentLabel(null)).toBe("-");
  });
});

describe("policy card helpers (S4)", () => {
  const radarUp: PolicyRadar = { up: true, reasons: [], checkpoint: 3, heldout_version: "abc", verdict: "up" };
  const radarDown: PolicyRadar = {
    up: false,
    reasons: ["world model has no active checkpoint"],
    checkpoint: null,
    heldout_version: null,
    verdict: null,
  };
  const closed: PolicyGate = {
    open: false,
    reasons: ["exam verdict is flat", "no offline A/B yet"],
    checkpoint: 1,
    heldout_version: "abc",
    exam_verdict: "flat",
    ab_verdict: null,
    forced: false,
  };
  const open: PolicyGate = { ...closed, open: true, reasons: [], exam_verdict: "up", ab_verdict: "gain" };
  const serving = { checkpoint: 1, previous: null, heldout_version: "abc", activated_at: "2026-09-05T00:00:00Z" };
  const verdict = (overall: PolicyVerdict["overall"], suites: PolicyVerdict["suites"]): PolicyVerdict => {
    const values = Object.values(suites);
    return {
      overall,
      suites,
      reference_score: 0,
      candidate_score: 0,
      eligible: overall === "up" && !values.includes("down"),
      regressed: overall === "down" || values.includes("down"),
    };
  };
  const adapter = (number: number, overrides: Partial<PolicyCheckpoint>): PolicyCheckpoint => ({
    number,
    trained_at: null,
    rows: 40,
    episodes: 25,
    battery_version: "b",
    heldout_version: "abc",
    metrics: null,
    baseline: null,
    verdict_vs_baseline: null,
    verdict_vs_active: null,
    actor: "auto",
    source: "trained",
    actions: 6,
    active: false,
    previous: false,
    forced: false,
    ...overrides,
  });

  it("keeps the master switch locked while the world model radar is down (no policy without a radar)", () => {
    expect(policySwitchLocked(null)).toBe(true);
    expect(policySwitchLocked({ enabled: false, radar: radarDown })).toBe(true);
    expect(policySwitchLocked({ enabled: false, radar: radarUp })).toBe(false);
    // Turning it off is always allowed, even after the radar went down again.
    expect(policySwitchLocked({ enabled: true, radar: radarDown })).toBe(false);
  });

  it("keeps the steer switch locked until N+1 beat N with no suite down and the A/B says gain", () => {
    expect(steerSwitchLocked(null)).toBe(true);
    expect(steerSwitchLocked({ enabled: false, steer: false, gate: open })).toBe(true);
    expect(steerSwitchLocked({ enabled: true, steer: false, gate: closed })).toBe(true);
    expect(steerSwitchLocked({ enabled: true, steer: false, gate: open })).toBe(false);
    // Turning steer off is always allowed, even after the gate closed again.
    expect(steerSwitchLocked({ enabled: true, steer: true, gate: closed })).toBe(false);
  });

  it("offers no button while off, train only with the radar up, the rest once an adapter serves", () => {
    const base = { enabled: true, radar: radarUp, active: null, heldout: { version: null, rows: 0 }, rows: 0, checkpoints: [] };
    expect(policyActions(null)).toEqual([]);
    expect(policyActions({ ...base, enabled: false, rows: 500, active: serving })).toEqual([]);
    expect(policyActions({ ...base, radar: radarDown })).toEqual([]);
    expect(policyActions(base)).toEqual(["train"]);
    expect(policyActions({ ...base, radar: radarDown, rows: 40 })).toEqual(["freeze"]);
    expect(policyActions({ ...base, rows: 40, active: serving })).toEqual(["train", "exam", "ab", "publish", "rollback", "freeze"]);
  });

  it("lets a human force only a flat adapter that is not serving and did not regress", () => {
    const flat = verdict("flat", { code: "flat", browser: "flat", desk: "flat" });
    const oneDown = verdict("up", { code: "up", browser: "up", desk: "down" });
    const up = verdict("up", { code: "up", browser: "up", desk: "up" });
    expect(forceableAdapter(null)).toBeNull();
    expect(forceableAdapter({ active: serving, checkpoints: [adapter(1, { active: true })] })).toBeNull();
    expect(forceableAdapter({ active: serving, checkpoints: [adapter(1, { active: true }), adapter(2, { verdict_vs_active: flat })] })).toBe(2);
    // A suite in down is a regression: never forced. An eligible adapter is activated by the engine, not forced.
    expect(forceableAdapter({ active: serving, checkpoints: [adapter(1, { active: true }), adapter(2, { verdict_vs_active: oneDown })] })).toBeNull();
    expect(forceableAdapter({ active: serving, checkpoints: [adapter(1, { active: true }), adapter(2, { verdict_vs_active: up })] })).toBeNull();
    // Without a serving adapter the verdict vs the baseline decides; the newest candidate wins.
    expect(
      forceableAdapter({
        active: null,
        checkpoints: [adapter(1, { verdict_vs_baseline: flat }), adapter(2, { verdict_vs_baseline: flat }), adapter(3, { verdict_vs_baseline: oneDown })],
      }),
    ).toBe(2);
  });

  it("asks before the actions that change what the scores mean or leave the project", () => {
    for (const action of ["freeze", "rollback", "force", "publish", "adopt", "unpublish"] as const) {
      expect(CONFIRMED_POLICY_ACTIONS.has(action)).toBe(true);
    }
    for (const action of ["train", "run", "exam", "ab"] as const) {
      expect(CONFIRMED_POLICY_ACTIONS.has(action)).toBe(false);
    }
  });

  it("prints baseline -> adapter accuracy and the per-suite verdicts", () => {
    expect(policyScoreLabel({ baseline_accuracy: 0.384, accuracy: 0.62 })).toBe("38% -> 62%");
    expect(policyScoreLabel({ baseline_accuracy: null, accuracy: 0.62 })).toBe("-");
    expect(policyScoreLabel(null)).toBe("-");
    expect(suiteVerdictsLabel(verdict("up", { desk: "up", code: "up", browser: "flat" }))).toBe("browser flat / code up / desk up");
    expect(suiteVerdictsLabel(null)).toBe("");
  });
});

describe("transfer card helpers (S5)", () => {
  const met: TransferPrereqs = { ok: true, checks: [], reasons: [] };
  const missing: TransferPrereqs = {
    ok: false,
    checks: [{ id: "s3", ok: false, detail: "world model exam is not up" }],
    reasons: ["s3: world model exam is not up"],
  };
  const suites = (overrides: Partial<TransferSuites>): TransferSuites => ({
    dir: "/home/me/.navin/transfer/suites",
    default_dir: "/home/me/.navin/transfer/suites",
    outside: true,
    placement_error: null,
    frozen: true,
    version: "52e89a2f445c",
    lock: { frozen_at: "2026-09-05T00:00:00Z", authors: ["alice"], attester: "carol", trainers: ["bob"], junior_baseline: {} },
    tampered: null,
    families: { code: 8, browser: 8, business: 8, plan: 8 },
    error: null,
    ...overrides,
  });
  const campaign: TransferCampaign = {
    id: "c0ffee01",
    ts: "2026-09-05T00:00:00Z",
    suites_version: "52e89a2f445c",
    verdict: "fail",
    stopped_at: "business",
    not_run: ["plan"],
    replay_of: null,
    isolation: { skills: false, recall: false, steer: false, hooks: false, s4_untouched: true },
    families: {
      code: { items: 8, passed: 8, pass_rate: 1, bar: 0.6, verdict: "pass" },
      browser: { items: 8, passed: 6, pass_rate: 0.75, bar: 0.6, verdict: "pass" },
      business: { items: 8, passed: 2, pass_rate: 0.25, bar: 0.6, verdict: "fail" },
    },
    duration_ms: 1200,
  };

  it("locks the master switch while a prerequisite is missing, never the way off", () => {
    expect(transferSwitchLocked(null)).toBe(true);
    expect(transferSwitchLocked({ enabled: false, prereqs: missing })).toBe(true);
    expect(transferSwitchLocked({ enabled: false, prereqs: met })).toBe(false);
    expect(transferSwitchLocked({ enabled: true, prereqs: missing })).toBe(false);
  });

  it("offers nothing off, the dossier and the drill on, the campaign only with frozen untouched suites", () => {
    expect(transferActions(null)).toEqual([]);
    expect(transferActions({ enabled: false, prereqs: met, suites: suites({}), campaign: null })).toEqual([]);
    expect(transferActions({ enabled: true, prereqs: met, suites: null, campaign: null })).toEqual(["safety", "kill_drill"]);
    expect(transferActions({ enabled: true, prereqs: met, suites: suites({ frozen: false, version: null, lock: null }), campaign: null })).toEqual([
      "safety",
      "kill_drill",
    ]);
    expect(transferActions({ enabled: true, prereqs: met, suites: suites({}), campaign: null })).toEqual(["safety", "kill_drill", "verify", "campaign"]);
    expect(transferActions({ enabled: true, prereqs: met, suites: suites({ tampered: "code.jsonl changed" }), campaign })).toEqual(["safety", "kill_drill", "verify"]);
    expect(transferActions({ enabled: true, prereqs: met, suites: suites({ outside: false, placement_error: "inside" }), campaign: null })).toEqual([
      "safety",
      "kill_drill",
      "verify",
    ]);
    expect(transferActions({ enabled: true, prereqs: missing, suites: suites({}), campaign: null })).toEqual(["safety", "kill_drill", "verify"]);
    expect(transferActions({ enabled: true, prereqs: met, suites: suites({}), campaign })).toEqual(["safety", "kill_drill", "verify", "campaign", "replay"]);
  });

  it("confirms the long or destructive buttons only", () => {
    expect([...CONFIRMED_TRANSFER_ACTIONS].sort()).toEqual(["campaign", "kill_drill", "replay"]);
    expect(CONFIRMED_TRANSFER_ACTIONS.has("safety")).toBe(false);
    expect(CONFIRMED_TRANSFER_ACTIONS.has("verify")).toBe(false);
  });

  it("labels each family against its bar and never averages", () => {
    expect(familyScoresLabel(campaign.families)).toBe("code 100%/60% pass / browser 75%/60% pass / business 25%/60% fail");
    expect(familyScoresLabel(null)).toBe("");
    expect(familyScoresLabel({})).toBe("");
  });

  it("tones the protocol answers: pass and discussable up, fail / void / forbidden down", () => {
    expect(claimTone("pass")).toBe("up");
    expect(claimTone("discussable")).toBe("up");
    for (const value of ["fail", "void", "forbidden", "collapse"]) expect(claimTone(value)).toBe("down");
    for (const value of ["not_run", "too_few", null, undefined]) expect(claimTone(value)).toBe("flat");
  });
});

describe("the ladder (a locked switch is not broken)", () => {
  const radarDown: PolicyRadar = { up: false, reasons: ["world model is off for this project"], checkpoint: null, heldout_version: null, verdict: null };
  const radarUp: PolicyRadar = { up: true, reasons: [], checkpoint: 2, heldout_version: "h", verdict: "up" };
  const missing: TransferPrereqs = {
    ok: false,
    checks: [{ id: "s2", ok: false, detail: "no skill draft was ever examined here" }],
    reasons: ["skills evolution: no skill draft was ever examined here"],
  };
  const met: TransferPrereqs = { ok: true, checks: [], reasons: [] };

  it("reads off, off, locked, locked on a fresh project and carries the reasons", () => {
    const steps = ladderSteps({
      agi: { enabled: false },
      world: { enabled: false },
      policy: { enabled: false, radar: radarDown },
      transfer: { enabled: false, prereqs: missing },
    });
    expect(steps.map((s) => s.id)).toEqual(["skills", "world", "policy", "transfer"]);
    expect(steps.map((s) => s.state)).toEqual(["off", "off", "locked", "locked"]);
    expect(steps[2].reasons).toEqual(["world model is off for this project"]);
    expect(steps[3].reasons).toEqual(["skills evolution: no skill draft was ever examined here"]);
  });

  it("unlocks by itself once the stage below has its proof, and never shows an on switch as locked", () => {
    const steps = ladderSteps({
      agi: { enabled: true },
      world: { enabled: true },
      policy: { enabled: false, radar: radarUp },
      transfer: { enabled: false, prereqs: met },
    });
    expect(steps.map((s) => s.state)).toEqual(["on", "on", "off", "off"]);
    const on = ladderSteps({
      agi: { enabled: true },
      world: { enabled: true },
      policy: { enabled: true, radar: radarDown },
      transfer: { enabled: true, prereqs: missing },
    });
    expect(on.map((s) => s.state)).toEqual(["on", "on", "on", "on"]);
    expect(on.every((s) => s.reasons.length === 0)).toBe(true);
  });

  it("still renders on a gateway without the transfer route", () => {
    const steps = ladderSteps({ agi: { enabled: false }, world: { enabled: false }, policy: null, transfer: null });
    expect(steps.map((s) => s.state)).toEqual(["off", "off", "off", "off"]);
  });

  it("prefers the gateway's rungs when it ships them", () => {
    const steps = ladderSteps({
      agi: { enabled: false },
      world: { enabled: false },
      policy: { enabled: false, radar: radarUp },
      transfer: {
        enabled: false,
        prereqs: met,
        ladder: [
          { id: "skills", label: "Skills evolution", state: "on", waits: "", reasons: [] },
          { id: "world", label: "World model", state: "on", waits: "", reasons: [] },
          { id: "policy", label: "Policy", state: "locked", waits: "waits for the world model to pass its exam", reasons: ["no checkpoint"] },
          { id: "transfer", label: "Transfer protocol", state: "locked", waits: "waits for ...", reasons: ["policy: off"] },
        ],
      },
    });
    expect(steps.map((s) => s.state)).toEqual(["on", "on", "locked", "locked"]);
    expect(steps[2].reasons).toEqual(["no checkpoint"]);
  });
});

describe("AGI panel layout", () => {
  const panel = readFileSync(resolve(__dirname, "DevAgiPanel.tsx"), "utf8");
  const workbench = readFileSync(resolve(__dirname, "DevWorkbench.tsx"), "utf8");
  const en = JSON.parse(
    readFileSync(resolve(__dirname, "../../i18n/locales/en/common.json"), "utf8"),
  ) as { dev: Record<string, unknown> };
  const fr = JSON.parse(
    readFileSync(resolve(__dirname, "../../i18n/locales/fr/common.json"), "utf8"),
  ) as { dev: Record<string, unknown> };

  it("sits first in the rail after the project folder picker", () => {
    const folder = workbench.indexOf("<DevProjectSelector");
    const agi = workbench.indexOf('key: "agi"', folder);
    const guardrails = workbench.indexOf('key: "guardrails"', agi);
    expect(folder).toBeGreaterThan(0);
    expect(agi).toBeGreaterThan(folder);
    expect(guardrails).toBeGreaterThan(agi);
    expect(workbench).toContain('tx("dev.agiTab", "AGI")');
    expect(workbench).toContain(') : mode === "agi" ? (');
    expect(workbench).toContain("<DevAgiPanel");
  });

  it("hosts the four evolution switches and reads them from the gateway", () => {
    for (const id of [
      "dev-agi-evolution",
      "dev-agi-evolve-enabled",
      "dev-agi-evolve-draft",
      "dev-agi-evolve-promote",
      "dev-agi-evolve-publish",
      "dev-agi-drafts",
      "dev-agi-draft-list",
      "dev-agi-journal-toggle",
      "dev-agi-refresh",
    ]) {
      expect(panel).toContain(`"${id}"`);
    }
    expect(panel).toContain("const next = await updateAgi(token, boardKey, fields)");
    // Sub-switches wait for the master flag, like the autonomy card.
    expect(panel).toContain("disabled={saving || !agi || !evolutionOn}");
    expect(panel).toContain(".navin/skills-evolve.json");
  });

  it("hosts the memory opt-in with the same guarantees as before", () => {
    for (const id of [
      "dev-agi-memory",
      "dev-agi-cognition-enabled",
      "dev-agi-cognition-episodes",
      "dev-agi-cognition-recall",
      "dev-agi-recall-restart",
      "dev-agi-recall-restart-link",
      "dev-agi-recall-live",
    ]) {
      expect(panel).toContain(`"${id}"`);
    }
    expect(panel).toContain("const next = await updateCognition(token, boardKey, fields)");
    expect(panel).toContain("setCognition(next)");
    // A missing route must not take the evolution card down with it.
    expect(panel).toContain("fetchCognition(token, boardKey).catch(() => null)");
    expect(panel).toContain("disabled={saving || !cognition || !memoryOn}");
    expect(panel).toContain(".navin/cognition.json");
  });

  it("hosts the world model switches between evolution and memory, advise behind the gate", () => {
    for (const id of [
      "dev-agi-world",
      "dev-agi-world-enabled",
      "dev-agi-world-log",
      "dev-agi-world-train",
      "dev-agi-world-advise",
      "dev-agi-world-beliefs",
      "dev-agi-world-stats",
      "dev-agi-world-score",
      "dev-agi-world-gate",
      "dev-agi-world-gate-reasons",
      "dev-agi-world-tool-live",
      "dev-agi-world-actions",
      "dev-agi-world-beliefs-list",
      "dev-agi-world-recent",
      "dev-agi-world-journal-toggle",
    ]) {
      expect(panel).toContain(`"${id}"`);
    }
    const evolution = panel.indexOf('testId="dev-agi-evolution"');
    const world = panel.indexOf('testId="dev-agi-world"');
    const memory = panel.indexOf('testId="dev-agi-memory"');
    expect(evolution).toBeGreaterThan(0);
    expect(world).toBeGreaterThan(evolution);
    expect(memory).toBeGreaterThan(world);
    expect(panel).toContain("const next = await updateWorld(token, boardKey, fields)");
    expect(panel).toContain("setWorld(next)");
    // A missing route must not take the other cards down with it.
    expect(panel).toContain("fetchWorld(token, boardKey).catch(() => null)");
    // Sub-switches wait for the master flag; advise also waits for the gate.
    expect(panel).toContain("disabled={saving || !world || !worldOn}");
    expect(panel).toContain("disabled={saving || !world || adviseLocked}");
    expect(panel).toContain("CONFIRMED_WORLD_ACTIONS.has(action)");
    // Action buttons never share a test id with the switch rows.
    expect(panel).toContain("testId={`dev-agi-world-action-${action}`}");
    expect(panel).toContain('"dev-agi-world-action-restore_beliefs"');
    expect(panel).toContain(".navin/world-model.json");
    expect(panel).toContain(".navin/world/trajectories.jsonl");
    expect(panel).toContain(".navin/BELIEFS.md");
  });

  it("hosts the policy switches between world model and memory, steer behind the gate", () => {
    for (const id of [
      "dev-agi-policy",
      "dev-agi-policy-enabled",
      "dev-agi-policy-log",
      "dev-agi-policy-train",
      "dev-agi-policy-steer",
      "dev-agi-policy-stats",
      "dev-agi-policy-radar",
      "dev-agi-policy-battery",
      "dev-agi-policy-heldout",
      "dev-agi-policy-score",
      "dev-agi-policy-suites",
      "dev-agi-policy-gate",
      "dev-agi-policy-gate-reasons",
      "dev-agi-policy-tool-live",
      "dev-agi-policy-live",
      "dev-agi-policy-turns",
      "dev-agi-policy-actions",
      "dev-agi-policy-action-force",
      "dev-agi-policy-adapters",
      "dev-agi-policy-published",
      "dev-agi-policy-recent",
      "dev-agi-policy-journal-toggle",
    ]) {
      expect(panel).toContain(`"${id}"`);
    }
    const world = panel.indexOf('testId="dev-agi-world"');
    const policy = panel.indexOf('testId="dev-agi-policy"');
    const memory = panel.indexOf('testId="dev-agi-memory"');
    expect(world).toBeGreaterThan(0);
    expect(policy).toBeGreaterThan(world);
    expect(memory).toBeGreaterThan(policy);
    expect(panel).toContain("const next = await updatePolicy(token, boardKey, fields)");
    expect(panel).toContain("setPolicy(next)");
    // A missing route must not take the other cards down with it.
    expect(panel).toContain("fetchPolicy(token, boardKey).catch(() => null)");
    // The master switch waits for the S3 radar; steer also waits for the gate.
    expect(panel).toContain("disabled={saving || !policy || policyLocked}");
    expect(panel).toContain("disabled={saving || !policy || steerLocked}");
    expect(panel).toContain("CONFIRMED_POLICY_ACTIONS.has(action)");
    expect(panel).toContain("testId={`dev-agi-policy-action-${action}`}");
    expect(panel).toContain(".navin/policy.json");
    expect(panel).toContain(".navin/policy/trajectories.jsonl");
    expect(panel).toContain("~/.navin/policy/published");
  });

  it("opens with the ladder: how the switches unlock, in words people read", () => {
    for (const id of ["dev-agi-ladder", "dev-agi-ladder-steps", "dev-agi-ladder-waits"]) {
      expect(panel).toContain(`"${id}"`);
    }
    expect(panel).toContain("data-testid={`dev-agi-ladder-${step.id}`}");
    expect(panel).toContain("data-state={step.state}");
    const ladder = panel.indexOf('testId="dev-agi-ladder"');
    const evolution = panel.indexOf('testId="dev-agi-evolution"');
    expect(ladder).toBeGreaterThan(0);
    expect(evolution).toBeGreaterThan(ladder);
    expect(panel).toContain("const ladder = ladderSteps({ agi, world, policy, transfer })");
    // The copy explains the lock without stage numbers.
    expect(panel).toContain("turns clickable by itself once the stage below has passed its exam");
    const userVisible = panel
      .split("\n")
      .filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line))
      .join("\n");
    expect(userVisible).not.toMatch(/"[^"]*\bS[1-5](\.\d)?\b[^"]*"/);
  });

  it("hosts the transfer protocol between policy and memory, never a claim the product writes", () => {
    for (const id of [
      "dev-agi-transfer",
      "dev-agi-transfer-enabled",
      "dev-agi-transfer-stats",
      "dev-agi-transfer-prereqs",
      "dev-agi-transfer-suites",
      "dev-agi-transfer-placement",
      "dev-agi-transfer-campaign",
      "dev-agi-transfer-families",
      "dev-agi-transfer-safety",
      "dev-agi-transfer-drill",
      "dev-agi-transfer-claim",
      "dev-agi-transfer-claim-reasons",
      "dev-agi-transfer-actions",
      "dev-agi-transfer-journal-toggle",
      "dev-agi-transfer-journal",
    ]) {
      expect(panel).toContain(`"${id}"`);
    }
    const policy = panel.indexOf('testId="dev-agi-policy"');
    const transfer = panel.indexOf('testId="dev-agi-transfer"');
    const memory = panel.indexOf('testId="dev-agi-memory"');
    expect(policy).toBeGreaterThan(0);
    expect(transfer).toBeGreaterThan(policy);
    expect(memory).toBeGreaterThan(transfer);
    expect(panel).toContain("const next = await updateTransfer(token, boardKey, fields)");
    expect(panel).toContain("setTransfer(next)");
    expect(panel).toContain("fetchTransfer(token, boardKey).catch(() => null)");
    // The master switch waits for S2 / S3.3 / S4.3; the buttons are confirmed when long or destructive.
    expect(panel).toContain("disabled={saving || !transfer || transferLocked}");
    expect(panel).toContain("CONFIRMED_TRANSFER_ACTIONS.has(action)");
    expect(panel).toContain("testId={`dev-agi-transfer-action-${action}`}");
    expect(panel).toContain(".navin/transfer.json");
    // The claim is only ever "forbidden" or "discussable"; the panel has no other word for it.
    expect(panel).toContain('transfer.claim.status === "discussable"');
    expect(panel).not.toMatch(/"[^"]*\bis AGI\b[^"]*"/);
    expect(panel).not.toMatch(/Navin is (an )?AGI/);
    // A freeze needs names on record: CLI only, no button.
    expect(panel).not.toContain('"dev-agi-transfer-action-freeze"');
  });

  it("confirms the human-only actions and keeps the answer", () => {
    expect(panel).toContain("<ConfirmDialog");
    expect(panel).toContain("CONFIRMED_ACTIONS.has(action)");
    expect(panel).toContain("const payload = await agiAction(token, boardKey, action, { name, brief })");
    expect(panel).toContain("setAgi(payload.state)");
    expect(panel).toContain("overflow-y-auto");
    expect(panel).toContain('data-testid="dev-agi-scroll"');
  });

  it("ships the copy in both languages", () => {
    const enAgi = en.dev.agi as Record<string, unknown>;
    const frAgi = fr.dev.agi as Record<string, unknown>;
    expect(Object.keys(enAgi).sort()).toEqual(Object.keys(frAgi).sort());
    const enWorld = enAgi.world as Record<string, unknown>;
    const frWorld = frAgi.world as Record<string, unknown>;
    expect(Object.keys(enWorld).sort()).toEqual(Object.keys(frWorld).sort());
    expect(Object.keys(enWorld.actions as object).sort()).toEqual(Object.keys(frWorld.actions as object).sort());
    expect(Object.keys(enWorld.done as object).sort()).toEqual(Object.keys(frWorld.done as object).sort());
    // Three sentences for S3: off, journal + train auto, advice = gate + you.
    expect(String(enWorld.hint)).toMatch(/off by default/i);
    expect(String(enWorld.hint)).toMatch(/trains outside the chat/i);
    expect(String(enWorld.hint)).toMatch(/A\/B proved a gain/i);
    expect(String(frWorld.hint)).toMatch(/par défaut/);
    expect(String(frWorld.hint)).toMatch(/hors du chat/);
    expect(String(frWorld.hint)).toMatch(/A\/B a prouvé un gain/);
    const enPolicy = enAgi.policy as Record<string, unknown>;
    const frPolicy = frAgi.policy as Record<string, unknown>;
    expect(Object.keys(enPolicy).sort()).toEqual(Object.keys(frPolicy).sort());
    expect(Object.keys(enPolicy.actions as object).sort()).toEqual(Object.keys(frPolicy.actions as object).sort());
    expect(Object.keys(enPolicy.done as object).sort()).toEqual(Object.keys(frPolicy.done as object).sort());
    // S4: off, train in a sandbox and never the chat model, steer = gates (exam, no suite down, A/B, kill switch).
    expect(String(enPolicy.hint)).toMatch(/off by default/i);
    expect(String(enPolicy.hint)).toMatch(/never the chat model/i);
    expect(String(enPolicy.hint)).toMatch(/no suite down/i);
    expect(String(enPolicy.hint)).toMatch(/executes nothing/i);
    expect(String(enPolicy.hint)).toMatch(/cuts itself off/i);
    expect(String(enPolicy.hint)).toMatch(/radar/i);
    expect(String(frPolicy.hint)).toMatch(/par défaut/);
    expect(String(frPolicy.hint)).toMatch(/jamais le modèle du chat/);
    expect(String(frPolicy.hint)).toMatch(/sans aucune suite en recul/);
    expect(String(frPolicy.hint)).toMatch(/n'exécute rien/);
    expect(String(frPolicy.hint)).toMatch(/se coupe tout seul/);
    expect(String(frPolicy.hint)).toMatch(/radar/);
    const enTransfer = enAgi.transfer as Record<string, unknown>;
    const frTransfer = frAgi.transfer as Record<string, unknown>;
    expect(Object.keys(enTransfer).sort()).toEqual(Object.keys(frTransfer).sort());
    for (const group of ["actions", "done", "confirm"]) {
      expect(Object.keys(enTransfer[group] as object).sort()).toEqual(Object.keys(frTransfer[group] as object).sort());
    }
    // Every button has a label and a "done" line; the long ones have a confirmation.
    expect(Object.keys(enTransfer.actions as object).sort()).toEqual(["campaign", "kill_drill", "replay", "safety", "verify"]);
    expect(Object.keys(enTransfer.done as object).sort()).toEqual(Object.keys(enTransfer.actions as object).sort());
    expect(Object.keys(enTransfer.confirm as object).sort()).toEqual([
      "campaign",
      "campaignTitle",
      "killDrill",
      "killDrillTitle",
      "replay",
      "replayTitle",
    ]);
    // S5: off by default, hidden exam + shutdown dossier, nothing in a chat turn, claim forbidden until both gates pass.
    expect(String(enTransfer.hint)).toMatch(/off by default/i);
    expect(String(enTransfer.hint)).toMatch(/hidden exam/i);
    expect(String(enTransfer.hint)).toMatch(/not a mode/i);
    expect(String(enTransfer.hint)).toMatch(/nothing here touches a chat turn/i);
    expect(String(enTransfer.hint)).toMatch(/claim stays forbidden/i);
    expect(String(enTransfer.hint)).toMatch(/steer on/i);
    expect(String(frTransfer.hint)).toMatch(/par défaut/);
    expect(String(frTransfer.hint)).toMatch(/examen caché/);
    expect(String(frTransfer.hint)).toMatch(/pas un mode/);
    expect(String(frTransfer.hint)).toMatch(/rien ici ne touche un tour de chat/);
    expect(String(frTransfer.hint)).toMatch(/claim reste interdit/);
    expect(String(frTransfer.hint)).toMatch(/steer on/);
    // The two claim words, and no third one; "discussable" is still not a word the product writes.
    expect(String(enTransfer.claimForbidden)).toMatch(/forbidden/i);
    expect(String(enTransfer.claimDiscussable)).toMatch(/discussable/i);
    expect(String(enTransfer.claimDiscussable)).toMatch(/not a word the product writes/i);
    expect(String(frTransfer.claimForbidden)).toMatch(/interdit/);
    expect(String(frTransfer.claimDiscussable)).toMatch(/discutable/);
    expect(String(frTransfer.claimDiscussable)).toMatch(/pas un mot que le produit écrit/);
    expect(String(enTransfer.claimHint)).toMatch(/nobody self-declares/i);
    expect(String(frTransfer.claimHint)).toMatch(/personne ne s'auto-déclare/);
    expect(en.dev.agiTab).toBe("AGI");
    expect(fr.dev.agiTab).toBe("AGI");
    const enGuard = en.dev.guardrails as Record<string, string>;
    const frGuard = fr.dev.guardrails as Record<string, string>;
    // Three sentences: off by default, auto-draft, publish = you.
    expect(enGuard.agiMovedDetail).toMatch(/off by default/i);
    expect(enGuard.agiMovedDetail).toMatch(/drafts a skill/i);
    expect(enGuard.agiMovedDetail).toMatch(/your click/i);
    expect(frGuard.agiMovedDetail).toMatch(/par défaut/);
    expect(frGuard.agiMovedDetail).toMatch(/rédige une skill/);
    expect(frGuard.agiMovedDetail).toMatch(/votre clic/);
    // Memory copy left Guardrails for good.
    expect(enGuard.memory).toBeUndefined();
    expect(frGuard.memory).toBeUndefined();
    // S5 in Guardrails: a hidden exam and a shutdown dossier, not a magic mode; Navin never writes the claim.
    expect(enGuard.transferNote).toMatch(/hidden exam/i);
    expect(enGuard.transferNote).toMatch(/shutdown dossier/i);
    expect(enGuard.transferNote).toMatch(/not a magic mode/i);
    expect(enGuard.transferNote).toMatch(/never writes the claim itself/i);
    expect(frGuard.transferNote).toMatch(/examen caché/);
    expect(frGuard.transferNote).toMatch(/dossier de coupure/);
    expect(frGuard.transferNote).toMatch(/pas un mode magique/);
    expect(frGuard.transferNote).toMatch(/n'écrit jamais le claim lui-même/);
    // The ladder copy exists in both languages and explains the lock in words.
    for (const key of ["ladderSection", "ladderHint", "ladderLocked", "ladderWaitsPolicy", "ladderWaitsTransfer", "ladderMissing"]) {
      expect(typeof enAgi[key]).toBe("string");
      expect(typeof frAgi[key]).toBe("string");
    }
    expect(String(enAgi.ladderHint)).toMatch(/turns clickable by itself/i);
    expect(String(frAgi.ladderHint)).toMatch(/devient cliquable tout seul/);
    // No em/en dash anywhere in the new copy, and no stage numbers: people read words.
    const blob = JSON.stringify([enAgi, frAgi, enGuard, frGuard]);
    expect(blob).not.toMatch(/[\u2013\u2014]/);
    expect(blob).not.toMatch(/\bS[1-5](\.\d)?\b/);
  });
});
