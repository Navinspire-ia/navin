// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ProofRunCard, proofKpis } from "./ProofRunCard";
import type { ProofReport } from "@/lib/evolve-api";

const proof: ProofReport = {
  schema: "proof/1",
  commit: "abc",
  profile: "standard",
  collected_at: "2026-10-24T09:38:10Z",
  verdict: "pass",
  robustness_score: 100,
  faults: [
    { fault: "load", description: "48 concurrent clients for 12s", verdict: "pass", checks: [], evidence: ["9615 req, p95 125.5 ms, p99 472.5 ms, 801.3 rps"] },
    { fault: "network_chaos", description: "+40ms latency", verdict: "pass", checks: [], evidence: ["under chaos: 5487 req, p95 78.2 ms; after chaos: p95 6.8 ms, 1982 rps"] },
    { fault: "kill_recovery", description: "hard-kill then restart", verdict: "pass", checks: [] },
  ],
};

const prove = (state: unknown) => ({ state, available: true, start: () => undefined, reset: () => undefined }) as never;

describe("robustness proof in the conversation", () => {
  it("sums the load numbers into KPIs", () => {
    expect(proofKpis(proof)).toEqual({ requests: 15102, p95: 125.5, p99: 472.5, rps: 1982 });
  });

  it("shows what the run is doing while it runs", () => {
    const html = renderToStaticMarkup(createElement(ProofRunCard, {
      prove: prove({ phase: "started", job: 1, startedAt: Date.now(), progress: { state: "running", detail: "load: 48 clients" } }),
      href: "#/code",
    }));
    expect(html).toContain("Running");
    expect(html).toContain("load: 48 clients");
  });

  it("ends with the verdict, the score, the scenarios and their numbers", () => {
    const html = renderToStaticMarkup(createElement(ProofRunCard, {
      prove: prove({ phase: "done", job: 1, proof, diagnosis: null, startedAt: 0, endedAt: 60_000 }),
      href: "#/code",
    }));
    expect(html).toContain("Passed");
    expect(html).toContain("100/100");
    expect(html).toContain("3/3");
    expect(html.split('data-testid="proof-run-fault"').length - 1).toBe(3);
  });

  it("stays out of the way when no proof was asked for", () => {
    expect(renderToStaticMarkup(createElement(ProofRunCard, { prove: prove({ phase: "idle" }), href: "" }))).toBe("");
  });
});
