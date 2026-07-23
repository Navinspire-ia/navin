import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import { useClient } from "@/providers/ClientProvider";

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

const DARK_THEME = {
  background: "#111318",
  foreground: "#e6e8ee",
  cursor: "#9ecbff",
  selectionBackground: "#2b4a7a80",
};

const LIGHT_THEME = {
  background: "#ffffff",
  foreground: "#1f2328",
  cursor: "#0969da",
  selectionBackground: "#54aeff40",
};

export default function DevTerminal({
  terminalId,
  shell,
  chatId,
  isDark,
  active,
  exitedLabel,
  onExit,
}: {
  terminalId: string;
  shell?: string;
  chatId?: string | null;
  isDark: boolean;
  active: boolean;
  exitedLabel: string;
  onExit: (terminalId: string, code: number | null) => void;
}) {
  const { client } = useClient();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const openedRef = useRef(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const term = new Terminal({
      fontSize: 12.5,
      fontFamily:
        '"JetBrains Mono", "SFMono-Regular", "SF Mono", "Fira Code", "Cascadia Code", monospace',
      cursorBlink: true,
      convertEol: false,
      scrollback: 8000,
      theme: isDark ? DARK_THEME : LIGHT_THEME,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(container);
    try {
      fit.fit();
    } catch {
      // container may be hidden on first mount
    }
    termRef.current = term;
    fitRef.current = fit;

    const unsubscribe = client.onTerminal(terminalId, (ev) => {
      if (ev.event === "terminal_output" && ev.data) {
        term.write(decodeOutput(ev.data));
      } else if (ev.event === "terminal_exit") {
        term.write(`\r\n\x1b[2m[${exitedLabel}]\x1b[0m\r\n`);
        onExit(terminalId, ev.exit_code ?? null);
      } else if (ev.event === "terminal_error" && ev.detail) {
        term.write(`\r\n\x1b[31m${ev.detail}\x1b[0m\r\n`);
      }
    });

    const dataDisposable = term.onData((data) => {
      client.sendTerminalInput(terminalId, encodeInput(data));
    });

    if (!openedRef.current) {
      openedRef.current = true;
      client.openTerminal({
        terminalId,
        shell,
        chatId: chatId ?? undefined,
        cols: term.cols,
        rows: term.rows,
      });
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
      term.options.theme = isDark ? DARK_THEME : LIGHT_THEME;
    }
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
      className="h-full w-full overflow-hidden px-2 py-1"
      style={{ display: active ? undefined : "none" }}
    />
  );
}
