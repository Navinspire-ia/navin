// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { TFunction } from "i18next";
import {
  Brain,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  ClipboardPaste,
  Download,
  KeyRound,
  Loader2,
  PackagePlus,
  Pencil,
  Plus,
  Search,
  Terminal,
  Trash2,
  Wand2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
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
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { InstallSkillDialog } from "@/components/settings/InstallSkillDialog";
import { createSkill, deleteSkill, fetchSkillDetail, setupSkill, updateSkill } from "@/lib/api";
import { readLastDevContext } from "@/lib/last-dev-context";
import { notifySkillsChanged } from "@/lib/skill-events";
import type { SkillDetail, SkillSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

type SkillFilter = "all" | "ready" | "setup" | "custom" | "careers";

const CATEGORY_ORDER = [
  "custom",
  "plugin",
  "seo",
  "marketing",
  "ads",
  "meeting",
  "writing",
  "sales",
  "careers",
  "documents",
  "intelligence",
  "security",
  "navigation",
  "devops",
  "data",
  "core",
] as const;

const CATEGORY_FALLBACK_LABELS: Record<string, string> = {
  custom: "Custom",
  plugin: "Installed skills",
  seo: "SEO",
  marketing: "Marketing",
  ads: "Ads",
  meeting: "Meeting",
  writing: "Writing & Copy",
  sales: "Sales",
  careers: "Careers & HR",
  documents: "Documents",
  intelligence: "Intelligence",
  security: "Security",
  navigation: "Web & Research",
  devops: "DevOps",
  data: "Data",
  core: "Core",
};

function skillCategory(skill: SkillSummary): string {
  const raw = (skill.category ?? "").toLowerCase();
  const normalized = raw === "career" ? "careers" : raw;
  if (normalized && CATEGORY_ORDER.includes(normalized as (typeof CATEGORY_ORDER)[number])) {
    return normalized;
  }
  if (normalized) return normalized;
  if ((skill.default_for ?? "").toLowerCase() === "career") return "careers";
  return skill.source === "workspace" ? "custom" : "core";
}

function skillCategoryLabel(category: string, t: TFunction): string {
  return t(`settings.skills.categories.${category}`, {
    defaultValue:
      CATEGORY_FALLBACK_LABELS[category] ??
      category.charAt(0).toUpperCase() + category.slice(1),
  });
}

export function SkillsCatalogSettings({
  skills,
  onSkillsChange,
}: {
  skills: SkillSummary[];
  onSkillsChange?: () => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<SkillFilter>("all");
  const [selectedSkill, setSelectedSkill] = useState<SkillSummary | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [installOpen, setInstallOpen] = useState(false);

  const readyCount = skills.filter((skill) => skill.available).length;
  const setupCount = skills.filter((skill) => !skill.available).length;
  const customCount = skills.filter((skill) => skill.source === "workspace").length;
  const careersCount = skills.filter(
    (skill) => skillCategory(skill) === "careers" || (skill.default_for ?? "") === "career",
  ).length;

  const groupedSkills = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const filtered = skills
      .filter((skill) => {
        if (filter === "ready") return skill.available;
        if (filter === "setup") return !skill.available;
        if (filter === "custom") return skill.source === "workspace";
        if (filter === "careers") {
          return skillCategory(skill) === "careers" || (skill.default_for ?? "") === "career";
        }
        return true;
      })
      .filter((skill) => {
        if (!normalized) return true;
        return `${skill.name} ${skill.description} ${skill.unavailable_reason ?? ""} ${skillCategory(skill)}`
          .toLowerCase()
          .includes(normalized);
      });

    const byCategory = new Map<string, SkillSummary[]>();
    for (const skill of filtered) {
      const category = skillCategory(skill);
      const bucket = byCategory.get(category);
      if (bucket) bucket.push(skill);
      else byCategory.set(category, [skill]);
    }
    for (const bucket of byCategory.values()) {
      bucket.sort((left, right) => {
        const rank = Number(!left.available) - Number(!right.available);
        return rank || left.name.localeCompare(right.name);
      });
    }
    const knownOrder = new Map<string, number>(
      CATEGORY_ORDER.map((value, index) => [value, index]),
    );
    return Array.from(byCategory.entries()).sort((left, right) => {
      const leftRank = knownOrder.get(left[0]) ?? 500;
      const rightRank = knownOrder.get(right[0]) ?? 500;
      return leftRank - rightRank || left[0].localeCompare(right[0]);
    });
  }, [filter, query, skills]);

  const filteredCount = groupedSkills.reduce((sum, [, list]) => sum + list.length, 0);

  const filterOptions: Array<{ value: SkillFilter; label: string; count: number }> = [
    { value: "all", label: t("settings.skills.filterAll", { defaultValue: "All" }), count: skills.length },
    {
      value: "ready",
      label: t("settings.skills.filterAvailable", { defaultValue: "Ready" }),
      count: readyCount,
    },
    {
      value: "setup",
      label: t("settings.skills.filterUnavailable", { defaultValue: "Needs setup" }),
      count: setupCount,
    },
    {
      value: "custom",
      label: t("settings.skills.filterCustom", { defaultValue: "Custom" }),
      count: customCount,
    },
    {
      value: "careers",
      label: t("settings.skills.filterCareers", { defaultValue: "Career" }),
      count: careersCount,
    },
  ];

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-border/50 bg-gradient-to-br from-primary/[0.07] via-card to-teal/10 px-4 py-5 sm:px-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-[0_10px_24px_rgba(3,105,255,0.28)]">
              <Brain className="h-5 w-5" aria-hidden />
            </span>
            <div className="min-w-0">
              <p className="text-[16px] font-semibold tracking-[-0.02em] text-foreground">
                {t("settings.skills.heroTitle", { defaultValue: "Skills" })}
              </p>
              <p className="mt-1 max-w-[46rem] text-[13px] leading-6 text-muted-foreground">
                {t("settings.skills.description", {
                  defaultValue:
                    "Instruction packs the agent loads in chat. Custom skills are saved in your workspace and used by the agent like built-ins.",
                })}
              </p>
              <div className="mt-3 flex flex-wrap gap-2 text-[12px]">
                <span className="rounded-full bg-background/80 px-2.5 py-1 font-medium text-foreground ring-1 ring-border/60">
                  {t("settings.skills.caption", {
                    available: readyCount,
                    total: skills.length,
                    defaultValue: "{{available}} ready · {{total}} total",
                  })}
                </span>
                <span className="rounded-full bg-background/80 px-2.5 py-1 font-medium text-foreground ring-1 ring-border/60">
                  {t("settings.skills.customCaption", {
                    count: customCount,
                    defaultValue: "{{count}} custom",
                  })}
                </span>
              </div>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              className="h-10 rounded-xl px-4"
              onClick={() => setInstallOpen(true)}
            >
              <PackagePlus className="mr-1.5 h-4 w-4" aria-hidden />
              {t("settings.skills.install", { defaultValue: "Install skill" })}
            </Button>
            <Button
              type="button"
              className="h-10 rounded-xl px-4"
              onClick={() => setCreateOpen(true)}
            >
              <Plus className="mr-1.5 h-4 w-4" aria-hidden />
              {t("settings.skills.addCustom", { defaultValue: "Add skill" })}
            </Button>
          </div>
        </div>
      </section>

      <InstallSkillDialog
        open={installOpen}
        onOpenChange={setInstallOpen}
        projectPath={readLastDevContext()?.projectPath}
        onInstalled={async () => {
          await onSkillsChange?.();
        }}
      />

      <section className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative min-w-0 flex-1">
          <Search
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("settings.skills.searchPlaceholder", {
              defaultValue: "Search skills",
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
              <span className="ml-1 text-[11px] opacity-70">{option.count}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-amber-500/20 bg-amber-500/[0.06] px-4 py-3 text-[13px] leading-5 text-muted-foreground">
        {t("settings.skills.setupHint", {
          defaultValue:
            "Needs setup ≠ off. Missing CLIs (e.g. gh, summarize) must be installed on the host. Custom skills are written to workspace/skills and appear in the agent skills summary immediately.",
        })}
      </section>

      {filteredCount ? (
        <div className="space-y-6">
          {groupedSkills.map(([category, categorySkills]) => (
            <section key={category}>
              <div className="mb-2.5 flex items-center gap-2 px-0.5">
                <h3 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
                  {skillCategoryLabel(category, t)}
                </h3>
                <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                  {categorySkills.length}
                </span>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                {categorySkills.map((skill) => (
                  <SkillCatalogRow
                    key={`${skill.source}:${skill.name}`}
                    skill={skill}
                    onSelect={setSelectedSkill}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-border/60 px-3 py-12 text-center text-sm text-muted-foreground">
          {t("settings.skills.empty", { defaultValue: "No skills match this view." })}
        </div>
      )}

      <CreateSkillDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={async (skill) => {
          await onSkillsChange?.();
          setSelectedSkill(skill);
        }}
      />

      <SkillDetailSheet
        skill={selectedSkill}
        open={selectedSkill !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedSkill(null);
        }}
        onChanged={async () => {
          await onSkillsChange?.();
        }}
        onDeleted={async () => {
          setSelectedSkill(null);
          await onSkillsChange?.();
        }}
      />
    </div>
  );
}

function SkillCatalogRow({
  skill,
  onSelect,
}: {
  skill: SkillSummary;
  onSelect: (skill: SkillSummary) => void;
}) {
  const { t } = useTranslation();
  const sourceLabel = skillSourceLabel(skill.source, t);
  const StatusIcon = skill.available ? Check : CircleAlert;
  const statusLabel = skill.available
    ? t("settings.skills.statusAvailable", { defaultValue: "Ready" })
    : t("settings.skills.statusUnavailable", { defaultValue: "Needs setup" });

  return (
    <button
      type="button"
      aria-label={t("settings.skills.openDetails", {
        name: skill.name,
        defaultValue: "Open details for {{name}}",
      })}
      onClick={() => onSelect(skill)}
      className={cn(
        "group flex min-w-0 items-center gap-3 rounded-2xl border border-border/45 bg-card/80 px-3.5 py-3.5 text-left shadow-sm transition-colors",
        "hover:border-primary/25 hover:bg-primary/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        !skill.available && "opacity-85",
      )}
    >
      <div
        className={cn(
          "flex h-12 w-12 shrink-0 items-center justify-center rounded-[14px]",
          skill.available ? "bg-primary/10 text-primary" : "bg-muted/70 text-muted-foreground",
        )}
      >
        <Brain className="h-5 w-5" strokeWidth={1.8} aria-hidden />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="truncate text-[15px] font-semibold leading-5 text-foreground">
            {skill.name}
          </h3>
          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-semibold leading-none text-muted-foreground">
            {sourceLabel}
          </span>
          {(skill.default_for ?? "").toLowerCase() === "career" || skillCategory(skill) === "careers" ? (
            <span className="shrink-0 rounded-full bg-indigo-500/10 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-indigo-700 dark:text-indigo-300">
              {t("settings.skills.studioCareer", { defaultValue: "Career" })}
            </span>
          ) : null}
        </div>
        <p className="mt-1 line-clamp-2 text-[13px] leading-5 text-muted-foreground">
          {skill.description}
        </p>
        {!skill.available && skill.unavailable_reason ? (
          <p className="mt-1.5 truncate text-[12px] font-medium leading-4 text-amber-700 dark:text-amber-300">
            {t("settings.skills.unavailableReason", {
              reason: skill.unavailable_reason,
              defaultValue: "Missing: {{reason}}",
            })}
          </p>
        ) : null}
      </div>
      <span
        title={!skill.available && skill.unavailable_reason ? skill.unavailable_reason : undefined}
        className={cn(
          "hidden shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-[12px] font-medium sm:inline-flex",
          skill.available
            ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
            : "bg-amber-500/10 text-amber-800 dark:text-amber-200",
        )}
      >
        <StatusIcon className="h-3.5 w-3.5" aria-hidden />
        {statusLabel}
      </span>
    </button>
  );
}

type WizardMode = "guided" | "paste";
type WizardStep = 1 | 2 | 3;

function slugifySkillName(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "");
}

function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.replace(/^[-*\d.)\s]+/, "").trim())
    .filter(Boolean);
}

function splitTokens(value: string): string[] {
  return value
    .split(/[,\s]+/)
    .map((token) => token.trim())
    .filter(Boolean);
}

function buildSkillMarkdown(input: {
  slug: string;
  description: string;
  category: string;
  emoji: string;
  always: boolean;
  bins: string[];
  env: string[];
  whenToUse: string[];
  workflow: string[];
  guidelines: string;
}): string {
  const navin: Record<string, unknown> = { category: input.category || "custom" };
  if (input.emoji.trim()) navin.emoji = input.emoji.trim();
  if (input.always) navin.always = true;
  if (input.bins.length || input.env.length) {
    const requires: Record<string, string[]> = {};
    if (input.bins.length) requires.bins = input.bins;
    if (input.env.length) requires.env = input.env;
    navin.requires = requires;
  }
  const title = input.slug
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
  const whenToUse = input.whenToUse.length
    ? input.whenToUse
    : ["Trigger this skill when the user request matches the description above."];
  const workflow = input.workflow.length
    ? input.workflow
    : [
        "Clarify the goal and constraints.",
        "Gather the minimum context needed (files, tools, prior decisions).",
        "Execute step by step; verify intermediate results.",
        "Deliver a concise result with next actions if useful.",
      ];
  const lines: string[] = [
    "---",
    `name: ${input.slug}`,
    `description: ${JSON.stringify(input.description.trim())}`,
    `metadata: ${JSON.stringify({ navin })}`,
    "---",
    "",
    `# ${title}`,
    "",
    "## Overview",
    "",
    input.description.trim(),
    "",
    "## When to use",
    "",
    ...whenToUse.map((item) => `- ${item}`),
    "",
    "## Workflow",
    "",
    ...workflow.map((item, index) => `${index + 1}. ${item}`),
  ];
  if (input.guidelines.trim()) {
    lines.push("", "## Guidelines", "", input.guidelines.trim());
  }
  return lines.join("\n") + "\n";
}

function parsePastedSkill(text: string): {
  name: string | null;
  description: string | null;
  hasFrontmatter: boolean;
} {
  const match = text.match(/^\s*---\s*\r?\n([\s\S]*?)\r?\n---/);
  if (!match) return { name: null, description: null, hasFrontmatter: false };
  const unquote = (value: string) => {
    const trimmed = value.trim();
    if (
      (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
      (trimmed.startsWith("'") && trimmed.endsWith("'"))
    ) {
      try {
        return trimmed.startsWith('"') ? (JSON.parse(trimmed) as string) : trimmed.slice(1, -1);
      } catch {
        return trimmed.slice(1, -1);
      }
    }
    return trimmed;
  };
  const nameMatch = match[1].match(/^name:\s*(.+)$/m);
  const descMatch = match[1].match(/^description:\s*(.+)$/m);
  return {
    name: nameMatch ? unquote(nameMatch[1]) : null,
    description: descMatch ? unquote(descMatch[1]) : null,
    hasFrontmatter: true,
  };
}

const WIZARD_CATEGORY_OPTIONS = CATEGORY_ORDER.filter((value) => value !== "core");

function CreateSkillDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (skill: SkillSummary) => void | Promise<void>;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const [mode, setMode] = useState<WizardMode>("guided");
  const [step, setStep] = useState<WizardStep>(1);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("custom");
  const [emoji, setEmoji] = useState("");
  const [always, setAlways] = useState(false);
  const [bins, setBins] = useState("");
  const [env, setEnv] = useState("");
  const [whenToUse, setWhenToUse] = useState("");
  const [workflow, setWorkflow] = useState("");
  const [guidelines, setGuidelines] = useState("");
  const [finalMarkdown, setFinalMarkdown] = useState("");
  const [markdownEdited, setMarkdownEdited] = useState(false);

  const [pasteText, setPasteText] = useState("");
  const [pasteName, setPasteName] = useState("");
  const [pasteNameTouched, setPasteNameTouched] = useState(false);

  useEffect(() => {
    if (!open) return;
    setMode("guided");
    setStep(1);
    setSaving(false);
    setError(null);
    setName("");
    setDescription("");
    setCategory("custom");
    setEmoji("");
    setAlways(false);
    setBins("");
    setEnv("");
    setWhenToUse("");
    setWorkflow("");
    setGuidelines("");
    setFinalMarkdown("");
    setMarkdownEdited(false);
    setPasteText("");
    setPasteName("");
    setPasteNameTouched(false);
  }, [open]);

  const slug = slugifySkillName(name);
  const pasteParsed = useMemo(() => parsePastedSkill(pasteText), [pasteText]);
  const pasteSlug = slugifySkillName(pasteNameTouched ? pasteName : (pasteParsed.name ?? pasteName));

  useEffect(() => {
    if (pasteNameTouched) return;
    if (pasteParsed.name) setPasteName(pasteParsed.name);
  }, [pasteParsed.name, pasteNameTouched]);

  const generatedMarkdown = useMemo(
    () =>
      buildSkillMarkdown({
        slug: slug || "my-skill",
        description,
        category,
        emoji,
        always,
        bins: splitTokens(bins),
        env: splitTokens(env),
        whenToUse: splitLines(whenToUse),
        workflow: splitLines(workflow),
        guidelines,
      }),
    [slug, description, category, emoji, always, bins, env, whenToUse, workflow, guidelines],
  );

  const goToPreview = () => {
    if (!markdownEdited) setFinalMarkdown(generatedMarkdown);
    setStep(3);
  };

  const step1Valid = slug.length > 0 && description.trim().length > 0;

  const submitGuided = async () => {
    setSaving(true);
    setError(null);
    try {
      const created = await createSkill(token, {
        name: slug,
        description: description.trim(),
        markdown: (markdownEdited ? finalMarkdown : generatedMarkdown).trim(),
      });
      notifySkillsChanged();
      await onCreated(created);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const submitPaste = async () => {
    setSaving(true);
    setError(null);
    try {
      const created = await createSkill(token, {
        name: pasteSlug,
        description: (pasteParsed.description ?? pasteSlug).trim() || pasteSlug,
        markdown: pasteText.trim(),
      });
      notifySkillsChanged();
      await onCreated(created);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const stepLabels = [
    t("settings.skills.wizardStepIdentity", { defaultValue: "Identity" }),
    t("settings.skills.wizardStepContent", { defaultValue: "Instructions" }),
    t("settings.skills.wizardStepReview", { defaultValue: "Review" }),
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[min(92vh,52rem)] max-w-2xl flex-col gap-0 overflow-hidden p-0 sm:rounded-2xl">
        <DialogHeader className="shrink-0 border-b border-border/50 px-5 py-4 text-left">
          <DialogTitle>
            {t("settings.skills.createTitle", { defaultValue: "Add custom skill" })}
          </DialogTitle>
          <DialogDescription>
            {t("settings.skills.createDescription", {
              defaultValue:
                "Creates workspace/skills/<name>/SKILL.md. The agent will see it in the skills summary and can load it with read_file.",
            })}
          </DialogDescription>
          <div className="mt-3 flex gap-1.5 rounded-2xl bg-muted/55 p-1">
            <button
              type="button"
              onClick={() => {
                setMode("guided");
                setError(null);
              }}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded-xl px-3 py-1.5 text-[12px] font-medium transition-colors",
                mode === "guided"
                  ? "bg-primary/10 text-primary shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Wand2 className="h-3.5 w-3.5" aria-hidden />
              {t("settings.skills.wizardModeGuided", { defaultValue: "Guided wizard" })}
            </button>
            <button
              type="button"
              onClick={() => {
                setMode("paste");
                setError(null);
              }}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded-xl px-3 py-1.5 text-[12px] font-medium transition-colors",
                mode === "paste"
                  ? "bg-primary/10 text-primary shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <ClipboardPaste className="h-3.5 w-3.5" aria-hidden />
              {t("settings.skills.wizardModePaste", { defaultValue: "Paste SKILL.md" })}
            </button>
          </div>
          {mode === "guided" ? (
            <div className="mt-3 flex items-center gap-2">
              {stepLabels.map((label, index) => {
                const current = (index + 1) as WizardStep;
                const active = step === current;
                const done = step > current;
                return (
                  <div key={label} className="flex min-w-0 flex-1 items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        if (current < step) setStep(current);
                        else if (current === 3 && step1Valid) goToPreview();
                        else if (current === 2 && step1Valid) setStep(2);
                      }}
                      className={cn(
                        "flex min-w-0 flex-1 items-center gap-2 rounded-xl px-2.5 py-1.5 text-left transition-colors",
                        active ? "bg-primary/10" : done ? "hover:bg-muted/60" : "opacity-60",
                      )}
                    >
                      <span
                        className={cn(
                          "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
                          active
                            ? "bg-primary text-primary-foreground"
                            : done
                              ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                              : "bg-muted text-muted-foreground",
                        )}
                      >
                        {done ? <Check className="h-3 w-3" aria-hidden /> : current}
                      </span>
                      <span
                        className={cn(
                          "truncate text-[12px] font-medium",
                          active ? "text-primary" : "text-muted-foreground",
                        )}
                      >
                        {label}
                      </span>
                    </button>
                    {index < stepLabels.length - 1 ? (
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50" aria-hidden />
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : null}
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {mode === "guided" && step === 1 ? (
            <div className="space-y-4">
              <label className="block space-y-1.5">
                <span className="text-[12px] font-medium text-muted-foreground">
                  {t("settings.skills.nameLabel", { defaultValue: "Name" })}
                </span>
                <Input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="my-team-skill"
                  className="h-11 rounded-xl"
                  autoFocus
                />
                {slug ? (
                  <span className="block truncate text-[11px] text-muted-foreground">
                    workspace/skills/{slug}/SKILL.md
                  </span>
                ) : null}
              </label>
              <label className="block space-y-1.5">
                <span className="text-[12px] font-medium text-muted-foreground">
                  {t("settings.skills.descriptionLabel", { defaultValue: "Description" })}
                </span>
                <Textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder={t("settings.skills.descriptionPlaceholder", {
                    defaultValue: "When to use this skill and what it helps the agent do…",
                  })}
                  className="min-h-[96px] rounded-xl"
                />
                <span className="block text-[11px] text-muted-foreground">
                  {t("settings.skills.wizardDescriptionHint", {
                    defaultValue:
                      "The agent uses this description to decide when to load the skill - make it precise.",
                  })}
                </span>
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block space-y-1.5">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {t("settings.skills.wizardCategoryLabel", { defaultValue: "Category" })}
                  </span>
                  <select
                    value={category}
                    onChange={(event) => setCategory(event.target.value)}
                    className="h-11 w-full rounded-xl border border-input bg-background px-3 text-[13px] shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {WIZARD_CATEGORY_OPTIONS.map((value) => (
                      <option key={value} value={value}>
                        {skillCategoryLabel(value, t)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block space-y-1.5">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {t("settings.skills.wizardEmojiLabel", { defaultValue: "Emoji (optional)" })}
                  </span>
                  <Input
                    value={emoji}
                    onChange={(event) => setEmoji(event.target.value)}
                    placeholder="🧠"
                    className="h-11 rounded-xl"
                  />
                </label>
              </div>
            </div>
          ) : null}

          {mode === "guided" && step === 2 ? (
            <div className="space-y-4">
              <label className="block space-y-1.5">
                <span className="text-[12px] font-medium text-muted-foreground">
                  {t("settings.skills.wizardWhenLabel", {
                    defaultValue: "When to use (one trigger per line)",
                  })}
                </span>
                <Textarea
                  value={whenToUse}
                  onChange={(event) => setWhenToUse(event.target.value)}
                  placeholder={t("settings.skills.wizardWhenPlaceholder", {
                    defaultValue:
                      "The user asks for a competitor analysis\nThe user shares a market research brief",
                  })}
                  className="min-h-[88px] rounded-xl"
                />
              </label>
              <label className="block space-y-1.5">
                <span className="text-[12px] font-medium text-muted-foreground">
                  {t("settings.skills.wizardWorkflowLabel", {
                    defaultValue: "Workflow (one step per line)",
                  })}
                </span>
                <Textarea
                  value={workflow}
                  onChange={(event) => setWorkflow(event.target.value)}
                  placeholder={t("settings.skills.wizardWorkflowPlaceholder", {
                    defaultValue:
                      "Clarify the goal and constraints\nCollect sources with web_search\nWrite the report with citations",
                  })}
                  className="min-h-[110px] rounded-xl"
                />
              </label>
              <label className="block space-y-1.5">
                <span className="text-[12px] font-medium text-muted-foreground">
                  {t("settings.skills.wizardGuidelinesLabel", {
                    defaultValue: "Guidelines & constraints (optional)",
                  })}
                </span>
                <Textarea
                  value={guidelines}
                  onChange={(event) => setGuidelines(event.target.value)}
                  placeholder={t("settings.skills.wizardGuidelinesPlaceholder", {
                    defaultValue: "Tone, output format, limits, things to never do…",
                  })}
                  className="min-h-[80px] rounded-xl"
                />
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block space-y-1.5">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {t("settings.skills.wizardBinsLabel", {
                      defaultValue: "Required CLIs (optional)",
                    })}
                  </span>
                  <Input
                    value={bins}
                    onChange={(event) => setBins(event.target.value)}
                    placeholder="gh, ffmpeg"
                    className="h-11 rounded-xl"
                  />
                </label>
                <label className="block space-y-1.5">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {t("settings.skills.wizardEnvLabel", {
                      defaultValue: "Required ENV vars (optional)",
                    })}
                  </span>
                  <Input
                    value={env}
                    onChange={(event) => setEnv(event.target.value)}
                    placeholder="HUBSPOT_API_KEY"
                    className="h-11 rounded-xl"
                  />
                </label>
              </div>
              <label className="flex cursor-pointer items-start gap-2.5 rounded-2xl border border-border/45 bg-muted/25 px-3.5 py-3">
                <input
                  type="checkbox"
                  checked={always}
                  onChange={(event) => setAlways(event.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded border-input"
                />
                <span className="min-w-0">
                  <span className="block text-[13px] font-medium text-foreground">
                    {t("settings.skills.wizardAlwaysLabel", {
                      defaultValue: "Always active",
                    })}
                  </span>
                  <span className="block text-[12px] leading-5 text-muted-foreground">
                    {t("settings.skills.wizardAlwaysHint", {
                      defaultValue:
                        "Inject the full skill into every prompt instead of loading on demand. Use sparingly - it consumes context on every turn.",
                    })}
                  </span>
                </span>
              </label>
            </div>
          ) : null}

          {mode === "guided" && step === 3 ? (
            <div className="space-y-3">
              <p className="text-[13px] leading-5 text-muted-foreground">
                {t("settings.skills.wizardReviewHint", {
                  defaultValue:
                    "Final SKILL.md - you can still adjust it before creating. Frontmatter name and description are required.",
                })}
              </p>
              <Textarea
                value={markdownEdited ? finalMarkdown : generatedMarkdown}
                onChange={(event) => {
                  setMarkdownEdited(true);
                  setFinalMarkdown(event.target.value);
                }}
                className="min-h-[min(46vh,26rem)] rounded-2xl font-mono text-[12px] leading-6"
              />
              {markdownEdited ? (
                <button
                  type="button"
                  onClick={() => {
                    setMarkdownEdited(false);
                    setFinalMarkdown(generatedMarkdown);
                  }}
                  className="text-[12px] font-medium text-primary hover:underline"
                >
                  {t("settings.skills.wizardRegenerate", {
                    defaultValue: "Reset to generated version",
                  })}
                </button>
              ) : null}
            </div>
          ) : null}

          {mode === "paste" ? (
            <div className="space-y-4">
              <label className="block space-y-1.5">
                <span className="text-[12px] font-medium text-muted-foreground">
                  {t("settings.skills.wizardPasteLabel", {
                    defaultValue: "Full SKILL.md content",
                  })}
                </span>
                <Textarea
                  value={pasteText}
                  onChange={(event) => setPasteText(event.target.value)}
                  placeholder={
                    '---\nname: my-skill\ndescription: "When to use this skill…"\n---\n\n# My Skill\n\n## Workflow\n…'
                  }
                  className="min-h-[min(38vh,20rem)] rounded-2xl font-mono text-[12px] leading-6"
                  autoFocus
                />
              </label>
              {pasteText.trim() && !pasteParsed.hasFrontmatter ? (
                <p className="rounded-xl bg-amber-500/10 px-3 py-2 text-[13px] text-amber-800 dark:text-amber-200">
                  {t("settings.skills.wizardPasteNoFrontmatter", {
                    defaultValue:
                      "The content must start with YAML frontmatter (--- name / description ---).",
                  })}
                </p>
              ) : null}
              <label className="block space-y-1.5">
                <span className="text-[12px] font-medium text-muted-foreground">
                  {t("settings.skills.nameLabel", { defaultValue: "Name" })}
                </span>
                <Input
                  value={pasteNameTouched ? pasteName : (pasteParsed.name ?? pasteName)}
                  onChange={(event) => {
                    setPasteNameTouched(true);
                    setPasteName(event.target.value);
                  }}
                  placeholder="my-skill"
                  className="h-11 rounded-xl"
                />
                {pasteSlug ? (
                  <span className="block truncate text-[11px] text-muted-foreground">
                    workspace/skills/{pasteSlug}/SKILL.md
                  </span>
                ) : null}
              </label>
              {pasteParsed.description ? (
                <p className="rounded-xl bg-muted/40 px-3 py-2 text-[12px] leading-5 text-muted-foreground">
                  {t("settings.skills.wizardPasteDetected", {
                    defaultValue: "Detected description:",
                  })}{" "}
                  <span className="text-foreground/85">{pasteParsed.description}</span>
                </p>
              ) : null}
            </div>
          ) : null}

          {error ? (
            <p className="mt-4 rounded-xl bg-destructive/10 px-3 py-2 text-[13px] text-destructive">
              {error}
            </p>
          ) : null}
        </div>

        <DialogFooter className="shrink-0 border-t border-border/50 px-5 py-3 sm:justify-between">
          <div>
            {mode === "guided" && step > 1 ? (
              <Button
                type="button"
                variant="ghost"
                onClick={() => setStep((value) => (value - 1) as WizardStep)}
                disabled={saving}
              >
                <ChevronLeft className="mr-1 h-4 w-4" aria-hidden />
                {t("settings.skills.wizardBack", { defaultValue: "Back" })}
              </Button>
            ) : (
              <Button type="button" variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
                {t("deleteConfirm.cancel", { defaultValue: "Cancel" })}
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            {mode === "guided" && step === 1 ? (
              <Button type="button" onClick={() => setStep(2)} disabled={!step1Valid}>
                {t("settings.skills.wizardNext", { defaultValue: "Next" })}
                <ChevronRight className="ml-1 h-4 w-4" aria-hidden />
              </Button>
            ) : null}
            {mode === "guided" && step === 2 ? (
              <Button type="button" onClick={goToPreview}>
                {t("settings.skills.wizardPreview", { defaultValue: "Preview" })}
                <ChevronRight className="ml-1 h-4 w-4" aria-hidden />
              </Button>
            ) : null}
            {mode === "guided" && step === 3 ? (
              <Button type="button" onClick={() => void submitGuided()} disabled={saving}>
                {saving ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
                {t("settings.skills.createAction", { defaultValue: "Create skill" })}
              </Button>
            ) : null}
            {mode === "paste" ? (
              <Button
                type="button"
                onClick={() => void submitPaste()}
                disabled={saving || !pasteText.trim() || !pasteParsed.hasFrontmatter || !pasteSlug}
              >
                {saving ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
                {t("settings.skills.createAction", { defaultValue: "Create skill" })}
              </Button>
            ) : null}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SkillDetailSheet({
  skill,
  open,
  onOpenChange,
  onChanged,
  onDeleted,
}: {
  skill: SkillSummary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void | Promise<void>;
  onDeleted: () => void | Promise<void>;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [settingUp, setSettingUp] = useState(false);
  const [setupMessage, setSetupMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !skill) return;
    let cancelled = false;
    setDetail(null);
    setLoading(true);
    setLoadFailed(false);
    setEditing(false);
    setActionError(null);
    setSetupMessage(null);
    fetchSkillDetail(token, skill.name)
      .then((payload) => {
        if (!cancelled) {
          setDetail(payload);
          setDraft(payload.raw_markdown);
        }
      })
      .catch(() => {
        if (!cancelled) setLoadFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, skill, token]);

  if (!skill) return null;

  const activeSkill = detail ?? skill;
  const sourceLabel = skillSourceLabel(activeSkill.source, t);
  const statusLabel = activeSkill.available
    ? t("settings.skills.statusAvailable", { defaultValue: "Ready" })
    : t("settings.skills.statusUnavailable", { defaultValue: "Needs setup" });
  const canEdit = Boolean(activeSkill.editable ?? activeSkill.source === "workspace");
  const canDelete = Boolean(activeSkill.deletable ?? activeSkill.source === "workspace");
  const bodyMarkdown = stripFrontmatter(detail?.raw_markdown ?? "");

  const saveEdit = async () => {
    setSaving(true);
    setActionError(null);
    try {
      const updated = await updateSkill(token, skill.name, draft);
      setDetail(updated);
      setDraft(updated.raw_markdown);
      setEditing(false);
      notifySkillsChanged();
      await onChanged();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const removeSkill = async () => {
    setConfirmingDelete(false);
    setDeleting(true);
    setActionError(null);
    try {
      await deleteSkill(token, skill.name);
      notifySkillsChanged();
      await onDeleted();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  };

  const runSetup = async (optionId?: string) => {
    setSettingUp(true);
    setActionError(null);
    setSetupMessage(null);
    try {
      const result = await setupSkill(token, skill.name, optionId);
      setDetail(result.skill);
      setDraft(result.skill.raw_markdown);
      setSetupMessage(
        result.ok
          ? t("settings.skills.setupDone", {
              defaultValue: "Installed. The skill is now ready.",
            })
          : result.message,
      );
      notifySkillsChanged();
      await onChanged();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setSettingUp(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <ConfirmDialog
        open={confirmingDelete}
        title={t("settings.skills.deleteTitle", {
          name: skill.name,
          defaultValue: "Delete “{{name}}”?",
        })}
        description={t("settings.skills.deleteConfirm", {
          name: skill.name,
          defaultValue: "Delete custom skill “{{name}}”? This cannot be undone.",
        })}
        confirmLabel={t("settings.skills.deleteAction", { defaultValue: "Delete" })}
        onCancel={() => setConfirmingDelete(false)}
        onConfirm={() => void removeSkill()}
      />
      <SheetContent
        side="right"
        className="flex w-[min(40rem,calc(100vw-0.75rem))] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none"
      >
        <div className="shrink-0 border-b border-border/50 bg-gradient-to-br from-primary/[0.08] via-background to-teal/10 px-5 pb-4 pt-5">
          <div className="flex items-start gap-3 pr-8">
            <div
              className={cn(
                "flex h-12 w-12 shrink-0 items-center justify-center rounded-[15px]",
                activeSkill.available
                  ? "bg-primary text-primary-foreground shadow-[0_10px_24px_rgba(3,105,255,0.25)]"
                  : "bg-muted text-muted-foreground",
              )}
            >
              <Brain className="h-5 w-5" strokeWidth={1.8} aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <SheetTitle className="truncate text-[22px] font-semibold tracking-[-0.02em]">
                {activeSkill.name}
              </SheetTitle>
              <SheetDescription className="sr-only">
                {t("settings.skills.detailDescription", {
                  name: activeSkill.name,
                  defaultValue: "Details for {{name}}.",
                })}
              </SheetDescription>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <Pill>{sourceLabel}</Pill>
                <Pill>{skillCategoryLabel(skillCategory(activeSkill), t)}</Pill>
                <Pill tone={activeSkill.available ? "success" : "warning"}>{statusLabel}</Pill>
              </div>
            </div>
          </div>
          {(canEdit || canDelete) && !loading ? (
            <div className="mt-4 flex flex-wrap gap-2">
              {canEdit ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="rounded-xl"
                  onClick={() => {
                    setEditing((value) => !value);
                    setActionError(null);
                    if (detail) setDraft(detail.raw_markdown);
                  }}
                >
                  <Pencil className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                  {editing
                    ? t("settings.skills.cancelEdit", { defaultValue: "Cancel edit" })
                    : t("settings.skills.editAction", { defaultValue: "Edit SKILL.md" })}
                </Button>
              ) : null}
              {canDelete ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="rounded-xl text-destructive hover:bg-destructive/10 hover:text-destructive"
                  disabled={deleting}
                  onClick={() => setConfirmingDelete(true)}
                >
                  {deleting ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                  )}
                  {t("settings.skills.deleteAction", { defaultValue: "Delete" })}
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {t("settings.skills.loadingDetail", { defaultValue: "Loading skill details..." })}
            </div>
          ) : loadFailed ? (
            <div className="rounded-2xl bg-destructive/10 px-3 py-3 text-sm text-destructive">
              {t("settings.skills.loadFailed", { defaultValue: "Could not load skill details." })}
            </div>
          ) : editing ? (
            <div className="space-y-3">
              <p className="text-[13px] leading-5 text-muted-foreground">
                {t("settings.skills.editHint", {
                  defaultValue:
                    "Keep YAML frontmatter with name + description. The agent uses the description for triggering.",
                })}
              </p>
              <Textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                className="min-h-[min(55vh,28rem)] rounded-2xl font-mono text-[12px] leading-6"
              />
              {actionError ? (
                <p className="rounded-xl bg-destructive/10 px-3 py-2 text-[13px] text-destructive">
                  {actionError}
                </p>
              ) : null}
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={() => setEditing(false)} disabled={saving}>
                  {t("deleteConfirm.cancel", { defaultValue: "Cancel" })}
                </Button>
                <Button type="button" onClick={() => void saveEdit()} disabled={saving || !draft.trim()}>
                  {saving ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
                  {t("settings.skills.saveAction", { defaultValue: "Save skill" })}
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              <section className="rounded-2xl border border-border/45 bg-card/70 px-4 py-3.5">
                <h3 className="text-[12px] font-medium uppercase tracking-[0.04em] text-muted-foreground">
                  {t("settings.skills.descriptionTitle", { defaultValue: "Description" })}
                </h3>
                <p className="mt-2 text-[14px] leading-6 text-foreground/90">
                  {activeSkill.description}
                </p>
              </section>

              <div className="grid grid-cols-2 gap-2">
                <MetaItem
                  label={t("settings.skills.source", { defaultValue: "Source" })}
                  value={sourceLabel}
                />
                <MetaItem
                  label={t("settings.skills.status", { defaultValue: "Status" })}
                  value={statusLabel}
                />
              </div>

              {!activeSkill.available && activeSkill.unavailable_reason ? (
                <section className="rounded-2xl border border-amber-500/25 bg-amber-500/[0.07] px-4 py-3.5">
                  <h3 className="text-[12px] font-medium uppercase tracking-[0.04em] text-amber-800 dark:text-amber-200">
                    {t("settings.skills.unavailableReasonLabel", {
                      defaultValue: "Unavailable reason",
                    })}
                  </h3>
                  <p className="mt-2 text-[13px] leading-5 text-amber-900 dark:text-amber-100">
                    {activeSkill.unavailable_reason}
                  </p>
                  {detail?.setup?.can_setup ? (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        size="sm"
                        className="rounded-xl"
                        disabled={settingUp}
                        onClick={() => void runSetup()}
                      >
                        {settingUp ? (
                          <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                        ) : (
                          <Download className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                        )}
                        {settingUp
                          ? t("settings.skills.setupRunning", { defaultValue: "Installing…" })
                          : t("settings.skills.setupAction", { defaultValue: "Setup" })}
                      </Button>
                      <span className="text-[12px] text-muted-foreground">
                        {detail.setup.options.find((option) => option.runnable)?.command}
                      </span>
                    </div>
                  ) : detail?.setup?.options.length ? (
                    <div className="mt-2 space-y-1.5">
                      <p className="text-[12px] leading-5 text-muted-foreground">
                        {t("settings.skills.setupManual", {
                          defaultValue:
                            "Automatic install is not possible on this host (missing package manager or sudo rights). Run one of these commands, then restart Navin:",
                        })}
                      </p>
                      {detail.setup.options.map((option) => (
                        <code
                          key={option.id}
                          className="block rounded-lg bg-background/70 px-2.5 py-1.5 font-mono text-[12px] text-foreground/85"
                        >
                          {option.command}
                        </code>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-2 text-[12px] leading-5 text-muted-foreground">
                      {t("settings.skills.notAToggle", {
                        defaultValue:
                          "There is no Activate button. Install the missing CLI/env on the host, then restart Navin.",
                      })}
                    </p>
                  )}
                </section>
              ) : null}

              {setupMessage ? (
                <p className="rounded-xl bg-emerald-500/10 px-3 py-2 text-[13px] text-emerald-700 dark:text-emerald-300">
                  {setupMessage}
                </p>
              ) : null}

              {detail ? <RequirementsSection detail={detail} /> : null}

              {actionError ? (
                <p className="rounded-xl bg-destructive/10 px-3 py-2 text-[13px] text-destructive">
                  {actionError}
                </p>
              ) : null}

              <section>
                <h3 className="mb-2 text-[12px] font-medium uppercase tracking-[0.04em] text-muted-foreground">
                  {t("settings.skills.instructionsTitle", { defaultValue: "Instructions" })}
                </h3>
                <div
                  className={cn(
                    "prose prose-sm max-w-none rounded-2xl border border-border/45 bg-muted/15 px-4 py-4",
                    "prose-headings:scroll-mt-4 prose-headings:font-semibold prose-p:leading-6",
                    "prose-pre:rounded-xl prose-pre:bg-background/80 dark:prose-invert",
                  )}
                >
                  {bodyMarkdown ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{bodyMarkdown}</ReactMarkdown>
                  ) : (
                    <p className="text-muted-foreground">
                      {t("settings.skills.rawInstructionsEmpty", {
                        defaultValue: "No raw instructions.",
                      })}
                    </p>
                  )}
                </div>
              </section>

              {detail?.raw_markdown ? (
                <details className="group rounded-2xl border border-border/45 bg-muted/20 px-3 py-3">
                  <summary className="cursor-pointer select-none text-[13px] font-medium text-foreground/90">
                    {t("settings.skills.rawInstructions", { defaultValue: "Raw SKILL.md" })}
                  </summary>
                  <pre className="mt-3 max-h-[min(36vh,24rem)] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-background/70 px-3.5 py-3 font-mono text-[12px] leading-[1.7] text-foreground/65">
                    {detail.raw_markdown}
                  </pre>
                </details>
              ) : null}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function RequirementsSection({ detail }: { detail: SkillDetail }) {
  const { t } = useTranslation();
  const { bins, env, missing_bins, missing_env } = detail.requirements;
  const hasRequirements = bins.length > 0 || env.length > 0;

  return (
    <section>
      <h3 className="mb-2 text-[12px] font-medium uppercase tracking-[0.04em] text-muted-foreground">
        {t("settings.skills.requirements", { defaultValue: "Requirements" })}
      </h3>
      {hasRequirements ? (
        <div className="space-y-3 rounded-2xl border border-border/45 bg-card/60 px-4 py-3.5">
          {missing_bins.length ? (
            <RequirementLine
              title={t("settings.skills.missingCommands", { defaultValue: "Missing CLI" })}
              items={missing_bins}
              tone="danger"
              icon={<Terminal className="h-3.5 w-3.5" aria-hidden />}
            />
          ) : null}
          {missing_env.length ? (
            <RequirementLine
              title={t("settings.skills.missingEnvironment", { defaultValue: "Missing ENV" })}
              items={missing_env}
              tone="danger"
              icon={<KeyRound className="h-3.5 w-3.5" aria-hidden />}
            />
          ) : null}
          {bins.length ? (
            <RequirementLine
              title={t("settings.skills.commands", { defaultValue: "Commands" })}
              items={bins}
              icon={<Terminal className="h-3.5 w-3.5" aria-hidden />}
            />
          ) : null}
          {env.length ? (
            <RequirementLine
              title={t("settings.skills.environment", { defaultValue: "Environment variables" })}
              items={env}
              icon={<KeyRound className="h-3.5 w-3.5" aria-hidden />}
            />
          ) : null}
        </div>
      ) : (
        <p className="rounded-2xl border border-border/40 bg-muted/20 px-4 py-3 text-[13px] text-muted-foreground">
          {t("settings.skills.noRequirements", { defaultValue: "No explicit requirements." })}
        </p>
      )}
    </section>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-border/40 bg-muted/30 px-3 py-2.5">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-0.5 truncate text-[13px] font-medium text-foreground">{value}</div>
    </div>
  );
}

function RequirementLine({
  title,
  items,
  icon,
  tone = "muted",
}: {
  title: string;
  items: string[];
  icon: ReactNode;
  tone?: "muted" | "danger";
}) {
  return (
    <div className="space-y-1.5">
      <div
        className={cn(
          "flex items-center gap-1.5 text-[12px]",
          tone === "danger" ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {icon}
        {title}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item) => (
          <Pill key={item}>{item}</Pill>
        ))}
      </div>
    </div>
  );
}

function Pill({
  children,
  tone = "muted",
}: {
  children: ReactNode;
  tone?: "muted" | "success" | "warning";
}) {
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
        tone === "success"
          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : tone === "warning"
            ? "bg-amber-500/10 text-amber-800 dark:text-amber-200"
            : "bg-muted text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}

function skillSourceLabel(source: string, t: TFunction): string {
  if (source === "workspace") {
    return t("settings.skills.sourceWorkspace", { defaultValue: "Custom" });
  }
  if (source === "builtin") {
    return t("settings.skills.sourceBuiltin", { defaultValue: "Built-in" });
  }
  return source;
}

function stripFrontmatter(markdown: string): string {
  const match = markdown.match(/^---\s*\r?\n[\s\S]*?\r?\n---\s*\r?\n?/);
  return match ? markdown.slice(match[0].length).trim() : markdown.trim();
}
