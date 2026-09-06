import { afterAll, beforeEach, describe, expect, it } from "vitest";

import {
  LAST_DEV_CONTEXT_KEY,
  readLastDevContext,
  rememberLastDevContext,
  resolveLastCodeOpen,
} from "./last-dev-context";

function installStorageStub(): () => void {
  const store = new Map<string, string>();
  const localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, String(value)),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  } satisfies Storage;
  const globals = globalThis as { window?: unknown };
  const previous = globals.window;
  globals.window = { localStorage } as unknown as Window & typeof globalThis;
  return () => {
    if (previous === undefined) delete globals.window;
    else globals.window = previous;
  };
}

const restore = installStorageStub();
afterAll(restore);

describe("last-dev-context", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("remembers chat + project and merges patches", () => {
    rememberLastDevContext({
      chatKey: "websocket:chat-1",
      projectPath: "/tmp/proj",
      projectName: "proj",
    });
    expect(readLastDevContext()).toMatchObject({
      chatKey: "websocket:chat-1",
      projectPath: "/tmp/proj",
      projectName: "proj",
    });

    rememberLastDevContext({ projectPath: "/tmp/other", projectName: "other" });
    expect(readLastDevContext()).toMatchObject({
      chatKey: "websocket:chat-1",
      projectPath: "/tmp/other",
      projectName: "other",
    });
    expect(window.localStorage.getItem(LAST_DEV_CONTEXT_KEY)).toContain("chat-1");
  });

  it("keeps previous chat when only the folder is updated", () => {
    rememberLastDevContext({ chatKey: "websocket:a", projectPath: "/a" });
    rememberLastDevContext({ projectPath: "/b" });
    expect(readLastDevContext()?.chatKey).toBe("websocket:a");
  });

  it("reopens the last project from Skills instead of no-op", () => {
    const target = resolveLastCodeOpen({
      view: "skills",
      currentProjectPath: "/tmp/proj",
      last: {
        chatKey: "websocket:chat-1",
        projectPath: "/tmp/proj",
        projectName: "proj",
        updatedAt: 1,
      },
      recentProjects: [],
      defaultPath: "/tmp/default",
      currentChatKey: "websocket:chat-1",
      knownChatKeys: ["websocket:chat-1"],
      samePath: (a, b) => a === b,
      isInternalPath: (path) => path.includes(".navin"),
    });
    expect(target).toEqual({
      action: "project",
      path: "/tmp/proj",
      name: "proj",
    });
  });

  it("does nothing when Code already shows the last project", () => {
    const target = resolveLastCodeOpen({
      view: "dev",
      currentProjectPath: "/tmp/proj",
      last: {
        chatKey: "websocket:chat-1",
        projectPath: "/tmp/proj",
        projectName: "proj",
        updatedAt: 1,
      },
      samePath: (a, b) => a === b,
      isInternalPath: () => false,
    });
    expect(target).toEqual({ action: "noop" });
  });
});
