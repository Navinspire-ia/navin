// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import {
  isStudioModuleId,
  placeStudioModule,
  studioModuleAtY,
  type StudioModuleId,
} from "@/lib/studio-modules";

export const STUDIO_DRAG_THRESHOLD_PX = 6;

/** Pointer reorder for Studio lists. HTML5 drag is dead in the Tauri webview. */
export function useStudioModuleDrag({
  order,
  onReorder,
}: {
  order: readonly StudioModuleId[];
  onReorder: (next: StudioModuleId[]) => void;
}) {
  const listRef = useRef<HTMLElement | null>(null);
  const orderRef = useRef(order);
  orderRef.current = order;
  const onReorderRef = useRef(onReorder);
  onReorderRef.current = onReorder;
  const [draggingId, setDraggingId] = useState<StudioModuleId | null>(null);
  const [overId, setOverId] = useState<StudioModuleId | null>(null);
  const skipClickRef = useRef(false);
  const dragRef = useRef<{
    id: StudioModuleId;
    pointerId: number;
    startY: number;
    active: boolean;
  } | null>(null);

  const rowsFromDom = () => {
    const root = listRef.current;
    if (!root) return [];
    return Array.from(root.querySelectorAll<HTMLElement>("[data-studio-module]")).flatMap((node) => {
      const id = node.getAttribute("data-studio-module") || "";
      if (!isStudioModuleId(id)) return [];
      const box = node.getBoundingClientRect();
      return [{ id, top: box.top, bottom: box.bottom }];
    });
  };
  const rowsFromDomRef = useRef(rowsFromDom);
  rowsFromDomRef.current = rowsFromDom;

  const finishDrag = (clientY: number) => {
    const session = dragRef.current;
    dragRef.current = null;
    setDraggingId(null);
    setOverId(null);
    if (!session?.active) return;
    skipClickRef.current = true;
    const target = studioModuleAtY(rowsFromDom(), clientY);
    if (!target || target === session.id) return;
    onReorderRef.current(placeStudioModule(orderRef.current, session.id, target));
  };
  const finishDragRef = useRef(finishDrag);
  finishDragRef.current = finishDrag;

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const session = dragRef.current;
      if (!session || event.pointerId !== session.pointerId) return;
      if (!session.active && Math.abs(event.clientY - session.startY) < STUDIO_DRAG_THRESHOLD_PX) {
        return;
      }
      if (!session.active) {
        session.active = true;
        setDraggingId(session.id);
      }
      event.preventDefault();
      const target = studioModuleAtY(rowsFromDomRef.current(), event.clientY);
      setOverId(target && target !== session.id ? target : null);
    };
    const onUp = (event: PointerEvent) => {
      const session = dragRef.current;
      if (!session || event.pointerId !== session.pointerId) return;
      finishDragRef.current(event.clientY);
    };
    window.addEventListener("pointermove", onMove, { passive: false });
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, []);

  const startDrag = (
    id: StudioModuleId,
    event: ReactPointerEvent<HTMLElement>,
    options?: { preventDefault?: boolean },
  ) => {
    if (event.button !== 0) return;
    if ((event.target as HTMLElement).closest("button, [role=switch], a")) return;
    if (options?.preventDefault !== false) event.preventDefault();
    dragRef.current = {
      id,
      pointerId: event.pointerId,
      startY: event.clientY,
      active: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const consumeClickIfDragged = () => {
    if (!skipClickRef.current) return false;
    skipClickRef.current = false;
    return true;
  };

  return {
    listRef,
    draggingId,
    overId,
    startDrag,
    finishDrag,
    consumeClickIfDragged,
  };
}
