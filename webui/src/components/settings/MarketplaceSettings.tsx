// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import { Download, ExternalLink, Loader2, Store } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useAccount } from "@/hooks/useAccount";
import { createSkill } from "@/lib/api";
import { notifySkillsChanged } from "@/lib/skill-events";
import {
  fetchMarketplaceSkills,
  installMarketplaceSkill,
  marketplaceBaseUrl,
  type MarketplaceSkillSummary,
} from "@/lib/marketplace";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const CLAWHUB_URL = "https://clawhub.ai";

export function MarketplaceSettings({
  onSkillsChange,
}: {
  onSkillsChange?: () => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const { account } = useAccount();
  const siteUrl = marketplaceBaseUrl(account?.server_url);

  const [skills, setSkills] = useState<MarketplaceSkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [catalogueOk, setCatalogueOk] = useState(false);
  const [statusHint, setStatusHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    setStatusHint(null);
    try {
      const result = await fetchMarketplaceSkills(siteUrl);
      setSkills(result.skills);
      if (result.errorCode === "unavailable") {
        setCatalogueOk(false);
        setStatusHint(
          t("settings.marketplace.unavailable", {
            url: siteUrl,
            defaultValue:
              "Navin Marketplace is not reachable on {{url}}. Deploy the site API (/api/marketplace/skills) and run the marketplace SQL, or use ClawHub below.",
          }),
        );
      } else if (result.errorCode === "http_error") {
        setCatalogueOk(false);
        setStatusHint(
          t("settings.marketplace.httpError", {
            url: siteUrl,
            status: result.status ?? "?",
            defaultValue:
              "Marketplace API on {{url}} returned HTTP {{status}}. Check deploy and SQL migrations, or use ClawHub.",
          }),
        );
      } else {
        setCatalogueOk(true);
      }
    } finally {
      setLoading(false);
    }
  }, [siteUrl, t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleInstall = async (skill: MarketplaceSkillSummary) => {
    setBusySlug(skill.slug);
    setError(null);
    setMessage(null);
    try {
      const result = await installMarketplaceSkill(skill.slug, {
        serverUrl: siteUrl,
      });
      const pkg = result.package;
      if (!pkg?.verified) {
        throw new Error(
          t("settings.marketplace.unsignedReject", {
            defaultValue: "Rejected: package signature missing or invalid.",
          }),
        );
      }

      if (token && pkg.skillMd?.trim()) {
        await createSkill(token, {
          name: pkg.slug,
          description: skill.description || pkg.name,
          markdown: pkg.skillMd,
        });
        notifySkillsChanged();
        await onSkillsChange?.();
        setMessage(
          t("settings.marketplace.installedLocal", {
            name: pkg.name,
            defaultValue: "Installed “{{name}}” into workspace skills.",
          }),
        );
      } else if (pkg.packageUrl) {
        setMessage(
          t("settings.marketplace.installedMetadata", {
            name: pkg.name,
            url: pkg.packageUrl,
            defaultValue:
              "Install recorded for “{{name}}”. Download the signed package: {{url}}",
          }),
        );
      } else {
        setMessage(
          t("settings.marketplace.installedHashOnly", {
            name: pkg.name,
            hash: pkg.contentHash ?? "",
            defaultValue:
              "Install recorded for “{{name}}” (content hash {{hash}}). No package URL available.",
          }),
        );
      }
    } catch (err) {
      const code = err instanceof Error ? err.message : String(err);
      if (code === "marketplace_unavailable") {
        setError(
          t("settings.marketplace.unavailable", {
            url: siteUrl,
            defaultValue:
              "Navin Marketplace is not reachable on {{url}}. Deploy the site API (/api/marketplace/skills) and run the marketplace SQL, or use ClawHub below.",
          }),
        );
      } else {
        setError(code);
      }
    } finally {
      setBusySlug(null);
    }
  };

  return (
    <section className="space-y-3 rounded-2xl border border-border/50 bg-card/80 px-4 py-4 sm:px-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-teal/15 text-teal ring-1 ring-teal/25">
            <Store className="h-5 w-5" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="text-[15px] font-semibold tracking-[-0.02em] text-foreground">
              {t("settings.marketplace.title", { defaultValue: "Marketplace" })}
            </p>
            <p className="mt-1 max-w-[46rem] text-[13px] leading-5 text-muted-foreground">
              {t("settings.marketplace.description", {
                defaultValue:
                  "Navin-native signed skills. Featured packages are verified before install. ClawHub remains available as an external registry.",
              })}
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground/80">
              {t("settings.marketplace.sourceLabel", {
                defaultValue: "Catalogue:",
              })}{" "}
              <a
                href={siteUrl}
                target="_blank"
                rel="noreferrer"
                className="font-medium text-foreground/80 underline-offset-2 hover:text-foreground hover:underline"
              >
                {siteUrl}
              </a>
            </p>
          </div>
        </div>
        <a
          href={CLAWHUB_URL}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-xl px-3 text-[12px] font-medium text-muted-foreground ring-1 ring-border/60 transition-colors hover:bg-muted/50 hover:text-foreground"
        >
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          {t("settings.marketplace.clawhubLink", {
            defaultValue: "ClawHub (external)",
          })}
        </a>
      </div>

      {statusHint ? (
        <p className="rounded-xl border border-border/60 bg-muted/40 px-3 py-2 text-[12px] leading-5 text-muted-foreground">
          {statusHint}
        </p>
      ) : null}
      {error ? (
        <p className="rounded-xl bg-destructive/8 px-3 py-2 text-[12px] text-destructive">
          {error}
        </p>
      ) : null}
      {message ? (
        <p className="rounded-xl bg-primary/8 px-3 py-2 text-[12px] text-foreground">
          {message}
        </p>
      ) : null}

      {loading ? (
        <div className="flex items-center gap-2 py-6 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t("settings.marketplace.loading", {
            defaultValue: "Loading marketplace…",
          })}
        </div>
      ) : !catalogueOk ? (
        <p className="py-2 text-[13px] text-muted-foreground">
          {t("settings.marketplace.useClawhub", {
            defaultValue:
              "Use ClawHub for skills until the Navin Marketplace API is deployed on your license server.",
          })}
        </p>
      ) : skills.length === 0 ? (
        <p className="py-4 text-[13px] text-muted-foreground">
          {t("settings.marketplace.empty", {
            defaultValue: "No featured Navin skills yet. Check back soon, or browse ClawHub.",
          })}
        </p>
      ) : (
        <ul className="divide-y divide-border/50 rounded-xl border border-border/40">
          {skills.map((skill) => {
            const busy = busySlug === skill.slug;
            return (
              <li
                key={skill.id}
                className="flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-medium text-foreground">
                    {skill.name}
                    <span className="ml-2 text-[11px] font-normal text-muted-foreground">
                      v{skill.latestVersion}
                    </span>
                  </p>
                  <p className="mt-0.5 line-clamp-2 text-[12px] leading-5 text-muted-foreground">
                    {skill.description || skill.slug}
                  </p>
                </div>
                <Button
                  type="button"
                  size="sm"
                  className={cn("h-9 shrink-0 rounded-xl px-3")}
                  disabled={busy}
                  onClick={() => void handleInstall(skill)}
                >
                  {busy ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Download className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                  )}
                  {t("settings.marketplace.install", { defaultValue: "Install" })}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
