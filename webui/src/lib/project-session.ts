// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Resume the most recent chat bound to a project workspace.
 *
 * Used by Open folder / `navin .` so we never invent a blank chat when one
 * already exists for that folder.
 */

import type { ChatSummary } from "@/lib/types";
import { sameWorkspacePath } from "@/lib/workspace";

/** Shared file-tree session. Never a real sidebar chat. */
export const SYNTHETIC_WEBUI_CHAT_IDS = new Set(["webui-dev"]);

export function isListedSidebarSession(
  session: Pick<ChatSummary, "chatId" | "key">,
): boolean {
  const id = (session.chatId || session.key.split(":").slice(1).join(":")).trim();
  return Boolean(id) && !SYNTHETIC_WEBUI_CHAT_IDS.has(id);
}

/** Empty untitled agent: no generated title and no first message yet. */
export function isBlankChat(session: ChatSummary): boolean {
  return !session.title?.trim() && !session.preview?.trim();
}

/**
 * Most recent empty "New chat" for this project (or an unbound blank).
 * Sidebar clicks must reuse this instead of minting another row.
 */
export function findBlankChatForWorkspace(
  sessions: readonly ChatSummary[],
  projectPath?: string | null,
): ChatSummary | null {
  const matches = sessions.filter((session) => {
    if (!isBlankChat(session) || !isListedSidebarSession(session)) return false;
    const path = session.workspaceScope?.project_path;
    if (!projectPath?.trim()) return !path;
    if (!path) return true;
    return sameWorkspacePath(path, projectPath);
  });
  if (matches.length === 0) return null;
  matches.sort((a, b) => {
    const aTs = Date.parse(a.updatedAt ?? a.createdAt ?? "") || 0;
    const bTs = Date.parse(b.updatedAt ?? b.createdAt ?? "") || 0;
    return bTs - aTs;
  });
  return matches[0] ?? null;
}

/**
 * Reuse the current blank "New Chat" when creating a folder, so we bind that
 * agent instead of minting a second empty one. Falls back to any blank for
 * the same workspace so "New chat" never stacks empty rows.
 */
export function findReusableEmptyChat(
  sessions: readonly ChatSummary[],
  preferredKey: string | null | undefined,
  defaultWorkspacePath?: string | null,
): ChatSummary | null {
  if (preferredKey) {
    const active = sessions.find((session) => session.key === preferredKey);
    if (active && isBlankChat(active) && isListedSidebarSession(active)) {
      const path = active.workspaceScope?.project_path;
      if (!path || sameWorkspacePath(path, defaultWorkspacePath)) {
        return active;
      }
    }
  }
  return findBlankChatForWorkspace(sessions, defaultWorkspacePath);
}

/** Most recently updated session whose workspace matches ``projectPath``. */
export function findSessionForProject(
  sessions: readonly ChatSummary[],
  projectPath: string,
): ChatSummary | null {
  const trimmed = projectPath.trim();
  if (!trimmed) return null;
  const matches = sessions.filter((session) =>
    sameWorkspacePath(session.workspaceScope?.project_path, trimmed),
  );
  if (matches.length === 0) return null;
  matches.sort((a, b) => {
    const aTs = Date.parse(a.updatedAt ?? a.createdAt ?? "") || 0;
    const bTs = Date.parse(b.updatedAt ?? b.createdAt ?? "") || 0;
    return bTs - aTs;
  });
  return matches[0] ?? null;
}

/**
 * ``attached`` is emitted for both ``new_chat`` and ``attach``. Only an
 * unknown chat id should resolve a pending create/fork promise - otherwise a
 * concurrent attach can steal the promise and leave an orphan empty chat.
 */
export function shouldResolvePendingNewChat(
  pending: boolean,
  chatIdWasKnown: boolean,
): boolean {
  return pending && !chatIdWasKnown;
}
