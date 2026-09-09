// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Client for the Notes module HTTP API (`/api/notes/*`).
 *
 * The gateway's HTTP layer only accepts GET, so note markdown and attachment
 * bytes travel as chunked base64 request headers (same transport as the Code
 * module's file-save).
 */

import { apiBodyHeaders, apiRequest } from "@/lib/api";

export type NotePropValue = string | number | boolean | string[];

export interface NoteSummary {
  id: string;
  path: string;
  folder: string;
  title: string;
  tags: string[];
  aliases: string[];
  created: string;
  updated: string;
  pinned: boolean;
  archived: boolean;
  props: Record<string, NotePropValue>;
  snippet: string;
  tasks_open: number;
  tasks_done: number;
  links: string[];
}

export interface NoteView {
  id: string;
  name: string;
  kind: "table" | "board";
  folder: string;
  tag: string;
  group_by: string;
  columns: string[];
  groups: string[];
  sort: string;
}

export interface BacklinkRef {
  id: string;
  title: string;
  folder: string;
}

export interface NoteBacklinks {
  backlinks: BacklinkRef[];
  unlinked_mentions: BacklinkRef[];
}

export interface NoteDetail {
  note: NoteSummary;
  markdown: string;
  backlinks?: NoteBacklinks;
}

export interface NotesPage {
  notes: NoteSummary[];
  next_cursor: string | null;
  total: number;
}

export interface NoteFolderNode {
  name: string;
  path: string;
  count: number;
  children: NoteFolderNode[];
}

export interface NoteTag {
  tag: string;
  count: number;
}

export interface NoteTaskRow {
  line: number;
  done: boolean;
  text: string;
  due: string | null;
  priority: number | null;
  tags: string[];
  note_id: string;
  note_title: string;
  note_folder: string;
}

export interface NoteTasksPage {
  tasks: NoteTaskRow[];
  next_cursor: string | null;
  total: number;
}

export interface NoteAttachment {
  path: string;
  name: string;
  size: number;
  modified: string;
  referenced_by: string[];
}

export interface NoteAttachmentsPage {
  files: NoteAttachment[];
  next_cursor: string | null;
  total: number;
}

export interface NoteSearchMatch {
  line: number;
  text: string;
  col: number;
}

export interface NoteSearchResult {
  id: string;
  title: string;
  folder: string;
  title_match: boolean;
  matches: NoteSearchMatch[];
  snippet: string;
}

export interface NoteHistorySnapshot {
  stamp: string;
  size: number;
}

export interface NoteHistoryPreview {
  stamp: string;
  title: string;
  markdown: string;
}

export interface NotesImportResult {
  ok: boolean;
  imported: number;
  skipped: number;
  overwritten: number;
  indexed: number;
}

export interface NotesRebuildResult {
  ok: boolean;
  manifest: { total: number };
  search: { total: number; added?: number; updated?: number; removed?: number };
}

export interface TrashedNote {
  id: string;
  title: string;
  trashed_from: string;
  trashed_at: string;
  snippet: string;
}

const NOTES_TIMEOUT_MS = 20_000;

function url(action: string, params: Record<string, string | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, value);
  }
  const encoded = query.toString();
  return `/api/notes/${action}${encoded ? `?${encoded}` : ""}`;
}

export async function listNotes(
  token: string,
  options: {
    folder?: string;
    tag?: string;
    query?: string;
    sort?: string;
    archived?: boolean;
    limit?: number;
    cursor?: string;
  } = {},
): Promise<NotesPage> {
  return apiRequest<NotesPage>(
    url("list", {
      folder: options.folder,
      tag: options.tag,
      q: options.query,
      sort: options.sort,
      archived: options.archived ? "1" : undefined,
      limit: options.limit?.toString(),
      cursor: options.cursor,
    }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function getNote(token: string, id: string): Promise<NoteDetail> {
  return apiRequest<NoteDetail>(url("get", { id }), token, undefined, NOTES_TIMEOUT_MS);
}

export async function createNote(
  token: string,
  options: { title: string; folder?: string; markdown?: string; template?: string },
): Promise<NoteDetail> {
  return apiRequest<NoteDetail>(
    url("create", {
      title: options.title,
      folder: options.folder,
      template: options.template,
    }),
    token,
    options.markdown ? { headers: apiBodyHeaders(options.markdown) } : undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function updateNote(
  token: string,
  id: string,
  options: {
    markdown?: string;
    title?: string;
    tags?: string[];
    pinned?: boolean;
    archived?: boolean;
    folder?: string;
    /** Merge semantics: a null value deletes the property. */
    props?: Record<string, NotePropValue | null>;
    baseUpdated?: string;
  },
): Promise<NoteDetail> {
  return apiRequest<NoteDetail>(
    url("update", {
      id,
      title: options.title,
      tags: options.tags ? JSON.stringify(options.tags) : undefined,
      pinned: options.pinned === undefined ? undefined : options.pinned ? "1" : "0",
      archived:
        options.archived === undefined ? undefined : options.archived ? "1" : "0",
      folder: options.folder,
      props: options.props ? JSON.stringify(options.props) : undefined,
      base_updated: options.baseUpdated,
    }),
    token,
    options.markdown === undefined
      ? undefined
      : { headers: apiBodyHeaders(options.markdown) },
    NOTES_TIMEOUT_MS,
  );
}

export async function deleteNote(token: string, id: string): Promise<{ ok: boolean }> {
  return apiRequest(url("delete", { id }), token, undefined, NOTES_TIMEOUT_MS);
}

export async function restoreNote(token: string, id: string): Promise<NoteDetail> {
  return apiRequest(url("restore", { id }), token, undefined, NOTES_TIMEOUT_MS);
}

export async function listTrash(token: string): Promise<{ notes: TrashedNote[] }> {
  return apiRequest(url("trash", {}), token, undefined, NOTES_TIMEOUT_MS);
}

/** Permanently delete one trashed note (file + edit history). */
export async function purgeNote(
  token: string,
  id: string,
): Promise<{ ok: boolean; id: string }> {
  return apiRequest(url("purge", { id }), token, undefined, NOTES_TIMEOUT_MS);
}

/** Permanently delete every trashed note. */
export async function emptyNotesTrash(
  token: string,
): Promise<{ ok: boolean; purged: number }> {
  return apiRequest(url("trash/empty", {}), token, undefined, NOTES_TIMEOUT_MS);
}

export async function listNoteFolders(
  token: string,
): Promise<{ folders: NoteFolderNode[] }> {
  return apiRequest(url("folders", {}), token, undefined, NOTES_TIMEOUT_MS);
}

export async function createNoteFolder(
  token: string,
  path: string,
): Promise<{ ok: boolean; path: string }> {
  return apiRequest(url("folders/create", { path }), token, undefined, NOTES_TIMEOUT_MS);
}

export async function renameNoteFolder(
  token: string,
  path: string,
  name: string,
): Promise<{ ok: boolean; path: string }> {
  return apiRequest(
    url("folders/rename", { path, name }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function deleteNoteFolder(
  token: string,
  path: string,
  options: { force?: boolean } = {},
): Promise<{ ok: boolean }> {
  return apiRequest(
    url("folders/delete", { path, force: options.force ? "1" : undefined }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function listNoteTags(token: string): Promise<{ tags: NoteTag[] }> {
  return apiRequest(url("tags", {}), token, undefined, NOTES_TIMEOUT_MS);
}

export async function listNoteTasks(
  token: string,
  options: { state?: "open" | "done" | "all"; folder?: string; limit?: number; cursor?: string } = {},
): Promise<NoteTasksPage> {
  return apiRequest(
    url("tasks", {
      state: options.state,
      folder: options.folder,
      limit: options.limit?.toString(),
      cursor: options.cursor,
    }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function addNoteTask(
  token: string,
  text: string,
  options: { noteId?: string; inboxTitle?: string } = {},
): Promise<{
  ok: boolean;
  note_id: string;
  note_title: string;
  line: number;
  text: string;
}> {
  return apiRequest(
    url("tasks/add", {
      text,
      id: options.noteId,
      inbox_title: options.inboxTitle,
    }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function toggleNoteTask(
  token: string,
  noteId: string,
  line: number,
  done: boolean,
): Promise<{ ok: boolean; line: number; done: boolean; updated?: string }> {
  return apiRequest(
    url("tasks/toggle", { id: noteId, line: String(line), done: done ? "1" : "0" }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export interface NotesGraphNode {
  id: string;
  kind: "note" | "tag";
  title: string;
  folder: string;
  tags: string[];
  pinned: boolean;
  count?: number;
}

export interface NotesGraphEdge {
  source: string;
  target: string;
  kind: "link" | "tag";
}

export interface NotesGraph {
  nodes: NotesGraphNode[];
  edges: NotesGraphEdge[];
}

export async function getNotesGraph(token: string): Promise<NotesGraph> {
  return apiRequest(url("graph", {}), token, undefined, NOTES_TIMEOUT_MS);
}

export async function getNoteBacklinks(
  token: string,
  id: string,
): Promise<NoteBacklinks> {
  return apiRequest(url("backlinks", { id }), token, undefined, NOTES_TIMEOUT_MS);
}

export async function searchNotes(
  token: string,
  query: string,
  limit?: number,
): Promise<{ results: NoteSearchResult[] }> {
  return apiRequest(
    url("search", { q: query, limit: limit?.toString() }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function listNoteHistory(
  token: string,
  id: string,
): Promise<{ snapshots: NoteHistorySnapshot[] }> {
  return apiRequest(url("history", { id }), token, undefined, NOTES_TIMEOUT_MS);
}

export async function getNoteHistorySnapshot(
  token: string,
  id: string,
  stamp: string,
): Promise<NoteHistoryPreview> {
  return apiRequest(
    url("history/get", { id, stamp }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function restoreNoteHistorySnapshot(
  token: string,
  note: NoteDetail,
  snapshot: NoteHistoryPreview,
): Promise<NoteDetail> {
  return updateNote(token, note.note.id, {
    markdown: snapshot.markdown,
    title: snapshot.title || note.note.title,
    baseUpdated: note.note.updated || undefined,
  });
}

export async function importNotesVault(
  token: string,
  path: string,
  conflict: "rename" | "skip" | "overwrite",
): Promise<NotesImportResult> {
  return apiRequest(
    url("import", { path, conflict }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function rebuildNotesIndex(token: string): Promise<NotesRebuildResult> {
  return apiRequest(url("rebuild", {}), token, undefined, NOTES_TIMEOUT_MS);
}

export async function listNoteAttachments(
  token: string,
  options: { limit?: number; cursor?: string } = {},
): Promise<NoteAttachmentsPage> {
  return apiRequest(
    url("attachments", { limit: options.limit?.toString(), cursor: options.cursor }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function uploadNoteAttachment(
  token: string,
  name: string,
  base64Data: string,
): Promise<{ path: string; name: string; size: number }> {
  return apiRequest(
    url("attachments/upload", { name }),
    token,
    { headers: apiBodyHeaders(base64Data) },
    NOTES_TIMEOUT_MS,
  );
}

export interface NotePassage {
  note_id: string;
  title: string;
  folder: string;
  heading: string;
  line: number;
  text: string;
  score: number;
}

export interface NotesAskResult {
  passages: NotePassage[];
  semantic: boolean;
  syncing: boolean;
}

/** Best passages across all notes (semantic memory, lexical fallback). */
export async function askNotes(
  token: string,
  query: string,
  limit?: number,
): Promise<NotesAskResult> {
  return apiRequest(
    url("ask", { q: query, limit: limit?.toString() }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function listNoteViews(token: string): Promise<{ views: NoteView[] }> {
  return apiRequest(url("views", {}), token, undefined, NOTES_TIMEOUT_MS);
}

export async function saveNoteView(
  token: string,
  view: Partial<NoteView> & { name: string; kind: "table" | "board" },
): Promise<{ view: NoteView }> {
  return apiRequest(
    url("views/save", { view: JSON.stringify(view) }),
    token,
    undefined,
    NOTES_TIMEOUT_MS,
  );
}

export async function deleteNoteView(
  token: string,
  id: string,
): Promise<{ ok: boolean }> {
  return apiRequest(url("views/delete", { id }), token, undefined, NOTES_TIMEOUT_MS);
}

/** URL usable directly in an <img> / link: auth travels as a query token. */
export function noteAttachmentUrl(token: string, path: string): string {
  return url("file", { path, token });
}
