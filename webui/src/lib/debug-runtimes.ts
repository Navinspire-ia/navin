// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Which editor files can host a real DAP session (mirrors navin.dap.session). */

const PYTHON = new Set(["py", "pyw"]);
const NODE = new Set(["js", "mjs", "cjs", "ts", "mts", "cts"]);
const GO = new Set(["go"]);
const LLDB = new Set([
  "rs",
  "c",
  "cc",
  "cpp",
  "cxx",
  "h",
  "hpp",
  "hxx",
  "hh",
  "m",
  "mm",
]);

export type DebugRuntime = "python" | "node" | "go" | "lldb";

export function debugRuntimeForPath(path: string | null | undefined): DebugRuntime | null {
  if (!path) return null;
  const base = path.replace(/\\/g, "/").split("/").pop() ?? "";
  const dot = base.lastIndexOf(".");
  if (dot < 0) return null;
  const ext = base.slice(dot + 1).toLowerCase();
  if (PYTHON.has(ext)) return "python";
  if (NODE.has(ext)) return "node";
  if (GO.has(ext)) return "go";
  if (LLDB.has(ext)) return "lldb";
  return null;
}

/** True when the gutter should accept breakpoint clicks for this file. */
export function breakpointsEnabledForPath(path: string | null | undefined): boolean {
  return debugRuntimeForPath(path) != null;
}
