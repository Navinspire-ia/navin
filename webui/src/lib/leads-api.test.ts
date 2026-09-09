// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  LEADS_DESK_HASH,
  crmProjectLabel,
  emptyLeadsDesk,
  leadHasBuyingSignal,
  leadHasDueStep,
  leadInOutreach,
  leadNextStep,
  leadOutreachDest,
  leadOutreachWhy,
  leadSourceLabel,
  leadsDeskHash,
  officialLeadHref,
  resolveLeadsCrmProject,
  usableCrmProject,
} from "./leads-api";

describe("leads sequences and sources", () => {
  it("finds the next unsent step with its draft", () => {
    const row = {
      id: "a",
      company: "Acme",
      sequence: {
        steps: [
          { n: 2, status: "pending", draft: { subject: "Re: Acme", body: "follow-up" } },
          { n: 1, status: "sent", draft: { subject: "Acme", body: "first" } },
          { n: 3, status: "pending" },
        ],
      },
    };
    expect(leadNextStep(row)?.n).toBe(2);
    expect(leadNextStep(row)?.draft?.subject).toBe("Re: Acme");
    expect(leadNextStep({ id: "b", company: "Nobody" })).toBeNull();
    expect(
      leadNextStep({ id: "c", company: "Done", sequence: { steps: [{ n: 1, status: "sent" }] } }),
    ).toBeNull();
  });

  it("never surfaces a step from a stopped sequence", () => {
    const stopped = {
      id: "d",
      company: "Opted out",
      sequence: { stopped: "opt-out", steps: [{ n: 1, status: "due", due_at: 10 }] },
    };
    expect(leadNextStep(stopped)).toBeNull();
    expect(leadHasDueStep(stopped, 100)).toBe(false);
    const live = { ...stopped, sequence: { steps: stopped.sequence.steps } };
    expect(leadNextStep(live)?.n).toBe(1);
    expect(leadHasDueStep(live, 100)).toBe(true);
  });

  it("labels sources for badges and keeps unknown ones readable", () => {
    expect(leadSourceLabel("sirene")).toBe("SIRENE");
    expect(leadSourceLabel("osm")).toBe("OpenStreetMap");
    expect(leadSourceLabel("hiring")).toBe("Hiring");
    expect(leadSourceLabel("companies_house")).toBe("Companies House");
    expect(leadSourceLabel("zoominfo")).toBe("Zoominfo");
    expect(leadSourceLabel("")).toBe("");
  });

  it("starts with approval mode and every open source on", () => {
    const desk = emptyLeadsDesk();
    expect(desk.profile.execution_mode).toBe("approval");
    expect(desk.profile.sources).toEqual(["web", "osm", "hiring"]);
    expect(desk.profile.daily_send_cap).toBe(20);
  });
});

describe("leads desk links", () => {
  it("keeps the desk hash inside the IDE", () => {
    expect(LEADS_DESK_HASH).toBe("#/leads");
    expect(leadsDeskHash()).toBe("#/leads");
    expect(leadsDeskHash({ pane: "book" })).toBe("#/leads?pane=book");
    expect(leadsDeskHash({ lead: "abc", chat: "websocket:1" })).toBe(
      "#/leads?chat=websocket%3A1&lead=abc",
    );
    expect(leadsDeskHash({ pane: "book", lead: "abc" })).toBe("#/leads?lead=abc");
  });

  it("sends official company and LinkedIn links out of the IDE", () => {
    expect(officialLeadHref("https://www.datadoghq.com")).toContain("datadoghq.com");
    expect(officialLeadHref("https://www.linkedin.com/in/someone")).toContain("linkedin.com");
    expect(officialLeadHref("datadoghq.com")).toContain("datadoghq.com");
    expect(officialLeadHref("linkedin.com/in/someone")).toContain("linkedin.com");
    expect(officialLeadHref("#/leads")).toBeNull();
    expect(officialLeadHref("/leads")).toBeNull();
    expect(officialLeadHref("http://tauri.localhost/#/leads")).toBeNull();
    expect(officialLeadHref("tauri://localhost/#/leads?lead=abc")).toBeNull();
  });

  it("detects due sequence steps and buying signals", () => {
    expect(
      leadHasDueStep({
        id: "ld-1",
        company: "Acme",
        sequence: { steps: [{ n: 1, status: "due", due_at: 1 }] },
      }),
    ).toBe(true);
    expect(
      leadHasDueStep({
        id: "ld-1",
        company: "Acme",
        sequence: { steps: [{ n: 1, status: "sent", due_at: 1 }] },
      }),
    ).toBe(false);
    expect(leadHasBuyingSignal({ id: "ld-1", company: "Acme", signals: [{ kind: "funding" }] })).toBe(true);
    expect(leadHasBuyingSignal({ id: "ld-1", company: "Acme", signals: [{ kind: "verified" }] })).toBe(false);
    expect(
      leadHasDueStep({
        id: "ld-1",
        company: "Acme",
        sequence: {
          steps: [
            { n: 1, status: "due", due_at: 9_999_999_999 },
            { n: 2, status: "pending", due_at: 1 },
          ],
        },
      }),
    ).toBe(false);
    expect(
      leadHasDueStep({
        id: "ld-1",
        company: "Acme",
        sequence: {
          steps: [
            { n: 1, status: "sent", due_at: 1 },
            { n: 2, status: "pending", due_at: 1 },
          ],
        },
      }),
    ).toBe(true);
    expect(leadOutreachDest({ id: "ld-1", company: "Acme", email: "a@acme.io" }, "email")).toBe("a@acme.io");
    expect(leadOutreachDest({ id: "ld-1", company: "Acme", extra: { telegram_to: "99" } }, "telegram")).toBe("99");
    expect(leadOutreachDest({ id: "ld-1", company: "Acme" }, "telegram")).toBe("");
    expect(leadInOutreach({ id: "ld-1", company: "Acme", stage: "new" })).toBe(false);
    expect(leadInOutreach({ id: "ld-1", company: "Acme", stage: "qualified" })).toBe(false);
    expect(leadInOutreach({ id: "ld-1", company: "Acme", stage: "contacted" })).toBe(true);
    expect(
      leadInOutreach({
        id: "ld-1",
        company: "Acme",
        stage: "qualified",
        sequence: { steps: [{ n: 1, status: "due" }] },
      }),
    ).toBe(true);
    expect(leadOutreachWhy({ id: "ld-1", company: "Acme" }, "email", {})).toBe("need-email");
    expect(
      leadOutreachWhy({ id: "ld-1", company: "Acme", email: "a@acme.io" }, "email", {
        email: { ready: false, hint: "Settings > Channels" },
      }),
    ).toBe("need-channel");
    expect(
      leadOutreachWhy({ id: "ld-1", company: "Acme", email: "a@acme.io" }, "email", {
        email: { ready: true },
      }),
    ).toBe("");
  });

  it("resolves a real CRM project and skips the NavinProjects container", () => {
    expect(usableCrmProject("")).toBe("");
    expect(usableCrmProject("/home/aymen/NavinProjects")).toBe("");
    expect(usableCrmProject("/home/aymen/projects/deploy7/navin-ai-v2")).toBe(
      "/home/aymen/projects/deploy7/navin-ai-v2",
    );
    expect(crmProjectLabel("/home/aymen/projects/deploy7/navin-ai-v2")).toBe("navin-ai-v2");
    expect(
      resolveLeadsCrmProject({
        projectPath: "/home/aymen/NavinProjects",
        recentProjects: [{ path: "/home/aymen/NavinProjects" }],
        extraPaths: ["/home/aymen/projects/deploy7/navin-ai-v2"],
      }),
    ).toBe("/home/aymen/projects/deploy7/navin-ai-v2");
    expect(resolveLeadsCrmProject({ projectPath: "/home/aymen/NavinProjects" })).toBe("");
  });
});
