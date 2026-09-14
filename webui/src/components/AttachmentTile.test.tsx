// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { toMediaAttachment } from "@/lib/media";
import { AttachmentTile } from "./AttachmentTile";

describe("large attachment previews", () => {
  it("keeps a queued file's base64 payload out of the rendered markup", () => {
    const dataUrl = `data:text/plain;base64,${"YWFh".repeat(4 * 1024 * 1024)}`;
    const html = renderToStaticMarkup(createElement(AttachmentTile, {
      attachment: { kind: "file", name: "large.txt", url: dataUrl },
    }));
    expect(html).toContain("large.txt");
    expect(html).not.toContain("data:text/plain");
    expect(html.length).toBeLessThan(5000);
  });

  it("preserves the original file through normalization for a local preview", () => {
    const file = new File(["Navin upload"], "report.txt", { type: "text/plain" });
    const attachment = toMediaAttachment({ kind: "file", name: file.name, file });
    expect(attachment.file).toBe(file);
    const html = renderToStaticMarkup(createElement(AttachmentTile, { attachment }));
    expect(html).toContain("report.txt");
    expect(html).not.toContain("Navin upload");
  });

  it("keeps stored attachments available through their server URL", () => {
    const html = renderToStaticMarkup(createElement(AttachmentTile, {
      attachment: { kind: "file", name: "report.pdf", url: "/media/signed-report.pdf" },
    }));
    expect(html).toContain('href="/media/signed-report.pdf"');
  });
});
