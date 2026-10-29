import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import ar from "@/i18n/locales/ar/common.json";
import en from "@/i18n/locales/en/common.json";
import fr from "@/i18n/locales/fr/common.json";
import { onCyberLaunch, requestCyberLaunch } from "./cyber-launch";

function lookup(bundle: unknown, key: string): unknown {
  return key.split(".").reduce<unknown>(
    (node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined),
    bundle,
  );
}

const here = dirname(fileURLToPath(import.meta.url));

/** The body of the effect that reacts to the rail click, up to the dependency list. */
function listenerBody(shell: string, hook: string): string {
  const start = shell.indexOf(`${hook}(`);
  expect(start).toBeGreaterThan(0);
  const end = shell.indexOf("}, [", start);
  expect(end).toBeGreaterThan(start);
  return shell.slice(start, end);
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

  it("ships the rail labels in fr, en and ar", () => {
    for (const bundle of [fr, en, ar]) {
      expect(lookup(bundle, "dev.cyberTab")).toBe("Cyber");
      expect(typeof lookup(bundle, "dev.cyberHint")).toBe("string");
    }
  });

  it("puts Cyber in the Code rail right above Reverse and switches the chat to Security", () => {
    const rail = readFileSync(join(here, "../components/dev/DevWorkbench.tsx"), "utf8");
    const cyber = rail.indexOf('key: "cyber"');
    const reverse = rail.indexOf("go: () => requestReverseLaunch()");
    expect(cyber).toBeGreaterThan(0);
    expect(cyber).toBeLessThan(reverse);
    expect(rail.slice(cyber, reverse)).toContain("go: () => requestCyberLaunch()");

    const shell = readFileSync(join(here, "../components/thread/ThreadShell.tsx"), "utf8");
    expect(listenerBody(shell, "onCyberLaunch")).toContain('handleComposerTurnModeChange("security")');
  });

  it("switches silently: no 'Security mode is on' line is written in the journal", () => {
    const shell = readFileSync(join(here, "../components/thread/ThreadShell.tsx"), "utf8");
    const body = listenerBody(shell, "onCyberLaunch");
    expect(body).not.toContain("setMessages");
    expect(body).not.toMatch(/Intro/);
    expect(shell).not.toContain("cyber-intro");
    // The sentence itself is gone from every bundle, not just unused.
    for (const bundle of [fr, en, ar]) {
      expect(lookup(bundle, "thread.composer.mode.securityIntro")).toBeUndefined();
    }
  });
});
