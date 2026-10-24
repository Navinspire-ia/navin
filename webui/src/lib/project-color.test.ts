// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";
import { PROJECT_FOLDER_TINTS, projectFolderTints } from "./project-color";

describe("project folder tints", () => {
  it("gives each project its own calm tint, stable across list order", () => {
    const paths = ["/home/a/shop", "/home/a/crm", "/home/a/blog", "/home/a/api", "/home/a/site"];
    const first = projectFolderTints(paths);
    const reordered = projectFolderTints([...paths].reverse());
    expect(new Set(first.values()).size).toBe(paths.length);
    for (const path of paths) {
      expect(first.get(path)).toBe(reordered.get(path));
      expect(PROJECT_FOLDER_TINTS).toContain(first.get(path));
    }
  });

  it("treats the same folder written differently as one project", () => {
    const tints = projectFolderTints(["C:\\Users\\a\\Shop\\", "c:/users/a/shop"]);
    expect(tints.get("C:\\Users\\a\\Shop\\")).toBe(tints.get("c:/users/a/shop"));
  });

  it("ignores rows without a project folder", () => {
    expect(projectFolderTints([null, undefined, ""]).size).toBe(0);
  });
});
