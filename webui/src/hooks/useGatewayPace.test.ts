import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import { GatewayPaceChip } from "@/components/dev/DevStatusBar";
import {
  noteGatewayAnswered,
  noteGatewayStalled,
  resetGatewayPace,
} from "@/lib/gateway-pace";

import { stallSeconds, useGatewayPace } from "./useGatewayPace";

function PaceProbe() {
  const pace = useGatewayPace();
  return createElement("i", null, `${pace.stall ?? "ok"}:${pace.failures}`);
}

describe("useGatewayPace", () => {
  beforeEach(() => resetGatewayPace());

  it("reads the ambient pace state", () => {
    expect(renderToStaticMarkup(createElement(PaceProbe))).toBe("<i>ok:0</i>");
    noteGatewayStalled("timeout");
    noteGatewayStalled("unreachable");
    expect(renderToStaticMarkup(createElement(PaceProbe))).toBe("<i>unreachable:2</i>");
    noteGatewayAnswered();
    expect(renderToStaticMarkup(createElement(PaceProbe))).toBe("<i>ok:0</i>");
  });

  it("counts whole seconds of the current stall, never negative, zero when healthy", () => {
    expect(stallSeconds(null, 10_000)).toBe(0);
    expect(stallSeconds(10_000, 10_000)).toBe(0);
    expect(stallSeconds(10_000, 13_400)).toBe(3);
    expect(stallSeconds(10_000, 13_600)).toBe(4);
    expect(stallSeconds(20_000, 10_000)).toBe(0);
  });
});

describe("GatewayPaceChip", () => {
  beforeEach(() => resetGatewayPace());

  it("renders nothing while the engine answers", () => {
    expect(renderToStaticMarkup(createElement(GatewayPaceChip))).toBe("");
  });

  it("shows one amber chip for a slow engine and a red one when it is unreachable", () => {
    noteGatewayStalled("timeout");
    const slow = renderToStaticMarkup(createElement(GatewayPaceChip));
    expect(slow).toContain('data-testid="gateway-pace-chip"');
    expect(slow).toContain('data-stall="timeout"');
    expect(slow).toContain('role="status"');
    expect(slow).toContain("text-amber-700");
    expect(slow).not.toMatch(/browser|navigateur|\d+ms/i);

    noteGatewayStalled("unreachable");
    const down = renderToStaticMarkup(createElement(GatewayPaceChip));
    expect(down).toContain('data-stall="unreachable"');
    expect(down).toContain("text-red-500");
  });
});

describe("wiring", () => {
  const read = (rel: string) => readFileSync(resolve(__dirname, rel), "utf8");

  it("puts the chip in the status bar and turns its dot amber while reads retry", () => {
    const bar = read("../components/dev/DevStatusBar.tsx");
    expect(bar).toContain("<GatewayPaceChip />");
    expect(bar).toContain("const stalled = useGatewayPace().stall !== null;");
    // A green dot next to a red chip would contradict it.
    expect(bar).toContain("connected && !stalled");
  });

  it("keeps knocking at boot while the engine (re)starts instead of a dead screen", () => {
    const app = read("../App.tsx");
    expect(app).toContain("isTransportError(e)");
    expect(app).toContain("transient: true");
    expect(app).toContain("BOOT_RETRY_BASE_MS * 2 ** Math.min(retry.attempt - 1, 3)");
    expect(app).toContain('t("app.error.engineWait", { attempt: state.attempt ?? 1 })');
    expect(app).toContain('desktop ? "app.error.desktopHint" : "app.error.gatewayHint"');
    expect(app).toContain('t("app.error.retryNow")');
  });

  it("names the engine, never the browser, in transport and boot copy", () => {
    for (const lang of ["en", "fr"]) {
      const locale = JSON.parse(read(`../i18n/locales/${lang}/common.json`)) as Record<
        string,
        Record<string, unknown>
      >;
      const transport = locale.transport as Record<string, unknown>;
      const pace = transport.pace as Record<string, string>;
      for (const value of [
        transport.timeout,
        transport.unreachable,
        transport.internal,
        pace.slow,
        pace.unreachable,
        pace.slowHint,
        pace.unreachableHint,
      ]) {
        expect(typeof value).toBe("string");
        expect(value as string).not.toMatch(/browser|navigateur|reload|recharge|\d+ms/i);
      }
      const panelError = locale.panelError as Record<string, string>;
      expect(panelError.reloadPage).not.toMatch(/page/i);
      expect(panelError.chunkFailure).not.toMatch(/reload the page|rechargez la page/i);
      const appError = (locale.app as Record<string, Record<string, string>>).error;
      expect(appError.desktopHint).not.toMatch(/browser|navigateur|page/i);
    }
  });
});
