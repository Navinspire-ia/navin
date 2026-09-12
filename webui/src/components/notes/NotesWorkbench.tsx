// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Notes workbench: Notion-style knowledge space backed by markdown files.
 *
 * Three columns: sections rail (folders, tags, tasks, files, trash), the note
 * list for the active filter, and the Tiptap editor with autosave. The chat
 * stays on the right (App shell), and "Ask Navin" seeds it with the note.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  IconButton,
  PrimaryButton,
  type IButtonStyles,
} from "@fluentui/react";
import "@/lib/fluent-icons";
import {
  Archive,
  ArchiveRestore,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  CornerDownLeft,
  Database,
  Download,
  FileText,
  Folder,
  FolderPlus,
  FolderInput,
  Hash,
  History,
  Languages,
  ListTree,
  Loader2,
  Notebook,
  Paperclip,
  Pin,
  PinOff,
  Plus,
  RotateCcw,
  Search,
  Sparkles,
  SpellCheck2,
  SquareSlash,
  StickyNote,
  Trash2,
  Waypoints,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { buildAgentRunPrompt } from "@/components/notes/agent-block";
import {
  buildNoteAskActionPrompt,
  buildNoteAskSeed,
  type NoteAskAction,
} from "@/components/notes/ask-actions";
import {
  exportNote,
  type NoteExportFormat,
} from "@/components/notes/note-export";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AttachmentPreview,
  type AttachmentPreviewTarget,
} from "@/components/notes/AttachmentPreview";
import { DatabasePane } from "@/components/notes/DatabasePane";
import { GraphPane } from "@/components/notes/GraphPane";
import { GuidePane } from "@/components/notes/GuidePane";
import {
  AskNotesPane,
  NoteHistoryPanel,
  NotesSearchResults,
  VaultToolsPane,
} from "@/components/notes/NotesAdvancedPanes";
import {
  NoteEditor,
  type NoteEditorHandle,
} from "@/components/notes/NoteEditor";
import {
  addNoteTask,
  createNote,
  createNoteFolder,
  deleteNote,
  deleteNoteFolder,
  emptyNotesTrash,
  getNote,
  listNoteAttachments,
  listNoteFolders,
  listNotes,
  listNoteTags,
  listNoteTasks,
  listTrash,
  purgeNote,
  restoreNote,
  searchNotes,
  toggleNoteTask,
  updateNote,
  uploadNoteAttachment,
  type NoteAttachment,
  type NoteDetail,
  type NoteFolderNode,
  type NoteSummary,
  type NoteSearchResult,
  type NoteTag,
  type NoteTaskRow,
  type TrashedNote,
} from "@/lib/notes-api";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

type Section =
  | { kind: "notes"; folder?: string; tag?: string; archived?: boolean }
  | { kind: "database" }
  | { kind: "graph" }
  | { kind: "tasks" }
  | { kind: "files" }
  | { kind: "ask" }
  | { kind: "vault" }
  | { kind: "trash" }
  | { kind: "guide" };

type SaveState = "idle" | "dirty" | "saving" | "saved" | "conflict" | "error";

/** Destructive actions gated behind the in-app confirmation dialog. */
type DangerAction =
  | { kind: "purge"; id: string; title: string }
  | { kind: "emptyTrash"; count: number }
  | { kind: "deleteFolder"; path: string; count: number };

const AUTOSAVE_DELAY_MS = 800;

const HEADER_BUTTON_STYLES: IButtonStyles = {
  root: { minHeight: 40, minWidth: "auto", padding: "0 12px", cursor: "pointer", flexShrink: 0 },
  label: { whiteSpace: "nowrap", overflow: "visible" },
};
const ICON_BUTTON_STYLES: IButtonStyles = {
  root: {
    height: 40,
    minHeight: 40,
    width: 40,
    minWidth: 40,
    padding: 0,
    cursor: "pointer",
    flexShrink: 0,
  },
  flexContainer: { justifyContent: "center" },
  icon: { margin: 0 },
  menuIcon: { display: "none" },
};

export interface NotesWorkbenchProps {
  chatOpen?: boolean;
  onToggleChat?: () => void;
  onSeed?: (text: string) => void;
  /** Auto-send a prompt to the chat (used by Résumé / Traduction / Correction). */
  onRun?: (text: string) => void;
  /**
   * Note to open when the desk mounts or when this changes (deep link from
   * another module, e.g. the Meetings desk after "Save to Notes"). The
   * `nonce` lets the same id be requested twice in a row.
   */
  openRequest?: { id: string; nonce: number } | null;
}

export function NotesWorkbench({
  chatOpen = false,
  onToggleChat,
  onSeed,
  onRun,
  openRequest = null,
}: NotesWorkbenchProps) {
  const { t, i18n } = useTranslation();
  const { token } = useClient();

  const [section, setSection] = useState<Section>({ kind: "notes" });
  const [search, setSearch] = useState("");
  const [notes, setNotes] = useState<NoteSummary[]>([]);
  const [searchResults, setSearchResults] = useState<NoteSearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [folders, setFolders] = useState<NoteFolderNode[]>([]);
  const [tags, setTags] = useState<NoteTag[]>([]);
  const [tasks, setTasks] = useState<NoteTaskRow[]>([]);
  const [attachments, setAttachments] = useState<NoteAttachment[]>([]);
  const [trash, setTrash] = useState<TrashedNote[]>([]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<NoteDetail | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [tagDraft, setTagDraft] = useState("");
  // null = closed; string = the name being typed in the inline rail input.
  // Never window.prompt: native dialogs are unavailable in packaged builds.
  const [folderDraft, setFolderDraft] = useState<string | null>(null);
  const [danger, setDanger] = useState<DangerAction | null>(null);
  // Attachments preview in-product: never a raw URL in a browser tab.
  const [preview, setPreview] = useState<AttachmentPreviewTarget | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const editorRef = useRef<NoteEditorHandle | null>(null);
  const draftRef = useRef<{ markdown: string | null; title: string | null }>({
    markdown: null,
    title: null,
  });
  const saveTimerRef = useRef<number | null>(null);
  const detailRef = useRef<NoteDetail | null>(null);
  detailRef.current = detail;
  // Saves run one after the other. Two overlapping updateNote calls both
  // carried the same base_updated token, so the second one came back 409 and
  // the badge said "changed elsewhere" while nothing had. The chain also lets
  // a flush target the note it was typed in even after the user moved on.
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  // note id -> the `updated` token the server returned last. React state lags
  // a render behind, and pin/tag/archive toggles also bump the token, so the
  // conflict check reads from here instead of from `detail`.
  const lastUpdatedRef = useRef<Map<string, string>>(new Map());
  // Monotonic id of the latest openNote call; late responses are dropped.
  const openRequestRef = useRef(0);

  const rememberSaved = useCallback((saved: NoteDetail) => {
    if (saved.note.updated) lastUpdatedRef.current.set(saved.note.id, saved.note.updated);
    setDetail((previous) =>
      previous && previous.note.id === saved.note.id
        ? {
            ...previous,
            // A title still being typed must not snap back to the saved one.
            note: { ...saved.note, title: draftRef.current.title ?? saved.note.title },
            markdown: saved.markdown,
          }
        : previous,
    );
    setNotes((currentNotes) =>
      currentNotes.map((note) => (note.id === saved.note.id ? saved.note : note)),
    );
  }, []);

  const refreshSidebarData = useCallback(async () => {
    if (!token) return;
    try {
      const [folderData, tagData] = await Promise.all([
        listNoteFolders(token),
        listNoteTags(token),
      ]);
      setFolders(folderData.folders);
      setTags(tagData.tags);
    } catch {
      // Sidebar counters are cosmetic; the list request surfaces real errors.
    }
  }, [token]);

  const refreshList = useCallback(async () => {
    if (!token) return;
    setListLoading(true);
    try {
      if (section.kind === "notes") {
        const page = await listNotes(token, {
          folder: section.folder,
          tag: section.tag,
          archived: section.archived,
          limit: 50,
        });
        setNotes(page.notes);
        setNextCursor(page.next_cursor);
      } else if (section.kind === "tasks") {
        const page = await listNoteTasks(token, { state: "open", limit: 200 });
        setTasks(page.tasks);
      } else if (section.kind === "files") {
        const page = await listNoteAttachments(token, { limit: 200 });
        setAttachments(page.files);
      } else if (section.kind === "trash") {
        const page = await listTrash(token);
        setTrash(page.notes);
      }
      // "database" loads its own data inside DatabasePane.
    } catch {
      if (section.kind === "notes") setNotes([]);
    } finally {
      setListLoading(false);
    }
  }, [section, token]);

  useEffect(() => {
    const query = search.trim();
    if (!token || !query) {
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }
    setSearchLoading(true);
    const timer = window.setTimeout(() => {
      void searchNotes(token, query, 30)
        .then((data) => setSearchResults(data.results))
        .catch(() => setSearchResults([]))
        .finally(() => setSearchLoading(false));
    }, 180);
    return () => window.clearTimeout(timer);
  }, [search, token]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  useEffect(() => {
    void refreshSidebarData();
  }, [refreshSidebarData]);

  const loadMore = useCallback(async () => {
    if (!token || !nextCursor || section.kind !== "notes") return;
    const page = await listNotes(token, {
      folder: section.folder,
      tag: section.tag,
      archived: section.archived,
      query: search || undefined,
      limit: 50,
      cursor: nextCursor,
    });
    setNotes((current) => [...current, ...page.notes]);
    setNextCursor(page.next_cursor);
  }, [nextCursor, search, section, token]);

  const cancelSaveTimer = useCallback(() => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
  }, []);

  const flushSave = useCallback((): Promise<void> => {
    const current = detailRef.current;
    const draft = draftRef.current;
    if (!token || !current) return Promise.resolve();
    if (draft.markdown === null && draft.title === null) return Promise.resolve();
    // Snapshot and clear the draft right away: whatever the user types from
    // here on belongs to the next save, and switching notes must not lose it.
    const noteId = current.note.id;
    const payload = {
      markdown: draft.markdown ?? undefined,
      title: draft.title ?? undefined,
    };
    draftRef.current = { markdown: null, title: null };
    cancelSaveTimer();
    setSaveState("saving");
    const run = saveChainRef.current.then(async () => {
      const base = lastUpdatedRef.current.get(noteId) ?? current.note.updated ?? "";
      try {
        const saved = await updateNote(token, noteId, {
          ...payload,
          baseUpdated: base || undefined,
        });
        rememberSaved(saved);
        if (detailRef.current?.note.id === noteId) {
          const pending = draftRef.current;
          setSaveState(pending.markdown !== null || pending.title !== null ? "dirty" : "saved");
        }
      } catch (error) {
        const conflict = error instanceof ApiError && error.status === 409;
        if (detailRef.current?.note.id === noteId) {
          // Put the unsaved text back so Ctrl+S or the next keystroke retries
          // it instead of silently dropping it. A conflict keeps it too: the
          // user decides between reload and export.
          if (draftRef.current.markdown === null && payload.markdown !== undefined) {
            draftRef.current.markdown = payload.markdown;
          }
          if (draftRef.current.title === null && payload.title !== undefined) {
            draftRef.current.title = payload.title;
          }
          setSaveState(conflict ? "conflict" : "error");
        }
      }
    });
    saveChainRef.current = run.catch(() => undefined);
    return run;
  }, [cancelSaveTimer, rememberSaved, token]);

  const scheduleSave = useCallback(() => {
    setSaveState("dirty");
    cancelSaveTimer();
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      void flushSave();
    }, AUTOSAVE_DELAY_MS);
  }, [cancelSaveTimer, flushSave]);

  // Leaving the page or the module must not eat the last 800 ms of typing.
  useEffect(() => {
    const onLeave = () => {
      void flushSave();
    };
    window.addEventListener("pagehide", onLeave);
    window.addEventListener("beforeunload", onLeave);
    return () => {
      window.removeEventListener("pagehide", onLeave);
      window.removeEventListener("beforeunload", onLeave);
      void flushSave();
    };
  }, [flushSave]);

  const openNote = useCallback(
    async (id: string, line?: number) => {
      if (!token) return;
      // The note being left may still hold unsaved keystrokes: hand them to
      // the save chain first (it targets that note by id, so this does not
      // block opening the next one).
      void flushSave();
      const requestId = ++openRequestRef.current;
      setSelectedId(id);
      setConfirmDelete(false);
      setSaveState("idle");
      draftRef.current = { markdown: null, title: null };
      try {
        const loaded = await getNote(token, id);
        // A slower response for a note the user already left must not
        // replace the one on screen.
        if (openRequestRef.current !== requestId) return;
        if (loaded.note.updated) lastUpdatedRef.current.set(loaded.note.id, loaded.note.updated);
        setDetail(loaded);
        if (line && line > 0) {
          const text = loaded.markdown.split(/\r?\n/)[line - 1]?.trim();
          if (text) {
            window.setTimeout(() => editorRef.current?.navigateToText(text), 0);
          }
        }
      } catch {
        if (openRequestRef.current === requestId) setDetail(null);
      }
    },
    [flushSave, token],
  );

  // Deep link from another module: open the requested note once per nonce.
  const handledOpenNonceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!openRequest || !token) return;
    if (handledOpenNonceRef.current === openRequest.nonce) return;
    handledOpenNonceRef.current = openRequest.nonce;
    setSection({ kind: "notes" });
    void openNote(openRequest.id);
  }, [openNote, openRequest, token]);

  const openWikilink = useCallback(
    async (title: string) => {
      const normalized = title.trim().toLocaleLowerCase();
      const local = notes.find(
        (note) =>
          note.title.toLocaleLowerCase() === normalized ||
          note.aliases.some((alias) => alias.toLocaleLowerCase() === normalized),
      );
      if (local) {
        await openNote(local.id);
        return;
      }
      if (!token) return;
      const data = await searchNotes(token, title, 10);
      const match = data.results.find(
        (result) => result.title.toLocaleLowerCase() === normalized,
      );
      if (match) await openNote(match.id, match.matches[0]?.line);
    },
    [notes, openNote, token],
  );

  const onMarkdownChange = useCallback(
    (markdown: string) => {
      draftRef.current.markdown = markdown;
      scheduleSave();
    },
    [scheduleSave],
  );

  const onTitleChange = useCallback(
    (title: string) => {
      draftRef.current.title = title;
      setDetail((previous) =>
        previous ? { ...previous, note: { ...previous.note, title } } : previous,
      );
      scheduleSave();
    },
    [scheduleSave],
  );

  const reloadAfterConflict = useCallback(async () => {
    const current = detailRef.current;
    if (!current) return;
    // Reload means "take the disk version": drop the draft on purpose so
    // openNote's flush has nothing to send.
    cancelSaveTimer();
    draftRef.current = { markdown: null, title: null };
    setSaveState("idle");
    await openNote(current.note.id);
  }, [cancelSaveTimer, openNote]);

  const onCreateNote = useCallback(async () => {
    if (!token) return;
    const folder = section.kind === "notes" ? section.folder ?? "" : "";
    const created = await createNote(token, {
      title: t("notes.untitled", { defaultValue: "Untitled" }),
      folder,
    });
    await refreshList();
    await refreshSidebarData();
    await openNote(created.note.id);
  }, [openNote, refreshList, refreshSidebarData, section, t, token]);

  const onDeleteNote = useCallback(async () => {
    const current = detailRef.current;
    if (!token || !current) return;
    // The note goes to the trash with its latest text, not the text of 800 ms ago.
    await flushSave();
    await deleteNote(token, current.note.id);
    setDetail(null);
    setSelectedId(null);
    setConfirmDelete(false);
    await refreshList();
    await refreshSidebarData();
  }, [flushSave, refreshList, refreshSidebarData, token]);

  const onTogglePinned = useCallback(async () => {
    const current = detailRef.current;
    if (!token || !current) return;
    await flushSave();
    const saved = await updateNote(token, current.note.id, {
      pinned: !current.note.pinned,
    });
    rememberSaved(saved);
    await refreshList();
  }, [flushSave, refreshList, rememberSaved, token]);

  const onToggleArchived = useCallback(async () => {
    const current = detailRef.current;
    if (!token || !current) return;
    await flushSave();
    await updateNote(token, current.note.id, {
      archived: !current.note.archived,
    });
    setDetail(null);
    setSelectedId(null);
    await refreshList();
  }, [flushSave, refreshList, token]);

  const onAddTag = useCallback(async () => {
    const current = detailRef.current;
    const cleaned = tagDraft.trim().replace(/^#/, "").toLowerCase();
    if (!token || !current || !cleaned) return;
    if (current.note.tags.includes(cleaned)) {
      setTagDraft("");
      return;
    }
    await flushSave();
    const saved = await updateNote(token, current.note.id, {
      tags: [...current.note.tags, cleaned],
    });
    rememberSaved(saved);
    setTagDraft("");
    await refreshSidebarData();
  }, [flushSave, refreshSidebarData, rememberSaved, tagDraft, token]);

  const onRemoveTag = useCallback(
    async (tag: string) => {
      const current = detailRef.current;
      if (!token || !current) return;
      await flushSave();
      const saved = await updateNote(token, current.note.id, {
        tags: current.note.tags.filter((existing) => existing !== tag),
      });
      rememberSaved(saved);
      await refreshSidebarData();
    },
    [flushSave, refreshSidebarData, rememberSaved, token],
  );

  const submitFolderDraft = useCallback(async () => {
    const name = (folderDraft ?? "").trim();
    setFolderDraft(null);
    if (!token || !name) return;
    await createNoteFolder(token, name);
    await refreshSidebarData();
  }, [folderDraft, refreshSidebarData, token]);

  const onToggleTask = useCallback(
    async (task: NoteTaskRow, done: boolean) => {
      if (!token) return;
      // Optimistic removal with rollback: a failed toggle used to vanish from
      // the list while staying open in the note.
      setTasks((current) =>
        current.filter(
          (row) => !(row.note_id === task.note_id && row.line === task.line),
        ),
      );
      try {
        const result = await toggleNoteTask(token, task.note_id, task.line, done);
        if (result.updated) lastUpdatedRef.current.set(task.note_id, result.updated);
        // The open note now differs on disk: reload it so its next save does
        // not write the unticked line back over the ticked one.
        if (detailRef.current?.note.id === task.note_id) {
          await openNote(task.note_id);
        }
      } catch {
        setTasks((current) =>
          current.some((row) => row.note_id === task.note_id && row.line === task.line)
            ? current
            : [task, ...current],
        );
      }
    },
    [openNote, token],
  );

  const onConfirmDanger = useCallback(async () => {
    if (!token || !danger) return;
    const action = danger;
    setDanger(null);
    try {
      if (action.kind === "purge") {
        await purgeNote(token, action.id);
      } else if (action.kind === "emptyTrash") {
        await emptyNotesTrash(token);
      } else {
        await deleteNoteFolder(token, action.path, { force: true });
        if (
          section.kind === "notes" &&
          section.folder &&
          (section.folder === action.path ||
            section.folder.startsWith(action.path + "/"))
        ) {
          setSection({ kind: "notes" });
        }
        await refreshSidebarData();
      }
      await refreshList();
    } catch {
      await refreshList();
    }
  }, [danger, refreshList, refreshSidebarData, section, token]);

  const onAddTask = useCallback(
    async (text: string) => {
      if (!token) return;
      await addNoteTask(token, text, {
        inboxTitle: t("notes.tasksInboxTitle", { defaultValue: "Tasks" }),
      });
      await refreshList();
    },
    [refreshList, t, token],
  );

  const onUploadFiles = useCallback(
    async (files: FileList) => {
      if (!token) return;
      for (const file of Array.from(files)) {
        const data = await file.arrayBuffer();
        let binary = "";
        const bytes = new Uint8Array(data);
        const chunk = 0x8000;
        for (let i = 0; i < bytes.length; i += chunk) {
          binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
        }
        await uploadNoteAttachment(token, file.name, btoa(binary));
      }
      await refreshList();
    },
    [refreshList, token],
  );

  // App opens the side chat when onSeed / onRun run (onOpenDeskChat).
  const revealChat = useCallback(() => {}, []);

  const noteAskContext = useCallback(() => {
    const current = detailRef.current;
    if (!current) return null;
    return {
      noteTitle: current.note.title,
      notePath: `~/.navin/notes/${current.note.path}`,
      noteId: current.note.id,
      locale: (i18n.language.startsWith("fr") ? "fr" : "en") as "fr" | "en",
    };
  }, [i18n.language]);

  const askNavin = useCallback(() => {
    const ctx = noteAskContext();
    if (!ctx || !onSeed) return;
    void flushSave();
    onSeed(
      buildNoteAskSeed({
        ...ctx,
        lead: t("notes.ask.about", { defaultValue: "À propos de cette note" }),
      }),
    );
    revealChat();
  }, [flushSave, noteAskContext, onSeed, revealChat, t]);

  const runNoteAskAction = useCallback(
    (action: NoteAskAction) => {
      const ctx = noteAskContext();
      if (!ctx) return;
      const leadKey =
        action === "summarize"
          ? "notes.ask.summarize"
          : action === "translate"
            ? "notes.ask.translate"
            : "notes.ask.correct";
      const leadDefault =
        action === "summarize"
          ? "Résume cette note"
          : action === "translate"
            ? "Traduis cette note"
            : "Corrige cette note";
      const prompt = buildNoteAskActionPrompt(action, {
        ...ctx,
        lead: t(leadKey, { defaultValue: leadDefault }),
      });
      void flushSave();
      if (onRun) {
        onRun(prompt);
      } else if (onSeed) {
        onSeed(prompt);
      } else {
        return;
      }
      revealChat();
    },
    [flushSave, noteAskContext, onRun, onSeed, revealChat, t],
  );

  const onRunAgentBlock = useCallback(
    (spec: string) => {
      const current = detailRef.current;
      if (!current || !onSeed) return;
      onSeed(
        buildAgentRunPrompt({
          noteTitle: current.note.title,
          notePath: `~/.navin/notes/${current.note.path}`,
          noteId: current.note.id,
          spec,
        }),
      );
      revealChat();
    },
    [onSeed, revealChat],
  );

  const onExportNote = useCallback(
    (format: NoteExportFormat) => {
      const current = detailRef.current;
      if (!current) return;
      const draft = draftRef.current;
      const snap = editorRef.current?.getSnapshot();
      const title = draft.title ?? current.note.title;
      const markdown =
        snap?.markdown || draft.markdown || current.markdown || "";
      exportNote(format, title, {
        markdown,
        html: snap?.html ?? null,
      });
    },
    [],
  );

  // Ctrl+S saves now; Ctrl+K jumps to search.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (saveTimerRef.current !== null) {
          window.clearTimeout(saveTimerRef.current);
          saveTimerRef.current = null;
        }
        void flushSave();
      } else if (event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [flushSave]);

  const sectionTitle = useMemo(() => {
    if (section.kind === "database")
      return t("notes.sections.database", { defaultValue: "Database" });
    if (section.kind === "tasks")
      return t("notes.sections.tasks", { defaultValue: "Tasks" });
    if (section.kind === "files")
      return t("notes.sections.files", { defaultValue: "Files" });
    if (section.kind === "ask")
      return t("notes.sections.ask", { defaultValue: "Ask Notes" });
    if (section.kind === "vault")
      return t("notes.sections.vault", { defaultValue: "Vault tools" });
    if (section.kind === "trash")
      return t("notes.sections.trash", { defaultValue: "Trash" });
    if (section.kind === "graph")
      return t("notes.sections.graph", { defaultValue: "Graph" });
    if (section.kind === "guide")
      return t("notes.sections.guide", { defaultValue: "Commands" });
    if (section.archived)
      return t("notes.sections.archived", { defaultValue: "Archived" });
    if (section.tag) return `#${section.tag}`;
    if (section.folder) return section.folder;
    return t("notes.sections.all", { defaultValue: "All notes" });
  }, [section, t]);

  if (!token) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
        {t("notes.connecting", { defaultValue: "Connecting…" })}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header
        className={cn(
          "flex shrink-0 flex-nowrap items-center gap-x-3 border-b border-border bg-muted/15 px-5 py-2.5",
          !chatOpen && NOTIFICATION_GUTTER,
        )}
      >
        <div className="flex min-w-0 flex-1 flex-nowrap items-center gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          <Notebook className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="shrink-0 text-xl font-semibold tracking-tight">
            {t("notes.title", { defaultValue: "Notes" })}
          </span>
          <span className="min-w-0 max-w-[14rem] truncate text-sm text-muted-foreground">
            / {sectionTitle}
          </span>
          <div className="relative shrink-0">
            <Search
              className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              ref={searchInputRef}
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                if (section.kind !== "notes") setSection({ kind: "notes" });
              }}
              placeholder={t("notes.searchPlaceholder", {
                defaultValue: "Search notes… (Ctrl+K)",
              })}
              className="h-10 w-36 rounded-lg border border-border/70 bg-background pl-7 pr-7 text-[12.5px] outline-none transition-colors focus:border-primary/50 focus-visible:ring-2 focus-visible:ring-primary sm:w-56"
            />
            {search ? (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
                aria-label={t("notes.clearSearch", { defaultValue: "Clear search" })}
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            ) : null}
          </div>
          <PrimaryButton
            text={t("notes.newAction", { defaultValue: "New" })}
            title={t("notes.newNote", { defaultValue: "New note" })}
            ariaLabel={t("notes.newNote", { defaultValue: "New note" })}
            iconProps={{ iconName: "Add" }}
            onClick={() => void onCreateNote()}
            styles={HEADER_BUTTON_STYLES}
            data-testid="notes-new"
          />
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {onToggleChat ? (
            <IconButton
              ariaLabel={
                chatOpen
                  ? t("notes.hideChat", { defaultValue: "Hide chat" })
                  : t("notes.chat", { defaultValue: "Chat" })
              }
              title={
                chatOpen
                  ? t("notes.hideChat", { defaultValue: "Hide chat" })
                  : t("notes.chat", { defaultValue: "Chat" })
              }
              iconProps={{ iconName: chatOpen ? "ChatSolid" : "Chat" }}
              onClick={onToggleChat}
              aria-pressed={Boolean(chatOpen)}
              checked={Boolean(chatOpen)}
              styles={ICON_BUTTON_STYLES}
              data-testid="notes-toggle-chat"
            />
          ) : null}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Sections rail */}
        <div className="flex w-40 shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-border/60 bg-muted/10 p-2 sm:w-52">
          <RailButton
            icon={StickyNote}
            label={t("notes.sections.all", { defaultValue: "All notes" })}
            active={
              section.kind === "notes" && !section.folder && !section.tag && !section.archived
            }
            onClick={() => setSection({ kind: "notes" })}
          />
          <RailButton
            icon={Database}
            label={t("notes.sections.database", { defaultValue: "Database" })}
            active={section.kind === "database"}
            onClick={() => setSection({ kind: "database" })}
          />
          <RailButton
            icon={Waypoints}
            label={t("notes.sections.graph", { defaultValue: "Graph" })}
            active={section.kind === "graph"}
            onClick={() => setSection({ kind: "graph" })}
          />
          <RailButton
            icon={Sparkles}
            label={t("notes.sections.ask", { defaultValue: "Ask Notes" })}
            active={section.kind === "ask"}
            onClick={() => setSection({ kind: "ask" })}
          />
          <RailButton
            icon={CheckSquare}
            label={t("notes.sections.tasks", { defaultValue: "Tasks" })}
            active={section.kind === "tasks"}
            onClick={() => setSection({ kind: "tasks" })}
          />
          <RailButton
            icon={Paperclip}
            label={t("notes.sections.files", { defaultValue: "Files" })}
            active={section.kind === "files"}
            onClick={() => setSection({ kind: "files" })}
          />
          <RailButton
            icon={FolderInput}
            label={t("notes.sections.vault", { defaultValue: "Vault tools" })}
            active={section.kind === "vault"}
            onClick={() => setSection({ kind: "vault" })}
          />
          <RailButton
            icon={Archive}
            label={t("notes.sections.archived", { defaultValue: "Archived" })}
            active={section.kind === "notes" && Boolean(section.archived)}
            onClick={() => setSection({ kind: "notes", archived: true })}
          />
          <RailButton
            icon={Trash2}
            label={t("notes.sections.trash", { defaultValue: "Trash" })}
            active={section.kind === "trash"}
            onClick={() => setSection({ kind: "trash" })}
          />
          <RailButton
            icon={SquareSlash}
            label={t("notes.sections.guide", { defaultValue: "Commands" })}
            active={section.kind === "guide"}
            onClick={() => setSection({ kind: "guide" })}
          />

          <div className="mt-3 flex items-center justify-between px-2">
            <span className="text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
              {t("notes.folders.title", { defaultValue: "Folders" })}
            </span>
            <button
              type="button"
              onClick={() => setFolderDraft((current) => (current === null ? "" : null))}
              className="rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
              aria-label={t("notes.folders.create", { defaultValue: "New folder" })}
              title={t("notes.folders.create", { defaultValue: "New folder" })}
            >
              <FolderPlus className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>
          <AnimatePresence initial={false}>
            {folderDraft !== null ? (
              <motion.div
                key="folder-draft"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.15, ease: "easeOut" }}
                className="overflow-hidden px-1"
              >
                <div className="flex items-center gap-1.5 rounded-lg border border-primary/40 bg-background px-2 py-1">
                  <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  <input
                    autoFocus
                    value={folderDraft}
                    onChange={(event) => setFolderDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void submitFolderDraft();
                      } else if (event.key === "Escape") {
                        setFolderDraft(null);
                      }
                    }}
                    onBlur={() => {
                      if (!(folderDraft ?? "").trim()) setFolderDraft(null);
                    }}
                    placeholder={t("notes.folders.createPrompt", {
                      defaultValue: "Folder name",
                    })}
                    aria-label={t("notes.folders.createPrompt", {
                      defaultValue: "Folder name",
                    })}
                    className="h-6 min-w-0 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-muted-foreground/60"
                  />
                  <button
                    type="button"
                    onMouseDown={(event) => {
                      event.preventDefault();
                      void submitFolderDraft();
                    }}
                    className="rounded p-0.5 text-primary transition-colors hover:bg-primary/10"
                    aria-label={t("notes.folders.confirm", { defaultValue: "Create" })}
                    title={t("notes.folders.confirm", { defaultValue: "Create" })}
                  >
                    <CornerDownLeft className="h-3 w-3" aria-hidden />
                  </button>
                </div>
              </motion.div>
            ) : null}
          </AnimatePresence>
          <FolderTree
            nodes={folders}
            activePath={section.kind === "notes" ? section.folder : undefined}
            onSelect={(path) => setSection({ kind: "notes", folder: path })}
            onDelete={(path, count) =>
              setDanger({ kind: "deleteFolder", path, count })
            }
          />

          {tags.length > 0 ? (
            <>
              <span className="mt-3 px-2 text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
                {t("notes.tags.title", { defaultValue: "Tags" })}
              </span>
              {tags.slice(0, 20).map((tag) => (
                <RailButton
                  key={tag.tag}
                  icon={Hash}
                  label={tag.tag}
                  badge={tag.count}
                  active={section.kind === "notes" && section.tag === tag.tag}
                  onClick={() => setSection({ kind: "notes", tag: tag.tag })}
                />
              ))}
            </>
          ) : null}
        </div>

        {/* Middle: list for the active section */}
        {section.kind === "notes" ? (
          <div className="flex w-52 shrink-0 flex-col overflow-y-auto border-r border-border/60 sm:w-72">
            {search.trim() ? (
              <NotesSearchResults
                query={search.trim()}
                results={searchResults}
                loading={searchLoading}
                onOpen={(id, line) => void openNote(id, line)}
              />
            ) : listLoading && notes.length === 0 ? (
              <div className="flex items-center justify-center py-10 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              </div>
            ) : notes.length === 0 ? (
              <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
                <StickyNote className="h-6 w-6 text-muted-foreground/60" aria-hidden />
                <p className="text-[12.5px] text-muted-foreground">
                  {search
                    ? t("notes.emptySearch", { defaultValue: "No note matches." })
                    : section.kind === "notes" && section.archived
                      ? t("notes.emptyArchived", {
                          defaultValue: "No archived notes. Archive a note from its toolbar to park it here.",
                        })
                      : t("notes.empty", {
                          defaultValue: "No notes yet. Create your first one.",
                        })}
                </p>
                {search.trim() && onSeed ? (
                  <button
                    type="button"
                    onClick={() =>
                      onSeed(
                        t("notes.ask.allNotesSeed", {
                          query: search.trim(),
                          defaultValue:
                            'Réponds en utilisant mes notes (outil notes) : "{{query}}"',
                        }),
                      )
                    }
                    className="mt-1 flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary/5 px-2.5 py-1.5 text-[12px] font-medium text-primary transition-colors hover:bg-primary/10"
                  >
                    <Sparkles className="h-3.5 w-3.5" aria-hidden />
                    {t("notes.ask.allNotesShort", {
                      defaultValue: "Ask Navin instead",
                    })}
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => void onCreateNote()}
                  className="mt-1 rounded-lg border border-border px-2.5 py-1.5 text-[12px] font-medium transition-colors hover:bg-muted/60"
                >
                  {t("notes.newNote", { defaultValue: "New note" })}
                </button>
              </div>
            ) : (
              <div className="flex flex-col p-1.5">
                {search.trim() && onSeed ? (
                  <button
                    type="button"
                    onClick={() =>
                      onSeed(
                        t("notes.ask.allNotesSeed", {
                          query: search.trim(),
                          defaultValue:
                            'Réponds en utilisant mes notes (outil notes) : "{{query}}"',
                        }),
                      )
                    }
                    className="mx-1 mb-1 flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary/5 px-2.5 py-2 text-left text-[12px] font-medium text-primary transition-colors hover:bg-primary/10"
                  >
                    <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden />
                    <span className="min-w-0 truncate">
                      {t("notes.ask.allNotes", {
                        query: search.trim(),
                        defaultValue: "Ask Navin: \u00ab {{query}} \u00bb",
                      })}
                    </span>
                  </button>
                ) : null}
                {notes.map((note) => (
                  <motion.button
                    key={note.id}
                    type="button"
                    layout="position"
                    onClick={() => void openNote(note.id)}
                    className={cn(
                      "flex flex-col gap-0.5 rounded-lg px-2.5 py-2 text-left transition-colors",
                      selectedId === note.id
                        ? "bg-muted/80"
                        : "hover:bg-muted/40",
                    )}
                  >
                    <span className="flex items-center gap-1.5">
                      {note.pinned ? (
                        <Pin className="h-3 w-3 shrink-0 text-primary" aria-hidden />
                      ) : null}
                      <span className="truncate text-[13px] font-medium">
                        {note.title}
                      </span>
                    </span>
                    {note.snippet ? (
                      <span className="line-clamp-2 text-[11.5px] leading-snug text-muted-foreground">
                        {note.snippet}
                      </span>
                    ) : null}
                    <span className="flex items-center gap-2 text-[10.5px] text-muted-foreground/80">
                      {note.folder ? (
                        <span className="inline-flex items-center gap-0.5">
                          <Folder className="h-2.5 w-2.5" aria-hidden />
                          {note.folder}
                        </span>
                      ) : null}
                      {note.tasks_open > 0 ? (
                        <span className="tabular-nums">
                          {note.tasks_open} ☐
                        </span>
                      ) : null}
                      {note.tags.slice(0, 3).map((tag) => (
                        <span key={tag}>#{tag}</span>
                      ))}
                    </span>
                  </motion.button>
                ))}
                {nextCursor ? (
                  <button
                    type="button"
                    onClick={() => void loadMore()}
                    className="mx-2 my-2 rounded-lg border border-border/70 px-2 py-1.5 text-[12px] text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
                  >
                    {t("notes.loadMore", { defaultValue: "Load more" })}
                  </button>
                ) : null}
              </div>
            )}
          </div>
        ) : null}

        {/* Main pane */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {section.kind === "database" ? (
            <DatabasePane
              token={token}
              onOpenNote={(id) => {
                setSection({ kind: "notes" });
                void openNote(id);
              }}
            />
          ) : section.kind === "graph" ? (
            <GraphPane
              token={token}
              onOpenNote={(id) => {
                setSection({ kind: "notes" });
                void openNote(id);
              }}
              onOpenTag={(tag) => setSection({ kind: "notes", tag })}
            />
          ) : section.kind === "tasks" ? (
            <TasksPane
              tasks={tasks}
              loading={listLoading}
              onToggle={onToggleTask}
              onAdd={onAddTask}
              onOpen={(id) => {
                setSection({ kind: "notes" });
                void openNote(id);
              }}
            />
          ) : section.kind === "files" ? (
            <FilesPane
              files={attachments}
              loading={listLoading}
              onUpload={onUploadFiles}
              onPreview={(file) =>
                setPreview({ path: file.path, name: file.name, size: file.size })
              }
            />
          ) : section.kind === "ask" ? (
            <AskNotesPane
              token={token}
              onOpen={(id, line) => {
                setSection({ kind: "notes" });
                void openNote(id, line);
              }}
            />
          ) : section.kind === "vault" ? (
            <VaultToolsPane
              token={token}
              onChanged={() => {
                void refreshList();
                void refreshSidebarData();
              }}
            />
          ) : section.kind === "trash" ? (
            <TrashPane
              notes={trash}
              loading={listLoading}
              onRestore={async (id) => {
                await restoreNote(token, id);
                await refreshList();
                await refreshSidebarData();
              }}
              onPurge={(id, title) => setDanger({ kind: "purge", id, title })}
              onEmptyTrash={() =>
                setDanger({ kind: "emptyTrash", count: trash.length })
              }
            />
          ) : section.kind === "guide" ? (
            <GuidePane />
          ) : detail ? (
            <>
              {/* Note toolbar */}
              <div className="flex shrink-0 items-center gap-1 border-b border-border/60 px-4 py-1.5">
                <SaveBadge state={saveState} onReload={reloadAfterConflict} />
                <div className="ml-auto flex items-center gap-0.5">
                  {onSeed || onRun ? (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button
                          type="button"
                          className="mr-1 flex h-7 items-center gap-1 rounded-md px-2 text-[11.5px] font-medium text-primary transition-colors hover:bg-primary/10"
                          title={t("notes.ask.menu", { defaultValue: "Navin IA" })}
                          aria-label={t("notes.ask.menu", {
                            defaultValue: "Navin IA",
                          })}
                        >
                          <Sparkles className="h-3 w-3" aria-hidden />
                          {t("notes.ask.menu", { defaultValue: "Navin IA" })}
                          <ChevronDown className="h-3 w-3 opacity-70" aria-hidden />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="min-w-[12.5rem]">
                        {onSeed ? (
                          <DropdownMenuItem
                            onSelect={() => askNavin()}
                            className="gap-2 text-[12.5px]"
                          >
                            <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden />
                            {t("notes.ask.button", { defaultValue: "Ask Navin" })}
                          </DropdownMenuItem>
                        ) : null}
                        <DropdownMenuItem
                          onSelect={() => runNoteAskAction("summarize")}
                          className="gap-2 text-[12.5px]"
                          title={t("notes.ask.summarizeHint", {
                            defaultValue: "Résumé en haut de la note",
                          })}
                        >
                          <ListTree className="h-3.5 w-3.5 shrink-0" aria-hidden />
                          {t("notes.ask.summarizeLabel", {
                            defaultValue: "Résumé",
                          })}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => runNoteAskAction("translate")}
                          className="gap-2 text-[12.5px]"
                          title={t("notes.ask.translateHint", {
                            defaultValue: "Traduction sous le texte",
                          })}
                        >
                          <Languages className="h-3.5 w-3.5 shrink-0" aria-hidden />
                          {t("notes.ask.translateLabel", {
                            defaultValue: "Traduction",
                          })}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => runNoteAskAction("correct")}
                          className="gap-2 text-[12.5px]"
                          title={t("notes.ask.correctHint", {
                            defaultValue: "Correction directement dans la note",
                          })}
                        >
                          <SpellCheck2 className="h-3.5 w-3.5 shrink-0" aria-hidden />
                          {t("notes.ask.correctLabel", {
                            defaultValue: "Correction",
                          })}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  ) : null}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        className="flex h-7 items-center gap-1 rounded-md px-2 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                        title={t("notes.export.title", {
                          defaultValue: "Export this note",
                        })}
                        aria-label={t("notes.export.title", {
                          defaultValue: "Export this note",
                        })}
                      >
                        <Download className="h-3 w-3" aria-hidden />
                        {t("notes.export.label", { defaultValue: "Export" })}
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="min-w-[10.5rem]">
                      <DropdownMenuItem
                        onSelect={() => onExportNote("md")}
                        className="text-[12.5px]"
                      >
                        {t("notes.export.md", { defaultValue: "Markdown (.md)" })}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => onExportNote("txt")}
                        className="text-[12.5px]"
                      >
                        {t("notes.export.txt", { defaultValue: "Text (.txt)" })}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => onExportNote("pdf")}
                        className="text-[12.5px]"
                      >
                        {t("notes.export.pdf", {
                          defaultValue: "PDF (print dialog)",
                        })}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <IconAction
                    icon={History}
                    label={t("notes.history.title", { defaultValue: "Version history" })}
                    onClick={() => setHistoryOpen(true)}
                  />
                  <IconAction
                    icon={SquareSlash}
                    label={t("notes.sections.guide", { defaultValue: "Commands" })}
                    onClick={() => setSection({ kind: "guide" })}
                  />
                  <IconAction
                    icon={detail.note.pinned ? PinOff : Pin}
                    label={
                      detail.note.pinned
                        ? t("notes.unpin", { defaultValue: "Unpin" })
                        : t("notes.pin", { defaultValue: "Pin" })
                    }
                    onClick={() => void onTogglePinned()}
                  />
                  <IconAction
                    icon={detail.note.archived ? ArchiveRestore : Archive}
                    label={
                      detail.note.archived
                        ? t("notes.unarchive", { defaultValue: "Unarchive" })
                        : t("notes.archive", { defaultValue: "Archive" })
                    }
                    onClick={() => void onToggleArchived()}
                  />
                  {confirmDelete ? (
                    <button
                      type="button"
                      onClick={() => void onDeleteNote()}
                      onBlur={() => setConfirmDelete(false)}
                      className="flex h-7 items-center gap-1 rounded-md bg-red-500/15 px-2 text-[11.5px] font-medium text-red-600 transition-colors hover:bg-red-500/25 dark:text-red-400"
                    >
                      {t("notes.confirmDelete", { defaultValue: "Confirm?" })}
                    </button>
                  ) : (
                    <IconAction
                      icon={Trash2}
                      label={t("notes.delete", { defaultValue: "Delete" })}
                      onClick={() => setConfirmDelete(true)}
                    />
                  )}
                </div>
              </div>

              {/* Title + tags */}
              <div className="shrink-0 px-4 pt-5 sm:px-8">
                <input
                  value={detail.note.title}
                  onChange={(event) => onTitleChange(event.target.value)}
                  placeholder={t("notes.untitled", { defaultValue: "Untitled" })}
                  className="w-full bg-transparent text-[26px] font-bold leading-tight tracking-tight outline-none placeholder:text-muted-foreground/50"
                />
                <div className="mt-1.5 flex max-h-20 flex-wrap items-center gap-1.5 overflow-y-auto">
                  {detail.note.tags.map((tag) => (
                    <span
                      key={tag}
                      className="group inline-flex items-center gap-1 rounded-full bg-muted/70 px-2 py-0.5 text-[11px] text-muted-foreground"
                    >
                      #{tag}
                      <button
                        type="button"
                        onClick={() => void onRemoveTag(tag)}
                        className="opacity-0 transition-opacity group-hover:opacity-100"
                        aria-label={t("notes.removeTag", {
                          defaultValue: "Remove tag",
                        })}
                      >
                        <X className="h-2.5 w-2.5" aria-hidden />
                      </button>
                    </span>
                  ))}
                  <input
                    value={tagDraft}
                    onChange={(event) => setTagDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void onAddTag();
                      }
                    }}
                    placeholder={t("notes.addTag", { defaultValue: "+ tag" })}
                    className="w-20 bg-transparent text-[11px] text-muted-foreground outline-none placeholder:text-muted-foreground/50"
                  />
                </div>
              </div>

              {/* Editor */}
              <div className="min-h-0 flex-1">
                <NoteEditor
                  ref={editorRef}
                  token={token}
                  noteId={detail.note.id}
                  markdown={detail.markdown}
                  onMarkdownChange={onMarkdownChange}
                  onRunAgent={onSeed ? onRunAgentBlock : undefined}
                  onOpenAttachment={(path, name) => setPreview({ path, name })}
                  wikilinkTargets={notes.map((note) => ({
                    id: note.id,
                    title: note.title,
                    aliases: note.aliases,
                  }))}
                  onOpenWikilink={(title) => void openWikilink(title)}
                />
              </div>

              {/* Backlinks footer */}
              {detail.backlinks &&
              (detail.backlinks.backlinks.length > 0 ||
                detail.backlinks.unlinked_mentions.length > 0) ? (
                <div className="max-h-28 shrink-0 overflow-y-auto border-t border-border/60 px-4 py-2 sm:px-8">
                  <span className="text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {t("notes.backlinks.title", { defaultValue: "Backlinks" })}
                  </span>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {detail.backlinks.backlinks.map((ref) => (
                      <button
                        key={ref.id}
                        type="button"
                        onClick={() => void openNote(ref.id)}
                        className="rounded-md border border-border/70 px-2 py-0.5 text-[11.5px] transition-colors hover:bg-muted/60"
                      >
                        {ref.title}
                      </button>
                    ))}
                    {detail.backlinks.unlinked_mentions.map((ref) => (
                      <button
                        key={`unlinked-${ref.id}`}
                        type="button"
                        onClick={() => void openNote(ref.id)}
                        className="rounded-md border border-dashed border-border/70 px-2 py-0.5 text-[11.5px] text-muted-foreground transition-colors hover:bg-muted/60"
                        title={t("notes.backlinks.unlinked", {
                          defaultValue: "Unlinked mention",
                        })}
                      >
                        {ref.title}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
              <Notebook className="h-8 w-8 text-muted-foreground/50" aria-hidden />
              <p className="max-w-64 text-[13px] text-muted-foreground">
                {t("notes.selectPrompt", {
                  defaultValue: "Select a note on the left, or create a new one.",
                })}
              </p>
              <button
                type="button"
                onClick={() => void onCreateNote()}
                className="flex items-center gap-1 rounded-lg bg-foreground px-3 py-1.5 text-[12.5px] font-medium text-background transition-[opacity,transform] hover:opacity-85 active:scale-[0.97]"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden />
                {t("notes.newNote", { defaultValue: "New note" })}
              </button>
            </div>
          )}
        </div>
      </div>

      <AttachmentPreview
        token={token}
        target={preview}
        onClose={() => setPreview(null)}
      />

      {detail ? (
        <NoteHistoryPanel
          token={token}
          note={detail}
          open={historyOpen}
          onDismiss={() => setHistoryOpen(false)}
          onRestored={(restored) => {
            draftRef.current = { markdown: null, title: null };
            setDetail(restored);
            setSaveState("saved");
            setNotes((current) =>
              current.map((note) => (note.id === restored.note.id ? restored.note : note)),
            );
          }}
        />
      ) : null}

      <ConfirmDialog
        open={danger !== null}
        title={
          danger?.kind === "purge"
            ? t("notes.trash.purgeTitle", { defaultValue: "Delete forever?" })
            : danger?.kind === "emptyTrash"
              ? t("notes.trash.emptyTitle", { defaultValue: "Empty the trash?" })
              : t("notes.folders.deleteTitle", { defaultValue: "Delete folder?" })
        }
        description={
          danger?.kind === "purge"
            ? t("notes.trash.purgeDescription", {
                title: danger.title,
                defaultValue:
                  '"{{title}}" will be permanently deleted. This cannot be undone.',
              })
            : danger?.kind === "emptyTrash"
              ? t("notes.trash.emptyDescription", {
                  count: danger.count,
                  defaultValue:
                    "{{count}} note(s) will be permanently deleted. This cannot be undone.",
                })
              : danger
                ? t("notes.folders.deleteDescription", {
                    path: danger.path,
                    count: danger.count,
                    defaultValue:
                      '"{{path}}" will be removed. Its {{count}} note(s) go to the trash.',
                  })
                : ""
        }
        confirmLabel={
          danger?.kind === "deleteFolder"
            ? t("notes.folders.deleteConfirm", { defaultValue: "Delete folder" })
            : t("notes.trash.purgeConfirm", { defaultValue: "Delete forever" })
        }
        onCancel={() => setDanger(null)}
        onConfirm={() => void onConfirmDanger()}
      />
    </div>
  );
}

function RailButton({
  icon: Icon,
  label,
  badge,
  active,
  onClick,
}: {
  icon: typeof StickyNote;
  label: string;
  badge?: number;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-8 items-center gap-2 rounded-lg px-2 text-[12.5px] transition-colors",
        active
          ? "bg-muted/80 font-medium text-foreground"
          : "text-muted-foreground hover:bg-muted/40 hover:text-foreground",
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 truncate text-left">{label}</span>
      {badge !== undefined ? (
        <span className="text-[10.5px] tabular-nums text-muted-foreground/70">
          {badge}
        </span>
      ) : null}
    </button>
  );
}

function FolderTree({
  nodes,
  activePath,
  onSelect,
  onDelete,
  depth = 0,
}: {
  nodes: NoteFolderNode[];
  activePath?: string;
  onSelect: (path: string) => void;
  onDelete: (path: string, count: number) => void;
  depth?: number;
}) {
  const { t } = useTranslation();
  if (!nodes.length && depth === 0) {
    return null;
  }
  return (
    <>
      {nodes.map((node) => (
        <div key={node.path} style={{ paddingLeft: depth ? 12 : 0 }}>
          <div className="group/folder relative">
            <RailButton
              icon={Folder}
              label={node.name}
              badge={node.count}
              active={activePath === node.path}
              onClick={() => onSelect(node.path)}
            />
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onDelete(node.path, node.count);
              }}
              className="absolute right-1 top-1/2 hidden h-5 w-5 -translate-y-1/2 items-center justify-center rounded bg-[hsl(var(--card))] text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive group-hover/folder:flex"
              aria-label={t("notes.folders.deleteAction", {
                name: node.name,
                defaultValue: "Delete folder {{name}}",
              })}
              title={t("notes.folders.deleteTitle", {
                defaultValue: "Delete folder?",
              })}
            >
              <Trash2 className="h-3 w-3" aria-hidden />
            </button>
          </div>
          {node.children.length ? (
            <FolderTree
              nodes={node.children}
              activePath={activePath}
              onSelect={onSelect}
              onDelete={onDelete}
              depth={depth + 1}
            />
          ) : null}
        </div>
      ))}
    </>
  );
}

function SaveBadge({
  state,
  onReload,
}: {
  state: SaveState;
  onReload: () => void;
}) {
  const { t } = useTranslation();
  if (state === "conflict") {
    return (
      <button
        type="button"
        onClick={onReload}
        className="flex items-center gap-1 rounded-md bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 transition-colors hover:bg-amber-500/25 dark:text-amber-400"
      >
        <RotateCcw className="h-3 w-3" aria-hidden />
        {t("notes.save.conflict", {
          defaultValue: "Changed elsewhere - reload",
        })}
      </button>
    );
  }
  const label =
    state === "saving" || state === "dirty"
      ? t("notes.save.saving", { defaultValue: "Saving…" })
      : state === "saved"
        ? t("notes.save.saved", { defaultValue: "Saved" })
        : state === "error"
          ? t("notes.save.error", { defaultValue: "Save failed" })
          : "";
  return (
    <AnimatePresence mode="popLayout">
      {label ? (
        <motion.span
          key={label}
          initial={{ opacity: 0, y: 2 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          className={cn(
            "text-[11px]",
            state === "error" ? "text-red-500" : "text-muted-foreground",
          )}
        >
          {label}
        </motion.span>
      ) : null}
    </AnimatePresence>
  );
}

function IconAction({
  icon: Icon,
  label,
  onClick,
}: {
  icon: typeof Pin;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
      aria-label={label}
      title={label}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
    </button>
  );
}

function TasksPane({
  tasks,
  loading,
  onToggle,
  onAdd,
  onOpen,
}: {
  tasks: NoteTaskRow[];
  loading: boolean;
  onToggle: (task: NoteTaskRow, done: boolean) => void;
  onAdd: (text: string) => Promise<void>;
  onOpen: (noteId: string) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);

  const submit = async () => {
    const text = draft.trim();
    if (!text || adding) return;
    setAdding(true);
    try {
      await onAdd(text);
      setDraft("");
    } finally {
      setAdding(false);
    }
  };

  if (loading && tasks.length === 0) {
    return <PaneSpinner />;
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
      <div className="mb-3 flex items-center gap-2 rounded-lg border border-border/60 px-2.5 py-1.5 focus-within:border-[hsl(var(--primary))]/50">
        <Plus className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void submit();
          }}
          placeholder={t("notes.tasksAddPlaceholder", {
            defaultValue: "Add a task, press Enter…",
          })}
          className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground/70"
          aria-label={t("notes.tasksAddPlaceholder", {
            defaultValue: "Add a task, press Enter…",
          })}
        />
        {adding ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
        ) : draft.trim() ? (
          <button
            type="button"
            onClick={() => void submit()}
            className="flex h-6 items-center gap-1 rounded-md bg-[hsl(var(--primary))]/10 px-2 text-[11px] font-medium text-[hsl(var(--primary))] transition-colors hover:bg-[hsl(var(--primary))]/20"
          >
            <CornerDownLeft className="h-3 w-3" aria-hidden />
            {t("notes.tasksAddAction", { defaultValue: "Add" })}
          </button>
        ) : null}
      </div>
      {!tasks.length ? (
        <PaneEmpty
          icon={CheckSquare}
          text={t("notes.tasksEmpty", {
            defaultValue: "No open tasks yet. Add one above.",
          })}
        />
      ) : null}
      {tasks.map((task) => (
        <motion.div
          key={`${task.note_id}:${task.line}`}
          layout="position"
          exit={{ opacity: 0, x: 8 }}
          className="group flex items-start gap-2.5 rounded-lg px-2 py-1.5 transition-colors hover:bg-muted/40"
        >
          <input
            type="checkbox"
            checked={task.done}
            onChange={(event) => onToggle(task, event.target.checked)}
            className="mt-0.5 h-4 w-4 cursor-pointer accent-[hsl(var(--primary))]"
            aria-label={task.text}
          />
          <div className="min-w-0 flex-1">
            <p className="text-[13px] leading-snug">{task.text}</p>
            <button
              type="button"
              onClick={() => onOpen(task.note_id)}
              className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
            >
              <FileText className="h-2.5 w-2.5" aria-hidden />
              {task.note_title}
              <ChevronRight className="h-2.5 w-2.5" aria-hidden />
            </button>
          </div>
          {task.due ? (
            <span className="shrink-0 rounded-md bg-muted/70 px-1.5 py-0.5 text-[10.5px] tabular-nums text-muted-foreground">
              {task.due}
            </span>
          ) : null}
          {task.priority ? (
            <span
              className={cn(
                "shrink-0 rounded-md px-1.5 py-0.5 text-[10.5px] font-medium",
                task.priority === 1
                  ? "bg-red-500/15 text-red-600 dark:text-red-400"
                  : task.priority === 2
                    ? "bg-amber-500/15 text-amber-700 dark:text-amber-400"
                    : "bg-muted/70 text-muted-foreground",
              )}
            >
              p{task.priority}
            </span>
          ) : null}
        </motion.div>
      ))}
    </div>
  );
}

function FilesPane({
  files,
  loading,
  onUpload,
  onPreview,
}: {
  files: NoteAttachment[];
  loading: boolean;
  onUpload: (files: FileList) => Promise<void>;
  onPreview: (file: NoteAttachment) => void;
}) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);

  const pickFiles = () => inputRef.current?.click();

  if (loading && files.length === 0) {
    return <PaneSpinner />;
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => {
          const picked = event.target.files;
          if (picked && picked.length) {
            setUploading(true);
            void onUpload(picked).finally(() => setUploading(false));
          }
          event.target.value = "";
        }}
      />
      <button
        type="button"
        onClick={pickFiles}
        disabled={uploading}
        className="mb-3 flex items-center justify-center gap-2 rounded-lg border border-dashed border-border/70 px-3 py-2.5 text-[12.5px] text-muted-foreground transition-colors hover:border-[hsl(var(--primary))]/50 hover:text-foreground disabled:opacity-60"
      >
        {uploading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <Plus className="h-3.5 w-3.5" aria-hidden />
        )}
        {uploading
          ? t("notes.filesUploading", { defaultValue: "Uploading…" })
          : t("notes.filesAddAction", { defaultValue: "Add files" })}
      </button>
      {!files.length ? (
        <PaneEmpty
          icon={Paperclip}
          text={t("notes.filesEmpty", {
            defaultValue: "No attachments yet. Add files above or use /image in a note.",
          })}
        />
      ) : null}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-2">
        {files.map((file) => (
        <button
          key={file.path}
          type="button"
          onClick={() => onPreview(file)}
          className="flex flex-col gap-1 rounded-xl border border-border/60 p-3 text-left transition-colors hover:bg-muted/40"
        >
          <Paperclip className="h-4 w-4 text-muted-foreground" aria-hidden />
          <span className="w-full truncate text-[12.5px] font-medium">{file.name}</span>
          <span className="text-[10.5px] tabular-nums text-muted-foreground">
            {formatSize(file.size)}
            {file.referenced_by.length
              ? ` · ${t("notes.filesUsed", {
                  defaultValue: "{{count}} note(s)",
                  count: file.referenced_by.length,
                })}`
              : ""}
          </span>
        </button>
        ))}
      </div>
    </div>
  );
}

function TrashPane({
  notes,
  loading,
  onRestore,
  onPurge,
  onEmptyTrash,
}: {
  notes: TrashedNote[];
  loading: boolean;
  onRestore: (id: string) => Promise<void>;
  onPurge: (id: string, title: string) => void;
  onEmptyTrash: () => void;
}) {
  const { t } = useTranslation();
  if (loading && notes.length === 0) {
    return <PaneSpinner />;
  }
  if (!notes.length) {
    return (
      <PaneEmpty
        icon={Trash2}
        text={t("notes.trashEmpty", { defaultValue: "Trash is empty." })}
      />
    );
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-[11.5px] text-muted-foreground">
          {t("notes.trash.count", {
            count: notes.length,
            defaultValue: "{{count}} note(s) in the trash",
          })}
        </span>
        <button
          type="button"
          onClick={onEmptyTrash}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-destructive/30 px-2 py-1 text-[11.5px] font-medium text-destructive transition-colors hover:bg-destructive/10"
        >
          <Trash2 className="h-3 w-3" aria-hidden />
          {t("notes.trash.emptyAction", { defaultValue: "Empty trash" })}
        </button>
      </div>
      <div className="flex flex-col gap-1">
        {notes.map((note) => (
          <div
            key={note.id}
            className="flex items-center gap-3 rounded-lg border border-border/50 px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-medium">{note.title}</p>
              {note.snippet ? (
                <p className="truncate text-[11.5px] text-muted-foreground">
                  {note.snippet}
                </p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => void onRestore(note.id)}
              className="flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-[11.5px] font-medium transition-colors hover:bg-muted/60"
            >
              <RotateCcw className="h-3 w-3" aria-hidden />
              {t("notes.restore", { defaultValue: "Restore" })}
            </button>
            <button
              type="button"
              onClick={() => onPurge(note.id, note.title)}
              className="flex shrink-0 items-center gap-1 rounded-md border border-destructive/30 px-2 py-1 text-[11.5px] font-medium text-destructive transition-colors hover:bg-destructive/10"
              title={t("notes.trash.purgeTitle", {
                defaultValue: "Delete forever?",
              })}
            >
              <Trash2 className="h-3 w-3" aria-hidden />
              {t("notes.trash.purgeAction", { defaultValue: "Delete forever" })}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function PaneSpinner() {
  return (
    <div className="flex flex-1 items-center justify-center text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
    </div>
  );
}

function PaneEmpty({
  icon: Icon,
  text,
}: {
  icon: typeof Paperclip;
  text: string;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 px-8 text-center">
      <Icon className="h-6 w-6 text-muted-foreground/50" aria-hidden />
      <p className="max-w-72 text-[12.5px] text-muted-foreground">{text}</p>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
