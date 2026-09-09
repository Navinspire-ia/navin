// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from "react";
import { DefaultButton, Dropdown, MessageBar, MessageBarType, PrimaryButton, TextField } from "@fluentui/react";

import type { SocialConnection } from "@/lib/marketing-api";
import { channelLabel, formatWhen } from "@/lib/marketing-publish";
import type { RunAction, Tx } from "./MarketingPublish";

const BUTTON = { root: { minHeight: 36, cursor: "pointer" as const } };

function SocialConnectionCard({ connection, busy, locale, tx, run }: {
  connection: SocialConnection;
  busy: boolean;
  locale: string;
  tx: Tx;
  run: RunAction;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const provider = connection.provider;
  const label = channelLabel(provider);
  const configured = Boolean(connection.client_id && connection.client_secret_set && connection.redirect_uri);
  const connected = connection.status === "connected" || connection.status === "refresh_required";
  const statusLabels: Record<string, string> = {
    not_connected: tx("oauth.disconnected", "Account not connected"),
    connected: tx("oauth.connected", "Account connected"),
    select_account: tx("oauth.selectAccount", "Choose a Page to finish connecting"),
    refresh_required: tx("oauth.renewing", "Authorization will be renewed before the next action"),
    expired: tx("oauth.expired", "Authorization expired: reconnect your account"),
  };
  const callback = connection.redirect_uri || `${window.location.origin}/api/marketing/oauth/callback`;
  const save = async () => {
    const body: Record<string, string> = {
      provider, client_id: draft.client_id ?? connection.client_id,
      redirect_uri: draft.redirect_uri ?? callback,
    };
    if ("client_secret" in draft) body.client_secret = draft.client_secret;
    if (await run("oauth-configure", body)) setDraft({});
  };

  return (
    <section className="rounded-xl border border-border/60 bg-background/60 p-4" data-testid={`marketing-oauth-${provider}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold">{label}</h4>
          <p className="mt-1 text-xs text-muted-foreground" role="status">{statusLabels[connection.status] || connection.status}</p>
          {connection.account ? <p className="mt-1 text-sm font-medium">{connection.account}</p> : null}
          {connected && connection.expires_at ? <p className="mt-1 text-xs text-muted-foreground">{tx("oauth.validUntil", "Valid until {{date}}", { date: formatWhen(connection.expires_at, locale) })}</p> : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <PrimaryButton
            text={connected ? tx("oauth.reconnect", "Reconnect") : tx("oauth.connect", "Connect account")}
            iconProps={{ iconName: "PlugConnected" }}
            disabled={busy || !configured || Object.keys(draft).length > 0}
            data-testid={`marketing-oauth-connect-${provider}`}
            styles={BUTTON}
            onClick={() => void run("oauth-connect", { provider }, (next) => {
              if (next.oauth?.authorization_url) window.location.assign(next.oauth.authorization_url);
            })}
          />
          {connected || connection.status === "select_account" || connection.status === "expired" ? (
            <DefaultButton
              text={tx("oauth.disconnect", "Disconnect")}
              disabled={busy}
              styles={BUTTON}
              data-testid={`marketing-oauth-disconnect-${provider}`}
              onClick={() => void run("oauth-disconnect", { provider })}
            />
          ) : null}
        </div>
      </div>
      {provider === "facebook" && connection.accounts.length > 1 ? (
        <Dropdown
          label={tx("oauth.facebookPage", "Page to publish to")}
          placeholder={tx("oauth.selectPage", "Choose your Facebook Page")}
          selectedKey={connection.account_id || null}
          disabled={busy}
          options={connection.accounts.map((account) => ({ key: account.id, text: account.name }))}
          onChange={(_, option) => option && void run("oauth-select-account", { provider, account_id: String(option.key) })}
          styles={{ root: { marginTop: 12 } }}
        />
      ) : null}
      {provider === "instagram" ? <p className="mt-2 text-xs text-muted-foreground">{tx("oauth.instagramHint", "Connect an Instagram professional account (Business or Creator).")}</p> : null}
      {provider === "tiktok" ? <p className="mt-2 text-xs text-muted-foreground">{tx("oauth.tiktokHint", "TikTok decides the available audiences and publishing access for your app. Choose the audience and confirm each post before sending.")}</p> : null}
      <details className="mt-3" open={!configured}>
        <summary className="cursor-pointer text-xs font-medium">{tx("oauth.application", "Application connection settings")}</summary>
        <p className="my-2 text-xs text-muted-foreground">{tx("oauth.setupHelp", "Register this callback URL in your application on {{provider}}. The client secret stays on the server.", { provider: label })}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label={tx("oauth.clientId", "Application client ID")} value={draft.client_id ?? connection.client_id} onChange={(_, value) => setDraft((old) => ({ ...old, client_id: value || "" }))} data-testid={`marketing-oauth-client-${provider}`} />
          <TextField label={tx("oauth.clientSecret", "Application client secret")} type="password" canRevealPassword placeholder={connection.client_secret_set ? tx("oauth.secretSaved", "Secret saved") : ""} value={draft.client_secret ?? ""} onChange={(_, value) => setDraft((old) => ({ ...old, client_secret: value || "" }))} data-testid={`marketing-oauth-secret-${provider}`} />
          <TextField label={tx("oauth.callback", "Public HTTPS callback URL")} value={draft.redirect_uri ?? callback} onChange={(_, value) => setDraft((old) => ({ ...old, redirect_uri: value || "" }))} styles={{ root: { gridColumn: "1 / -1" } }} data-testid={`marketing-oauth-callback-${provider}`} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <DefaultButton text={tx("oauth.saveApp", "Save application")} disabled={busy || (!Object.keys(draft).length && configured)} styles={BUTTON} onClick={() => void save()} data-testid={`marketing-oauth-save-${provider}`} />
          <a href={connection.docs_url} target="_blank" rel="noopener noreferrer" className="text-xs underline">{tx("oauth.providerGuide", "Official connection guide")}</a>
        </div>
      </details>
      {connection.last_error ? <MessageBar messageBarType={MessageBarType.error} styles={{ root: { marginTop: 12 } }}>{connection.last_error}</MessageBar> : null}
    </section>
  );
}

export function SocialConnections({ connections, busy, locale, tx, run }: {
  connections: SocialConnection[];
  busy: boolean;
  locale: string;
  tx: Tx;
  run: RunAction;
}) {
  return (
    <section className="rounded-2xl border border-border/70 bg-background/80 p-4" data-testid="marketing-social-connections">
      <h3 className="text-sm font-semibold">{tx("oauth.title", "Connect your social accounts")}</h3>
      <p className="mb-4 mt-1 text-xs text-muted-foreground">{tx("oauth.intro", "Authorize your account on each network, then enable its publishing connector below. Your publishing schedule stays under your control.")}</p>
      <div className="grid gap-3">{connections.map((connection) => <SocialConnectionCard key={connection.provider} connection={connection} busy={busy} locale={locale} tx={tx} run={run} />)}</div>
    </section>
  );
}
