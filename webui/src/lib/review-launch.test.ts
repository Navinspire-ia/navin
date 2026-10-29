import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import ar from "@/i18n/locales/ar/common.json";
import en from "@/i18n/locales/en/common.json";
import fr from "@/i18n/locales/fr/common.json";
import { onReviewLaunch, requestReviewLaunch } from "./review-launch";

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

describe("review launch", () => {
  it("delivers a rail click to the thread listener and stops after unsubscribe", () => {
    const target = new EventTarget();
    const handler = vi.fn();
    const off = onReviewLaunch(handler, target);
    requestReviewLaunch(target);
    expect(handler).toHaveBeenCalledTimes(1);
    off();
    requestReviewLaunch(target);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("ships the rail labels in fr, en and ar", () => {
    for (const bundle of [fr, en, ar]) {
      expect(typeof lookup(bundle, "dev.reviewTab")).toBe("string");
      expect(typeof lookup(bundle, "dev.reviewHint")).toBe("string");
    }
  });

  it("puts Review in the Code rail right under Cyber and switches the chat to Review", () => {
    const rail = readFileSync(join(here, "../components/dev/DevWorkbench.tsx"), "utf8");
    const cyber = rail.indexOf("go: () => requestCyberLaunch()");
    const review = rail.indexOf('key: "review"');
    const reverse = rail.indexOf("go: () => requestReverseLaunch()");
    expect(cyber).toBeGreaterThan(0);
    expect(review).toBeGreaterThan(cyber);
    expect(review).toBeLessThan(reverse);
    expect(rail.slice(review, reverse)).toContain("go: () => requestReviewLaunch()");
    // Review lives in the chat: the click must not restore the right-hand workbench column.
    expect(rail.slice(review, reverse)).toContain("keepLayout: true");

    const shell = readFileSync(join(here, "../components/thread/ThreadShell.tsx"), "utf8");
    expect(listenerBody(shell, "onReviewLaunch")).toContain('handleComposerTurnModeChange("review")');
  });

  it("switches silently: no 'Review mode is on' line is written in the journal", () => {
    const shell = readFileSync(join(here, "../components/thread/ThreadShell.tsx"), "utf8");
    const body = listenerBody(shell, "onReviewLaunch");
    expect(body).not.toContain("setMessages");
    expect(body).not.toMatch(/Intro/);
    expect(shell).not.toContain("review-intro");
    for (const bundle of [fr, en, ar]) {
      expect(lookup(bundle, "thread.composer.mode.reviewIntro")).toBeUndefined();
    }
  });

  it("folds AGI, Guardrails, Graph, Rules and Install skill into the rail's single Other menu", () => {
    const rail = readFileSync(join(here, "../components/dev/DevWorkbench.tsx"), "utf8");
    // One Other menu only: the inline rail entry that duplicated it is gone.
    expect(rail).not.toContain('key: "other"');
    const menu = rail.indexOf("const overflowMenuItems = (");
    expect(menu).toBeGreaterThan(0);
    const block = rail.slice(menu, rail.indexOf("];", menu));
    for (const key of ["skills", "rules", "agi", "guardrails", "graph"]) {
      expect(block).toContain(`key: "${key}"`);
    }
  });
});
