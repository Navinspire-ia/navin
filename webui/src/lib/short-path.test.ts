import { describe, expect, it } from "vitest";

import { looksLikePath, shortenArgValue, shortenPath, truncateMiddle } from "./short-path";

describe("truncateMiddle", () => {
  it("leaves a short value alone", () => {
    expect(truncateMiddle("api.ts", 44)).toBe("api.ts");
  });

  it("keeps both ends of a long value", () => {
    const out = truncateMiddle("a".repeat(30) + "b".repeat(30), 20);
    expect(out).toHaveLength(20);
    expect(out).toContain("…");
    expect(out.startsWith("a")).toBe(true);
    expect(out.endsWith("b")).toBe(true);
  });
});

describe("shortenPath", () => {
  it("leaves a path that already fits alone", () => {
    expect(shortenPath("src/lib/api.ts")).toBe("src/lib/api.ts");
  });

  it("keeps the file name and the folders that fit", () => {
    const out = shortenPath(
      ".navin/resources/document-templates/ppt/black_and_white_clean/slide_01.html",
    );
    expect(out).toBe("…/ppt/black_and_white_clean/slide_01.html");
  });

  it("never cuts the file name while parents could be dropped instead", () => {
    const out = shortenPath("a/bbbbbbbbbbbbbbbbbbbb/cccccccccccccccccccc/slide_01.html", 30);
    expect(out.endsWith("slide_01.html")).toBe(true);
    expect(out).not.toContain("slide_01.…");
  });

  it("never drops the file name", () => {
    const out = shortenPath("a/b/c/d/e/f/g/h/i/j/k/target-file-name.tsx");
    expect(out.endsWith("target-file-name.tsx")).toBe(true);
  });

  it("stays within the budget", () => {
    const deep = Array.from({ length: 20 }, (_, i) => `folder-${i}`).join("/");
    expect(shortenPath(`${deep}/file.ts`, 30).length).toBeLessThanOrEqual(30);
  });

  it("adds as many parents as the budget allows", () => {
    // A wider budget should show strictly more context than a narrow one.
    const path = "one/two/three/four/five/six/seven/eight/nine/leaf.ts";
    expect(shortenPath(path, 60).length).toBeGreaterThan(shortenPath(path, 24).length);
  });

  it("truncates a single segment that is too long on its own", () => {
    const out = shortenPath("a".repeat(80), 30);
    expect(out).toHaveLength(30);
    expect(out).toContain("…");
  });

  it("truncates a file name longer than the budget even when nested", () => {
    const out = shortenPath(`deep/folder/${"n".repeat(80)}.ts`, 30);
    expect(out.length).toBeLessThanOrEqual(30);
  });

  it("keeps windows separators", () => {
    const out = shortenPath(
      "C:\\Users\\me\\projects\\deploy\\resources\\templates\\slide_01.html",
    );
    expect(out).toContain("\\");
    expect(out).not.toContain("/");
    expect(out.endsWith("slide_01.html")).toBe(true);
  });

  it("does not claim to have dropped folders when it has not", () => {
    // Length comes from one long segment, not from depth: an ellipsised prefix
    // would suggest parents that were never there.
    const out = shortenPath(`/${"x".repeat(60)}`, 30);
    expect(out.startsWith("…/")).toBe(false);
  });
});

describe("looksLikePath", () => {
  it("accepts a path", () => {
    expect(looksLikePath("src/lib/api.ts")).toBe(true);
    expect(looksLikePath("C:\\tmp\\a.txt")).toBe(true);
  });

  it("rejects prose and bare names", () => {
    expect(looksLikePath("run the whole suite")).toBe(false);
    expect(looksLikePath("api.ts")).toBe(false);
  });
});

describe("shortenArgValue", () => {
  it("shortens a path by segment", () => {
    const out = shortenArgValue("a/b/c/d/e/f/g/h/i/j/k/leaf.ts", 12);
    expect(out).toBe("…/k/leaf.ts");
  });

  it("caps a long non-path value in the middle", () => {
    const out = shortenArgValue("word ".repeat(30), 20);
    expect(out).toHaveLength(20);
  });
});
