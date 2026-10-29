// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The agent needs a login (a website, a git forge) or an app connector and
 * paused on it.
 *
 * The card embeds the existing Sites & logins form, the Git connect panel or
 * the connector form, so values go from these fields to the vault over HTTP,
 * exactly like Settings. The socket only ever carries "saved" / "connected" /
 * "declined"; the engine then checks the vault and hands the agent refs or a
 * connected status, never values.
 */

import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Check, KeyRound, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConnectorIcon } from "@/components/settings/connectors/ConnectorIcon";
import { ConnectorSetupForm } from "@/components/settings/connectors/ConnectorSetupForm";
import { GitConnectPanel } from "@/components/settings/GitConnectPanel";
import { SiteLoginsPanel } from "@/components/settings/SiteLoginsPanel";
import { Button } from "@/components/ui/button";
import {
  addCredentialRequest,
  credentialClosedFromEvent,
  credentialRequestFromEvent,
  removeCredentialRequest,
  type ClosedCredentialRequest,
  type CredentialAnswerStatus,
  type PendingCredentialRequest,
} from "@/lib/credential-requests";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };
const RESULT_VISIBLE_MS = 8000;

interface CardProps {
  request: PendingCredentialRequest;
  token: string | null | undefined;
  onAnswer: (requestId: string, status: CredentialAnswerStatus, login?: string) => void;
}

export function CredentialRequestCard({ request, token, onAnswer }: CardProps) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const [answered, setAnswered] = useState(false);
  const [app, setApp] = useState<{ name: string; icon: string } | null>(null);
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    String(t(`thread.credential.${key}`, { defaultValue: fallback, ...(values ?? {}) }));

  useEffect(() => {
    setAnswered(false);
    setApp(null);
  }, [request.requestId]);

  const answer = useCallback(
    (status: CredentialAnswerStatus, login?: string) => {
      if (answered) return;
      setAnswered(true);
      onAnswer(request.requestId, status, login);
    },
    [answered, onAnswer, request.requestId],
  );

  const connector = request.kind === "connector";
  const title =
    request.kind === "site_login"
      ? tx("titleSite", "Sign in to {{host}}", { host: request.host })
      : connector
        ? tx("titleConnector", "Connect {{name}}", { name: app?.name ?? request.host })
        : tx("titleGit", "Connect {{host}}", { host: request.host });

  return (
    <motion.div
      role="dialog"
      aria-labelledby={`credential-title-${request.requestId}`}
      data-testid="credential-request-card"
      initial={reduceMotion ? false : { opacity: 0, y: 8, scale: 0.99 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={spring}
      className="mb-2 flex w-full min-w-0 max-w-full flex-col gap-2.5 overflow-hidden rounded-xl border border-border/70 bg-card/95 px-3 py-2.5 text-[14px]"
    >
      <div className="flex min-w-0 items-start gap-2">
        {connector && app ? (
          <ConnectorIcon icon={app.icon} name={app.name} size="sm" className="mt-0.5" />
        ) : (
          <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-[12px] font-medium uppercase tracking-wide text-muted-foreground">
            {connector ? tx("eyebrowConnector", "App connection needed") : tx("eyebrow", "Login needed")}
          </p>
          <p id={`credential-title-${request.requestId}`} className="truncate text-[14px] font-medium text-foreground" dir="auto">
            {title}
          </p>
          {request.reason ? (
            <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground" dir="auto">
              {request.reason}
            </p>
          ) : null}
          <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground/90">
            {connector
              ? tx(
                  "privacyConnector",
                  "Checked with the app, then kept in the local vault. The agent never sees the key or token.",
                )
              : tx("privacy", "Saved in the local vault. Navin only gets a reference, never the value.")}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 shrink-0 rounded-full text-[12.5px] text-muted-foreground"
          disabled={answered}
          onClick={() => answer("declined")}
          data-testid="credential-decline"
        >
          <X className="me-1 h-3.5 w-3.5" aria-hidden />
          {tx("decline", "Decline")}
        </Button>
      </div>
      <div className={cn(answered && "pointer-events-none opacity-60")}>
        {request.kind === "site_login" ? (
          <SiteLoginsPanel token={token} compact hideList site={request.host} onSaved={() => answer("saved")} />
        ) : connector ? (
          <ConnectorSetupForm
            token={token}
            compact
            service={request.host}
            onLoaded={(detail) => setApp({ name: detail.name, icon: detail.icon })}
            onConnected={(account) => answer("connected", account)}
          />
        ) : (
          <GitConnectPanel token={token} compact host={request.host} onConnected={(login) => answer("connected", login ?? "")} />
        )}
      </div>
    </motion.div>
  );
}

function CredentialResult({ result }: { result: ClosedCredentialRequest }) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    String(t(`thread.credential.${key}`, { defaultValue: fallback, ...(values ?? {}) }));
  const ok = result.status === "saved" || result.status === "connected";
  const label =
    result.status === "saved"
      ? tx("saved", "Saved")
      : result.status === "connected"
        ? result.login
          ? tx("connected", "Connected as {{login}}", { login: result.login })
          : tx("connectedNoLogin", "Connected")
        : result.status === "timed_out"
          ? tx("timedOut", "The request expired")
          : result.status === "not_saved"
            ? tx("notSaved", "Nothing was saved")
            : tx("declined", "Declined");
  return (
    <div
      className="mb-2 flex min-w-0 flex-wrap items-center gap-2 rounded-xl border border-border/60 bg-card/90 px-3 py-2 text-[13px]"
      data-testid="credential-result"
      role="status"
    >
      {ok ? <Check className="h-4 w-4 text-emerald-600 dark:text-emerald-300" aria-hidden /> : <X className="h-4 w-4 text-muted-foreground" aria-hidden />}
      <span className={cn("font-medium", ok ? "text-emerald-700 dark:text-emerald-300" : "text-muted-foreground")}>{label}</span>
      {result.refs.map((ref) => (
        <code key={ref} dir="ltr" className="rounded-md bg-muted/60 px-1.5 py-0.5 font-mono text-[12px] text-foreground/85">
          {ref}
        </code>
      ))}
    </div>
  );
}

/**
 * Listens on the chat for credential cards, oldest first, one at a time.
 * Drop-in for the thread shell: `<CredentialRequestCards chatId={chatId} />`.
 */
export function CredentialRequestCards({ chatId, className }: { chatId: string | null | undefined; className?: string }) {
  const { client, token } = useClient();
  const [queue, setQueue] = useState<PendingCredentialRequest[]>([]);
  const [result, setResult] = useState<ClosedCredentialRequest | null>(null);

  useEffect(() => {
    setQueue([]);
    setResult(null);
    if (!client || !chatId) return;
    return client.onChat(chatId, (ev) => {
      const request = credentialRequestFromEvent(ev);
      if (request) {
        setQueue((current) => addCredentialRequest(current, request));
        return;
      }
      const closed = credentialClosedFromEvent(ev);
      if (closed) {
        setQueue((current) => removeCredentialRequest(current, closed.requestId));
        if (closed.status !== "stopped") setResult(closed);
        return;
      }
      if (ev.event === "turn_end") setQueue([]);
    });
  }, [chatId, client]);

  useEffect(() => {
    if (!result) return;
    const timer = window.setTimeout(() => setResult(null), RESULT_VISIBLE_MS);
    return () => window.clearTimeout(timer);
  }, [result]);

  const onAnswer = useCallback(
    (requestId: string, status: CredentialAnswerStatus, login?: string) => {
      client.sendCredentialAnswer(requestId, status, login ?? "");
    },
    [client],
  );

  const current = queue[0];
  if (!current && !result) return null;
  return (
    <div className={cn("relative mx-auto w-full min-w-0 max-w-[49.5rem]", className)}>
      <AnimatePresence initial={false}>
        {current ? (
          <CredentialRequestCard key={current.requestId} request={current} token={token} onAnswer={onAnswer} />
        ) : result ? (
          <CredentialResult key={`result-${result.requestId}`} result={result} />
        ) : null}
      </AnimatePresence>
    </div>
  );
}
