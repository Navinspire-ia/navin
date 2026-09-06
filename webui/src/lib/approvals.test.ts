import { describe, expect, it } from "vitest";

import {
  addApproval,
  dropExpired,
  isExpired,
  removeApproval,
  secondsLeft,
  toPendingApproval,
  type ApprovalRequestFrame,
} from "./approvals";

const NOW = 1_700_000_000_000;

function frame(overrides: Partial<ApprovalRequestFrame> = {}): ApprovalRequestFrame {
  return {
    request_id: "r1",
    tool: "exec",
    action: "Run something dangerous",
    reason: "It matches a rule",
    expires_at_ms: NOW + 300_000,
    ...overrides,
  };
}

describe("toPendingApproval", () => {
  it("fills the optional fields with empty strings", () => {
    const request = toPendingApproval(frame(), NOW);
    expect(request.detail).toBe("");
    expect(request.consequence).toBe("");
    expect(request.scope).toBe("");
  });

  it("keeps what the backend sent", () => {
    const request = toPendingApproval(
      frame({ detail: "$ rm -rf build", scope: "exec:recursiveDelete" }),
      NOW,
    );
    expect(request.detail).toBe("$ rm -rf build");
    expect(request.scope).toBe("exec:recursiveDelete");
  });

  it("does not offer to remember a request with no scope to remember", () => {
    const request = toPendingApproval(frame({ remember_offered: true }), NOW);
    expect(request.rememberOffered).toBe(false);
  });

  it("offers to remember a scoped request", () => {
    const request = toPendingApproval(
      frame({ remember_offered: true, scope: "git:push-force" }),
      NOW,
    );
    expect(request.rememberOffered).toBe(true);
  });

  it("treats a missing expiry as no expiry", () => {
    expect(toPendingApproval(frame({ expires_at_ms: null }), NOW).expiresAt).toBeNull();
    expect(toPendingApproval(frame({ expires_at_ms: undefined }), NOW).expiresAt).toBeNull();
  });
});

describe("addApproval", () => {
  it("adds a request", () => {
    expect(addApproval([], frame(), NOW)).toHaveLength(1);
  });

  it("keeps one entry when the same request arrives twice", () => {
    const once = addApproval([], frame(), NOW);
    const twice = addApproval(once, frame(), NOW + 10);
    expect(twice).toHaveLength(1);
    expect(twice[0].receivedAt).toBe(NOW + 10);
  });

  it("keeps two different requests, oldest first", () => {
    const list = addApproval(addApproval([], frame(), NOW), frame({ request_id: "r2" }), NOW + 1);
    expect(list.map((entry) => entry.requestId)).toEqual(["r1", "r2"]);
  });

  it("ignores a request with no id", () => {
    expect(addApproval([], frame({ request_id: "" }), NOW)).toEqual([]);
  });

  it("ignores a replayed request that has already expired", () => {
    expect(addApproval([], frame({ expires_at_ms: NOW - 1 }), NOW)).toEqual([]);
  });

  it("ignores a request that was already answered", () => {
    const settled = new Set(["r1"]);
    expect(addApproval([], frame(), NOW, settled)).toEqual([]);
  });

  it("does not mutate the list it was given", () => {
    const before = addApproval([], frame(), NOW);
    addApproval(before, frame({ request_id: "r2" }), NOW);
    expect(before).toHaveLength(1);
  });
});

describe("removeApproval", () => {
  it("drops the answered request and leaves the others", () => {
    const list = addApproval(addApproval([], frame(), NOW), frame({ request_id: "r2" }), NOW);
    expect(removeApproval(list, "r1").map((entry) => entry.requestId)).toEqual(["r2"]);
  });

  it("is a no-op for something that was never shown", () => {
    const list = addApproval([], frame(), NOW);
    expect(removeApproval(list, "unknown")).toHaveLength(1);
  });
});

describe("expiry", () => {
  it("is not expired before the deadline", () => {
    expect(isExpired(toPendingApproval(frame(), NOW), NOW + 299_000)).toBe(false);
  });

  it("is expired at the deadline", () => {
    expect(isExpired(toPendingApproval(frame(), NOW), NOW + 300_000)).toBe(true);
  });

  it("never expires without a deadline", () => {
    const request = toPendingApproval(frame({ expires_at_ms: null }), NOW);
    expect(isExpired(request, NOW + 10_000_000)).toBe(false);
  });

  it("drops only the stale ones", () => {
    const list = addApproval(
      addApproval([], frame(), NOW),
      frame({ request_id: "r2", expires_at_ms: NOW + 1_000 }),
      NOW,
    );
    expect(dropExpired(list, NOW + 2_000).map((entry) => entry.requestId)).toEqual(["r1"]);
  });
});

describe("secondsLeft", () => {
  it("counts down in whole seconds, rounding up", () => {
    const request = toPendingApproval(frame(), NOW);
    expect(secondsLeft(request, NOW)).toBe(300);
    expect(secondsLeft(request, NOW + 500)).toBe(300);
    expect(secondsLeft(request, NOW + 1_500)).toBe(299);
  });

  it("floors at zero rather than going negative", () => {
    expect(secondsLeft(toPendingApproval(frame(), NOW), NOW + 400_000)).toBe(0);
  });

  it("has nothing to count without a deadline", () => {
    expect(secondsLeft(toPendingApproval(frame({ expires_at_ms: null }), NOW), NOW)).toBeNull();
  });
});
