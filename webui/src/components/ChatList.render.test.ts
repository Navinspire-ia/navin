// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import "@/i18n";
import { ChatList, RunningDots } from "@/components/ChatList";
import type { ChatSummary } from "@/lib/types";

const RECENT = new Date(Date.now() - 2 * 3_600_000).toISOString();

function session(key: string, title: string): ChatSummary {
  const chatId = key.split(":")[1] ?? key;
  return {
    key,
    channel: "websocket",
    chatId,
    createdAt: RECENT,
    updatedAt: RECENT,
    title,
    preview: title,
  };
}

const noop = () => {};

function renderList(runningChatIds: string[]) {
  return renderToStaticMarkup(
    createElement(ChatList, {
      sessions: [session("websocket:aaa", "Signing the build"), session("websocket:bbb", "Invoice details")],
      activeKey: "websocket:bbb",
      onSelect: noop,
      onRequestDelete: noop,
      onTogglePin: noop,
      onRequestRename: noop,
      onToggleArchive: noop,
      runningChatIds,
      showTimestamps: true,
    }),
  );
}

function rowOf(html: string, title: string): string {
  const start = html.indexOf(`<li`, Math.max(0, html.indexOf(title) - 2_000));
  const end = html.indexOf("</li>", html.indexOf(title));
  return html.slice(start, end);
}

describe("ChatList running indicator", () => {
  it("shows the animated dots in the timestamp slot for a running chat only", () => {
    const html = renderList(["aaa"]);
    const runningRow = rowOf(html, "Signing the build");
    const idleRow = rowOf(html, "Invoice details");

    expect(runningRow).toContain('data-testid="chat-running-dots"');
    expect(runningRow).toContain('aria-label="Agent running"');
    expect(runningRow).not.toMatch(/tabular-nums[^>]*>2h</);

    expect(idleRow).not.toContain("chat-running-dots");
    expect(idleRow).toMatch(/tabular-nums[^>]*>2h</);
  });

  it("puts pinned chats in a Pinned section with a drag handle", () => {
    const html = renderToStaticMarkup(
      createElement(ChatList, {
        sessions: [
          session("websocket:aaa", "Signing the build"),
          session("websocket:bbb", "Invoice details"),
        ],
        activeKey: "websocket:bbb",
        onSelect: noop,
        onRequestDelete: noop,
        onTogglePin: noop,
        onMoveChat: noop,
        onRequestRename: noop,
        onToggleArchive: noop,
        pinnedKeys: ["websocket:aaa"],
        showTimestamps: true,
      }),
    );
    expect(html).toContain('aria-label="Pinned"');
    expect(html.indexOf("Signing the build")).toBeLessThan(html.indexOf("Invoice details"));
    expect(html).toContain('data-testid="chat-drag-handle"');
    expect(html).toContain("Drag to reorder");
  });

  it("keeps the leading glyph column free of the old spinner ring", () => {
    const html = renderList(["aaa"]);
    expect(html).not.toContain("animate-spin");
  });

  it("renders four staggered dots that go clockwise and respect reduced motion", () => {
    const html = renderToStaticMarkup(createElement(RunningDots, { label: "Agent running" }));
    const delays = Array.from(html.matchAll(/animation-delay:(\d+)ms/g), (m) => Number(m[1]));
    expect(delays).toEqual([0, 300, 900, 600]);
    expect(html.match(/chat-run-dot/g)).toHaveLength(4);
    expect(html.match(/motion-reduce:animate-none/g)).toHaveLength(4);
    expect(html).toContain('role="img"');
    expect(html).toContain('title="Agent running"');
  });
});
