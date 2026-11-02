// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import "@xterm/xterm/css/xterm.css";

import { paintTerminalViewport, terminalSurfaceStyle, terminalTheme, TERMINAL_FONT } from "./terminalTheme";
import "./terminal.css";

/**
 * One xterm renderer shared by every agent terminal tab. Creating a Terminal
 * (char measuring, WebGL context) costs hundreds of ms on WebKitGTK, and the
 * workbench switches to the newest tab on every agent command, so the active
 * tab borrows this instance instead of building its own.
 */
interface PooledTerminal {
  term: Terminal;
  fit: FitAddon;
  /** Tab currently displaying the terminal, and how to detach its feed. */
  owner: symbol | null;
  release: (() => void) | null;
}

let pool: PooledTerminal | null = null;
let mountedTabs = 0;

function createPooledTerminal(container: HTMLElement, isDark: boolean): PooledTerminal {
  const term = new Terminal({
    fontSize: 12.5,
    fontFamily: TERMINAL_FONT,
    cursorBlink: false,
    cursorStyle: "bar",
    cursorInactiveStyle: "none",
    disableStdin: true,
    // Exec output is pipe-captured text with bare \n line endings.
    convertEol: true,
    scrollback: 8000,
    theme: terminalTheme(isDark, true),
  });
  const fit = new FitAddon();
  term.loadAddon(fit);
  // Opened into the first visible container so xterm measures real cells.
  term.open(container);
  try {
    const webgl = new WebglAddon();
    // Context loss falls back to the DOM renderer for the pool's lifetime.
    webgl.onContextLoss(() => webgl.dispose());
    term.loadAddon(webgl);
  } catch {
    // DOM renderer stays in place.
  }
  return { term, fit, owner: null, release: null };
}

function disposePool(): void {
  if (!pool) return;
  const current = pool;
  pool = null;
  current.release?.();
  current.term.dispose();
}

function safeFit(fit: FitAddon): void {
  try {
    fit.fit();
  } catch {
    // ignore fit races while hidden or mid-dispose
  }
}

/**
 * Read-only terminal tab for a command the agent runs through its exec tool
 * (Cursor's "agent terminals"). There is no PTY behind it: the workbench
 * pushes decoded output chunks streamed over the main websocket, so the tab
 * only displays - it never accepts input.
 */
export default function AgentExecTerminal({
  termId,
  backlog,
  subscribe,
  isDark,
  active,
}: {
  termId: string;
  /** Full output accumulated before this tab was mounted. */
  backlog: (id: string) => string;
  /** Live feed for one terminal id; returns the unsubscribe function. */
  subscribe: (id: string, handler: (chunk: string) => void) => () => void;
  isDark: boolean;
  active: boolean;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const isDarkRef = useRef(isDark);
  isDarkRef.current = isDark;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !active) return;

    const owner = Symbol(termId);
    if (!pool) {
      pool = createPooledTerminal(container, isDarkRef.current);
    } else {
      // Another tab still holding the renderer gives it up first.
      pool.release?.();
      const element = pool.term.element;
      if (element && element.parentElement !== container) container.appendChild(element);
      pool.term.options.theme = terminalTheme(isDarkRef.current, true);
    }
    const { term, fit } = pool;
    paintTerminalViewport(container, isDarkRef.current);

    term.reset();
    const existing = backlog(termId);
    if (existing) term.write(existing);
    const unsubscribe = subscribe(termId, (chunk) => term.write(chunk));

    const resizeObserver = new ResizeObserver(() => {
      if (!container.offsetParent) return;
      safeFit(fit);
    });
    resizeObserver.observe(container);
    safeFit(fit);
    const raf = requestAnimationFrame(() => safeFit(fit));

    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      unsubscribe();
      // Detach, never dispose: the next active tab reuses this renderer.
      const element = term.element;
      if (element && element.parentElement === container) container.removeChild(element);
      if (pool && pool.owner === owner) {
        pool.owner = null;
        pool.release = null;
      }
    };
    pool.owner = owner;
    pool.release = release;
    return release;
    // Only the visible command needs a renderer. Backlog replays on selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [termId, subscribe, active]);

  useEffect(() => {
    if (active && pool?.owner) {
      const { term } = pool;
      term.options.theme = terminalTheme(isDark, true);
      try {
        term.refresh(0, Math.max(0, term.rows - 1));
      } catch {
        // ignore if the renderer is mid-dispose
      }
    }
    paintTerminalViewport(containerRef.current, isDark);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark]);

  // Declared after the feed effect so its cleanup runs last on unmount.
  useEffect(() => {
    mountedTabs += 1;
    return () => {
      mountedTabs -= 1;
      if (mountedTabs === 0) disposePool();
    };
  }, []);

  return (
    <div
      ref={containerRef}
      dir="ltr"
      className="navin-terminal h-full w-full overflow-hidden px-2 py-1"
      style={{
        ...terminalSurfaceStyle(isDark),
        display: active ? undefined : "none",
      }}
    />
  );
}
