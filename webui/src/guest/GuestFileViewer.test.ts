// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { parseDelimited, previewKind } from "./GuestFileViewer";

describe("previewKind", () => {
  it("knows documents, media and office files", () => {
    expect(previewKind("docs/report.pdf")).toBe("pdf");
    expect(previewKind("README.md")).toBe("markdown");
    expect(previewKind("data/sales.xlsx")).toBe("sheet");
    expect(previewKind("old.xls")).toBe("sheet");
    expect(previewKind("memo.docx")).toBe("office");
    expect(previewKind("deck.PPTX")).toBe("office");
    expect(previewKind("demo.mp4")).toBe("video");
    expect(previewKind("voice.mp3")).toBe("audio");
    expect(previewKind("logo.svg")).toBe("image");
    expect(previewKind("table.csv")).toBe("csv");
    expect(previewKind("site/index.html")).toBe("html");
    expect(previewKind("src/app.tsx")).toBe("text");
    expect(previewKind("Dockerfile")).toBe("text");
  });

  it("leaves unknown binaries to the download", () => {
    expect(previewKind("archive.zip")).toBeNull();
    expect(previewKind("app.exe")).toBeNull();
  });
});

describe("parseDelimited", () => {
  it("handles quotes, embedded commas and new lines", () => {
    expect(parseDelimited('name,note\n"Ana","a, b"\n"Bo","say ""hi""\nthere"\n', ",")).toEqual([
      ["name", "note"],
      ["Ana", "a, b"],
      ["Bo", 'say "hi"\nthere'],
    ]);
  });

  it("reads tab separated values", () => {
    expect(parseDelimited("a\tb\r\n1\t2", "\t")).toEqual([["a", "b"], ["1", "2"]]);
  });
});
