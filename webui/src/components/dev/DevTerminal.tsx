// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import "@xterm/xterm/css/xterm.css";

import { useClient } from "@/providers/ClientProvider";
import { createTerminalRecovery, type TerminalRecovery } from "@/lib/terminal-recovery";
import { interruptSendsSigint, isInterruptChord } from "@/lib/terminal-keys";
import { paintTerminalViewport, terminalSurfaceStyle, terminalTheme, TERMINAL_FONT } from "./terminalTheme";
import "./terminal.css";

function encodeInput(data: string): string {
  const bytes = new TextEncoder().encode(data);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function decodeOutput(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/** What the gateway answers for a terminal id it has no session for. */
const TERMINAL_GONE_DETAIL = "terminal not found";

export default function DevTerminal({
  terminalId,
  shell,
  chatId,
  cwd,
  isDark,
  active,
  exitedLabel,
  restartedLabel,
  lostLabel,
  sandbox = false,
  sandboxLabel,
  sandboxUnavailableLabel,
  onSandbox,
  onExit,
}: {
  terminalId: string;
  shell?: string;
  chatId?: string | null;
  /** Folder to start the shell in; the project root when absent. */
  cwd?: string | null;
  isDark: boolean;
  active: boolean;
  exitedLabel: string;
  restartedLabel: string;
  lostLabel: string;
  /** Isolated terminal: ask the gateway to start the shell inside the OS sandbox. */
  sandbox?: boolean;
  /** Dim banner printed once when the shell really is confined. */
  sandboxLabel?: string;
  /** Printed once when isolation was asked for but the host cannot provide it. */
  sandboxUnavailableLabel?: string;
  /** Tells the tab whether the shell ended up confined. */
  onSandbox?: (terminalId: string, active: boolean) => void;
  onExit: (terminalId: string, code: number | null) => void;
}) {
  const { client } = useClient();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const openedRef = useRef(false);
  // Last confinement announced in the terminal: null until the first open.
  const sandboxStateRef = useRef<boolean | null>(null);
  const recoveryRef = useRef<TerminalRecovery | null>(null);
  if (recoveryRef.current === null) recoveryRef.current = createTerminalRecovery();
  const recovery = recoveryRef.current;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const term = new Terminal({
      fontSize: 12.5,
      fontFamily: TERMINAL_FONT,
      cursorBlink: true,
      cursorStyle: "bar",
      cursorInactiveStyle: "bar",
      cursorWidth: 2,
      convertEol: false,
      scrollback: 8000,
      theme: terminalTheme(isDark),
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(container);
    paintTerminalViewport(container, isDark);
    // GPU renderer: the default DOM renderer is visibly sluggish, especially
    // inside the desktop webviews. Fall back silently to DOM where WebGL is
    // unavailable (e.g. software-rendered WSLg) or lost at runtime.
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

    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown") return true;
      if (!isInterruptChord(event)) return true;
      if (!interruptSendsSigint(term.hasSelection())) return true;
      // Cmd+C with no selection is OS copy, not SIGINT. Send ETX ourselves.
      // Ctrl+C without a selection is already ETX in xterm; still prevent the
      // webview from treating it as a page-level copy.
      event.preventDefault();
      term.input("\x03");
      return false;
    });

    const sendOpen = () => {
      client.openTerminal({
        terminalId,
        shell,
        chatId: chatId ?? undefined,
        cwd: cwd ?? undefined,
        cols: term.cols,
        rows: term.rows,
        ...(sandbox ? { sandbox: true } : {}),
      });
    };

    // Say in the terminal itself whether the isolation asked for is real: a
    // tab badge alone is easy to miss when a global install fails. Repeated
    // only when the answer changes, e.g. a fresh shell after a gateway
    // restart that now (or no longer) has the sandbox helper.
    const announceSandbox = (confined: boolean) => {
      if (!sandbox || sandboxStateRef.current === confined) return;
      sandboxStateRef.current = confined;
      onSandbox?.(terminalId, confined);
      const line = confined ? sandboxLabel : sandboxUnavailableLabel;
      if (!line) return;
      term.write(confined ? `\x1b[2m[${line}]\x1b[0m\r\n` : `\x1b[33m[${line}]\x1b[0m\r\n`);
    };

    // The gateway re-attaches to a live shell if there still is one, and
    // otherwise spawns a fresh one under the same id - so re-opening is safe
    // whichever way the session was lost.
    const recover = () => {
      const decision = recovery.decide();
      if (decision === "wait") return;
      if (decision === "give-up") {
        term.write(`\r\n\x1b[31m${lostLabel}\x1b[0m\r\n`);
        return;
      }
      term.write(`\r\n\x1b[2m[${restartedLabel}]\x1b[0m\r\n`);
      sendOpen();
    };

    const unsubscribe = client.onTerminal(terminalId, (ev) => {
      if (ev.event === "terminal_output" && ev.data) {
        term.write(decodeOutput(ev.data));
      } else if (ev.event === "terminal_opened") {
        recovery.markAttached();
        announceSandbox(ev.sandbox === true);
      } else if (ev.event === "terminal_exit") {
        term.write(`\r\n\x1b[2m[${exitedLabel}]\x1b[0m\r\n`);
        recovery.markExited();
        onExit(terminalId, ev.exit_code ?? null);
      } else if (ev.event === "terminal_error" && ev.detail) {
        if (ev.detail === TERMINAL_GONE_DETAIL) {
          recover();
          return;
        }
        term.write(`\r\n\x1b[31m${ev.detail}\x1b[0m\r\n`);
      }
    });

    // Re-open as soon as the socket is back, before the client flushes the
    // keystrokes it buffered while down - those would otherwise land on a
    // session the gateway no longer knows and come back as errors.
    let wasOpen = client.status === "open";
    const unsubscribeStatus = client.onStatus((status) => {
      const dropped = !wasOpen;
      wasOpen = status === "open";
      // A tab opened while the socket was down has its open frame waiting in
      // the send queue; it is not a loss to recover from.
      if (status !== "open" || !dropped || !recovery.attached) return;
      recover();
    });

    const dataDisposable = term.onData((data) => {
      client.sendTerminalInput(terminalId, encodeInput(data));
    });

    if (!openedRef.current) {
      openedRef.current = true;
      sendOpen();
    }

    const resizeObserver = new ResizeObserver(() => {
      if (!container.offsetParent) return;
      try {
        fit.fit();
        client.resizeTerminal(terminalId, term.cols, term.rows);
      } catch {
        // ignore fit races while hidden
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      dataDisposable.dispose();
      unsubscribe();
      unsubscribeStatus();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
    // The terminal session lives as long as the component instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, terminalId]);

  useEffect(() => {
    const term = termRef.current;
    if (term) {
      term.options.theme = terminalTheme(isDark);
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
    const term = termRef.current;
    if (!fit || !term) return;
    const raf = requestAnimationFrame(() => {
      try {
        fit.fit();
        client.resizeTerminal(terminalId, term.cols, term.rows);
        term.focus();
      } catch {
        // ignore
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [active, client, terminalId]);

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
