// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PipelineList } from "@/components/studio/tenders/TendersDesk";
import { TenderNoticeDetail } from "@/components/studio/tenders/TenderNoticeDialog";
import type { TenderNotice } from "@/lib/tenders-api";

const tx = (key: string, fallback: string) => {
  if (key === "unknown") return "non renseigne";
  if (key === "noticeStatus.open") return "Ouvert";
  if (key === "langFr") return "Francais";
  return fallback.replace(/\{\{(\w+)\}\}/g, "");
};

function notice(partial: Partial<TenderNotice> = {}): TenderNotice {
  return {
    id: "tn-1",
    source_id: "ted",
    country: "FR",
    title: "Senior Data platform rebuild for a public buyer in several regions",
    stage: "matched",
    score: 77,
    ...partial,
  };
}

describe("tender notice cards", () => {
  it("prints a board card: pills, title, buyer, excerpt and a facts column without unknown values", () => {
    const html = renderToStaticMarkup(
      createElement(PipelineList, {
        rows: [
          notice({
            status: "open",
            buyer: "DINUM",
            deadline: "2026-09-15",
            description: "<p>Are you a talented Senior Data Engineer looking for a remote...</p>",
          }),
        ],
        view: "pipeline",
        tx,
        busy: false,
        token: "tok",
        onAction: () => {},
      }),
    );
    expect(html).toContain('data-testid="tenders-notice-card"');
    expect(html).toContain("flex-nowrap");
    expect(html).toContain('data-testid="tenders-facts-row"');
    expect(html).toContain("Ouvert");
    expect(html).toContain("DINUM");
    expect(html).toContain("Deadline");
    expect(html).toContain("France");
    expect(html).toContain("View this notice");
    expect(html).toContain("77%");
    expect(html).toContain('data-testid="tenders-notice-favorite"');
    // Empty facts stay off the card: no Budget / Duration placeholders.
    expect(html).not.toContain("non renseigne");
    expect(html).not.toContain("Budget");
    expect(html).toContain("Are you a talented Senior Data Engineer");
    expect(html).not.toContain("<p>Are you");
    expect(html).not.toContain("fr manquant");
    expect(html).not.toContain("match_reasons");
    expect(html).not.toContain("\u2014");
    expect(html).not.toContain("\u2013");
    expect(html).not.toContain('data-testid="tenders-notice-go"');
    expect(html).not.toContain('data-testid="tenders-notice-nogo"');
  });

  it("no longer renders a table: the list is a stack of cards", () => {
    const html = renderToStaticMarkup(
      createElement(PipelineList, {
        rows: [notice({ status: "open", buyer: "DINUM", deadline: "2026-09-15" })],
        view: "pipeline",
        tx,
        busy: false,
        token: "tok",
        onAction: () => {},
      }),
    );
    expect(html).toContain('data-testid="tenders-notice-list"');
    expect(html).not.toContain("<table");
    expect(html).not.toContain("tenders-notice-table");
    expect(html).not.toContain("min-w-[72rem]");
    expect(html).not.toContain('data-testid="tenders-notice-grid"');
  });

  it("opens a cleaned detail surface without raw HTML", () => {
    const html = renderToStaticMarkup(
      createElement(TenderNoticeDetail, {
        notice: notice({
          description: "<p>Are you a talented Senior Data Engineer looking for a remote...</p>",
          status: "open",
          buyer: "lemon.io",
        }),
        tx,
        token: "tok",
      }),
    );
    expect(html).toContain('data-testid="tenders-notice-detail"');
    expect(html).toContain("Are you a talented Senior Data Engineer looking for a remote");
    expect(html).not.toContain("<p>Are you");
    expect(html).toContain("flex-nowrap");
    expect(html).toContain("lemon.io");
    expect(html).not.toContain("fr manquant");
  });
});
