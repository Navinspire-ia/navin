// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import { flushDirtyFiles, registerDirtyFileFlusher } from "./dirty-file-flusher";

describe("dirty-file-flusher", () => {
  afterEach(() => {
    registerDirtyFileFlusher(null);
  });

  it("is a no-op when nothing is registered", async () => {
    await expect(flushDirtyFiles()).resolves.toBeUndefined();
  });

  it("runs the registered saver before a proof", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    registerDirtyFileFlusher(save);
    await flushDirtyFiles();
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("surfaces a failed save so the proof does not start on stale buffers", async () => {
    registerDirtyFileFlusher(async () => {
      throw new Error("Could not save src/app.ts before the proof.");
    });
    await expect(flushDirtyFiles()).rejects.toThrow("src/app.ts");
  });
});
