import { deriveTitle } from "@/lib/format";
import type { ChatSummary, SidebarSortMode } from "@/lib/types";
import { normalizeWorkspacePath, projectNameFromPath, sameWorkspacePath } from "@/lib/workspace";

export const COLLAPSED_CHATS_VISIBLE_COUNT = 5;
export const INITIAL_GROUP_VISIBLE_COUNT = 5;
export const GROUP_VISIBLE_INCREMENT = 5;
export const SECTION_PROJECTS_ID = "section:projects";
export const SECTION_CHATS_ID = "section:chats";

export interface SessionGroup {
  id: string;
  label: string;
  sessions: ChatSummary[];
  kind?: "project";
  projectPath?: string;
  projectKey?: string;
  updatedAt?: string | null;
}

export interface ChatGroupLabels {
  pinned: string;
  all: string;
  today: string;
  yesterday: string;
  last7Days: string;
  last30Days: string;
  older: string;
  earlier: string;
  archived: string;
  projects: string;
  fallbackTitle: string;
}

export type DateBucket = "today" | "yesterday" | "week" | "month" | "older";

const DAY_MS = 24 * 60 * 60 * 1000;

/** Cursor-style recency bucket for a session timestamp. */
export function dateBucketOf(
  value: string | null | undefined,
  now: Date = new Date(),
): DateBucket {
  const ts = Date.parse(value ?? "");
  if (!Number.isFinite(ts)) return "older";
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (ts >= startOfToday) return "today";
  if (ts >= startOfToday - DAY_MS) return "yesterday";
  if (ts >= startOfToday - 7 * DAY_MS) return "week";
  if (ts >= startOfToday - 30 * DAY_MS) return "month";
  return "older";
}

export function dateBucketLabel(bucket: DateBucket, labels: ChatGroupLabels): string {
  switch (bucket) {
    case "today":
      return labels.today;
    case "yesterday":
      return labels.yesterday;
    case "week":
      return labels.last7Days;
    case "month":
      return labels.last30Days;
    default:
      return labels.older;
  }
}

export interface ChatGroupingOptions {
  pinnedKeys: string[];
  archivedKeys: string[];
  /** Manual order for unpinned chats. Keys listed here beat recency. */
  chatOrder?: string[];
  titleOverrides: Record<string, string>;
  projectNameOverrides: Record<string, string>;
  showArchived: boolean;
  sort: SidebarSortMode;
  defaultWorkspacePath?: string | null;
  recentProjects?: Array<{ path: string; name?: string }>;
}

/**
 * Prefer project folders when any chat has a project scope; otherwise fall
 * back to a flat Cursor-style recency list (Pinned / Last 7 / … / Archived).
 */
export function groupSessions(
  sessions: ChatSummary[],
  labels: ChatGroupLabels,
  options: ChatGroupingOptions,
): SessionGroup[] {
  const hasProjectChats = sessions.some((session) => session.workspaceScope?.project_path);
  const hasRecentProjects = Boolean(options.recentProjects?.length);
  if (hasProjectChats || hasRecentProjects) {
    return groupSessionsByProject(sessions, labels, options);
  }
  return groupSessionsByDate(sessions, labels, options);
}

/** Flat Cursor-style recency sections. */
function groupSessionsByDate(
  sessions: ChatSummary[],
  labels: ChatGroupLabels,
  options: ChatGroupingOptions,
): SessionGroup[] {
  const now = new Date();
  const buckets = new Map<string, ChatSummary[]>();
  const pinned = new Set(options.pinnedKeys);
  const archived = new Set(options.archivedKeys);
  const chatOrder = options.chatOrder ?? [];

  const archivedSessions: ChatSummary[] = [];
  const normalSessions: ChatSummary[] = [];

  for (const session of sessions) {
    if (archived.has(session.key)) {
      if (options.showArchived) archivedSessions.push(session);
      continue;
    }
    if (pinned.has(session.key)) {
      continue;
    }
    if (options.sort === "title_asc") {
      normalSessions.push(session);
      continue;
    }
    const raw = dateBucketOf(session.updatedAt ?? session.createdAt, now);
    const cursorBucket: DateBucket =
      raw === "today" || raw === "yesterday" || raw === "week" ? "week" : raw;
    const bucket = buckets.get(cursorBucket) ?? [];
    bucket.push(session);
    buckets.set(cursorBucket, bucket);
  }

  const groups: SessionGroup[] = (
    [
      ["week", labels.last7Days],
      ["month", labels.last30Days],
      ["older", labels.older],
    ] as const
  )
    .map(([id, label]) => ({
      id: `date:${id}`,
      label,
      sessions: sortSessions(
        buckets.get(id) ?? [],
        options.sort,
        options.titleOverrides,
        chatOrder,
      ),
    }))
    .filter((group) => group.sessions.length > 0);

  if (options.sort === "title_asc" && normalSessions.length) {
    groups.push({
      id: "date:all",
      label: labels.all,
      sessions: sortSessions(
        normalSessions,
        options.sort,
        options.titleOverrides,
        chatOrder,
      ),
    });
  }
  const pinnedGroup = pinnedSessionGroup(sessions, labels, options);
  if (pinnedGroup) {
    groups.unshift(pinnedGroup);
  }
  if (archivedSessions.length) {
    groups.push({
      id: "archived",
      label: labels.archived,
      sessions: sortSessions(
        archivedSessions,
        options.sort,
        options.titleOverrides,
      ),
    });
  }
  return groups;
}

function groupSessionsByProject(
  sessions: ChatSummary[],
  labels: Pick<ChatGroupLabels, "all" | "pinned">,
  options: ChatGroupingOptions,
): SessionGroup[] {
  const archived = new Set(options.archivedKeys);
  const pinned = new Set(options.pinnedKeys);
  const chatOrder = options.chatOrder ?? [];
  const conversations: ChatSummary[] = [];
  const buckets = new Map<string, {
    path?: string;
    label: string;
    sessions: ChatSummary[];
    updatedAt: string | null;
  }>();

  for (const session of sessions) {
    if (archived.has(session.key) && !options.showArchived) {
      continue;
    }
    if (pinned.has(session.key) && !archived.has(session.key)) {
      continue;
    }
    const scope = session.workspaceScope;
    const path = scope?.project_path || "";
    if (!path || sameWorkspacePath(path, options.defaultWorkspacePath)) {
      conversations.push(session);
      continue;
    }
    const key = normalizeWorkspacePath(path);
    const label = options.projectNameOverrides[key]?.trim()
      || scope?.project_name?.trim()
      || projectNameFromPath(path);
    const bucket = buckets.get(key) ?? {
      path,
      label,
      sessions: [],
      updatedAt: null,
    };
    bucket.sessions.push(session);
    const candidate = session.updatedAt ?? session.createdAt ?? null;
    if (isNewerDate(candidate, bucket.updatedAt)) {
      bucket.updatedAt = candidate;
    }
    buckets.set(key, bucket);
  }

  for (const project of options.recentProjects ?? []) {
    const path = project.path?.trim();
    if (!path || sameWorkspacePath(path, options.defaultWorkspacePath)) {
      continue;
    }
    const key = normalizeWorkspacePath(path);
    if (buckets.has(key)) {
      continue;
    }
    buckets.set(key, {
      path,
      label: options.projectNameOverrides[key]?.trim()
        || project.name?.trim()
        || projectNameFromPath(path),
      sessions: [],
      updatedAt: null,
    });
  }

  const projectGroups: SessionGroup[] = Array.from(buckets.entries()).map(([key, bucket]) => ({
    id: `project:${key}`,
    label: bucket.label,
    kind: "project" as const,
    projectPath: bucket.path,
    projectKey: key,
    updatedAt: bucket.updatedAt,
    sessions: sortProjectSessions(
      bucket.sessions,
      options.sort,
      options.titleOverrides,
      archived,
      chatOrder,
    ),
  }));

  projectGroups.sort((a, b) => {
    const timeOrder = dateToTime(b.updatedAt) - dateToTime(a.updatedAt);
    if (timeOrder !== 0) return timeOrder;
    return a.label.localeCompare(b.label, "en", {
      numeric: true,
      sensitivity: "base",
    });
  });

  const groups: SessionGroup[] = [...projectGroups];
  const chatsUpdatedAt = conversations.reduce<string | null>(
    (best, s) => {
      const candidate = s.updatedAt ?? s.createdAt ?? null;
      return isNewerDate(candidate, best) ? candidate : best;
    },
    null,
  );
  groups.push({
    id: "workspace:chats",
    label: labels.all,
    updatedAt: chatsUpdatedAt,
    sessions: sortProjectSessions(
      conversations,
      options.sort,
      options.titleOverrides,
      archived,
      chatOrder,
    ),
  });

  const pinnedGroup = pinnedSessionGroup(sessions, labels, options);
  if (pinnedGroup) {
    groups.unshift(pinnedGroup);
  }

  return groups;
}

function sortProjectSessions(
  sessions: ChatSummary[],
  sort: SidebarSortMode,
  titleOverrides: Record<string, string>,
  archived: Set<string>,
  chatOrder: string[],
): ChatSummary[] {
  return sortSessions(sessions, sort, titleOverrides, chatOrder).sort((a, b) => {
    const archiveOrder = Number(archived.has(a.key)) - Number(archived.has(b.key));
    if (archiveOrder !== 0) return archiveOrder;
    return 0;
  });
}

export function limitGroups(
  groups: SessionGroup[],
  limit: number,
  activeKey: string | null,
  collapsedGroups: Record<string, boolean>,
): SessionGroup[] {
  let remaining = Math.max(0, limit);
  let activeVisible = !activeKey;
  const out: SessionGroup[] = [];

  for (const group of groups) {
    if (isCollapsedProject(group, collapsedGroups)) {
      out.push({ ...group, sessions: [] });
      continue;
    }
    const visible = remaining > 0
      ? group.sessions.slice(0, remaining)
      : [];
    remaining -= visible.length;
    if (activeKey && visible.some((session) => session.key === activeKey)) {
      activeVisible = true;
    }
    if (visible.length > 0) {
      out.push({ ...group, sessions: visible });
    }
  }

  if (activeVisible || !activeKey) return out;

  for (const group of groups) {
    if (isCollapsedProject(group, collapsedGroups)) continue;
    const active = group.sessions.find((session) => session.key === activeKey);
    if (!active) continue;
    const existing = out.find((item) => item.id === group.id);
    if (existing) {
      existing.sessions = [...existing.sessions, active];
    } else {
      out.push({ ...group, sessions: [active] });
    }
    return out;
  }

  return out;
}

export function isCollapsedProject(
  group: SessionGroup,
  collapsedGroups: Record<string, boolean>,
): boolean {
  return group.kind === "project" && Boolean(collapsedGroups[group.id]);
}

export function isFoldableChatsGroup(group: SessionGroup): boolean {
  return (
    group.id === "pinned"
    || group.id === "archived"
    || group.id === "workspace:chats"
    || group.id === "date:all"
    || group.id.startsWith("date:")
  );
}

export function isFoldedChatsGroup(
  group: SessionGroup,
  collapsedGroups: Record<string, boolean>,
): boolean {
  if (!isFoldableChatsGroup(group)) return false;
  if (group.sessions.length <= COLLAPSED_CHATS_VISIBLE_COUNT) return false;
  if (collapsedGroups[group.id] === false) return false;
  if (collapsedGroups[group.id] === true) return true;
  return (
    group.id === "archived"
    || group.id === "date:month"
    || group.id === "date:older"
    || group.id === "workspace:chats"
  );
}

export function visibleSessionsForGroup(
  group: SessionGroup,
  activeKey: string | null,
  collapsedGroups: Record<string, boolean>,
): ChatSummary[] {
  if (!isFoldedChatsGroup(group, collapsedGroups)) {
    return group.sessions;
  }
  return visibleSessionsForLimit(group.sessions, INITIAL_GROUP_VISIBLE_COUNT, activeKey);
}

export function visibleSessionsForLimit(
  sessions: ChatSummary[],
  limit: number,
  activeKey: string | null,
): ChatSummary[] {
  const cap = Math.max(0, limit);
  const visible = sessions.slice(0, cap);
  if (!activeKey || visible.some((session) => session.key === activeKey)) {
    return visible;
  }
  const active = sessions.find((session) => session.key === activeKey);
  return active ? [...visible, active] : visible;
}

/**
 * Older chats in the same folder (or in the loose Chats list) as ``key``.
 * Used by "Archive prior chats".
 */
export function priorChatKeys(
  sessions: readonly ChatSummary[],
  key: string,
  options: {
    defaultWorkspacePath?: string | null;
    archivedKeys?: readonly string[];
  } = {},
): string[] {
  const current = sessions.find((session) => session.key === key);
  if (!current) return [];
  const currentPath = current.workspaceScope?.project_path || "";
  const inProject = Boolean(
    currentPath && !sameWorkspacePath(currentPath, options.defaultWorkspacePath),
  );
  const currentTime = sessionTime(current, "updatedAt");
  const archived = new Set(options.archivedKeys ?? []);
  return sessions
    .filter((session) => {
      if (session.key === key || archived.has(session.key)) return false;
      const path = session.workspaceScope?.project_path || "";
      const sameGroup = inProject
        ? sameWorkspacePath(path, currentPath)
        : !path || sameWorkspacePath(path, options.defaultWorkspacePath);
      if (!sameGroup) return false;
      return sessionTime(session, "updatedAt") < currentTime;
    })
    .map((session) => session.key);
}

export function displayTitle(
  session: ChatSummary,
  titleOverrides: Record<string, string>,
  fallbackTitle: string,
): string {
  return (
    titleOverrides[session.key]?.trim()
    || session.title?.trim()
    || deriveTitle(session.preview, fallbackTitle)
  );
}

function sortSessions(
  sessions: ChatSummary[],
  sort: SidebarSortMode,
  titleOverrides: Record<string, string>,
  chatOrder: string[] = [],
): ChatSummary[] {
  const rank = new Map(chatOrder.map((key, index) => [key, index]));
  const copy = [...sessions];
  copy.sort((a, b) => {
    const aRank = rank.get(a.key) ?? Number.POSITIVE_INFINITY;
    const bRank = rank.get(b.key) ?? Number.POSITIVE_INFINITY;
    if (aRank !== bRank) return aRank - bRank;
    if (sort === "title_asc") {
      const titleOrder = titleForSort(a, titleOverrides).localeCompare(
        titleForSort(b, titleOverrides),
        "en",
        { numeric: true, sensitivity: "base" },
      );
      if (titleOrder !== 0) return titleOrder;
      return sessionTime(b, "updatedAt") - sessionTime(a, "updatedAt");
    }
    const aTime = sessionTime(a, sort === "created_desc" ? "createdAt" : "updatedAt");
    const bTime = sessionTime(b, sort === "created_desc" ? "createdAt" : "updatedAt");
    return bTime - aTime;
  });
  return copy;
}

function pinnedSessionGroup(
  sessions: ChatSummary[],
  labels: Pick<ChatGroupLabels, "pinned">,
  options: ChatGroupingOptions,
): SessionGroup | null {
  const archived = new Set(options.archivedKeys);
  const byKey = new Map(
    sessions
      .filter((session) => !archived.has(session.key))
      .map((session) => [session.key, session]),
  );
  const pinnedSessions: ChatSummary[] = [];
  const seen = new Set<string>();
  for (const key of options.pinnedKeys) {
    const session = byKey.get(key);
    if (!session || seen.has(key)) continue;
    seen.add(key);
    pinnedSessions.push(session);
  }
  if (!pinnedSessions.length) return null;
  return {
    id: "pinned",
    label: labels.pinned,
    sessions: pinnedSessions,
  };
}

/** Move ``dragKey`` to the slot currently held by ``targetKey``. */
export function reorderKeyList(
  keys: readonly string[],
  dragKey: string,
  targetKey: string,
): string[] {
  if (dragKey === targetKey) return [...keys];
  const from = keys.indexOf(dragKey);
  const to = keys.indexOf(targetKey);
  if (from === -1 || to === -1) return [...keys];
  const next = [...keys];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/** Pin ``key`` at the top, or keep it where it already sits. */
export function pinChatKey(pinnedKeys: readonly string[], key: string): string[] {
  const without = pinnedKeys.filter((item) => item !== key);
  return [key, ...without];
}

export function unpinChatKey(pinnedKeys: readonly string[], key: string): string[] {
  return pinnedKeys.filter((item) => item !== key);
}

/**
 * Drop ``dragKey`` onto ``targetKey``. A pinned target pins the dragged chat
 * at that slot; an unpinned target unpins it and restores it next to the
 * target in the unpinned list.
 */
export function placeChatKey(
  pinnedKeys: readonly string[],
  chatOrder: readonly string[],
  dragKey: string,
  targetKey: string,
  targetPinned: boolean,
  visibleUnpinnedKeys: readonly string[] = [],
): { pinnedKeys: string[]; chatOrder: string[] } {
  if (!dragKey || dragKey === targetKey) {
    return { pinnedKeys: [...pinnedKeys], chatOrder: [...chatOrder] };
  }
  const nextPinned = pinnedKeys.filter((key) => key !== dragKey);
  if (targetPinned) {
    const idx = nextPinned.indexOf(targetKey);
    nextPinned.splice(idx === -1 ? 0 : idx, 0, dragKey);
    return {
      pinnedKeys: nextPinned,
      chatOrder: chatOrder.filter((key) => key !== dragKey),
    };
  }
  const visible = visibleUnpinnedKeys.filter((key) => key !== dragKey);
  const seeded = mergeKeyOrder(
    chatOrder.filter((key) => key !== dragKey),
    visible,
  );
  const idx = seeded.indexOf(targetKey);
  seeded.splice(idx === -1 ? seeded.length : idx, 0, dragKey);
  return { pinnedKeys: nextPinned, chatOrder: seeded };
}

function mergeKeyOrder(preferred: readonly string[], visible: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const key of preferred) {
    if (!visible.includes(key) || seen.has(key)) continue;
    seen.add(key);
    out.push(key);
  }
  for (const key of visible) {
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(key);
  }
  for (const key of preferred) {
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(key);
  }
  return out;
}

function isNewerDate(a: string | null, b: string | null): boolean {
  return dateToTime(a) > dateToTime(b);
}

function dateToTime(value: string | null | undefined): number {
  const ts = Date.parse(value ?? "");
  return Number.isFinite(ts) ? ts : 0;
}

function titleForSort(
  session: ChatSummary,
  titleOverrides: Record<string, string>,
): string {
  return (
    titleOverrides[session.key]?.trim()
    || session.title?.trim()
    || deriveTitle(session.preview, "new chat")
  ).toLocaleLowerCase("en");
}

function sessionTime(session: ChatSummary, field: "createdAt" | "updatedAt"): number {
  const ts = Date.parse(session[field] ?? "");
  return Number.isFinite(ts) ? ts : 0;
}
