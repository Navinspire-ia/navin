// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ChannelSetupPresentation } from "@/components/settings/channels/catalog";
import { fetchNavinFeatures } from "@/lib/api";
import { postMarketing } from "@/lib/marketing-api";
import { ideOauthCallbackUrl } from "@/lib/social-oauth-callback";
import type { NavinFeatureInfo, NavinFeaturesPayload } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

function callbackFor(feature: NavinFeatureInfo): string {
  return ideOauthCallbackUrl(feature.name, {
    saved: feature.config_values?.redirect_uri,
    suggested: feature.suggested_redirect_uri,
  });
}

export function SocialOauthSetup({
  token,
  feature,
  setup,
  onFeaturesUpdate,
}: {
  token: string;
  feature: NavinFeatureInfo;
  setup: ChannelSetupPresentation;
  onFeaturesUpdate: (payload: NavinFeaturesPayload) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [clientId, setClientId] = useState(feature.config_values?.client_id ?? "");
  const [clientSecret, setClientSecret] = useState("");
  const [redirectUri, setRedirectUri] = useState(callbackFor(feature));
  const [subreddit, setSubreddit] = useState(feature.config_values?.subreddit ?? "");
  const [busy, setBusy] = useState<"save" | "connect" | "disconnect" | "page" | null>(null);
  const [notice, setNotice] = useState(feature.last_error || "");

  useEffect(() => {
    setClientId(feature.config_values?.client_id ?? "");
    setClientSecret("");
    setRedirectUri(callbackFor(feature));
    setSubreddit(feature.config_values?.subreddit ?? "");
    setNotice(feature.last_error || "");
  }, [feature.name, feature.config_values?.client_id, feature.config_values?.redirect_uri, feature.config_values?.subreddit, feature.last_error, feature.suggested_redirect_uri]);

  const refresh = async () => {
    onFeaturesUpdate(await fetchNavinFeatures(token));
  };

  const persistApp = async () => {
    if (clientId.trim()) {
      await postMarketing(token, "oauth-configure", {
        provider: feature.name,
        client_id: clientId,
        redirect_uri: redirectUri,
        ...(clientSecret ? { client_secret: clientSecret } : {}),
      });
    }
    if (feature.name === "reddit") {
      await postMarketing(token, "settings", { publish: { reddit: { subreddit } } });
    }
  };

  const saveApp = async () => {
    setBusy("save");
    setNotice("");
    try {
      await persistApp();
      await refresh();
      setClientSecret("");
      setNotice(tx("settings.channels.oauth.saved", "Application saved. Connect the account, then turn the channel On."));
    } catch (err) {
      setNotice((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const connect = async () => {
    setBusy("connect");
    setNotice("");
    try {
      await persistApp();
      const next = await postMarketing(token, "oauth-connect", {
        provider: feature.name,
        return_to: "channels",
      });
      const url = next.oauth?.authorization_url;
      if (url) window.location.assign(url);
      else setNotice(tx("settings.channels.oauth.noUrl", "The provider did not return an authorization URL."));
    } catch (err) {
      setNotice((err as Error).message);
      setBusy(null);
    }
  };

  const disconnect = async () => {
    setBusy("disconnect");
    setNotice("");
    try {
      await postMarketing(token, "oauth-disconnect", { provider: feature.name });
      await refresh();
    } catch (err) {
      setNotice((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const selectPage = async (accountId: string) => {
    setBusy("page");
    setNotice("");
    try {
      await postMarketing(token, "oauth-select-account", { provider: feature.name, account_id: accountId });
      await refresh();
    } catch (err) {
      setNotice((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const connected = feature.oauth_status === "connected" || feature.oauth_status === "refresh_required";
  const pickPage = feature.name === "facebook" && (feature.oauth_status === "select_account" || (feature.oauth_accounts?.length ?? 0) > 1);

  return (
    <div className="mt-4 space-y-3">
      <p className="text-[12.5px] leading-5 text-muted-foreground">
        {t("settings.channels.oauth.help", {
          defaultValue:
            "Your {{provider}} user id is never typed here. Click Connect and sign in: the account is detected automatically. The app client ID is only needed once if it is not already in the environment.",
          provider: feature.display_name,
        })}
      </p>
      <label className="block space-y-1 text-[12px] font-medium text-foreground">
        {tx("settings.channels.oauth.clientId", "App client ID (not your account id)")}
        <Input value={clientId} onChange={(event) => setClientId(event.target.value)} className="h-9" />
      </label>
      <label className="block space-y-1 text-[12px] font-medium text-foreground">
        {tx(
          "settings.channels.oauth.clientSecret",
          feature.name === "reddit" ? "App client secret (optional for a Reddit installed app)" : "App client secret",
        )}
        <Input
          type="password"
          value={clientSecret}
          placeholder={feature.client_secret_set ? tx("settings.channels.oauth.secretSaved", "Secret saved") : ""}
          onChange={(event) => setClientSecret(event.target.value)}
          className="h-9"
        />
      </label>
      <label className="block space-y-1 text-[12px] font-medium text-foreground">
        {tx("settings.channels.oauth.callback", "Callback URL")}
        <Input
          value={redirectUri}
          onChange={(event) => setRedirectUri(event.target.value)}
          placeholder={
            feature.name === "reddit"
              ? "http://127.0.0.1:8766/api/marketing/oauth/callback"
              : "https://ton-domaine/api/marketing/oauth/callback"
          }
          className="h-9"
        />
      </label>
      {feature.name === "reddit" ? (
        <label className="block space-y-1 text-[12px] font-medium text-foreground">
          {tx("settings.channels.oauth.subreddit", "Subreddit (without r/)")}
          <Input value={subreddit} onChange={(event) => setSubreddit(event.target.value)} className="h-9" />
        </label>
      ) : null}
      {pickPage && feature.oauth_accounts?.length ? (
        <label className="block space-y-1 text-[12px] font-medium text-foreground">
          {tx("settings.channels.oauth.facebookPage", "Page to publish to")}
          <select
            className="h-9 w-full rounded-md border border-border/70 bg-background px-3 text-[13px]"
            value={feature.oauth_account_id || ""}
            disabled={busy === "page"}
            onChange={(event) => void selectPage(event.target.value)}
          >
            <option value="">{tx("settings.channels.oauth.selectPage", "Choose a Facebook Page")}</option>
            {feature.oauth_accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {feature.oauth_account ? (
        <p className="text-[12.5px] text-foreground">
          {t("settings.channels.oauth.account", {
            defaultValue: "Connected as {{account}}",
            account: feature.oauth_account,
          })}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" disabled={Boolean(busy)} onClick={() => void saveApp()}>
          {busy === "save" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
          {tx("settings.channels.oauth.saveApp", "Save application")}
        </Button>
        <Button type="button" size="sm" disabled={Boolean(busy)} onClick={() => void connect()}>
          {busy === "connect" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
          {connected
            ? tx("settings.channels.oauth.reconnect", "Reconnect")
            : tx("settings.channels.oauth.connect", "Connect account")}
        </Button>
        {connected || feature.oauth_status === "select_account" || feature.oauth_status === "expired" ? (
          <Button type="button" size="sm" variant="ghost" disabled={Boolean(busy)} onClick={() => void disconnect()}>
            {tx("settings.channels.oauth.disconnect", "Disconnect")}
          </Button>
        ) : null}
        {setup.officialUrl ? (
          <a href={setup.officialUrl} target="_blank" rel="noopener noreferrer" className="self-center text-[12px] underline">
            {setup.officialLabel || tx("settings.channels.oauth.guide", "Official connection guide")}
          </a>
        ) : null}
      </div>
      {notice ? <p className="text-[12.5px] leading-5 text-muted-foreground">{notice}</p> : null}
    </div>
  );
}
