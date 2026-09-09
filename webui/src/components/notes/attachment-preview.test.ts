// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { attachmentKind } from "./attachment-kind";

describe("attachmentKind", () => {
  it.each([
    ["photo.PNG", "image"],
    ["scan.jpeg", "image"],
    ["report.pdf", "pdf"],
    ["voix.mp3", "audio"],
    ["demo.mp4", "video"],
    ["essai-upload.txt", "text"],
    ["notes.md", "markdown"],
    ["README.markdown", "markdown"],
    ["config.yaml", "text"],
    ["script.py", "text"],
    ["archive.zip", "other"],
    ["sans-extension", "other"],
  ])("%s -> %s", (name, kind) => {
    expect(attachmentKind(name)).toBe(kind);
  });
});
