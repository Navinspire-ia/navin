import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import ar from "@/i18n/locales/ar/common.json";
import en from "@/i18n/locales/en/common.json";
import fr from "@/i18n/locales/fr/common.json";
import type { UIMessage } from "@/lib/types";
import {
  REVIEW_INTRO_FALLBACK,
  REVIEW_INTRO_KEY,
  appendReviewIntro,
  buildReviewIntroMessage,
  onReviewLaunch,
  requestReviewLaunch,
} from "./review-launch";

function lookup(bundle: unknown, key: string): unknown {
  return key.split(".").reduce<unknown>(
    (node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined),
    bundle,
  );
}

const here = dirname(fileURLToPath(import.meta.url));

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

  it("builds the journal question from the Navin language", () => {
    const t = vi.fn((key: string, options: { defaultValue: string }) =>
      String(lookup(fr, key) ?? options.defaultValue),
    );
    const message = buildReviewIntroMessage(t, 42);
    expect(t).toHaveBeenCalledWith(REVIEW_INTRO_KEY, { defaultValue: REVIEW_INTRO_FALLBACK });
    expect(message).toMatchObject({ id: "review-intro-42", role: "assistant", createdAt: 42 });
    expect(message.content).toBe(lookup(fr, REVIEW_INTRO_KEY));
  });

  it("ships a real fr, en and ar question with the rail labels", () => {
    const intros = [fr, en, ar].map((bundle) => lookup(bundle, REVIEW_INTRO_KEY));
    for (const intro of intros) {
      expect(typeof intro).toBe("string");
      expect(intro as string).not.toMatch(/[\u2013\u2014]/);
    }
    expect(new Set(intros).size).toBe(3);
    expect(lookup(en, REVIEW_INTRO_KEY)).toBe(REVIEW_INTRO_FALLBACK);
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
    const listener = shell.indexOf("onReviewLaunch(");
    expect(listener).toBeGreaterThan(0);
    expect(shell.slice(listener, listener + 400)).toContain('handleComposerTurnModeChange("review")');
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

  it("does not stack the question on repeated clicks", () => {
    const user: UIMessage = { id: "u1", role: "user", content: "hi", createdAt: 1 };
    const intro = buildReviewIntroMessage((_key, options) => options.defaultValue, 2);
    const once = appendReviewIntro([user], intro);
    expect(once).toEqual([user, intro]);
    expect(appendReviewIntro(once, buildReviewIntroMessage((_k, o) => o.defaultValue, 3))).toBe(once);
  });
});
