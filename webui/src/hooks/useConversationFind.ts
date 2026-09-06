import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { matchingRowIds, stepMatchIndex, uiMessageHaystack } from "@/lib/conversation-search";
import type { UIMessage } from "@/lib/types";

export function useConversationFind(
  messages: UIMessage[],
  jumpToMessage: (id: string) => void,
  isActive?: () => boolean,
) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const ids = useMemo(
    () => matchingRowIds(messages, query, uiMessageHaystack),
    [messages, query],
  );

  useEffect(() => {
    setIndex(0);
  }, [query]);

  const activeId = ids.length ? ids[Math.min(index, ids.length - 1)] : "";

  useEffect(() => {
    if (!open || !activeId) return;
    jumpToMessage(activeId);
  }, [activeId, jumpToMessage, open]);

  const openFind = useCallback(() => {
    setOpen(true);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, []);

  const closeFind = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "f") return;
      if (isActive && !isActive()) return;
      event.preventDefault();
      openFind();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isActive, openFind]);

  return {
    open,
    query,
    setQuery,
    index: Math.min(index, Math.max(ids.length - 1, 0)),
    total: ids.length,
    inputRef,
    openFind,
    closeFind,
    goPrev: () => setIndex((current) => stepMatchIndex(current, ids.length, -1)),
    goNext: () => setIndex((current) => stepMatchIndex(current, ids.length, 1)),
  };
}
