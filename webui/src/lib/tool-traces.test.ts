// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  canonicalToolTraceKey,
  formatToolCallTrace,
  mergeUniqueToolTraceLines,
  toolEventTraceKey,
} from "./tool-traces";

describe("canonicalToolTraceKey", () => {
  it("gives the history (Python json.dumps) and live (JSON.stringify) forms one key", () => {
    const python = 'exec({"command": "npm test", "timeout": 60})';
    const live = 'exec({"command":"npm test","timeout":60})';
    expect(canonicalToolTraceKey(python)).toBe(canonicalToolTraceKey(live));
    expect(canonicalToolTraceKey(live)).toBe(live);
  });

  it("matches a structured event to its trace line whatever the spacing", () => {
    const event = { name: "exec", call_id: "c1", arguments: { command: "ls -la" } };
    expect(formatToolCallTrace(event)).toBe('exec({"command":"ls -la"})');
    expect(toolEventTraceKey(event)).toBe(canonicalToolTraceKey('exec({"command": "ls -la"})'));
  });

  it("leaves plain progress text and non-JSON args as they are", () => {
    expect(canonicalToolTraceKey("  Thinking about it ")).toBe("Thinking about it");
    expect(canonicalToolTraceKey("exec(ls -la)")).toBe("exec(ls -la)");
    expect(canonicalToolTraceKey("list_dir()")).toBe("list_dir()");
  });
});

describe("mergeUniqueToolTraceLines", () => {
  it("dedupes the same call written with other JSON spacing", () => {
    const { traces, added } = mergeUniqueToolTraceLines(
      ['exec({"command":"npm test"})'],
      ['exec({"command": "npm test"})', 'read_file({"path": "a.ts"})'],
    );
    expect(traces).toEqual(['exec({"command":"npm test"})', 'read_file({"path": "a.ts"})']);
    expect(added).toBe(true);
  });
});
