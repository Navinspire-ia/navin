// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Interface zoom: Ctrl/Cmd +/-/0 and Ctrl + wheel, like any browser.
 *
 * The desktop shell cannot provide this on its own: wry only wires
 * `zoomHotkeysEnabled` to WebView2, so Linux (WebKitGTK) and macOS (WKWebView)
 * ship with no way to resize the UI at all. The webui therefore owns zoom on
 * every platform and drives the native webview through the Tauri `set_webview_zoom`
 * command, falling back to CSS when that command is not reachable.
 *
 * The stored level is relative to the shell baseline (`zoomBaseline` URL param).
 * Default baseline is 1.0 on Windows, Linux (AppImage/deb/rpm/pacman, WSL) and both
 * macOS arches, same as the local web UI. `NAVIN_DESKTOP_ZOOM` can raise it.
 */
import { consumeUrlUiZoomFlag, consumeUrlZoomBaseline } from "./bootstrap";
import { isDesktopShell, tauriInvoke } from "./desktop";

export const ZOOM_STEPS = [
  0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5,
];
export const MIN_ZOOM = ZOOM_STEPS[0];
export const MAX_ZOOM = ZOOM_STEPS[ZOOM_STEPS.length - 1];
export const DEFAULT_ZOOM = 1;

const ZOOM_STORAGE_KEY = "navin-webui.ui-zoom";
const BASELINE_STORAGE_KEY = "navin-webui.ui-zoom-baseline";
const DESKTOP_FLAG_KEY = "navin-webui.ui-zoom-desktop";
/** Escape hatch to exercise the controller in a plain browser (dev only). */
const FORCE_STORAGE_KEY = "navin-webui.ui-zoom-force";

const EPSILON = 0.001;

/** Keep the value inside the supported range, at a sane precision. */
export function clampZoom(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_ZOOM;
  const rounded = Math.round(value * 1000) / 1000;
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, rounded));
}

/** Next / previous preset, for the keyboard shortcuts and the +/- buttons. */
export function zoomStep(current: number, direction: -1 | 1): number {
  const value = clampZoom(current);
  if (direction > 0) {
    return ZOOM_STEPS.find((step) => step > value + EPSILON) ?? MAX_ZOOM;
  }
  const lower = ZOOM_STEPS.filter((step) => step < value - EPSILON);
  return lower.length > 0 ? lower[lower.length - 1] : MIN_ZOOM;
}

/** Wheel deltas come in pixels, lines or pages depending on the platform. */
export function normalizeWheelDelta(deltaY: number, deltaMode = 0): number {
  if (!Number.isFinite(deltaY)) return 0;
  if (deltaMode === 1) return deltaY * 16;
  if (deltaMode === 2) return deltaY * 100;
  return deltaY;
}

/**
 * Continuous zoom for Ctrl + wheel and trackpad pinch, which send a stream of
 * small deltas: stepping through the presets there would be far too coarse.
 */
export function zoomByWheel(current: number, deltaY: number, deltaMode = 0): number {
  const normalized = normalizeWheelDelta(deltaY, deltaMode);
  if (!normalized) return clampZoom(current);
  const bounded = Math.max(-240, Math.min(240, normalized));
  const next = clampZoom(current) * Math.exp(-bounded * 0.001);
  return clampZoom(Math.round(next * 100) / 100);
}

export function zoomLabel(zoom: number): string {
  return `${Math.round(clampZoom(zoom) * 100)}%`;
}

export function parseZoomBaseline(raw: string | null | undefined): number {
  const value = Number.parseFloat((raw ?? "").trim());
  if (!Number.isFinite(value) || value < 0.25 || value > 5) return 1;
  return Math.round(value * 1000) / 1000;
}

/**
 * WebKitGTK / WKWebView: wry's `set_webview_zoom` often resolves without
 * changing the painted size on a remote http page. Chromium (WebView2) is
 * the one engine where the native command actually zooms.
 */
export function usesDocumentCssZoom(userAgent = typeof navigator === "undefined" ? "" : navigator.userAgent): boolean {
  return /AppleWebKit/i.test(userAgent) && !/Chrome|Chromium|Edg\//i.test(userAgent);
}

/**
 * Linux AppImage / .deb / .rpm: WebKitGTK. Not Chromium, not WKWebView.
 * Backdrop-filter and `hsl(var(--token) / alpha)` are both broken or very
 * expensive here, so the document class opts the UI into solid paint.
 */
export function isWebKitGtk(userAgent = typeof navigator === "undefined" ? "" : navigator.userAgent): boolean {
  if (!usesDocumentCssZoom(userAgent)) return false;
  return /Linux|X11/i.test(userAgent) && !/Android/i.test(userAgent);
}

/** Before the first paint, so Linux never flashes a blurred / transparent frame. */
export function applyWebKitGtkDocumentClass(
  userAgent = typeof navigator === "undefined" ? "" : navigator.userAgent,
): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("webkit-engine", usesDocumentCssZoom(userAgent));
  document.documentElement.classList.toggle("webkit-gtk", isWebKitGtk(userAgent));
}

function desktopZoomFlagSet(): boolean {
  try {
    return window.localStorage.getItem(DESKTOP_FLAG_KEY) === "1"
      || window.localStorage.getItem(FORCE_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * A plain browser already zooms with the same shortcuts, and its own handling
 * cannot be cancelled, so the controller only takes over inside the desktop
 * shell (or when explicitly forced for development).
 */
export function isZoomControlAvailable(): boolean {
  if (typeof window === "undefined") return false;
  if (tauriInvoke()) return true;
  if (isDesktopShell()) return true;
  return desktopZoomFlagSet();
}

export function readStoredZoom(): number {
  if (typeof window === "undefined") return DEFAULT_ZOOM;
  try {
    const raw = window.localStorage.getItem(ZOOM_STORAGE_KEY);
    if (!raw) return DEFAULT_ZOOM;
    return clampZoom(Number.parseFloat(raw));
  } catch {
    return DEFAULT_ZOOM;
  }
}

export function writeStoredZoom(zoom: number): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ZOOM_STORAGE_KEY, String(clampZoom(zoom)));
  } catch {
    // Zoom must keep working even when storage is unavailable.
  }
}

export function readZoomBaseline(): number {
  if (typeof window === "undefined") return 1;
  try {
    return parseZoomBaseline(window.localStorage.getItem(BASELINE_STORAGE_KEY));
  } catch {
    return 1;
  }
}

function rememberZoomBaseline(raw: string): void {
  try {
    window.localStorage.setItem(BASELINE_STORAGE_KEY, String(parseZoomBaseline(raw)));
  } catch {
    // ignore storage errors
  }
}

/**
 * What the shell wants this launch. A desktop session without `zoomBaseline`
 * in the URL is 100 %: an old WSLg 1.25 left in localStorage must not survive.
 * Browser tabs (no desktop flag) keep whatever they already stored.
 */
export function resolveShellZoomBaseline(fromUrl: string, desktop: boolean): number | null {
  if ((fromUrl || "").trim()) return parseZoomBaseline(fromUrl);
  if (desktop) return 1;
  return null;
}

export function applyShellZoomBaseline(fromUrl: string, desktop: boolean): void {
  const next = resolveShellZoomBaseline(fromUrl, desktop);
  if (next == null) return;
  rememberZoomBaseline(String(next));
}

let cssFallback = false;

export const CSS_SCALE_ZOOM_CLASS = "ui-css-scale-zoom";

/**
 * Layout size of `<html>` when zoom is applied via `transform: scale`.
 * Scale does not shrink the layout box, so without this the painted page
 * grows past the window and the bottom chrome (composer, Code menus)
 * scrolls off - the bug Windows never sees, because WebView2 zooms the
 * viewport instead.
 */
export function cssScaleZoomLayoutPercent(effective: number): number {
  if (!Number.isFinite(effective) || effective <= 0) return 100;
  return 100 / effective;
}

/**
 * Did the engine honour CSS `zoom` on `<html>`? Zoom divides the layout
 * viewport, so the root's layout width shrinks by the same factor.
 */
export function cssZoomTookEffect(
  widthBefore: number,
  widthAfter: number,
  effective: number,
): boolean {
  if (!widthBefore || !widthAfter) return false;
  if (!Number.isFinite(effective) || effective <= 0) return false;
  const expected = widthBefore / effective;
  const tolerance = Math.max(2, expected * 0.02);
  return Math.abs(widthAfter - expected) <= tolerance;
}

/** Parse computed or inline CSS zoom (`1.5`, `150%`, `normal`). */
export function parseCssZoomValue(raw: string | null | undefined): number {
  if (raw == null) return 1;
  const value = String(raw).trim().toLowerCase();
  if (!value || value === "normal") return 1;
  if (value.endsWith("%")) {
    const pct = Number.parseFloat(value);
    return Number.isFinite(pct) && pct > 0 ? pct / 100 : 1;
  }
  const n = Number.parseFloat(value);
  return Number.isFinite(n) && n > 0 ? n : 1;
}

/**
 * CSS `zoom` currently on `<html>`. Native webview zoom is not included.
 */
export function documentCssZoom(root?: HTMLElement | null): number {
  if (typeof document === "undefined") return 1;
  const el = root ?? document.documentElement;
  const inline = parseCssZoomValue(el.style.zoom);
  if (Math.abs(inline - 1) > EPSILON) return inline;
  return parseCssZoomValue(getComputedStyle(el).zoom);
}

/**
 * WebKitGTK paints `zoom` on `<html>` without shrinking layout. Pointer
 * `clientX/Y` stay in viewport pixels while `position: fixed` still uses
 * pre-zoom coordinates, so a menu opened at the pointer is painted
 * `zoom` times further down (and can leave the window).
 *
 * Engines that divide the layout viewport (Chrome) keep one coordinate
 * space: do not convert.
 */
export function cssZoomSplitsPointerFromFixed(
  viewportWidth: number,
  layoutWidth: number,
  zoom: number,
  enginePaintsZoomWithoutLayout = false,
): boolean {
  if (!Number.isFinite(zoom) || zoom <= 0 || Math.abs(zoom - 1) < EPSILON) {
    return false;
  }
  // Explicit override for tests. Production no longer forces this from
  // isWebKitGtk(): a false positive plus clamp pinned menus to the origin.
  if (enginePaintsZoomWithoutLayout) return true;
  return !cssZoomTookEffect(viewportWidth, layoutWidth, zoom);
}

export function documentPointerUsesFixedLayerSplit(
  root?: HTMLElement | null,
): boolean {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return false;
  }
  const el = root ?? document.documentElement;
  // Do not force a split just because this is WebKitGTK. When the engine
  // already shrinks layout, Radix writes layout-space translates and a
  // second divide + clamp pins every menu to the top-left.
  return cssZoomSplitsPointerFromFixed(
    window.innerWidth,
    el.clientWidth,
    documentCssZoom(el),
  );
}

/** Convert a pointer event into `left`/`top` for a `position: fixed` menu. */
export function viewportPointerToFixed(
  clientX: number,
  clientY: number,
  zoom: number,
  splits: boolean,
): { x: number; y: number } {
  if (!splits || !Number.isFinite(zoom) || zoom <= 0 || Math.abs(zoom - 1) < EPSILON) {
    return { x: clientX, y: clientY };
  }
  return { x: clientX / zoom, y: clientY / zoom };
}

export function clampFixedToVisualViewport(args: {
  x: number;
  y: number;
  menuWidth: number;
  menuHeight: number;
  viewportWidth: number;
  viewportHeight: number;
  zoom: number;
  splits: boolean;
  gap?: number;
}): { x: number; y: number } {
  const gap = args.gap ?? 8;
  const zoom = args.zoom;
  if (
    args.splits
    && Number.isFinite(zoom)
    && zoom > 0
    && Math.abs(zoom - 1) >= EPSILON
  ) {
    const maxX = args.viewportWidth / zoom - args.menuWidth - gap;
    const maxY = args.viewportHeight / zoom - args.menuHeight - gap;
    // A negative max means the measured box is bigger than the layout
    // viewport (offsetWidth already visual). Pinning to `gap` is how
    // zoomed dropdowns ended up in the top-left.
    return {
      x: maxX >= gap ? Math.max(gap, Math.min(args.x, maxX)) : args.x,
      y: maxY >= gap ? Math.max(gap, Math.min(args.y, maxY)) : args.y,
    };
  }
  const maxX = args.viewportWidth - args.menuWidth - gap;
  const maxY = args.viewportHeight - args.menuHeight - gap;
  return {
    x: Math.max(gap, Math.min(args.x, maxX)),
    y: Math.max(gap, Math.min(args.y, maxY)),
  };
}

const RADIX_ZOOM_SRC = "data-navin-zoom-src";
const RADIX_ZOOM_OUT = "data-navin-zoom-out";
const RADIX_POPPER_WRAPPER = "[data-radix-popper-content-wrapper]";

function parseMatrixTranslate(value: string): { x: number; y: number } | null {
  const matrix3d = value.match(/matrix3d\(\s*([^)]+)\)/i);
  if (matrix3d) {
    const parts = matrix3d[1].split(",").map((part) => Number.parseFloat(part.trim()));
    if (parts.length === 16 && Number.isFinite(parts[12]) && Number.isFinite(parts[13])) {
      return { x: parts[12], y: parts[13] };
    }
  }
  const matrix = value.match(/matrix\(\s*([^)]+)\)/i);
  if (!matrix) return null;
  const parts = matrix[1].split(",").map((part) => Number.parseFloat(part.trim()));
  if (parts.length !== 6 || !parts.every(Number.isFinite)) return null;
  return { x: parts[4], y: parts[5] };
}

/** `translate()` / `translate3d()` / `matrix()` / `matrix3d()` in px. */
export function parseCssTranslate(transform: string): { x: number; y: number } | null {
  const value = transform.trim();
  if (!value || value === "none") return null;
  // Radix parks unmeasured menus at `translate(0, -200%)`.
  if (value.includes("%")) return null;
  const three = value.match(
    /translate3d\(\s*(-?\d*\.?\d+)(?:px)?\s*,\s*(-?\d*\.?\d+)(?:px)?\s*,\s*(-?\d*\.?\d+)(?:px)?\s*\)/i,
  );
  if (three) {
    const x = Number.parseFloat(three[1]);
    const y = Number.parseFloat(three[2]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
  }
  const two = value.match(
    /translate\(\s*(-?\d*\.?\d+)(?:px)?(?:\s*,\s*(-?\d*\.?\d+)(?:px)?)?\s*\)/i,
  );
  if (two) {
    const x = Number.parseFloat(two[1]);
    const y = two[2] != null ? Number.parseFloat(two[2]) : 0;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
  }
  return parseMatrixTranslate(value);
}

/**
 * Radix Popper writes visual-pixel translates. Under WebKitGTK paint-only
 * zoom those must be divided into pre-zoom fixed layout space.
 */
export function cssZoomPopperTranslate(
  x: number,
  y: number,
  zoom: number,
  splits: boolean,
): { x: number; y: number } {
  if (!splits || !Number.isFinite(zoom) || zoom <= 0 || Math.abs(zoom - 1) < EPSILON) {
    return { x, y };
  }
  return { x: x / zoom, y: y / zoom };
}

function formatCssTranslate(x: number, y: number, source: string): string {
  if (/translate3d\(/i.test(source)) {
    return `translate3d(${x}px, ${y}px, 0)`;
  }
  return `translate(${x}px, ${y}px)`;
}

function popperTransformEquals(a: string, b: string): boolean {
  if (a === b) return true;
  const pa = parseCssTranslate(a);
  const pb = parseCssTranslate(b);
  if (!pa || !pb) return false;
  return Math.abs(pa.x - pb.x) < 0.51 && Math.abs(pa.y - pb.y) < 0.51;
}

/**
 * Layout-space transform for a Radix popper, or `null` when nothing should
 * be rewritten (no split, already converted, unmeasured park).
 *
 * Never clamps. `applyCssZoomToRadixPopper` used to set
 * `maxHeight = layoutH - 16` and then clamp against `offsetHeight`. The
 * ResizeObserver / style MutationObserver ran again on that inflated box,
 * `maxY` collapsed to `gap` (8), and Effort / model / session menus pinned
 * to the top-left. Radix collision + the content `max-h-[min(...,28rem)]`
 * already keep tall menus on-screen.
 */
export function planCssZoomPopperTransform(args: {
  transform: string;
  lastOut: string | null;
  zoom: number;
  splits: boolean;
}): string | null {
  if (!args.splits) return null;
  const transform = args.transform;
  if (!transform) return null;
  if (args.lastOut && popperTransformEquals(transform, args.lastOut)) return null;
  const parsed = parseCssTranslate(transform);
  if (!parsed) return null;
  if (Math.abs(parsed.x) < 1 && Math.abs(parsed.y) < 1) return null;
  const converted = cssZoomPopperTranslate(parsed.x, parsed.y, args.zoom, args.splits);
  const next = formatCssTranslate(converted.x, converted.y, transform);
  return next === transform ? null : next;
}

/**
 * While Radix Presence plays the close animation, keep the last layout
 * translate. `animate-out` (and some engine-flattening of child
 * `transform`) can zero the wrapper to (0, 0) for a frame: the menu
 * blinks at the origin instead of fading in place.
 */
export function planFrozenCssZoomPopperTransform(args: {
  transform: string;
  lastOut: string | null;
}): string | null {
  if (!args.lastOut) return null;
  if (popperTransformEquals(args.transform, args.lastOut)) return null;
  return args.lastOut;
}

/**
 * Last non-origin translate while the menu is open. Conversion only
 * happens when pointer/fixed split; chat ⋯ still needs this at 100%
 * zoom because the trigger is `display: none` until hover, so Radix
 * repositions to (0, 0) the moment close hides it.
 */
export function rememberOpenPopperTransform(transform: string): string | null {
  if (!transform) return null;
  const parsed = parseCssTranslate(transform);
  if (!parsed) return null;
  if (Math.abs(parsed.x) < 1 && Math.abs(parsed.y) < 1) return null;
  return transform;
}

function radixPopperContentState(wrapper: HTMLElement): string | null {
  const content = wrapper.querySelector<HTMLElement>(
    "[data-radix-dropdown-menu-content], [data-radix-dropdown-menu-sub-content], [data-radix-popover-content], [data-radix-select-content]",
  );
  return content?.getAttribute("data-state") ?? null;
}

/**
 * offsetWidth can already be visual under WebKitGTK CSS zoom. Using that
 * as a layout size makes `viewport/zoom - menu` negative, so clamp sticks
 * every dropdown to the top-left.
 */
export function popperLayoutSize(args: {
  offsetWidth: number;
  offsetHeight: number;
  visualWidth: number;
  visualHeight: number;
  viewportWidth: number;
  viewportHeight: number;
  zoom: number;
  splits: boolean;
}): { width: number; height: number } {
  const { offsetWidth, offsetHeight, visualWidth, visualHeight, viewportWidth, viewportHeight, zoom, splits } = args;
  let width = offsetWidth;
  let height = offsetHeight;
  if (
    splits
    && Number.isFinite(zoom)
    && zoom > 1
    && Math.abs(zoom - 1) >= EPSILON
  ) {
    const layoutW = viewportWidth / zoom;
    const layoutH = viewportHeight / zoom;
    if (width > layoutW + 1 || height > layoutH + 1) {
      width = width / zoom;
      height = height / zoom;
    } else if (
      visualWidth > 0
      && Math.abs(visualWidth - offsetWidth * zoom) > Math.max(2, offsetWidth * 0.05)
      && Math.abs(visualWidth - offsetWidth) <= Math.max(2, offsetWidth * 0.05)
    ) {
      width = visualWidth / zoom;
      height = visualHeight / zoom;
    }
  }
  return { width, height };
}

function withPopperApplyLock(wrapper: HTMLElement, fn: () => void): void {
  if (wrapper.dataset.navinZoomApplying === "1") return;
  wrapper.dataset.navinZoomApplying = "1";
  try {
    fn();
  } finally {
    wrapper.dataset.navinZoomApplying = "0";
  }
}

/**
 * Correct a `[data-radix-popper-content-wrapper]` so a zoomed menu stays
 * next to its trigger on WebKitGTK. Convert visual translates into layout
 * space once. Do not clamp and do not set `maxHeight`: both caused a
 * second observer pass that pinned menus at (8, 8).
 */
export function applyCssZoomToRadixPopper(wrapper: HTMLElement): void {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  const transform = wrapper.style.transform;
  const lastOut = wrapper.getAttribute(RADIX_ZOOM_OUT);
  if (radixPopperContentState(wrapper) === "closed") {
    const frozen = planFrozenCssZoomPopperTransform({ transform, lastOut });
    if (!frozen) return;
    withPopperApplyLock(wrapper, () => {
      wrapper.style.transform = frozen;
    });
    return;
  }
  const next = planCssZoomPopperTransform({
    transform,
    lastOut,
    zoom: documentCssZoom(),
    splits: documentPointerUsesFixedLayerSplit(),
  });
  if (next) {
    withPopperApplyLock(wrapper, () => {
      wrapper.setAttribute(RADIX_ZOOM_SRC, transform);
      wrapper.setAttribute(RADIX_ZOOM_OUT, next);
      wrapper.style.transform = next;
    });
    return;
  }
  const remembered = rememberOpenPopperTransform(transform);
  if (remembered && !popperTransformEquals(remembered, lastOut ?? "")) {
    wrapper.setAttribute(RADIX_ZOOM_OUT, remembered);
  }
}

let popperWatch: MutationObserver | null = null;

function scanRadixPoppers(): void {
  if (typeof document === "undefined") return;
  document.querySelectorAll<HTMLElement>(RADIX_POPPER_WRAPPER).forEach(applyCssZoomToRadixPopper);
}

/** Keep Radix Effort / model / session menus in layout space after React rewrites transform. */
export function ensureCssZoomPopperWatch(): void {
  if (typeof document === "undefined" || popperWatch) return;
  popperWatch = new MutationObserver((records) => {
    for (const record of records) {
      const targets: Node[] = [record.target];
      if (record.type === "childList") {
        record.addedNodes.forEach((node) => targets.push(node));
      }
      for (const target of targets) {
        if (!(target instanceof HTMLElement)) continue;
        const wrapper = target.closest(RADIX_POPPER_WRAPPER);
        if (wrapper instanceof HTMLElement) applyCssZoomToRadixPopper(wrapper);
      }
    }
  });
  popperWatch.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["style", "data-state"],
  });
}

function clearScaleZoom(root: HTMLElement): void {
  root.classList.remove(CSS_SCALE_ZOOM_CLASS);
  root.style.removeProperty("transform");
  root.style.removeProperty("transform-origin");
  root.style.removeProperty("width");
  root.style.removeProperty("height");
  root.style.removeProperty("min-height");
}

function clearCssZoom(root: HTMLElement): void {
  clearScaleZoom(root);
  root.style.removeProperty("zoom");
}

function applyCssZoom(effective: number): void {
  const root = document.documentElement;
  if (Math.abs(effective - 1) < EPSILON) {
    clearCssZoom(root);
    ensureCssZoomPopperWatch();
    scanRadixPoppers();
    return;
  }
  // CSS `zoom` first. Chrome shrinks the layout viewport so pointer events
  // and `position: fixed` stay in one space. WebKitGTK paints the zoom
  // without shrinking layout: context menus convert pointer coords
  // (see viewportPointerToFixed). Never fall back to transform: scale,
  // which also splits hit-testing, so a click on a model row lands on
  // "Manage models" in WKWebView.
  clearScaleZoom(root);
  root.style.removeProperty("zoom");
  root.style.setProperty("zoom", String(effective));
  ensureCssZoomPopperWatch();
}

/** Push a zoom level to the webview (native when possible, CSS otherwise). */
export function applyUiZoom(zoom: number): void {
  if (typeof document === "undefined") return;
  const effective = clampZoom(zoom) * readZoomBaseline();
  const invoke = tauriInvoke();
  // WebKit: do not wait on set_webview_zoom. It commonly "succeeds" and
  // paints nothing, which left Linux users with no Ctrl +/- at all.
  if (usesDocumentCssZoom() || !invoke || cssFallback) {
    applyCssZoom(effective);
    return;
  }
  void invoke("plugin:webview|set_webview_zoom", { value: effective }).catch(() => {
    cssFallback = true;
    applyCssZoom(effective);
  });
}

/** Restore the persisted level as early as possible, before the first paint. */
export function initUiZoom(): void {
  if (typeof window === "undefined") return;
  applyWebKitGtkDocumentClass();
  const desktopFlag = consumeUrlUiZoomFlag();
  if (desktopFlag === "1" || desktopFlag.toLowerCase() === "true") {
    try {
      window.localStorage.setItem(DESKTOP_FLAG_KEY, "1");
    } catch {
      // Zoom must keep working even when storage is unavailable.
    }
  }
  const fromUrl = consumeUrlZoomBaseline();
  applyShellZoomBaseline(
    fromUrl,
    desktopFlag === "1"
      || desktopFlag.toLowerCase() === "true"
      || isDesktopShell()
      || desktopZoomFlagSet(),
  );
  if (!isZoomControlAvailable()) return;
  applyUiZoom(readStoredZoom());
  ensureCssZoomPopperWatch();
}
