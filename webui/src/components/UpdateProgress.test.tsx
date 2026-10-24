// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { ProgressBar, updateStep } from "@/components/UpdateProgress";
import type { UpdateStatus } from "@/lib/api";

const t = ((key: string, options?: { defaultValue?: string; percent?: number }) =>
  `${key}:${options?.percent ?? ""}`) as unknown as Parameters<typeof updateStep>[1];

function status(partial: Partial<UpdateStatus>): UpdateStatus {
  return { state: "idle", progress: 0, totalBytes: 0, ...partial } as UpdateStatus;
}

afterEach(cleanup);

describe("updateStep", () => {
  it("measures the download only once its size is known", () => {
    expect(updateStep(status({ state: "downloading" }), t)).toEqual({ label: "updates.preparing:", percent: null });
    expect(updateStep(status({ state: "downloading", totalBytes: 10, progress: 42.4 }), t)).toEqual({
      label: "updates.downloading:42",
      percent: 42,
    });
  });

  it("stays full once downloaded instead of going back to the sweep", () => {
    expect(updateStep(status({ state: "ready", progress: 100, totalBytes: 10 }), t)).toEqual({
      label: "updates.downloading:100",
      percent: 100,
    });
  });

  it("shows no frozen 0% while the installer has not reported yet", () => {
    expect(updateStep(status({ state: "installing", phase: "preparing", progress: 0 }), t)).toEqual({
      label: "updates.installing:",
      percent: null,
    });
    expect(updateStep(status({ state: "installing", phase: "preparing", progress: 37 }), t)).toEqual({
      label: "updates.installingPercent:37",
      percent: 37,
    });
    expect(updateStep(status({ state: "installing", progress: 100 }), t).percent).toBeNull();
    expect(updateStep(status({ state: "installing", phase: "restarting", progress: 100 }), t)).toEqual({
      label: "updates.restarting:",
      percent: null,
    });
  });
});

describe("ProgressBar", () => {
  function parts(container: HTMLElement) {
    const bar = container.querySelector('[role="progressbar"]') as HTMLElement;
    return {
      bar,
      fill: bar.querySelector('[data-part="fill"]') as HTMLElement | null,
      sweep: bar.querySelector('[data-part="sweep"]') as HTMLElement | null,
    };
  }

  it("anchors the fill at the reading start after a sweep, never where the sweep stood", () => {
    const view = render(createElement(ProgressBar, { percent: null }));
    const sweep = parts(view.container).sweep;
    expect(sweep).not.toBeNull();
    expect(sweep?.className).toContain("rtl:-scale-x-100");

    view.rerender(createElement(ProgressBar, { percent: 40 }));
    const after = parts(view.container);
    expect(after.sweep).toBeNull();
    expect(after.fill).not.toBeNull();
    expect(after.fill).not.toBe(sweep?.firstElementChild);
    expect(after.fill?.className).toContain("start-0");
    // No physical offset left over from the sweep animation.
    expect(after.fill?.style.left).toBe("");
    expect(after.fill?.style.transform).toBe("");
    expect(after.bar.getAttribute("aria-valuenow")).toBe("40");
    expect(after.bar.dataset.mode).toBe("measured");

    view.rerender(createElement(ProgressBar, { percent: null }));
    const again = parts(view.container);
    expect(again.fill).toBeNull();
    expect(again.sweep).not.toBeNull();
    expect(again.bar.getAttribute("aria-valuenow")).toBeNull();
    expect(again.bar.getAttribute("aria-busy")).toBe("true");
  });

  it("keeps one fill element while the percent moves, both ways", () => {
    const view = render(createElement(ProgressBar, { percent: 20 }));
    const fill = parts(view.container).fill;
    view.rerender(createElement(ProgressBar, { percent: 80 }));
    view.rerender(createElement(ProgressBar, { percent: 5 }));
    expect(parts(view.container).fill).toBe(fill);
    expect(parts(view.container).bar.getAttribute("aria-valuenow")).toBe("5");
  });
});
