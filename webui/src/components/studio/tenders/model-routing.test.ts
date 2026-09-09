// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ModelRouting } from "@/components/studio/tenders/TendersDesk";
import type { TenderDesk, TenderModelRoute } from "@/lib/tenders-api";

const tx = (_key: string, fallback: string, values?: Record<string, string | number>) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ""));

function desk(tasks: TenderModelRoute[], routed: number): TenderDesk {
  return {
    profile: { send_mode: "approval", countries: [], crafts: [], channels: {}, references: [] },
    tenders: [],
    kpis: {},
    sources: [],
    catalog: [],
    queries: [],
    discoveries: [],
    journal: [],
    stages: [],
    send_modes: [],
    models: { enabled: routed > 0, routed, tasks },
  };
}

function render(tasks: TenderModelRoute[], routed = tasks.filter((row) => row.preset).length): string {
  return renderToStaticMarkup(createElement(ModelRouting, { desk: desk(tasks, routed), tx }));
}

describe("tenders model routing", () => {
  it("stays out of the way when the snapshot has no routes", () => {
    expect(render([])).toBe("");
  });

  it("names the Settings model behind each desk task", () => {
    const html = render([
      { task: "qualify", role: "deep", preset: "frontier", model: "qwen/qwen3.8-max" },
      { task: "write", role: "docs", preset: "economy", model: "deepseek/deepseek-v4-flash" },
      { task: "mail", role: "docs", preset: "economy", model: "deepseek/deepseek-v4-flash" },
    ]);
    expect(html).toContain("Each task runs on the model you routed");
    expect(html).toContain("Score and GO / NO-GO");
    expect(html).toContain("qwen/qwen3.8-max");
    expect(html).toContain("deep");
    expect(html).toContain("Write the dossier");
    expect(html).toContain("deepseek/deepseek-v4-flash");
    expect(html).not.toContain("{{");
  });

  it("says so when Settings has no route yet", () => {
    const html = render(
      [{ task: "qualify", role: "deep", preset: "", model: "" }],
      0,
    );
    expect(html).toContain("No model routed - the desk stays on templates");
    expect(html).toContain("not routed");
  });
});
