// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  breakpointsEnabledForPath,
  debugRuntimeForPath,
} from "@/lib/debug-runtimes";

describe("debugRuntimeForPath", () => {
  it("maps python / node / go / lldb extensions", () => {
    expect(debugRuntimeForPath("app.py")).toBe("python");
    expect(debugRuntimeForPath("src/index.ts")).toBe("node");
    expect(debugRuntimeForPath("cmd/main.go")).toBe("go");
    expect(debugRuntimeForPath("src/main.rs")).toBe("lldb");
    expect(debugRuntimeForPath("lib.cpp")).toBe("lldb");
  });

  it("returns null for unsupported editor files", () => {
    expect(debugRuntimeForPath("README.md")).toBeNull();
    expect(debugRuntimeForPath("style.css")).toBeNull();
    expect(debugRuntimeForPath("data.json")).toBeNull();
    expect(debugRuntimeForPath(null)).toBeNull();
  });
});

describe("breakpointsEnabledForPath", () => {
  it("enables only files with a DAP runtime", () => {
    expect(breakpointsEnabledForPath("main.go")).toBe(true);
    expect(breakpointsEnabledForPath("notes.md")).toBe(false);
  });
});
