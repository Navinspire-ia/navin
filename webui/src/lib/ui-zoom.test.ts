// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  MAX_ZOOM,
  MIN_ZOOM,
  clampFixedToVisualViewport,
  clampZoom,
  cssScaleZoomLayoutPercent,
  cssZoomPopperTranslate,
  planCssZoomPopperTransform,
  planFrozenCssZoomPopperTransform,
  rememberOpenPopperTransform,
  cssZoomSplitsPointerFromFixed,
  cssZoomTookEffect,
  popperLayoutSize,
  isWebKitGtk,
  normalizeWheelDelta,
  parseCssTranslate,
  parseCssZoomValue,
  parseZoomBaseline,
  resolveShellZoomBaseline,
  usesDocumentCssZoom,
  viewportPointerToFixed,
  zoomByWheel,
  zoomLabel,
  zoomStep,
} from "./ui-zoom";

describe("clampZoom", () => {
  it("keeps values inside the supported range", () => {
    expect(clampZoom(0.1)).toBe(MIN_ZOOM);
    expect(clampZoom(9)).toBe(MAX_ZOOM);
    expect(clampZoom(1.25)).toBe(1.25);
  });

  it("falls back to 100 % for garbage", () => {
    expect(clampZoom(Number.NaN)).toBe(1);
    expect(clampZoom(Number.POSITIVE_INFINITY)).toBe(1);
  });
});

describe("zoomStep", () => {
  it("walks the presets in both directions", () => {
    expect(zoomStep(1, 1)).toBe(1.1);
    expect(zoomStep(1, -1)).toBe(0.9);
    expect(zoomStep(1.12, 1)).toBe(1.25);
    expect(zoomStep(1.12, -1)).toBe(1.1);
  });

  it("stops at the bounds", () => {
    expect(zoomStep(MAX_ZOOM, 1)).toBe(MAX_ZOOM);
    expect(zoomStep(MIN_ZOOM, -1)).toBe(MIN_ZOOM);
  });
});

describe("normalizeWheelDelta", () => {
  it("scales line and page deltas up to pixels", () => {
    expect(normalizeWheelDelta(-3, 1)).toBe(-48);
    expect(normalizeWheelDelta(1, 2)).toBe(100);
    expect(normalizeWheelDelta(120)).toBe(120);
  });
});

describe("zoomByWheel", () => {
  it("zooms in when scrolling up and out when scrolling down", () => {
    expect(zoomByWheel(1, -120)).toBeGreaterThan(1);
    expect(zoomByWheel(1, 120)).toBeLessThan(1);
  });

  it("stays within the range whatever the delta is", () => {
    expect(zoomByWheel(MAX_ZOOM, -100000)).toBe(MAX_ZOOM);
    expect(zoomByWheel(MIN_ZOOM, 100000)).toBe(MIN_ZOOM);
  });

  it("ignores empty deltas", () => {
    expect(zoomByWheel(1.25, 0)).toBe(1.25);
  });
});

describe("parseZoomBaseline", () => {
  it("reads the shell baseline and rejects nonsense", () => {
    expect(parseZoomBaseline("1.25")).toBe(1.25);
    expect(parseZoomBaseline("")).toBe(1);
    expect(parseZoomBaseline("abc")).toBe(1);
    expect(parseZoomBaseline("42")).toBe(1);
  });
});

describe("resolveShellZoomBaseline", () => {
  it("keeps an explicit URL baseline (NAVIN_DESKTOP_ZOOM)", () => {
    expect(resolveShellZoomBaseline("1.25", true)).toBe(1.25);
  });

  it("resets a leftover 1.25 when the new shell sends no baseline", () => {
    expect(resolveShellZoomBaseline("", true)).toBe(1);
    expect(resolveShellZoomBaseline("  ", true)).toBe(1);
  });

  it("does not touch a plain browser tab", () => {
    expect(resolveShellZoomBaseline("", false)).toBeNull();
  });
});

describe("zoomLabel", () => {
  it("renders a percentage", () => {
    expect(zoomLabel(1)).toBe("100%");
    expect(zoomLabel(1.25)).toBe("125%");
  });
});

describe("usesDocumentCssZoom", () => {
  it("is true for WebKitGTK / WKWebView, false for Chromium", () => {
    expect(
      usesDocumentCssZoom(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
      ),
    ).toBe(true);
    expect(
      usesDocumentCssZoom(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
      ),
    ).toBe(true);
    expect(
      usesDocumentCssZoom(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
      ),
    ).toBe(false);
  });
});

describe("cssScaleZoomLayoutPercent", () => {
  it("shrinks the layout box so scale() still fills the window", () => {
    expect(cssScaleZoomLayoutPercent(1)).toBe(100);
    expect(cssScaleZoomLayoutPercent(1.25)).toBe(80);
    expect(cssScaleZoomLayoutPercent(2)).toBe(50);
  });

  it("rejects nonsense", () => {
    expect(cssScaleZoomLayoutPercent(0)).toBe(100);
    expect(cssScaleZoomLayoutPercent(Number.NaN)).toBe(100);
  });
});

describe("cssZoomTookEffect", () => {
  it("detects the engine dividing the layout viewport", () => {
    expect(cssZoomTookEffect(1000, 800, 1.25)).toBe(true);
    expect(cssZoomTookEffect(1000, 500, 2)).toBe(true);
    expect(cssZoomTookEffect(1000, 2000, 0.5)).toBe(true);
  });

  it("reports an engine that ignored zoom (desktop must not scale() in that case)", () => {
    expect(cssZoomTookEffect(1000, 1000, 1.25)).toBe(false);
    expect(cssZoomTookEffect(0, 800, 1.25)).toBe(false);
    expect(cssZoomTookEffect(1000, 800, 0)).toBe(false);
  });
});

describe("parseCssZoomValue", () => {
  it("reads numbers, percents, and normal", () => {
    expect(parseCssZoomValue("1.5")).toBe(1.5);
    expect(parseCssZoomValue("150%")).toBe(1.5);
    expect(parseCssZoomValue("normal")).toBe(1);
    expect(parseCssZoomValue("")).toBe(1);
    expect(parseCssZoomValue(null)).toBe(1);
  });
});

describe("css zoom pointer / fixed split", () => {
  it("does not convert when Chrome shrinks the layout viewport", () => {
    expect(cssZoomSplitsPointerFromFixed(1000, 800, 1.25)).toBe(false);
    expect(viewportPointerToFixed(400, 600, 1.25, false)).toEqual({
      x: 400,
      y: 600,
    });
  });

  it("converts WebKitGTK paint-only zoom so a menu sits on the pointer", () => {
    expect(cssZoomSplitsPointerFromFixed(1000, 1000, 2)).toBe(true);
    expect(viewportPointerToFixed(400, 600, 2, true)).toEqual({
      x: 200,
      y: 300,
    });
  });

  it("leaves 100% zoom and invalid zoom alone", () => {
    expect(cssZoomSplitsPointerFromFixed(1000, 1000, 1)).toBe(false);
    expect(viewportPointerToFixed(400, 600, 2, false)).toEqual({
      x: 400,
      y: 600,
    });
  });

  it("does not force a split when layout already shrunk (production WebKitGTK path)", () => {
    // Production no longer passes isWebKitGtk() as the engine flag.
    // Forcing a split here is what pinned Effort / model menus top-left.
    expect(cssZoomSplitsPointerFromFixed(1000, 500, 2)).toBe(false);
    expect(cssZoomSplitsPointerFromFixed(1000, 500, 2, true)).toBe(true);
  });

  it("does not pin to the origin when the measured menu cannot fit in layout space", () => {
    expect(
      clampFixedToVisualViewport({
        x: 200,
        y: 300,
        menuWidth: 800,
        menuHeight: 400,
        viewportWidth: 1000,
        viewportHeight: 500,
        zoom: 2,
        splits: true,
        gap: 8,
      }),
    ).toEqual({ x: 200, y: 300 });
  });

  it("clamps a low click into the visible window under paint-only zoom", () => {
    // 1080px window at 250% zoom: layout-space height is 432px. A menu
    // opened at clientY=1000 would paint at 2500px without conversion.
    const point = viewportPointerToFixed(200, 1000, 2.5, true);
    expect(point).toEqual({ x: 80, y: 400 });
    const clamped = clampFixedToVisualViewport({
      x: point.x,
      y: point.y,
      menuWidth: 160,
      menuHeight: 180,
      viewportWidth: 1920,
      viewportHeight: 1080,
      zoom: 2.5,
      splits: true,
      gap: 8,
    });
    expect(clamped.y).toBeLessThanOrEqual(1080 / 2.5 - 180 - 8);
    expect(clamped.y).toBeGreaterThanOrEqual(8);
    expect(clamped.x).toBe(80);
  });

  it("clamps in viewport pixels when the engine keeps one coordinate space", () => {
    expect(
      clampFixedToVisualViewport({
        x: 1800,
        y: 1000,
        menuWidth: 160,
        menuHeight: 180,
        viewportWidth: 1920,
        viewportHeight: 1080,
        zoom: 1.25,
        splits: false,
        gap: 8,
      }),
    ).toEqual({ x: 1752, y: 892 });
  });
});

describe("isWebKitGtk", () => {
  it("matches the Linux AppImage / .deb webview only", () => {
    expect(
      isWebKitGtk(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
      ),
    ).toBe(true);
    expect(
      isWebKitGtk(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)",
      ),
    ).toBe(false);
    expect(
      isWebKitGtk(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
      ),
    ).toBe(false);
    expect(
      isWebKitGtk(
        "Mozilla/5.0 (Linux; Android 14) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/605.1.15",
      ),
    ).toBe(false);
  });
});

describe("css zoom radix popper translate", () => {
  it("keeps Chrome no-split identity", () => {
    expect(cssZoomPopperTranslate(400, 600, 1.25, false)).toEqual({
      x: 400,
      y: 600,
    });
    expect(parseCssTranslate("translate(400px, 600px)")).toEqual({
      x: 400,
      y: 600,
    });
  });

  it("divides WebKitGTK zoom 2 translate(400px, 600px) to (200, 300)", () => {
    expect(parseCssTranslate("translate(400px, 600px)")).toEqual({
      x: 400,
      y: 600,
    });
    expect(cssZoomPopperTranslate(400, 600, 2, true)).toEqual({
      x: 200,
      y: 300,
    });
  });


});

describe("popperLayoutSize", () => {
  it("divides an offset box that already overflowed layout space", () => {
    expect(
      popperLayoutSize({
        offsetWidth: 800,
        offsetHeight: 400,
        visualWidth: 1600,
        visualHeight: 800,
        viewportWidth: 1000,
        viewportHeight: 500,
        zoom: 2,
        splits: true,
      }),
    ).toEqual({ width: 400, height: 200 });
  });

  it("does not convert when the engine keeps one coordinate space", () => {
    expect(
      popperLayoutSize({
        offsetWidth: 800,
        offsetHeight: 400,
        visualWidth: 800,
        visualHeight: 400,
        viewportWidth: 1000,
        viewportHeight: 500,
        zoom: 2,
        splits: false,
      }),
    ).toEqual({ width: 800, height: 400 });
  });
});

describe("planCssZoomPopperTransform", () => {
  it("divides a visual translate under WebKitGTK split", () => {
    expect(
      planCssZoomPopperTransform({
        transform: "translate(938.64px, 1074.72px)",
        lastOut: null,
        zoom: 1.1,
        splits: true,
      }),
    ).toBe(`translate(${938.64 / 1.1}px, ${1074.72 / 1.1}px)`);
  });

  it("does not rewrite an already-converted transform (maxHeight observer pass)", () => {
    const first = planCssZoomPopperTransform({
      transform: "translate(938.64px, 1074.72px)",
      lastOut: null,
      zoom: 1.1,
      splits: true,
    });
    expect(first).toBeTruthy();
    expect(
      planCssZoomPopperTransform({
        transform: first!,
        lastOut: first,
        zoom: 1.1,
        splits: true,
      }),
    ).toBeNull();
  });

  it("does not pin to the 8px origin after a second pass", () => {
    const first = planCssZoomPopperTransform({
      transform: "translate(938.64px, 1074.72px)",
      lastOut: null,
      zoom: 1.1,
      splits: true,
    });
    expect(first).not.toMatch(/translate\(8px/);
    expect(
      planCssZoomPopperTransform({
        transform: first!,
        lastOut: first,
        zoom: 1.1,
        splits: true,
      }),
    ).toBeNull();
  });

  it("passes through on Chrome (no split)", () => {
    expect(
      planCssZoomPopperTransform({
        transform: "translate(938.64px, 1074.72px)",
        lastOut: null,
        zoom: 1.1,
        splits: false,
      }),
    ).toBeNull();
  });
});

describe("planFrozenCssZoomPopperTransform", () => {
  it("restores the last layout translate when close zeros the wrapper", () => {
    expect(
      planFrozenCssZoomPopperTransform({
        transform: "translate(0px, 0px)",
        lastOut: "translate(853.309px, 977.018px)",
      }),
    ).toBe("translate(853.309px, 977.018px)");
  });

  it("does not rewrite when the close animation left the convert in place", () => {
    expect(
      planFrozenCssZoomPopperTransform({
        transform: "translate(853.309px, 977.018px)",
        lastOut: "translate(853.309px, 977.018px)",
      }),
    ).toBeNull();
  });

  it("does nothing when zoom never converted the popper", () => {
    expect(
      planFrozenCssZoomPopperTransform({
        transform: "translate(100px, 200px)",
        lastOut: null,
      }),
    ).toBeNull();
  });

  it("holds a 100% zoom chat-actions translate after the trigger hides", () => {
    const remembered = rememberOpenPopperTransform("translate(12px, 380px)");
    expect(remembered).toBe("translate(12px, 380px)");
    expect(
      planFrozenCssZoomPopperTransform({
        transform: "translate(0px, 0px)",
        lastOut: remembered,
      }),
    ).toBe("translate(12px, 380px)");
  });
});

describe("rememberOpenPopperTransform", () => {
  it("keeps a real translate so close can freeze without a zoom convert", () => {
    expect(rememberOpenPopperTransform("translate(12px, 380px)")).toBe(
      "translate(12px, 380px)",
    );
  });

  it("ignores an origin park so a hidden trigger cannot clobber lastOut", () => {
    expect(rememberOpenPopperTransform("translate(0px, 0px)")).toBeNull();
    expect(rememberOpenPopperTransform("")).toBeNull();
  });
});
