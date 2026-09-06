import { describe, expect, it } from "vitest";

import {
  INITIAL_GROUP_VISIBLE_COUNT,
  groupSessions,
  placeChatKey,
  priorChatKeys,
  visibleSessionsForLimit,
  type ChatGroupLabels,
} from "./chat-groups";
import type { ChatSummary } from "@/lib/types";

const labels: ChatGroupLabels = {
  pinned: "Pinned",
  all: "Chats",
  today: "Today",
  yesterday: "Yesterday",
  last7Days: "Last 7 days",
  last30Days: "Last 30 days",
  older: "Older",
  earlier: "Earlier",
  archived: "Archived",
  projects: "Projects",
  fallbackTitle: "New chat",
};

function session(
  partial: Partial<ChatSummary> & Pick<ChatSummary, "key" | "chatId">,
): ChatSummary {
  return {
    channel: "websocket",
    createdAt: partial.createdAt ?? null,
    updatedAt: partial.updatedAt ?? null,
    preview: partial.preview ?? "",
    workspaceScope: partial.workspaceScope,
    title: partial.title,
    key: partial.key,
    chatId: partial.chatId,
  };
}

function daysAgo(days: number): string {
  const d = new Date();
  d.setHours(12, 0, 0, 0);
  d.setDate(d.getDate() - days);
  return d.toISOString();
}

describe("groupSessions project folders", () => {
  it("keeps project folders above classic chats even when a chat is newer", () => {
    const groups = groupSessions(
      [
        session({
          key: "websocket:classic",
          chatId: "classic",
          updatedAt: daysAgo(0),
          preview: "classic",
        }),
        session({
          key: "websocket:proj-old",
          chatId: "proj-old",
          updatedAt: daysAgo(2),
          preview: "old project chat",
          workspaceScope: {
            project_path: "/home/aymen/projects/alpha",
            project_name: "alpha",
            access_mode: "full",
          },
        }),
        session({
          key: "websocket:proj-new",
          chatId: "proj-new",
          updatedAt: daysAgo(0),
          preview: "newer project chat",
          workspaceScope: {
            project_path: "/home/aymen/projects/beta",
            project_name: "beta",
            access_mode: "full",
          },
        }),
      ],
      labels,
      {
        pinnedKeys: [],
        archivedKeys: [],
        titleOverrides: {},
        projectNameOverrides: {},
        showArchived: false,
        sort: "updated_desc",
        defaultWorkspacePath: "/home/aymen/.navin/workspace",
      },
    );

    expect(groups.map((group) => group.id)).toEqual([
      "project:/home/aymen/projects/beta",
      "project:/home/aymen/projects/alpha",
      "workspace:chats",
    ]);
    expect(groups.every((group, index) => {
      if (group.id === "workspace:chats") {
        return index === groups.length - 1;
      }
      return group.kind === "project";
    })).toBe(true);
  });

  it("keeps every recent project folder, not just the first", () => {
    const groups = groupSessions(
      [],
      labels,
      {
        pinnedKeys: [],
        archivedKeys: [],
        titleOverrides: {},
        projectNameOverrides: {},
        showArchived: false,
        sort: "updated_desc",
        defaultWorkspacePath: "/home/aymen/NavinProjects",
        recentProjects: [
          { path: "/home/aymen/projects/alpha", name: "alpha" },
          { path: "/home/aymen/projects/beta", name: "beta" },
        ],
      },
    );
    expect(groups.map((group) => group.id)).toEqual([
      "project:/home/aymen/projects/alpha",
      "project:/home/aymen/projects/beta",
      "workspace:chats",
    ]);
  });
});

describe("groupSessions flat Cursor list", () => {
  it("groups by recency when no project chats exist", () => {
    const groups = groupSessions(
      [
        session({
          key: "websocket:a",
          chatId: "a",
          updatedAt: daysAgo(0),
        }),
        session({
          key: "websocket:b",
          chatId: "b",
          updatedAt: daysAgo(20),
        }),
        session({
          key: "websocket:pinned",
          chatId: "pinned",
          updatedAt: daysAgo(40),
        }),
      ],
      labels,
      {
        pinnedKeys: ["websocket:pinned"],
        archivedKeys: [],
        titleOverrides: {},
        projectNameOverrides: {},
        showArchived: false,
        sort: "updated_desc",
      },
    );

    expect(groups.map((g) => g.id)).toEqual([
      "pinned",
      "date:week",
      "date:month",
    ]);
    expect(groups[0].sessions.map((row) => row.key)).toEqual(["websocket:pinned"]);
  });

  it("keeps pinned chats in pin order, newest pin first, not recency", () => {
    const groups = groupSessions(
      [
        session({
          key: "websocket:old-pin",
          chatId: "old-pin",
          updatedAt: daysAgo(1),
          title: "old pin",
        }),
        session({
          key: "websocket:new-pin",
          chatId: "new-pin",
          updatedAt: daysAgo(20),
          title: "new pin",
        }),
      ],
      labels,
      {
        pinnedKeys: ["websocket:new-pin", "websocket:old-pin"],
        archivedKeys: [],
        titleOverrides: {},
        projectNameOverrides: {},
        showArchived: false,
        sort: "updated_desc",
      },
    );
    expect(groups[0].id).toBe("pinned");
    expect(groups[0].sessions.map((row) => row.key)).toEqual([
      "websocket:new-pin",
      "websocket:old-pin",
    ]);
  });
});

describe("groupSessions extracts pinned above project folders", () => {
  it("lifts a pinned project chat into the Pinned section", () => {
    const groups = groupSessions(
      [
        session({
          key: "websocket:classic",
          chatId: "classic",
          updatedAt: daysAgo(0),
          preview: "classic",
        }),
        session({
          key: "websocket:proj",
          chatId: "proj",
          updatedAt: daysAgo(2),
          preview: "project chat",
          workspaceScope: {
            project_path: "/home/aymen/projects/alpha",
            project_name: "alpha",
            access_mode: "full",
          },
        }),
      ],
      labels,
      {
        pinnedKeys: ["websocket:proj"],
        archivedKeys: [],
        titleOverrides: {},
        projectNameOverrides: {},
        showArchived: false,
        sort: "updated_desc",
        defaultWorkspacePath: "/home/aymen/.navin/workspace",
        recentProjects: [{ path: "/home/aymen/projects/alpha", name: "alpha" }],
      },
    );
    expect(groups.map((group) => group.id)).toEqual([
      "pinned",
      "project:/home/aymen/projects/alpha",
      "workspace:chats",
    ]);
    expect(groups[0].sessions.map((row) => row.key)).toEqual(["websocket:proj"]);
    expect(groups[1].sessions.map((row) => row.key)).toEqual([]);
  });
});

describe("placeChatKey", () => {
  it("reorders pinned chats and unpins back next to the drop target", () => {
    const pinned = placeChatKey(
      ["a", "b", "c"],
      [],
      "c",
      "a",
      true,
    );
    expect(pinned.pinnedKeys).toEqual(["c", "a", "b"]);

    const unpinned = placeChatKey(
      ["c", "a"],
      ["x", "y"],
      "c",
      "y",
      false,
      ["x", "y"],
    );
    expect(unpinned.pinnedKeys).toEqual(["a"]);
    expect(unpinned.chatOrder).toEqual(["x", "c", "y"]);
  });
});

describe("visibleSessionsForLimit", () => {
  it("shows the first 5 chats and keeps the active one", () => {
    const rows = Array.from({ length: 12 }, (_, index) =>
      session({
        key: `websocket:${index}`,
        chatId: String(index),
        updatedAt: daysAgo(index),
      }),
    );
    const visible = visibleSessionsForLimit(rows, INITIAL_GROUP_VISIBLE_COUNT, "websocket:9");
    expect(visible.map((row) => row.key)).toEqual([
      "websocket:0",
      "websocket:1",
      "websocket:2",
      "websocket:3",
      "websocket:4",
      "websocket:9",
    ]);
    expect(visibleSessionsForLimit(rows, INITIAL_GROUP_VISIBLE_COUNT, null)).toHaveLength(5);
  });
});

describe("priorChatKeys", () => {
  it("archives older chats in the same project folder only", () => {
    const keys = priorChatKeys(
      [
        session({
          key: "websocket:keep",
          chatId: "keep",
          updatedAt: "2026-08-19T12:00:00.000Z",
          workspaceScope: {
            project_path: "/home/me/app",
            project_name: "app",
            access_mode: "full",
            restrict_to_workspace: false,
          },
        }),
        session({
          key: "websocket:old",
          chatId: "old",
          updatedAt: "2026-08-18T12:00:00.000Z",
          workspaceScope: {
            project_path: "/home/me/app",
            project_name: "app",
            access_mode: "full",
            restrict_to_workspace: false,
          },
        }),
        session({
          key: "websocket:other",
          chatId: "other",
          updatedAt: "2026-08-01T12:00:00.000Z",
          workspaceScope: {
            project_path: "/home/me/other",
            project_name: "other",
            access_mode: "full",
            restrict_to_workspace: false,
          },
        }),
      ],
      "websocket:keep",
      { defaultWorkspacePath: "/home/me/NavinProjects" },
    );
    expect(keys).toEqual(["websocket:old"]);
  });
});
