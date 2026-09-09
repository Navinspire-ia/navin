// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useRef, useState } from "react";

import { updateLiveSession, type AgentLiveSession } from "@/lib/agent-live";
import type { NavinClient } from "@/lib/navin-client";

export function useAgentLiveSessions(client: NavinClient, chatId: string | null, onStart: () => void) {
  const [byId, setById] = useState<Record<string, AgentLiveSession>>({});
  const [selected, setSelected] = useState<Record<string, string>>({});
  const current = useRef({ chatId, onStart });
  current.current = { chatId, onStart };

  useEffect(() => client.onAgentBrowser((update) => {
    if (!update.chatId) return;
    setById((previous) => {
      const next = { ...previous, [update.id]: updateLiveSession(previous[update.id], update) };
      // Keep one latest frame per session, with bounded memory across chats.
      const ids = Object.keys(next);
      for (const id of ids.slice(0, Math.max(0, ids.length - 12))) delete next[id];
      return next;
    });
    if (update.phase === "start" && update.chatId === current.current.chatId) {
      setSelected((previous) => previous[update.chatId] ? previous : { ...previous, [update.chatId]: update.id });
      current.current.onStart();
    }
  }), [client]);

  const sessions = Object.values(byId).filter((session) => session.chatId === chatId);
  const session = sessions.find((item) => item.id === selected[chatId ?? ""])
    ?? sessions.find((item) => item.live)
    ?? sessions.at(-1)
    ?? null;

  return {
    session,
    sessions,
    select(id: string) {
      if (chatId) setSelected((previous) => ({ ...previous, [chatId]: id }));
    },
    forget(id: string) {
      setById((previous) => {
        const next = { ...previous };
        delete next[id];
        return next;
      });
    },
  };
}
