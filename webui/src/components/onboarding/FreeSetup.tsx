import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, ExternalLink, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  fetchAccount,
  fetchAccountConnectUrl,
  fetchOpenRouterConnectUrl,
  fetchOpenRouterStatus,
} from "@/lib/api";
import { publishAccount } from "@/hooks/useAccount";
import { cn } from "@/lib/utils";

const FREE_POLL_MS = 3000;
const FREE_POLL_FAST_MS = 1500;

export type FreeSetupState = {
  accountConnected: boolean;
  openRouterConnected: boolean;
  /** User clicked Connect and we are waiting for the browser OAuth callback. */
  accountPending: boolean;
  openRouterPending: boolean;
  /** Both connections done: free models are installed and ready. */
  done: boolean;
  busy: "" | "account" | "openrouter";
  error: string;
  connectAccount: () => void;
  connectOpenRouter: () => void;
};

/**
 * Drives the Free plan setup: polls the navin.live account and OpenRouter
 * connection status while `active`, and exposes the two connect actions
 * (each opens the browser on the corresponding OAuth page).
 *
 * Both steps support create-account OR sign-in on the same handoff page.
 * Leaving that page for a naked signup URL is what breaks sync.
 *
 * `onSynced` fires once when navin.live or OpenRouter flips to connected so
 * the IDE can refresh models/providers without a full page reload.
 */
export function useFreeSetup(
  active: boolean,
  token?: string,
  onSynced?: () => void,
): FreeSetupState {
  const { t, i18n } = useTranslation();
  const [accountConnected, setAccountConnected] = useState(false);
  const [openRouterConnected, setOpenRouterConnected] = useState(false);
  const [accountPending, setAccountPending] = useState(false);
  const [openRouterPending, setOpenRouterPending] = useState(false);
  const [busy, setBusy] = useState<"" | "account" | "openrouter">("");
  const [error, setError] = useState("");
  const previousRef = useRef({ account: false, openrouter: false });
  const onSyncedRef = useRef(onSynced);
  onSyncedRef.current = onSynced;

  // Poll both connections so the checkmarks appear on their own when the
  // user comes back from the browser. Faster while either OAuth is open.
  useEffect(() => {
    if (!active || !token) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const [account, openrouter] = await Promise.all([
          fetchAccount(token),
          fetchOpenRouterStatus(token),
        ]);
        if (cancelled) return;
        const accountOk = Boolean(account?.connected);
        const openRouterOk = Boolean(openrouter?.connected);
        if (account) publishAccount(account);
        setAccountConnected(accountOk);
        setOpenRouterConnected(openRouterOk);
        if (accountOk) setAccountPending(false);
        if (openRouterOk) setOpenRouterPending(false);

        const prev = previousRef.current;
        const accountJustConnected = accountOk && !prev.account;
        const openRouterJustConnected = openRouterOk && !prev.openrouter;
        previousRef.current = { account: accountOk, openrouter: openRouterOk };
        if (accountJustConnected || openRouterJustConnected) {
          onSyncedRef.current?.();
        }
      } catch {
        // Gateway briefly unreachable: keep the last known state.
      }
    };
    void tick();
    const waiting = accountPending || openRouterPending;
    const interval = waiting ? FREE_POLL_FAST_MS : FREE_POLL_MS;
    const id = window.setInterval(() => void tick(), interval);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [active, accountPending, openRouterPending, token]);

  // Leaving Free setup cancels the "waiting" UI; Connect can be clicked again.
  useEffect(() => {
    if (!active) {
      setAccountPending(false);
      setOpenRouterPending(false);
    }
  }, [active]);

  const connectAccount = useCallback(() => {
    if (!token) return;
    setBusy("account");
    setError("");
    // Always open /connect?state=… - create or sign-in stay on that page.
    setAccountPending(true);
    void fetchAccountConnectUrl(token, "", {
      open: true,
      locale: i18n.language?.slice(0, 2) || "en",
    })
      .catch(() => {
        setAccountPending(false);
        setError(t("onboarding.wizard.freeError"));
      })
      .finally(() => setBusy(""));
  }, [i18n.language, t, token]);

  const connectOpenRouter = useCallback(() => {
    if (!token) return;
    setBusy("openrouter");
    setError("");
    // Always open the OAuth PKCE URL - never a naked signup page. Signup /
    // sign-in are available on that same OpenRouter auth page.
    setOpenRouterPending(true);
    void fetchOpenRouterConnectUrl(token, "", {
      open: true,
      locale: i18n.language?.slice(0, 2) || "en",
    })
      .catch(() => {
        setOpenRouterPending(false);
        setError(t("onboarding.wizard.freeError"));
      })
      .finally(() => setBusy(""));
  }, [i18n.language, t, token]);

  return {
    accountConnected,
    openRouterConnected,
    accountPending,
    openRouterPending,
    done: accountConnected && openRouterConnected,
    busy,
    error,
    connectAccount,
    connectOpenRouter,
  };
}

/** The two guided connection rows (Navin account, then OpenRouter). */
export function FreeSetupSteps({ state }: { state: FreeSetupState }) {
  const { t } = useTranslation();
  const {
    accountConnected,
    openRouterConnected,
    accountPending,
    openRouterPending,
    busy,
    error,
    connectAccount,
    connectOpenRouter,
  } = state;

  return (
    <div className="grid gap-3">
      <div
        className={cn(
          "flex items-center justify-between gap-3 rounded-xl border px-4 py-3",
          accountConnected ? "border-primary/60 bg-primary/5" : "border-border",
        )}
      >
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">
            {t("onboarding.wizard.freeStepAccount")}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {accountConnected
              ? t("onboarding.wizard.freeStepAccountDone")
              : accountPending
                ? t("onboarding.wizard.freeStepAccountWaiting")
                : t("onboarding.wizard.freeStepAccountBody")}
          </div>
        </div>
        {accountConnected ? (
          <Check className="size-4 shrink-0 text-primary" aria-hidden />
        ) : (
          <Button
            type="button"
            size="sm"
            disabled={busy === "account"}
            onClick={connectAccount}
          >
            {busy === "account" || accountPending ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <ExternalLink className="size-3.5" aria-hidden />
            )}
            {accountPending
              ? t("onboarding.wizard.freeOpenRouterRetry", {
                  defaultValue: "Retry Connect",
                })
              : t("onboarding.wizard.freeConnect")}
          </Button>
        )}
      </div>
      {accountPending && !accountConnected ? (
        <ol className="list-decimal space-y-1 rounded-xl border border-border/60 bg-muted/30 px-4 py-3 pl-8 text-xs leading-5 text-muted-foreground">
          <li>{t("onboarding.wizard.freeAccountGuide1")}</li>
          <li>{t("onboarding.wizard.freeAccountGuide2")}</li>
          <li>{t("onboarding.wizard.freeAccountGuide3")}</li>
        </ol>
      ) : null}
      <div
        className={cn(
          "flex items-center justify-between gap-3 rounded-xl border px-4 py-3",
          openRouterConnected ? "border-primary/60 bg-primary/5" : "border-border",
          !accountConnected && !openRouterConnected ? "opacity-60" : "",
        )}
      >
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">
            {t("onboarding.wizard.freeStepOpenRouter")}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {openRouterConnected
              ? t("onboarding.wizard.freeStepOpenRouterDone")
              : openRouterPending
                ? t("onboarding.wizard.freeStepOpenRouterWaiting")
                : t("onboarding.wizard.freeStepOpenRouterBody")}
          </div>
        </div>
        {openRouterConnected ? (
          <Check className="size-4 shrink-0 text-primary" aria-hidden />
        ) : (
          <Button
            type="button"
            size="sm"
            disabled={!accountConnected || busy === "openrouter"}
            onClick={connectOpenRouter}
          >
            {busy === "openrouter" || openRouterPending ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <ExternalLink className="size-3.5" aria-hidden />
            )}
            {openRouterPending
              ? t("onboarding.wizard.freeOpenRouterRetry", {
                  defaultValue: "Retry Connect",
                })
              : t("onboarding.wizard.freeConnect")}
          </Button>
        )}
      </div>
      {openRouterPending && !openRouterConnected ? (
        <ol className="list-decimal space-y-1 rounded-xl border border-border/60 bg-muted/30 px-4 py-3 pl-8 text-xs leading-5 text-muted-foreground">
          <li>{t("onboarding.wizard.freeOpenRouterGuide1")}</li>
          <li>{t("onboarding.wizard.freeOpenRouterGuide2")}</li>
          <li>{t("onboarding.wizard.freeOpenRouterGuide3")}</li>
        </ol>
      ) : null}
      <p className="text-xs text-muted-foreground">
        {t("onboarding.wizard.freeModelsHint")}
      </p>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}

export type FreeSetupDialogProps = {
  open: boolean;
  onClose: () => void;
  /** Called when the setup finished (both connections done). */
  onDone: () => void;
  /** Fired when navin.live or OpenRouter just became connected. */
  onSynced?: () => void;
  token?: string;
};

/**
 * Standalone Free plan setup dialog, reachable outside the first-run wizard
 * (e.g. from the "no model is ready" banner).
 */
export function FreeSetupDialog({
  open,
  onClose,
  onDone,
  onSynced,
  token,
}: FreeSetupDialogProps) {
  const { t } = useTranslation();
  const state = useFreeSetup(open, token, onSynced);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="free-setup-dialog-title"
    >
      <div className="w-full max-w-lg rounded-2xl border border-border bg-card p-6 shadow-xl">
        <h2
          id="free-setup-dialog-title"
          className="text-2xl font-semibold tracking-tight text-foreground"
        >
          {t("onboarding.wizard.freeSetupTitle")}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {t("onboarding.wizard.freeSetupBody")}
        </p>
        <div className="mt-6">
          <FreeSetupSteps state={state} />
        </div>
        <div className="mt-8 flex items-center justify-between gap-3">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("onboarding.wizard.freeSetupLater")}
          </Button>
          <Button type="button" disabled={!state.done} onClick={onDone}>
            {t("onboarding.wizard.freeSetupDone")}
          </Button>
        </div>
      </div>
    </div>
  );
}
