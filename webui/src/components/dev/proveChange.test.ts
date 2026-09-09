// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { DaemonStatus, EvolveOverview, ProofReport } from "@/lib/evolve-api";
import {
  extractJobId,
  isJobTerminal,
  jobFromStatus,
  latestProof,
  proofSummary,
  proveProjectPath,
  verdictTone,
} from "./proveChange";

function proof(partial: Partial<ProofReport>): ProofReport {
  return {
    schema: "proof/1",
    commit: "abc",
    profile: "standard",
    collected_at: "2026-08-19T10:00:00Z",
    faults: [],
    verdict: "pass",
    robustness_score: 90,
    ...partial,
  };
}

describe("extractJobId", () => {
  it("reads nested and flat job ids", () => {
    expect(extractJobId({ result: { job: 7 } })).toBe(7);
    expect(extractJobId({ job: 3 })).toBe(3);
    expect(extractJobId({ job: "12" })).toBe(12);
    expect(extractJobId({})).toBeNull();
  });
});

describe("proveProjectPath", () => {
  it("uses the chat workspace first, then the last Code folder", () => {
    expect(proveProjectPath("/home/me/app")).toBe("/home/me/app");
    expect(proveProjectPath("  /home/me/app  ", "/other")).toBe("/home/me/app");
    expect(proveProjectPath("", "/home/me/code")).toBe("/home/me/code");
    expect(proveProjectPath(null, "  /home/me/code  ")).toBe("/home/me/code");
    expect(proveProjectPath("", "")).toBeNull();
    expect(proveProjectPath(null)).toBeNull();
  });
});

describe("jobFromStatus / isJobTerminal", () => {
  const status: DaemonStatus = {
    online: true,
    status: {
      engine: "navin-engine",
      protocol: 1,
      root: "/p",
      uptime_secs: 10,
      jobs: [
        { id: 1, kind: "proof.run", state: "running" },
        { id: 2, kind: "proof.run", state: "done" },
      ],
    },
  };

  it("finds the job by id", () => {
    expect(jobFromStatus(status, 2)?.state).toBe("done");
    expect(jobFromStatus(status, 99)).toBeNull();
    expect(jobFromStatus(null, 1)).toBeNull();
  });

  it("classifies terminal states", () => {
    expect(isJobTerminal("done")).toBe(true);
    expect(isJobTerminal("FAILED")).toBe(true);
    expect(isJobTerminal("cancelled")).toBe(true);
    expect(isJobTerminal("running")).toBe(false);
    expect(isJobTerminal("queued")).toBe(false);
    expect(isJobTerminal(null)).toBe(false);
  });
});

describe("latestProof", () => {
  it("returns the most recent proof by collected_at", () => {
    const overview = {
      proofs: [
        proof({ collected_at: "2026-08-19T08:00:00Z", robustness_score: 50 }),
        proof({ collected_at: "2026-08-19T11:00:00Z", robustness_score: 88 }),
        proof({ collected_at: "2026-08-19T09:00:00Z", robustness_score: 60 }),
      ],
    } as unknown as EvolveOverview;
    expect(latestProof(overview)?.robustness_score).toBe(88);
  });

  it("handles empty overviews", () => {
    expect(latestProof(null)).toBeNull();
    expect(latestProof({ proofs: [] } as unknown as EvolveOverview)).toBeNull();
  });
});

describe("verdictTone / proofSummary", () => {
  it("maps verdicts to tones", () => {
    expect(verdictTone("pass")).toBe("good");
    expect(verdictTone("weak")).toBe("warn");
    expect(verdictTone("fail")).toBe("bad");
    expect(verdictTone(null)).toBe("muted");
  });

  it("formats the summary line", () => {
    expect(proofSummary(proof({ verdict: "pass", robustness_score: 91.6 }))).toBe(
      "pass · 92/100",
    );
    expect(proofSummary(null)).toBe("");
  });
});
