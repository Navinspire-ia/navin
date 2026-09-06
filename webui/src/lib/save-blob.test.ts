import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { onNotification } from "@/lib/notification-bus";
import type { NotificationInput } from "@/lib/notifications";
import {
  fileNameFromPath,
  fileNameFromUrl,
  isDesktopShell,
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
