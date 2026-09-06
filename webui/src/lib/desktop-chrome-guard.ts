/**
 * Inside the desktop shell the page *is* the application, so nothing the
 * webview inherits from its browser engine may leak through. WebKitGTK and
 * WebView2 still ship a page context menu (Back / Forward / Reload / Save as /
 * Print / Inspect), the shortcuts behind it, mouse back/forward buttons and
 * drop-to-navigate. Reloading while the engine restarts is how users landed on
 * a raw "could not connect" page signed by a browser engine instead of Navin.
 *
 * Everything here is decided by pure functions (unit-tested) and wired by one
 * installer. Handlers only call `preventDefault`, never `stopPropagation`, so
 * the app's own shortcuts (Ctrl+P quick open, Ctrl+S save, Ctrl+R in the
 * terminal) keep receiving their events.
 */
import { isDesktopShell } from "./desktop";
import { hostPlatform, type HostPlatform } from "./host-platform";

export type BlockedShortcut =
  | "reload"
  | "history"
  | "print"
  | "save"
  | "view-source"
  | "open-file"
  | "new-window"
  | "close-window";

export interface KeyLike {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
}

/**
 * Which browser feature a key chord would trigger, or null when the chord is
 * harmless. Only chords with a browser default are listed: app shortcuts that
 * share a chord (Ctrl+P, Ctrl+S, Ctrl+T) still fire because the guard never
 * stops propagation; the browser side of them is what gets cancelled.
 */
export function browserShortcut(event: KeyLike, platform: HostPlatform): BlockedShortcut | null {
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  const mod = platform === "macos" ? event.metaKey : event.ctrlKey;
  const otherMod = platform === "macos" ? event.ctrlKey : event.metaKey;

  if (key === "F5") return "reload";
  if (platform !== "macos" && event.altKey && !event.ctrlKey && !event.metaKey) {
    if (key === "ArrowLeft" || key === "ArrowRight" || key === "Home") return "history";
  }
  if (!mod || otherMod || event.altKey) return null;

  switch (key) {
    case "r":
      return "reload";
    case "p":
      return event.shiftKey ? null : "print";
    case "s":
      return "save";
    case "u":
      return event.shiftKey ? null : "view-source";
    case "o":
      return event.shiftKey ? null : "open-file";
    case "n":
    case "t":
      return "new-window";
    case "w":
      return "close-window";
    default:
      return null;
  }
}

export interface TargetLike {
  closest?: (selector: string) => unknown;
}

const EDITABLE_SELECTOR =
  'input, textarea, select, [contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"]';

/** Text fields keep the engine's edit menu (cut, copy, paste, spelling). */
export function isEditableTarget(target: unknown): boolean {
  const node = target as TargetLike | null;
  if (!node || typeof node.closest !== "function") return false;
  return Boolean(node.closest(EDITABLE_SELECTOR));
}

/**
 * The page menu (Back, Reload, Save as, Print, Inspect) never belongs in the
 * IDE. Custom menus (explorer, chat list) call `preventDefault` before this
 * runs and are left alone; editable fields keep the native edit menu.
 */
export function shouldBlockContextMenu(event: { defaultPrevented: boolean; target: unknown }): boolean {
  if (event.defaultPrevented) return false;
  return !isEditableTarget(event.target);
}

/** Mouse buttons 3 and 4 are history navigation in every engine. */
export function isHistoryMouseButton(button: number): boolean {
  return button === 3 || button === 4;
}

/**
 * A middle click on a link asks the engine for a new tab, which the shell
 * denies; cancel it so nothing flickers.
 */
export function shouldBlockAuxClick(event: { button: number; target: unknown }): boolean {
  if (isHistoryMouseButton(event.button)) return true;
  if (event.button !== 1) return false;
  const node = event.target as TargetLike | null;
  return Boolean(node && typeof node.closest === "function" && node.closest("a[href]"));
}

/**
 * Dropping a file or URL outside an app drop zone must not navigate the
 * webview away from Navin. Zones that accept drops call `preventDefault` on
 * `dragover` and `drop` themselves; this only covers everywhere else.
 */
export function shouldBlockDrop(event: { defaultPrevented: boolean }): boolean {
  return !event.defaultPrevented;
}

// -- Safe reload --------------------------------------------------------------

const HEALTH_PATH = "/health";
const RELOAD_PROBE_TIMEOUT_MS = 2_000;
const RELOAD_PROBE_PAUSE_MS = 1_000;
export const SAFE_RELOAD_MAX_WAIT_MS = 30_000;

export interface SafeReloadOptions {
  /** Answers true when the engine serves HTTP again. Injected in tests. */
  probe?: () => Promise<boolean>;
  /** Performs the reload once it is safe. Injected in tests. */
  reload?: () => void;
  /** Sleep between probes. Injected in tests. */
  sleep?: (ms: number) => Promise<void>;
  maxWaitMs?: number;
  /** True to force the shell behaviour outside the shell (tests). */
  desktopShell?: boolean;
}

/** One `/health` round trip with a short deadline. */
export async function probeEngine(
  fetchImpl: typeof fetch = fetch,
  timeoutMs: number = RELOAD_PROBE_TIMEOUT_MS,
): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(HEALTH_PATH, {
      cache: "no-store",
      signal: controller.signal,
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Reload the interface without ever showing an engine error page.
 *
 * In a browser tab a reload is what the user asked for, done at once. In the
 * shell the engine is probed first and the reload happens only once it
 * answers, so a restart in progress leaves the current screen (with its own
 * "engine is starting" state) in place instead of a WebKit or Chromium error.
 * Resolves false when the engine stayed silent for the whole wait.
 */
export async function safeReload(options: SafeReloadOptions = {}): Promise<boolean> {
  const reload = options.reload ?? (() => window.location.reload());
  const desktop = options.desktopShell ?? isDesktopShell();
  if (!desktop) {
    reload();
    return true;
  }
  const probe = options.probe ?? (() => probeEngine());
  const sleep =
    options.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const deadline = Date.now() + (options.maxWaitMs ?? SAFE_RELOAD_MAX_WAIT_MS);
  for (;;) {
    if (await probe()) {
      reload();
      return true;
    }
    if (Date.now() >= deadline) return false;
    await sleep(RELOAD_PROBE_PAUSE_MS);
  }
}

// -- Installer ----------------------------------------------------------------

export type BlockedFeature = BlockedShortcut | "context-menu" | "aux-click" | "drop";

type GuardListener = (event: Event) => void;

/** The slice of `window` the guard listens on; injectable for Node tests. */
export interface GuardHost {
  addEventListener: (type: string, listener: GuardListener, options?: { capture: boolean }) => void;
  removeEventListener: (
    type: string,
    listener: GuardListener,
    options?: { capture: boolean },
  ) => void;
}

export interface GuardOptions {
  platform?: HostPlatform;
  /** Defaults to `isDesktopShell()`; the guard is a no-op in a browser tab. */
  enabled?: boolean;
  /** Called with the blocked feature, for a debug trace. */
  onBlocked?: (what: BlockedFeature) => void;
  /** Defaults to `window`. */
  host?: GuardHost;
  /** Element carrying the install marker; defaults to `document.documentElement`. */
  root?: { dataset: Record<string, string | undefined> };
}

type GuardKeyEvent = KeyLike & { preventDefault(): void };
type GuardMouseEvent = {
  button: number;
  target: unknown;
  defaultPrevented: boolean;
  preventDefault(): void;
};
type GuardDragEvent = {
  defaultPrevented: boolean;
  dataTransfer?: { dropEffect: string } | null;
  preventDefault(): void;
};

/**
 * Install the guard; returns the uninstaller. Idempotent through
 * `data-desktop-chrome-guard` on the root element so HMR does not stack it.
 */
export function installDesktopChromeGuard(options: GuardOptions = {}): () => void {
  const host = options.host ?? (typeof window === "undefined" ? null : window);
  const root =
    options.root ?? (typeof document === "undefined" ? null : document.documentElement);
  if (!host || !root) return () => {};
  const enabled = options.enabled ?? isDesktopShell();
  if (!enabled) return () => {};
  if (root.dataset.desktopChromeGuard === "1") return () => {};
  root.dataset.desktopChromeGuard = "1";

  const platform = options.platform ?? hostPlatform();
  const note = options.onBlocked ?? (() => {});

  const onKeyDown = (event: GuardKeyEvent) => {
    const what = browserShortcut(event, platform);
    if (!what) return;
    event.preventDefault();
    note(what);
  };
  const onContextMenu = (event: GuardMouseEvent) => {
    if (!shouldBlockContextMenu(event)) return;
    event.preventDefault();
    note("context-menu");
  };
  const onMouse = (event: GuardMouseEvent) => {
    if (!isHistoryMouseButton(event.button)) return;
    event.preventDefault();
    note("aux-click");
  };
  const onAuxClick = (event: GuardMouseEvent) => {
    if (!shouldBlockAuxClick(event)) return;
    event.preventDefault();
    note("aux-click");
  };
  const onDragOver = (event: GuardDragEvent) => {
    if (event.defaultPrevented) return;
    // Cancelling dragover is what lets `drop` fire (and be cancelled) instead
    // of the engine navigating to the dropped file.
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "none";
  };
  const onDrop = (event: GuardDragEvent) => {
    if (!shouldBlockDrop(event)) return;
    event.preventDefault();
    note("drop");
  };

  // Capture for keys: a Radix menu or dialog that stops propagation must not
  // let Ctrl+R through. Bubble for the rest: the app's own handlers decide
  // first (custom context menus, drop zones).
  // The handlers read only the fields their structural types name, so the
  // casts to the DOM listener shape are safe for Event subclasses and for the
  // plain objects tests dispatch.
  const bindings: Array<[string, GuardListener, boolean]> = [
    ["keydown", onKeyDown as unknown as GuardListener, true],
    ["contextmenu", onContextMenu as unknown as GuardListener, false],
    ["mousedown", onMouse as unknown as GuardListener, false],
    ["mouseup", onMouse as unknown as GuardListener, false],
    ["auxclick", onAuxClick as unknown as GuardListener, false],
    ["dragover", onDragOver as unknown as GuardListener, false],
    ["drop", onDrop as unknown as GuardListener, false],
  ];
  for (const [type, listener, capture] of bindings) {
    host.addEventListener(type, listener, { capture });
  }

  return () => {
    for (const [type, listener, capture] of bindings) {
      host.removeEventListener(type, listener, { capture });
    }
    delete root.dataset.desktopChromeGuard;
  };
}
