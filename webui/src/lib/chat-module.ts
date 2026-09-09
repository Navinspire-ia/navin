// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Resolve which shell module a chat should reopen in. */

export const CHAT_MODULE_VIEWS = [
  "chat",
  "dev",
  "risklens",
  "scraping",
  "content",
  "marketing",
  "montage",
  "ads",
  "seo",
  "leads",
  "tenders",
  "career",
  "ops",
  "notes",
  "trading",
  "meeting",
  "crm",
] as const;

export type ChatModuleView = (typeof CHAT_MODULE_VIEWS)[number];

const CHAT_MODULE_SET = new Set<string>(CHAT_MODULE_VIEWS);

/** Normalize stored / URL aliases to the internal ShellView id. */
export function normalizeChatModuleView(value: string | null | undefined): ChatModuleView | null {
  const raw = (value || "").trim().toLowerCase();
  if (!raw) return null;
  if (raw === "code") return "dev";
  if (CHAT_MODULE_SET.has(raw)) return raw as ChatModuleView;
  return null;
}

/**
 * Infer the owning module from the chat preview's leading slash command.
 *
 * Sidebar previews look like `/forge ship it` or `/meeting Build…`. When
 * `module_by_key` was never written (older chats, or turns started from
 * `#/new`), this keeps "open the right desk" working from the list alone.
 */
export function inferChatModuleFromPreview(
  preview: string | null | undefined,
): ChatModuleView | null {
  const raw = (preview || "").trim();
  if (!raw.startsWith("/")) return null;
  const command = raw.slice(1).split(/\s+/u, 1)[0]?.toLowerCase() || "";
  if (!command) return null;
  switch (command) {
    case "forge":
    case "cruise":
    case "mission":
    case "debug":
    case "inspect":
    case "code":
    case "dev":
      return "dev";
    case "ask":
      return "chat";
    case "meeting":
      return "meeting";
    case "montage":
      return "montage";
    case "seo":
      return "seo";
    case "ads":
      return "ads";
    case "leads":
      return "leads";
    case "tenders":
      return "tenders";
    case "career":
    case "jobs":
    case "freelance":
      return "career";
    case "trading":
    case "trade":
      return "trading";
    case "scraping":
    case "scrape":
      return "scraping";
    case "marketing":
      return "marketing";
    case "risklens":
    case "premortem":
      return "risklens";
    case "content":
      return "content";
    case "ops":
      return "ops";
    case "notes":
      return "notes";
    case "crm":
      return "crm";
    default:
      return null;
  }
}

/**
 * Pick the shell view for a selected chat.
 *
 * Order: slash in the preview → stored module tag → current workbench → chat.
 * The preview wins on purpose: a thread once opened inside Code can still be
 * an `/ask` turn, and must reopen as full chat instead of staying on `#/code`.
 */
export function resolveChatOpenView(
  sessionKey: string,
  moduleByKey: Record<string, string> | null | undefined,
  fallbackView: string | null | undefined = "chat",
  preview?: string | null,
): ChatModuleView {
  const inferred = inferChatModuleFromPreview(preview);
  if (inferred) return inferred;
  const stored = normalizeChatModuleView(moduleByKey?.[sessionKey]);
  if (isStickyChatModule(stored)) return stored;
  if (stored === "chat") return "chat";
  const fallback = normalizeChatModuleView(fallbackView);
  if (fallback) return fallback;
  return "chat";
}

/**
 * Sticky modules own chats. Visiting chat or settings must not stamp
 * the current thread.
 */
export function isStickyChatModule(
  view: string | null | undefined,
): view is Exclude<ChatModuleView, "chat"> {
  const normalized = normalizeChatModuleView(view);
  return Boolean(normalized && normalized !== "chat");
}

/** True when this view is a product module that owns chats (not settings). */
export function isChatOwningModule(view: string | null | undefined): boolean {
  return isStickyChatModule(view);
}

/**
 * Key a folder consistently: the same directory arrives with or without a
 * trailing slash and with either separator depending on which picker sent it.
 */
export function projectModuleKey(path: string | null | undefined): string {
  const raw = (path || "").trim().replace(/\\/g, "/");
  if (!raw) return "";
  return raw.replace(/\/+$/, "") || raw;
}

/**
 * Where a freshly created chat opens.
 *
 * Only the workbench's own entry points (Code's + tab, a project agent, the
 * first message typed on a workbench welcome screen) keep the editor. Plain
 * "New chat" always lands in Tchat, which needs no project to answer.
 */
export function viewForCreatedChat(
  currentView: string | null | undefined,
  options?: { keepWorkbench?: boolean; isWorkbench?: boolean },
): "chat" | string {
  if (!options?.keepWorkbench) return "chat";
  if (!options.isWorkbench) return "chat";
  return (currentView || "chat").trim() || "chat";
}

/**
 * Pick the shell view for a project folder, so a folder tagged Code opens the
 * Code workbench on launch instead of dropping into plain chat.
 */
export function resolveProjectOpenView(
  path: string | null | undefined,
  moduleByPath: Record<string, string> | null | undefined,
  fallbackView: string | null | undefined = "chat",
): ChatModuleView {
  const key = projectModuleKey(path);
  const stored = key ? normalizeChatModuleView(moduleByPath?.[key]) : null;
  if (isStickyChatModule(stored)) return stored;
  const fallback = normalizeChatModuleView(fallbackView);
  if (fallback) return fallback;
  return "chat";
}
