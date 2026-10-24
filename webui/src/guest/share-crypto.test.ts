// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { ShareCipher, parseShareLocation } from "./share-crypto";

const KEY = "93-SN-eN7t8ig1_BYkfDQPZc87gAuLIEW8FvzMcl958";
const SHARE = "abcdefghijklmnop1234";
// Sealed by navin/share/crypto.py with KEY and SHARE.
const FROM_PYTHON = "bPD_F-cozqAafCdh8005cC5NZolx4f-eEpxpRL61NDyRaiAhCT5wTmepb8UXbc4BgFFsSBacKdw";

describe("ShareCipher", () => {
  it("opens a frame sealed by the Python host", async () => {
    const cipher = await ShareCipher.create(KEY, SHARE);
    expect(await cipher.open(FROM_PYTHON)).toEqual({ type: "snapshot", x: "é" });
  });

  it("round-trips and binds frames to their share", async () => {
    const cipher = await ShareCipher.create(KEY, SHARE);
    const frame = await cipher.seal({ type: "hello", name: "Ana" });
    expect(await cipher.open(frame)).toEqual({ type: "hello", name: "Ana" });
    const other = await ShareCipher.create(KEY, "zzzzzzzzzzzzzzzz9999");
    await expect(other.open(frame)).rejects.toBeTruthy();
  });

  it("rejects a short key", async () => {
    await expect(ShareCipher.create("abc", SHARE)).rejects.toThrow();
  });
});

describe("parseShareLocation", () => {
  it("reads the share id from the path and the key from the fragment", () => {
    expect(parseShareLocation({ pathname: `/s/${SHARE}`, hash: `#${KEY}` })).toEqual({ shareId: SHARE, key: KEY });
  });

  it("refuses a link without its key", () => {
    expect(parseShareLocation({ pathname: `/s/${SHARE}`, hash: "" })).toBeNull();
    expect(parseShareLocation({ pathname: "/other", hash: `#${KEY}` })).toBeNull();
  });
});
