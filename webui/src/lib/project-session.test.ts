// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  findBlankChatForWorkspace,
  findReusableEmptyChat,
  findSessionForProject,
  isBlankChat,
  isListedSidebarSession,
  shouldResolvePendingNewChat,
} from "./project-session";
import type { ChatSummary } from "./types";

function session(
  partial: Partial<ChatSummary> & { chatId: string; projectPath?: string },
): ChatSummary {
  return {
    key: `websocket:${partial.chatId}`,
    channel: "websocket",
    chatId: partial.chatId,
    createdAt: partial.createdAt ?? "2026-01-01T00:00:00.000Z",
    updatedAt: partial.updatedAt ?? partial.createdAt ?? "2026-01-01T00:00:00.000Z",
    title: partial.title ?? "",
    preview: partial.preview ?? "",
    workspaceScope: partial.projectPath
      ? {
          project_path: partial.projectPath,
          project_name: "demo",
          access_mode: "full",
          restrict_to_workspace: false,
        }
      : null,
  };
}

describe("findSessionForProject", () => {
  it("returns null when no chat is bound to the project", () => {
    expect(
      findSessionForProject(
        [session({ chatId: "a", projectPath: "/other" })],
        "/home/me/app",
      ),
    ).toBeNull();
  });

  it("picks the most recently updated chat for the project", () => {
    const older = session({
      chatId: "old",
      projectPath: "/home/me/app",
      updatedAt: "2026-01-01T10:00:00.000Z",
    });
    const newer = session({
      chatId: "new",
      projectPath: "/home/me/app/",
      updatedAt: "2026-01-02T10:00:00.000Z",
    });
    const other = session({
      chatId: "x",
      projectPath: "/home/me/other",
      updatedAt: "2026-01-03T10:00:00.000Z",
    });
    expect(findSessionForProject([older, other, newer], "/home/me/app")?.chatId).toBe(
      "new",
    );
  });

  it("ignores chats without a project scope", () => {
    expect(
      findSessionForProject([session({ chatId: "bare" })], "/home/me/app"),
    ).toBeNull();
  });
});

describe("isListedSidebarSession", () => {
  it("hides the shared file-tree session", () => {
    expect(isListedSidebarSession(session({ chatId: "webui-dev" }))).toBe(false);
    expect(isListedSidebarSession(session({ chatId: "real-chat" }))).toBe(true);
  });
});

describe("findReusableEmptyChat", () => {
  it("reuses the active blank chat from the default workspace", () => {
    const blank = session({ chatId: "blank" });
    expect(
      findReusableEmptyChat([blank], blank.key, "/home/me/NavinProjects")?.chatId,
    ).toBe("blank");
  });

  it("does not steal a chat that already belongs to another project", () => {
    const bound = session({ chatId: "bound", projectPath: "/home/me/other" });
    expect(
      findReusableEmptyChat([bound], bound.key, "/home/me/NavinProjects"),
    ).toBeNull();
  });

  it("ignores a chat that already has a title or preview", () => {
    const titled = session({ chatId: "titled", title: "Roadmap" });
    expect(isBlankChat(titled)).toBe(false);
    expect(findReusableEmptyChat([titled], titled.key)).toBeNull();
  });

  it("falls back to another blank in the same project", () => {
    const titled = session({
      chatId: "titled",
      title: "Roadmap",
      projectPath: "/home/me/app",
    });
    const blank = session({ chatId: "blank", projectPath: "/home/me/app" });
    expect(
      findReusableEmptyChat([titled, blank], titled.key, "/home/me/app")?.chatId,
    ).toBe("blank");
  });

  it("reuses an unbound blank when no chat is selected", () => {
    const blank = session({ chatId: "blank" });
    expect(
      findReusableEmptyChat([blank], null, "/home/me/NavinProjects")?.chatId,
    ).toBe("blank");
  });
});

describe("findBlankChatForWorkspace", () => {
  it("picks the newest blank for the project", () => {
    const older = session({
      chatId: "old",
      projectPath: "/home/me/app",
      updatedAt: "2026-01-01T10:00:00.000Z",
    });
    const newer = session({
      chatId: "new",
      projectPath: "/home/me/app",
      updatedAt: "2026-01-02T10:00:00.000Z",
    });
    expect(findBlankChatForWorkspace([older, newer], "/home/me/app")?.chatId).toBe(
      "new",
    );
  });

  it("skips chats that already have a message", () => {
    const used = session({
      chatId: "used",
      projectPath: "/home/me/app",
      preview: "hello",
    });
    expect(findBlankChatForWorkspace([used], "/home/me/app")).toBeNull();
  });
});

describe("shouldResolvePendingNewChat", () => {
  it("resolves only for unknown chat ids while a create is pending", () => {
    expect(shouldResolvePendingNewChat(true, false)).toBe(true);
    expect(shouldResolvePendingNewChat(true, true)).toBe(false);
    expect(shouldResolvePendingNewChat(false, false)).toBe(false);
  });
});
