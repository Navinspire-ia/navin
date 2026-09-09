// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Last Code desk context: project folder + chat to restore when reopening Code. */

export const LAST_DEV_CONTEXT_KEY = "navin-webui.lastDevContext";

export type LastDevContext = {
  chatKey: string | null;
  projectPath: string | null;
  projectName: string | null;
  updatedAt: number;
};

export function readLastDevContext(): LastDevContext | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(LAST_DEV_CONTEXT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<LastDevContext>;
    if (!parsed || typeof parsed !== "object") return null;
    const chatKey =
      typeof parsed.chatKey === "string" && parsed.chatKey.trim()
        ? parsed.chatKey.trim()
        : null;
    const projectPath =
      typeof parsed.projectPath === "string" && parsed.projectPath.trim()
        ? parsed.projectPath.trim()
        : null;
    const projectName =
      typeof parsed.projectName === "string" && parsed.projectName.trim()
        ? parsed.projectName.trim()
        : null;
    if (!chatKey && !projectPath) return null;
    return {
      chatKey,
      projectPath,
      projectName,
      updatedAt:
        typeof parsed.updatedAt === "number" ? parsed.updatedAt : Date.now(),
    };
  } catch {
    return null;
  }
}

/**
 * Merge into the stored Code context. Omitting ``chatKey`` keeps the previous
 * chat; pass ``chatKey: null`` only when intentionally clearing it.
 */
export function rememberLastDevContext(
  patch: Partial<Omit<LastDevContext, "updatedAt">> & {
    clearChatKey?: boolean;
  },
): void {
  if (typeof window === "undefined") return;
  try {
    const prev = readLastDevContext();
    const clearChat = patch.clearChatKey === true;
    const next: LastDevContext = {
      chatKey: clearChat
        ? null
        : patch.chatKey !== undefined
          ? patch.chatKey
          : (prev?.chatKey ?? null),
      projectPath:
        patch.projectPath !== undefined
          ? patch.projectPath
          : (prev?.projectPath ?? null),
      projectName:
        patch.projectName !== undefined
          ? patch.projectName
          : (prev?.projectName ?? null),
      updatedAt: Date.now(),
    };
    if (!next.chatKey && !next.projectPath) {
      window.localStorage.removeItem(LAST_DEV_CONTEXT_KEY);
      return;
    }
    window.localStorage.setItem(LAST_DEV_CONTEXT_KEY, JSON.stringify(next));
  } catch {
    // private mode / quota
  }
}

export type LastCodeOpen =
  | { action: "noop" }
  | { action: "project"; path: string; name?: string }
  | { action: "chat"; chatKey: string }
  | { action: "open" };

const EPHEMERAL_CODE_VIEWS = new Set([
  "settings",
  "tools",
  "apps",
  "automations",
  "skills",
  "templates",
]);

/** Decide how the sidebar Code button should reopen the last project. */
export function resolveLastCodeOpen(input: {
  view: string;
  currentProjectPath?: string | null;
  last: LastDevContext | null;
  recentProjects?: Array<{ path: string; name?: string | null }>;
  defaultPath?: string | null;
  currentChatKey?: string | null;
  knownChatKeys?: Iterable<string>;
  samePath: (a?: string | null, b?: string | null) => boolean;
  isInternalPath: (path: string) => boolean;
}): LastCodeOpen {
  const recent = input.recentProjects ?? [];
  const projectFromLast = input.last?.projectPath?.trim() || null;
  const projectFromRecent =
    recent.find(
      (entry) =>
        !input.samePath(entry.path, input.defaultPath) &&
        !input.isInternalPath(entry.path),
    )?.path ?? null;
  const projectPath = projectFromLast || projectFromRecent;
  const alreadyOnLastProject =
    input.view === "dev" &&
    !EPHEMERAL_CODE_VIEWS.has(input.view) &&
    !!projectPath &&
    input.samePath(input.currentProjectPath, projectPath);

  if (alreadyOnLastProject) return { action: "noop" };

  if (projectPath) {
    const name =
      (projectFromLast && input.last?.projectName) ||
      recent.find((entry) => input.samePath(entry.path, projectPath))?.name ||
      undefined;
    return { action: "project", path: projectPath, name: name || undefined };
  }

  const known = new Set(input.knownChatKeys ?? []);
  const lastChat = input.last?.chatKey?.trim() || null;
  if (lastChat && (known.size === 0 || known.has(lastChat))) {
    return { action: "chat", chatKey: lastChat };
  }
  const currentChat = input.currentChatKey?.trim() || null;
  if (currentChat && (known.size === 0 || known.has(currentChat))) {
    return { action: "chat", chatKey: currentChat };
  }
  return { action: "open" };
}
