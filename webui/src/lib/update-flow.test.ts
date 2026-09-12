// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import type { UpdateStatus } from "./api";
import { waitForUpdateDownload } from "./update-flow";

function status(state: UpdateStatus["state"], progress = 0): UpdateStatus {
  return { state, progress, downloadedBytes: progress, totalBytes: 100 };
}

afterEach(() => vi.useRealTimers());

describe("update download progress", () => {
  it("waits for verified bytes and reports download progress before installation", async () => {
    vi.useFakeTimers();
    const read = vi.fn()
      .mockResolvedValueOnce(status("downloading", 53))
      .mockResolvedValueOnce(status("ready", 100));
    const progress = vi.fn();
    let ready = false;
    const result = waitForUpdateDownload(status("downloading"), read, progress)
      .then((value) => { ready = true; return value; });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(ready).toBe(false);
    expect(progress).toHaveBeenLastCalledWith(status("downloading", 53));
    await vi.advanceTimersByTimeAsync(1_000);
    expect(await result).toEqual(status("ready", 100));
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("surfaces signature and download failures instead of starting the installer", async () => {
    const read = vi.fn();
    await expect(waitForUpdateDownload({
      ...status("error"), error: "Update checksum verification failed",
    }, read)).rejects.toThrow("checksum");
    expect(read).not.toHaveBeenCalled();
  });

  it("accepts an already downloaded update without downloading it again", async () => {
    const read = vi.fn();
    expect(await waitForUpdateDownload(status("ready", 100), read)).toEqual(status("ready", 100));
    expect(read).not.toHaveBeenCalled();
  });

  it("reports an interrupted download after an engine restart", async () => {
    await expect(waitForUpdateDownload(status("idle"), vi.fn())).rejects.toThrow("stopped");
  });
});
