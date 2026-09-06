import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import "@xterm/xterm/css/xterm.css";

import { paintTerminalViewport, terminalSurfaceStyle, terminalTheme, TERMINAL_FONT } from "./terminalTheme";
import "./terminal.css";

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
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

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
    term.open(container);
    paintTerminalViewport(container, isDark);
    try {
      const webgl = new WebglAddon();
      webgl.onContextLoss(() => webgl.dispose());
      term.loadAddon(webgl);
    } catch {
      // DOM renderer stays in place.
    }
    try {
      fit.fit();
    } catch {
      // container may be hidden on first mount
    }
    termRef.current = term;
    fitRef.current = fit;

    const existing = backlog(termId);
    if (existing) term.write(existing);
    const unsubscribe = subscribe(termId, (chunk) => term.write(chunk));

    const resizeObserver = new ResizeObserver(() => {
      if (!container.offsetParent) return;
      try {
        fit.fit();
      } catch {
        // ignore fit races while hidden
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      unsubscribe();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
    // The feed lives as long as the component instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [termId, subscribe]);

  useEffect(() => {
    const term = termRef.current;
    if (term) {
      term.options.theme = terminalTheme(isDark, true);
      try {
        term.refresh(0, Math.max(0, term.rows - 1));
      } catch {
        // ignore if the renderer is mid-dispose
      }
    }
    paintTerminalViewport(containerRef.current, isDark);
  }, [isDark]);

  useEffect(() => {
    if (!active) return;
    const fit = fitRef.current;
    if (!fit) return;
    const raf = requestAnimationFrame(() => {
      try {
        fit.fit();
      } catch {
        // ignore
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [active]);

  return (
    <div
      ref={containerRef}
      className="navin-terminal h-full w-full overflow-hidden px-2 py-1"
      style={{
        ...terminalSurfaceStyle(isDark),
        display: active ? undefined : "none",
      }}
    />
  );
}
