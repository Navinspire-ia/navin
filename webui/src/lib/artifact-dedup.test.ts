import { describe, expect, it } from "vitest";

import { artifactsNotInEditor, fingerprintContent } from "@/lib/artifact-dedup";
import type { DevOpenFile } from "@/lib/dev-open-files";
import type { ArtifactRecord } from "@/lib/types";

const BODY = "<html><body><h1>Planet Earth 3D</h1></body></html>";

function artifact(overrides: Partial<ArtifactRecord> = {}): ArtifactRecord {
  return {
    id: "art-1",
    type: "html",
    title: "Terre 3D (Three.js)",
    content: BODY,
    ...overrides,
  };
}

function openFile(overrides: Partial<DevOpenFile> = {}): DevOpenFile {
  return { path: "/repo/earth_3d.html", hash: fingerprintContent(BODY), ...overrides };
}

describe("fingerprintContent", () => {
  it("matches two copies of the same body", () => {
    expect(fingerprintContent(BODY)).toBe(fingerprintContent(BODY));
  });

  it("ignores line endings and surrounding blank space", () => {
    expect(fingerprintContent("a\r\nb")).toBe(fingerprintContent("\n a\nb  "));
  });

  it("separates different bodies", () => {
    expect(fingerprintContent(BODY)).not.toBe(fingerprintContent(`${BODY}<!-- x -->`));
  });

  it("has no fingerprint for an unloaded tab", () => {
    expect(fingerprintContent("   ")).toBe("");
  });
});

describe("artifactsNotInEditor", () => {
  it("leaves the list untouched when no editor is open", () => {
    const artifacts = [artifact()];
    expect(artifactsNotInEditor(artifacts, [])).toBe(artifacts);
  });

  it("drops an artifact whose body the editor already renders", () => {
    // The case that produced the duplicate: presented as inline content, so
    // there is no source_path to match on.
    expect(artifactsNotInEditor([artifact({ source_path: null })], [openFile()])).toEqual([]);
  });

  it("drops an artifact backed by an open file even if bodies differ", () => {
    const stale = artifact({ source_path: "/repo/earth_3d.html", content: "old" });
    expect(artifactsNotInEditor([stale], [openFile()])).toEqual([]);
  });

  it("matches a source path across separator and case differences", () => {
    const win = artifact({ source_path: "\\Repo\\Earth_3D.html", content: "old" });
    expect(artifactsNotInEditor([win], [openFile()])).toEqual([]);
  });

  it("keeps an artifact that is nowhere in the editor", () => {
    const other = artifact({ id: "art-2", content: "<p>other</p>", source_path: null });
    expect(artifactsNotInEditor([other], [openFile()])).toEqual([other]);
  });

  it("keeps artifacts when the open tab has not loaded yet", () => {
    const artifacts = [artifact({ source_path: null })];
    const loading = openFile({ path: "/repo/other.html", hash: "" });
    expect(artifactsNotInEditor(artifacts, [loading])).toEqual(artifacts);
  });

  it("drops only the duplicate out of several artifacts", () => {
    const dup = artifact({ source_path: null });
    const kept = artifact({ id: "art-2", content: "<p>chart</p>", source_path: null });
    expect(artifactsNotInEditor([dup, kept], [openFile()])).toEqual([kept]);
  });
});
