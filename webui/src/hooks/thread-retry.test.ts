import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { fetchThreadWithRetry } from "./useSessions";

const NO_WAIT = [0, 0, 0];

describe("fetchThreadWithRetry", () => {
  it("loads the conversation once a slow or restarting engine answers", async () => {
    const load = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new ApiError(503, "HTTP 503"))
      .mockResolvedValueOnce({ messages: [1] });
    await expect(fetchThreadWithRetry(load, () => false, NO_WAIT)).resolves.toEqual({ messages: [1] });
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("does not retry a final client error", async () => {
    const load = vi.fn().mockRejectedValue(new ApiError(403, "HTTP 403"));
    await expect(fetchThreadWithRetry(load, () => false, NO_WAIT)).rejects.toThrow("HTTP 403");
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("gives up after the last retry", async () => {
    const load = vi.fn().mockRejectedValue(new ApiError(502, "HTTP 502"));
    await expect(fetchThreadWithRetry(load, () => false, NO_WAIT)).rejects.toThrow("HTTP 502");
    expect(load).toHaveBeenCalledTimes(4);
  });

  it("stops retrying when the user switched conversation", async () => {
    const load = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(fetchThreadWithRetry(load, () => true, NO_WAIT)).rejects.toThrow();
    expect(load).toHaveBeenCalledTimes(1);
  });
});
