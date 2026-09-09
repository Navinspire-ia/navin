// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  agentTermTitle,
  applyOptimisticTreeFile,
  baseName,
  contextBucketWidths,
  dropPathKey,
  fileEditIsOnDisk,
  formatContextTokens,
  formatSize,
  formatTokenCount,
  gitStatusColor,
  isCsvEditorPath,
  isHtmlEditorPath,
  isMarkdownEditorPath,
  lookupTreeNode,
  mergeFileTreeListing,
  movePathKey,
  persistPreviewUrl,
  PREVIEW_URL_STORAGE_KEY,
  previewPortFromUrl,
  readStoredPreviewUrl,
  defaultEditorPreviewOn,
  mergeReviewTabs,
  REVIEW_VISIBLE_FILE_LIMIT,
  reviewLineStats,
  reviewOpenableFiles,
  reviewStatusBadge,
  splitReviewVisibleFiles,
  tabMatchesDiffFile,
  sameTreeDir,
  terminalStartDir,
  treeDirIsExpanded,
  treeDirsToReload,
  treeExpandedWith,
  treeExpandedWithout,
  treeFileAbsolutePath,
  treeNodeKeysToUpdate,
  treeSessionChatId,
} from "./devWorkbenchUtils";

function installMemoryStorage() {
  const store = new Map<string, string>();
  const storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  };
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("localStorage", storage);
  return storage;
}

describe("previewPortFromUrl", () => {
  it("parses explicit ports and defaults", () => {
    expect(previewPortFromUrl("http://localhost:3000")).toBe(3000);
    expect(previewPortFromUrl("https://example.com")).toBe(443);
    expect(previewPortFromUrl("localhost:5174")).toBe(5174);
    expect(previewPortFromUrl("")).toBeNull();
  });
});

describe("preview URL storage", () => {
  beforeEach(() => {
    installMemoryStorage();
  });

  it("persists and restores a valid preview URL", () => {
    persistPreviewUrl("http://127.0.0.1:4173/");
    expect(readStoredPreviewUrl()).toBe("http://127.0.0.1:4173/");
    expect(localStorage.getItem(PREVIEW_URL_STORAGE_KEY)).toContain("4173");
  });

  it("rejects Navin self ports", () => {
    persistPreviewUrl("http://127.0.0.1:8765/");
    expect(readStoredPreviewUrl()).toBeNull();
  });
});

describe("path / editor helpers", () => {
  it("baseName handles posix and windows separators", () => {
    expect(baseName("src/app.ts")).toBe("app.ts");
    expect(baseName("C:\\proj\\main.py")).toBe("main.py");
  });

  it("detects markdown / html / csv editor paths", () => {
    expect(isMarkdownEditorPath("README.md", null)).toBe(true);
    expect(isHtmlEditorPath("index.html", null)).toBe(true);
    expect(isCsvEditorPath("data.tsv", null)).toBe(true);
    expect(isMarkdownEditorPath("app.ts", null)).toBe(false);
  });

  it("formats sizes and token counts", () => {
    expect(formatSize(512)).toBe("512 B");
    expect(formatSize(2048)).toBe("2.0 KB");
    expect(formatTokenCount(42)).toBe("42");
    expect(formatTokenCount(12_500)).toBe("13k");
    expect(formatTokenCount(2_500_000)).toBe("2.5M");
    expect(formatContextTokens(42)).toBe("42");
    expect(formatContextTokens(1_400)).toBe("1.4K");
    expect(formatContextTokens(148_400)).toBe("148.4K");
  });

  it("sizes context-bar segments as a share of the window", () => {
    expect(
      contextBucketWidths(
        [
          { id: "system", tokens: 20_000 },
          { id: "tools", tokens: 10_000 },
        ],
        200_000,
      ),
    ).toEqual([
      { id: "system", percent: 10 },
      { id: "tools", percent: 5 },
    ]);
    expect(contextBucketWidths([{ id: "system", tokens: 10 }], 0)).toEqual([
      { id: "system", percent: 0 },
    ]);
  });

  it("maps review and git status visuals", () => {
    expect(reviewStatusBadge("created").letter).toBe("A");
    expect(reviewStatusBadge("deleted").letter).toBe("D");
    expect(gitStatusColor("added")).toContain("emerald");
    expect(gitStatusColor("conflict")).toContain("red");
  });

  it("truncates agent terminal titles", () => {
    expect(agentTermTitle("  npm   test  ")).toBe("npm test");
    expect(agentTermTitle("a".repeat(40)).length).toBe(33);
  });

  it("starts a terminal in the explicit folder, else the project", () => {
    // Explicit folder (context menu) wins over the project root.
    expect(terminalStartDir("/proj/src", "/proj")).toBe("/proj/src");
    // No explicit folder: the project the desk is on, never undefined when
    // a project is known - the gateway would fall back to its default
    // workspace otherwise.
    expect(terminalStartDir(undefined, "/proj")).toBe("/proj");
    expect(terminalStartDir(null, "/proj")).toBe("/proj");
    expect(terminalStartDir("  ", "/proj")).toBe("/proj");
    // Nothing known: leave it to the gateway's session scope.
    expect(terminalStartDir(undefined, null)).toBeUndefined();
    expect(terminalStartDir("", "  ")).toBeUndefined();
  });

  it("drops a path from a per-path record, and keeps identity when absent", () => {
    const record = { "/a.ts": true, "/b.ts": false };
    expect(dropPathKey(record, "/a.ts")).toEqual({ "/b.ts": false });
    // Same object back: a no-op must not trigger a re-render.
    expect(dropPathKey(record, "/missing.ts")).toBe(record);
  });

  it("moves a path of a per-path record on rename", () => {
    const record = { "/old.ts": "boom", "/other.ts": "keep" };
    expect(movePathKey(record, "/old.ts", "/new.ts")).toEqual({
      "/new.ts": "boom",
      "/other.ts": "keep",
    });
    expect(movePathKey(record, "/absent.ts", "/new.ts")).toBe(record);
    // A falsy value still travels; only a missing key is a no-op.
    expect(movePathKey({ "/a.ts": false }, "/a.ts", "/b.ts")).toEqual({
      "/b.ts": false,
    });
  });
});

describe("treeDirsToReload", () => {
  const root = "/Users/me/Forgejo/lynara";
  const dockerfile = `${root}/deploy/docker/ocr-rh-crm/Dockerfile`;

  it("reloads the root, the parent, and already-listed ancestors", () => {
    const parent = `${root}/deploy/docker/ocr-rh-crm`;
    const docker = `${root}/deploy/docker`;
    expect(treeDirsToReload(dockerfile, root, [parent, docker])).toEqual([
      null,
      parent,
      docker,
    ]);
  });

  it("maps a posix edit path onto a native explorer key", () => {
    const nativeParent = "C:\\proj\\deploy\\docker\\ocr-rh-crm";
    expect(
      treeDirsToReload(
        "C:/proj/deploy/docker/ocr-rh-crm/Dockerfile",
        "C:/proj",
        [nativeParent],
      ),
    ).toEqual([null, nativeParent]);
  });

  it("joins a relative file_edit path onto the project root", () => {
    const abs = treeFileAbsolutePath(
      { path: "deploy/docker/ocr-rh-crm/Dockerfile" },
      root,
    );
    expect(abs).toBe(dockerfile);
    expect(treeDirsToReload(abs, root, [])).toEqual([
      null,
      `${root}/deploy/docker/ocr-rh-crm`,
    ]);
  });

  it("only reloads the root for a file sitting in the project folder", () => {
    expect(treeDirsToReload(`${root}/Dockerfile`, root, [])).toEqual([null]);
  });

  it("prefers absolute_path over the display path", () => {
    expect(
      treeFileAbsolutePath(
        { path: "Dockerfile", absolute_path: dockerfile },
        root,
      ),
    ).toBe(dockerfile);
  });

  it("maps a WSL UNC explorer key onto a posix agent path", () => {
    const nativeRoot = "\\\\wsl.localhost\\Ubuntu\\home\\me\\lynara";
    const nativeParent = `${nativeRoot}\\deploy\\docker\\ocr-rh-crm`;
    expect(
      treeDirsToReload(
        "/home/me/lynara/deploy/docker/ocr-rh-crm/Dockerfile",
        nativeRoot,
        [nativeParent],
        "deploy/docker/ocr-rh-crm/Dockerfile",
      ),
    ).toEqual([null, nativeParent]);
  });

  it("maps /mnt/c agent paths onto C:\\ explorer keys", () => {
    const nativeParent = "C:\\proj\\deploy\\docker\\ocr-rh-crm";
    expect(
      treeDirsToReload(
        "/mnt/c/proj/deploy/docker/ocr-rh-crm/Dockerfile",
        "C:\\proj",
        [nativeParent],
      ),
    ).toEqual([null, nativeParent]);
  });

  it("uses a relative display path when the absolute path cannot be mapped", () => {
    const nativeParent = `${root}/deploy/docker/ocr-rh-crm`;
    expect(
      treeDirsToReload(
        "/other/host/deploy/docker/ocr-rh-crm/Dockerfile",
        root,
        [nativeParent],
        "deploy/docker/ocr-rh-crm/Dockerfile",
      ),
    ).toEqual([null, nativeParent]);
  });
});

describe("treeSessionChatId", () => {
  it("strips the websocket prefix and keeps a bare chat id", () => {
    expect(treeSessionChatId("websocket:abc-1")).toBe("abc-1");
    expect(treeSessionChatId("abc-1")).toBe("abc-1");
    expect(treeSessionChatId(null)).toBeNull();
  });
});

describe("fileEditIsOnDisk", () => {
  it("treats phase end as a committed write", () => {
    expect(fileEditIsOnDisk({ status: "done" })).toBe(true);
    expect(fileEditIsOnDisk({ phase: "end", status: "editing" })).toBe(true);
    expect(fileEditIsOnDisk({ status: "editing", phase: "start" })).toBe(false);
    expect(fileEditIsOnDisk({ status: "done", operation: "delete" })).toBe(false);
  });
});

describe("mergeFileTreeListing", () => {
  const dockerfile = {
    name: "Dockerfile",
    path: "/proj/Dockerfile",
    type: "file" as const,
    size: 0,
  };
  const sandbox = {
    name: "sandbox",
    path: "/proj/sandbox",
    type: "dir" as const,
    size: 0,
  };

  it("replaces when no retain options are set", () => {
    expect(mergeFileTreeListing([dockerfile], [sandbox])).toEqual([sandbox]);
  });

  it("keeps the previous listing when scandir still returns empty", () => {
    expect(mergeFileTreeListing([dockerfile], [], { keepIfEmpty: true })).toEqual([
      dockerfile,
    ]);
  });

  it("keeps only the just-written file missing from a stale listing", () => {
    const previous = [sandbox, dockerfile];
    expect(
      mergeFileTreeListing(previous, [sandbox], { retainNames: ["Dockerfile"] }).map(
        (entry) => entry.name,
      ),
    ).toEqual(["sandbox", "Dockerfile"]);
  });

  it("does not keep unrelated extras after a delete", () => {
    expect(
      mergeFileTreeListing([sandbox, dockerfile], [sandbox], { retainNames: [] }),
    ).toEqual([sandbox]);
  });
});

describe("lookupTreeNode / treeNodeKeysToUpdate", () => {
  it("finds a Windows explorer key from a posix listing path", () => {
    const native = "C:\\proj\\deploy\\docker\\ocr-rh-crm";
    const nodes = {
      [native]: { entries: [], loading: false, error: null },
    };
    expect(lookupTreeNode(nodes, "C:/proj/deploy/docker/ocr-rh-crm")).toBe(nodes[native]);
    expect(
      treeNodeKeysToUpdate(
        nodes,
        "C:/proj/deploy/docker/ocr-rh-crm",
        native,
        "C:/proj",
      ),
    ).toEqual(["C:/proj/deploy/docker/ocr-rh-crm", native]);
  });
});

describe("treeDirIsExpanded / treeExpandedWith", () => {
  it("treats Windows and posix spellings as the same open folder", () => {
    const native = "C:\\proj\\cron";
    const posix = "C:/proj/cron";
    const expanded = new Set([native]);
    expect(treeDirIsExpanded(expanded, posix, "C:/proj")).toBe(true);
    const opened = treeExpandedWith(new Set(), posix, [native], "C:/proj");
    expect(opened.has(native)).toBe(true);
    expect(treeDirIsExpanded(opened, native, "C:/proj")).toBe(true);
  });

  it("does not treat different project roots as the same folder", () => {
    expect(
      sameTreeDir("C:/proj-a", "C:/proj-b"),
    ).toBe(false);
    expect(
      sameTreeDir("C:\\Users\\me\\proj", "C:/Users/me/proj"),
    ).toBe(true);
  });

  it("removes every alias when collapsing", () => {
    const expanded = new Set(["C:\\proj\\cron", "C:/proj/cron"]);
    const next = treeExpandedWithout(expanded, "C:/proj/cron", "C:/proj");
    expect(next.size).toBe(0);
  });
});

describe("applyOptimisticTreeFile", () => {
  it("inserts a file into a folder that was listed as empty without dropping siblings above", () => {
    const root = "/Users/me/Forgejo/lynara";
    const parent = `${root}/deploy/docker/ocr-rh-crm`;
    const docker = `${root}/deploy/docker`;
    const nodes = {
      __root__: {
        entries: [
          { name: "backend", path: `${root}/backend`, type: "dir" as const, size: 0 },
          { name: "deploy", path: `${root}/deploy`, type: "dir" as const, size: 0 },
        ],
        loading: false,
        error: null,
      },
      [docker]: {
        entries: [
          { name: "crm", path: `${root}/deploy/docker/crm`, type: "dir" as const, size: 0 },
          { name: "ocr-rh-crm", path: parent, type: "dir" as const, size: 0 },
        ],
        loading: false,
        error: null,
      },
      [parent]: { entries: [], loading: false, error: null },
    };
    const next = applyOptimisticTreeFile(nodes, {
      rootPath: root,
      fileAbs: `${parent}/Dockerfile`,
      displayPath: "deploy/docker/ocr-rh-crm/Dockerfile",
    });
    expect(next.nodes[parent]?.entries.map((entry) => entry.name)).toEqual(["Dockerfile"]);
    expect(next.nodes[docker]?.entries.map((entry) => entry.name)).toEqual([
      "crm",
      "ocr-rh-crm",
    ]);
    expect(next.nodes.__root__?.entries.map((entry) => entry.name)).toEqual([
      "backend",
      "deploy",
    ]);
    expect(next.parentKey).toBe(parent);
  });

  it("removes a deleted file from the parent listing", () => {
    const root = "/proj";
    const parent = `${root}/deploy`;
    const nodes = {
      __root__: {
        entries: [{ name: "deploy", path: parent, type: "dir" as const, size: 0 }],
        loading: false,
        error: null,
      },
      [parent]: {
        entries: [
          { name: "Dockerfile", path: `${parent}/Dockerfile`, type: "file" as const, size: 0 },
          { name: "compose.yml", path: `${parent}/compose.yml`, type: "file" as const, size: 0 },
        ],
        loading: false,
        error: null,
      },
    };
    const next = applyOptimisticTreeFile(nodes, {
      rootPath: root,
      fileAbs: `${parent}/Dockerfile`,
      displayPath: "deploy/Dockerfile",
      deleted: true,
    });
    expect(next.nodes[parent]?.entries.map((entry) => entry.name)).toEqual(["compose.yml"]);
  });
});

describe("defaultEditorPreviewOn", () => {
  it("keeps Preview for a normal file and source for a pending review", () => {
    expect(defaultEditorPreviewOn(false)).toBe(true);
    expect(defaultEditorPreviewOn(true)).toBe(false);
  });
});

describe("review tabs", () => {
  it("opens every non-deleted review file as a tab", () => {
    const files = reviewOpenableFiles([
      { path: "/p/a.md", display_path: "a.md", status: "created" },
      { path: "/p/gone.py", display_path: "gone.py", status: "deleted" },
      { path: "/p/b.ts", display_path: "b.ts", status: "modified" },
    ]);
    expect(files.map((file) => file.path)).toEqual(["/p/a.md", "/p/b.ts"]);
    expect(
      mergeReviewTabs([{ path: "/p/a.md", displayPath: "a.md", name: "a.md" }], files),
    ).toEqual([
      { path: "/p/a.md", displayPath: "a.md", name: "a.md" },
      { path: "/p/b.ts", displayPath: "b.ts", name: "b.ts" },
    ]);
  });

  it("matches a workbench tab to the relative diff path", () => {
    const relativeOf = (path: string) => path.replace(/^\/home\/proj\//, "");
    expect(tabMatchesDiffFile("/home/proj/a.md", "a.md", relativeOf)).toBe(true);
    expect(tabMatchesDiffFile("/home/proj/b.ts", "a.md", relativeOf)).toBe(false);
  });

  it("keeps three review files visible and the rest in Other files", () => {
    const files = ["a", "b", "c", "d", "e"].map((name) => ({ path: `/${name}` }));
    expect(REVIEW_VISIBLE_FILE_LIMIT).toBe(3);
    expect(splitReviewVisibleFiles(files, "/a")).toEqual({
      visible: [{ path: "/a" }, { path: "/b" }, { path: "/c" }],
      overflow: [{ path: "/d" }, { path: "/e" }],
    });
    expect(splitReviewVisibleFiles(files, "/e")).toEqual({
      visible: [{ path: "/e" }, { path: "/a" }, { path: "/b" }],
      overflow: [{ path: "/c" }, { path: "/d" }],
    });
  });
});

describe("reviewLineStats", () => {
  it("sums added and deleted lines across pending files", () => {
    expect(
      reviewLineStats([
        { added: 12, deleted: 3 },
        { added: 0, deleted: 8 },
        { added: 5 },
      ]),
    ).toEqual({ added: 17, deleted: 11 });
  });

  it("treats missing or non-finite counts as zero", () => {
    expect(reviewLineStats([{ added: Number.NaN, deleted: -4 }, {}])).toEqual({
      added: 0,
      deleted: 0,
    });
  });
});
