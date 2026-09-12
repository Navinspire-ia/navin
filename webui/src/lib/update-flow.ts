// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { UpdateStatus } from "./api";

export async function waitForUpdateDownload(
  initial: UpdateStatus,
  readStatus: () => Promise<UpdateStatus>,
  onProgress?: (status: UpdateStatus) => void,
): Promise<UpdateStatus> {
  const deadline = Date.now() + 60 * 60_000;
  let status = initial;
  for (;;) {
    onProgress?.(status);
    if (status.state === "error") {
      throw new Error(status.error || "Update download failed");
    }
    if (["ready", "installing", "restarting"].includes(status.state)) return status;
    if (status.state !== "downloading") {
      throw new Error("The update download stopped. Retry the update.");
    }
    if (Date.now() >= deadline) {
      throw new Error("The update download is taking too long. Check the connection and retry.");
    }
    await new Promise<void>((resolve) => setTimeout(resolve, 1_000));
    status = await readStatus();
  }
}
