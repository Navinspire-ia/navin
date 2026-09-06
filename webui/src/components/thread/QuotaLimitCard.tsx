import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ExternalLink, Gauge, KeyRound, Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useAccount } from "@/hooks/useAccount";
import { localizedPlanName } from "@/lib/accountPlan";
import { openExternalUrl, type AccountPayload } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const DEFAULT_SITE_URL = "https://navin.live";

/** Sentinel émis par le runner Python pour les plafonds d'abo géré. */
export const NAVIN_QUOTA_LIMIT_SENTINEL = "__NAVIN_QUOTA_LIMIT__";
/** Sentinel pour un 402 sur la clé BYOK de l'utilisateur. */
export const NAVIN_PROVIDER_CREDIT_SENTINEL = "__NAVIN_PROVIDER_CREDIT__";

export type QuotaBubbleKind = "plan" | "provider" | null;

/**
 * Un 402 BYOK ne doit jamais passer pour une limite d'abonnement.
 * Les anciennes bulles mélangent encore le sentinel plan et le texte
 * « API key is out of quota » : ce texte gagne, c'est un crédit fournisseur.
 */
export function quotaBubbleKind(content: string): QuotaBubbleKind {
  const text = content.trim();
  if (!text) return null;
  const lower = text.toLowerCase();
  if (
    text.startsWith(NAVIN_PROVIDER_CREDIT_SENTINEL)
    || lower.includes("api key is out of quota")
    || lower.includes("your own api key is out of credit")
  ) {
    return "provider";
  }
  if (
    text.startsWith(NAVIN_QUOTA_LIMIT_SENTINEL)
    || lower.includes("monthly model quota is used up")
  ) {
    return "plan";
  }
  return null;
}

/** True si le message assistant est une erreur de plafond d'abonnement. */
export function isQuotaLimitMessage(content: string): boolean {
  return quotaBubbleKind(content) === "plan";
}

export function isProviderCreditMessage(content: string): boolean {
  return quotaBubbleKind(content) === "provider";
}

export interface ProviderCreditDetails {
  /** Display name of the provider that refused the call (Z.AI, Anthropic...). */
  provider: string | null;
  /** Model slug the call was made for. */
  model: string | null;
  /** The provider's own message, verbatim. */
  detail: string | null;
}

/** Line prefix written by navin/agent/runner.py (_PROVIDER_MESSAGE_PREFIX). */
const PROVIDER_MESSAGE_PREFIX = "Provider message: ";
const PROVIDER_REFUSAL_RE =
  /^(.+?) refused the call(?: for (.+?))?: the key is out of credit\.$/;
const GENERIC_PROVIDER_LABEL = "the provider";

/**
 * Lit la bulle BYOK produite par le runner. Les anciennes bulles ("Your own
 * API key is out of credit...") ne portent ni fournisseur ni message : tout
 * reste à null et la carte affiche son texte générique.
 */
export function parseProviderCreditMessage(content: string): ProviderCreditDetails {
  const out: ProviderCreditDetails = { provider: null, model: null, detail: null };
  const lines = content
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  for (const line of lines) {
    if (line.startsWith(NAVIN_PROVIDER_CREDIT_SENTINEL)) continue;
    if (line.startsWith(PROVIDER_MESSAGE_PREFIX)) {
      out.detail = line.slice(PROVIDER_MESSAGE_PREFIX.length).trim() || null;
      continue;
    }
    const match = PROVIDER_REFUSAL_RE.exec(line);
    if (match) {
      const provider = match[1].trim();
      out.provider =
        provider && provider.toLowerCase() !== GENERIC_PROVIDER_LABEL ? provider : null;
      out.model = match[2]?.trim() || null;
    }
  }
  return out;
}

export interface SelectedModelRoute {
  /** Provider id behind the model picked in the composer ("navin" = plan key). */
  provider: string | null;
  /** That provider's display label, the name the runner writes in refusals. */
  providerLabel: string | null;
}

/**
 * The refusal no longer describes what the next send would use: a plan card
 * while a BYOK / local model is selected, a provider card while another
 * provider is selected. Kept conservative on purpose: another Navin model on
 * an exhausted plan, or another model on the same empty key, still fails.
 */
export function refusalOutdatedForSelection(
  content: string,
  route: SelectedModelRoute | null | undefined,
): boolean {
  if (!route) return false;
  const kind = quotaBubbleKind(content);
  if (kind === "plan") {
    const provider = route.provider?.trim().toLowerCase() ?? "";
    return provider !== "" && provider !== "navin";
  }
  if (kind === "provider") {
    const named = parseProviderCreditMessage(content).provider?.trim().toLowerCase() ?? "";
    const selected = route.providerLabel?.trim().toLowerCase() ?? "";
    return named !== "" && selected !== "" && named !== selected;
  }
  return false;
}

export type QuotaCardVariant = "checking" | "desync" | "no-budget" | "limit";

/**
 * Décide quelle carte montrer pour un appel refusé.
 *
 * Une offre sans budget inclus (Free, ou BYOK seul) n'expose aucun `usage`.
 * Lire cette absence comme "0 % consommé" annoncerait un budget sain juste
 * après un refus bien réel, d'où le test explicite sur le budget.
 */
export function quotaCardVariant(
  account: Pick<AccountPayload, "usage"> | null,
  loading: boolean,
): QuotaCardVariant {
  if (!account || loading) return "checking";
  const budget = account.usage?.budget_micro_usd ?? 0;
  if (budget <= 0) return "no-budget";
  const usedPercent = Math.min(100, Math.max(0, account.usage?.used_percent ?? 0));
  return usedPercent < 85 ? "desync" : "limit";
}

interface QuotaLimitCardProps {
  /**
   * A later user prompt exists: the refusal is history, not the current
   * state of the plan. One muted line, no account refresh, no actions.
   */
  compact?: boolean;
}

/** Past refusal, kept short: a card shouting "limit reached" under a thread
 *  that has moved on (other model, topped-up plan) read as a live problem. */
function CompactRefusal({
  icon,
  text,
  testId,
}: {
  icon: ReactNode;
  text: string;
  testId: string;
}) {
  return (
    <p
      role="status"
      className="flex max-w-xl items-start gap-2 text-[12.5px] leading-relaxed text-muted-foreground"
      data-testid={testId}
    >
      <span className="mt-0.5 shrink-0 text-muted-foreground/70">{icon}</span>
      <span className="min-w-0">{text}</span>
    </p>
  );
}

/**
 * Remplace le long message provider "out of quota".
 * Si le budget abo est encore dispo (cas post-upgrade / bulle historique),
 * on n'affiche PAS une fausse "limite atteinte".
 */
export function QuotaLimitCard({ compact = false }: QuotaLimitCardProps = {}) {
  const { t, i18n } = useTranslation();
  const { token } = useClient();
  const { account, reload, loading } = useAccount();
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  useEffect(() => {
    if (compact) return;
    void reload({ refresh: true });
  }, [compact, reload]);

  if (compact) {
    return (
      <CompactRefusal
        icon={<Gauge className="h-3.5 w-3.5" aria-hidden />}
        text={tx(
          "quotaLimit.planCompact",
          "Not answered: the Navin plan's monthly model budget was used up at the time.",
        )}
        testId="quota-limit-compact"
      />
    );
  }

  const usedPercent = Math.min(
    100,
    Math.max(0, account?.usage?.used_percent ?? 0),
  );
  const variant = quotaCardVariant(account, loading);
  const hasManagedBudget = variant !== "no-budget";
  const planLabel =
    localizedPlanName(t, account) ||
    tx("quotaLimit.planFallback", "your plan");
  const periodEndSec =
    account?.usage?.period_end || account?.period_end || null;
  const renewalLabel = periodEndSec
    ? new Date(periodEndSec * 1000).toLocaleDateString(i18n.language || undefined)
    : null;

  const siteUrl = (account?.server_url || DEFAULT_SITE_URL).replace(/\/+$/, "");
  const locale = (i18n.language || "en").split("-")[0] || "en";

  const openUpgrade = async () => {
    setBusy(true);
    try {
      const href = `${siteUrl}/${locale}/dashboard#subscription`;
      const result = await openExternalUrl(token, href);
      if (!result.opened) window.open(href, "_blank", "noopener,noreferrer");
    } finally {
      setBusy(false);
    }
  };

  const refreshAccount = async () => {
    setRefreshing(true);
    try {
      await reload({ refresh: true });
    } finally {
      setRefreshing(false);
    }
  };

  if (variant === "checking") {
    return (
      <div className="flex max-w-md items-center gap-2 rounded-2xl border border-border/45 bg-card/50 p-4 text-[12.5px] text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        {tx("quotaLimit.checking", "Checking your plan…")}
      </div>
    );
  }

  if (variant === "desync") {
    return (
      <div
        role="status"
        className={cn(
          "max-w-md rounded-2xl border border-border/45 bg-card/50 p-4",
          "text-[12.5px] leading-relaxed text-muted-foreground",
        )}
      >
        <p className="font-medium text-foreground">
          {tx("quotaLimit.desyncTitle", "Model access blocked")}
        </p>
        <p className="mt-1">
          {tx(
            "quotaLimit.desyncBody",
            "Your plan still has budget, but the managed key is out of sync (common after an upgrade). Refresh the account, then retry your message.",
          )}
        </p>
        <p className="mt-2 text-[11px]">
          {tx("quotaLimit.monthlyUsage", "Monthly usage")}
          {planLabel ? ` · ${planLabel}` : ""} · {usedPercent}%
          {renewalLabel
            ? ` · ${tx("quotaLimit.renewal", "Renewal")} ${renewalLabel}`
            : ""}
        </p>
        <div className="mt-3">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={refreshing}
            onClick={() => void refreshAccount()}
          >
            {refreshing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            )}
            {tx("quotaLimit.refreshAccount", "Refresh account")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div
      role="alert"
      className={cn(
        "max-w-md rounded-2xl border border-border/55 bg-card/70 p-4",
        "shadow-sm",
      )}
    >
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-muted/60 text-muted-foreground">
          <Gauge className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[14px] font-semibold text-foreground">
            {hasManagedBudget
              ? tx("quotaLimit.title", "Plan limit reached")
              : tx("quotaLimit.noBudgetTitle", "No included model budget")}
          </h3>
          <p className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">
            {hasManagedBudget
              ? tx(
                  "quotaLimit.body",
                  "Your subscription quota for managed models is used up for this period. Upgrade to keep going, or wait until renewal.",
                )
              : tx(
                  "quotaLimit.noBudgetBody",
                  "Your current plan includes no managed model budget, so Navin models cannot run. Upgrade your plan, or add your own provider key in Settings, Providers.",
                )}
          </p>
        </div>
      </div>

      {hasManagedBudget ? (
        <div className="mt-3 rounded-xl border border-border/45 bg-background/40 px-3 py-2.5">
          <div className="flex items-baseline justify-between gap-2 text-[12px]">
            <span className="text-muted-foreground">
              {tx("quotaLimit.monthlyUsage", "Monthly usage")}
              {planLabel ? (
                <span className="text-foreground/80"> · {planLabel}</span>
              ) : null}
            </span>
            <span className="font-semibold text-foreground">{usedPercent}%</span>
          </div>
          <div
            className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuenow={usedPercent}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full bg-red-500 transition-[width]"
              style={{ width: `${usedPercent}%` }}
            />
          </div>
          {renewalLabel ? (
            <p className="mt-2 text-[11px] text-muted-foreground">
              {tx("quotaLimit.renewal", "Renewal")} · {renewalLabel}
            </p>
          ) : null}
        </div>
      ) : planLabel ? (
        <p className="mt-3 text-[11px] text-muted-foreground">
          {tx("quotaLimit.currentPlan", "Current plan")} · {planLabel}
        </p>
      ) : null}

      <div className="mt-3">
        <Button
          type="button"
          size="sm"
          className="w-full sm:w-auto"
          disabled={busy}
          onClick={() => void openUpgrade()}
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          )}
          {tx("quotaLimit.upgrade", "Upgrade plan")}
        </Button>
      </div>
    </div>
  );
}

interface ProviderCreditCardProps {
  /** The persisted bubble: sentinel, refusal line, provider message, hint. */
  content?: string;
  /** Label of the model that ran the turn, when the bubble does not name it. */
  modelLabel?: string;
  /** A later user prompt exists: show the refusal as a past, one-line note. */
  compact?: boolean;
}

/**
 * 402 / crédit vide sur une clé BYOK: pas le budget Navin.
 * La carte cite le fournisseur mot pour mot : c'est lui qui dit où recharger.
 */
export function ProviderCreditCard({
  content = "",
  modelLabel,
  compact = false,
}: ProviderCreditCardProps) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const parsed = parseProviderCreditMessage(content);
  const providerName = parsed.provider;
  const modelName = modelLabel?.trim() || parsed.model;
  const subject = providerName && modelName
    ? `${providerName} · ${modelName}`
    : providerName || modelName || null;
  const who = providerName || tx("quotaLimit.providerFallback", "The provider");

  const openProviders = () => {
    const next = "#/settings?section=providers";
    if (window.location.hash !== next) {
      window.location.hash = next;
    }
  };

  if (compact) {
    return (
      <CompactRefusal
        icon={<KeyRound className="h-3.5 w-3.5" aria-hidden />}
        text={
          parsed.detail
            ? t("quotaLimit.providerCompactDetail", {
                defaultValue: "Not answered: {{provider}} refused the call. \"{{detail}}\"",
                provider: modelName ? `${who} (${modelName})` : who,
                detail: parsed.detail,
              })
            : t("quotaLimit.providerCompact", {
                defaultValue:
                  "Not answered: {{provider}} refused the call, its API key was out of credit.",
                provider: modelName ? `${who} (${modelName})` : who,
              })
        }
        testId="provider-credit-compact"
      />
    );
  }

  return (
    <div
      role="alert"
      className={cn(
        "max-w-md rounded-2xl border border-border/55 bg-card/70 p-4",
        "shadow-sm",
      )}
      data-testid="provider-credit-card"
    >
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-muted/60 text-muted-foreground">
          <KeyRound className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[14px] font-semibold text-foreground">
            {providerName
              ? t("quotaLimit.providerTitleNamed", {
                  defaultValue: "{{provider}} credit exhausted",
                  provider: providerName,
                })
              : tx("quotaLimit.providerTitle", "Provider credit exhausted")}
          </h3>
          {subject ? (
            <p className="mt-0.5 truncate text-[11.5px] text-muted-foreground/80" title={subject}>
              {subject}
            </p>
          ) : null}
          {parsed.detail ? (
            <blockquote
              className="mt-2 border-l-2 border-border/70 pl-3 text-[12.5px] leading-relaxed text-foreground/90"
              data-testid="provider-credit-detail"
            >
              {parsed.detail}
            </blockquote>
          ) : (
            <p className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">
              {tx(
                "quotaLimit.providerBody",
                "The provider rejected this call because the API key it runs on is out of credit.",
              )}
            </p>
          )}
          <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
            {tx(
              "quotaLimit.providerHint",
              "This model runs on your own API key. Top up at the provider, or pick another model.",
            )}
          </p>
        </div>
      </div>
      <div className="mt-3">
        <Button type="button" size="sm" variant="outline" onClick={openProviders}>
          {tx("quotaLimit.openProviders", "Open providers")}
        </Button>
      </div>
    </div>
  );
}
