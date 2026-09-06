/**
 * Notion-style database views over notes: saved views (Table / Board) whose
 * rows are ordinary notes and whose cells are frontmatter properties.
 *
 * Views never own notes; they are saved filters + presentation. Editing a cell
 * or moving a Kanban card writes the property back into the note frontmatter.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Columns3,
  Loader2,
  Plus,
  Table2,
  Trash2,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { propToText, textToProp } from "@/components/notes/database-props";
import {
  createNote,
  deleteNoteView,
  listNotes,
  listNoteViews,
  saveNoteView,
  updateNote,
  type NotePropValue,
  type NoteSummary,
  type NoteView,
} from "@/lib/notes-api";
import { cn } from "@/lib/utils";

const UNGROUPED = "__none__";

export interface DatabasePaneProps {
  token: string;
  onOpenNote: (id: string) => void;
}

export function DatabasePane({ token, onOpenNote }: DatabasePaneProps) {
  const { t } = useTranslation();
  const [views, setViews] = useState<NoteView[]>([]);
  const [activeViewId, setActiveViewId] = useState<string | null>(null);
  const [notes, setNotes] = useState<NoteSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [viewDraft, setViewDraft] = useState<{ name: string; kind: "table" | "board" } | null>(null);
  const [confirmDeleteView, setConfirmDeleteView] = useState(false);

  const activeView = useMemo(
    () => views.find((view) => view.id === activeViewId) ?? views[0] ?? null,
    [activeViewId, views],
  );

  const refreshViews = useCallback(async () => {
    const payload = await listNoteViews(token);
    setViews(payload.views);
    return payload.views;
  }, [token]);

  const refreshNotes = useCallback(async () => {
    if (!activeView) {
      setNotes([]);
      return;
    }
    setLoading(true);
    try {
      const page = await listNotes(token, {
        folder: activeView.folder || undefined,
        tag: activeView.tag || undefined,
        sort: activeView.sort,
        limit: 200,
      });
      setNotes(page.notes);
    } finally {
      setLoading(false);
    }
  }, [activeView, token]);

  useEffect(() => {
    void refreshViews().finally(() => setLoading(false));
  }, [refreshViews]);

  useEffect(() => {
    void refreshNotes();
  }, [refreshNotes]);

  const onCreateView = useCallback(async () => {
    const draft = viewDraft;
    setViewDraft(null);
    if (!draft || !draft.name.trim()) return;
    const saved = await saveNoteView(token, {
      name: draft.name.trim(),
      kind: draft.kind,
      group_by: "status",
      columns: draft.kind === "table" ? ["status", "priority", "due"] : [],
    });
    await refreshViews();
    setActiveViewId(saved.view.id);
  }, [refreshViews, token, viewDraft]);

  const onDeleteView = useCallback(async () => {
    if (!activeView) return;
    setConfirmDeleteView(false);
    await deleteNoteView(token, activeView.id);
    const remaining = await refreshViews();
    setActiveViewId(remaining[0]?.id ?? null);
  }, [activeView, refreshViews, token]);

  const setNoteProp = useCallback(
    async (noteId: string, key: string, value: NotePropValue | null) => {
      const saved = await updateNote(token, noteId, { props: { [key]: value } });
      setNotes((current) =>
        current.map((note) => (note.id === saved.note.id ? saved.note : note)),
      );
    },
    [token],
  );

  const onAddRow = useCallback(
    async (groupValue?: string) => {
      if (!activeView) return;
      const created = await createNote(token, {
        title: t("notes.untitled", { defaultValue: "Untitled" }),
        folder: activeView.folder || "",
      });
      if (activeView.tag) {
        await updateNote(token, created.note.id, { tags: [activeView.tag] });
      }
      if (groupValue && groupValue !== UNGROUPED) {
        await updateNote(token, created.note.id, {
          props: { [activeView.group_by]: groupValue },
        });
      }
      await refreshNotes();
      onOpenNote(created.note.id);
    },
    [activeView, onOpenNote, refreshNotes, t, token],
  );

  if (loading && views.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* View tabs */}
      <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border/60 px-4 py-1.5">
        {views.map((view) => (
          <button
            key={view.id}
            type="button"
            onClick={() => setActiveViewId(view.id)}
            className={cn(
              "flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[12px] font-medium transition-colors",
              activeView?.id === view.id
                ? "bg-muted/80 text-foreground"
                : "text-muted-foreground hover:bg-muted/40 hover:text-foreground",
            )}
          >
            {view.kind === "board" ? (
              <Columns3 className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <Table2 className="h-3.5 w-3.5" aria-hidden />
            )}
            {view.name}
          </button>
        ))}
        <AnimatePresence initial={false}>
          {viewDraft !== null ? (
            <motion.div
              key="view-draft"
              initial={{ opacity: 0, width: 0 }}
              animate={{ opacity: 1, width: "auto" }}
              exit={{ opacity: 0, width: 0 }}
              transition={{ duration: 0.15, ease: "easeOut" }}
              className="flex shrink-0 items-center gap-1 overflow-hidden"
            >
              <div className="flex h-7 items-center gap-1 rounded-md border border-primary/40 bg-background px-1.5">
                <button
                  type="button"
                  onMouseDown={(event) => {
                    event.preventDefault();
                    setViewDraft((current) =>
                      current
                        ? { ...current, kind: current.kind === "table" ? "board" : "table" }
                        : current,
                    );
                  }}
                  className="rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
                  aria-label={t("notes.database.toggleKind", {
                    defaultValue: "Toggle view type",
                  })}
                  title={t("notes.database.toggleKind", {
                    defaultValue: "Toggle view type",
                  })}
                >
                  {viewDraft.kind === "board" ? (
                    <Columns3 className="h-3.5 w-3.5" aria-hidden />
                  ) : (
                    <Table2 className="h-3.5 w-3.5" aria-hidden />
                  )}
                </button>
                <input
                  autoFocus
                  value={viewDraft.name}
                  onChange={(event) =>
                    setViewDraft((current) =>
                      current ? { ...current, name: event.target.value } : current,
                    )
                  }
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void onCreateView();
                    } else if (event.key === "Escape") {
                      setViewDraft(null);
                    }
                  }}
                  onBlur={() => {
                    if (!viewDraft.name.trim()) setViewDraft(null);
                  }}
                  placeholder={t("notes.database.viewName", {
                    defaultValue: "View name",
                  })}
                  className="h-6 w-32 bg-transparent text-[12px] outline-none placeholder:text-muted-foreground/60"
                />
              </div>
            </motion.div>
          ) : null}
        </AnimatePresence>
        <button
          type="button"
          onClick={() =>
            setViewDraft((current) =>
              current === null ? { name: "", kind: "table" } : null,
            )
          }
          className="flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[12px] text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {t("notes.database.newView", { defaultValue: "New view" })}
        </button>
        {activeView ? (
          <div className="ml-auto flex shrink-0 items-center gap-1">
            {confirmDeleteView ? (
              <button
                type="button"
                onClick={() => void onDeleteView()}
                onBlur={() => setConfirmDeleteView(false)}
                className="flex h-7 items-center gap-1 rounded-md bg-red-500/15 px-2 text-[11.5px] font-medium text-red-600 transition-colors hover:bg-red-500/25 dark:text-red-400"
              >
                {t("notes.confirmDelete", { defaultValue: "Confirm?" })}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmDeleteView(true)}
                className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                aria-label={t("notes.database.deleteView", {
                  defaultValue: "Delete view",
                })}
                title={t("notes.database.deleteView", {
                  defaultValue: "Delete view",
                })}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              </button>
            )}
          </div>
        ) : null}
      </div>

      {!activeView ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
          <Table2 className="h-6 w-6 text-muted-foreground/60" aria-hidden />
          <p className="max-w-sm text-[12.5px] text-muted-foreground">
            {t("notes.database.empty", {
              defaultValue:
                "Turn your notes into a database: create a Table or Board view. Rows are notes, columns are properties saved in the note itself.",
            })}
          </p>
          <button
            type="button"
            onClick={() => setViewDraft({ name: "", kind: "table" })}
            className="mt-1 rounded-lg border border-border px-2.5 py-1.5 text-[12px] font-medium transition-colors hover:bg-muted/60"
          >
            {t("notes.database.newView", { defaultValue: "New view" })}
          </button>
        </div>
      ) : activeView.kind === "board" ? (
        <BoardView
          view={activeView}
          notes={notes}
          loading={loading}
          onOpenNote={onOpenNote}
          onSetProp={setNoteProp}
          onAddCard={(group) => void onAddRow(group)}
        />
      ) : (
        <TableView
          view={activeView}
          notes={notes}
          loading={loading}
          onOpenNote={onOpenNote}
          onSetProp={setNoteProp}
          onAddRow={() => void onAddRow()}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Table

function TableView({
  view,
  notes,
  loading,
  onOpenNote,
  onSetProp,
  onAddRow,
}: {
  view: NoteView;
  notes: NoteSummary[];
  loading: boolean;
  onOpenNote: (id: string) => void;
  onSetProp: (noteId: string, key: string, value: NotePropValue | null) => Promise<void>;
  onAddRow: () => void;
}) {
  const { t } = useTranslation();
  const columns = useMemo(() => {
    if (view.columns.length > 0) return view.columns;
    const keys: string[] = [];
    for (const note of notes) {
      for (const key of Object.keys(note.props)) {
        if (!keys.includes(key)) keys.push(key);
        if (keys.length >= 6) return keys;
      }
    }
    return keys.length > 0 ? keys : ["status", "priority"];
  }, [notes, view.columns]);

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="w-full border-collapse text-[12.5px]">
        <thead className="sticky top-0 z-10 bg-background">
          <tr className="border-b border-border/60 text-left">
            <th className="min-w-56 px-4 py-2 font-semibold text-muted-foreground">
              {t("notes.database.titleColumn", { defaultValue: "Title" })}
            </th>
            {columns.map((column) => (
              <th
                key={column}
                className="min-w-32 px-3 py-2 font-semibold capitalize text-muted-foreground"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {notes.map((note) => (
            <tr
              key={note.id}
              className="group border-b border-border/40 transition-colors hover:bg-muted/30"
            >
              <td className="px-4 py-1.5">
                <button
                  type="button"
                  onClick={() => onOpenNote(note.id)}
                  className="max-w-full truncate text-left font-medium hover:underline"
                >
                  {note.title}
                </button>
              </td>
              {columns.map((column) => (
                <PropCell
                  key={column}
                  value={note.props[column]}
                  onCommit={(value) => void onSetProp(note.id, column, value)}
                />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {loading && notes.length === 0 ? (
        <div className="flex items-center justify-center py-10 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        </div>
      ) : null}
      <button
        type="button"
        onClick={onAddRow}
        className="m-2 flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12px] text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
      >
        <Plus className="h-3.5 w-3.5" aria-hidden />
        {t("notes.database.addRow", { defaultValue: "New row" })}
      </button>
    </div>
  );
}

function PropCell({
  value,
  onCommit,
}: {
  value: NotePropValue | undefined;
  onCommit: (value: NotePropValue | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const initial = propToText(value);

  const commit = () => {
    setEditing(false);
    if (draft === initial) return;
    onCommit(textToProp(draft));
  };

  return (
    <td className="px-3 py-1.5">
      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commit();
            } else if (event.key === "Escape") {
              setEditing(false);
            }
          }}
          className="h-6 w-full min-w-24 rounded border border-primary/40 bg-background px-1.5 text-[12px] outline-none"
        />
      ) : (
        <button
          type="button"
          onClick={() => {
            setDraft(initial);
            setEditing(true);
          }}
          className={cn(
            "block h-6 w-full min-w-24 truncate rounded px-1.5 text-left transition-colors hover:bg-muted/50",
            initial ? "" : "text-muted-foreground/40",
          )}
        >
          {initial || "…"}
        </button>
      )}
    </td>
  );
}

// ---------------------------------------------------------------------------
// Board (Kanban)

function BoardView({
  view,
  notes,
  loading,
  onOpenNote,
  onSetProp,
  onAddCard,
}: {
  view: NoteView;
  notes: NoteSummary[];
  loading: boolean;
  onOpenNote: (id: string) => void;
  onSetProp: (noteId: string, key: string, value: NotePropValue | null) => Promise<void>;
  onAddCard: (group: string) => void;
}) {
  const { t } = useTranslation();
  const [dragOver, setDragOver] = useState<string | null>(null);
  const draggingRef = useRef<string | null>(null);
  const [groupDraft, setGroupDraft] = useState<string | null>(null);
  const [extraGroups, setExtraGroups] = useState<string[]>([]);

  const groups = useMemo(() => {
    const ordered: string[] = [];
    for (const group of view.groups) {
      if (!ordered.includes(group)) ordered.push(group);
    }
    for (const note of notes) {
      const raw = note.props[view.group_by];
      const label = propToText(raw);
      if (label && !ordered.includes(label)) ordered.push(label);
    }
    for (const group of extraGroups) {
      if (!ordered.includes(group)) ordered.push(group);
    }
    return ordered;
  }, [extraGroups, notes, view.group_by, view.groups]);

  const byGroup = useMemo(() => {
    const map = new Map<string, NoteSummary[]>();
    map.set(UNGROUPED, []);
    for (const group of groups) map.set(group, []);
    for (const note of notes) {
      const label = propToText(note.props[view.group_by]) || UNGROUPED;
      if (!map.has(label)) map.set(label, []);
      map.get(label)!.push(note);
    }
    return map;
  }, [groups, notes, view.group_by]);

  const dropOn = (group: string) => {
    const noteId = draggingRef.current;
    draggingRef.current = null;
    setDragOver(null);
    if (!noteId) return;
    void onSetProp(noteId, view.group_by, group === UNGROUPED ? null : group);
  };

  const columnOrder = [UNGROUPED, ...groups].filter(
    (group) => group !== UNGROUPED || (byGroup.get(UNGROUPED)?.length ?? 0) > 0,
  );

  return (
    <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden">
      {loading && notes.length === 0 ? (
        <div className="flex h-full items-center justify-center text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        </div>
      ) : (
        <div className="flex h-full min-w-max gap-3 p-4">
          {columnOrder.map((group) => (
            <div
              key={group}
              onDragOver={(event) => {
                event.preventDefault();
                setDragOver(group);
              }}
              onDragLeave={() => setDragOver((current) => (current === group ? null : current))}
              onDrop={(event) => {
                event.preventDefault();
                dropOn(group);
              }}
              className={cn(
                "flex h-full w-64 shrink-0 flex-col rounded-xl border bg-muted/20 transition-colors",
                dragOver === group ? "border-primary/50 bg-primary/5" : "border-border/50",
              )}
            >
              <div className="flex shrink-0 items-center justify-between px-3 py-2">
                <span className="text-[11.5px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {group === UNGROUPED
                    ? t("notes.database.noGroup", { defaultValue: "No status" })
                    : group}
                </span>
                <span className="text-[11px] tabular-nums text-muted-foreground/70">
                  {byGroup.get(group)?.length ?? 0}
                </span>
              </div>
              <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto px-2 pb-2">
                <AnimatePresence initial={false}>
                  {(byGroup.get(group) ?? []).map((note) => (
                    <motion.div
                      key={note.id}
                      layout
                      initial={{ opacity: 0, scale: 0.96 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0, scale: 0.96 }}
                      transition={{ duration: 0.15 }}
                      draggable
                      onDragStart={() => {
                        draggingRef.current = note.id;
                      }}
                      onDragEnd={() => {
                        draggingRef.current = null;
                        setDragOver(null);
                      }}
                      onClick={() => onOpenNote(note.id)}
                      className="cursor-grab rounded-lg border border-border/60 bg-background px-2.5 py-2 shadow-sm transition-shadow hover:shadow active:cursor-grabbing"
                    >
                      <span className="line-clamp-2 text-[12.5px] font-medium">
                        {note.title}
                      </span>
                      {note.snippet ? (
                        <span className="mt-0.5 line-clamp-2 block text-[11px] leading-snug text-muted-foreground">
                          {note.snippet}
                        </span>
                      ) : null}
                      {note.tags.length > 0 ? (
                        <span className="mt-1 flex flex-wrap gap-1">
                          {note.tags.slice(0, 3).map((tag) => (
                            <span
                              key={tag}
                              className="rounded-full bg-muted/70 px-1.5 py-px text-[10px] text-muted-foreground"
                            >
                              #{tag}
                            </span>
                          ))}
                        </span>
                      ) : null}
                    </motion.div>
                  ))}
                </AnimatePresence>
                <button
                  type="button"
                  onClick={() => onAddCard(group)}
                  className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-[11.5px] text-muted-foreground/70 transition-colors hover:bg-muted/50 hover:text-foreground"
                >
                  <Plus className="h-3 w-3" aria-hidden />
                  {t("notes.database.addCard", { defaultValue: "New card" })}
                </button>
              </div>
            </div>
          ))}

          {/* New column */}
          <div className="w-56 shrink-0">
            {groupDraft !== null ? (
              <div className="flex h-9 items-center gap-1.5 rounded-xl border border-primary/40 bg-background px-2.5">
                <input
                  autoFocus
                  value={groupDraft}
                  onChange={(event) => setGroupDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      const name = groupDraft.trim();
                      setGroupDraft(null);
                      if (name) setExtraGroups((current) => [...current, name]);
                    } else if (event.key === "Escape") {
                      setGroupDraft(null);
                    }
                  }}
                  onBlur={() => {
                    if (!groupDraft.trim()) setGroupDraft(null);
                  }}
                  placeholder={t("notes.database.groupName", {
                    defaultValue: "Column name",
                  })}
                  className="h-6 min-w-0 flex-1 bg-transparent text-[12px] outline-none placeholder:text-muted-foreground/60"
                />
                <button
                  type="button"
                  onMouseDown={(event) => {
                    event.preventDefault();
                    setGroupDraft(null);
                  }}
                  className="rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
                  aria-label={t("notes.database.cancel", { defaultValue: "Cancel" })}
                >
                  <X className="h-3 w-3" aria-hidden />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setGroupDraft("")}
                className="flex h-9 w-full items-center gap-1.5 rounded-xl border border-dashed border-border/60 px-3 text-[12px] text-muted-foreground transition-colors hover:border-border hover:text-foreground"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden />
                {t("notes.database.addGroup", { defaultValue: "New column" })}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
