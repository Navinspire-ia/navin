import { describe, expect, it } from "vitest";

import {
  DEFAULT_ARCHIVE_AFTER_DAYS,
  DEFAULT_DELETE_AFTER_DAYS,
  retentionDays,
  retentionProfilePayload,
} from "@/components/studio/tenders/retention";

describe("tenders retention options", () => {
  it("defaults to 45 then 60 days", () => {
    expect(retentionDays({})).toEqual({
      archive_after_days: DEFAULT_ARCHIVE_AFTER_DAYS,
      delete_after_days: DEFAULT_DELETE_AFTER_DAYS,
    });
  });

  it("never deletes before archive, matching the sidecar", () => {
    expect(retentionDays({ archive_after_days: 90, delete_after_days: 30 })).toEqual({
      archive_after_days: 90,
      delete_after_days: 90,
    });
  });

  it("posts the same fields the Tauri sidecar stores on the profile", () => {
    expect(retentionProfilePayload({ archive_after_days: "20", delete_after_days: "90" })).toEqual({
      archive_after_days: 20,
      delete_after_days: 90,
    });
  });
});
