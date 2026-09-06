import { useCallback, useEffect, useRef, useState } from "react";

import {
  DEFAULT_ZOOM,
  applyUiZoom,
  clampZoom,
  isZoomControlAvailable,
  readStoredZoom,
  writeStoredZoom,
  zoomByWheel,
  zoomStep,
} from "@/lib/ui-zoom";
import {
  type DebugCommandPayload,
  startUiZoomDebugPoller,
} from "@/lib/ui-zoom-debug";

/** How long the zoom level stays on screen after the last change. */
const INDICATOR_MS = 1600;

export interface UiZoomController {
  zoom: number;
  /** False in a plain browser, where the browser's own zoom already applies. */
  available: boolean;
  visible: boolean;
  zoomIn: () => void;
  zoomOut: () => void;
  reset: () => void;
  dismiss: () => void;
}

/**
 * Ctrl/Cmd +/-/0 and Ctrl + wheel for the whole app, with the level persisted
 * across restarts. See `lib/ui-zoom.ts` for why the webui, and not the desktop
 * shell, owns these shortcuts.
 */
export function useUiZoom(): UiZoomController {
  const [available] = useState(isZoomControlAvailable);
  const [zoom, setZoom] = useState(readStoredZoom);
  const [visible, setVisible] = useState(false);
  const zoomRef = useRef(zoom);
  const hideTimer = useRef<number | null>(null);

  const dismiss = useCallback(() => {
    if (hideTimer.current !== null) {
      window.clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
    setVisible(false);
  }, []);

  const commit = useCallback((next: number) => {
    const value = clampZoom(next);
    zoomRef.current = value;
    setZoom(value);
    writeStoredZoom(value);
    applyUiZoom(value);
    setVisible(true);
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      hideTimer.current = null;
      setVisible(false);
    }, INDICATOR_MS);
  }, []);

  const zoomIn = useCallback(() => commit(zoomStep(zoomRef.current, 1)), [commit]);
  const zoomOut = useCallback(() => commit(zoomStep(zoomRef.current, -1)), [commit]);
  const reset = useCallback(() => commit(DEFAULT_ZOOM), [commit]);

  useEffect(() => {
    if (!available) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      // Prefer `code`: WebKitGTK on AZERTY/Linux often reports a layout
      // specific `key` for the same physical +/- / 0 keys.
      const plus = event.key === "+" || event.key === "=" || event.code === "Equal" || event.code === "NumpadAdd";
      const minus = event.key === "-" || event.key === "_" || event.code === "Minus" || event.code === "NumpadSubtract";
      const zero = event.key === "0" || event.code === "Digit0" || event.code === "Numpad0";
      if (plus) {
        event.preventDefault();
        event.stopPropagation();
        zoomIn();
        return;
      }
      if (minus) {
        event.preventDefault();
        event.stopPropagation();
        zoomOut();
        return;
      }
      if (zero) {
        event.preventDefault();
        event.stopPropagation();
        reset();
      }
    };

    const onWheel = (event: WheelEvent) => {
      if (event.defaultPrevented) return;
      if (!event.ctrlKey && !event.metaKey) return;
      // Capture so WebKitGTK cannot swallow Ctrl + scroll before bubble.
      event.preventDefault();
      event.stopPropagation();
      commit(zoomByWheel(zoomRef.current, event.deltaY, event.deltaMode));
    };

    const keyOpts: AddEventListenerOptions = { capture: true };
    const wheelOpts: AddEventListenerOptions = { capture: true, passive: false };
    window.addEventListener("keydown", onKeyDown, keyOpts);
    document.addEventListener("keydown", onKeyDown, keyOpts);
    window.addEventListener("wheel", onWheel, wheelOpts);
    document.addEventListener("wheel", onWheel, wheelOpts);
    return () => {
      window.removeEventListener("keydown", onKeyDown, keyOpts);
      document.removeEventListener("keydown", onKeyDown, keyOpts);
      document.removeEventListener("wheel", onWheel, wheelOpts);
    };
  }, [available, commit, reset, zoomIn, zoomOut]);

   useEffect(() => () => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
  }, []);

  // Localhost debug channel: while the desktop shell is up, poll the gateway
  // for commands queued by `curl http://127.0.0.1:8766/api/debug/ui-zoom`
  // and apply them through the same `commit` path the keyboard shortcuts use
  // so the ZoomIndicator state stays in sync with poller-driven changes.
  useEffect(() => {
    if (!available) return;
    return startUiZoomDebugPoller({
      onCommand: (command: DebugCommandPayload) => {
        if (command.zoom !== null && Number.isFinite(command.zoom)) {
          commit(command.zoom);
          return true;
        }
        if (command.action === "in") {
          commit(zoomStep(zoomRef.current, 1));
          return true;
        }
        if (command.action === "out") {
          commit(zoomStep(zoomRef.current, -1));
          return true;
        }
        if (command.action === "reset") {
          commit(DEFAULT_ZOOM);
          return true;
        }
        return false;
      },
    }).stop;
  }, [available, commit]);


  return { zoom, available, visible, zoomIn, zoomOut, reset, dismiss };
}
