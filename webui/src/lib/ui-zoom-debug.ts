/**
 * Localhost debug channel for the UI zoom controller.
 *
 * Pairs with the `navin/webui/ui_zoom_debug.py` route exposed by the desktop
 * gateway on `/api/debug/ui-zoom`. That route is intentionally loopback-only
 * and unauthenticated (mirrors `/health`) so a developer tooling can drive
 * the zoom level, trigger a specific menu (session / effort / model) and read
 * back a layout snapshot without needing a gateway token.
 *
 * The file keeps two pieces of state local:
 *
 *  - `collectUiZoomSnapshot()`: a pure DOM helper that the poller calls after
 *    every applied command. It reads `document` / `visualViewport`, the
 *    `[data-navin-debug]` triggers and the Radix popper wrappers, and returns
 *    a plain object that travels back to the gateway via the chunked
 *    `X-Navin-File-Body-*` headers (gateway ignores fetch bodies on POST).
 *  - `startUiZoomDebugPoller()`: drives the round-trip. It is opt-in: only
 *    boots when `isZoomControlAvailable()` returns true (desktop shell), so a
 *    plain browser tab never spins an extra timer.
 */
import {
  documentCssZoom,
  documentPointerUsesFixedLayerSplit,
  isWebKitGtk,
  readStoredZoom,
} from "@/lib/ui-zoom";

/** Same chunk size as `bodyHeaders` in `lib/api.ts`. Mirrored locally so this
 *  module does not pull the api.ts dependency. The gateway reassembles the
 *  body by concatenating `X-Navin-File-Body-0`, `-1`, ... in numeric order. */
const FILE_BODY_CHUNK_CHARS = 6000;

const DEBUG_PATH = "/api/debug/ui-zoom";
const POLL_INTERVAL_MS = 250;
/** The backend rebroadcasts the snapshot after applying each command; the
 *  client just waits long enough for two RAFs (paint + resize observers) so
 *  the popper rects are stable. */
const POST_DELAY_FRAMES = 2;

export type DebugCommandPayload = {
  seq: number;
  zoom: number | null;
  action: string | null;
  target: string | null;
  selector: string | null;
  hud: boolean;
  issuedAt: number;
};

export type DebugResponse = {
  ok: boolean;
  seq: number;
  command: DebugCommandPayload | null;
  snapshot: UiZoomSnapshot | null;
  updatedAt: number;
};

export interface UiZoomDebugTarget {
  target: string;
  ariaLabel: string | null;
  rect: { x: number; y: number; width: number; height: number } | null;
  offsetWidth: number;
  offsetHeight: number;
}

export interface UiZoomDebugPopper {
  transform: string | null;
  src: string | null;
  out: string | null;
  rect: { x: number; y: number; width: number; height: number } | null;
  offsetWidth: number;
  offsetHeight: number;
  applying: boolean;
}

export interface UiZoomDebugMenu {
  role: string;
  left: string | null;
  top: string | null;
  rect: { x: number; y: number; width: number; height: number } | null;
  offsetWidth: number;
  offsetHeight: number;
}

export interface UiZoomSnapshot {
  zoom: number;
  storedZoom: number;
  splits: boolean;
  innerWidth: number;
  innerHeight: number;
  clientWidth: number;
  clientHeight: number;
  visualViewport: {
    width: number;
    height: number;
    scale: number;
    offsetLeft: number;
    offsetTop: number;
  } | null;
  htmlZoom: string;
  userAgent: string;
  webkitGtk: boolean;
  triggers: UiZoomDebugTarget[];
  poppers: UiZoomDebugPopper[];
  menus: UiZoomDebugMenu[];
}

function safeRect(el: Element | null): UiZoomDebugTarget["rect"] {
  if (!el || typeof el.getBoundingClientRect !== "function") return null;
  const rect = el.getBoundingClientRect();
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
}

function describePopper(wrapper: Element): UiZoomDebugPopper {
  const el = wrapper as HTMLElement;
  const rect = safeRect(el);
  const dataset = el.dataset ?? {};
  return {
    transform: typeof el.style?.transform === "string" ? el.style.transform : null,
    src: dataset["navinZoomSrc"] ?? null,
    out: dataset["navinZoomOut"] ?? null,
    rect,
    offsetWidth: (el as HTMLElement).offsetWidth ?? 0,
    offsetHeight: (el as HTMLElement).offsetHeight ?? 0,
    applying: dataset["navinZoomApplying"] === "1",
  };
}

function describeMenu(el: Element): UiZoomDebugMenu {
  const style = (el as HTMLElement).style;
  const role = el.getAttribute("role") ?? "";
  return {
    role,
    left: style?.left ?? null,
    top: style?.top ?? null,
    rect: safeRect(el),
    offsetWidth: (el as HTMLElement).offsetWidth ?? 0,
    offsetHeight: (el as HTMLElement).offsetHeight ?? 0,
  };
}

/**
 * Pure DOM read, exported so tests can call it against a jsdom-built tree
 * and so callers can run the snapshot outside the poller (e.g. right before
 * a hand-driven zoom change).
 */
export function collectUiZoomSnapshot(): UiZoomSnapshot {
  const docEl = typeof document !== "undefined" ? document.documentElement : null;
  const win = typeof window !== "undefined" ? window : null;
  const nav = typeof navigator !== "undefined" ? navigator : null;
  const ua = nav?.userAgent ?? "";
  const vv = win && win.visualViewport ? win.visualViewport : null;
  const triggers: UiZoomDebugTarget[] = typeof document !== "undefined"
    ? Array.from(document.querySelectorAll<HTMLElement>("[data-navin-debug]")).map(
        (el) => ({
          target: el.dataset["navinDebug"] ?? "",
          ariaLabel: el.getAttribute("aria-label"),
          rect: safeRect(el),
          offsetWidth: el.offsetWidth,
          offsetHeight: el.offsetHeight,
        }),
      )
    : [];
  const poppers: UiZoomDebugPopper[] = typeof document !== "undefined"
    ? Array.from(document.querySelectorAll("[data-radix-popper-content-wrapper]")).map(
        describePopper,
      )
    : [];
  const menus: UiZoomDebugMenu[] = typeof document !== "undefined"
    ? Array.from(
        document.querySelectorAll<HTMLElement>("[role=\"menu\"], [role=\"listbox\"]"),
      ).map(describeMenu)
    : [];
  return {
    zoom: documentCssZoom(docEl),
    storedZoom: readStoredZoom(),
    splits: documentPointerUsesFixedLayerSplit(docEl),
    innerWidth: win?.innerWidth ?? 0,
    innerHeight: win?.innerHeight ?? 0,
    clientWidth: docEl?.clientWidth ?? 0,
    clientHeight: docEl?.clientHeight ?? 0,
    visualViewport: vv
      ? {
          width: vv.width,
          height: vv.height,
          scale: vv.scale,
          offsetLeft: vv.offsetLeft,
          offsetTop: vv.offsetTop,
        }
      : null,
    htmlZoom: docEl?.style?.zoom ?? "",
    userAgent: ua.slice(0, 180),
    webkitGtk: isWebKitGtk(ua),
    triggers,
    poppers,
    menus,
  };
}

/**
 * Mirror of `bodyHeaders` in `lib/api.ts`. Kept private to this module so the
 * gateway debug endpoint never needs to import the full api.ts dependency.
 */
export function encodeFileBodyHeaders(content: string): Record<string, string> {
  const bytes = new TextEncoder().encode(content);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  const b64 = btoa(binary);
  const headers: Record<string, string> = {};
  let index = 0;
  for (let i = 0; i < b64.length; i += FILE_BODY_CHUNK_CHARS) {
    headers[`X-Navin-File-Body-${index}`] = b64.slice(
      i,
      i + FILE_BODY_CHUNK_CHARS,
    );
    index += 1;
  }
  return headers;
}

async function waitFrames(count: number): Promise<void> {
  if (typeof window === "undefined") return;
  for (let i = 0; i < count; i += 1) {
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
  }
}

async function postSnapshot(snapshot: UiZoomSnapshot): Promise<void> {
  if (typeof window === "undefined" || typeof fetch === "undefined") return;
  const body = JSON.stringify({ snapshot });
  try {
    // GET: the gateway handshake rejects POST before routing.
    await fetch(DEBUG_PATH, {
      method: "GET",
      headers: encodeFileBodyHeaders(body),
      credentials: "same-origin",
    });
  } catch {
    // Debug-only: never throw from the poller.
  }
}

function applyCommand(command: DebugCommandPayload): boolean {
  if (typeof window === "undefined") return false;
  if (command.action === "open" && command.target) {
    const el = document.querySelector<HTMLElement>(
      `[data-navin-debug="${command.target}"]`,
    );
    if (el) {
      // Session ⋯ is `hidden` until hover; force it visible so a synthetic
      // pointer sequence can hit it. Radix DropdownMenuTrigger listens to
      // pointerdown, not HTMLElement.click().
      el.classList.remove("hidden");
      el.style.display = "inline-flex";
      el.style.visibility = "visible";
      el.style.pointerEvents = "auto";
      const rect = el.getBoundingClientRect();
      const clientX = rect.x + Math.max(1, rect.width / 2);
      const clientY = rect.y + Math.max(1, rect.height / 2);
      const base = {
        bubbles: true,
        cancelable: true,
        composed: true,
        view: window,
        clientX,
        clientY,
        button: 0,
        buttons: 1,
      };
      try {
        el.focus();
      } catch {
        // Focus is best-effort; pointer events still toggle Radix.
      }
      el.dispatchEvent(new PointerEvent("pointerdown", { ...base, pointerId: 1, pointerType: "mouse" }));
      el.dispatchEvent(new MouseEvent("mousedown", base));
      el.dispatchEvent(new PointerEvent("pointerup", { ...base, buttons: 0, pointerId: 1, pointerType: "mouse" }));
      el.dispatchEvent(new MouseEvent("mouseup", { ...base, buttons: 0 }));
      el.dispatchEvent(new MouseEvent("click", { ...base, buttons: 0 }));
      return true;
    }
    return false;
  }
  if (command.action === "click" && command.selector) {
    const el = document.querySelector<HTMLElement>(command.selector);
    if (el) {
      el.click();
      return true;
    }
    return false;
  }
  if (command.action === "outside") {
    document.querySelectorAll<HTMLElement>("[data-navin-debug]").forEach((el) => {
      el.style.display = "";
      el.style.visibility = "";
      el.style.pointerEvents = "";
    });
    const x = Math.max(8, (window.innerWidth || 800) * 0.62);
    const y = Math.max(8, (window.innerHeight || 600) * 0.42);
    const base = {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
      clientX: x,
      clientY: y,
      button: 0,
      buttons: 1,
    };
    const target = document.elementFromPoint(x, y) ?? document.body;
    target.dispatchEvent(new PointerEvent("pointerdown", { ...base, pointerId: 1, pointerType: "mouse" }));
    target.dispatchEvent(new MouseEvent("mousedown", base));
    return true;
  }
  if (command.action === "escape") {
    const target =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : document.body;
    if (target) {
      target.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
      target.dispatchEvent(
        new KeyboardEvent("keyup", { key: "Escape", bubbles: true }),
      );
      return true;
    }
    return false;
  }
  if (
    command.action === "dump" ||
    command.action === "in" ||
    command.action === "out" ||
    command.action === "reset" ||
    (command.zoom !== null && Number.isFinite(command.zoom))
  ) {
    return true;
  }
  return false;
}

export interface UiZoomDebugPoller {
  stop(): void;
}

export interface UiZoomDebugPollerOptions {
  endpoint?: string;
  intervalMs?: number;
  onCommand: (command: DebugCommandPayload) => boolean;
}

export function startUiZoomDebugPoller(
  options: UiZoomDebugPollerOptions,
): UiZoomDebugPoller {
  if (typeof window === "undefined" || typeof fetch === "undefined") {
    return { stop: () => {} };
  }
  const endpoint = options.endpoint ?? DEBUG_PATH;
  const intervalMs = options.intervalMs ?? POLL_INTERVAL_MS;
  let lastHandledSeq = 0;
  let stopped = false;
  let timer: number | null = null;

  const poll = async () => {
    if (stopped) return;
    try {
      const res = await fetch(endpoint, {
        credentials: "same-origin",
      });
      if (!res.ok) return;
      const data = (await res.json()) as DebugResponse;
      const command = data.command;
      if (!command) return;
      if (command.seq <= lastHandledSeq) return;
      lastHandledSeq = command.seq;
      const localApplied = applyCommand(command);
      const hookApplied = options.onCommand(command);
      if (!(localApplied || hookApplied)) return;
      await waitFrames(POST_DELAY_FRAMES);
      // Radix popper + CSS zoom settle after more than two frames on WebKitGTK.
      await new Promise<void>((resolve) => window.setTimeout(resolve, 400));
      await postSnapshot(collectUiZoomSnapshot());
    } catch {
      // swallow: the next tick will try again
    }
  };

  const schedule = () => {
    if (stopped) return;
    timer = window.setTimeout(async () => {
      await poll();
      schedule();
    }, intervalMs);
  };
  void poll().then(schedule);

  return {
    stop: () => {
      stopped = true;
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    },
  };
}
