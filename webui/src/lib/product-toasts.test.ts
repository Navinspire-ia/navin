import { describe, expect, it } from "vitest";

import { isReleaseAnnouncement } from "./product-toasts";

describe("isReleaseAnnouncement", () => {
  it("matches an admin-published release card", () => {
    expect(isReleaseAnnouncement({ id: "release-1.0.4", kind: "release" })).toBe(true);
  });

  it("matches by id when kind was stripped", () => {
    expect(isReleaseAnnouncement({ id: "release-1.0.4" })).toBe(true);
  });

  it("leaves ordinary news alone", () => {
    expect(isReleaseAnnouncement({ id: "models-qwen", kind: "models" })).toBe(false);
    expect(isReleaseAnnouncement({ id: "news-welcome", kind: "news" })).toBe(false);
  });
});
