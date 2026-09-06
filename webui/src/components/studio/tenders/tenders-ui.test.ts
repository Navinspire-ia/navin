import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  CHANNELS_HASH,
  MODELS_HASH,
  FILTER_CALLOUT,
  FILL_BUTTON_STYLES,
  HERO_ACTIONS_CLASS,
  NOTICE_ROW_MENU,
  OfficialLink,
  SURFACE,
  accessLabel,
  coverageLabel,
  lastSentMail,
  lastUnsentMail,
  openInIdeHash,
  chapterTitle,
} from "@/components/studio/tenders/tenders-ui";
import type { TenderSource } from "@/lib/tenders-api";

const tx = (_key: string, fallback: string) => fallback;

function row(partial: Partial<TenderSource>): TenderSource {
  return {
    id: "x",
    name: "X",
    country: "FR",
    priority: "P1",
    ingest: "html",
    url: "https://example.gov",
    ...partial,
  };
}

describe("tenders IDE hashes stay inside the WebView", () => {
  it("keeps Settings Channels and Models on hash routes", () => {
    expect(CHANNELS_HASH).toBe("#/settings?section=tools");
    expect(MODELS_HASH).toBe("#/settings?section=models");
  });

  it("never hands an IDE hash to window.open", () => {
    const source = `${openInIdeHash.toString()}\n${OfficialLink.toString()}`;
    expect(source).not.toContain("window.open");
    expect(source).toContain("location.hash");
  });

  it("keeps notice menus open on Tauri WebView scroll", () => {
    expect(NOTICE_ROW_MENU.calloutProps?.preventDismissOnScroll).toBe(true);
    expect(NOTICE_ROW_MENU.calloutProps?.preventDismissOnResize).toBe(true);
  });

  it("keeps result-filter dropdowns open on Tauri WebView scroll", () => {
    expect(FILTER_CALLOUT?.preventDismissOnScroll).toBe(true);
    expect(FILTER_CALLOUT?.preventDismissOnResize).toBe(true);
    expect(FILTER_CALLOUT).toBe(NOTICE_ROW_MENU.calloutProps);
  });
});

describe("tenders source table labels", () => {
  it("maps access to the four registry columns", () => {
    expect(accessLabel(row({ access: "api" }), tx)).toBe("API");
    expect(accessLabel(row({ access: "opendata" }), tx)).toBe("Open Data");
    expect(accessLabel(row({ access: "api_key" }), tx)).toBe("API + key");
    expect(accessLabel(row({ access: "covered", covered_by: "ted" }), tx)).toBe("TED");
    expect(accessLabel(row({ access: "scrape" }), tx)).toBe("Scraping");
  });

  it("keeps coverage labels honest for the same rows", () => {
    expect(coverageLabel(row({ coverage: "api" }), tx)).toBe("live API");
    expect(coverageLabel(row({ coverage: "search" }), tx)).toBe("scrape + web search");
    expect(coverageLabel(row({ coverage: "covered", covered_by: "ted" }), tx)).toBe(
      "covered by {{src}}",
    );
  });
});

describe("tenders loop chrome", () => {
  it("exposes Start, Pause and Run cycle, with an icon-only refresh on the right", () => {
    const workspace = readFileSync(resolve(__dirname, "TendersWorkspace.tsx"), "utf8");
    expect(workspace).toContain('data-testid="tenders-start-loop"');
    expect(workspace).toContain('data-testid="tenders-pause-loop"');
    expect(workspace).not.toContain('data-testid="tenders-schedule"');
    expect(workspace).toContain('data-testid="tenders-run-cycle"');
    expect(workspace).toContain('data-testid="tenders-refresh"');
    expect(workspace).toContain('data-testid="tenders-chat"');
    expect(workspace).toContain("IconButton");
    expect(workspace).toContain('iconName: "Refresh"');
    expect(workspace).toContain('iconName: "Chat"');
    expect(workspace).not.toContain('text={tx("refresh"');
    expect(workspace).toContain('tx("chat", "Chat")');
    expect(workspace).toContain('run("stop")');
    expect(workspace).toContain('run("tick", { force: true })');
    expect(workspace).toContain("setScheduleOpen(true)");
    expect(workspace).toContain(': "desk"');
    expect(workspace).not.toContain('deskPane === "tenders"\n                        ? "tenders"');
    expect(workspace).not.toContain('pane === "home"');
    expect(workspace).toContain('desk?.loop?.phase === "hunt"');
    expect(workspace).not.toContain("desk?.loop?.enabled ||");
    expect(workspace).not.toContain("\u2014");
    expect(workspace).not.toContain("\u2013");
    expect(workspace).toContain("overflow-x-hidden");
    expect(workspace).toContain('data-testid="tenders-scroll"');
    expect(workspace).toContain("overflow-y-auto");
    expect(workspace).toContain('run("stage", { id, stage: "go" })');
    expect(workspace).toContain('run("stage", { id, stage: "no-go" })');
  });

  it("shows Autopilot and the heartbeat lane on the dashboard", () => {
    const desk = readFileSync(resolve(__dirname, "TendersDesk.tsx"), "utf8");
    expect(desk).toContain('data-testid="tenders-autopilot-badge"');
    expect(desk).toContain('data-testid="tenders-heartbeat-lane"');
    expect(desk).toContain("watchLane");
    expect(desk).toContain("autopilotHunt");
    expect(desk).not.toContain("\u2014");
    expect(desk).not.toContain("\u2013");
    expect(desk).toContain("TenderFactsRow");
    expect(desk).toContain("flex-nowrap");
    expect(desk).toContain("TenderNoticeDialog");
    expect(desk).not.toContain("match_reasons");
    expect(desk).not.toContain("fr manquant");
    expect(desk).toContain('data-testid="tenders-notice-chips"');
    expect(desk).toContain("flex-nowrap");
    expect(desk).toContain("const filter = picked ?? \"all\"");
    expect(desk).not.toContain('baseCounts.play > 0');
    expect(desk.indexOf('id: "all"')).toBeLessThan(desk.indexOf('id: "play"'));
    expect(desk).not.toContain('useState<NoticeLayout>("list")');
    expect(desk).not.toContain('data-testid="tenders-layout-list"');
    expect(desk).not.toContain('data-testid="tenders-layout-cards"');
    expect(desk).not.toContain("layoutCards");
    expect(desk).toContain('layout = "list"');
    expect(desk).toContain("tenders-notice-table");
    expect(desk).not.toContain("min-w-[72rem]");
    expect(desk).toContain("NoticeGoButtons");
    expect(desk).toContain('onAction("go"');
    expect(desk).toContain('onAction("nogo"');
    expect(desk).toContain('key: "go"');
    expect(desk).toContain('key: "nogo"');
  });
});

describe("tenders dashboard stays inside the pane", () => {
  it("lets hero actions wrap instead of overflowing", () => {
    expect(HERO_ACTIONS_CLASS).toContain("min-w-0");
    expect(HERO_ACTIONS_CLASS).toContain("minmax");
    expect(HERO_ACTIONS_CLASS).not.toContain("xl:grid-cols-3");
    expect(FILL_BUTTON_STYLES.root).toMatchObject({
      minWidth: 0,
      maxWidth: "100%",
      width: "100%",
    });
    expect(FILL_BUTTON_STYLES.root).not.toMatchObject({ overflow: "hidden" });
    expect(FILL_BUTTON_STYLES.label).toMatchObject({ whiteSpace: "normal" });
    expect(FILL_BUTTON_STYLES.label).not.toMatchObject({ overflow: "hidden" });
    expect(SURFACE).toContain("min-w-0");
  });
});

describe("last unsent tender mail", () => {
  it("prefers the newest draft that has not left yet", () => {
    const mail = [
      { kind: "clarification", sent: true, body: "old" },
      { kind: "followup", sent: false, body: "draft" },
      { kind: "clarification", sent: true, body: "later sent" },
    ];
    expect(lastUnsentMail(mail)?.kind).toBe("followup");
    expect(lastSentMail(mail)?.body).toBe("later sent");
    expect(lastUnsentMail([{ kind: "go", sent: true }])).toBeUndefined();
  });
});

describe("dossier chapter titles follow the notice language", () => {
  it("uses French headings on a French reply even if the UI is English", () => {
    expect(chapterTitle("fr", "cover", "Cover page")).toBe("Page de garde");
    expect(chapterTitle("fr", "staffing", "Staffing")).toBe("Equipe");
    expect(chapterTitle("fr", "letter", "Submission letter")).toBe("Lettre de candidature");
    expect(chapterTitle("en", "cover", "Cover page")).toBe("Cover page");
  });
});
