import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FollowUpBanner } from "@/components/studio/tenders/TendersDesk";
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

function render(followUp: TenderDesk["follow_up"]): string {
  return renderToStaticMarkup(
    createElement(FollowUpBanner, {
      desk: desk(followUp),
      tx,
      busy: "",
      onFollow: () => {},
      onOpen: () => {},
    }),
  );
}

describe("tenders follow-up banner", () => {
  it("stays out of the way when nothing is waiting", () => {
    expect(render({ pending: 0, events: [] })).toBe("");
  });

  it("names what needs the user and offers a single digest", () => {
    const html = render({
      pending: 2,
      events: [
        { id: "a", key: "go", kind: "go", title: "Plateforme data" },
        { id: "b", key: "d3", kind: "deadline", days: 2, title: "Audit cyber" },
      ],
    });
    expect(html).toContain("2 notice(s) need you");
    expect(html).toContain("Plateforme data");
    expect(html).toContain("Due in 2 day(s)");
    expect(html).toContain("Send the digest now");
    expect(html).not.toContain("{{");
  });
});
