import { Camera, History, Loader2, RefreshCw, Undo2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { WorkspaceCheckpoint } from "@/lib/api";
import {
  createCheckpoint,
  fetchCheckpointDiff,
  fetchCheckpoints,
  restoreCheckpoint,
} from "@/lib/api";
import { parseCheckpointError } from "@/lib/checkpointError";
import { cn } from "@/lib/utils";

function formatTs(ts: number): string {
  try {
    return new Date(ts * 1000).toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

/**
 * Workspace checkpoints tab: full snapshots of the project tree (shadow git),
 * taken automatically before each agent turn and on demand. Restore rewinds
 * every file - including ones created or deleted since - after taking a
 * safety snapshot of the current state.
 */
export function DevCheckpointsPanel({
  token,
  sessionKey,
  onRestored,
}: {
  token: string;
  sessionKey: string;
  onRestored?: () => void;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  // A failure the gateway named (no WSL, distribution asleep) is worth a
  // sentence the user can act on, in their language; anything else is shown
  // as the gateway wrote it.
  const describeError = useCallback(
    (err: unknown) => {
      const failure = parseCheckpointError(err);
      return failure.code
        ? tx(`dev.checkpoints.errors.${failure.code}`, failure.message)
        : failure.message;
    },
    [tx],
  );

  const [checkpoints, setCheckpoints] = useState<WorkspaceCheckpoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [diffFiles, setDiffFiles] = useState<
    Record<string, { status: string; path: string }[]>
  >({});

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchCheckpoints(token, sessionKey)
      .then((payload) => setCheckpoints(payload.checkpoints))
      .catch((err) => setError(describeError(err)))
      .finally(() => setLoading(false));
  }, [token, sessionKey, describeError]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleCreate = useCallback(async () => {
    setBusy("create");
    setError(null);
    setNotice(null);
    try {
      const result = await createCheckpoint(token, sessionKey, label.trim());
      setLabel("");
      setNotice(
        result.created
          ? tx("dev.checkpoints.created", "Checkpoint created.")
          : tx("dev.checkpoints.unchanged", "No changes since last checkpoint."),
      );
      refresh();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(null);
    }
  }, [token, sessionKey, label, refresh, tx, describeError]);

  const [restoreTarget, setRestoreTarget] = useState<string | null>(null);

  const handleRestore = useCallback(
    async (id: string) => {
      setBusy(id);
      setError(null);
      setNotice(null);
      try {
        const result = await restoreCheckpoint(token, sessionKey, id);
        setNotice(
          tx("dev.checkpoints.restored", "Workspace restored.")
          + ` (${tx("dev.checkpoints.safety", "safety")}: ${result.safety})`,
        );
        refresh();
        onRestored?.();
      } catch (err) {
        setError(describeError(err));
      } finally {
        setBusy(null);
      }
    },
    [token, sessionKey, refresh, onRestored, tx, describeError],
  );

  const toggleDiff = useCallback(
    async (id: string) => {
      if (expanded === id) {
        setExpanded(null);
        return;
      }
      setExpanded(id);
      if (!diffFiles[id]) {
        try {
          const diff = await fetchCheckpointDiff(token, sessionKey, id);
          setDiffFiles((prev) => ({ ...prev, [id]: diff.files }));
        } catch {
          setDiffFiles((prev) => ({ ...prev, [id]: [] }));
        }
      }
    },
    [expanded, diffFiles, token, sessionKey],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ConfirmDialog
        open={restoreTarget !== null}
        title={tx("dev.checkpoints.restoreTitle", "Restore this checkpoint?")}
        description={tx(
          "dev.checkpoints.restoreConfirm",
          "Restore the whole workspace to this checkpoint? Current state is saved as a safety checkpoint first.",
        )}
        confirmLabel={tx("dev.checkpoints.restore", "Restore")}
        onCancel={() => setRestoreTarget(null)}
        onConfirm={() => {
          const id = restoreTarget;
          setRestoreTarget(null);
          if (id) void handleRestore(id);
        }}
      />
      <div className="flex shrink-0 items-center gap-1.5 px-2 pb-1.5 pt-1">
        <input
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && busy === null) void handleCreate();
          }}
          placeholder={tx("dev.checkpoints.labelPlaceholder", "Label (optional)")}
          className="h-7 min-w-0 flex-1 rounded-md border border-border/60 bg-background px-2 text-[11px] outline-none focus:ring-1 focus:ring-ring"
        />
        <button
          type="button"
          onClick={() => void handleCreate()}
          disabled={busy !== null}
          className="flex h-7 shrink-0 items-center gap-1 rounded-md bg-foreground px-2 text-[11px] font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy === "create" ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <Camera className="h-3 w-3" aria-hidden />
          )}
          {tx("dev.checkpoints.create", "Snapshot")}
        </button>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
          aria-label={tx("dev.checkpoints.refresh", "Refresh")}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
        </button>
      </div>
      {error ? (
        <p className="px-2 pb-1 text-[11px] text-destructive">{error}</p>
      ) : null}
      {notice ? (
        <p className="px-2 pb-1 text-[11px] text-emerald-600">{notice}</p>
      ) : null}
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {checkpoints.length === 0 && !loading ? (
          <p className="px-2 py-2 text-[12px] text-muted-foreground">
            {tx(
              "dev.checkpoints.empty",
              "No checkpoints yet. One is taken automatically before each agent turn.",
            )}
          </p>
        ) : null}
        {checkpoints.map((checkpoint) => (
          <div
            key={checkpoint.id}
            className="mb-1 rounded-md border border-border/40 bg-background/60"
          >
            <div className="flex items-center gap-1.5 px-2 py-1.5">
              <History
                className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                aria-hidden
              />
              <button
                type="button"
                onClick={() => void toggleDiff(checkpoint.id)}
                className="flex min-w-0 flex-1 flex-col text-left"
                title={checkpoint.id}
              >
                <span className="truncate text-[11.5px] text-foreground">
                  {checkpoint.label
                    || (checkpoint.reason === "pre-turn"
                      ? tx("dev.checkpoints.preTurn", "Before agent turn")
                      : checkpoint.reason === "pre-restore"
                        ? tx("dev.checkpoints.preRestore", "Before restore")
                        : tx("dev.checkpoints.manual", "Manual snapshot"))}
                </span>
                <span className="text-[10px] tabular-nums text-muted-foreground">
                  {formatTs(checkpoint.ts)} · {checkpoint.id.slice(0, 7)}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setRestoreTarget(checkpoint.id)}
                disabled={busy !== null}
                className="flex shrink-0 items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5 text-[10.5px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
              >
                {busy === checkpoint.id ? (
                  <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                ) : (
                  <Undo2 className="h-3 w-3" aria-hidden />
                )}
                {tx("dev.checkpoints.restore", "Restore")}
              </button>
            </div>
            {expanded === checkpoint.id ? (
              <div className="border-t border-border/40 px-2 py-1">
                {!diffFiles[checkpoint.id] ? (
                  <p className="py-1 text-[11px] text-muted-foreground">
                    {tx("dev.checkpoints.diffLoading", "Comparing…")}
                  </p>
                ) : diffFiles[checkpoint.id].length === 0 ? (
                  <p className="py-1 text-[11px] text-muted-foreground">
                    {tx(
                      "dev.checkpoints.diffEmpty",
                      "Identical to the current workspace.",
                    )}
                  </p>
                ) : (
                  diffFiles[checkpoint.id].slice(0, 50).map((file) => (
                    <div
                      key={file.path}
                      className="flex items-center gap-1.5 py-0.5"
                    >
                      <span
                        className={cn(
                          "w-4 text-center font-mono text-[10px] font-bold",
                          file.status === "D"
                            ? "text-destructive"
                            : file.status === "A"
                              ? "text-emerald-600"
                              : "text-muted-foreground",
                        )}
                      >
                        {file.status}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-foreground/90">
                        {file.path}
                      </span>
                    </div>
                  ))
                )}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}
