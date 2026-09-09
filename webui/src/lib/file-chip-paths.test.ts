// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { isAbsoluteChipPath, resolveChipPathFromEdits, writtenFileEdits } from "./file-chip-paths";
import type { UIFileEdit, UIMessage } from "./types";

function edit(partial: Partial<UIFileEdit> & { path: string }): UIFileEdit {
  return {
    call_id: partial.call_id ?? `call-${partial.path}`,
    tool: "write_file",
    added: 1,
    deleted: 0,
    status: "done",
    ...partial,
  };
}

function trace(edits: UIFileEdit[], id = "t"): UIMessage {
  return { id, role: "tool", kind: "trace", content: "", fileEdits: edits } as UIMessage;
}

const projectEdit = edit({
  path: "khalys-fragrances/supabase/schema.sql",
  absolute_path: "/home/k/NavinProjects/khalys-fragrances/supabase/schema.sql",
});

describe("resolveChipPathFromEdits", () => {
  it("maps a bare chip name onto the absolute path the agent wrote", () => {
    expect(resolveChipPathFromEdits("schema.sql", [trace([projectEdit])])).toBe(
      projectEdit.absolute_path,
    );
  });

  it("maps a nested relative chip onto the matching edit", () => {
    expect(resolveChipPathFromEdits("supabase/schema.sql", [trace([projectEdit])])).toBe(
      projectEdit.absolute_path,
    );
    expect(resolveChipPathFromEdits("./supabase/schema.sql:12", [trace([projectEdit])])).toBe(
      projectEdit.absolute_path,
    );
  });

  it("prefers the most recent edit when two files share a name", () => {
    const older = edit({ path: "legacy/report.sql", absolute_path: "/p/legacy/report.sql" });
    const newer = edit({ path: "supabase/report.sql", absolute_path: "/p/supabase/report.sql" });
    const messages = [trace([older], "a"), trace([newer], "b")];
    expect(resolveChipPathFromEdits("report.sql", messages)).toBe("/p/supabase/report.sql");
    expect(resolveChipPathFromEdits("legacy/report.sql", messages)).toBe("/p/legacy/report.sql");
  });

  it("ignores deleted and failed edits", () => {
    const gone = edit({ path: "old.sql", absolute_path: "/p/old.sql", operation: "delete" });
    const failed = edit({ path: "bad.sql", absolute_path: "/p/bad.sql", status: "error" });
    expect(writtenFileEdits([trace([gone, failed])])).toEqual([]);
    expect(resolveChipPathFromEdits("old.sql", [trace([gone])])).toBe("old.sql");
  });

  it("leaves absolute paths and unknown names alone", () => {
    expect(resolveChipPathFromEdits("/abs/x.sql", [trace([projectEdit])])).toBe("/abs/x.sql");
    expect(resolveChipPathFromEdits("C:\\w\\x.sql", [trace([projectEdit])])).toBe("C:\\w\\x.sql");
    expect(resolveChipPathFromEdits("other.sql", [trace([projectEdit])])).toBe("other.sql");
    expect(resolveChipPathFromEdits("  ", [])).toBe("");
  });

  it("does not let a different name with the same suffix match", () => {
    expect(resolveChipPathFromEdits("ma.sql", [trace([projectEdit])])).toBe("ma.sql");
  });

  it("recognises absolute spellings", () => {
    expect(isAbsoluteChipPath("/home/x")).toBe(true);
    expect(isAbsoluteChipPath("D:/x")).toBe(true);
    expect(isAbsoluteChipPath("\\\\wsl.localhost\\Ubuntu\\home")).toBe(true);
    expect(isAbsoluteChipPath("file:///tmp/x")).toBe(true);
    expect(isAbsoluteChipPath("src/x.ts")).toBe(false);
  });
});
