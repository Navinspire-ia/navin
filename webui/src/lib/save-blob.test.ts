// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { onNotification } from "@/lib/notification-bus";
import type { NotificationInput } from "@/lib/notifications";
import {
  DESKTOP_DOWNLOAD_EVENT,
  SAVE_NAME_HEADER,
  fileNameFromPath,
  fileNameFromUrl,
  isDesktopShell,
  listenForDesktopDownloads,
  saveBlob,
  saveDownload,
} from "@/lib/save-blob";

const created: string[] = [];
const revoked: string[] = [];
const clicked: { href: string; download: string; inDocument: boolean }[] = [];
let attached = 0;

function stubDom(): void {
  created.length = 0;
  revoked.length = 0;
  clicked.length = 0;
  attached = 0;
  let counter = 0;

  vi.stubGlobal("URL", {
    createObjectURL: () => {
      const url = `blob:navin/${(counter += 1)}`;
      created.push(url);
      return url;
    },
    revokeObjectURL: (url: string) => {
      revoked.push(url);
    },
  });

  const anchor = {
    href: "",
    download: "",
    rel: "",
    click: () => {
      clicked.push({ href: anchor.href, download: anchor.download, inDocument: attached > 0 });
    },
    remove: () => {
      attached -= 1;
    },
  };
  vi.stubGlobal("document", {
    createElement: () => anchor,
    body: {
      appendChild: () => {
        attached += 1;
      },
    },
  });
}

describe("saveBlob", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubDom();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  // The bug this guards: revoking in the same tick as the click killed the
  // download before the browser read a byte, silently, so every download
  // button in the chat looked dead.
  it("keeps the blob URL alive after the click", () => {
    saveBlob(new Blob(["report"]), "report.pdf");

    expect(clicked).toHaveLength(1);
    expect(revoked).toEqual([]);
  });

  it("releases the blob URL later, so a long session does not leak", () => {
    saveBlob(new Blob(["report"]), "report.pdf");
    vi.advanceTimersByTime(60_000);

    expect(revoked).toEqual(created);
  });

  it("clicks an anchor that is attached to the document, as Firefox requires", () => {
    saveBlob(new Blob(["report"]), "report.pdf");

    expect(clicked[0].inDocument).toBe(true);
    expect(clicked[0].download).toBe("report.pdf");
  });

  it("announces a browser save, pointing at the Downloads folder", () => {
    const seen: NotificationInput[] = [];
    const unsubscribe = onNotification((input) => seen.push(input));
    saveBlob(new Blob(["report"]), "report.pdf");
    unsubscribe();

    expect(seen).toHaveLength(1);
    expect(seen[0].level).toBe("success");
    expect(seen[0].toast).toBe(true);
    expect(seen[0].title).toContain("report.pdf");
    expect(seen[0].detail).toMatch(/download|téléchargement/i);
  });
});

describe("saveDownload in the desktop shell", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubShell(result: unknown): void {
    vi.stubGlobal("window", {
      __TAURI__: { core: { invoke: async () => result } },
      setTimeout: () => 0,
    });
  }

  it("announces the exact path the user picked in the dialog", async () => {
    stubShell("/home/user/Documents/report.pdf");
    const seen: NotificationInput[] = [];
    const unsubscribe = onNotification((input) => seen.push(input));
    await saveDownload(new Blob(["report"]), "report.pdf");
    unsubscribe();

    expect(seen).toHaveLength(1);
    expect(seen[0].title).toContain("report.pdf");
    expect(seen[0].detail).toBe("/home/user/Documents/report.pdf");
  });

  it("still announces on an older shell that only returns true", async () => {
    stubShell(true);
    const seen: NotificationInput[] = [];
    const unsubscribe = onNotification((input) => seen.push(input));
    await saveDownload(new Blob(["report"]), "report.pdf");
    unsubscribe();

    expect(seen).toHaveLength(1);
    expect(seen[0].detail).toMatch(/download|téléchargement/i);
  });

  it("stays silent when the user cancels the save dialog", async () => {
    stubShell(null);
    const seen: NotificationInput[] = [];
    const unsubscribe = onNotification((input) => seen.push(input));
    await saveDownload(new Blob(["report"]), "report.pdf");
    unsubscribe();

    expect(seen).toHaveLength(0);
  });

  it("hands the shell the raw bytes and the name in a header, not a JSON array", async () => {
    const invoke = vi.fn().mockResolvedValue("/home/user/Documents/r.pdf");
    vi.stubGlobal("window", { __TAURI__: { core: { invoke } }, setTimeout: () => 0 });
    await saveDownload(new Blob([new Uint8Array([1, 2, 255])]), "exports/Rapport été.pdf");

    expect(invoke).toHaveBeenCalledTimes(1);
    const [command, body, options] = invoke.mock.calls[0];
    expect(command).toBe("save_bytes");
    expect(body).toBeInstanceOf(Uint8Array);
    expect(Array.from(body as Uint8Array)).toEqual([1, 2, 255]);
    expect(options).toEqual({ headers: { [SAVE_NAME_HEADER]: "Rapport%20%C3%A9t%C3%A9.pdf" } });
  });

  it("falls back to the JSON form once, for a shell that refuses a raw body", async () => {
    const invoke = vi
      .fn()
      .mockRejectedValueOnce("invalid args `filename` for command `save_bytes`: missing required key filename")
      .mockResolvedValueOnce("/home/user/r.pdf");
    vi.stubGlobal("window", { __TAURI__: { core: { invoke } }, setTimeout: () => 0 });
    await saveDownload(new Blob([new Uint8Array([7, 8])]), "r.pdf");

    expect(invoke).toHaveBeenCalledTimes(2);
    expect(invoke.mock.calls[1]).toEqual(["save_bytes", { filename: "r.pdf", contents: [7, 8] }]);
  });

  it("does not open a second dialog when the save itself failed", async () => {
    const invoke = vi.fn().mockRejectedValue("Permission denied (os error 13)");
    vi.stubGlobal("window", { __TAURI__: { core: { invoke } }, setTimeout: () => 0 });
    stubDom();
    await saveDownload(new Blob(["x"]), "r.pdf");

    expect(invoke).toHaveBeenCalledTimes(1);
  });
});

describe("downloads the desktop shell ran itself", () => {
  it("announces where the file landed, or why it did not", () => {
    const target = new EventTarget();
    const seen: NotificationInput[] = [];
    const unsubscribe = onNotification((input) => seen.push(input));
    const stop = listenForDesktopDownloads(target);
    target.dispatchEvent(
      new CustomEvent(DESKTOP_DOWNLOAD_EVENT, { detail: { name: "clip.mp4", path: "/home/user/Videos/clip.mp4" } }),
    );
    target.dispatchEvent(
      new CustomEvent(DESKTOP_DOWNLOAD_EVENT, { detail: { name: "clip.mp4", error: "No space left on device" } }),
    );
    target.dispatchEvent(new CustomEvent(DESKTOP_DOWNLOAD_EVENT, { detail: null }));
    stop();
    target.dispatchEvent(
      new CustomEvent(DESKTOP_DOWNLOAD_EVENT, { detail: { name: "late.mp4", path: "/tmp/late.mp4" } }),
    );
    unsubscribe();

    expect(seen).toHaveLength(2);
    expect(seen[0]).toMatchObject({ level: "success", detail: "/home/user/Videos/clip.mp4" });
    expect(seen[0].title).toContain("clip.mp4");
    expect(seen[1]).toMatchObject({ level: "error", detail: "clip.mp4: No space left on device" });
  });
});

describe("file name helpers", () => {
  it("takes the last segment of a path, on both slash styles", () => {
    expect(fileNameFromPath("reports/q3/summary.md")).toBe("summary.md");
    expect(fileNameFromPath("C:\\reports\\summary.md")).toBe("summary.md");
  });

  it("keeps a hash in a file name, which is not a URL fragment", () => {
    expect(fileNameFromPath("reports/notes#1.md")).toBe("notes#1.md");
  });

  it("drops the query and fragment of a URL", () => {
    expect(fileNameFromUrl("/api/media/deck.pptx?token=abc#page=2")).toBe("deck.pptx");
  });

  it("falls back when there is no segment at all", () => {
    expect(fileNameFromPath("/")).toBe("download");
  });
});

describe("desktop shell detection", () => {
  it("is false in a plain browser, so Vite on :5173 keeps the blob path", () => {
    expect(isDesktopShell()).toBe(false);
  });
});
