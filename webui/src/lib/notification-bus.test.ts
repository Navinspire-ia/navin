import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./api-error";
import { describeRequestFailure, onNotification, publishNotification } from "./notification-bus";

const REVIEW_URL =
  "http://127.0.0.1:8765/api/sessions/websocket%3Aabc-123/review-action?action=accept";

describe("describeRequestFailure", () => {
  it("never surfaces API or gateway failures to the user", () => {
    const cases: unknown[] = [
      new ApiError(500, "Internal Server Error"),
      new ApiError(404, "Not Found"),
      new ApiError(400, "path must be absolute"),
      new ApiError(401, "Unauthorized"),
      new ApiError(403, "Forbidden"),
      new TypeError("Failed to fetch"),
      new Error("Request timed out after 20000ms"),
      new Error("unexpected transport failure"),
    ];
    for (const failure of cases) {
      expect(describeRequestFailure(REVIEW_URL, failure)).toBeNull();
    }
  });

  it("stays silent when the caller aborted", () => {
    const aborted = new Error("aborted");
    aborted.name = "AbortError";
    expect(describeRequestFailure(REVIEW_URL, aborted)).toBeNull();
  });

  it("stays silent for ambient pollers and account", () => {
    expect(
      describeRequestFailure(
        "http://127.0.0.1:8765/api/sessions/websocket%3Aabc/board",
        new ApiError(500, "Internal Server Error"),
      ),
    ).toBeNull();
    expect(
      describeRequestFailure(
        "http://127.0.0.1:8765/api/settings/pairing",
        new ApiError(500, "Internal Server Error"),
      ),
    ).toBeNull();
    expect(
      describeRequestFailure(
        "http://127.0.0.1:8765/api/webui/runtime/health",
        new ApiError(404, "API route not found"),
      ),
    ).toBeNull();
    expect(
      describeRequestFailure(
        "http://127.0.0.1:8765/api/webui/account",
        new ApiError(500, "Internal Server Error"),
      ),
    ).toBeNull();
    expect(
      describeRequestFailure(
        "http://127.0.0.1:8765/api/webui/diagnostics?path=report.html",
        new ApiError(400, "relative path needs a root query parameter"),
      ),
    ).toBeNull();
    expect(
      describeRequestFailure(
        "http://127.0.0.1:8765/api/sessions/websocket%3Aabc/board/update?op=claim",
        new ApiError(500, "Internal Server Error"),
      ),
    ).toBeNull();
  });
});

describe("publishNotification", () => {
  it("delivers to subscribers until they unsubscribe", () => {
    const seen = vi.fn();
    const unsubscribe = onNotification(seen);
    publishNotification({ level: "info", source: "board", title: "one" });
    unsubscribe();
    publishNotification({ level: "info", source: "board", title: "two" });
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0].title).toBe("one");
  });

  it("still reaches the other subscribers when one throws", () => {
    const healthy = vi.fn();
    const unsubBroken = onNotification(() => {
      throw new Error("subscriber is broken");
    });
    const unsubHealthy = onNotification(healthy);
    expect(() =>
      publishNotification({ level: "info", source: "board", title: "one" }),
    ).not.toThrow();
    expect(healthy).toHaveBeenCalledTimes(1);
    unsubBroken();
    unsubHealthy();
  });
});
