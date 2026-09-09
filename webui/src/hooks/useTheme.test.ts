// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { WINDOW_COLOR, resolveInitialTheme } from "./useTheme";

// Navin is dark on every OS unless the user says otherwise. Two things used to
// break that: the OS preference decided it, so the same install looked
// different on two machines, and the theme was written to storage on every
// mount, which turned a default nobody had chosen into a stored preference that
// no later default could reach. The storage key was bumped for that second one,
// so the value the old version pinned has to read as "nothing chosen".
describe("resolveInitialTheme", () => {
  it("is dark when nothing has been chosen", () => {
    expect(resolveInitialTheme(null)).toBe("dark");
  });

  it("honours an explicit choice either way", () => {
    expect(resolveInitialTheme("light")).toBe("light");
    expect(resolveInitialTheme("dark")).toBe("dark");
  });

  it("falls back to dark on a value it does not recognise", () => {
    expect(resolveInitialTheme("")).toBe("dark");
    expect(resolveInitialTheme("system")).toBe("dark");
    expect(resolveInitialTheme("Light")).toBe("dark");
  });
});

describe("the colour the window frame is told to use", () => {
  it("is defined for both themes, so the title bar never keeps a stale one", () => {
    expect(WINDOW_COLOR.dark).toMatch(/^#[0-9a-f]{6}$/);
    expect(WINDOW_COLOR.light).toMatch(/^#[0-9a-f]{6}$/);
    expect(WINDOW_COLOR.dark).not.toBe(WINDOW_COLOR.light);
  });

  it("matches the pre-paint fallback in index.html", async () => {
    const html = await import("fs").then((fs) =>
      fs.readFileSync(new URL("../../index.html", import.meta.url), "utf8"),
    );
    // The script in the document head decides the same thing this hook does,
    // earlier; if the two disagree the window changes colour after load.
    expect(html).toContain(WINDOW_COLOR.dark);
    expect(html).toContain(WINDOW_COLOR.light);
    expect(html).not.toContain("prefers-color-scheme: dark");
  });
});
