import { useCallback, useEffect, useRef, useState } from "react";
import {
  BadgeCheck,
  CircleUserRound,
  ExternalLink,
  Gauge,
  Loader2,
  LogOut,
  RefreshCw,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { ModelTokenUsageTable } from "@/components/settings/ModelTokenUsageTable";
import { useAccount } from "@/hooks/useAccount";
import { localizedPlanName } from "@/lib/accountPlan";
import { openExternalUrl } from "@/lib/api";
import type { SettingsPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const CONNECT_POLL_INTERVAL_MS = 3_000;
// Aligné sur le TTL du state côté gateway (15 min) : l'utilisateur peut
// prendre son temps dans le navigateur (création de compte, e-mail...).
const CONNECT_POLL_TIMEOUT_MS = 15 * 60_000;
const DEFAULT_SITE_URL = "https://navin.live";

/**
 * Settings > Account: the navin.live subscription of this install.
 *
 * Signed out, it offers the Cursor-style browser handoff (sign in on
 * navin.live, the device activates itself). Signed in, it shows who is
 * connected, the plan, the managed budget consumption, and the
 * manage/refresh/sign-out actions.
 */
export function AccountSettings({
  onOpenProviders,
  usage: tokenUsage,
}: {
  onOpenProviders?: () => void;
  usage?: SettingsPayload["usage"];
} = {}) {
  const { t, i18n } = useTranslation();
  const { token } = useClient();
  const tx = useCallback(
    (key: string, fallback: string, vars?: Record<string, unknown>) =>
      t(key, { defaultValue: fallback, ...(vars ?? {}) }),
    [t],
  );
  const { account, loading, reload, connectUrl, logout } = useAccount();

  const [busy, setBusy] = useState<"connect" | "logout" | "refresh" | "link" | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [waitingForBrowser, setWaitingForBrowser] = useState(false);
  const [manualConnectUrl, setManualConnectUrl] = useState<string | null>(null);
  const [manualSiteUrl, setManualSiteUrl] = useState<string | null>(null);
  const pollStop = useRef<(() => void) | null>(null);

  useEffect(() => () => pollStop.current?.(), []);

  // Pull live usage from navin.live when opening Account (same source as dashboard).
  useEffect(() => {
    if (!account?.connected) return;
    void reload({ refresh: true });
    // Only on mount / when connection flips on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account?.connected]);

  const siteUrl = (account?.server_url || DEFAULT_SITE_URL).replace(/\/+$/, "");
  const locale = (i18n.language || "en").slice(0, 2) || "en";

  const openSitePath = useCallback(
    async (path: string) => {
      const normalized = path.startsWith("/") ? path : `/${path}`;
      const href = `${siteUrl}/${locale}${normalized}`;
      setBusy("link");
      setError(null);
      setManualSiteUrl(null);
      try {
        const { opened } = await openExternalUrl(token, href);
        if (!opened) {
          const popup = window.open(href, "_blank", "noopener");
          if (!popup) setManualSiteUrl(href);
        }
      } catch (err) {
        const popup = window.open(href, "_blank", "noopener");
        if (!popup) {
          setManualSiteUrl(href);
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        setBusy(null);
      }
    },
    [locale, siteUrl, token],
  );

  const startConnect = useCallback(async () => {
    setBusy("connect");
    setError(null);
    setManualConnectUrl(null);
    try {
      const { url } = await connectUrl({ locale });
      // WebView: window.open is a no-op. Prefer the desktop opener, then the
      // gateway (xdg-open / open / start), then a manual copy link.
      const native = (await openExternalUrl(token, url)).opened;
      if (!native) {
        const popup = window.open(url, "_blank", "noopener");
        if (!popup) {
          setManualConnectUrl(url);
        }
      }
      setWaitingForBrowser(true);
      // Activation runs in the gateway (poll navin.live) - no localhost tab.
      // The gateway also pushes ``account_updated`` over the websocket, so
      // this poll is a fallback for dropped connections / suspended timers.
      pollStop.current?.();
      const startedAt = Date.now();
      let inFlight = false;
      const check = async () => {
        // Account requests can be slower than the poll period on a busy
        // gateway: never stack a second request on top of the first.
        if (inFlight) return;
        inFlight = true;
        let payload: Awaited<ReturnType<typeof reload>> | null;
        try {
          payload = await reload();
        } catch {
          payload = null;
        } finally {
          inFlight = false;
        }
        const timedOut = Date.now() - startedAt > CONNECT_POLL_TIMEOUT_MS;
        if (payload?.connected || timedOut) {
          pollStop.current?.();
          pollStop.current = null;
          setWaitingForBrowser(false);
          if (payload?.connected) {
            setManualConnectUrl(null);
          } else if (timedOut) {
            setError(
              tx(
                "settings.account.connectTimeout",
                "Sign-in did not finish. Complete the browser steps (sign in, then Connect), or try again.",
              ),
            );
          }
        }
      };
      const timer = window.setInterval(() => void check(), CONNECT_POLL_INTERVAL_MS);
      // Timers are throttled or suspended while the window is hidden behind
      // the sign-in browser: check immediately when the user comes back.
      const onReturn = () => {
        if (document.visibilityState !== "visible") return;
        void check();
      };
      window.addEventListener("focus", onReturn);
      document.addEventListener("visibilitychange", onReturn);
      pollStop.current = () => {
        window.clearInterval(timer);
        window.removeEventListener("focus", onReturn);
        document.removeEventListener("visibilitychange", onReturn);
      };
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }, [connectUrl, locale, reload, token, tx]);

  const doLogout = useCallback(async () => {
    setBusy("logout");
    setError(null);
    try {
      await logout();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }, [logout]);

  const doRefresh = useCallback(async () => {
    setBusy("refresh");
    setError(null);
    await reload({ refresh: true });
    setBusy(null);
  }, [reload]);

  if (loading && !account) {
    return (
      <div className="flex items-center gap-2 px-1 py-2 text-[13px] text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        {tx("settings.account.loading", "Loading account…")}
      </div>
    );
  }

  const connected = account?.connected ?? false;
  const planPrice = account?.plan_price_usd;
  const planName = localizedPlanName(t, account);
  const usage = account?.usage ?? null;
  const usedPercent = Math.min(100, Math.max(0, usage?.used_percent ?? 0));
  const periodEndSec = usage?.period_end || account?.period_end || null;
  const renewalLabel = periodEndSec
    ? new Date(periodEndSec * 1000).toLocaleDateString(i18n.language || undefined)
    : null;
  const usageBarClass =
    usedPercent >= 95
      ? "bg-red-500"
      : usedPercent >= 80
        ? "bg-amber-500"
        : "bg-primary";

  const teamCard = (
    <section className="rounded-2xl border border-border/55 bg-card/50 p-5">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-muted/60 text-muted-foreground">
          <Users className="h-5 w-5" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[15px] font-semibold text-foreground">
            {tx("settings.account.teamSettings", "Team settings")}
          </h3>
          <p className="mt-1 text-[13px] text-muted-foreground">
            {tx(
              "settings.account.teamSettingsHint",
              "Invite collaborators, shared projects, ownership zones and the PM activity feed open on navin.live.",
            )}
          </p>
          <div className="mt-3">
            <Button
              type="button"
              variant="secondary"
              disabled={busy !== null}
              onClick={() => void openSitePath("/account/team")}
            >
              {busy === "link" ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <ExternalLink className="h-4 w-4" aria-hidden />
              )}
              {tx("settings.account.openTeamSettings", "Open team page")}
            </Button>
          </div>
        </div>
      </div>
    </section>
  );

  return (
    <div className="space-y-4">
      {!connected ? (
        <section className="rounded-2xl border border-border/55 bg-card/50 p-5">
          <div className="flex items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-muted/60 text-muted-foreground">
              <CircleUserRound className="h-5 w-5" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <h3 className="text-[15px] font-semibold text-foreground">
                {tx("settings.account.signedOutTitle", "Connect your Navin account")}
              </h3>
              <p className="mt-1 text-[13px] text-muted-foreground">
                {tx(
                  "settings.account.signedOutSubtitle",
                  "Sign in on navin.live to sync your plan, managed models and usage with this editor.",
                )}
              </p>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button type="button" onClick={startConnect} disabled={busy !== null}>
              {busy === "connect" || waitingForBrowser ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <ExternalLink className="h-4 w-4" aria-hidden />
              )}
              {waitingForBrowser
                ? tx("settings.account.waitingForBrowser", "Finish signing in the browser…")
                : tx("settings.account.signInButton", "Sign in with navin.live")}
            </Button>
            <button
              type="button"
              onClick={() => void openSitePath("/pricing")}
              className="text-[13px] font-medium text-muted-foreground underline underline-offset-4 hover:text-foreground"
            >
              {tx("settings.account.viewPlans", "View plans")}
            </button>
            {onOpenProviders ? (
              <button
                type="button"
                onClick={onOpenProviders}
                className="text-[13px] font-medium text-muted-foreground underline underline-offset-4 hover:text-foreground"
              >
                {tx("settings.account.useOwnKeys", "Or configure your own providers")}
              </button>
            ) : null}
          </div>

          {manualConnectUrl ? (
            <p className="mt-3 text-[12px] leading-relaxed text-muted-foreground">
              {tx(
                "settings.account.openManually",
                "Browser did not open automatically. Open this link:",
              )}{" "}
              <a
                href={manualConnectUrl}
                target="_blank"
                rel="noreferrer noopener"
                className="break-all font-medium text-foreground underline underline-offset-4"
              >
                {manualConnectUrl}
              </a>
            </p>
          ) : null}
        </section>
      ) : (
        <section className="rounded-2xl border border-border/55 bg-card/50 p-5">
          <div className="flex items-center gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary/15 text-base font-semibold text-primary">
              {(account?.name || account?.email || "?").trim().charAt(0).toUpperCase()}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-[15px] font-semibold text-foreground">
                  {account?.name || account?.email}
                </span>
                <span className="rounded-full bg-primary/12 px-2.5 py-0.5 text-[11px] font-semibold tracking-wide text-primary">
                  {planName}
                </span>
              </div>
              {account?.email ? (
                <p className="truncate text-[12.5px] text-muted-foreground">{account.email}</p>
              ) : null}
            </div>
            <BadgeCheck className="h-5 w-5 shrink-0 text-emerald-500" aria-hidden />
          </div>

          {account?.error ? (
            <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12.5px] text-amber-600 dark:text-amber-400">
              {account.error}
            </p>
          ) : null}

          <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-2 text-[12.5px] sm:grid-cols-2">
            <div className="flex items-center justify-between gap-3 sm:justify-start">
              <dt className="text-muted-foreground">
                {tx("settings.account.subscription", "Subscription")}
              </dt>
              <dd className="text-foreground">
                {planName}
                {typeof planPrice === "number" && planPrice > 0
                  ? ` - ${tx("settings.account.priceMonthly", "${{price}}/month", { price: planPrice })}`
                  : ""}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-3 sm:justify-start">
              <dt className="text-muted-foreground">
                {tx("settings.account.managedModels", "Managed models")}
              </dt>
              <dd className="text-foreground">
                {account?.managed_key_active
                  ? tx("settings.account.managedActive", "Active (included in your plan)")
                  : tx("settings.account.managedInactive", "Using your own API keys")}
              </dd>
            </div>
          </dl>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={busy !== null}
              onClick={() => void openSitePath("/dashboard")}
            >
              {busy === "link" ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <ExternalLink className="h-4 w-4" aria-hidden />
              )}
              {tx("settings.account.manageButton", "Manage plan")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={doRefresh}
              disabled={busy !== null}
            >
              <RefreshCw
                className={cn("h-4 w-4", busy === "refresh" && "animate-spin")}
                aria-hidden
              />
              {tx("settings.account.refreshButton", "Refresh")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={doLogout}
              disabled={busy !== null}
              className="text-destructive hover:text-destructive"
            >
              {busy === "logout" ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <LogOut className="h-4 w-4" aria-hidden />
              )}
              {tx("settings.account.signOutButton", "Sign out")}
            </Button>
          </div>
        </section>
      )}

      {connected && usage ? (
        <section className="rounded-2xl border border-border/55 bg-card/50 p-5">
          <div className="flex items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-muted/60 text-muted-foreground">
              <Gauge className="h-5 w-5" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <h3 className="text-[15px] font-semibold text-foreground">
                {tx("settings.account.usageTitle", "Usage")}
              </h3>
              <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                {tx(
                  "settings.account.usageSubtitle",
                  "Managed model consumption for the current period.",
                )}
              </p>
            </div>
          </div>

          <div className="mt-4">
            <div className="flex items-baseline justify-between text-[12.5px]">
              <span className="text-muted-foreground">
                {tx("settings.account.monthlyUsage", "Monthly usage")}
              </span>
              <span className="font-semibold text-foreground">{usedPercent}%</span>
            </div>
            <div
              className="mt-2 h-2 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-valuenow={usedPercent}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className={cn("h-full rounded-full transition-[width]", usageBarClass)}
                style={{ width: `${usedPercent}%` }}
              />
            </div>
          </div>

          {renewalLabel ? (
            <div className="mt-4 border-t border-border/55 pt-4">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {tx("settings.account.renewal", "Renewal")}
              </p>
              <p className="mt-1 text-[13px] text-foreground">{renewalLabel}</p>
            </div>
          ) : null}

          {usage.mode && usage.mode !== "normal" ? (
            <p className="mt-4 rounded-xl border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-[12.5px] text-amber-700 dark:text-amber-300">
              {usage.mode === "exhausted"
                ? tx(
                    "settings.account.usageExhausted",
                    "Monthly budget reached. Managed models stay on the lightest tier until renewal.",
                  )
                : tx(
                    "settings.account.usageReduced",
                    "Approaching the monthly budget - managed models are throttled.",
                  )}
            </p>
          ) : null}
        </section>
      ) : null}

      {tokenUsage ? (
        <section className="rounded-2xl border border-border/55 bg-card/50 p-5">
          <ModelTokenUsageTable usage={tokenUsage} />
        </section>
      ) : null}

      {connected ? teamCard : null}

      {manualSiteUrl ? (
        <p className="text-[12px] leading-relaxed text-muted-foreground">
          {tx(
            "settings.account.openManually",
            "Browser did not open automatically. Open this link:",
          )}{" "}
          <a
            href={manualSiteUrl}
            target="_blank"
            rel="noreferrer noopener"
            className="break-all font-medium text-foreground underline underline-offset-4"
          >
            {manualSiteUrl}
          </a>
        </p>
      ) : null}

      {error ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-[12.5px] text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
