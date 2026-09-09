// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import {
  clampFixedToVisualViewport,
  documentCssZoom,
  documentPointerUsesFixedLayerSplit,
  viewportPointerToFixed,
} from "@/lib/ui-zoom";
import { cn } from "@/lib/utils";

function pointerFixedLayer() {
  return {
    zoom: documentCssZoom(),
    splits: documentPointerUsesFixedLayerSplit(),
  };
}

export interface ContextMenuEntry {
  id: string;
  label: string;
  onSelect?: () => void;
  icon?: ReactNode;
  shortcut?: string;
  danger?: boolean;
  disabled?: boolean;
  /** Renders a divider above this entry. */
  separatorBefore?: boolean;
}

export interface ContextMenuState {
  x: number;
  y: number;
  items: ContextMenuEntry[];
}

const EDGE_GAP = 8;

/**
 * A right-click menu placed at the pointer.
 *
 * Rendered in a portal so a menu opened near the bottom of a scrolling tree is
 * not clipped by its container, and repositioned after measuring so it stays
 * on screen rather than running off the corner it was opened in.
 */
export function ContextMenu({
  state,
  onClose,
}: {
  state: ContextMenuState | null;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [ready, setReady] = useState(false);

  useLayoutEffect(() => {
    if (!state) {
      setReady(false);
      return;
    }
    const node = ref.current;
    if (!node) return;
    // offsetWidth/Height are layout pixels, matching `position: fixed`
    // under WebKitGTK's paint-only zoom. getBoundingClientRect can be
    // visual (already multiplied by zoom) on some engines.
    const width = node.offsetWidth;
    const height = node.offsetHeight;
    const viewport = window.visualViewport;
    const { zoom, splits } = pointerFixedLayer();
    setPosition(
      clampFixedToVisualViewport({
        x: state.x,
        y: state.y,
        menuWidth: width,
        menuHeight: height,
        viewportWidth: viewport?.width ?? window.innerWidth,
        viewportHeight: viewport?.height ?? window.innerHeight,
        zoom,
        splits,
        gap: EDGE_GAP,
      }),
    );
    setReady(true);
  }, [state]);

  useEffect(() => {
    if (!state) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    const onPointerDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) onClose();
    };
    // Capture: a click meant to dismiss the menu should not also land on the
    // tree row underneath it and open a file.
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("mousedown", onPointerDown, true);
    window.addEventListener("resize", onClose);
    window.addEventListener("blur", onClose);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("mousedown", onPointerDown, true);
      window.removeEventListener("resize", onClose);
      window.removeEventListener("blur", onClose);
    };
  }, [onClose, state]);

  if (!state) return null;

  return createPortal(
    <div
      ref={ref}
      role="menu"
      style={{ left: position.x, top: position.y, opacity: ready ? 1 : 0 }}
      className="fixed z-[100] min-w-[13rem] max-w-[20rem] rounded-[14px] border border-border bg-popover p-1 text-popover-foreground shadow-lg dark:border-border"
      onContextMenu={(event) => event.preventDefault()}
    >
      {state.items.map((item) => (
        <div key={item.id}>
          {item.separatorBefore ? (
            <div className="-mx-1 my-1 h-px bg-border/50" aria-hidden />
          ) : null}
          <button
            type="button"
            role="menuitem"
            disabled={item.disabled}
            onClick={() => {
              onClose();
              item.onSelect?.();
            }}
            className={cn(
              "flex w-full items-center gap-2 rounded-[10px] px-2.5 py-1.5 text-left text-[13px] transition-colors",
              "disabled:pointer-events-none disabled:opacity-40",
              item.danger
                ? "text-destructive hover:bg-destructive/10"
                : "hover:bg-foreground/[0.06] dark:hover:bg-white/[0.08]",
            )}
          >
            {item.icon ? (
              <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center text-muted-foreground">
                {item.icon}
              </span>
            ) : null}
            <span className="min-w-0 flex-1 truncate">{item.label}</span>
            {item.shortcut ? (
              <span className="shrink-0 text-[11px] text-muted-foreground">
                {item.shortcut}
              </span>
            ) : null}
          </button>
        </div>
      ))}
    </div>,
    document.body,
  );
}

/** Opens `items` at the pointer, with the browser's own menu suppressed. */
export function useContextMenu() {
  const [state, setState] = useState<ContextMenuState | null>(null);

  const open = useCallback(
    (event: { preventDefault: () => void; clientX: number; clientY: number }, items: ContextMenuEntry[]) => {
      event.preventDefault();
      if (items.length === 0) return;
      const { zoom, splits } = pointerFixedLayer();
      const point = viewportPointerToFixed(event.clientX, event.clientY, zoom, splits);
      setState({ x: point.x, y: point.y, items });
    },
    [],
  );

  const close = useCallback(() => setState(null), []);

  return { state, open, close };
}
