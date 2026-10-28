import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import ar from "@/i18n/locales/ar/common.json";
import en from "@/i18n/locales/en/common.json";
import fr from "@/i18n/locales/fr/common.json";
import { OPEN_SETTINGS_SECTION_EVENT, openSettingsSection } from "@/lib/settings-events";
import type { UIMessage } from "@/lib/types";
import {
  REVERSE_INTRO_FALLBACK,
  REVERSE_INTRO_KEY,
  appendReverseIntro,
  buildReverseIntroMessage,
  onReverseLaunch,
  requestReverseLaunch,
} from "./reverse-launch";

function lookup(bundle: unknown, key: string): unknown {
  return key.split(".").reduce<unknown>(
    (node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined),
    bundle,
  );
}

describe("reverse launch", () => {
  it("delivers a rail click to the thread listener and stops after unsubscribe", () => {
    const target = new EventTarget();
    const handler = vi.fn();
    const off = onReverseLaunch(handler, target);
    requestReverseLaunch(target);
    expect(handler).toHaveBeenCalledTimes(1);
    off();
    requestReverseLaunch(target);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("builds the journal question from the Navin language", () => {
    const t = vi.fn((key: string, options: { defaultValue: string }) =>
      String(lookup(fr, key) ?? options.defaultValue),
    );
    const message = buildReverseIntroMessage(t, 42);
    expect(t).toHaveBeenCalledWith(REVERSE_INTRO_KEY, { defaultValue: REVERSE_INTRO_FALLBACK });
    expect(message).toMatchObject({ id: "reverse-intro-42", role: "assistant", createdAt: 42 });
    expect(message.content).toBe(lookup(fr, REVERSE_INTRO_KEY));
  });

  it("ships a real fr, en and ar question with the rail labels", () => {
    const intros = [fr, en, ar].map((bundle) => lookup(bundle, REVERSE_INTRO_KEY));
    for (const intro of intros) {
      expect(typeof intro).toBe("string");
      expect(intro as string).not.toMatch(/[\u2013\u2014]/);
    }
    expect(new Set(intros).size).toBe(3);
    expect(lookup(en, REVERSE_INTRO_KEY)).toBe(REVERSE_INTRO_FALLBACK);
    for (const bundle of [fr, en, ar]) {
      expect(lookup(bundle, "dev.reverseTab")).toBe("Reverse");
      expect(typeof lookup(bundle, "dev.reverseNew")).toBe("string");
      expect(typeof lookup(bundle, "dev.reverseHint")).toBe("string");
      expect(lookup(bundle, "dev.teamTab")).toBe("Team");
      expect(lookup(bundle, "dev.teamHint") as string).not.toMatch(/[\u2013\u2014]/);
    }
  });

  it("puts Team with a NEW badge right above Reverse and opens Settings > Team", () => {
    const source = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../components/dev/DevWorkbench.tsx"),
      "utf8",
    );
    const team = source.indexOf('key: "team"');
    const reverse = source.indexOf("go: () => requestReverseLaunch()");
    expect(team).toBeGreaterThan(0);
    expect(team).toBeLessThan(reverse);
    const entry = source.slice(team, reverse);
    expect(entry).toContain('tx("dev.reverseNew", "NEW")');
    expect(entry).toContain('go: () => openSettingsSection("team")');
  });

  it("draws the Team, Cyber and Reverse NEW markers as quiet dots, not yellow text pills", () => {
    const source = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../components/dev/DevWorkbench.tsx"),
      "utf8",
    );
    const team = source.indexOf('key: "team"');
    const reverse = source.indexOf("go: () => requestReverseLaunch()");
    const entries = source.slice(team, reverse);
    // Every launcher (Team, Cyber, Reverse) asks for the dot, and the loud yellow pill is gone.
    expect(entries.match(/dot: true/g)).toHaveLength(3);
    expect(source).not.toContain("bg-yellow-400 text-yellow-950");
    // The dot keeps the NEW label for assistive tech and renders a small round marker.
    const render = source.slice(source.indexOf("entry.badge?.dot ?"));
    expect(render).toContain("aria-label={entry.badge.text}");
    expect(render).toContain("h-1.5 w-1.5 shrink-0 rounded-full");
  });

  it("routes a Team click to the Settings section listener", () => {
    const seen: string[] = [];
    const target = new EventTarget();
    vi.stubGlobal("window", target);
    const listener = (event: Event) => seen.push((event as CustomEvent<{ section: string }>).detail.section);
    target.addEventListener(OPEN_SETTINGS_SECTION_EVENT, listener);
    try {
      openSettingsSection("team");
    } finally {
      vi.unstubAllGlobals();
    }
    expect(seen).toEqual(["team"]);
  });

  it("does not stack the question on repeated clicks", () => {
    const user: UIMessage = { id: "u1", role: "user", content: "hi", createdAt: 1 };
    const intro = buildReverseIntroMessage((_key, options) => options.defaultValue, 2);
    const once = appendReverseIntro([user], intro);
    expect(once).toEqual([user, intro]);
    expect(appendReverseIntro(once, buildReverseIntroMessage((_k, o) => o.defaultValue, 3))).toBe(once);
  });
});
