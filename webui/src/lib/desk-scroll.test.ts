import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const chrome = readFileSync(resolve(__dirname, "../globals.css"), "utf8");
const app = readFileSync(resolve(__dirname, "../App.tsx"), "utf8");

describe("desk scroll under native-host chrome", () => {
  it("hosts every workbench view under one scroll host", () => {
    expect(app).toContain('data-desk-scroll-host=""');
    expect(app).toContain(
      'className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden"\n                  data-desk-scroll-host=""',
    );
  });

  it("keeps a thin visible scrollbar on desk scroll containers", () => {
    expect(chrome).toContain('html.native-host [data-desk-scroll-host] [class*="overflow-y-auto"]');
    expect(chrome).toContain('html.native-host [data-desk-scroll-host] [class*="overflow-auto"]');
    expect(chrome).toContain('[data-desk-scroll-host] [class*="overflow-y-auto"]::-webkit-scrollbar');
    expect(chrome).toContain("scrollbar-width: thin !important;");
  });
});
