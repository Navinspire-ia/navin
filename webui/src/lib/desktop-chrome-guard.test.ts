// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  browserShortcut,
  installDesktopChromeGuard,
  isEditableTarget,
  isHistoryMouseButton,
  probeEngine,
  safeReload,
  shouldBlockAuxClick,
  shouldBlockContextMenu,
  shouldBlockDrop,
  type KeyLike,
} from "./desktop-chrome-guard";

function key(partial: Partial<KeyLike> & { key: string }): KeyLike {
  return { ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...partial };
}

describe("browserShortcut", () => {
  it("names the reload chords on every platform", () => {
    expect(browserShortcut(key({ key: "F5" }), "linux")).toBe("reload");
    expect(browserShortcut(key({ key: "F5", ctrlKey: true }), "windows")).toBe("reload");
    expect(browserShortcut(key({ key: "r", ctrlKey: true }), "linux")).toBe("reload");
    expect(browserShortcut(key({ key: "R", ctrlKey: true, shiftKey: true }), "windows")).toBe(
      "reload",
    );
    expect(browserShortcut(key({ key: "r", metaKey: true }), "macos")).toBe("reload");
  });

  it("uses the platform modifier: Ctrl on Windows/Linux, Cmd on macOS", () => {
    expect(browserShortcut(key({ key: "r", metaKey: true }), "linux")).toBeNull();
    expect(browserShortcut(key({ key: "r", ctrlKey: true }), "macos")).toBeNull();
    // Ctrl+Cmd or Alt in the chord is never a browser shortcut.
    expect(browserShortcut(key({ key: "r", ctrlKey: true, metaKey: true }), "linux")).toBeNull();
    expect(browserShortcut(key({ key: "r", ctrlKey: true, altKey: true }), "linux")).toBeNull();
  });

  it("covers the page menu features: print, save, source, open, windows", () => {
    expect(browserShortcut(key({ key: "p", ctrlKey: true }), "linux")).toBe("print");
    expect(browserShortcut(key({ key: "s", ctrlKey: true }), "linux")).toBe("save");
    expect(browserShortcut(key({ key: "s", ctrlKey: true, shiftKey: true }), "linux")).toBe("save");
    expect(browserShortcut(key({ key: "u", ctrlKey: true }), "linux")).toBe("view-source");
    expect(browserShortcut(key({ key: "o", ctrlKey: true }), "linux")).toBe("open-file");
    expect(browserShortcut(key({ key: "n", ctrlKey: true }), "linux")).toBe("new-window");
    expect(browserShortcut(key({ key: "t", ctrlKey: true }), "linux")).toBe("new-window");
    expect(browserShortcut(key({ key: "w", ctrlKey: true }), "linux")).toBe("close-window");
  });

  it("leaves the app's own chords alone", () => {
    // Ctrl+Shift+P command palette, Ctrl+Shift+O new chat, Ctrl+K, Ctrl+Shift+F.
    expect(browserShortcut(key({ key: "P", ctrlKey: true, shiftKey: true }), "linux")).toBeNull();
    expect(browserShortcut(key({ key: "O", ctrlKey: true, shiftKey: true }), "linux")).toBeNull();
    expect(browserShortcut(key({ key: "k", ctrlKey: true }), "linux")).toBeNull();
    expect(browserShortcut(key({ key: "F", ctrlKey: true, shiftKey: true }), "linux")).toBeNull();
    expect(browserShortcut(key({ key: "f", ctrlKey: true }), "linux")).toBeNull();
    // Plain typing and editing keys.
    expect(browserShortcut(key({ key: "r" }), "linux")).toBeNull();
    expect(browserShortcut(key({ key: "ArrowLeft" }), "linux")).toBeNull();
    expect(browserShortcut(key({ key: "Backspace" }), "linux")).toBeNull();
  });

  it("blocks Alt+arrow history on Windows/Linux only", () => {
    expect(browserShortcut(key({ key: "ArrowLeft", altKey: true }), "windows")).toBe("history");
    expect(browserShortcut(key({ key: "ArrowRight", altKey: true }), "linux")).toBe("history");
    expect(browserShortcut(key({ key: "Home", altKey: true }), "linux")).toBe("history");
    // Alt+arrow is word movement on macOS, not navigation.
    expect(browserShortcut(key({ key: "ArrowLeft", altKey: true }), "macos")).toBeNull();
    // Ctrl+Alt+arrow is a desktop (workspace) chord, not the browser's.
    expect(
      browserShortcut(key({ key: "ArrowLeft", altKey: true, ctrlKey: true }), "linux"),
    ).toBeNull();
  });
});

describe("context menu, mouse and drop decisions", () => {
  const editable = (matches: boolean) => ({ closest: () => (matches ? {} : null) });

  it("keeps the native edit menu in text fields and blocks the page menu elsewhere", () => {
    expect(isEditableTarget(editable(true))).toBe(true);
    expect(isEditableTarget(editable(false))).toBe(false);
    expect(isEditableTarget(null)).toBe(false);
    expect(shouldBlockContextMenu({ defaultPrevented: false, target: editable(false) })).toBe(true);
    expect(shouldBlockContextMenu({ defaultPrevented: false, target: editable(true) })).toBe(false);
  });

  it("defers to custom app menus that already handled the event", () => {
    expect(shouldBlockContextMenu({ defaultPrevented: true, target: editable(false) })).toBe(false);
  });

  it("recognises the history mouse buttons and middle clicks on links", () => {
    expect(isHistoryMouseButton(3)).toBe(true);
    expect(isHistoryMouseButton(4)).toBe(true);
    expect(isHistoryMouseButton(0)).toBe(false);
    expect(isHistoryMouseButton(2)).toBe(false);
    const link = { closest: (selector: string) => (selector === "a[href]" ? {} : null) };
    expect(shouldBlockAuxClick({ button: 1, target: link })).toBe(true);
    expect(shouldBlockAuxClick({ button: 1, target: editable(false) })).toBe(false);
    expect(shouldBlockAuxClick({ button: 3, target: editable(false) })).toBe(true);
  });

  it("blocks drops nobody claimed and leaves app drop zones alone", () => {
    expect(shouldBlockDrop({ defaultPrevented: false })).toBe(true);
    expect(shouldBlockDrop({ defaultPrevented: true })).toBe(false);
  });
});

describe("installDesktopChromeGuard", () => {
  // Node has EventTarget and Event but no DOM: the host doubles as the event
  // target, and `closest` on it stands for the element under the pointer.
  class Host extends EventTarget {
    editable = false;
    closest(selector: string): object | null {
      if (selector === "a[href]") return null;
      return this.editable ? {} : null;
    }
    fire(type: string, detail: Record<string, unknown> = {}): Event {
      const event = Object.assign(new Event(type, { cancelable: true }), detail);
      this.dispatchEvent(event);
      return event;
    }
  }
  const keyEvent = (partial: Partial<KeyLike> & { key: string }) => ({
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    ...partial,
  });

  let uninstall: (() => void) | null = null;
  let host: Host;
  let root: { dataset: Record<string, string | undefined> };
  beforeEach(() => {
    host = new Host();
    root = { dataset: {} };
  });
  afterEach(() => {
    uninstall?.();
    uninstall = null;
  });

  it("is a no-op outside the desktop shell", () => {
    uninstall = installDesktopChromeGuard({ enabled: false, platform: "linux", host, root });
    expect(host.fire("keydown", keyEvent({ key: "F5" })).defaultPrevented).toBe(false);
    expect(root.dataset.desktopChromeGuard).toBeUndefined();
  });

  it("cancels reload keys, the page context menu, history buttons and unclaimed drops", () => {
    const blocked: string[] = [];
    uninstall = installDesktopChromeGuard({
      enabled: true,
      platform: "linux",
      host,
      root,
      onBlocked: (what) => blocked.push(what),
    });
    expect(root.dataset.desktopChromeGuard).toBe("1");

    expect(host.fire("keydown", keyEvent({ key: "r", ctrlKey: true })).defaultPrevented).toBe(true);
    expect(
      host.fire("keydown", keyEvent({ key: "P", ctrlKey: true, shiftKey: true })).defaultPrevented,
    ).toBe(false);

    expect(host.fire("contextmenu", { button: 2 }).defaultPrevented).toBe(true);
    host.editable = true;
    expect(host.fire("contextmenu", { button: 2 }).defaultPrevented).toBe(false);
    host.editable = false;

    expect(host.fire("mouseup", { button: 3 }).defaultPrevented).toBe(true);
    expect(host.fire("mouseup", { button: 0 }).defaultPrevented).toBe(false);

    const dataTransfer = { dropEffect: "copy" };
    expect(host.fire("dragover", { dataTransfer }).defaultPrevented).toBe(true);
    expect(dataTransfer.dropEffect).toBe("none");
    expect(host.fire("drop", {}).defaultPrevented).toBe(true);

    expect(blocked).toEqual(["reload", "context-menu", "aux-click", "drop"]);
  });

  it("defers to app handlers that already claimed the event", () => {
    uninstall = installDesktopChromeGuard({ enabled: true, platform: "linux", host, root });
    const claimed = new Event("contextmenu", { cancelable: true });
    claimed.preventDefault();
    let reached = false;
    host.addEventListener("contextmenu", () => {
      reached = true;
    });
    host.dispatchEvent(claimed);
    expect(reached).toBe(true);
    // Already prevented by the app; the guard did nothing extra (no throw, no
    // double handling), and the same holds for drops.
    const drop = new Event("drop", { cancelable: true });
    drop.preventDefault();
    host.dispatchEvent(drop);
    expect(drop.defaultPrevented).toBe(true);
  });

  it("listens for keys in the capture phase so stopped propagation cannot leak a reload", () => {
    const captures: Array<[string, boolean | undefined]> = [];
    const spy = {
      addEventListener: (
        type: string,
        listener: (event: Event) => void,
        options?: { capture: boolean },
      ) => {
        captures.push([type, options?.capture]);
        host.addEventListener(type, listener, options);
      },
      removeEventListener: (
        type: string,
        listener: (event: Event) => void,
        options?: { capture: boolean },
      ) => {
        host.removeEventListener(type, listener, options);
      },
    };
    uninstall = installDesktopChromeGuard({ enabled: true, platform: "windows", host: spy, root });
    expect(captures).toContainEqual(["keydown", true]);
    expect(captures).toContainEqual(["contextmenu", false]);
    expect(captures).toContainEqual(["drop", false]);
  });

  it("installs once and removes its marker and listeners on uninstall", () => {
    uninstall = installDesktopChromeGuard({ enabled: true, platform: "linux", host, root });
    const second = installDesktopChromeGuard({ enabled: true, platform: "linux", host, root });
    second();
    expect(root.dataset.desktopChromeGuard).toBe("1");
    uninstall();
    uninstall = null;
    expect(root.dataset.desktopChromeGuard).toBeUndefined();
    expect(host.fire("keydown", keyEvent({ key: "F5" })).defaultPrevented).toBe(false);
  });
});

describe("safeReload", () => {
  it("reloads at once in a browser tab", async () => {
    const reload = vi.fn();
    const probe = vi.fn(async () => false);
    await expect(safeReload({ desktopShell: false, reload, probe })).resolves.toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(probe).not.toHaveBeenCalled();
  });

  it("waits for the engine before reloading in the shell", async () => {
    const reload = vi.fn();
    const answers = [false, false, true];
    const probe = vi.fn(async () => answers.shift() ?? true);
    const sleep = vi.fn(async () => {});
    await expect(
      safeReload({ desktopShell: true, reload, probe, sleep, maxWaitMs: 60_000 }),
    ).resolves.toBe(true);
    expect(probe).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledTimes(2);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("gives up without reloading when the engine stays silent", async () => {
    const reload = vi.fn();
    const probe = vi.fn(async () => false);
    const sleep = vi.fn(async () => {});
    await expect(
      safeReload({ desktopShell: true, reload, probe, sleep, maxWaitMs: 0 }),
    ).resolves.toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it("probes /health without caching and treats any failure as not ready", async () => {
    const ok = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/health");
      expect(init?.cache).toBe("no-store");
      return new Response("{}", { status: 200 });
    });
    await expect(probeEngine(ok as unknown as typeof fetch)).resolves.toBe(true);
    const down = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(probeEngine(down as unknown as typeof fetch)).resolves.toBe(false);
    const gatewayError = vi.fn(async () => new Response("", { status: 502 }));
    await expect(probeEngine(gatewayError as unknown as typeof fetch)).resolves.toBe(false);
  });
});

describe("wiring", () => {
  const read = (rel: string) => readFileSync(resolve(__dirname, rel), "utf8");

  it("is installed before the first paint and drives the panel restart button", () => {
    const main = read("../main.tsx");
    expect(main).toContain('from "./lib/desktop-chrome-guard"');
    expect(main).toContain("installDesktopChromeGuard();");
    const boundary = read("../components/PanelErrorBoundary.tsx");
    expect(boundary).toContain("safeReload()");
    expect(boundary).not.toContain("window.location.reload()");
  });

  it("keeps the Electron shell on the Navin splash when a load or renderer fails", () => {
    const electron = readFileSync(
      resolve(__dirname, "../../../desktop-electron/main.js"),
      "utf8",
    );
    expect(electron).toContain('"did-fail-load"');
    expect(electron).toContain('"render-process-gone"');
    expect(electron).toContain("recoverAfterFailedLoad(");
    expect(electron).toContain("guardWebuiLoads(mainWindow)");
  });

  it("makes the Tauri supervisor probe /health before reloading the WebUI", () => {
    const tauri = readFileSync(
      resolve(__dirname, "../../../desktop/src-tauri/src/main.rs"),
      "utf8",
    );
    expect(tauri).toContain("fn safe_reload_script(webui_port: u16)");
    expect(tauri).toContain("window.eval(&safe_reload_script(webui_port))");
    expect(tauri).not.toContain('window.eval("location.reload()")');
  });
});
