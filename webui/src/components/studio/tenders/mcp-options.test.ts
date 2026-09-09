// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TendersMcpOptions } from "@/components/studio/tenders/TendersDesk";
import type { TenderDesk } from "@/lib/tenders-api";

const tx = (_key: string, fallback: string, values?: Record<string, string | number>) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ""));

function desk(mcp: NonNullable<TenderDesk["stack"]>["mcp"]): TenderDesk {
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
    stack: mcp ? { mcp } : undefined,
  };
}

function render(mcp: NonNullable<TenderDesk["stack"]>["mcp"]): string {
  return renderToStaticMarkup(createElement(TendersMcpOptions, { desk: desk(mcp), tx }));
}

describe("tenders MCP options", () => {
  it("stays hidden when the snapshot has no MCP", () => {
    expect(render(undefined)).toBe("");
    expect(render([])).toBe("");
  });

  it("names LinkedIn as the recommended buyer-research option", () => {
    const html = render([
      { id: "exa", name: "Exa", role: "Public research on official hosts" },
      {
        id: "linkedin",
        name: "LinkedIn",
        recommended: true,
        role: "Recommended buyer research: company pages, contacts and posts via your LinkedIn session",
        confirm: ["connect_with_person", "send_message"],
        jobs: ["search_jobs", "get_saved_jobs", "get_job_details"],
        groups: {
          company: ["get_company_profile", "get_company_employees"],
          people: ["search_people", "get_person_profile"],
          posts: ["get_feed", "search_posts"],
          inbox: ["get_inbox", "get_conversation"],
          jobs: ["search_jobs", "get_job_details"],
          session: ["close_session"],
        },
      },
    ]);
    expect(html).toContain("Optional MCP for the buyer, never for the notice");
    expect(html).toContain("LinkedIn is the recommended option");
    expect(html).toContain("Recommended");
    expect(html).toContain("Open Settings &gt; Tools");
    expect(html).toContain("LinkedIn");
    expect(html).toContain("Exa");
    expect(html).toContain("get_company_profile");
    expect(html).toContain("get_feed");
    expect(html).toContain("close_session");
    expect(html).toContain("send_message");
    expect(html).toContain("search_jobs");
    expect(html).toContain("uvx mcp-server-linkedin@latest --login");
    expect(html).toContain("Job tools stay hiring context");
    expect(html).not.toContain("{{");
  });
});
