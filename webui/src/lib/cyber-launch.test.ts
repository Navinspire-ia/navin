import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import ar from "@/i18n/locales/ar/common.json";
import en from "@/i18n/locales/en/common.json";
import fr from "@/i18n/locales/fr/common.json";
import type { UIMessage } from "@/lib/types";
import {
  CYBER_INTRO_FALLBACK,
  CYBER_INTRO_KEY,
  appendCyberIntro,
  buildCyberIntroMessage,
  onCyberLaunch,
  requestCyberLaunch,
} from "./cyber-launch";

function lookup(bundle: unknown, key: string): unknown {
  return key.split(".").reduce<unknown>(
    (node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined),
    bundle,
  );
}

describe("cyber launch", () => {
  it("delivers a rail click to the thread listener and stops after unsubscribe", () => {
    const target = new EventTarget();
    const handler = vi.fn();
    const off = onCyberLaunch(handler, target);
    requestCyberLaunch(target);
    expect(handler).toHaveBeenCalledTimes(1);
    off();
    requestCyberLaunch(target);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("builds the journal question from the Navin language", () => {
    const t = vi.fn((key: string, options: { defaultValue: string }) =>
      String(lookup(fr, key) ?? options.defaultValue),
    );
    const message = buildCyberIntroMessage(t, 42);
    expect(t).toHaveBeenCalledWith(CYBER_INTRO_KEY, { defaultValue: CYBER_INTRO_FALLBACK });
    expect(message).toMatchObject({ id: "cyber-intro-42", role: "assistant", createdAt: 42 });
    expect(message.content).toBe(lookup(fr, CYBER_INTRO_KEY));
  });

  it("ships a real fr, en and ar question with the rail labels", () => {
    const intros = [fr, en, ar].map((bundle) => lookup(bundle, CYBER_INTRO_KEY));
    for (const intro of intros) {
      expect(typeof intro).toBe("string");
      expect(intro as string).not.toMatch(/[\u2013\u2014]/);
    }
    expect(new Set(intros).size).toBe(3);
    expect(lookup(en, CYBER_INTRO_KEY)).toBe(CYBER_INTRO_FALLBACK);
    for (const bundle of [fr, en, ar]) {
      expect(lookup(bundle, "dev.cyberTab")).toBe("Cyber");
      expect(typeof lookup(bundle, "dev.cyberHint")).toBe("string");
    }
  });

  it("puts Cyber in the Code rail right above Reverse and switches the chat to Security", () => {
    const rail = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../components/dev/DevWorkbench.tsx"),
      "utf8",
    );
    const cyber = rail.indexOf('key: "cyber"');
    const reverse = rail.indexOf("go: () => requestReverseLaunch()");
    expect(cyber).toBeGreaterThan(0);
    expect(cyber).toBeLessThan(reverse);
    expect(rail.slice(cyber, reverse)).toContain("go: () => requestCyberLaunch()");

    const shell = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../components/thread/ThreadShell.tsx"),
      "utf8",
    );
    const listener = shell.indexOf("onCyberLaunch(");
    expect(listener).toBeGreaterThan(0);
    expect(shell.slice(listener, listener + 400)).toContain('handleComposerTurnModeChange("security")');
  });

  it("does not stack the question on repeated clicks", () => {
    const user: UIMessage = { id: "u1", role: "user", content: "hi", createdAt: 1 };
    const intro = buildCyberIntroMessage((_key, options) => options.defaultValue, 2);
    const once = appendCyberIntro([user], intro);
    expect(once).toEqual([user, intro]);
    expect(appendCyberIntro(once, buildCyberIntroMessage((_k, o) => o.defaultValue, 3))).toBe(once);
  });
});
