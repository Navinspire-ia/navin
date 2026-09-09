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
  it("keeps title, score and menu on one row and shows a labeled facts table", () => {
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
        layout: "cards",
        tx,
        busy: false,
        token: "tok",
        onAction: () => {},
      }),
    );
    expect(html).toContain("flex-nowrap");
    expect(html).toContain("truncate");
    expect(html).toContain('data-testid="tenders-facts-row"');
    expect(html).toContain("Status");
    expect(html).toContain("Ouvert");
    expect(html).toContain("DINUM");
    expect(html).toContain("non renseigne");
    expect(html).not.toContain("<p>");
    expect(html).not.toContain("fr manquant");
    expect(html).not.toContain("en OK");
    expect(html).not.toContain("match_reasons");
    expect(html).not.toContain("Are you a talented");
    expect(html).not.toContain("\u2014");
    expect(html).not.toContain("\u2013");
    expect(html).not.toContain('data-testid="tenders-notice-go"');
    expect(html).not.toContain('data-testid="tenders-notice-nogo"');
  });

  it("renders a compact table when layout is list", () => {
    const html = renderToStaticMarkup(
      createElement(PipelineList, {
        rows: [
          notice({
            status: "open",
            buyer: "DINUM",
            deadline: "2026-09-15",
          }),
        ],
        view: "pipeline",
        layout: "list",
        tx,
        busy: false,
        token: "tok",
        onAction: () => {},
      }),
    );
    expect(html).toContain('data-testid="tenders-notice-list"');
    expect(html).toContain("tenders-notice-table");
    expect(html).toContain("<table");
    expect(html).toContain("Title");
    expect(html).toContain("DINUM");
    expect(html).not.toContain("Language");
    expect(html).not.toContain("Source");
    expect(html).not.toContain("min-w-[72rem]");
    expect(html).not.toContain("overflow-x-auto");
    expect(html).not.toContain('data-testid="tenders-notice-grid"');
    expect(html).not.toContain('data-testid="tenders-notice-go"');
    expect(html).not.toContain('data-testid="tenders-notice-nogo"');
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
