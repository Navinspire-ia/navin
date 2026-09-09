// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type DragEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  Customizer,
  DefaultButton,
  Dropdown,
  Icon,
  IconButton,
  MessageBar,
  MessageBarType,
  PrimaryButton,
  ProgressIndicator,
  Slider,
  Spinner,
  SpinnerSize,
  TextField,
  TooltipHost,
  createTheme,
  type IDropdownOption,
} from "@fluentui/react";
import "@/lib/fluent-icons";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import {
  cancelMontageJob,
  deleteMontageTimeline,
  fetchMontageJob,
  fetchMontageTimeline,
  listMontageJobs,
  listMontageTimelines,
  montageJobIsTerminal,
  montageJobOutput,
  previewMontageTimeline,
  probeMontageMedia,
  renderMontageTimeline,
  resumeMontageJob,
  saveMontageTimeline,
  workspaceFileDownloadUrl,
  type MontageAsset,
  type MontageJob,
  type MontageJobSummary,
  type MontageTimeline as MontageTimelineDocument,
  type MontageTimelineSummary,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { MontageUpdate } from "@/lib/navin-client";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";
import { TimelineSpatialPreview } from "./TimelineSpatialPreview";
import {
  FPS_OPTIONS,
  TRANSITIONS,
  createHistory,
  createTimeline,
  formatTime,
  nextFreeOutput,
  outputCollision,
  sourceDuration,
  timelineDuration,
  timelineFingerprint,
  timelineReducer,
  transitionFits,
  visualDuration,
  visualFromAsset,
  type TimelinePatch,
} from "./timelineModel";

/**
 * Dark Fluent palette. Selected / hovered dropdown items use
 * `neutralDark` as text (`menuItemTextHovered`). Leaving it at the
 * default `#201f1e` made the chosen transition vanish on the navy list.
 */
const fluentTheme = createTheme({
  isInverted: true,
  palette: {
    themePrimary: "#EC4899",
    themeLighterAlt: "#1A0812",
    themeLighter: "#4C1430",
    themeLight: "#9D174D",
    themeTertiary: "#BE185D",
    themeSecondary: "#DB2777",
    themeDarkAlt: "#F472B6",
    themeDark: "#F9A8D4",
    themeDarker: "#FBCFE8",
    neutralLighterAlt: "#0F172A",
    neutralLighter: "#172033",
    neutralLight: "#1E293B",
    neutralQuaternaryAlt: "#273449",
    neutralQuaternary: "#334155",
    neutralTertiaryAlt: "#475569",
    neutralTertiary: "#94A3B8",
    neutralSecondary: "#CBD5E1",
    neutralPrimaryAlt: "#E2E8F0",
    neutralPrimary: "#F8FAFC",
    neutralDark: "#F8FAFC",
    black: "#F8FAFC",
    white: "#0F172A",
    blue: "#2563EB",
    green: "#34D399",
    red: "#F87171",
  },
  defaultFontStyle: { fontFamily: "Inter, sans-serif" },
});

const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };
const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const JOB_POLL_MS = 1000;
const RECENT_JOBS = 8;

type MontageTimelineProps = {
  token: string;
  sessionKey?: string | null;
  projectPath?: string | null;
  assets: MontageAsset[];
  /** Seed the chat composer with a prompt about the current timeline. */
  onSeed?: (text: string) => void;
};

type BusyAction = "load" | "save" | "preview" | "delete" | null;

const profileOptions: IDropdownOption[] = [
  { key: "1920x1080", text: "1080p - 16:9", data: { width: 1920, height: 1080 } },
  { key: "1080x1920", text: "Vertical - 9:16", data: { width: 1080, height: 1920 } },
  { key: "1080x1080", text: "Square - 1:1", data: { width: 1080, height: 1080 } },
  { key: "1080x1350", text: "Portrait - 4:5", data: { width: 1080, height: 1350 } },
  { key: "2560x1080", text: "Cinematic - 21:9", data: { width: 2560, height: 1080 } },
  { key: "3840x2160", text: "4K - 16:9", data: { width: 3840, height: 2160 } },
];

const fpsOptions: IDropdownOption[] = FPS_OPTIONS.map((fps) => ({ key: fps, text: `${fps} fps` }));

function fileName(path: string): string {
  return path.split("/").at(-1) || path;
}

function isSubtitle(asset: MontageAsset): boolean {
  return /\.(srt|vtt|ass)$/i.test(asset.path);
}

function jobLabel(job: MontageJob): string {
  const timeline = job.payload?.timeline;
  if (typeof timeline === "string" && timeline) return timeline;
  const output = montageJobOutput(job);
  return output ? fileName(output) : job.operation;
}

function relativeTime(iso: string | null | undefined, now: number): string {
  if (!iso) return "";
  const stamp = Date.parse(iso);
  if (Number.isNaN(stamp)) return "";
  const seconds = Math.max(0, Math.round((now - stamp) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} d`;
}

/** Fold a websocket job summary into the full manifest we already hold. */
function mergeJobSummary(job: MontageJob, summary: MontageJobSummary): MontageJob {
  return {
    ...job,
    status: summary.status ?? job.status,
    progress: summary.progress ?? job.progress,
    error: summary.error ?? null,
    updated_at: summary.updated_at ?? job.updated_at,
    completed_at: summary.completed_at ?? job.completed_at,
    cancel_requested: summary.cancel_requested ?? job.cancel_requested,
    active: montageJobIsTerminal(summary.status) ? false : job.active,
  };
}

function TrackLabel({ icon, children }: { icon: string; children: React.ReactNode }) {
  return (
    <div className="flex h-14 items-center gap-2 px-2 text-[11px] font-semibold text-slate-300">
      <Icon iconName={icon} className="text-[14px] text-slate-400" aria-hidden />
      <span className="truncate">{children}</span>
    </div>
  );
}

export function MontageTimeline({
  token,
  sessionKey,
  projectPath,
  assets,
  onSeed,
}: MontageTimelineProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const reducedMotion = useReducedMotion();
  const tx = useCallback(
    (key: string, fallback: string, values?: Record<string, string | number>) =>
      t(key, { defaultValue: fallback, ...values }),
    [t],
  );
  const workspace = useMemo(
    () => ({
      sessionKey: sessionKey || undefined,
      path: projectPath || undefined,
    }),
    [projectPath, sessionKey],
  );
  const [history, dispatch] = useReducer(
    timelineReducer,
    createTimeline(),
    createHistory,
  );
  const timeline = history.present;
  const [summaries, setSummaries] = useState<MontageTimelineSummary[]>([]);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [zoom, setZoom] = useState(64);
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [busy, setBusy] = useState<BusyAction>("load");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [savedFingerprint, setSavedFingerprint] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<MontageJob | null>(null);
  const [jobs, setJobs] = useState<MontageJob[]>([]);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [measuring, setMeasuring] = useState<string[]>([]);
  // Clip paths the probe reported as gone or unreadable (404): a stale export
  // that was deleted, a moved source... ffmpeg would only fail at render time.
  const [missing, setMissing] = useState<string[]>([]);
  // Bumped when the asset library changes so every clip is verified again.
  const [probeEpoch, setProbeEpoch] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const dragIndex = useRef<number | null>(null);
  const probeCache = useRef(new Map<string, number | null>());
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  const timelineRef = useRef(timeline);
  const dirtyRef = useRef(false);
  const savedFingerprintRef = useRef<string | null>(null);
  const activeJobRef = useRef<MontageJob | null>(null);

  const dirty = savedFingerprint != null && timelineFingerprint(timeline) !== savedFingerprint;
  timelineRef.current = timeline;
  dirtyRef.current = dirty;
  savedFingerprintRef.current = savedFingerprint;
  activeJobRef.current = activeJob;

  const markSaved = useCallback((document: MontageTimelineDocument) => {
    setSavedFingerprint(timelineFingerprint(document));
  }, []);

  const refreshList = useCallback(async () => {
    const result = await listMontageTimelines(token, workspace);
    setSummaries(result.timelines);
    return result.timelines;
  }, [token, workspace]);

  const refreshJobs = useCallback(async () => {
    try {
      const result = await listMontageJobs(token, { ...workspace, limit: RECENT_JOBS });
      setJobs(result.jobs);
      const running = result.jobs.find((job) => !montageJobIsTerminal(job.status) && job.active);
      if (running && !activeJobRef.current) setActiveJob(running);
    } catch {
      // The jobs panel is informational; a failed refresh keeps the last list.
    }
  }, [token, workspace]);

  useEffect(() => {
    let active = true;
    setBusy("load");
    setError(null);
    void refreshList()
      .then(async (rows) => {
        if (!active || rows.length === 0) {
          if (active) markSaved(createTimeline());
          return;
        }
        const loaded = await fetchMontageTimeline(token, rows[0].name, workspace);
        if (active) {
          dispatch({ type: "replace", timeline: loaded });
          markSaved(loaded);
        }
      })
      .catch((cause) => {
        if (active) {
          setError(
            cause instanceof Error
              ? cause.message
              : tx("montage.timeline.loadError", "Could not load timelines."),
          );
        }
      })
      .finally(() => {
        if (active) setBusy(null);
      });
    void refreshJobs();
    return () => {
      active = false;
    };
  }, [markSaved, refreshJobs, refreshList, token, tx, workspace]);

  // Probe every clip once per path: videos get their real length (trims and
  // crossfades are placed against the source, never a guess) and any clip
  // whose file is gone is flagged before ffmpeg would fail on it. The server
  // caches probes by path/size/mtime, so re-checking a saved clip is cheap.
  useEffect(() => {
    void probeEpoch;
    const pending = timeline.visuals.filter((visual) => !probeCache.current.has(visual.path));
    if (pending.length === 0) return;
    const paths = Array.from(new Set(pending.map((visual) => visual.path)));
    for (const path of paths) probeCache.current.set(path, null);
    setMeasuring((current) => Array.from(new Set([...current, ...paths])));
    // Only an unmount drops results: a `measure` dispatch re-runs this effect,
    // and cancelling there would lose the sibling probes still in flight.
    void Promise.all(
      paths.map(async (path) => {
        try {
          const info = await probeMontageMedia(token, path, workspace);
          if (!mountedRef.current) return;
          probeCache.current.set(path, info.duration);
          setMissing((current) => current.filter((item) => item !== path));
          if (info.duration && info.duration > 0) {
            dispatch({ type: "measure", path, duration: info.duration });
          }
        } catch (cause) {
          if (!mountedRef.current) return;
          probeCache.current.set(path, null);
          if (cause instanceof ApiError && cause.status === 404) {
            setMissing((current) => (current.includes(path) ? current : [...current, path]));
          }
        } finally {
          if (mountedRef.current) {
            setMeasuring((current) => current.filter((item) => item !== path));
          }
        }
      }),
    );
  }, [probeEpoch, timeline.visuals, token, workspace]);

  // Follow the active render: the websocket pushes progress, and a slow poll
  // covers a missed frame or a reconnect.
  const activeJobId = activeJob?.id ?? null;
  const activeJobTerminal = montageJobIsTerminal(activeJob?.status);
  useEffect(() => {
    if (!activeJobId || activeJobTerminal) return;
    const timer = window.setInterval(() => {
      fetchMontageJob(token, activeJobId, workspace)
        .then((job) => {
          setActiveJob((current) => (current?.id === job.id ? job : current));
          if (montageJobIsTerminal(job.status)) void refreshJobs();
        })
        .catch(() => undefined);
    }, JOB_POLL_MS);
    return () => window.clearInterval(timer);
  }, [activeJobId, activeJobTerminal, refreshJobs, token, workspace]);

  useEffect(() => {
    if (!activeJobId || activeJobTerminal) return;
    const timer = window.setInterval(() => setNow(Date.now()), JOB_POLL_MS);
    return () => window.clearInterval(timer);
  }, [activeJobId, activeJobTerminal]);

  // Live updates from the agent or another window: refresh what changed.
  useEffect(() => {
    return client.onMontageUpdate((update: MontageUpdate) => {
      if (
        update.projectPath &&
        projectPath &&
        update.projectPath.replace(/\/+$/, "") !== projectPath.replace(/\/+$/, "")
      ) {
        return;
      }
      if (update.kind === "job") {
        const summary = update.job;
        if (summary) {
          setActiveJob((current) => {
            if (current && current.id === summary.id) return mergeJobSummary(current, summary);
            if (!current && !montageJobIsTerminal(summary.status)) {
              // A render started elsewhere (the agent): adopt it so the user can follow it.
              void fetchMontageJob(token, summary.id, workspace)
                .then((job) => setActiveJob((latest) => latest ?? job))
                .catch(() => undefined);
            }
            return current;
          });
          if (montageJobIsTerminal(summary.status)) void refreshJobs();
        }
        return;
      }
      if (update.kind === "assets") {
        // A file appeared, vanished or was re-rendered: re-verify every clip.
        probeCache.current.clear();
        setProbeEpoch((epoch) => epoch + 1);
        return;
      }
      if (update.kind === "timeline") {
        void refreshList()
          .then(async (rows) => {
            const current = timelineRef.current;
            if (update.name !== current.name) return;
            const stillExists = rows.some((row) => row.name === current.name);
            if (!stillExists || dirtyRef.current) return;
            const loaded = await fetchMontageTimeline(token, current.name, workspace);
            // Our own save echoes back through the bus; only foreign edits are news.
            if (timelineFingerprint(loaded) === savedFingerprintRef.current) return;
            dispatch({ type: "replace", timeline: loaded });
            markSaved(loaded);
            setNotice(
              tx("montage.timeline.updatedByAgent", "Timeline updated by the agent."),
            );
          })
          .catch(() => undefined);
      }
    });
  }, [client, markSaved, projectPath, refreshJobs, refreshList, token, tx, workspace]);

  const mutateDocument = useCallback((patch: TimelinePatch) => {
    dispatch({ type: "patch", patch });
  }, []);

  const loadTimeline = async (name: string) => {
    setBusy("load");
    setError(null);
    setNotice(null);
    try {
      const loaded = await fetchMontageTimeline(token, name, workspace);
      dispatch({ type: "replace", timeline: loaded });
      markSaved(loaded);
      setSelectedIndex(null);
      setPreviewPath(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tx("montage.timeline.loadError", "Could not load timeline."));
    } finally {
      setBusy(null);
    }
  };

  const save = useCallback(async () => {
    if (!NAME_PATTERN.test(timeline.name) || timeline.visuals.length === 0) return false;
    setBusy("save");
    setError(null);
    setNotice(null);
    try {
      const saved = await saveMontageTimeline(token, timeline, workspace);
      dispatch({ type: "replace", timeline: saved });
      markSaved(saved);
      await refreshList();
      setNotice(tx("montage.timeline.saved", "Timeline saved."));
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tx("montage.timeline.saveError", "Could not save timeline."));
      return false;
    } finally {
      setBusy(null);
    }
  }, [markSaved, refreshList, timeline, token, tx, workspace]);

  const preview = async () => {
    if (!(await save())) return;
    setBusy("preview");
    try {
      const result = await previewMontageTimeline(token, timeline.name, workspace);
      setPreviewPath(typeof result.path === "string" ? result.path : null);
      setNotice(tx("montage.timeline.previewReady", "Backend preview is ready."));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tx("montage.timeline.previewError", "Preview failed."));
    } finally {
      setBusy(null);
    }
  };

  const render = async () => {
    if (!transitionFits(timeline)) {
      setError(
        tx(
          "montage.timeline.transitionTooLong",
          "The transition is longer than the shortest clip. Shorten it or the clip before rendering.",
        ),
      );
      return;
    }
    if (outputCollision(timeline) != null) {
      setError(
        tx(
          "montage.timeline.outputIsInput",
          "The output path is also a clip on the timeline. Rename the output or remove that clip before rendering.",
        ),
      );
      return;
    }
    if (!(await save())) return;
    setError(null);
    setNotice(null);
    try {
      const job = await renderMontageTimeline(token, timeline.name, {
        ...workspace,
        output: timeline.output,
      });
      setActiveJob(job);
      setJobsOpen(true);
      if (job.status === "failed") {
        setError(job.error || tx("montage.timeline.renderError", "Render failed."));
      }
      void refreshJobs();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tx("montage.timeline.renderError", "Render failed."));
    }
  };

  const cancelRender = async (job: MontageJob) => {
    try {
      const updated = await cancelMontageJob(token, job.id, workspace);
      setActiveJob((current) => (current?.id === updated.id ? { ...current, ...updated } : current));
      void refreshJobs();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tx("montage.timeline.cancelError", "Could not cancel the render."));
    }
  };

  const retryRender = async (job: MontageJob) => {
    setError(null);
    try {
      const updated = await resumeMontageJob(token, job.id, workspace);
      setActiveJob(updated);
      void refreshJobs();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tx("montage.timeline.renderError", "Render failed."));
    }
  };

  const removeTimeline = async () => {
    if (!summaries.some((item) => item.name === timeline.name)) return;
    setBusy("delete");
    setError(null);
    try {
      await deleteMontageTimeline(token, timeline.name, workspace);
      const rows = await refreshList();
      if (rows[0]) {
        await loadTimeline(rows[0].name);
      } else {
        const fresh = createTimeline();
        dispatch({ type: "replace", timeline: fresh });
        markSaved(fresh);
        setPreviewPath(null);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tx("montage.timeline.deleteError", "Could not delete timeline."));
    } finally {
      setBusy(null);
    }
  };

  const askAgent = () => {
    if (!onSeed) return;
    onSeed(
      tx(
        "montage.timeline.agentSeed",
        '/montage Open the timeline "{{name}}" with montage(action=timeline_get, name={{name}}), then ',
        { name: timeline.name },
      ),
    );
  };

  const onDrop = (event: DragEvent, to: number) => {
    event.preventDefault();
    const from = dragIndex.current;
    dragIndex.current = null;
    if (from != null) dispatch({ type: "reorder", from, to });
  };

  const beginTrim = (
    event: ReactPointerEvent,
    index: number,
    edge: "start" | "end",
  ) => {
    event.preventDefault();
    event.stopPropagation();
    const visual = timeline.visuals[index];
    const originX = event.clientX;
    const origin =
      visual.kind === "image"
        ? visualDuration(visual)
        : edge === "start"
          ? visual.start ?? 0
          : visual.end ?? visualDuration(visual);
    const onUp = (upEvent: PointerEvent) => {
      const delta = (upEvent.clientX - originX) / zoom;
      const value = visual.kind === "image" && edge === "start" ? origin - delta : origin + delta;
      dispatch({
        type: "trim",
        index,
        edge,
        value,
        snap: snapEnabled ? 0.1 : 0.001,
      });
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointerup", onUp, { once: true });
  };

  const trimKeyboard = (
    index: number,
    edge: "start" | "end",
    direction: -1 | 1,
  ) => {
    const visual = timeline.visuals[index];
    const current =
      visual.kind === "image"
        ? visualDuration(visual)
        : edge === "start"
          ? visual.start ?? 0
          : visual.end ?? visualDuration(visual);
    const delta = direction * (snapEnabled ? 0.1 : 0.01);
    dispatch({ type: "trim", index, edge, value: current + delta, snap: snapEnabled ? 0.1 : 0.001 });
  };

  const addAsset = (asset: MontageAsset, target?: "voice" | "music" | "subtitles") => {
    const cached = probeCache.current.get(asset.path);
    const visual = visualFromAsset(asset, cached != null ? { duration: cached } : null);
    if (visual) {
      dispatch({ type: "add", visual });
      setSelectedIndex(timeline.visuals.length);
      return;
    }
    if (target) dispatch({ type: "set-audio", track: target, path: asset.path });
  };

  const duration = timelineDuration(timeline);
  const audioAssets = assets.filter((asset) => asset.kind === "audio");
  const subtitleAssets = assets.filter(isSubtitle);
  const visualAssets = assets.filter((asset) => visualFromAsset(asset) != null);
  const fileUrl = (path: string | null | undefined) =>
    path && sessionKey ? workspaceFileDownloadUrl(token, sessionKey, path, "", projectPath) : null;
  const previewUrl = fileUrl(previewPath);
  const validName = NAME_PATTERN.test(timeline.name);
  const transitionOk = transitionFits(timeline);
  const collision = outputCollision(timeline);
  const missingClips = timeline.visuals.filter((visual) => missing.includes(visual.path));
  const dismissLabel = tx("common.dismiss", "Dismiss");
  const selectedVisual = selectedIndex != null ? timeline.visuals[selectedIndex] : null;
  const isSaved = summaries.some((item) => item.name === timeline.name);

  const transitionOptions: IDropdownOption[] = TRANSITIONS.map((key) => ({
    key,
    text:
      key === "none"
        ? tx("montage.timeline.transitionNone", "None (hard cut)")
        : key,
  }));

  return (
    <Customizer settings={{ theme: fluentTheme }}>
      <motion.div
        initial={reducedMotion ? false : { opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={spring}
        className="min-h-full bg-[#0F172A] p-3 text-white antialiased [&_.ms-Button]:min-h-10 [&_.ms-Button]:cursor-pointer"
        data-testid="montage-timeline"
      >
        <div className="mx-auto flex max-w-[1680px] flex-col gap-3">
          <header className="flex flex-wrap items-end gap-2 rounded-2xl bg-[#131d31] p-3 shadow-[0_18px_45px_rgba(0,0,0,0.26),inset_0_1px_rgba(255,255,255,0.05)]">
            <TextField
              label={tx("montage.timeline.name", "Timeline name")}
              value={timeline.name}
              errorMessage={
                validName ? undefined : tx("montage.timeline.nameHint", "Use lowercase letters, numbers, _ or -.")
              }
              onChange={(_, value) => {
                const name = value ?? "";
                mutateDocument({
                  name,
                  output: `marketing/montage/exports/${name || "montage"}.mp4`,
                });
              }}
              styles={{ root: { minWidth: 190, flex: "1 1 220px" } }}
            />
            <Dropdown
              label={tx("montage.timeline.savedTimelines", "Saved timelines")}
              placeholder={tx("montage.timeline.choose", "Choose")}
              options={summaries.map((item) => ({
                key: item.name,
                text: `${item.name} (${item.visuals})`,
              }))}
              selectedKey={isSaved ? timeline.name : undefined}
              onChange={(_, option) => option && void loadTimeline(String(option.key))}
              styles={{ root: { minWidth: 190, flex: "1 1 220px" } }}
            />
            <div className="flex min-h-10 flex-wrap items-center gap-1">
              <DefaultButton
                iconProps={{ iconName: "Add" }}
                text={tx("montage.timeline.new", "New")}
                disabled={busy != null}
                onClick={() => {
                  const fresh = createTimeline(`montage-${Date.now()}`);
                  dispatch({ type: "replace", timeline: fresh });
                  markSaved(fresh);
                  setSelectedIndex(null);
                  setPreviewPath(null);
                  setNotice(null);
                }}
              />
              <TooltipHost content={tx("montage.timeline.undo", "Undo")}>
                <IconButton
                  iconProps={{ iconName: "Undo" }}
                  ariaLabel={tx("montage.timeline.undo", "Undo")}
                  disabled={history.past.length === 0 || busy != null}
                  onClick={() => dispatch({ type: "undo" })}
                />
              </TooltipHost>
              <TooltipHost content={tx("montage.timeline.redo", "Redo")}>
                <IconButton
                  iconProps={{ iconName: "Redo" }}
                  ariaLabel={tx("montage.timeline.redo", "Redo")}
                  disabled={history.future.length === 0 || busy != null}
                  onClick={() => dispatch({ type: "redo" })}
                />
              </TooltipHost>
              <PrimaryButton
                iconProps={{ iconName: "Save" }}
                text={
                  dirty
                    ? tx("montage.timeline.saveChanges", "Save changes")
                    : tx("montage.timeline.save", "Save")
                }
                disabled={!validName || timeline.visuals.length === 0 || busy != null}
                onClick={() => void save()}
              />
              <DefaultButton
                iconProps={{ iconName: "Photo2" }}
                text={tx("montage.timeline.preview", "Preview")}
                disabled={!validName || timeline.visuals.length === 0 || busy != null}
                onClick={() => void preview()}
              />
              <PrimaryButton
                iconProps={{ iconName: "Play" }}
                text={tx("montage.timeline.render", "Render")}
                disabled={
                  !validName ||
                  timeline.visuals.length === 0 ||
                  collision != null ||
                  missingClips.length > 0 ||
                  busy != null ||
                  (activeJob != null && !activeJobTerminal)
                }
                onClick={() => void render()}
                data-testid="montage-render"
              />
              {onSeed ? (
                <DefaultButton
                  iconProps={{ iconName: "Robot" }}
                  text={tx("montage.timeline.askAgent", "Ask the agent")}
                  disabled={!validName}
                  onClick={askAgent}
                  data-testid="montage-ask-agent"
                />
              ) : null}
              <IconButton
                iconProps={{ iconName: "Delete" }}
                ariaLabel={tx("montage.timeline.delete", "Delete")}
                disabled={!isSaved || busy != null}
                onClick={() => void removeTimeline()}
              />
            </div>
          </header>

          {busy ? (
            <div className="rounded-xl bg-[#172033] p-2">
              <Spinner label={tx(`montage.timeline.busy.${busy}`, "Working...")} />
            </div>
          ) : null}
          {error ? (
            <MessageBar
              messageBarType={MessageBarType.error}
              onDismiss={() => setError(null)}
              dismissButtonAriaLabel={dismissLabel}
            >
              {error}
            </MessageBar>
          ) : null}
          {notice ? (
            <MessageBar
              messageBarType={MessageBarType.success}
              onDismiss={() => setNotice(null)}
              dismissButtonAriaLabel={dismissLabel}
            >
              {notice}
            </MessageBar>
          ) : null}
          {!transitionOk ? (
            <MessageBar
              messageBarType={MessageBarType.warning}
              actions={
                <DefaultButton
                  text={tx("montage.timeline.shortenTransition", "Shorten transition")}
                  onClick={() => {
                    const shortest = Math.min(...timeline.visuals.map(visualDuration));
                    mutateDocument({
                      transition_duration: Math.max(0.1, Math.round((shortest / 2) * 100) / 100),
                    });
                  }}
                />
              }
            >
              {tx(
                "montage.timeline.transitionTooLong",
                "The transition is longer than the shortest clip. Shorten it or the clip before rendering.",
              )}
            </MessageBar>
          ) : null}
          {collision != null ? (
            <MessageBar
              messageBarType={MessageBarType.warning}
              data-testid="montage-output-collision"
              actions={
                <DefaultButton
                  text={tx("montage.timeline.renameOutput", "Rename output")}
                  onClick={() => mutateDocument({ output: nextFreeOutput(timeline) })}
                />
              }
            >
              {tx(
                "montage.timeline.outputIsInput",
                "The output path is also a clip on the timeline. Rename the output or remove that clip before rendering.",
              )}
            </MessageBar>
          ) : null}
          {missingClips.length > 0 ? (
            <MessageBar
              messageBarType={MessageBarType.severeWarning}
              data-testid="montage-missing-clips"
              actions={
                <DefaultButton
                  text={tx("montage.timeline.removeMissing", "Remove missing clips")}
                  onClick={() => {
                    mutateDocument({
                      visuals: timeline.visuals.filter((visual) => !missing.includes(visual.path)),
                    });
                    setSelectedIndex(null);
                  }}
                />
              }
            >
              {tx("montage.timeline.missingClips", "These files are missing or unreadable: {{files}}", {
                files: missingClips.map((visual) => fileName(visual.path)).join(", "),
              })}
            </MessageBar>
          ) : null}

          <AnimatePresence initial={false}>
            {activeJob ? (
              <motion.section
                key={activeJob.id}
                initial={reducedMotion ? false : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={spring}
                className="rounded-2xl bg-[#131d31] p-3 shadow-[0_14px_38px_rgba(0,0,0,0.22),inset_0_1px_rgba(255,255,255,0.04)]"
                data-testid="montage-render-job"
                aria-live="polite"
              >
                <RenderJobCard
                  job={activeJob}
                  now={now}
                  tx={tx}
                  fileUrl={fileUrl}
                  onCancel={() => void cancelRender(activeJob)}
                  onRetry={() => void retryRender(activeJob)}
                  onDismiss={() => setActiveJob(null)}
                />
              </motion.section>
            ) : null}
          </AnimatePresence>

          <div className="grid min-h-0 gap-3 xl:grid-cols-[minmax(0,1fr)_320px]">
            <main className="min-w-0 rounded-2xl bg-[#111b2e] p-3 shadow-[0_14px_38px_rgba(0,0,0,0.24),inset_0_1px_rgba(255,255,255,0.04)]">
              <div className="mb-3 flex flex-wrap items-center gap-3">
                <div className="min-w-[180px] flex-1">
                  <Slider
                    label={tx("montage.timeline.zoom", "Zoom")}
                    min={24}
                    max={120}
                    step={4}
                    value={zoom}
                    showValue
                    onChange={setZoom}
                  />
                </div>
                <TooltipHost content={tx("montage.timeline.snapping", "Snapping")}>
                  <IconButton
                    toggle
                    checked={snapEnabled}
                    iconProps={{ iconName: "Magnet" }}
                    ariaLabel={tx("montage.timeline.snapping", "Snapping")}
                    onClick={() => setSnapEnabled((value) => !value)}
                    styles={{ rootChecked: { background: "#2563EB", color: "#fff" } }}
                  />
                </TooltipHost>
                <span className="font-mono text-xs tabular-nums text-slate-300" data-testid="montage-duration">
                  {formatTime(duration)} · {timeline.width}x{timeline.height} · {timeline.fps} fps
                  {timeline.transition !== "none" && timeline.visuals.length > 1
                    ? ` · ${timeline.transition} ${timeline.transition_duration.toFixed(2)}s`
                    : ""}
                </span>
              </div>

              {timeline.visuals.length === 0 ? (
                <div className="flex min-h-52 flex-col items-center justify-center rounded-xl border border-dashed border-white/15 px-5 text-center">
                  <Icon iconName="Video" className="mb-2 text-3xl text-pink-400" aria-hidden />
                  <h2 className="text-wrap-balance text-sm font-semibold">
                    {tx("montage.timeline.empty", "Add a real image or video to start.")}
                  </h2>
                  <p className="mt-1 max-w-md text-pretty text-xs text-slate-400">
                    {tx("montage.timeline.emptyHint", "Use assets from the project library. The saved timeline is rendered by the Montage backend.")}
                  </p>
                </div>
              ) : (
                <div className="overflow-x-auto rounded-xl bg-[#080d19] shadow-[inset_0_1px_rgba(255,255,255,0.04)]">
                  <div
                    className="grid min-w-max grid-cols-[96px_1fr]"
                    style={{ width: Math.max(650, 96 + duration * zoom) }}
                  >
                    <div className="h-7 border-r border-white/10" />
                    <div className="relative h-7 border-b border-white/10 font-mono text-[10px] tabular-nums text-slate-500">
                      {Array.from({ length: Math.ceil(duration) + 1 }, (_, second) => (
                        <span
                          key={second}
                          className="absolute top-1"
                          style={{ left: second * zoom }}
                        >
                          {second}s
                        </span>
                      ))}
                    </div>
                    <TrackLabel icon="Video">{tx("montage.timeline.visualTrack", "Video / image")}</TrackLabel>
                    <div className="flex h-14 items-center border-b border-white/5">
                      {timeline.visuals.map((visual, index) => {
                        const source = sourceDuration(visual);
                        const isMeasuring =
                          visual.kind === "video" && source == null && measuring.includes(visual.path);
                        const isMissing = missing.includes(visual.path);
                        const overlap =
                          index > 0 && timeline.transition !== "none" ? timeline.transition_duration * zoom : 0;
                        return (
                          <motion.div
                            layout={!reducedMotion}
                            key={`${visual.path}-${index}`}
                            draggable
                            onDragStart={() => {
                              dragIndex.current = index;
                            }}
                            onDragOver={(event) => event.preventDefault()}
                            onDrop={(event) => onDrop(event, index)}
                            onClick={() => setSelectedIndex(index)}
                            className={cn(
                              "group relative flex h-11 cursor-grab items-center overflow-hidden rounded-lg px-3 text-left shadow-[inset_0_1px_rgba(255,255,255,0.12)] focus-within:ring-2 focus-within:ring-pink-400 active:cursor-grabbing",
                              selectedIndex === index
                                ? "bg-pink-500 text-white ring-2 ring-pink-300"
                                : isMissing
                                  ? "bg-rose-900/80 text-rose-100 outline outline-1 outline-dashed outline-rose-400"
                                  : visual.kind === "video"
                                    ? "bg-blue-600 text-white"
                                    : "bg-sky-500 text-slate-950",
                            )}
                            data-missing={isMissing ? "true" : undefined}
                            style={{
                              width: Math.max(56, visualDuration(visual) * zoom),
                              marginLeft: overlap ? -Math.min(overlap, 40) : 0,
                            }}
                            data-testid={`timeline-clip-${index}`}
                          >
                            <button
                              type="button"
                              className="min-w-0 flex-1 cursor-grab text-left focus:outline-none"
                              aria-label={`${fileName(visual.path)}, ${formatTime(visualDuration(visual))}`}
                            >
                              <span className="block truncate text-[11px] font-semibold">{fileName(visual.path)}</span>
                              <span className="flex items-center gap-1 font-mono text-[10px] tabular-nums opacity-80">
                                {formatTime(visualDuration(visual))}
                                {visual.kind === "video" && source != null ? (
                                  <span className="opacity-70">
                                    {`[${(visual.start ?? 0).toFixed(1)}-${(visual.end ?? source).toFixed(1)} / ${source.toFixed(1)}s]`}
                                  </span>
                                ) : null}
                                {isMeasuring ? (
                                  <Spinner size={SpinnerSize.xSmall} ariaLabel={tx("montage.timeline.measuring", "Measuring clip")} />
                                ) : isMissing ? (
                                  <span className="font-sans font-semibold uppercase tracking-wide">
                                    {tx("montage.timeline.missing", "Missing")}
                                  </span>
                                ) : visual.kind === "video" && source == null ? (
                                  <span title={tx("montage.timeline.unmeasured", "Length unknown - measured at render time")}>?</span>
                                ) : null}
                              </span>
                            </button>
                            {(["start", "end"] as const).map((edge) => (
                              <button
                                key={edge}
                                type="button"
                                className={cn(
                                  "absolute inset-y-0 w-3 cursor-ew-resize bg-white/20 opacity-70 transition-opacity hover:opacity-100 focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-white",
                                  edge === "start" ? "left-0" : "right-0",
                                )}
                                aria-label={tx(
                                  `montage.timeline.trim.${edge}`,
                                  edge === "start" ? "Trim clip start" : "Trim clip end",
                                )}
                                onPointerDown={(event) => beginTrim(event, index, edge)}
                                onKeyDown={(event) => {
                                  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                                    event.preventDefault();
                                    trimKeyboard(index, edge, event.key === "ArrowLeft" ? -1 : 1);
                                  }
                                }}
                              />
                            ))}
                          </motion.div>
                        );
                      })}
                    </div>
                    <TrackLabel icon="Microphone">{tx("montage.timeline.voice", "Voice")}</TrackLabel>
                    <AuxTrack
                      path={timeline.voice}
                      color="bg-violet-500"
                      zoom={zoom}
                      duration={duration}
                      clearLabel={tx("montage.timeline.clearVoice", "Remove voice")}
                      onClear={() => dispatch({ type: "set-audio", track: "voice", path: null })}
                    />
                    <TrackLabel icon="MusicInCollection">{tx("montage.timeline.music", "Music")}</TrackLabel>
                    <AuxTrack
                      path={timeline.music}
                      color="bg-emerald-500"
                      zoom={zoom}
                      duration={duration}
                      clearLabel={tx("montage.timeline.clearMusic", "Remove music")}
                      onClear={() => dispatch({ type: "set-audio", track: "music", path: null })}
                    />
                    <TrackLabel icon="ClosedCaption">{tx("montage.timeline.subtitles", "Subtitles")}</TrackLabel>
                    <AuxTrack
                      path={timeline.subtitles}
                      color="bg-amber-400 text-slate-950"
                      zoom={zoom}
                      duration={duration}
                      clearLabel={tx("montage.timeline.clearSubtitles", "Remove subtitles")}
                      onClear={() => dispatch({ type: "set-audio", track: "subtitles", path: null })}
                    />
                  </div>
                </div>
              )}

              <AnimatePresence initial={false}>
                {selectedVisual && selectedIndex != null ? (
                  <motion.div
                    initial={reducedMotion ? false : { opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={spring}
                    className="mt-3 flex flex-wrap items-end gap-3 rounded-2xl bg-[#131d31] p-3"
                    data-testid="montage-clip-inspector"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-semibold" title={selectedVisual.path}>
                        {fileName(selectedVisual.path)}
                      </p>
                      <p className="font-mono text-[11px] tabular-nums text-slate-400">
                        {selectedVisual.kind === "video"
                          ? sourceDuration(selectedVisual) != null
                            ? tx("montage.timeline.clipSource", "Source {{seconds}}s", {
                                seconds: sourceDuration(selectedVisual)!.toFixed(2),
                              })
                            : tx("montage.timeline.unmeasured", "Length unknown - measured at render time")
                          : tx("montage.timeline.stillDuration", "Still image")}
                      </p>
                    </div>
                    {selectedVisual.kind === "image" ? (
                      <TextField
                        label={tx("montage.timeline.duration", "Duration (s)")}
                        type="number"
                        min={0.1}
                        step={0.1}
                        value={String(selectedVisual.duration ?? 4)}
                        onChange={(_, value) => {
                          const parsed = Number(value);
                          if (Number.isFinite(parsed)) {
                            dispatch({ type: "trim", index: selectedIndex, edge: "end", value: parsed, snap: 0.01 });
                          }
                        }}
                        styles={{ root: { width: 120 } }}
                      />
                    ) : (
                      <>
                        <TextField
                          label={tx("montage.timeline.trimStart", "Start (s)")}
                          type="number"
                          min={0}
                          step={0.1}
                          value={String(selectedVisual.start ?? 0)}
                          onChange={(_, value) => {
                            const parsed = Number(value);
                            if (Number.isFinite(parsed)) {
                              dispatch({ type: "trim", index: selectedIndex, edge: "start", value: parsed, snap: 0.01 });
                            }
                          }}
                          styles={{ root: { width: 110 } }}
                        />
                        <TextField
                          label={tx("montage.timeline.trimEnd", "End (s)")}
                          type="number"
                          min={0.1}
                          step={0.1}
                          value={String(selectedVisual.end ?? sourceDuration(selectedVisual) ?? 4)}
                          onChange={(_, value) => {
                            const parsed = Number(value);
                            if (Number.isFinite(parsed)) {
                              dispatch({ type: "trim", index: selectedIndex, edge: "end", value: parsed, snap: 0.01 });
                            }
                          }}
                          styles={{ root: { width: 110 } }}
                        />
                      </>
                    )}
                    <DefaultButton
                      iconProps={{ iconName: "Delete" }}
                      text={tx("montage.timeline.removeClip", "Remove clip")}
                      onClick={() => {
                        dispatch({ type: "remove", index: selectedIndex });
                        setSelectedIndex(null);
                      }}
                    />
                  </motion.div>
                ) : null}
              </AnimatePresence>
            </main>

            <aside className="flex min-w-0 flex-col gap-3">
              <section className="rounded-2xl bg-[#131d31] p-3 shadow-[0_14px_38px_rgba(0,0,0,0.22),inset_0_1px_rgba(255,255,255,0.04)]">
                <h2 className="mb-2 flex items-center gap-2 text-xs font-semibold">
                  <Icon iconName="Settings" className="text-pink-400" aria-hidden />
                  {tx("montage.timeline.editSettings", "Edit settings")}
                </h2>
                <Dropdown
                  label={tx("montage.timeline.transition", "Transition")}
                  options={transitionOptions}
                  selectedKey={TRANSITIONS.includes(timeline.transition as (typeof TRANSITIONS)[number]) ? timeline.transition : "none"}
                  onChange={(_, option) => option && mutateDocument({ transition: String(option.key) })}
                  data-testid="montage-transition"
                />
                {timeline.transition !== "none" ? (
                  <Slider
                    label={tx("montage.timeline.transitionDuration", "Transition length (s)")}
                    min={0.1}
                    max={2}
                    step={0.05}
                    value={timeline.transition_duration}
                    showValue
                    valueFormat={(value) => `${value.toFixed(2)}s`}
                    onChange={(value) => mutateDocument({ transition_duration: Math.round(value * 100) / 100 })}
                  />
                ) : null}
                <Slider
                  label={tx("montage.timeline.musicGain", "Music level (dB)")}
                  min={-40}
                  max={0}
                  step={1}
                  value={timeline.music_gain_db}
                  showValue
                  valueFormat={(value) => `${value} dB`}
                  disabled={!timeline.music}
                  onChange={(value) => mutateDocument({ music_gain_db: value })}
                />
                <Dropdown
                  label={tx("montage.timeline.fps", "Frame rate")}
                  options={fpsOptions}
                  selectedKey={FPS_OPTIONS.includes(timeline.fps as (typeof FPS_OPTIONS)[number]) ? timeline.fps : undefined}
                  placeholder={`${timeline.fps} fps`}
                  onChange={(_, option) => option && mutateDocument({ fps: Number(option.key) })}
                />
              </section>

              <section className="rounded-2xl bg-[#131d31] p-3">
                <h2 className="mb-2 text-xs font-semibold">{tx("montage.timeline.output", "Output")}</h2>
                <Dropdown
                  label={tx("montage.timeline.format", "Format")}
                  options={profileOptions}
                  selectedKey={`${timeline.width}x${timeline.height}`}
                  placeholder={`${timeline.width}x${timeline.height}`}
                  onChange={(_, option) => {
                    const data = option?.data as { width: number; height: number } | undefined;
                    if (data) mutateDocument(data);
                  }}
                />
                <TextField
                  label={tx("montage.timeline.outputPath", "Output path")}
                  value={timeline.output}
                  onChange={(_, value) => mutateDocument({ output: value ?? "" })}
                />
              </section>

              <section className="rounded-2xl bg-[#131d31] p-3">
                <h2 className="mb-2 flex items-center gap-2 text-xs font-semibold">
                  <Icon iconName="OpenFolderHorizontal" className="text-blue-400" aria-hidden />
                  {tx("montage.timeline.assets", "Project assets")}
                </h2>
                {assets.length === 0 ? (
                  <p className="rounded-xl border border-dashed border-white/15 p-4 text-center text-xs text-slate-400">
                    {tx("montage.timeline.assetsEmpty", "No compatible project assets found.")}
                  </p>
                ) : (
                  <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
                    {visualAssets.map((asset) => (
                      <AssetRow
                        key={asset.path}
                        asset={asset}
                        action={tx("montage.timeline.add", "Add")}
                        icon={(asset.visual ?? asset.kind) === "video" ? "Video" : "Photo2"}
                        detail={
                          asset.duration != null
                            ? formatTime(asset.duration)
                            : asset.width && asset.height
                              ? `${asset.width}x${asset.height}`
                              : undefined
                        }
                        onAction={() => addAsset(asset)}
                      />
                    ))}
                    {audioAssets.map((asset) => (
                      <div key={asset.path} className="rounded-xl bg-[#0b1323] p-2">
                        <p className="truncate text-[11px] font-medium">{asset.name}</p>
                        <div className="mt-1 flex gap-1">
                          <DefaultButton
                            iconProps={{ iconName: "Microphone" }}
                            text={tx("montage.timeline.asVoice", "Voice")}
                            onClick={() => addAsset(asset, "voice")}
                          />
                          <DefaultButton
                            iconProps={{ iconName: "MusicInCollection" }}
                            text={tx("montage.timeline.asMusic", "Music")}
                            onClick={() => addAsset(asset, "music")}
                          />
                        </div>
                      </div>
                    ))}
                    {subtitleAssets.map((asset) => (
                      <AssetRow
                        key={asset.path}
                        asset={asset}
                        action={tx("montage.timeline.use", "Use")}
                        icon="ClosedCaption"
                        onAction={() => addAsset(asset, "subtitles")}
                      />
                    ))}
                  </div>
                )}
              </section>

              <section className="rounded-2xl bg-[#131d31] p-3" data-testid="montage-jobs">
                <button
                  type="button"
                  className="flex w-full items-center gap-2 text-left text-xs font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-pink-400"
                  onClick={() => setJobsOpen((open) => !open)}
                  aria-expanded={jobsOpen}
                >
                  <Icon iconName={jobsOpen ? "ChevronDown" : "ChevronRight"} className="text-[12px] text-slate-400" aria-hidden />
                  <Icon iconName="History" className="text-blue-400" aria-hidden />
                  <span className="flex-1">{tx("montage.timeline.jobs", "Renders")}</span>
                  {jobs.length > 0 ? (
                    <span className="rounded-full bg-white/10 px-2 py-0.5 font-mono text-[10px] tabular-nums">{jobs.length}</span>
                  ) : null}
                </button>
                {jobsOpen ? (
                  jobs.length === 0 ? (
                    <p className="mt-2 rounded-xl border border-dashed border-white/15 p-3 text-center text-xs text-slate-400">
                      {tx("montage.timeline.jobsEmpty", "No render yet. Save, then Render.")}
                    </p>
                  ) : (
                    <ul className="mt-2 space-y-1">
                      {jobs.map((job) => (
                        <li key={job.id} className="flex items-center gap-2 rounded-xl bg-[#0b1323] p-2 text-[11px]">
                          <StatusPill status={job.status} tx={tx} />
                          <button
                            type="button"
                            className="min-w-0 flex-1 truncate text-left hover:underline focus:outline-none"
                            title={montageJobOutput(job) ?? job.id}
                            onClick={() => setActiveJob(job)}
                          >
                            {jobLabel(job)}
                          </button>
                          <span className="font-mono text-[10px] tabular-nums text-slate-500">
                            {relativeTime(job.updated_at, now)}
                          </span>
                          {!montageJobIsTerminal(job.status) ? (
                            <IconButton
                              iconProps={{ iconName: "Cancel" }}
                              ariaLabel={tx("montage.timeline.cancelRender", "Cancel render")}
                              onClick={() => void cancelRender(job)}
                            />
                          ) : job.status === "completed" && fileUrl(montageJobOutput(job)) ? (
                            <IconButton
                              iconProps={{ iconName: "OpenInNewWindow" }}
                              ariaLabel={tx("montage.timeline.openOutput", "Open output")}
                              href={fileUrl(montageJobOutput(job)) ?? undefined}
                              target="_blank"
                              rel="noreferrer"
                            />
                          ) : job.status !== "completed" ? (
                            <IconButton
                              iconProps={{ iconName: "Refresh" }}
                              ariaLabel={tx("montage.timeline.retryRender", "Retry render")}
                              onClick={() => void retryRender(job)}
                            />
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )
                ) : null}
              </section>

              {previewUrl ? (
                <section className="rounded-2xl bg-[#131d31] p-3">
                  <h2 className="mb-2 text-xs font-semibold">{tx("montage.timeline.backendPreview", "Backend preview")}</h2>
                  <img
                    src={previewUrl}
                    alt={tx("montage.timeline.previewAlt", "Rendered timeline preview")}
                    className="aspect-video w-full rounded-xl object-cover outline outline-1 outline-white/10"
                  />
                </section>
              ) : null}

              <section className="rounded-2xl bg-[#131d31] p-3 shadow-[0_14px_38px_rgba(0,0,0,0.22),inset_0_1px_rgba(255,255,255,0.04)]">
                <h2 className="mb-2 flex items-center gap-2 text-xs font-semibold">
                  <Icon iconName="CubeShape" className="text-pink-400" aria-hidden />
                  {tx("montage.timeline.spatial", "Spatial track map")}
                </h2>
                <TimelineSpatialPreview
                  timeline={timeline}
                  selectedIndex={selectedIndex}
                  label={tx("montage.timeline.spatialLabel", "Interactive 3D overview of montage tracks")}
                />
              </section>
            </aside>
          </div>
        </div>
      </motion.div>
    </Customizer>
  );
}

type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

function StatusPill({ status, tx }: { status: string; tx: Tx }) {
  const tone =
    status === "completed"
      ? "bg-emerald-500/20 text-emerald-300"
      : status === "failed"
        ? "bg-rose-500/20 text-rose-300"
        : status === "cancelled"
          ? "bg-slate-500/20 text-slate-300"
          : "bg-blue-500/20 text-blue-300";
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide", tone)}>
      {tx(`montage.timeline.status.${status}`, status)}
    </span>
  );
}

function RenderJobCard({
  job,
  now,
  tx,
  fileUrl,
  onCancel,
  onRetry,
  onDismiss,
}: {
  job: MontageJob;
  now: number;
  tx: Tx;
  fileUrl: (path: string | null | undefined) => string | null;
  onCancel: () => void;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const terminal = montageJobIsTerminal(job.status);
  const progress = job.progress ?? null;
  const fraction = progress?.fraction ?? null;
  const output = montageJobOutput(job);
  const outputUrl = fileUrl(output);
  const started = job.started_at ? Date.parse(job.started_at) : NaN;
  const elapsed = Number.isNaN(started) ? null : Math.max(0, Math.round((now - started) / 1000));
  const eta =
    fraction != null && fraction > 0.02 && elapsed != null && !terminal
      ? Math.max(0, Math.round((elapsed / fraction) * (1 - fraction)))
      : null;
  const result = job.steps.at(-1)?.result ?? null;
  const durationS = typeof result?.duration_s === "number" ? result.duration_s : null;
  const sizeBytes = typeof result?.size_bytes === "number" ? result.size_bytes : null;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill status={job.status} tx={tx} />
        <span className="min-w-0 flex-1 truncate text-xs font-semibold" title={output ?? job.id}>
          {output ? fileName(output) : jobLabel(job)}
        </span>
        {!terminal ? (
          <>
            <span className="font-mono text-[11px] tabular-nums text-slate-300">
              {fraction != null
                ? `${Math.round(fraction * 100)}%`
                : formatTime(progress?.out_time_s ?? 0)}
              {progress?.speed ? ` · ${progress.speed.toFixed(1)}x` : ""}
              {eta != null ? ` · ${tx("montage.timeline.eta", "~{{seconds}}s left", { seconds: eta })}` : ""}
            </span>
            <DefaultButton
              iconProps={{ iconName: "Cancel" }}
              text={tx("montage.timeline.cancelRender", "Cancel render")}
              disabled={Boolean(job.cancel_requested)}
              onClick={onCancel}
              data-testid="montage-cancel-render"
            />
          </>
        ) : (
          <>
            {job.status === "completed" && outputUrl ? (
              <PrimaryButton
                iconProps={{ iconName: "OpenInNewWindow" }}
                text={tx("montage.timeline.openOutput", "Open output")}
                href={outputUrl}
                target="_blank"
                rel="noreferrer"
              />
            ) : null}
            {job.status !== "completed" ? (
              <DefaultButton
                iconProps={{ iconName: "Refresh" }}
                text={tx("montage.timeline.retryRender", "Retry render")}
                onClick={onRetry}
              />
            ) : null}
            <IconButton
              iconProps={{ iconName: "ChromeClose" }}
              ariaLabel={tx("montage.timeline.dismiss", "Dismiss")}
              onClick={onDismiss}
            />
          </>
        )}
      </div>
      {!terminal ? (
        <ProgressIndicator
          percentComplete={fraction ?? undefined}
          description={
            job.cancel_requested
              ? tx("montage.timeline.cancelling", "Stopping ffmpeg...")
              : tx("montage.timeline.rendering", "Rendering with ffmpeg... you can keep editing.")
          }
          barHeight={4}
        />
      ) : null}
      {job.status === "completed" ? (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-emerald-300">
            {tx("montage.timeline.renderDone", "Render completed.")}
            {durationS != null ? ` ${formatTime(durationS)}` : ""}
            {sizeBytes != null ? ` · ${(sizeBytes / 1_000_000).toFixed(1)} MB` : ""}
            {job.latency_s ? ` · ${Math.round(job.latency_s)}s` : ""}
          </p>
          {outputUrl ? (
            <video
              src={outputUrl}
              controls
              preload="metadata"
              className="max-h-72 w-full rounded-xl bg-black outline outline-1 outline-white/10"
              data-testid="montage-output-video"
            />
          ) : output ? (
            <p className="font-mono text-[11px] text-slate-400">{output}</p>
          ) : null}
        </div>
      ) : null}
      {job.status === "failed" ? (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-xl bg-rose-500/10 p-2 font-mono text-[11px] text-rose-200">
          {job.error || tx("montage.timeline.renderError", "Render failed.")}
        </pre>
      ) : null}
      {job.status === "cancelled" ? (
        <p className="text-xs text-slate-300">{tx("montage.timeline.renderCancelled", "Render cancelled. The partial file was removed.")}</p>
      ) : null}
    </div>
  );
}

function AuxTrack({
  path,
  color,
  zoom,
  duration,
  clearLabel,
  onClear,
}: {
  path: string | null;
  color: string;
  zoom: number;
  duration: number;
  clearLabel: string;
  onClear: () => void;
}) {
  return (
    <div className="flex h-14 items-center border-b border-white/5">
      {path ? (
        <div
          className={cn("flex h-10 items-center gap-2 rounded-lg px-3 text-[11px] font-semibold", color)}
          style={{ width: Math.max(80, duration * zoom) }}
          title={path}
        >
          <span className="min-w-0 flex-1 truncate">{fileName(path)}</span>
          <button
            type="button"
            className="flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center rounded-full focus:outline-none focus:ring-2 focus:ring-white"
            aria-label={clearLabel}
            onClick={onClear}
          >
            <Icon iconName="Cancel" aria-hidden />
          </button>
        </div>
      ) : (
        <span className="px-3 text-[10px] text-slate-600">-</span>
      )}
    </div>
  );
}

function AssetRow({
  asset,
  action,
  icon,
  detail,
  onAction,
}: {
  asset: MontageAsset;
  action: string;
  icon: string;
  detail?: string;
  onAction: () => void;
}) {
  return (
    <div className="flex items-center gap-2 rounded-xl bg-[#0b1323] p-2">
      <Icon iconName={icon} className="text-blue-400" aria-hidden />
      <span className="min-w-0 flex-1 truncate text-[11px]" title={asset.path}>
        {asset.name}
      </span>
      {detail ? <span className="font-mono text-[10px] tabular-nums text-slate-500">{detail}</span> : null}
      <DefaultButton text={action} onClick={onAction} />
    </div>
  );
}
