// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { beforeEach, describe, expect, it } from "vitest";

import {
  __clearCmdkFileHistoryForTests,
  cmdkHistoryKey,
  loadCmdkFileEdits,
  rememberCmdkFileEdit,
} from "./cmdkFileHistory";

describe("cmdkFileHistory", () => {
  beforeEach(() => {
    __clearCmdkFileHistoryForTests();
  });

  it("scopes history per file path", () => {
    rememberCmdkFileEdit("a.ts", {
      instruction: "rename",
      before: "foo",
      after: "bar",
    });
    rememberCmdkFileEdit("b.ts", {
      instruction: "other",
      before: "x",
      after: "y",
    });
    expect(loadCmdkFileEdits("a.ts")).toHaveLength(1);
    expect(loadCmdkFileEdits("a.ts")[0]?.after).toBe("bar");
    expect(loadCmdkFileEdits("b.ts")[0]?.instruction).toBe("other");
    expect(cmdkHistoryKey("a.ts")).toContain("a.ts");
  });

  it("dedupes identical applied edits and keeps newest first", () => {
    rememberCmdkFileEdit("f.py", {
      instruction: "fix",
      before: "a",
      after: "b",
      at: 1,
    });
    rememberCmdkFileEdit("f.py", {
      instruction: "fix",
      before: "a",
      after: "b",
      at: 2,
    });
    const rows = loadCmdkFileEdits("f.py");
    expect(rows).toHaveLength(1);
    expect(rows[0]?.at).toBe(2);
  });

  it("ignores no-op replacements", () => {
    rememberCmdkFileEdit("noop.ts", {
      instruction: "same",
      before: "x",
      after: "x",
    });
    expect(loadCmdkFileEdits("noop.ts")).toHaveLength(0);
  });
});
