// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { FollowUpButton, followLine } from "@/components/studio/tenders/TendersDesk";
import type { TenderDesk } from "@/lib/tenders-api";

const tx = (_key: string, fallback: string, values?: Record<string, string | number>) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ""));

function desk(followUp: TenderDesk["follow_up"]): TenderDesk {
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
    follow_up: followUp,
  };
}

describe("tenders follow-up", () => {
  it("names what needs the user on one line per event", () => {
    expect(followLine({ id: "a", key: "go", kind: "go", title: "Plateforme data" }, tx)).toBe("GO to confirm");
    expect(followLine({ id: "b", key: "d3", kind: "deadline", days: 2, title: "Audit cyber" }, tx)).toBe(
      "Due in 2 day(s)",
    );
    expect(followLine({ id: "c", key: "r9", kind: "relance", days: 9, title: "Cyber" }, tx)).toBe(
      "Silent for 9 day(s) since submission",
    );
  });

  it("is a header button that carries the pending count", () => {
    const html = renderToStaticMarkup(
      createElement(FollowUpButton, {
        desk: desk({
          pending: 2,
          events: [
            { id: "a", key: "go", kind: "go", title: "Plateforme data" },
            { id: "b", key: "d3", kind: "deadline", days: 2, title: "Audit cyber" },
          ],
        }),
        tx,
        busy: "",
        onFollow: () => {},
        onOpen: () => {},
      }),
    );
    expect(html).toContain('data-testid="tenders-follow-up"');
    expect(html).toContain("Follow-up · 2");
    // The panel only opens on click: nothing else weighs on the header.
    expect(html).not.toContain("Send the digest now");
    expect(html).not.toContain("One digest goes to every channel");
    const quiet = renderToStaticMarkup(
      createElement(FollowUpButton, { desk: desk({ pending: 0, events: [] }), tx, busy: "", onFollow: () => {}, onOpen: () => {} }),
    );
    expect(quiet).toContain("Follow-up");
    expect(quiet).not.toContain("Follow-up ·");
  });

  it("replaces the banner above the list", () => {
    const workspace = readFileSync(resolve(__dirname, "TendersWorkspace.tsx"), "utf8");
    expect(workspace).toContain("<FollowUpButton");
    expect(workspace).toContain('onFollow={() => void run("follow")}');
    expect(workspace).not.toContain("FollowUpBanner");
    const source = readFileSync(resolve(__dirname, "TendersDesk.tsx"), "utf8");
    expect(source).not.toContain("export function FollowUpBanner");
    expect(source).toContain('data-testid="tenders-follow-send"');
    expect(source).toContain('data-menu-scroll=""');
  });
});
