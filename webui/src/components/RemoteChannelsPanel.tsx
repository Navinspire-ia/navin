// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Mail, MessageCircle, Send, Users } from "lucide-react";

import {
  fetchRemoteChannels,
  testRemoteChannels,
  updateRemoteChannel,
  type RemoteChannelName,
  type RemoteChannelRow,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const CHANNELS_HASH = "#/settings?section=channels";
const P = "notifications.channels";

// Product names: shown as they are in every language.
const META: Record<RemoteChannelName, { label: string; icon: typeof Mail; placeholder: string }> = {
  email: { label: "Email", icon: Mail, placeholder: "email" },
  whatsapp: { label: "WhatsApp", icon: MessageCircle, placeholder: "" },
  telegram: { label: "Telegram", icon: Send, placeholder: "telegram" },
  teams: { label: "Microsoft Teams", icon: Users, placeholder: "teams" },
};

const FLAGS = [
  { key: "notify", label: "Notifications", title: "Errors, blocks and finished tasks sent to this channel" },
  { key: "approve", label: "Approvals", title: "Approval requests: reply YES or NO with the code" },
  { key: "chat", label: "Chat", title: "Send prompts to Navin from this channel" },
] as const;

const PLACEHOLDERS: Record<string, string> = {
  email: "you@example.com",
  telegram: "Chat ID (e.g. 123456789)",
  teams: "Teams conversation ID",
};

/**
 * Channels Navin can reach you on when you are away from the app. Transport
 * credentials stay in Settings > Channels; this only picks what goes where.
 */
export default function RemoteChannelsPanel() {
  const { t } = useTranslation();
  const { token } = useClient();
  const [rows, setRows] = useState<RemoteChannelRow[] | null>(null);
  const [drafts, setDrafts] = useState<Partial<Record<RemoteChannelName, string>>>({});
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    fetchRemoteChannels(token)
      .then((payload) => {
        if (live) setRows(payload.channels);
      })
      .catch(() => {
        if (live) {
          setFailed(true);
          setRows([]);
        }
      });
    return () => {
      live = false;
    };
  }, [token]);

  const save = useCallback(
    (channel: RemoteChannelName, changes: Parameters<typeof updateRemoteChannel>[2]) => {
      setMessage("");
      updateRemoteChannel(token, channel, changes)
        .then((payload) => setRows(payload.channels))
        .catch(() => setMessage(t(`${P}.saveFailed`, "Could not save")));
    },
    [t, token],
  );

  const test = () => {
    setMessage(t(`${P}.sending`, "Sending..."));
    testRemoteChannels(token)
      .then((payload) =>
        setMessage(
          payload.queued
            ? t(`${P}.testSent`, { count: payload.queued, defaultValue: "Test sent to {{count}} channel(s)" })
            : t(`${P}.testNone`, "No ready channel with Notifications on"),
        ),
      )
      .catch(() => setMessage(t(`${P}.testFailed`, "Test failed")));
  };

  if (rows === null) {
    return (
      <div className="flex items-center gap-2 px-3 py-6 text-[12px] text-muted-foreground" role="status">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {t(`${P}.loading`, "Loading channels...")}
      </div>
    );
  }
  if (failed) {
    return (
      <p className="px-3 py-6 text-center text-[12px] text-muted-foreground" role="alert">
        {t(`${P}.unavailable`, "Channels unavailable")}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2 px-3 py-2.5" data-testid="remote-channels-panel">
      {rows.map((row) => {
        const meta = META[row.channel];
        const Icon = meta.icon;
        const draft = drafts[row.channel] ?? row.to;
        const placeholder = meta.placeholder
          ? t(`${P}.placeholder.${meta.placeholder}`, PLACEHOLDERS[meta.placeholder])
          : "+33612345678";
        return (
          <div key={row.channel} className="rounded-lg border border-border/60 p-2">
            <div className="flex items-center gap-2">
              <Icon className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={1.75} />
              <span className="text-[12.5px] font-medium">{meta.label}</span>
              <span
                className={cn(
                  "ms-auto rounded-full px-1.5 text-[10px]",
                  row.ready
                    ? "bg-emerald-500/15 text-emerald-600"
                    : row.configured
                      ? "bg-amber-500/15 text-amber-600"
                      : "bg-muted text-muted-foreground",
                )}
                title={row.hint}
              >
                {row.ready
                  ? t(`${P}.status.ready`, "ready")
                  : row.configured
                    ? t(`${P}.status.offline`, "offline")
                    : t(`${P}.status.setup`, "to set up")}
              </span>
            </div>
            {!row.configured ? (
              <a href={CHANNELS_HASH} className="mt-1 block text-[11px] text-primary hover:underline" title={row.hint}>
                {t(`${P}.configure`, "Set up in Settings > Channels")}
              </a>
            ) : null}
            <input
              value={draft}
              placeholder={placeholder}
              aria-label={t(`${P}.recipient`, { channel: meta.label, defaultValue: "{{channel}} recipient" })}
              onChange={(event) => setDrafts((prev) => ({ ...prev, [row.channel]: event.target.value }))}
              onBlur={() => {
                if (draft !== row.to) save(row.channel, { to: draft });
              }}
              className="mt-1.5 w-full rounded-md border border-border/60 bg-background px-2 py-1 text-[11.5px]"
            />
            <div className="mt-1.5 flex gap-1">
              {FLAGS.map((flag) => (
                <button
                  key={flag.key}
                  type="button"
                  title={t(`${P}.flags.${flag.key}Title`, flag.title)}
                  aria-pressed={row[flag.key]}
                  onClick={() => save(row.channel, { [flag.key]: !row[flag.key] })}
                  className={cn(
                    "min-h-7 flex-1 rounded-md border px-1.5 py-0.5 text-[11px] transition-colors",
                    row[flag.key]
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border/60 text-muted-foreground hover:text-foreground",
                  )}
                >
                  {t(`${P}.flags.${flag.key}`, flag.label)}
                </button>
              ))}
            </div>
          </div>
        );
      })}
      <div className="flex items-center justify-between gap-2">
        <button type="button" onClick={test} className="text-[11px] text-primary hover:underline">
          {t(`${P}.sendTest`, "Send a test")}
        </button>
        {message ? (
          <span className="text-[11px] text-muted-foreground" role="status">
            {message}
          </span>
        ) : null}
      </div>
      <p className="text-[10.5px] leading-snug text-muted-foreground">
        {t(
          `${P}.help`,
          "Approvals: reply \"YES code\", \"NO code\" or \"ALWAYS code\". Credentials are set in Settings > Channels or through MCP.",
        )}
      </p>
    </div>
  );
}
