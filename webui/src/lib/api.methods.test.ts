// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// The gateway serves its HTTP API from the websockets handshake hook, and the
// library raises "unsupported HTTP method; expected GET" while parsing the
// request line - before any navin code runs. A POST is answered with a bare 500
// that carries no hint of the cause, which is exactly how "Accept all" and the
// updater came to fail: nothing in the client or the route handler was wrong.
//
// Types cannot express that constraint, so this reads the source instead. If the
// gateway ever gains a real HTTP server, delete this test rather than work
// around it.
const SOURCE = readFileSync(fileURLToPath(new URL("./api.ts", import.meta.url)), "utf8");

describe("gateway API calls", () => {
  it("never sets an HTTP method, because only GET survives the handshake parser", () => {
    const offenders = SOURCE.split("\n")
      .map((line, index) => ({ line: line.trim(), number: index + 1 }))
      .filter(({ line }) => /\bmethod\s*:\s*["'`]/.test(line))
      .map(({ line, number }) => `api.ts:${number}: ${line}`);

    expect(offenders).toEqual([]);
  });
});
