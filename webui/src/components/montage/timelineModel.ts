// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { MontageAsset, MontageMediaInfo, MontageTimeline, MontageTimelineVisual } from "@/lib/api";

export const DEFAULT_IMAGE_DURATION = 4;
export const MIN_CLIP_DURATION = 0.1;
/** Fallback when a clip could not be measured; the backend re-probes on render. */
export const UNKNOWN_VIDEO_DURATION = 4;

/** xfade styles the assembler accepts (mirrors `navin.montage.assemble.TRANSITIONS`). */
export const TRANSITIONS = [
  "none",
  "fade",
  "fadeblack",
  "fadewhite",
  "dissolve",
  "wipeleft",
  "wiperight",
  "wipeup",
  "wipedown",
  "slideleft",
  "slideright",
  "slideup",
  "slidedown",
  "circleopen",
  "circleclose",
  "radial",
  "smoothleft",
  "smoothright",
  "pixelize",
  "hblur",
  "distance",
  "zoomin",
] as const;

export const FPS_OPTIONS = [24, 25, 30, 50, 60] as const;

export type TimelineHistory = {
  past: MontageTimeline[];
  present: MontageTimeline;
  future: MontageTimeline[];
};

export type TimelinePatch = Partial<
  Pick<
    MontageTimeline,
    | "name"
    | "output"
    | "width"
    | "height"
    | "fps"
    | "music_gain_db"
    | "transition"
    | "transition_duration"
    | "extra_metadata"
    | "visuals"
  >
>;

export type TimelineCommand =
  | { type: "replace"; timeline: MontageTimeline }
  | { type: "patch"; patch: TimelinePatch }
  | { type: "reorder"; from: number; to: number }
  | { type: "trim"; index: number; edge: "start" | "end"; value: number; snap?: number }
  | { type: "add"; visual: MontageTimelineVisual }
  | { type: "measure"; path: string; duration: number }
  | { type: "remove"; index: number }
  | { type: "set-audio"; track: "voice" | "music" | "subtitles"; path: string | null }
  | { type: "undo" }
  | { type: "redo" };

export function createTimeline(name = "nouveau-montage"): MontageTimeline {
  return {
    schema_version: 1,
    name,
    visuals: [],
    output: `marketing/montage/exports/${name}.mp4`,
    music: null,
    voice: null,
    subtitles: null,
    width: 1920,
    height: 1080,
    fps: 30,
    music_gain_db: -18,
    transition: "fade",
    transition_duration: 0.35,
    extra_metadata: {},
  };
}

export function cloneTimeline(timeline: MontageTimeline): MontageTimeline {
  return {
    ...timeline,
    visuals: timeline.visuals.map((visual) => ({ ...visual })),
    extra_metadata: { ...timeline.extra_metadata },
  };
}

/**
 * Seconds a visual occupies on the timeline. For a video, `duration` is the
 * measured source length (same meaning as the backend), so the played part
 * is the trim window clamped to it; a still simply shows for `duration`.
 */
export function visualDuration(visual: MontageTimelineVisual): number {
  if (visual.kind === "image") {
    return Math.max(MIN_CLIP_DURATION, visual.duration ?? DEFAULT_IMAGE_DURATION);
  }
  const source = visual.duration != null && visual.duration > 0 ? visual.duration : null;
  const start = Math.max(0, visual.start ?? 0);
  let end = visual.end ?? source ?? start + UNKNOWN_VIDEO_DURATION;
  if (source != null) end = Math.min(end, source);
  return Math.max(MIN_CLIP_DURATION, end - start);
}

/** Measured source length of a video clip, when known. */
export function sourceDuration(visual: MontageTimelineVisual): number | null {
  if (visual.kind !== "video") return null;
  return visual.duration != null && visual.duration > 0 ? visual.duration : null;
}

/** Whether the render will accept the transition against the shortest clip. */
export function transitionFits(timeline: MontageTimeline): boolean {
  if (timeline.transition === "none" || timeline.visuals.length < 2) return true;
  const shortest = Math.min(...timeline.visuals.map(visualDuration));
  return timeline.transition_duration < shortest;
}

function comparablePath(path: string): string {
  return path.trim().replace(/\\/g, "/").replace(/^(\.\/)+/, "").replace(/\/{2,}/g, "/");
}

/**
 * The input that the output path would overwrite, or null. A previous export
 * sits in the asset library, so it can be dropped onto the timeline while the
 * output still points at it; the backend refuses that render (`validate_spec`).
 */
export function outputCollision(timeline: MontageTimeline): string | null {
  const target = comparablePath(timeline.output);
  if (!target) return null;
  const inputs = [
    ...timeline.visuals.map((visual) => visual.path),
    timeline.music,
    timeline.voice,
    timeline.subtitles,
  ];
  return inputs.find((path) => path && comparablePath(path) === target) ?? null;
}

/** The first `name-v2`, `name-v3`... output that no clip or track is using. */
export function nextFreeOutput(timeline: MontageTimeline): string {
  const match = /^(.*?)(?:-v(\d+))?(\.[A-Za-z0-9]+)?$/.exec(timeline.output.trim());
  const stem = match?.[1] || "marketing/montage/exports/montage";
  const suffix = match?.[3] || ".mp4";
  let version = match?.[2] ? Number(match[2]) + 1 : 2;
  for (;;) {
    const candidate = `${stem}-v${version}${suffix}`;
    if (outputCollision({ ...timeline, output: candidate }) == null) return candidate;
    version += 1;
  }
}

/**
 * Total length: crossfades overlap the clips, so each transition shortens the
 * edit (same arithmetic as `resolve_total_duration` on the backend).
 */
export function timelineDuration(timeline: MontageTimeline): number {
  const total = timeline.visuals.reduce((sum, visual) => sum + visualDuration(visual), 0);
  if (timeline.transition !== "none" && timeline.visuals.length > 1) {
    return Math.max(
      MIN_CLIP_DURATION,
      total - timeline.transition_duration * (timeline.visuals.length - 1),
    );
  }
  return total;
}

/** Build a timeline visual from a gallery asset, using the probe when available. */
export function visualFromAsset(
  asset: Pick<MontageAsset, "path" | "kind" | "visual" | "duration">,
  info?: Pick<MontageMediaInfo, "duration"> | null,
): MontageTimelineVisual | null {
  // `visual: null` is the backend saying "previewable, not a render input"
  // (an .svg); only a missing field falls back to the display kind.
  const kind =
    asset.visual !== undefined
      ? asset.visual
      : asset.kind === "video" || asset.kind === "image"
        ? asset.kind
        : null;
  if (kind !== "video" && kind !== "image") return null;
  if (kind === "image") {
    return { path: asset.path, kind, duration: DEFAULT_IMAGE_DURATION, start: null, end: null };
  }
  const measured = info?.duration ?? asset.duration ?? null;
  const source = measured != null && measured > 0 ? Math.round(measured * 1000) / 1000 : null;
  // `end: null` means "play to the end of the file": once the probe answers,
  // the whole clip is used instead of freezing a guessed 4 s window.
  return {
    path: asset.path,
    kind,
    duration: source,
    start: 0,
    end: source,
  };
}

export function snapTime(value: number, interval = 0.1): number {
  if (!Number.isFinite(value)) return 0;
  return Math.round(value / interval) * interval;
}

export function reorderVisuals(
  timeline: MontageTimeline,
  from: number,
  to: number,
): MontageTimeline {
  if (
    from === to ||
    from < 0 ||
    to < 0 ||
    from >= timeline.visuals.length ||
    to >= timeline.visuals.length
  ) {
    return timeline;
  }
  const visuals = timeline.visuals.map((visual) => ({ ...visual }));
  const [moved] = visuals.splice(from, 1);
  visuals.splice(to, 0, moved);
  return { ...cloneTimeline(timeline), visuals };
}

export function trimVisual(
  timeline: MontageTimeline,
  index: number,
  edge: "start" | "end",
  rawValue: number,
  snap = 0.1,
): MontageTimeline {
  const visual = timeline.visuals[index];
  if (!visual) return timeline;
  const visuals = timeline.visuals.map((item) => ({ ...item }));
  const current = visuals[index];
  const value = Math.max(0, snapTime(rawValue, snap));
  if (current.kind === "image") {
    current.duration = Math.max(MIN_CLIP_DURATION, value);
    return { ...cloneTimeline(timeline), visuals };
  }
  // Trims never leave the measured source: dragging past the end of the file
  // would render a frozen frame (or be refused by the assembler).
  const source = sourceDuration(current);
  const currentEnd = current.end ?? source ?? (current.start ?? 0) + UNKNOWN_VIDEO_DURATION;
  if (edge === "start") {
    const end = source != null ? Math.min(currentEnd, source) : currentEnd;
    current.start = Math.max(0, Math.min(value, end - MIN_CLIP_DURATION));
    current.end = end;
  } else {
    const start = current.start ?? 0;
    const limit = source ?? Number.POSITIVE_INFINITY;
    current.end = Math.min(limit, Math.max(start + MIN_CLIP_DURATION, value));
    current.start = start;
  }
  return { ...cloneTimeline(timeline), visuals };
}

/**
 * Record the measured source length on every video clip using `path` and clamp
 * their trims to it. Keyed by path (not index) because the probe answers after
 * the user may have reordered or duplicated the clip.
 */
export function measureVisual(
  timeline: MontageTimeline,
  path: string,
  duration: number,
): MontageTimeline {
  if (!(duration > 0)) return timeline;
  const source = Math.round(duration * 1000) / 1000;
  let changed = false;
  const visuals = timeline.visuals.map((item) => {
    if (item.kind !== "video" || item.path !== path || item.duration === source) return { ...item };
    changed = true;
    const start = Math.min(Math.max(0, item.start ?? 0), Math.max(0, source - MIN_CLIP_DURATION));
    const end = Math.min(source, Math.max(start + MIN_CLIP_DURATION, item.end ?? source));
    return { ...item, duration: source, start, end };
  });
  if (!changed) return timeline;
  return { ...cloneTimeline(timeline), visuals };
}

function applyMutation(timeline: MontageTimeline, command: TimelineCommand): MontageTimeline {
  if (command.type === "replace") return cloneTimeline(command.timeline);
  if (command.type === "patch") return { ...cloneTimeline(timeline), ...command.patch };
  if (command.type === "reorder") return reorderVisuals(timeline, command.from, command.to);
  if (command.type === "trim") {
    return trimVisual(timeline, command.index, command.edge, command.value, command.snap);
  }
  if (command.type === "measure") return measureVisual(timeline, command.path, command.duration);
  if (command.type === "add") {
    return {
      ...cloneTimeline(timeline),
      visuals: [...timeline.visuals.map((item) => ({ ...item })), { ...command.visual }],
    };
  }
  if (command.type === "remove") {
    if (!timeline.visuals[command.index]) return timeline;
    return {
      ...cloneTimeline(timeline),
      visuals: timeline.visuals.filter((_, index) => index !== command.index),
    };
  }
  if (command.type === "set-audio") {
    return { ...cloneTimeline(timeline), [command.track]: command.path };
  }
  return timeline;
}

export function createHistory(timeline = createTimeline()): TimelineHistory {
  return { past: [], present: cloneTimeline(timeline), future: [] };
}

export function timelineReducer(
  history: TimelineHistory,
  command: TimelineCommand,
): TimelineHistory {
  if (command.type === "undo") {
    const previous = history.past.at(-1);
    if (!previous) return history;
    return {
      past: history.past.slice(0, -1),
      present: cloneTimeline(previous),
      future: [cloneTimeline(history.present), ...history.future],
    };
  }
  if (command.type === "redo") {
    const next = history.future[0];
    if (!next) return history;
    return {
      past: [...history.past, cloneTimeline(history.present)],
      present: cloneTimeline(next),
      future: history.future.slice(1),
    };
  }
  const next = applyMutation(history.present, command);
  if (next === history.present) return history;
  if (command.type === "measure") {
    // A probe result is bookkeeping, not an edit: it must not eat an undo step.
    return { ...history, present: next };
  }
  return {
    past: [...history.past.slice(-49), cloneTimeline(history.present)],
    present: next,
    future: [],
  };
}

/** Stable serialization used to compare a document with its saved state. */
export function timelineFingerprint(timeline: MontageTimeline): string {
  return JSON.stringify({
    ...timeline,
    visuals: timeline.visuals.map((visual) => [
      visual.path,
      visual.kind,
      visual.duration,
      visual.start,
      visual.end,
    ]),
  });
}

export function formatTime(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const remainder = safe - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${remainder.toFixed(1).padStart(4, "0")}`;
}
