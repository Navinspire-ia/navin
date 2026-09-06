import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  CareerEmployers,
  employerFeedState,
  parseEmployerInput,
  sortEmployerRows,
} from "@/components/studio/career/CareerEmployers";
import type { CareerEmployerRow, CareerEmployers as Payload } from "@/lib/career-api";

const tx = (_key: string, fallback: string, values?: Record<string, string | number>) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_match, name: string) => String(values?.[name] ?? ""));

function row(id: string, patch: Partial<CareerEmployerRow> = {}): CareerEmployerRow {
  return { id, name: id, kind: "esn", markets: ["FR"], ...patch };
}

describe("employer helpers", () => {
  it("reads the feed state from ats and error", () => {
    expect(employerFeedState(row("a", { ats: "lever" }))).toBe("ready");
    expect(employerFeedState(row("b", { ats: "careers-page" }))).toBe("page");
    expect(employerFeedState(row("c", { ats: "lever", last_error: "HTTP 500" }))).toBe("error");
    expect(employerFeedState(row("d"))).toBe("pending");
    expect(employerFeedState(row("e", { last_error: "no feed" }))).toBe("error");
  });

  it("sorts user rows first, hidden rows last, then esn before agencies", () => {
    const sorted = sortEmployerRows([
      row("agency", { kind: "agency" }),
      row("muted", { hidden: true }),
      row("mine", { user: true, kind: "product" }),
      row("esn"),
    ]);
    expect(sorted.map((item) => item.id)).toEqual(["mine", "esn", "agency", "muted"]);
  });

  it("parses name + url, a bare url, and the pipe shorthand", () => {
    expect(parseEmployerInput("Theodo", "jobs.lever.co/theodo")).toEqual({
      name: "Theodo",
      url: "https://jobs.lever.co/theodo",
    });
    expect(parseEmployerInput("", "https://careers.acme.io/jobs")).toEqual({
      name: "careers.acme.io",
      url: "https://careers.acme.io/jobs",
    });
    expect(parseEmployerInput("Acme | https://acme.io/careers", "")).toEqual({
      name: "Acme",
      url: "https://acme.io/careers",
    });
    expect(parseEmployerInput("", "")).toBeNull();
    expect(parseEmployerInput("Acme", "not a url at all")).toBeNull();
  });
});

describe("CareerEmployers panel", () => {
  const payload: Payload = {
    enabled: true,
    markets: ["FR", "BE"],
    summary: { directory: 3, resolved: 2, by_ats: { lever: 1, greenhouse: 1 }, user: 1, hidden: 0 },
    rows: [
      row("theodo", { name: "Theodo", ats: "lever", last_count: 12, careers_url: "https://jobs.lever.co/theodo" }),
      row("acme", { name: "Acme", user: true, kind: "product", ats: "greenhouse", last_count: 3 }),
      row("dead", { name: "Dead Co", last_error: "HTTP 404" }),
    ],
  };

  it("renders the summary, the toggle and the add form without opening the list", () => {
    const html = renderToStaticMarkup(
      createElement(CareerEmployers, {
        tx,
        employers: payload,
        userEmployers: [{ name: "Acme", url: "https://acme.io/careers" }],
        hidden: [],
        token: "t",
        onSaveProfile: () => {},
      }),
    );
    expect(html).toContain('data-testid="career-employers-summary"');
    expect(html).toContain("Feeds ready");
    expect(html).toContain(">2<");
    expect(html).toContain(">15<");
    expect(html).toContain("Show the 3 houses");
    expect(html).toContain('data-testid="career-employers-toggle"');
    expect(html).toContain('data-testid="career-employers-add-button"');
    expect(html).not.toContain('data-testid="career-employers-list"');
  });

  it("falls back to the visible rows when no summary is present", () => {
    const html = renderToStaticMarkup(
      createElement(CareerEmployers, {
        tx,
        employers: undefined,
        userEmployers: [],
        hidden: [],
        token: "t",
        onSaveProfile: () => {},
      }),
    );
    expect(html).toContain("Show the 0 houses");
    expect(html).toContain("Watching");
  });
});
