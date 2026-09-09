// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Check, ExternalLink, Loader2, Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { TemplateInstallProgress } from "@/components/settings/TemplateInstallProgress";
import { createAppTemplate, fetchAppTemplates, installAppTemplate } from "@/lib/api";
import {
  fetchAwsAppTemplatesCatalog,
  mergeAppTemplates,
  STUDIO_CATEGORIES,
  templateBucket,
} from "@/lib/app-templates-fallback";
import { agentShortName, templateVisual } from "@/lib/app-templates-visuals";
import type { AppTemplateSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

type TemplateFilter = "all" | "chat" | "rag" | "agents" | "business";

const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };

export function AppTemplatesSettings({
  embedded = false,
  onCreated,
}: {
  embedded?: boolean;
  onCreated?: (dest: string, slug: string, previewUrl?: string | null) => void;
} = {}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const reduceMotion = useReducedMotion();
  const [templates, setTemplates] = useState<AppTemplateSummary[]>(() => mergeAppTemplates([]));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<TemplateFilter>("all");
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<AppTemplateSummary | null>(null);
  const [createDest, setCreateDest] = useState("");
  const [mode, setMode] = useState<"install" | "create">("install");
  const [runState, setRunState] = useState<"idle" | "running" | "done" | "error">("idle");
  const finishRef = useRef<{ dest: string; slug: string; preview: string | null } | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const awsRows = await fetchAwsAppTemplatesCatalog();
      if (awsRows) {
        setTemplates(mergeAppTemplates(awsRows));
        return;
      }
      const payload = await fetchAppTemplates(token);
      setTemplates(mergeAppTemplates(payload.templates ?? []));
    } catch {
      setTemplates(mergeAppTemplates([]));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // token is stable for the session
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const counts = useMemo(() => {
    const next = { all: templates.length, chat: 0, rag: 0, agents: 0, business: 0 };
    for (const row of templates) next[templateBucket(row)] += 1;
    return next;
  }, [templates]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return templates.filter((row) => {
      if (filter !== "all" && templateBucket(row) !== filter) return false;
      if (!needle) return true;
      const hay = [
        row.slug,
        row.name,
        row.description,
        row.category,
        row.source_name,
        row.stack.join(" "),
        row.domain_agents.join(" "),
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [filter, query, templates]);

  const filterOptions: Array<{ value: TemplateFilter; label: string; count: number }> = [
    {
      value: "all",
      label: t("settings.templates.filterAll", { defaultValue: "Tous" }),
      count: counts.all,
    },
    {
      value: "chat",
      label: t("settings.templates.filterChat", { defaultValue: "Chat" }),
      count: counts.chat,
    },
    {
      value: "rag",
      label: t("settings.templates.filterRag", { defaultValue: "RAG" }),
      count: counts.rag,
    },
    {
      value: "agents",
      label: t("settings.templates.filterAgents", { defaultValue: "Agents IA" }),
      count: counts.agents,
    },
    {
      value: "business",
      label: t("settings.templates.filterBusiness", { defaultValue: "Business" }),
      count: counts.business,
    },
  ];

  const closeUse = () => {
    if (runState === "running") return;
    setPending(null);
    setRunState("idle");
    finishRef.current = null;
  };

  const settleUse = () => {
    const next = finishRef.current;
    finishRef.current = null;
    setPending(null);
    setCreateDest("");
    setBusySlug(null);
    setRunState("idle");
    if (next && (next.dest || next.preview)) onCreated?.(next.dest, next.slug, next.preview);
    void refresh();
  };

  const runUse = async () => {
    if (!pending || runState === "running") return;
    if (mode === "create" && !createDest.trim()) {
      setError(
        t("settings.templates.destRequired", {
          defaultValue: "Enter a folder, e.g. ./mon-crm",
        }),
      );
      return;
    }
    setBusySlug(pending.slug);
    setRunState("running");
    setError(null);
    setMessage(null);
    finishRef.current = null;
    try {
      if (mode === "create") {
        const dest = createDest.trim();
        const result = await createAppTemplate(token, pending.slug, dest);
        const preview = result.preview_url || result.bootstrap?.preview_url || "";
        setMessage(
          t("settings.templates.createdStarted", {
            slug: result.slug,
            dest: result.dest,
            defaultValue: "Created {{slug}} in {{dest}}. Env, database and app are starting.",
          }),
        );
        finishRef.current = {
          dest: result.dest || dest,
          slug: result.slug,
          preview: preview || null,
        };
      } else {
        const result = await installAppTemplate(token, pending.slug);
        const preview = result.preview_url || result.bootstrap?.preview_url || "";
        const starting = Boolean(preview) || (result.started && result.started.length > 0);
        setMessage(
          starting
            ? t("settings.templates.installedStarted", {
                slug: result.slug,
                path: result.plugin_dir,
                defaultValue: "Installed {{slug}}. Env, database and app are starting.",
              })
            : t("settings.templates.installed", {
                slug: result.slug,
                path: result.plugin_dir,
                defaultValue: "Installed {{slug}} in this workspace",
              }),
        );
        finishRef.current = {
          dest: result.workspace || "",
          slug: result.slug,
          preview: preview || null,
        };
      }
      setRunState("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Install failed.");
      setRunState("error");
      setBusySlug(null);
    }
  };

  return (
    <div className={cn("flex flex-col gap-5", embedded && "p-4 sm:p-6")}>
      <section className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative min-w-0 flex-1">
          <Search
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            id="template-search"
            name="template-search"
            type="search"
            autoComplete="off"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("settings.templates.searchPlaceholder", {
              defaultValue: "Search CRM, finance, chatbot, RAG...",
            })}
            className="h-11 rounded-2xl border-border/60 bg-card/90 pl-10 text-[13px] shadow-sm"
          />
        </div>
        <div className="flex shrink-0 flex-wrap gap-1.5 rounded-2xl bg-muted/55 p-1">
          {filterOptions.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setFilter(option.value)}
              className={cn(
                "rounded-xl px-3 py-1.5 text-[12px] font-medium transition-colors",
                filter === option.value
                  ? "bg-primary/10 text-primary shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {option.label}
              <span className="ml-1 tabular-nums text-[11px] opacity-70">{option.count}</span>
            </button>
          ))}
        </div>
      </section>

      {error ? (
        <p className="rounded-2xl border border-destructive/25 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
          {error}
        </p>
      ) : null}
      {message ? (
        <p className="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-3 text-[13px] text-emerald-800 dark:text-emerald-200">
          {message}
        </p>
      ) : null}

      {loading && templates.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t("settings.templates.loading", { defaultValue: "Loading studio..." })}
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border/60 px-3 py-12 text-center text-sm text-muted-foreground">
          {t("settings.templates.empty", { defaultValue: "No templates match this view." })}
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          <AnimatePresence initial={false}>
            {visible.map((row, index) => {
              const visual = templateVisual(row.category);
              const Icon = visual.icon;
              const agents = row.domain_agents;
              return (
                <motion.article
                  key={row.slug}
                  initial={reduceMotion ? false : { opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{
                    ...spring,
                    delay: reduceMotion ? 0 : Math.min(index, 8) * 0.04,
                  }}
                  className="flex min-w-0 items-stretch gap-3 rounded-[20px] border border-border/45 bg-card/80 px-3.5 py-3.5 shadow-[0_1px_0_rgba(255,255,255,0.04)_inset,0_10px_28px_-22px_rgba(0,0,0,0.5)]"
                >
                  <div
                    className={cn(
                      "flex h-12 w-12 shrink-0 items-center justify-center rounded-[12px] shadow-[0_8px_18px_-10px_rgba(0,0,0,0.65)]",
                      visual.tile,
                      visual.ink,
                    )}
                  >
                    <Icon className="h-5 w-5" strokeWidth={2} aria-hidden />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-2">
                      <h3 className="truncate text-[15px] font-semibold leading-5 text-foreground">
                        {row.name}
                      </h3>
                      {row.featured && STUDIO_CATEGORIES.has(row.category) ? (
                        <span className="inline-flex shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
                          {t("settings.templates.studioBadge", { defaultValue: "Studio" })}
                        </span>
                      ) : null}
                      {row.installed ? (
                        <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300">
                          <Check className="h-3 w-3" aria-hidden />
                          {t("settings.templates.installedBadge", { defaultValue: "Installed" })}
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-1 line-clamp-2 text-[13px] leading-5 text-muted-foreground [text-wrap:pretty]">
                      {row.description}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <span
                        className={cn(
                          "rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.04em]",
                          visual.chip,
                        )}
                      >
                        {visual.label}
                      </span>
                      {agents.map((agent) => (
                        <span
                          key={agent}
                          className="rounded-full bg-muted/70 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                        >
                          {agentShortName(agent)}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-col items-end justify-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      className="h-9 rounded-xl px-3"
                      disabled={busySlug === row.slug}
                      onClick={() => {
                        setMode("install");
                        setCreateDest(`./mon-${row.slug}`);
                        setError(null);
                        setMessage(null);
                        setRunState("idle");
                        setPending(row);
                      }}
                    >
                      {busySlug === row.slug ? (
                        <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                      ) : null}
                      {t("settings.templates.use", { defaultValue: "Use" })}
                    </Button>
                    <a
                      href={row.source_github}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                    >
                      {row.source_name}
                      <ExternalLink className="h-3 w-3" aria-hidden />
                    </a>
                  </div>
                </motion.article>
              );
            })}
          </AnimatePresence>
        </div>
      )}

      <Dialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) closeUse();
        }}
      >
        <DialogContent className="max-w-lg">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void runUse();
            }}
          >
            <DialogHeader>
              <DialogTitle>
                {t("settings.templates.useTitle", {
                  name: pending?.name ?? "",
                  defaultValue: "Use {{name}}",
                })}
              </DialogTitle>
              <DialogDescription>
                {t("settings.templates.useDescription", {
                  defaultValue:
                    "Install adds the plugin overlay to this workspace. Create scaffolds a new project folder.",
                })}
              </DialogDescription>
            </DialogHeader>
            {pending ? (
              <div className="space-y-3 text-[13px]">
                <p className="text-muted-foreground [text-wrap:pretty]">{pending.description}</p>
                <div>
                  <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
                    {t("settings.templates.agentsLabel", {
                      count: pending.domain_agents.length,
                      defaultValue: "{{count}} product agents",
                    })}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {pending.domain_agents.map((agent) => (
                      <span
                        key={agent}
                        className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-foreground"
                      >
                        {agentShortName(agent)}
                      </span>
                    ))}
                  </div>
                  <p className="mt-2 text-[11px] text-muted-foreground [text-wrap:pretty]">
                    {t("settings.templates.universalAgents", {
                      defaultValue:
                        "Plus 5 universal agents: research, analytics, document, knowledge, automation.",
                    })}
                  </p>
                </div>
                <div className="flex gap-1.5 rounded-2xl bg-muted/55 p-1">
                  <button
                    type="button"
                    disabled={runState === "running" || runState === "done"}
                    onClick={() => setMode("install")}
                    className={cn(
                      "flex-1 rounded-xl px-3 py-1.5 text-[12px] font-medium",
                      mode === "install"
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground",
                    )}
                  >
                    {t("settings.templates.modeInstall", { defaultValue: "Install here" })}
                  </button>
                  <button
                    type="button"
                    disabled={runState === "running" || runState === "done"}
                    onClick={() => setMode("create")}
                    className={cn(
                      "flex-1 rounded-xl px-3 py-1.5 text-[12px] font-medium",
                      mode === "create"
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground",
                    )}
                  >
                    {t("settings.templates.modeCreate", { defaultValue: "Create folder" })}
                  </button>
                </div>
                {mode === "create" ? (
                  <div className="space-y-1.5">
                    <label
                      htmlFor="template-create-dest"
                      className="text-[12px] font-medium text-foreground"
                    >
                      {t("settings.templates.destLabel", {
                        defaultValue: "Project folder",
                      })}
                    </label>
                    <Input
                      id="template-create-dest"
                      name="dest"
                      type="text"
                      autoComplete="off"
                      spellCheck={false}
                      value={createDest}
                      onChange={(event) => setCreateDest(event.target.value)}
                      placeholder="./mon-crm"
                      disabled={runState === "running" || runState === "done"}
                      className="h-10 rounded-xl"
                    />
                  </div>
                ) : (
                  <p className="rounded-xl bg-muted/40 px-3 py-2 text-[12px] text-muted-foreground">
                    {t("settings.templates.installHint", {
                      slug: pending.slug,
                      defaultValue:
                        "Writes .navin/apps/{{slug}}/ (navin.json + overlay.json + agents). Edit overlay.json to customize.",
                    })}
                  </p>
                )}
                {error ? (
                  <p
                    role="alert"
                    className="rounded-xl border border-destructive/25 bg-destructive/5 px-3 py-2 text-[12px] text-destructive"
                  >
                    {error}
                  </p>
                ) : null}
                {runState !== "idle" ? (
                  <TemplateInstallProgress
                    mode={mode}
                    running={runState === "running"}
                    finished={runState === "done"}
                    failed={runState === "error"}
                    onSettled={settleUse}
                  />
                ) : null}
              </div>
            ) : null}
            <DialogFooter className="mt-4">
              <Button
                type="button"
                variant="outline"
                onClick={closeUse}
                disabled={runState === "running" || runState === "done"}
              >
                {t("common.cancel", { defaultValue: "Cancel" })}
              </Button>
              <Button
                type="submit"
                disabled={Boolean(busySlug) || (mode === "create" && !createDest.trim())}
              >
                {busySlug ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                {mode === "create"
                  ? t("settings.templates.confirmCreate", { defaultValue: "Create" })
                  : t("settings.templates.confirmInstall", { defaultValue: "Use" })}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
