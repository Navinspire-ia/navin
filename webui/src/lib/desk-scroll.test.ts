import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const chrome = readFileSync(resolve(__dirname, "../globals.css"), "utf8");
const app = readFileSync(resolve(__dirname, "../App.tsx"), "utf8");
const settings = readFileSync(
  resolve(__dirname, "../components/settings/SettingsView.tsx"),
  "utf8",
);

describe("desk scroll under native-host chrome", () => {
  it("hosts every workbench view under one scroll host", () => {
    expect(app).toContain('data-desk-scroll-host=""');
    expect(app).toContain(
      'className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden"\n                  data-desk-scroll-host=""',
    );
  });

  it("hosts Settings so Models and the other tabs keep a visible scrollbar", () => {
    expect(settings).toContain('data-settings-scroll-host=""');
    expect(settings).toContain("overscroll-contain [scrollbar-gutter:stable]");
  });

  it("keeps a thin visible scrollbar on desk scroll containers", () => {
    expect(chrome).toContain(
      'html.native-host :is([data-desk-scroll-host], [data-settings-scroll-host]) [class*="overflow-y-auto"]',
    );
    expect(chrome).toContain(
      'html.native-host :is([data-desk-scroll-host], [data-settings-scroll-host]) [class*="overflow-auto"]',
    );
    expect(chrome).toContain(
      '[data-settings-scroll-host]) [class*="overflow-y-auto"]::-webkit-scrollbar',
    );
    expect(chrome).toContain("scrollbar-width: thin !important;");
  });
});
