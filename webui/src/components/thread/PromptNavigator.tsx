// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ListTree, Loader2, Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  type ListedPrompt,
  type NavigatorPrompt,
  navigatorPrompts,
  userPromptAnchors,
} from "@/components/thread/promptNavigation";
import { fmtDateTime } from "@/lib/format";
import type { UIMessage } from "@/lib/types";
import { cn } from "@/lib/utils";

interface PromptNavigatorProps {
  messages: UIMessage[];
  /** Prompts of the chat before the first loaded message (the thread is paged). */
  userMessageOffset?: number;
  /** Every prompt of the chat, oldest first; asked for each time the list opens. */
  loadPrompts?: () => Promise<ListedPrompt[]>;
  onJumpToPrompt: (promptId: string) => void;
  /** Load the older pages up to this prompt, then show it; false when it could not. */
  onJumpToPromptIndex?: (index: number) => Promise<boolean>;
}

type ListState =
  | { status: "idle" | "loading" }
  | { status: "ready"; prompts: ListedPrompt[] }
  | { status: "error" };

export function PromptNavigator({
  messages,
  userMessageOffset = 0,
  loadPrompts,
  onJumpToPrompt,
  onJumpToPromptIndex,
}: PromptNavigatorProps) {
  const { i18n, t } = useTranslation();
  const loaded = useMemo(() => userPromptAnchors(messages), [messages]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [list, setList] = useState<ListState>({ status: "idle" });
  const [jumping, setJumping] = useState<number | null>(null);
  const [jumpFailed, setJumpFailed] = useState(false);
  const openSeq = useRef(0);

  const needsList = userMessageOffset > 0 && Boolean(loadPrompts);

  useEffect(() => {
    if (!open || !needsList || !loadPrompts) return;
    const seq = ++openSeq.current;
    setList({ status: "loading" });
    loadPrompts().then(
      (prompts) => {
        if (seq === openSeq.current) setList({ status: "ready", prompts });
      },
      () => {
        if (seq === openSeq.current) setList({ status: "error" });
      },
    );
  }, [loadPrompts, needsList, open]);

  const prompts = useMemo(
    () => navigatorPrompts(
      loaded,
      list.status === "ready" ? list.prompts : null,
      userMessageOffset,
    ),
    [list, loaded, userMessageOffset],
  );

  const filteredPrompts = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return prompts;
    return prompts.filter((prompt) =>
      `${prompt.label}\n${prompt.preview}`.toLocaleLowerCase().includes(needle),
    );
  }, [prompts, query]);

  const jump = useCallback((prompt: NavigatorPrompt) => {
    if (prompt.id) {
      setOpen(false);
      onJumpToPrompt(prompt.id);
      return;
    }
    if (!onJumpToPromptIndex || jumping !== null) return;
    setJumping(prompt.index);
    setJumpFailed(false);
    onJumpToPromptIndex(prompt.index).then(
      (reached) => {
        setJumping(null);
        if (reached) setOpen(false);
        else setJumpFailed(true);
      },
      () => {
        setJumping(null);
        setJumpFailed(true);
      },
    );
  }, [jumping, onJumpToPrompt, onJumpToPromptIndex]);

  const onOpenChange = useCallback((next: boolean) => {
    setOpen(next);
    if (!next) setJumpFailed(false);
  }, []);

  if (loaded.length === 0 && userMessageOffset === 0) return null;

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className={cn(
          "host-no-drag h-8 w-8 rounded-full text-muted-foreground/80",
          "hover:bg-accent/40 hover:text-foreground",
        )}
        aria-label={t("thread.promptNavigator.open")}
        onClick={() => setOpen(true)}
      >
        <ListTree className="h-4 w-4" />
      </Button>

      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="right"
          aria-describedby={undefined}
          className="w-[min(92vw,24rem)] gap-0 p-0 sm:max-w-[24rem]"
        >
          <div className="border-b px-5 pb-4 pt-5">
            <SheetTitle className="text-base font-medium">
              {t("thread.promptNavigator.title")}
            </SheetTitle>
            <div className="relative mt-4">
              <Search
                aria-hidden
                className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
              />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-label={t("thread.promptNavigator.search")}
                placeholder={t("thread.promptNavigator.search")}
                className={cn(
                  "h-10 w-full rounded-full border border-border bg-background ps-9 pe-3 text-sm",
                  "outline-none transition focus:border-ring focus:ring-2 focus:ring-ring/20",
                )}
              />
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
            {needsList && list.status === "loading" ? (
              <div
                role="status"
                className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground"
              >
                <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
                {t("thread.promptNavigator.loadingEarlier")}
              </div>
            ) : null}
            {needsList && list.status === "error" ? (
              <div role="alert" className="px-3 py-2 text-xs text-destructive">
                {t("thread.promptNavigator.earlierFailed")}
              </div>
            ) : null}
            {jumpFailed ? (
              <div role="alert" className="px-3 py-2 text-xs text-destructive">
                {t("thread.promptNavigator.jumpFailed")}
              </div>
            ) : null}
            {filteredPrompts.length > 0 ? (
              <div className="space-y-1">
                {filteredPrompts.map((prompt) => (
                  <PromptNavigatorRow
                    key={prompt.key}
                    locale={i18n.resolvedLanguage || i18n.language}
                    prompt={prompt}
                    loading={jumping === prompt.index}
                    disabled={jumping !== null}
                    onJump={jump}
                  />
                ))}
              </div>
            ) : (
              <div className="px-3 py-10 text-center text-sm text-muted-foreground">
                {t("thread.promptNavigator.noResults")}
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}

interface PromptNavigatorRowProps {
  disabled: boolean;
  loading: boolean;
  locale: string;
  onJump: (prompt: NavigatorPrompt) => void;
  prompt: NavigatorPrompt;
}

function PromptNavigatorRow({
  disabled,
  loading,
  locale,
  onJump,
  prompt,
}: PromptNavigatorRowProps) {
  const { t } = useTranslation();
  const timestamp = prompt.createdAt === null ? "" : fmtDateTime(prompt.createdAt, locale);
  return (
    <button
      type="button"
      disabled={disabled}
      aria-busy={loading || undefined}
      className={cn(
        "w-full rounded-xl px-3 py-3 text-start transition",
        "hover:bg-accent focus-visible:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
        "disabled:cursor-default disabled:hover:bg-transparent",
        disabled && !loading && "opacity-60",
      )}
      aria-label={t("thread.promptNavigator.jumpTo", { label: prompt.label })}
      onClick={() => onJump(prompt)}
    >
      <div className="max-h-20 overflow-hidden whitespace-pre-wrap break-words text-sm leading-5 text-foreground">
        {prompt.preview}
      </div>
      {timestamp || loading ? (
        <div className="mt-1 flex items-center gap-1.5 text-[10px] leading-4 text-muted-foreground/75">
          {loading ? <Loader2 aria-hidden className="h-3 w-3 animate-spin" /> : null}
          {timestamp}
        </div>
      ) : null}
    </button>
  );
}
