// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { MessageSquarePlus } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  annotationIsOnMessage,
  getAnnotationRequest,
  getAnnotations,
  getActiveAnnotationScope,
  locateQuote,
  requestAnnotation,
  snapToWords,
  subscribeAnnotations,
  type AnnotationSource,
} from "@/lib/annotations";
import { cn } from "@/lib/utils";

import {
  buildTextIndex,
  pointOffset,
  rangeFromOffsets,
  rectOf,
  setLayerHighlights,
} from "./annotationDom";

/** The conversation a document belongs to: where its annotations are kept. */
export const AnnotationScopeContext = createContext<string | null>(null);

const BLOCKS = "p, li, pre, blockquote, h1, h2, h3, h4, h5, h6, tr";
const HANDLE_HIDE_MS = 280;

interface AnnotationLayerProps {
  /** Annotate an assistant message: its id and content fingerprint. */
  messageId?: string;
  messageKey?: string;
  /** Annotate the rendered view of a file: its project-relative path. */
  filePath?: string;
  /** False while the document is still being written. */
  enabled?: boolean;
  className?: string;
  children: ReactNode;
}

/**
 * Makes a rendered document annotatable: selecting text (or grabbing a block
 * by its margin handle) opens the annotation toolbar, and passages already
 * annotated stay highlighted.
 */
export function AnnotationLayer({
  messageId,
  messageKey,
  filePath,
  enabled = true,
  className,
  children,
}: AnnotationLayerProps) {
  const { t } = useTranslation();
  const contextScope = useContext(AnnotationScopeContext);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [owner] = useState(() => ({}));
  const activeScope = useSyncExternalStore(subscribeAnnotations, getActiveAnnotationScope, () => null);
  const scope = contextScope ?? activeScope;
  const drafts = useSyncExternalStore(
    subscribeAnnotations,
    () => getAnnotations(scope),
    () => getAnnotations(null),
  );
  const request = useSyncExternalStore(subscribeAnnotations, getAnnotationRequest, () => null);

  const source = useMemo<AnnotationSource | null>(() => {
    if (filePath) return { kind: "file", path: filePath };
    if (messageId) return { kind: "message", id: messageId, key: messageKey ?? "" };
    return null;
  }, [filePath, messageId, messageKey]);

  const isMine = useCallback(
    (candidate: AnnotationSource) => {
      if (candidate.kind === "file") return !!filePath && candidate.path === filePath;
      return !!messageId && annotationIsOnMessage(
        { source: candidate } as Parameters<typeof annotationIsOnMessage>[0],
        messageId,
        messageKey ?? "",
      );
    },
    [filePath, messageId, messageKey],
  );

  const passages = useMemo(() => {
    const list = drafts
      .filter((annotation) => isMine(annotation.source))
      .map((annotation) => ({
        quote: annotation.quote,
        offset: annotation.offset,
        keep: annotation.action === "keep",
      }));
    if (request && request.scope === scope && isMine(request.source)) {
      list.push({ quote: request.quote, offset: request.offset, keep: false });
    }
    return list;
  }, [drafts, isMine, request, scope]);

  const repaint = useCallback(() => {
    const root = rootRef.current;
    if (!root || !passages.length) {
      setLayerHighlights(owner, [], []);
      return;
    }
    const index = buildTextIndex(root);
    const mark: Range[] = [];
    const keep: Range[] = [];
    for (const passage of passages) {
      const start = locateQuote(index.text, passage.quote, passage.offset);
      const range = start < 0 ? null : rangeFromOffsets(index, start, start + passage.quote.length);
      if (range) (passage.keep ? keep : mark).push(range);
    }
    setLayerHighlights(owner, mark, keep);
  }, [owner, passages]);

  // The markdown renders lazily and re-renders while it settles: repaint when
  // the document changes, but only while this layer has something to show.
  useEffect(() => {
    repaint();
    const root = rootRef.current;
    if (!root || !passages.length || typeof MutationObserver === "undefined") return undefined;
    let frame = 0;
    const observer = new MutationObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(repaint);
    });
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [passages.length, repaint]);

  useEffect(() => () => setLayerHighlights(owner, [], []), [owner]);

  const annotateSelection = useCallback(() => {
    const root = rootRef.current;
    const selection = window.getSelection();
    if (!root || !source || !selection || selection.isCollapsed || !selection.rangeCount) return;
    const picked = selection.getRangeAt(0);
    if (!root.contains(picked.startContainer) || !root.contains(picked.endContainer)) return;
    const index = buildTextIndex(root);
    const snapped = snapToWords(
      index.text,
      pointOffset(index, root, picked.startContainer, picked.startOffset),
      pointOffset(index, root, picked.endContainer, picked.endOffset),
    );
    const range = rangeFromOffsets(index, snapped.start, snapped.end);
    if (!range) return;
    requestAnnotation({
      scope,
      source,
      quote: index.text.slice(snapped.start, snapped.end),
      offset: snapped.start,
      rect: rectOf(range),
    });
  }, [scope, source]);

  // The drag may end outside the document: listen for its release anywhere.
  const onMouseDown = useCallback(
    (event: ReactMouseEvent) => {
      if (!enabled || event.button !== 0) return;
      document.addEventListener("mouseup", () => window.setTimeout(annotateSelection, 0), { once: true });
    },
    [annotateSelection, enabled],
  );

  // ---- margin handle: grab a whole block in one click ----------------------
  const [handle, setHandle] = useState<{ left: number; top: number } | null>(null);
  const blockRef = useRef<Element | null>(null);
  const hideTimer = useRef<number | null>(null);

  const cancelHide = useCallback(() => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = null;
  }, []);
  const hideHandle = useCallback(() => {
    cancelHide();
    blockRef.current = null;
    setHandle(null);
  }, [cancelHide]);
  const scheduleHide = useCallback(() => {
    cancelHide();
    hideTimer.current = window.setTimeout(hideHandle, HANDLE_HIDE_MS);
  }, [cancelHide, hideHandle]);

  useEffect(() => cancelHide, [cancelHide]);

  // A handle left behind by a scroll would point at the wrong block.
  useEffect(() => {
    if (!handle) return undefined;
    window.addEventListener("scroll", hideHandle, { capture: true, passive: true });
    return () => window.removeEventListener("scroll", hideHandle, { capture: true });
  }, [handle, hideHandle]);

  const onMouseMove = useCallback(
    (event: ReactMouseEvent) => {
      if (!enabled || event.buttons !== 0) return;
      if (!window.matchMedia?.("(hover: hover)").matches) return;
      const root = rootRef.current;
      const target = event.target instanceof Element ? event.target.closest(BLOCKS) : null;
      if (!root || !target || !root.contains(target)) {
        if (blockRef.current) scheduleHide();
        return;
      }
      cancelHide();
      if (target === blockRef.current) return;
      blockRef.current = target;
      const box = target.getBoundingClientRect();
      setHandle({ left: Math.max(2, box.left - 26), top: box.top + 1 });
    },
    [cancelHide, enabled, scheduleHide],
  );

  const annotateBlock = useCallback(() => {
    const root = rootRef.current;
    const block = blockRef.current;
    if (!root || !block || !source) return;
    const index = buildTextIndex(root);
    const snapped = snapToWords(
      index.text,
      pointOffset(index, root, block, 0),
      pointOffset(index, root, block, block.childNodes.length),
    );
    if (snapped.end <= snapped.start) return;
    requestAnnotation({
      scope,
      source,
      quote: index.text.slice(snapped.start, snapped.end),
      offset: snapped.start,
      rect: rectOf(block),
    });
    hideHandle();
  }, [hideHandle, scope, source]);

  const handleLabel = t("annotations.block", { defaultValue: "Annotate this block" });

  return (
    <div
      ref={rootRef}
      className={className}
      data-annotation-layer=""
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseLeave={scheduleHide}
    >
      {children}
      {enabled && handle
        ? createPortal(
            <button
              type="button"
              aria-label={handleLabel}
              title={handleLabel}
              data-testid="annotation-block-handle"
              onMouseEnter={cancelHide}
              onMouseLeave={scheduleHide}
              onMouseDown={(event) => event.stopPropagation()}
              onClick={annotateBlock}
              style={{ left: handle.left, top: handle.top }}
              className={cn(
                "fixed z-[60] inline-flex h-6 w-6 items-center justify-center rounded-md",
                "border border-border/60 bg-background text-muted-foreground shadow-sm",
                "transition-colors hover:bg-muted hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden />
            </button>,
            document.body,
          )
        : null}
    </div>
  );
}
