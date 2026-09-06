import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowUp, FileCode2, MessageSquarePlus, SquareDashedMousePointer, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
  designModePrompt,
  pickedElementLabel,
  popoverPlacement,
  type PickedElement,
} from "./designMode";

const POPOVER_WIDTH = 360;
const POPOVER_ESTIMATED_HEIGHT = 150;

/**
 * Inline prompt anchored to the element the user picked in the preview: one
 * line about what should change, sent straight to the agent with the element
 * facts attached. Rendered by the preview browser over its iframe.
 */
export function DevDesignModePrompt({
  element,
  sourcePath,
  frame,
  canSend,
  onSend,
  onAddToChat,
  onClose,
}: {
  element: PickedElement | null;
  /** Project-relative source file of the component, when it was resolved. */
  sourcePath: string | null;
  /** Size of the preview frame the popover is positioned in. */
  frame: { width: number; height: number };
  /** False when nothing can send a message right now (viewer role, no chat). */
  canSend: boolean;
  onSend: (text: string) => void;
  onAddToChat?: (text: string, sourcePath: string | null) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const reduceMotion = useReducedMotion();
  const [instruction, setInstruction] = useState("");
  const [height, setHeight] = useState(POPOVER_ESTIMATED_HEIGHT);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // A new pick keeps the words already typed: retargeting is cheaper than
  // rewriting the request.
  useEffect(() => {
    if (!element) setInstruction("");
  }, [element]);

  useEffect(() => {
    if (!element) return;
    const id = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => window.cancelAnimationFrame(id);
  }, [element]);

  useLayoutEffect(() => {
    const node = cardRef.current;
    if (!node) return;
    const measured = node.getBoundingClientRect().height;
    if (measured && Math.abs(measured - height) > 1) setHeight(measured);
  });

  const placement = useMemo(
    () =>
      element
        ? popoverPlacement(element.rect, frame, { width: POPOVER_WIDTH, height })
        : null,
    [element, frame, height],
  );

  const submit = useCallback(
    (mode: "send" | "chat") => {
      if (!element) return;
      const text = designModePrompt(element, instruction, sourcePath);
      if (mode === "chat" && onAddToChat) onAddToChat(text, sourcePath);
      else if (canSend) onSend(text);
      else if (onAddToChat) onAddToChat(text, sourcePath);
      else return;
      setInstruction("");
      onClose();
    },
    [canSend, element, instruction, onAddToChat, onClose, onSend, sourcePath],
  );

  const label = element ? pickedElementLabel(element) : "";
  const ready = instruction.trim().length > 0;

  return (
    <AnimatePresence>
      {element && placement ? (
        <motion.div
          key={`${element.selector}:${element.rect.x}:${element.rect.y}`}
          ref={cardRef}
          role="dialog"
          aria-label={tx("dev.designMode.dialog", "Ask the agent about this element")}
          data-testid="design-mode-prompt"
          initial={reduceMotion ? false : { opacity: 0, y: placement.above ? 4 : -4, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.98 }}
          transition={{ type: "spring", duration: 0.28, bounce: 0 }}
          className="absolute z-20 flex flex-col gap-2 rounded-xl border border-border/70 bg-popover p-2.5 text-popover-foreground shadow-xl"
          style={{ left: placement.left, top: placement.top, width: POPOVER_WIDTH }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              onClose();
            }
          }}
        >
          <div className="flex min-w-0 items-center gap-1.5">
            <span
              className="inline-flex min-w-0 items-center gap-1 rounded-md bg-blue-500/10 px-1.5 py-0.5 text-[11.5px] font-semibold text-blue-700 dark:text-blue-300"
              title={element.selector}
              data-testid="design-mode-chip"
            >
              <SquareDashedMousePointer className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span className="truncate">{label}</span>
            </span>
            <button
              type="button"
              onClick={onClose}
              className="ml-auto inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              title={tx("dev.designMode.close", "Close (Esc)")}
              aria-label={tx("dev.designMode.close", "Close (Esc)")}
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>
          {sourcePath || element.text ? (
            <p className="flex min-w-0 items-center gap-1.5 px-0.5 text-[11px] text-muted-foreground">
              {sourcePath ? (
                <span
                  className="inline-flex min-w-0 shrink-0 items-center gap-1 font-mono text-[10.5px]"
                  title={sourcePath}
                  data-testid="design-mode-source"
                >
                  <FileCode2 className="h-3 w-3 shrink-0" aria-hidden />
                  <span className="max-w-[11rem] truncate">{sourcePath.split("/").pop()}</span>
                </span>
              ) : null}
              {element.text ? (
                <span className="min-w-0 truncate" title={element.text}>
                  {"\u201c"}
                  {element.text}
                  {"\u201d"}
                </span>
              ) : null}
            </p>
          ) : null}
          <textarea
            ref={inputRef}
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                if (ready) submit("send");
              }
            }}
            rows={2}
            spellCheck={false}
            placeholder={tx("dev.designMode.placeholder", "What should change here?")}
            className="w-full resize-none rounded-lg border border-input bg-background px-2.5 py-1.5 text-[13px] leading-5 placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            data-testid="design-mode-input"
          />
          <div className="flex items-center gap-1.5">
            <span
              className="min-w-0 flex-1 truncate text-[10.5px] text-muted-foreground"
              title={tx("dev.designMode.enterHint", "Enter to send, Shift+Enter for a new line")}
            >
              {tx("dev.designMode.enterShort", "Enter to send")}
            </span>
            {onAddToChat ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 gap-1 rounded-md px-2 text-[11.5px]"
                disabled={!ready}
                onClick={() => submit("chat")}
                title={tx(
                  "dev.designMode.addToChatHint",
                  "Put the request in the composer without sending it",
                )}
                data-testid="design-mode-add-to-chat"
              >
                <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden />
                {tx("dev.designMode.addToChat", "Add to chat")}
              </Button>
            ) : null}
            <Button
              type="button"
              size="sm"
              className={cn("h-7 gap-1 rounded-md px-2.5 text-[11.5px]")}
              disabled={!ready || (!canSend && !onAddToChat)}
              onClick={() => submit("send")}
              title={tx("dev.designMode.sendHint", "Send to the agent now (Enter)")}
              data-testid="design-mode-send"
            >
              <ArrowUp className="h-3.5 w-3.5" aria-hidden />
              {tx("dev.designMode.send", "Send to agent")}
            </Button>
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
