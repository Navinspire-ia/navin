import { useCallback, useEffect, useState } from "react";
import {
  DefaultButton,
  Dropdown,
  MessageBar,
  MessageBarType,
  Panel,
  PanelType,
  PrimaryButton,
  Spinner,
  TextField,
  type IDropdownOption,
} from "@fluentui/react";
import "@/lib/fluent-icons";
import { motion, useReducedMotion } from "framer-motion";
import { DatabaseZap, FolderInput, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/lib/api";
import {
  askNotes,
  getNoteHistorySnapshot,
  importNotesVault,
  listNoteHistory,
  rebuildNotesIndex,
  restoreNoteHistorySnapshot,
  type NoteDetail,
  type NoteHistoryPreview,
  type NoteHistorySnapshot,
  type NoteSearchResult,
  type NotesAskResult,
  type NotesImportResult,
  type NotesRebuildResult,
} from "@/lib/notes-api";
import { formatSnapshotStamp, highlightMatch } from "./notes-highlight";

export function NotesSearchResults({
  query,
  results,
  loading,
  onOpen,
}: {
  query: string;
  results: NoteSearchResult[];
  loading: boolean;
  onOpen: (id: string, line?: number) => void;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  if (loading && results.length === 0) return <Spinner className="py-8" />;
  if (!results.length) {
    return (
      <p className="p-6 text-center text-xs text-muted-foreground">
        {t("notes.search.empty", { defaultValue: "No result in note contents." })}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1 p-1.5" role="listbox">
      {results.map((result) => (
        <motion.button
          key={result.id}
          type="button"
          initial={reduceMotion ? false : { opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          onClick={() => onOpen(result.id, result.matches[0]?.line)}
          className="rounded-lg px-2.5 py-2 text-left outline-none hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-primary"
          role="option"
        >
          <span className="block truncate text-[13px] font-semibold">
            <Highlighted text={result.title} query={query} />
          </span>
          {result.matches.length ? (
            <span className="mt-1 flex flex-col gap-1">
              {result.matches.map((match) => (
                <span
                  key={`${match.line}:${match.col}`}
                  className="grid grid-cols-[2.5rem_1fr] gap-1 text-[11.5px] leading-snug text-muted-foreground"
                >
                  <span className="font-mono text-[10px] text-primary">
                    {t("notes.search.line", {
                      line: match.line,
                      defaultValue: "L{{line}}",
                    })}
                  </span>
                  <span className="line-clamp-2">
                    <Highlighted text={match.text} query={query} />
                  </span>
                </span>
              ))}
            </span>
          ) : (
            <span className="line-clamp-2 text-[11.5px] text-muted-foreground">
              <Highlighted text={result.snippet} query={query} />
            </span>
          )}
          {result.folder ? (
            <span className="mt-1 block truncate text-[10px] text-muted-foreground">
              {result.folder}
            </span>
          ) : null}
        </motion.button>
      ))}
    </div>
  );
}

function Highlighted({ text, query }: { text: string; query: string }) {
  return (
    <>
      {highlightMatch(text, query).map((part, index) =>
        part.highlighted ? (
          <mark key={index} className="rounded-sm bg-yellow-300/60 px-0.5 text-inherit">
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}

export function AskNotesPane({
  token,
  onOpen,
}: {
  token: string;
  onOpen: (id: string, line?: number) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<NotesAskResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    const value = query.trim();
    if (!value || loading) return;
    setLoading(true);
    setError("");
    try {
      setResult(await askNotes(token, value, 8));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto w-full max-w-3xl">
        <div className="mb-5 flex items-center gap-3">
          <span className="rounded-xl bg-primary/10 p-2 text-primary">
            <Sparkles className="h-5 w-5" aria-hidden />
          </span>
          <div>
            <h2 className="text-lg font-semibold">
              {t("notes.askSurface.title", { defaultValue: "Ask Notes" })}
            </h2>
            <p className="text-xs text-muted-foreground">
              {t("notes.askSurface.hint", {
                defaultValue: "Find the most relevant passages across your vault.",
              })}
            </p>
          </div>
        </div>
        <TextField
          multiline
          autoAdjustHeight
          value={query}
          onChange={(_, value) => setQuery(value ?? "")}
          placeholder={t("notes.askSurface.placeholder", {
            defaultValue: "What did I decide about...",
          })}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) void submit();
          }}
        />
        <div className="mt-2 flex justify-end">
          <PrimaryButton onClick={() => void submit()} disabled={!query.trim() || loading}>
            {loading
              ? t("notes.askSurface.searching", { defaultValue: "Searching..." })
              : t("notes.askSurface.submit", { defaultValue: "Ask" })}
          </PrimaryButton>
        </div>
        {error ? (
          <MessageBar className="mt-4" messageBarType={MessageBarType.error}>
            {error}
          </MessageBar>
        ) : null}
        {result ? (
          <div className="mt-5">
            <MessageBar
              messageBarType={
                result.syncing
                  ? MessageBarType.warning
                  : result.semantic
                    ? MessageBarType.success
                    : MessageBarType.info
              }
            >
              {result.syncing
                ? t("notes.askSurface.syncing", {
                    defaultValue: "Semantic index syncing. Results may improve shortly.",
                  })
                : result.semantic
                  ? t("notes.askSurface.semantic", {
                      defaultValue: "Semantic search is active.",
                    })
                  : t("notes.askSurface.fallback", {
                      defaultValue: "Lexical fallback is active.",
                    })}
            </MessageBar>
            <div className="mt-3 flex flex-col gap-2">
              {result.passages.map((passage, index) => (
                <button
                  key={`${passage.note_id}:${passage.line}:${index}`}
                  type="button"
                  onClick={() => onOpen(passage.note_id, passage.line)}
                  className="rounded-xl border border-border/70 p-3 text-left outline-none hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-primary"
                >
                  <span className="flex items-center justify-between gap-2">
                    <strong className="truncate text-sm">{passage.title}</strong>
                    <span className="shrink-0 text-[10px] text-primary">
                      {t("notes.search.line", {
                        line: passage.line,
                        defaultValue: "L{{line}}",
                      })}
                    </span>
                  </span>
                  {passage.heading ? (
                    <span className="mt-1 block text-[11px] font-medium text-muted-foreground">
                      {passage.heading}
                    </span>
                  ) : null}
                  <span className="mt-1 block text-xs leading-relaxed">{passage.text}</span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function NoteHistoryPanel({
  token,
  note,
  open,
  onDismiss,
  onRestored,
}: {
  token: string;
  note: NoteDetail;
  open: boolean;
  onDismiss: () => void;
  onRestored: (note: NoteDetail) => void;
}) {
  const { t, i18n } = useTranslation();
  const [snapshots, setSnapshots] = useState<NoteHistorySnapshot[]>([]);
  const [preview, setPreview] = useState<NoteHistoryPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await listNoteHistory(token, note.note.id);
      setSnapshots(data.snapshots);
      if (data.snapshots[0]) {
        setPreview(await getNoteHistorySnapshot(token, note.note.id, data.snapshots[0].stamp));
      } else {
        setPreview(null);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [note.note.id, token]);

  useEffect(() => {
    if (open) void load();
  }, [load, open]);

  const restore = async () => {
    if (!preview || restoring) return;
    setRestoring(true);
    setError("");
    try {
      const saved = await restoreNoteHistorySnapshot(token, note, preview);
      onRestored(saved);
      await load();
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 409
          ? t("notes.history.conflict", {
              defaultValue: "The note changed. Reload it before restoring.",
            })
          : caught instanceof Error
            ? caught.message
            : String(caught),
      );
    } finally {
      setRestoring(false);
    }
  };

  return (
    <Panel
      isOpen={open}
      onDismiss={onDismiss}
      type={PanelType.medium}
      headerText={t("notes.history.title", { defaultValue: "Version history" })}
      closeButtonAriaLabel={t("notes.history.close", { defaultValue: "Close history" })}
    >
      {error ? <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar> : null}
      {loading ? <Spinner className="py-6" /> : null}
      {!loading && snapshots.length === 0 ? (
        <MessageBar>{t("notes.history.empty", { defaultValue: "No snapshot yet." })}</MessageBar>
      ) : null}
      <div className="mt-3 grid min-h-0 gap-3 sm:grid-cols-[12rem_1fr]">
        <div className="flex max-h-72 flex-col gap-1 overflow-y-auto">
          {snapshots.map((snapshot) => (
            <DefaultButton
              key={snapshot.stamp}
              checked={preview?.stamp === snapshot.stamp}
              onClick={() =>
                void getNoteHistorySnapshot(token, note.note.id, snapshot.stamp)
                  .then(setPreview)
                  .catch((caught) =>
                    setError(caught instanceof Error ? caught.message : String(caught)),
                  )
              }
              text={formatSnapshotStamp(snapshot.stamp, i18n.language)}
              title={`${snapshot.size} B`}
            />
          ))}
        </div>
        <div>
          {preview ? (
            <>
              <pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap rounded-lg border border-border/70 bg-muted/20 p-3 text-xs">
                {preview.markdown}
              </pre>
              <PrimaryButton className="mt-3" onClick={() => void restore()} disabled={restoring}>
                {restoring
                  ? t("notes.history.restoring", { defaultValue: "Restoring..." })
                  : t("notes.history.restore", { defaultValue: "Restore this version" })}
              </PrimaryButton>
              <p className="mt-2 text-[11px] text-muted-foreground">
                {t("notes.history.safety", {
                  defaultValue: "The current version is snapshotted before restore.",
                })}
              </p>
            </>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}

const CONFLICT_OPTIONS: IDropdownOption[] = [
  { key: "rename", text: "Rename" },
  { key: "skip", text: "Skip" },
  { key: "overwrite", text: "Overwrite" },
];

export function VaultToolsPane({
  token,
  onChanged,
}: {
  token: string;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [path, setPath] = useState("");
  const [conflict, setConflict] = useState<"rename" | "skip" | "overwrite">("rename");
  const [busy, setBusy] = useState<"import" | "rebuild" | null>(null);
  const [result, setResult] = useState<NotesImportResult | NotesRebuildResult | null>(null);
  const [error, setError] = useState("");

  const runImport = async () => {
    setBusy("import");
    setError("");
    try {
      setResult(await importNotesVault(token, path.trim(), conflict));
      onChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  };
  const rebuild = async () => {
    setBusy("rebuild");
    setError("");
    try {
      setResult(await rebuildNotesIndex(token));
      onChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto w-full max-w-2xl space-y-5">
        <section className="rounded-xl border border-border/70 p-4">
          <h2 className="mb-3 flex items-center gap-2 text-base font-semibold">
            <FolderInput className="h-4 w-4 text-primary" aria-hidden />
            {t("notes.vault.importTitle", { defaultValue: "Import a vault" })}
          </h2>
          <TextField
            label={t("notes.vault.path", { defaultValue: "Folder, Markdown or ZIP path" })}
            value={path}
            onChange={(_, value) => setPath(value ?? "")}
          />
          <Dropdown
            className="mt-3"
            label={t("notes.vault.conflict", { defaultValue: "Name conflicts" })}
            options={CONFLICT_OPTIONS.map((option) => ({
              ...option,
              text: t(`notes.vault.${String(option.key)}`, { defaultValue: option.text }),
            }))}
            selectedKey={conflict}
            onChange={(_, option) =>
              setConflict((option?.key as "rename" | "skip" | "overwrite") ?? "rename")
            }
          />
          <PrimaryButton
            className="mt-3"
            onClick={() => void runImport()}
            disabled={!path.trim() || busy !== null}
          >
            {busy === "import"
              ? t("notes.vault.importing", { defaultValue: "Importing..." })
              : t("notes.vault.import", { defaultValue: "Import" })}
          </PrimaryButton>
        </section>
        <section className="rounded-xl border border-border/70 p-4">
          <h2 className="mb-2 flex items-center gap-2 text-base font-semibold">
            <DatabaseZap className="h-4 w-4 text-primary" aria-hidden />
            {t("notes.vault.rebuildTitle", { defaultValue: "Search index" })}
          </h2>
          <p className="mb-3 text-xs text-muted-foreground">
            {t("notes.vault.rebuildHint", {
              defaultValue: "Rebuild the manifest and full-text index from Markdown files.",
            })}
          </p>
          <DefaultButton onClick={() => void rebuild()} disabled={busy !== null}>
            {busy === "rebuild"
              ? t("notes.vault.rebuilding", { defaultValue: "Rebuilding..." })
              : t("notes.vault.rebuild", { defaultValue: "Rebuild index" })}
          </DefaultButton>
        </section>
        {error ? <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar> : null}
        {result ? (
          <MessageBar messageBarType={MessageBarType.success}>
            {"imported" in result
              ? t("notes.vault.importResult", {
                  imported: result.imported,
                  skipped: result.skipped,
                  overwritten: result.overwritten,
                  indexed: result.indexed,
                  defaultValue:
                    "{{imported}} imported, {{skipped}} skipped, {{overwritten}} overwritten, {{indexed}} indexed.",
                })
              : t("notes.vault.rebuildResult", {
                  count: result.search.total,
                  defaultValue: "{{count}} notes indexed.",
                })}
          </MessageBar>
        ) : null}
      </div>
    </div>
  );
}

