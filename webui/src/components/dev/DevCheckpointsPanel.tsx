// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { intlLocale } from "@/i18n/intl";
import { Camera, History, Loader2, Play, RefreshCw, Undo2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { WorkspaceCheckpoint } from "@/lib/api";
import {
  createCheckpoint,
  fetchCheckpointDiff,
  fetchCheckpoints,
  fetchMissionDiff,
  fetchMissionCompare,
  replayMissionStep,
  restoreCheckpoint,
  restoreMissionVersion,
} from "@/lib/api";
import type { MissionCompare, MissionDiff, MissionStrategy } from "@/lib/api";
import { parseCheckpointError } from "@/lib/checkpointError";
import { cn } from "@/lib/utils";

type TimelinePayload = {
  events: {
    kind: string;
    title: string;
    at: string;
    version: number | null;
    restorable?: boolean;
  }[];
  steps?: { id: string; title: string; status: string; retry_count: number }[];
  last_coherent: { checkpoint_id: string } | null;
};

function strategyLine(s: MissionStrategy, tx: (key: string, fallback: string) => string): string {
  const parts = [
    `v${s.version}`,
    `${s.criteria_proven}/${s.criteria_total} ${tx("dev.checkpoints.mission.criteria", "criteria")}`,
    `${s.steps_completed}/${s.steps_total} ${tx("dev.checkpoints.mission.stepsShort", "steps")}`,
    `${s.retries} ${tx("dev.checkpoints.mission.retries", "retries")}`,
  ];
  if (s.duration_s !== null) parts.push(`${s.duration_s}s`);
  parts.push(`${s.test_events} ${tx("dev.checkpoints.mission.tests", "tests")}`);
  return `${parts.join(" - ")}${s.decision ? ` (${s.decision})` : ""}`;
}

function formatTs(ts: number): string {
  try {
    return new Date(ts * 1000).toLocaleString(intlLocale(), {
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

  const [tl, setTl] = useState<TimelinePayload | null>(null);
  const [tlState, setTlState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [tlReload, setTlReload] = useState(0);
  const [missionTarget, setMissionTarget] = useState<number | null>(null);
  const [missionBusy, setMissionBusy] = useState(false);
  const [missionMsg, setMissionMsg] = useState<
    { ok: boolean; text: string } | null
  >(null);
  const [picked, setPicked] = useState<number[]>([]);
  const [mDiff, setMDiff] = useState<MissionDiff | null>(null);
  const [mDiffState, setMDiffState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [mCompare, setMCompare] = useState<MissionCompare | null>(null);
  const [replayTarget, setReplayTarget] = useState<string | null>(null);

  const handleReplay = useCallback(
    async (stepId: string) => {
      setMissionBusy(true);
      setMissionMsg(null);
      try {
        const r = await replayMissionStep(token, sessionKey, stepId, "Time Machine (UI)");
        setMissionMsg({
          ok: true,
          text: `${tx("dev.checkpoints.mission.replayed", "Step replayed")}: ${r.replayed} -> v${r.version}`,
        });
        setPicked([]);
        setMDiff(null);
        setMCompare(null);
        setMDiffState("idle");
        setTlReload((n) => n + 1);
      } catch (err) {
        setMissionMsg({ ok: false, text: describeError(err) });
      } finally {
        setMissionBusy(false);
      }
    },
    [token, sessionKey, tx, describeError],
  );

  const handleMissionRestore = useCallback(
    async (version: number) => {
      setMissionBusy(true);
      setMissionMsg(null);
      try {
        const r = await restoreMissionVersion(
          token,
          sessionKey,
          String(version),
          "Time Machine (UI)",
        );
        setMissionMsg({
          ok: true,
          text: `${tx("dev.checkpoints.mission.restored", "Mission restored")}: v${r.restored_from} -> v${r.version}`,
        });
        setPicked([]);
        setMDiff(null);
        setMCompare(null);
        setMDiffState("idle");
        setTlReload((n) => n + 1);
      } catch (err) {
        setMissionMsg({ ok: false, text: describeError(err) });
      } finally {
        setMissionBusy(false);
      }
    },
    [token, sessionKey, tx, describeError],
  );

  const togglePick = useCallback((version: number) => {
    setPicked((prev) =>
      prev.includes(version)
        ? prev.filter((v) => v !== version)
        : [...prev, version].slice(-2),
    );
    setMDiff(null);
    setMCompare(null);
    setMDiffState("idle");
  }, []);

  const handleCompare = useCallback(async () => {
    if (picked.length !== 2) return;
    const [a, b] = [...picked].sort((x, y) => x - y);
    setMDiffState("loading");
    try {
      const [diff, compare] = await Promise.all([
        fetchMissionDiff(token, sessionKey, a, b),
        fetchMissionCompare(token, sessionKey, a, b),
      ]);
      setMDiff(diff);
      setMCompare(compare);
      setMDiffState("ready");
    } catch {
      setMDiffState("error");
    }
  }, [picked, token, sessionKey]);

  useEffect(() => {
    let alive = true;
    setTlState("loading");
    fetch(
      `/api/sessions/${encodeURIComponent(sessionKey)}/checkpoints/timeline`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: TimelinePayload) => {
        if (!alive) return;
        setTl(data);
        setTlState("ready");
      })
      .catch(() => alive && setTlState("error"));
    return () => {
      alive = false;
    };
  }, [token, sessionKey, tlReload]);

  const diffLines: string[] = [];
  if (mDiff) {
    if (mDiff.goal) diffLines.push(`goal: ${mDiff.goal.from} -> ${mDiff.goal.to}`);
    if (mDiff.status)
      diffLines.push(`status: ${mDiff.status.from} -> ${mDiff.status.to}`);
    for (const [name, d] of [
      ["criteria", mDiff.criteria],
      ["facts", mDiff.facts],
      ["constraints", mDiff.constraints],
    ] as const) {
      d.added.forEach((x) => diffLines.push(`+ ${name}: ${x}`));
      d.removed.forEach((x) => diffLines.push(`- ${name}: ${x}`));
    }
    Object.entries(mDiff.evidence).forEach(([k, v]) =>
      diffLines.push(`proof ${k}: ${v.from ?? "-"} -> ${v.to ?? "-"}`),
    );
    mDiff.steps.added.forEach((x) => diffLines.push(`+ step: ${x}`));
    mDiff.steps.removed.forEach((x) => diffLines.push(`- step: ${x}`));
    mDiff.steps.changed.forEach((x) =>
      diffLines.push(`step ${x.title}: ${x.from} -> ${x.to}`),
    );
  }

  const timelineSection = (
    <div className="border-b border-border/50 px-3 py-2 text-[11px]">
      <div className="font-medium">
        {tx("dev.checkpoints.timeline.title", "Mission timeline")}
      </div>
      {tlState === "loading" ? (
        <p className="text-muted-foreground">
          {tx("dev.checkpoints.timeline.loading", "Loading timeline...")}
        </p>
      ) : tlState === "error" ? (
        <p className="text-destructive">
          {tx("dev.checkpoints.timeline.error", "Timeline unavailable.")}
        </p>
      ) : !tl || tl.events.length === 0 ? (
        <p className="text-muted-foreground">
          {tx("dev.checkpoints.timeline.empty", "No mission events yet.")}
        </p>
      ) : (
        <>
          {tl.last_coherent ? (
            <p className="text-muted-foreground">
              {tx("dev.checkpoints.timeline.lastCoherent", "Last coherent state")}
              {": "}
              {tl.last_coherent.checkpoint_id}
            </p>
          ) : null}
          <ul className="max-h-40 overflow-y-auto">
            {tl.events.slice(-30).map((ev, i) => (
              <li key={i} className="flex items-center gap-1.5">
                {ev.kind === "decision" && ev.version !== null ? (
                  <input
                    type="checkbox"
                    checked={picked.includes(ev.version)}
                    disabled={!ev.restorable}
                    onChange={() => ev.version !== null && togglePick(ev.version)}
                    aria-label={tx("dev.checkpoints.mission.pick", "Select for comparison")}
                    className="h-3 w-3 shrink-0"
                  />
                ) : null}
                <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                  {ev.version !== null ? `v${ev.version}` : ev.kind}
                </span>
                <span className="min-w-0 flex-1 truncate">{ev.title}</span>
                {ev.kind === "decision" && ev.version !== null && ev.restorable ? (
                  <button
                    type="button"
                    disabled={missionBusy}
                    onClick={() => setMissionTarget(ev.version)}
                    title={tx("dev.checkpoints.mission.restore", "Restore this version")}
                    className="flex h-5 shrink-0 items-center rounded px-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                  >
                    <Undo2 className="h-3 w-3" aria-hidden />
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
          <div className="mt-1 flex items-center gap-1.5">
            <button
              type="button"
              disabled={picked.length !== 2 || mDiffState === "loading"}
              onClick={() => void handleCompare()}
              className="flex h-6 items-center gap-1 rounded-md border border-border/60 px-2 transition-colors hover:bg-muted disabled:opacity-50"
            >
              {mDiffState === "loading" ? (
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              ) : null}
              {tx("dev.checkpoints.mission.compare", "Compare 2 versions")}
            </button>
            <span className="text-muted-foreground tabular-nums">
              {picked.length}/2
            </span>
          </div>
          {missionMsg ? (
            <p className={missionMsg.ok ? "text-muted-foreground" : "text-destructive"}>
              {missionMsg.text}
            </p>
          ) : null}
          {mDiffState === "error" ? (
            <p className="text-destructive">
              {tx("dev.checkpoints.mission.diffError", "Comparison unavailable.")}
            </p>
          ) : mDiffState === "ready" ? (
            diffLines.length === 0 ? (
              <p className="text-muted-foreground">
                {tx("dev.checkpoints.mission.noDiff", "No difference between these versions.")}
              </p>
            ) : (
              <ul className="mt-1 max-h-32 overflow-y-auto font-mono text-[10px]">
                {diffLines.map((line, i) => (
                  <li key={i} className="break-words">{line}</li>
                ))}
              </ul>
            )
          ) : null}
          {mDiffState === "ready" && mCompare ? (
            <div className="mt-1" role="region" aria-label={tx("dev.checkpoints.mission.strategies", "Strategy comparison")}>
              <div className="font-medium">
                {tx("dev.checkpoints.mission.strategies", "Strategy comparison")}
              </div>
              <ul className="font-mono text-[10px]">
                {[mCompare.a, mCompare.b].map((s) => (
                  <li key={s.version} className="break-words">
                    {mCompare.better === s.version ? "* " : "  "}
                    {strategyLine(s, tx)}
                    {s.measured === "snapshot"
                      ? ` [${tx("dev.checkpoints.mission.fromSnapshot", "start state only")}]`
                      : ""}
                  </li>
                ))}
              </ul>
              <p className="text-muted-foreground">
                {mCompare.better === null
                  ? tx("dev.checkpoints.mission.tie", "Same measured result.")
                  : `${tx("dev.checkpoints.mission.better", "Better strategy")}: v${mCompare.better} (${mCompare.reasons.join("; ")})`}
              </p>
            </div>
          ) : null}
        </>
      )}
      {tlState === "ready" && tl?.steps && tl.steps.length > 0 ? (
        <div className="mt-1.5">
          <div className="font-medium">{tx("dev.checkpoints.mission.steps", "Mission steps")}</div>
          <ul className="max-h-32 overflow-y-auto">
            {tl.steps.map((step) => (
              <li key={step.id} className="flex items-center gap-1.5">
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{step.status}</span>
                <span className="min-w-0 flex-1 truncate">{step.title}</span>
                {step.retry_count > 0 ? (
                  <span className="shrink-0 tabular-nums text-muted-foreground">x{step.retry_count}</span>
                ) : null}
                <button
                  type="button"
                  disabled={missionBusy}
                  onClick={() => setReplayTarget(step.id)}
                  aria-label={`${tx("dev.checkpoints.mission.replay", "Replay this step")} ${step.title}`}
                  title={tx("dev.checkpoints.mission.replay", "Replay this step")}
                  className="flex h-5 shrink-0 items-center rounded px-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                  <Play className="h-3 w-3" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {timelineSection}
      <ConfirmDialog
        open={replayTarget !== null}
        title={tx("dev.checkpoints.mission.replayTitle", "Replay this step?")}
        description={tx(
          "dev.checkpoints.mission.replayConfirm",
          "This step and the steps that depend on it go back to planned, as a new version. Earlier steps keep their results.",
        )}
        confirmLabel={tx("dev.checkpoints.mission.replayAction", "Replay")}
        onCancel={() => setReplayTarget(null)}
        onConfirm={() => {
          const id = replayTarget;
          setReplayTarget(null);
          if (id) void handleReplay(id);
        }}
      />
      <ConfirmDialog
        open={missionTarget !== null}
        title={tx("dev.checkpoints.mission.restoreTitle", "Restore this mission version?")}
        description={tx(
          "dev.checkpoints.mission.restoreConfirm",
          "The mission registry returns to this version as a new version. History is kept.",
        )}
        confirmLabel={tx("dev.checkpoints.restore", "Restore")}
        onCancel={() => setMissionTarget(null)}
        onConfirm={() => {
          const v = missionTarget;
          setMissionTarget(null);
          if (v !== null) void handleMissionRestore(v);
        }}
      />
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
                className="flex min-w-0 flex-1 flex-col text-start"
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
                  <Undo2 className="rtl:-scale-x-100 h-3 w-3" aria-hidden />
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
