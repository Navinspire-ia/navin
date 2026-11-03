// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Send, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  ComposerModeMenu,
  applyComposerTurnMode,
  type ComposerTurnMode,
} from "@/components/thread/ComposerModeMenu";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The modes a driving guest may pick. Each one goes out as the same slash
 * prefix the host composer uses, and the host only lets those through
 * (navin/share/host.py SAFE_SLASH); Montage is host-only.
 */
export const GUEST_MODES: readonly ComposerTurnMode[] = ["ask", "plan", "agent", "review", "security", "debug", "reverse"];

const MODE_KEY = "navin.guest.mode";

export function useGuestMode(): [ComposerTurnMode, (mode: ComposerTurnMode) => void] {
  const [mode, setMode] = useState<ComposerTurnMode>(() => {
    try {
      const saved = window.localStorage.getItem(MODE_KEY) as ComposerTurnMode | null;
      if (saved && GUEST_MODES.includes(saved)) return saved;
    } catch {
      // Private mode: the default.
    }
    return "agent";
  });
  const choose = useCallback((next: ComposerTurnMode) => {
    setMode(next);
    try {
      window.localStorage.setItem(MODE_KEY, next);
    } catch {
      // Kept for this visit only.
    }
  }, []);
  return [mode, choose];
}

// Round icon buttons at the bottom right of the box; the hit area reaches 40px.
const ROUND_ACTION = "relative h-8 w-8 shrink-0 rounded-full transition-transform active:scale-[0.96] after:absolute after:-inset-1 after:content-['']";

/**
 * The guest's message box, laid out like the host composer: the turn mode as a
 * menu at the bottom left, stop and send at the bottom right, inside the box.
 */
export function GuestComposer({
  canDrive,
  streaming,
  onSend,
  onStop,
}: {
  canDrive: boolean;
  streaming: boolean;
  onSend: (content: string) => void;
  onStop: () => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useGuestMode();
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const coarse = useMemo(() => window.matchMedia?.("(pointer: coarse)").matches ?? false, []);

  // The box grows with its text, up to a few lines.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || !canDrive) return;
    onSend(applyComposerTurnMode(text, mode));
    setDraft("");
  };

  const stopLabel = t("chat.liveShare.guest.control.stopAgent", { defaultValue: "Stop the agent" });
  const sendLabel = t("chat.liveShare.guest.send", { defaultValue: "Send" });
  return (
    <form
      onSubmit={submit}
      className="border-t border-border/70 px-3 py-2.5 sm:px-4 sm:py-3"
      style={{ paddingBottom: "max(0.625rem, env(safe-area-inset-bottom))" }}
    >
      <div
        className={cn(
          "mx-auto w-full max-w-3xl rounded-xl border border-border bg-background shadow-[0_1px_2px_rgba(15,23,42,0.06)] transition-[border-color,box-shadow] focus-within:border-ring/60 focus-within:ring-2 focus-within:ring-ring/25",
          !canDrive && "opacity-70",
        )}
      >
        <textarea
          ref={textareaRef}
          value={draft}
          disabled={!canDrive}
          rows={1}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // On touch keyboards Enter is a new line; the send button sends.
            if (e.key === "Enter" && !e.shiftKey && !coarse) submit(e);
          }}
          placeholder={
            canDrive
              ? t("chat.liveShare.guest.placeholder", { defaultValue: "Message the agent..." })
              : t("chat.liveShare.guest.placeholderReadOnly", { defaultValue: "Read only. Ask to drive to write." })
          }
          className="block max-h-40 min-h-[44px] w-full resize-none bg-transparent px-3 pb-1 pt-2.5 text-base outline-none disabled:cursor-not-allowed sm:text-sm"
        />
        <div className="flex min-h-10 items-center gap-2 px-2 pb-2">
          {canDrive ? (
            <ComposerModeMenu mode={mode} modes={GUEST_MODES} isHero={false} compact onChange={setMode} />
          ) : null}
          <div className="ms-auto flex items-center gap-2">
            {streaming && canDrive ? (
              <Button
                type="button"
                size="icon"
                variant="outline"
                title={stopLabel}
                aria-label={stopLabel}
                onClick={onStop}
                className={cn(
                  ROUND_ACTION,
                  "border-border/70 bg-card text-foreground/85 hover:bg-muted/65 hover:text-foreground",
                )}
              >
                <Square className="h-3 w-3 fill-current" aria-hidden />
              </Button>
            ) : null}
            {/* A prompt sent mid-run follows the current turn, as on the host. */}
            {!streaming || draft.trim() ? (
              <Button
                type="submit"
                size="icon"
                disabled={!canDrive || !draft.trim()}
                aria-label={sendLabel}
                className={ROUND_ACTION}
              >
                <Send className="h-3.5 w-3.5" aria-hidden />
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    </form>
  );
}
