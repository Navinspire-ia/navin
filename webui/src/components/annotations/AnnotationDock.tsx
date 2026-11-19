// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { createPortal } from "react-dom";
import { ChevronRight, MessageSquareText, Send, Trash2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  ANNOTATION_ACTIONS,
  addAnnotation,
  annotationActionLabel,
  annotationLocation,
  clearAnnotations,
  dismissAnnotationRequest,
  formatReviewMessage,
  getActiveAnnotationScope,
  getAnnotationRequest,
  getAnnotations,
  removeAnnotation,
  setActiveAnnotationScope,
  sortAnnotations,
  subscribeAnnotations,
  updateAnnotation,
  type Annotation,
  type AnnotationAction,
  type AnnotationRequest,
} from "@/lib/annotations";
import { cn } from "@/lib/utils";

const TOOLBAR_WIDTH = 348;
const TOOLBAR_ROOM = 230;

function isEditable(target: EventTarget | null): boolean {
  return target instanceof HTMLElement
    && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
}

interface AnnotationDockProps {
  /** Conversation whose review this dock holds. */
  scope: string;
  /** Sends one user message in the conversation. */
  onSend: (text: string) => void;
  /** False while the agent is answering or the chat is read-only. */
  canSend: boolean;
}

/**
 * Review surface of one conversation: the toolbar that opens on a selection,
 * and the tray above the composer that collects the annotations until the
 * user sends them together.
 */
export function AnnotationDock({ scope, onSend, canSend }: AnnotationDockProps) {
  const { t } = useTranslation();
  const drafts = useSyncExternalStore(
    subscribeAnnotations,
    () => getAnnotations(scope),
    () => getAnnotations(null),
  );
  const request = useSyncExternalStore(subscribeAnnotations, getAnnotationRequest, () => null);
  const [open, setOpen] = useState(false);
  const ordered = useMemo(() => sortAnnotations(drafts), [drafts]);

  // Annotations made outside the thread (the code editor) land in the
  // conversation that is on screen.
  useEffect(() => {
    setActiveAnnotationScope(scope);
    return () => {
      if (getActiveAnnotationScope() === scope) setActiveAnnotationScope(null);
    };
  }, [scope]);

  const sendAll = useCallback(() => {
    if (!canSend || !ordered.length) return;
    onSend(formatReviewMessage(ordered));
    clearAnnotations(scope);
    setOpen(false);
  }, [canSend, onSend, ordered, scope]);

  const count = ordered.length;
  const title = t("annotations.count", {
    count,
    defaultValue: count === 1 ? "Review: 1 annotation" : `Review: ${count} annotations`,
  });
  const waitHint = t("annotations.wait", { defaultValue: "Wait for the current answer to finish." });

  return (
    <>
      {request && request.scope === scope ? (
        <AnnotationToolbar key={request.nonce} request={request} onSend={onSend} canSend={canSend} />
      ) : null}
      {count ? (
        <section
          aria-label={title}
          data-testid="annotation-tray"
          className="relative z-10 mb-2 w-full rounded-xl border border-border/60 bg-background/95 shadow-sm"
        >
          <div className="flex items-center gap-1.5 px-2 py-1.5">
            <button
              type="button"
              onClick={() => setOpen((current) => !current)}
              aria-expanded={open}
              className={cn(
                "flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-1.5 py-1 text-start text-[13.5px] font-medium",
                "hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              <ChevronRight
                className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
                aria-hidden
              />
              <MessageSquareText className="h-3.5 w-3.5 shrink-0 text-amber-500" aria-hidden />
              <span className="truncate">{title}</span>
            </button>
            <button
              type="button"
              onClick={() => clearAnnotations(scope)}
              className="rounded-md px-2 py-1 text-[13px] text-muted-foreground hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t("annotations.clear", { defaultValue: "Clear" })}
            </button>
            <button
              type="button"
              onClick={sendAll}
              disabled={!canSend}
              title={canSend ? undefined : waitHint}
              data-testid="annotation-send"
              className="inline-flex items-center gap-1.5 rounded-md bg-foreground px-2.5 py-1 text-[13px] font-medium text-background disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Send className="h-3 w-3" aria-hidden />
              {t("annotations.send", { defaultValue: "Send feedback" })}
            </button>
          </div>
          {open ? (
            <ol className="max-h-60 space-y-1 overflow-auto border-t border-border/50 p-2">
              {ordered.map((annotation) => (
                <AnnotationRow key={annotation.id} scope={scope} annotation={annotation} />
              ))}
            </ol>
          ) : null}
        </section>
      ) : null}
    </>
  );
}

function AnnotationRow({ scope, annotation }: { scope: string; annotation: Annotation }) {
  const { t } = useTranslation();
  const location = annotationLocation(annotation.source);
  return (
    <li className="rounded-lg bg-muted/35 px-2 py-1.5" data-testid="annotation-row">
      <div className="flex items-center gap-1.5">
        <select
          value={annotation.action}
          onChange={(event) => updateAnnotation(scope, annotation.id, { action: event.target.value as AnnotationAction })}
          aria-label={t("annotations.action", { defaultValue: "Action" })}
          className="h-6 shrink-0 rounded-md border border-border/60 bg-background px-1 text-[12.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {ANNOTATION_ACTIONS.map((spec) => (
            <option key={spec.id} value={spec.id}>
              {t(`annotations.actions.${spec.id}`, { defaultValue: spec.label })}
            </option>
          ))}
          <option value="comment">{t("annotations.actions.comment", { defaultValue: annotationActionLabel("comment") })}</option>
        </select>
        <span className="min-w-0 flex-1 truncate text-[13px] text-muted-foreground" title={annotation.quote}>
          {location ? <span className="me-1.5 font-mono text-[12px]">{location}</span> : null}
          {annotation.quote.replace(/\s+/g, " ")}
        </span>
        <button
          type="button"
          onClick={() => removeAnnotation(scope, annotation.id)}
          aria-label={t("annotations.remove", { defaultValue: "Remove annotation" })}
          className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      <input
        type="text"
        value={annotation.comment}
        onChange={(event) => updateAnnotation(scope, annotation.id, { comment: event.target.value })}
        placeholder={t("annotations.commentPlaceholder", { defaultValue: "Add a comment (optional)" })}
        className="mt-1 h-7 w-full rounded-md border border-transparent bg-transparent px-1.5 text-[13px] outline-none placeholder:text-muted-foreground/70 hover:border-border/60 focus:border-border focus:bg-background"
      />
    </li>
  );
}

function AnnotationToolbar({
  request,
  onSend,
  canSend,
}: {
  request: AnnotationRequest;
  onSend: (text: string) => void;
  canSend: boolean;
}) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const commentRef = useRef<HTMLTextAreaElement | null>(null);
  const [comment, setComment] = useState("");

  // Opened from an editor, the keyboard still belongs to that editor: a
  // letter would be typed into the file. Take the focus into the comment box.
  useEffect(() => {
    if (isEditable(document.activeElement)) commentRef.current?.focus();
  }, []);

  const finish = useCallback(() => {
    window.getSelection()?.removeAllRanges();
    dismissAnnotationRequest();
  }, []);

  const add = useCallback(
    (action: AnnotationAction) => {
      if (action === "comment" && !comment.trim()) return;
      addAnnotation(request.scope, {
        source: request.source,
        quote: request.quote,
        offset: request.offset,
        action,
        comment,
      });
      finish();
    },
    [comment, finish, request],
  );

  // Ctrl/Cmd+Enter: this one annotation goes to the agent right away. While
  // the agent is still answering it is kept as a draft instead of being lost.
  const sendNow = useCallback(() => {
    if (!comment.trim()) return;
    if (!canSend) {
      add("comment");
      return;
    }
    onSend(formatReviewMessage([{
      id: "now",
      source: request.source,
      quote: request.quote,
      offset: request.offset,
      action: "comment",
      comment: comment.trim(),
      createdAt: Date.now(),
    }]));
    finish();
  }, [add, canSend, comment, finish, onSend, request]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        finish();
        return;
      }
      if (event.ctrlKey || event.metaKey) return;
      const key = event.key.toLowerCase();
      const spec = ANNOTATION_ACTIONS.find((candidate) => candidate.key === key);
      // Inside the comment box a letter is text: Alt+letter tags from there.
      if (isEditable(event.target)) {
        if (event.altKey && spec && panelRef.current?.contains(event.target as Node)) {
          event.preventDefault();
          add(spec.id);
        }
        return;
      }
      if (event.altKey) return;
      if (spec) {
        event.preventDefault();
        add(spec.id);
      } else if (event.key === "Enter") {
        event.preventDefault();
        commentRef.current?.focus();
      }
    };
    const onMouseDown = (event: MouseEvent) => {
      if (panelRef.current?.contains(event.target as Node)) return;
      dismissAnnotationRequest();
    };
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("mousedown", onMouseDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("mousedown", onMouseDown, true);
    };
  }, [add, finish]);

  const { rect } = request;
  const viewportWidth = window.innerWidth || 1024;
  const viewportHeight = window.innerHeight || 768;
  const above = viewportHeight - rect.bottom < TOOLBAR_ROOM && rect.top > TOOLBAR_ROOM;
  const left = Math.max(8, Math.min(rect.left, viewportWidth - TOOLBAR_WIDTH - 8));
  const style = above
    ? { left, bottom: viewportHeight - rect.top + 8, width: TOOLBAR_WIDTH }
    : { left, top: Math.min(rect.bottom + 8, viewportHeight - 60), width: TOOLBAR_WIDTH };

  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label={t("annotations.toolbar", { defaultValue: "Annotate selection" })}
      data-testid="annotation-toolbar"
      style={style}
      className="fixed z-[70] rounded-xl border border-border/70 bg-popover p-2 text-popover-foreground shadow-lg animate-in fade-in-0 zoom-in-95 duration-150 motion-reduce:animate-none"
    >
      <div className="flex items-start gap-1">
        <div className="flex min-w-0 flex-1 flex-wrap gap-1">
          {ANNOTATION_ACTIONS.map((spec) => (
            <button
              key={spec.id}
              type="button"
              // Keep the text selection alive while the button is pressed.
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => add(spec.id)}
              data-testid={`annotation-action-${spec.id}`}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-[13px]",
                "transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              {t(`annotations.actions.${spec.id}`, { defaultValue: spec.label })}
              <kbd className="rounded bg-muted px-1 font-mono text-[11px] uppercase text-muted-foreground">{spec.key}</kbd>
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={finish}
          aria-label={t("common.close", { defaultValue: "Close" })}
          className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      <textarea
        ref={commentRef}
        value={comment}
        onChange={(event) => setComment(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Enter" || event.shiftKey) return;
          event.preventDefault();
          if (event.ctrlKey || event.metaKey) sendNow();
          else add("comment");
        }}
        rows={2}
        placeholder={t("annotations.commentHint", { defaultValue: "Or write your own comment..." })}
        data-testid="annotation-comment"
        className="mt-2 w-full resize-none rounded-md border border-border/60 bg-background px-2 py-1.5 text-[13.5px] leading-snug outline-none placeholder:text-muted-foreground/70 focus:border-ring"
      />
      <p className="mt-1 text-[12px] leading-snug text-muted-foreground">
        {t("annotations.keysHint", {
          defaultValue: "Press a letter to tag. Enter adds the comment, Ctrl+Enter sends it now, Alt+letter tags with the comment.",
        })}
      </p>
    </div>,
    document.body,
  );
}
