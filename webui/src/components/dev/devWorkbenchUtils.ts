// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Pure helpers extracted from DevWorkbench (split #8).
 */

import { isNavinOwnPort } from "@/lib/preview-url";
import type { FilePreviewPayload, FileTreeEntry, ReviewChangeStatus } from "@/lib/types";

export const PREVIEW_URL_STORAGE_KEY = "navin.dev.previewUrl";

export const GIT_STATUS_LETTERS: Record<string, string> = {
  modified: "M",
  added: "A",
  deleted: "D",
  renamed: "R",
  copied: "C",
  typechange: "T",
  conflict: "!",
  untracked: "U",
};

/**
 * Drop one path from a per-path record, returning it unchanged when the path is
 * absent so React can skip the re-render.
 */
export function dropPathKey<T>(
  record: Record<string, T>,
  path: string,
): Record<string, T> {
  if (!(path in record)) return record;
  const next = { ...record };
  delete next[path];
  return next;
}

/** Move one path of a per-path record to its new path, after a rename. */
export function movePathKey<T>(
  record: Record<string, T>,
  from: string,
  to: string,
): Record<string, T> {
  if (!(from in record)) return record;
  const next = { ...record };
  const moved = next[from];
  delete next[from];
  if (moved !== undefined) next[to] = moved;
  return next;
}

/** Last URL that successfully opened in Preview (hint for rediscovery only). */
export function readStoredPreviewUrl(): string | null {
  try {
    const raw = localStorage.getItem(PREVIEW_URL_STORAGE_KEY)?.trim();
    if (!raw || !/^https?:\/\//i.test(raw)) return null;
    try {
      const parsed = new URL(raw);
      const port = parsed.port || (parsed.protocol === "https:" ? "443" : "80");
      // Never restore Navin's own ports as the project preview URL. The list
      // is shared with preview-url.ts and covers the desktop shell's ports and
      // whatever dynamic port actually served this page.
      if (isNavinOwnPort(port) || port === "5173") {
        localStorage.removeItem(PREVIEW_URL_STORAGE_KEY);
        return null;
      }
    } catch {
      return null;
    }
    return raw;
  } catch {
    return null;
  }
}

export function persistPreviewUrl(url: string): void {
  try {
    if (!url.trim()) {
      localStorage.removeItem(PREVIEW_URL_STORAGE_KEY);
      return;
    }
    try {
      const parsed = new URL(/^https?:\/\//i.test(url) ? url : `http://${url}`);
      const port = parsed.port || (parsed.protocol === "https:" ? "443" : "80");
      if (isNavinOwnPort(port)) {
        localStorage.removeItem(PREVIEW_URL_STORAGE_KEY);
        return;
      }
    } catch {
      // keep going and store the raw value
    }
    localStorage.setItem(PREVIEW_URL_STORAGE_KEY, url);
  } catch {
    // ignore
  }
}

/** Port from the Preview URL bar - Vite/Next change it every start. */
export function previewPortFromUrl(raw: string): number | null {
  const value = raw.trim();
  if (!value) return null;
  try {
    const normalized = /^https?:\/\//i.test(value) ? value : `http://${value}`;
    const parsed = new URL(normalized);
    if (parsed.port) {
      const port = Number(parsed.port);
      return Number.isInteger(port) && port > 0 && port <= 65535 ? port : null;
    }
    if (parsed.protocol === "https:") return 443;
    if (parsed.protocol === "http:") return 80;
  } catch {
    // fall through
  }
  return null;
}

export function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function reviewStatusBadge(status: ReviewChangeStatus): {
  letter: string;
  className: string;
} {
  switch (status) {
    case "created":
      return { letter: "A", className: "text-emerald-500" };
    case "deleted":
      return { letter: "D", className: "text-red-500" };
    default:
      return { letter: "M", className: "text-amber-500" };
  }
}

/** Sum pending-review line counts for the Changes rail / composer bar. */
export function reviewLineStats(
  changes: Array<{ added?: number; deleted?: number }>,
): { added: number; deleted: number } {
  let added = 0;
  let deleted = 0;
  for (const change of changes) {
    if (Number.isFinite(change.added)) {
      added += Math.max(0, Math.round(change.added as number));
    }
    if (Number.isFinite(change.deleted)) {
      deleted += Math.max(0, Math.round(change.deleted as number));
    }
  }
  return { added, deleted };
}

// Windows paths (C:\..., \\wsl.localhost\...) use backslashes: split on both.
export function baseName(path: string): string {
  return path.replace(/[/\\]+$/, "").split(/[/\\]/).pop() || path;
}

/**
 * Markdown / HTML / CSV default to Preview, except a pending review: then the
 * source editor must open so the gray vs-previous diff is visible.
 */
export function defaultEditorPreviewOn(hasPendingReview: boolean): boolean {
  return !hasPendingReview;
}

/** Pending review files that can be opened as a tab or a git diff. */
export function reviewOpenableFiles<T extends { status: string }>(changes: T[]): T[] {
  return changes.filter((change) => change.status !== "deleted");
}

/** How many review files open as tabs / chips. The rest stay in Other files. */
export const REVIEW_VISIBLE_FILE_LIMIT = 3;

/**
 * Show at most `limit` review files. The active file stays visible; everything
 * else goes in the overflow menu so opening a large review cannot flood tabs.
 */
export function splitReviewVisibleFiles<T extends { path: string }>(
  files: T[],
  activePath: string | null,
  limit = REVIEW_VISIBLE_FILE_LIMIT,
): { visible: T[]; overflow: T[] } {
  if (files.length <= limit) {
    return { visible: files, overflow: [] };
  }
  const active = activePath
    ? files.find((file) => file.path === activePath) ?? null
    : null;
  const rest = active ? files.filter((file) => file.path !== active.path) : files;
  const visible = active ? [active, ...rest.slice(0, limit - 1)] : rest.slice(0, limit);
  const visiblePaths = new Set(visible.map((file) => file.path));
  return {
    visible,
    overflow: files.filter((file) => !visiblePaths.has(file.path)),
  };
}

/** Add pending review files as editor tabs (caller should pass at most 3). */
export function mergeReviewTabs(
  tabs: Array<{ path: string; displayPath: string; name: string }>,
  files: Array<{ path: string; display_path: string }>,
): Array<{ path: string; displayPath: string; name: string }> {
  const next = [...tabs];
  const seen = new Set(next.map((tab) => tab.path));
  for (const file of files) {
    if (seen.has(file.path)) continue;
    seen.add(file.path);
    next.push({
      path: file.path,
      displayPath: file.display_path || file.path,
      name: baseName(file.path),
    });
  }
  return next;
}

/** True when this editor tab is the file currently shown in the diff view. */
export function tabMatchesDiffFile(
  tabPath: string,
  diffFile: string | null,
  relativeOf: (path: string) => string,
): boolean {
  if (!diffFile) return false;
  const rel = relativeOf(tabPath).replace(/\\/g, "/");
  const target = diffFile.replace(/\\/g, "/").replace(/^\/+/, "");
  if (rel === target) return true;
  return tabPath.replace(/\\/g, "/").endsWith(`/${target}`);
}

export function isMarkdownEditorPath(
  path: string,
  preview: FilePreviewPayload | null | undefined,
): boolean {
  if (preview?.kind === "markdown") return true;
  if ((preview?.language ?? "").toLowerCase() === "markdown") return true;
  return /\.(md|mdx|markdown)$/i.test(path);
}

export function isHtmlEditorPath(
  path: string,
  preview: FilePreviewPayload | null | undefined,
): boolean {
  if (preview?.kind === "html") return true;
  return /\.(html?|xhtml)$/i.test(path);
}

export function isCsvEditorPath(
  path: string,
  preview: FilePreviewPayload | null | undefined,
): boolean {
  if (preview?.kind === "csv") return true;
  if ((preview?.language ?? "").toLowerCase() === "csv") return true;
  return /\.(csv|tsv)$/i.test(path);
}

export function gitStatusColor(status: string): string {
  if (status === "untracked" || status === "added") return "text-emerald-500";
  if (status === "deleted" || status === "conflict") return "text-red-500";
  return "text-amber-500";
}

/**
 * Starting directory for a new integrated terminal.
 *
 * The explicit folder (context menu "Open in Integrated Terminal") wins;
 * otherwise the project the desk is on. Always sending a folder matters: the
 * gateway falls back to its default workspace when it cannot resolve the
 * session scope (no active chat, scope not persisted yet), which is never
 * where the user is working.
 */
export function terminalStartDir(
  explicitCwd: string | null | undefined,
  projectRoot: string | null | undefined,
): string | undefined {
  const explicit = (explicitCwd ?? "").trim();
  if (explicit) return explicit;
  const root = (projectRoot ?? "").trim();
  return root || undefined;
}

/** Scrollback title for an agent exec terminal tab. */
export function agentTermTitle(command: string | undefined): string {
  const trimmed = (command ?? "").trim().replace(/\s+/g, " ");
  if (!trimmed) return "agent";
  return trimmed.length > 32 ? `${trimmed.slice(0, 32)}…` : trimmed;
}

export function formatTokenCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}k`;
  return String(value);
}

/** Cursor-style counts in the context panel (~148.4K). */
export function formatContextTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.max(0, Math.round(value)));
}

/** Segment widths as a share of the model window. No minimum padding. */
export function contextBucketWidths(
  buckets: Array<{ id: string; tokens: number }>,
  windowTokens: number,
): Array<{ id: string; percent: number }> {
  if (windowTokens <= 0) {
    return buckets.map((bucket) => ({ id: bucket.id, percent: 0 }));
  }
  return buckets.map((bucket) => ({
    id: bucket.id,
    percent: Math.max(0, (bucket.tokens / windowTokens) * 100),
  }));
}

export const CONTEXT_BUCKET_COLORS: Record<string, string> = {
  system: "#94a3b8",
  bootstrap: "#64748b",
  rules: "#34d399",
  memory: "#2dd4bf",
  tool_contract: "#818cf8",
  skills: "#fb923c",
  subagents: "#60a5fa",
  tools: "#a78bfa",
  mcp: "#c084fc",
  history: "#f472b6",
  summary: "#e879f9",
  conversation: "#fb7185",
  tool_results: "#fbbf24",
  other: "#a8a29e",
};

export function contextBucketLabel(
  id: string,
  fallback: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const keys: Record<string, string> = {
    system: "dev.status.contextBucketSystem",
    bootstrap: "dev.status.contextBucketBootstrap",
    rules: "dev.status.contextBucketRules",
    memory: "dev.status.contextBucketMemory",
    tool_contract: "dev.status.contextBucketToolContract",
    skills: "dev.status.contextBucketSkills",
    subagents: "dev.status.contextBucketSubagents",
    tools: "dev.status.contextBucketTools",
    mcp: "dev.status.contextBucketMcp",
    history: "dev.status.contextBucketHistory",
    summary: "dev.status.contextBucketSummary",
    conversation: "dev.status.contextBucketConversation",
    tool_results: "dev.status.contextBucketToolResults",
    other: "dev.status.contextBucketOther",
  };
  const key = keys[id];
  return key ? t(key, { defaultValue: fallback }) : fallback;
}

export function contextReasonLabel(
  reason: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  switch (reason) {
    case "open_file":
      return t("dev.status.contextReasonOpen", { defaultValue: "open tab" });
    case "git_dirty":
      return t("dev.status.contextReasonDirty", { defaultValue: "git dirty" });
    case "file_mention":
      return t("dev.status.contextReasonMention", { defaultValue: "mentioned" });
    case "project_rules":
      return t("dev.status.contextReasonRules", { defaultValue: "project rules" });
    default:
      return reason;
  }
}

/** Slash-normalized path without a trailing separator, for tree key matching. */
export function normalizeTreePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "");
}

function parentTreePath(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut > 0 ? path.slice(0, cut) : "";
}

function isAbsoluteTreePath(path: string): boolean {
  return (
    path.startsWith("/") ||
    /^[A-Za-z]:[\\/]/.test(path) ||
    path.startsWith("\\\\") ||
    path.startsWith("//")
  );
}

/**
 * Compare explorer keys across Windows, POSIX and WSL spellings of the same
 * folder. Agent ``file_edit`` events use Path.as_posix(); the tree lists with
 * whatever the gateway scandir returned, which often differs on Windows/WSL.
 */
export function canonicalTreePath(path: string): string {
  let value = normalizeTreePath(path);
  const unc = value.match(/^\/\/wsl(?:\.localhost|\$)\/[^/]+(.*)$/i);
  if (unc) value = unc[1] || "/";
  const drive = value.match(/^([A-Za-z]):(\/.*)?$/);
  if (drive) value = `/mnt/${drive[1].toLowerCase()}${drive[2] || "/"}`;
  return value.toLowerCase();
}

/** Project-relative posix path, or "" when abs is the root / cannot be mapped. */
export function treeRelativePath(
  abs: string,
  rootPath: string | null | undefined,
): string {
  const file = normalizeTreePath(abs);
  if (!file) return "";
  if (!isAbsoluteTreePath(file)) return file.replace(/^\/+/, "");
  const root = normalizeTreePath(rootPath ?? "");
  if (!root) return "";
  const fileKey = canonicalTreePath(file);
  const rootKey = canonicalTreePath(root);
  if (!fileKey || !rootKey) return "";
  if (fileKey === rootKey) return "";
  if (!fileKey.startsWith(`${rootKey}/`)) return "";
  const suffix = fileKey.slice(rootKey.length + 1);
  const suffixParts = suffix.split("/").filter(Boolean);
  const fileParts = file.split("/").filter((part) => part !== "");
  if (fileParts.length >= suffixParts.length) {
    return fileParts.slice(fileParts.length - suffixParts.length).join("/");
  }
  return suffix;
}

/** Chat id used by live `file_edit` / `fs_changed` events. */
export function treeSessionChatId(
  sessionKey: string | null | undefined,
): string | null {
  const raw = (sessionKey ?? "").trim();
  if (!raw) return null;
  return raw.startsWith("websocket:") ? raw.slice("websocket:".length) : raw;
}

export type TreeNodeLike = {
  entries: FileTreeEntry[];
  loading: boolean;
  error: string | null;
};

export function sortFileTreeEntries(entries: FileTreeEntry[]): FileTreeEntry[] {
  return [...entries].sort((left, right) => {
    if (left.type !== right.type) return left.type === "dir" ? -1 : 1;
    return left.name.toLowerCase().localeCompare(right.name.toLowerCase());
  });
}

/**
 * Merge a gateway listing with the explorer cache.
 *
 * ``keepIfEmpty`` covers the WSL/9p race where scandir still returns [].
 * ``retainNames`` keeps just-written files that a stale non-empty listing
 * omitted, without preserving unrelated rows (no ghost files after delete).
 */
export function mergeFileTreeListing(
  previous: FileTreeEntry[],
  incoming: FileTreeEntry[],
  opts?: { keepIfEmpty?: boolean; retainNames?: Iterable<string> },
): FileTreeEntry[] {
  if (opts?.keepIfEmpty && incoming.length === 0 && previous.length > 0) {
    return previous;
  }
  const retain = new Set(
    [...(opts?.retainNames ?? [])]
      .map((name) => name.trim().toLowerCase())
      .filter(Boolean),
  );
  if (retain.size === 0) return incoming;
  const incomingNames = new Set(incoming.map((entry) => entry.name.toLowerCase()));
  const extras = previous.filter(
    (entry) =>
      retain.has(entry.name.toLowerCase()) && !incomingNames.has(entry.name.toLowerCase()),
  );
  if (extras.length === 0) return incoming;
  return sortFileTreeEntries([...incoming, ...extras]);
}

/** Explorer node for a folder, matching Windows / POSIX / WSL spellings. */
export function lookupTreeNode<T>(
  nodes: Record<string, T>,
  parentKey: string,
  rootPath?: string | null,
): T | undefined {
  if (parentKey === "__root__") return nodes.__root__;
  if (Object.prototype.hasOwnProperty.call(nodes, parentKey)) {
    return nodes[parentKey];
  }
  for (const [key, state] of Object.entries(nodes)) {
    if (key === "__root__") continue;
    if (sameTreeDir(key, parentKey, rootPath)) return state;
  }
  return undefined;
}

/** Keys that hold the same folder as a listing request / response. */
export function treeNodeKeysToUpdate(
  nodes: Record<string, unknown>,
  requested: string | null,
  listedPath: string | null | undefined,
  rootPath?: string | null,
): string[] {
  const keys: string[] = [];
  const add = (key: string) => {
    if (key && !keys.includes(key)) keys.push(key);
  };
  if (requested) add(requested);
  if (listedPath) add(listedPath);
  for (const key of Object.keys(nodes)) {
    if (key === "__root__") continue;
    if (requested && sameTreeDir(key, requested, rootPath)) add(key);
    if (listedPath && sameTreeDir(key, listedPath, rootPath)) add(key);
  }
  return keys;
}

export function sameTreeDir(
  a: string | null | undefined,
  b: string | null | undefined,
  rootPath?: string | null,
): boolean {
  if (!a || !b) return false;
  if (normalizeTreePath(a) === normalizeTreePath(b)) return true;
  if (canonicalTreePath(a) === canonicalTreePath(b)) return true;
  if (!rootPath) return false;
  const left = treeRelativePath(a, rootPath);
  const right = treeRelativePath(b, rootPath);
  return left !== "" && left === right;
}

/** True when a folder row should show as expanded (Windows / POSIX aliases). */
export function treeDirIsExpanded(
  expanded: Set<string>,
  path: string,
  rootPath?: string | null,
): boolean {
  for (const key of expanded) {
    if (key === path || sameTreeDir(key, path, rootPath)) return true;
  }
  return false;
}

export function treeExpandedWithout(
  expanded: Set<string>,
  path: string,
  rootPath?: string | null,
): Set<string> {
  const next = new Set(expanded);
  for (const key of expanded) {
    if (key === path || sameTreeDir(key, path, rootPath)) next.delete(key);
  }
  return next;
}

export function treeExpandedWith(
  expanded: Set<string>,
  path: string,
  knownPaths: Iterable<string>,
  rootPath?: string | null,
): Set<string> {
  const next = treeExpandedWithout(expanded, path, rootPath);
  next.add(resolveTreeNodeKey(path, knownPaths, rootPath));
  return next;
}

/**
 * Absolute file path from a `file_edit` payload. Prefers `absolute_path`
 * (posix); a relative `path` is joined onto the project root.
 */
export function treeFileAbsolutePath(
  edit: { path?: string; absolute_path?: string },
  rootPath: string | null | undefined,
): string {
  const abs = (edit.absolute_path ?? "").trim();
  if (abs) return abs;
  const rel = (edit.path ?? "").trim();
  if (!rel) return "";
  if (isAbsoluteTreePath(rel)) return rel;
  const root = normalizeTreePath(rootPath ?? "");
  if (!root) return rel;
  return `${root}/${rel.replace(/^[\\/]+/, "").replace(/\\/g, "/")}`;
}

/** Explorer node key for a dir, using a native key when known. */
export function resolveTreeNodeKey(
  candidate: string,
  knownPaths: Iterable<string>,
  rootPath?: string | null,
): string {
  const normalized = normalizeTreePath(candidate);
  for (const known of knownPaths) {
    if (normalizeTreePath(known) === normalized) return known;
  }
  const canon = canonicalTreePath(candidate);
  for (const known of knownPaths) {
    if (canonicalTreePath(known) === canon) return known;
  }
  if (rootPath) {
    const rel = treeRelativePath(candidate, rootPath).toLowerCase();
    if (rel) {
      for (const known of knownPaths) {
        if (treeRelativePath(known, rootPath).toLowerCase() === rel) return known;
      }
    }
  }
  return candidate;
}

function joinUnderRoot(
  root: string,
  rel: string,
  known: string[],
): string {
  const existing = resolveTreeNodeKey(
    `${normalizeTreePath(root)}/${rel}`,
    known,
    root,
  );
  if (known.some((path) => path === existing || sameTreeDir(path, existing, root))) {
    return existing;
  }
  const sample = known.find((path) => path.includes("\\") && !path.includes("/")) || root;
  if (sample.includes("\\") && !sample.includes("/")) {
    return `${String(root).replace(/\\+$/, "")}\\${rel.replace(/\//g, "\\")}`;
  }
  return `${normalizeTreePath(root)}/${rel}`;
}

/**
 * Directories the explorer should re-list after an agent write.
 *
 * Always includes the project root (`null`) and the file's parent, plus any
 * already-listed ancestor so a folder left open as "empty" picks up the file.
 * ``displayPath`` is the relative ``file_edit.path`` (workspace spelling).
 */
export function treeDirsToReload(
  filePath: string,
  rootPath: string | null | undefined,
  knownPaths: Iterable<string>,
  displayPath?: string,
): Array<string | null> {
  const file = normalizeTreePath(filePath);
  const root = normalizeTreePath(rootPath ?? "");
  const known = [...knownPaths];
  const dirs: Array<string | null> = [null];
  const seen = new Set<string>(["__root__"]);

  const add = (key: string | null) => {
    const mark = key ?? "__root__";
    if (seen.has(mark)) return;
    seen.add(mark);
    dirs.push(key);
  };

  const relFromAbs = treeRelativePath(file, rootPath);
  const relFromDisplay =
    displayPath && !isAbsoluteTreePath(displayPath.trim())
      ? normalizeTreePath(displayPath.trim()).replace(/^\/+/, "")
      : "";
  const relRaw = relFromAbs || relFromDisplay;
  const rel = relRaw.toLowerCase();
  const parentRel = relRaw.includes("/")
    ? relRaw.slice(0, relRaw.lastIndexOf("/"))
    : "";

  if (parentRel && root) {
    add(joinUnderRoot(rootPath || root, parentRel, known));
  } else if (file) {
    const parent = parentTreePath(file);
    if (parent && parent !== root) add(resolveTreeNodeKey(parent, known, rootPath));
  }

  for (const path of known) {
    if (!path || path === root) continue;
    const knownRel = treeRelativePath(path, rootPath).toLowerCase();
    if (rel && knownRel) {
      if (rel === knownRel || rel.startsWith(`${knownRel}/`)) add(path);
      continue;
    }
    const norm = normalizeTreePath(path);
    if (file === norm || file.startsWith(`${norm}/`) || canonicalTreePath(file).startsWith(`${canonicalTreePath(path)}/`)) {
      add(path);
    }
  }
  return dirs;
}

export function fileEditIsOnDisk(edit: {
  status?: string;
  phase?: string;
  operation?: string;
}): boolean {
  if (edit.operation === "delete") return false;
  return edit.status === "done" || edit.phase === "end";
}

function emptyTreeNode(): TreeNodeLike {
  return { entries: [], loading: false, error: null };
}

/**
 * Show a just-written file in the explorer immediately, even when the folder
 * was listed as empty and the gateway listing is still catching up (WSL/9p).
 * Does not expand collapsed ancestors; the parent row already open stays open.
 */
export function applyOptimisticTreeFile(
  nodes: Record<string, TreeNodeLike>,
  opts: {
    rootPath: string | null | undefined;
    fileAbs: string;
    displayPath?: string;
    deleted?: boolean;
  },
): { nodes: Record<string, TreeNodeLike>; parentKey: string | null } {
  const root = opts.rootPath ?? "";
  const relFromAbs = treeRelativePath(opts.fileAbs, opts.rootPath);
  const relFromDisplay =
    opts.displayPath && !isAbsoluteTreePath(opts.displayPath.trim())
      ? normalizeTreePath(opts.displayPath.trim()).replace(/^\/+/, "")
      : "";
  const rel = relFromAbs || relFromDisplay;
  if (!rel || !root) return { nodes, parentKey: null };

  const parts = rel.split("/").filter(Boolean);
  if (parts.length === 0) return { nodes, parentKey: null };

  const known = Object.keys(nodes).filter((key) => key !== "__root__");
  const next: Record<string, TreeNodeLike> = { ...nodes };
  let fileParent: string | null = null;

  const writeNode = (key: string, entries: FileTreeEntry[]) => {
    const resolved = key === "__root__"
      ? "__root__"
      : resolveTreeNodeKey(key, Object.keys(next), opts.rootPath);
    const previous = lookupTreeNode(next, resolved, opts.rootPath) ?? next[key];
    const node: TreeNodeLike = {
      ...(previous ?? emptyTreeNode()),
      entries: sortFileTreeEntries(entries),
      loading: false,
      error: null,
    };
    next[resolved] = node;
    if (key !== resolved) next[key] = node;
  };

  const upsert = (
    parentKey: string,
    childRel: string,
    childName: string,
    type: "dir" | "file",
  ): string => {
    const existingParent = lookupTreeNode(next, parentKey, opts.rootPath) ?? next[parentKey];
    const resolvedParent =
      parentKey === "__root__"
        ? "__root__"
        : resolveTreeNodeKey(parentKey, Object.keys(next), opts.rootPath);
    const entries = [...(existingParent?.entries ?? [])];
    const idx = entries.findIndex(
      (entry) => entry.name.toLowerCase() === childName.toLowerCase(),
    );
    const childPath =
      idx >= 0 ? entries[idx].path : joinUnderRoot(root, childRel, known);
    if (opts.deleted && type === "file") {
      if (idx >= 0) entries.splice(idx, 1);
    } else if (idx < 0) {
      entries.push({ name: childName, path: childPath, type, size: 0 });
    }
    writeNode(resolvedParent, entries);
    return childPath;
  };

  let parentKey = "__root__";
  let prefix = "";
  for (let i = 0; i < parts.length; i += 1) {
    const name = parts[i];
    const childRel = prefix ? `${prefix}/${name}` : name;
    const isFile = i === parts.length - 1;
    const childPath = upsert(
      parentKey,
      childRel,
      name,
      isFile ? "file" : "dir",
    );
    if (isFile) {
      fileParent = parentKey === "__root__" ? null : parentKey;
    } else {
      parentKey = resolveTreeNodeKey(childPath, [...known, ...Object.keys(next)], opts.rootPath);
      if (!lookupTreeNode(next, parentKey, opts.rootPath)) {
        next[parentKey] = emptyTreeNode();
      }
      prefix = childRel;
    }
  }
  return { nodes: next, parentKey: fileParent };
}

