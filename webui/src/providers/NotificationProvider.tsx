// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { useClient } from "@/providers/ClientProvider";
import { onNotification } from "@/lib/notification-bus";
import {
  addNotification,
  dismissNotification,
  markAllRead as markAllReadIn,
  markRead as markReadIn,
  unreadCount as countUnread,
  type NotificationEntry,
  type NotificationInput,
  type NotificationLevel,
  type NotificationSource,
} from "@/lib/notifications";

interface NotificationContextValue {
  notifications: readonly NotificationEntry[];
  unreadCount: number;
  notify: (input: NotificationInput) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  dismiss: (id: string) => void;
  clear: () => void;
}

const NotificationContext = createContext<NotificationContextValue | null>(null);

const LEVELS: readonly string[] = ["info", "success", "warning", "error"];
const SOURCES: readonly string[] = [
  "connection",
  "request",
  "agent",
  "board",
  "session",
  "update",
  "announcement",
];

function asLevel(value: string | undefined): NotificationLevel {
  return LEVELS.includes(value ?? "") ? (value as NotificationLevel) : "info";
}

function asSource(value: string | undefined): NotificationSource {
  return SOURCES.includes(value ?? "") ? (value as NotificationSource) : "session";
}

/**
 * Collects everything worth telling the user about into one list.
 *
 * Two sources feed it: the module-level bus (failed gateway calls, reported
 * from outside React) and the websocket (agents, retries, anything the
 * backend raises about the user's work).
 */
export function NotificationProvider({ children }: { children: ReactNode }) {
  const { client } = useClient();
  const [notifications, setNotifications] = useState<readonly NotificationEntry[]>([]);

  const notify = useCallback((input: NotificationInput) => {
    setNotifications((current) => addNotification(current, input));
  }, []);

  useEffect(() => onNotification(notify), [notify]);

  useEffect(
    () =>
      client.onNotification((incoming) =>
        notify({
          level: asLevel(incoming.level),
          source: asSource(incoming.source),
          title: incoming.title,
          detail: incoming.detail,
          key: incoming.key,
          chatId: incoming.chatId,
        }),
      ),
    [client, notify],
  );

  // Connection health deliberately does not notify: it is ambient state, not
  // an event about the user's work, and it kept burying real notifications
  // (agent runs, board moves, file operations) under "restored / lost" noise.
  // The status bar and the sidebar badge show it live instead.

  const value = useMemo<NotificationContextValue>(
    () => ({
      notifications,
      unreadCount: countUnread(notifications),
      notify,
      markRead: (id) => setNotifications((current) => markReadIn(current, id)),
      markAllRead: () => setNotifications((current) => markAllReadIn(current)),
      dismiss: (id) => setNotifications((current) => dismissNotification(current, id)),
      clear: () => setNotifications([]),
    }),
    [notifications, notify],
  );

  return <NotificationContext.Provider value={value}>{children}</NotificationContext.Provider>;
}

export function useNotifications(): NotificationContextValue {
  const ctx = useContext(NotificationContext);
  if (!ctx) {
    throw new Error("useNotifications must be used within a NotificationProvider");
  }
  return ctx;
}
