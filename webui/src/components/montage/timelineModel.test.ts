import { describe, expect, it } from "vitest";

import {
  createHistory,
  createTimeline,
  formatTime,
  measureVisual,
  nextFreeOutput,
  outputCollision,
  reorderVisuals,
  snapTime,
  timelineDuration,
  timelineFingerprint,
  timelineReducer,
  transitionFits,
  trimVisual,
  visualDuration,
  visualFromAsset,
} from "./timelineModel";

const video = {
  path: "media/intro.mp4",
  kind: "video" as const,
  duration: null,
  start: 1,
  end: 5,
};
const image = {
  path: "media/card.png",
  kind: "image" as const,
  duration: 3,
  start: null,
  end: null,
};

describe("Montage timeline model", () => {
  it("calculates duration and formats tabular time", () => {
    const timeline = { ...createTimeline("demo"), transition: "none", visuals: [video, image] };
    expect(timelineDuration(timeline)).toBe(7);
    expect(formatTime(67.25)).toBe("01:07.3");
  });

  it("flags an output path that is also a clip and proposes a free name", () => {
    const exportClip = { ...image, kind: "video" as const, path: "./marketing//montage/exports/demo.mp4" };
    const timeline = {
      ...createTimeline("demo"),
      output: "marketing/montage/exports/demo.mp4",
      visuals: [video, exportClip],
    };
    expect(outputCollision(timeline)).toBe(exportClip.path);
    expect(outputCollision({ ...timeline, visuals: [video] })).toBeNull();
    expect(outputCollision({ ...timeline, visuals: [video], music: timeline.output })).toBe(
      timeline.output,
    );
    expect(nextFreeOutput(timeline)).toBe("marketing/montage/exports/demo-v2.mp4");
    const taken = {
      ...timeline,
      output: "marketing/montage/exports/demo-v2.mp4",
      visuals: [exportClip, { ...exportClip, path: "marketing/montage/exports/demo-v2.mp4" }],
    };
    expect(nextFreeOutput(taken)).toBe("marketing/montage/exports/demo-v3.mp4");
  });

  it("subtracts crossfade overlaps like the renderer does", () => {
    const timeline = {
      ...createTimeline("demo"),
      transition: "fade",
      transition_duration: 0.5,
      visuals: [video, image],
    };
    expect(timelineDuration(timeline)).toBeCloseTo(6.5);
    expect(transitionFits(timeline)).toBe(true);
    expect(transitionFits({ ...timeline, transition_duration: 3 })).toBe(false);
    expect(transitionFits({ ...timeline, transition: "none", transition_duration: 3 })).toBe(true);
  });

  it("clamps a video trim to its measured source length", () => {
    const measured = { ...video, duration: 6, start: 0, end: 6 };
    const timeline = { ...createTimeline("demo"), visuals: [measured] };
    const past = trimVisual(timeline, 0, "end", 9, 0.1);
    expect(past.visuals[0].end).toBe(6);
    const before = trimVisual(timeline, 0, "start", -2, 0.1);
    expect(before.visuals[0].start).toBe(0);
    // A trim window past the file end plays only what exists.
    expect(visualDuration({ ...measured, end: 12 })).toBe(6);
    expect(visualDuration({ ...measured, start: 4, end: 12 })).toBe(2);
  });

  it("records a probe result on every clip with that path without eating undo", () => {
    const twice = { ...createTimeline("demo"), visuals: [{ ...video, start: 2, end: 20 }, image, { ...video }] };
    const measured = measureVisual(twice, "media/intro.mp4", 8.25);
    expect(measured.visuals[0]).toMatchObject({ duration: 8.25, start: 2, end: 8.25 });
    expect(measured.visuals[2]).toMatchObject({ duration: 8.25, start: 1, end: 5 });
    expect(measured.visuals[1]).toEqual(image);
    expect(measureVisual(twice, "media/other.mp4", 3)).toBe(twice);

    let history = createHistory(twice);
    history = timelineReducer(history, { type: "measure", path: "media/intro.mp4", duration: 8.25 });
    expect(history.past).toHaveLength(0);
    expect(history.present.visuals[0].duration).toBe(8.25);
  });

  it("builds timeline visuals from gallery assets using the probe", () => {
    const clip = { path: "marketing/montage/demos/a.mp4", kind: "video", visual: "video" as const, duration: null };
    expect(visualFromAsset(clip, { duration: 12.5 })).toEqual({
      path: clip.path,
      kind: "video",
      duration: 12.5,
      start: 0,
      end: 12.5,
    });
    // Unknown length: the clip plays to its end once measured, shown as 4 s meanwhile.
    const unknown = visualFromAsset(clip);
    expect(unknown).toMatchObject({ duration: null, start: 0, end: null });
    expect(visualDuration(unknown!)).toBe(4);
    const measured = measureVisual({ ...createTimeline("demo"), visuals: [unknown!] }, clip.path, 11);
    expect(measured.visuals[0]).toMatchObject({ duration: 11, start: 0, end: 11 });
    expect(visualDuration(measured.visuals[0])).toBe(11);
    const gif = { path: "x/anim.gif", kind: "image", visual: "video" as const, duration: 2 };
    expect(visualFromAsset(gif)?.kind).toBe("video");
    const svg = { path: "x/logo.svg", kind: "image", visual: null, duration: null };
    expect(visualFromAsset(svg)).toBeNull();
    const doc = { path: "x/notes.md", kind: "document", duration: null };
    expect(visualFromAsset(doc)).toBeNull();
  });

  it("patches edit settings and tracks the saved fingerprint", () => {
    let history = createHistory({ ...createTimeline("demo"), visuals: [image] });
    const saved = timelineFingerprint(history.present);
    history = timelineReducer(history, { type: "patch", patch: { transition: "wipeleft", fps: 25 } });
    expect(history.present.transition).toBe("wipeleft");
    expect(history.present.fps).toBe(25);
    expect(timelineFingerprint(history.present)).not.toBe(saved);
    history = timelineReducer(history, { type: "undo" });
    expect(timelineFingerprint(history.present)).toBe(saved);
  });

  it("reorders clips without mutating the document", () => {
    const timeline = { ...createTimeline("demo"), visuals: [video, image] };
    const reordered = reorderVisuals(timeline, 0, 1);
    expect(reordered.visuals.map((item) => item.path)).toEqual([
      "media/card.png",
      "media/intro.mp4",
    ]);
    expect(timeline.visuals[0].path).toBe("media/intro.mp4");
  });

  it("snaps pointer trims and enforces a positive clip duration", () => {
    const timeline = { ...createTimeline("demo"), visuals: [video] };
    expect(snapTime(2.16, 0.1)).toBeCloseTo(2.2);
    const trimmed = trimVisual(timeline, 0, "start", 4.98, 0.1);
    expect(trimmed.visuals[0].start).toBe(4.9);
    expect(trimmed.visuals[0].end).toBe(5);
  });

  it("supports critical add, trim, undo and redo interactions", () => {
    let history = createHistory(createTimeline("demo"));
    history = timelineReducer(history, { type: "add", visual: image });
    history = timelineReducer(history, {
      type: "trim",
      index: 0,
      edge: "end",
      value: 5.2,
      snap: 0.1,
    });
    expect(history.present.visuals[0].duration).toBe(5.2);
    history = timelineReducer(history, { type: "undo" });
    expect(history.present.visuals[0].duration).toBe(3);
    history = timelineReducer(history, { type: "redo" });
    expect(history.present.visuals[0].duration).toBe(5.2);
  });
});
