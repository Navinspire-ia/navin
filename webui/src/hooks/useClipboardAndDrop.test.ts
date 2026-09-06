import { describe, expect, it } from "vitest";

import {
  consumePasteForAttachments,
  dragCarriesFiles,
  extractImageFilesFromDrop,
  extractImageFilesFromPaste,
  isDuplicatePasteBurst,
  pasteFilesKey,
  pasteTextIsRedundant,
} from "./useClipboardAndDrop";

function pngFile(name = "shot.png"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "image/png" });
}

describe("consumePasteForAttachments", () => {
  it("lets a text prompt reach the composer, even if a file is also advertised", () => {
    expect(consumePasteForAttachments("Pull the latest branch", 1)).toBe(false);
    expect(consumePasteForAttachments("hello", 0)).toBe(false);
  });

  it("consumes a file-only paste so the image is attached", () => {
    expect(consumePasteForAttachments("", 1)).toBe(true);
  });

  it("consumes the path WebKitGTK pastes next to the image", () => {
    expect(consumePasteForAttachments("file:///home/a/shot.png", 1)).toBe(true);
    expect(consumePasteForAttachments("/home/a/shot.png", 1)).toBe(true);
  });
});

describe("pasteTextIsRedundant", () => {
  it("keeps real prose and multi-line text", () => {
    expect(pasteTextIsRedundant("Pull the latest branch")).toBe(false);
    expect(pasteTextIsRedundant("/home/a/shot.png\nand more")).toBe(false);
  });
});

describe("dragCarriesFiles", () => {
  it("accepts the Chromium and the WebKitGTK announcements", () => {
    expect(dragCarriesFiles(["Files"])).toBe(true);
    expect(dragCarriesFiles(["text/uri-list", "text/plain"])).toBe(true);
    expect(dragCarriesFiles(["public.file-url"])).toBe(true);
  });

  it("ignores a text selection drag", () => {
    expect(dragCarriesFiles(["text/plain", "text/html"])).toBe(false);
    expect(dragCarriesFiles([])).toBe(false);
    expect(dragCarriesFiles(undefined)).toBe(false);
  });
});

describe("attachment extraction", () => {
  it("reads the file list when items is empty, as WebKitGTK leaves it", () => {
    const file = pngFile();
    const event = { clipboardData: { items: [], files: [file] } };
    expect(extractImageFilesFromPaste(event as never)).toEqual([file]);
  });

  it("does not attach the same file twice when both channels expose it", () => {
    const file = pngFile();
    const items = [{ kind: "file", getAsFile: () => file }];
    const event = { dataTransfer: { items, files: [file] } };
    expect(extractImageFilesFromDrop(event as never)).toEqual([file]);
  });

  it("keeps one screenshot when items and files are two File objects", () => {
    const fromItems = new File([new Uint8Array([1, 2, 3])], "image.png", {
      type: "image/png",
      lastModified: 1,
    });
    const fromFiles = new File([new Uint8Array([1, 2, 3])], "image.png", {
      type: "image/png",
      lastModified: 99,
    });
    const event = {
      clipboardData: {
        items: [{ kind: "file", getAsFile: () => fromItems }],
        files: [fromFiles],
      },
    };
    expect(extractImageFilesFromPaste(event as never)).toEqual([fromItems]);
  });

  it("collapses a native-plus-DOM paste burst of the same image", () => {
    const file = pngFile();
    const key = pasteFilesKey([file]);
    expect(isDuplicatePasteBurst({ at: 1000, key }, key, 1100)).toBe(true);
    expect(isDuplicatePasteBurst({ at: 1000, key }, key, 1600)).toBe(false);
  });

  it("skips unsupported kinds", () => {
    const binary = new File([new Uint8Array([1])], "app.bin", {
      type: "application/octet-stream",
    });
    const event = { dataTransfer: { items: [], files: [binary] } };
    expect(extractImageFilesFromDrop(event as never)).toEqual([]);
  });
});
