// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo, useState } from "react";
import {
  Accessibility,
  Activity,
  BadgeCheck,
  BookOpen,
  Bug,
  ChevronDown,
  ClipboardCheck,
  Crosshair,
  FileCheck,
  FileLock2,
  Fingerprint,
  FlaskConical,
  Gauge,
  Globe,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  PackageSearch,
  Palette,
  Radar,
  Recycle,
  ScanSearch,
  ScrollText,
  Server,
  ShieldCheck,
  Smartphone,
  Swords,
  Telescope,
  Waypoints,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

type ActionDef = {
  id: string;
  icon: typeof ClipboardCheck;
  /** Builds the message sent to the agent. */
  build: (scope: string, fix: boolean) => string;
};

type ActionGroup = {
  id: string;
  actions: ActionDef[];
};

const FIX_SUFFIX =
  " After reporting, automatically fix the confirmed issues (apply the changes and verify them).";

const PROPOSE_SUFFIX = " Do not change code yet; propose fixes ordered by impact.";

/**
 * Appended to the actions that send a plain prompt instead of a slash command.
 *
 * Slash commands get this from their mission brief on the backend
 * (`_TRACKED_RUN_CLAUSE` in `navin/command/builtin.py`), but these never reach
 * that rewriter - so without it, two buttons sitting next to each other in the
 * same menu would behave differently: one tracked, one silent.
 */
const TRACKED_SUFFIX =
  " Run this as tracked work: put the steps on the board with the `board` tool" +
  " before starting, so the chat shows the plan live. Claim and close each task as" +
  " you reach it rather than all at the end, file each confirmed finding as its own" +
  " task, and finish with a short report of what changed, what you verified, and" +
  " what is left.";

const GROUPS: ActionGroup[] = [
  {
    id: "quality",
    actions: [
      {
        id: "review",
        icon: ClipboardCheck,
        build: (scope, fix) => `/inspect ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "debug",
        icon: Bug,
        build: (scope, fix) => `/debug ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "tests",
        icon: FlaskConical,
        build: (scope, fix) =>
          `Run the test suite for ${scope}. Report failures with their root causes.` +
          (fix
            ? " Fix the failing tests or code until the whole suite passes, then summarize what changed."
            : PROPOSE_SUFFIX),
      },
      {
        id: "quality",
        icon: BadgeCheck,
        build: (scope, fix) =>
          `Run a full quality gate on ${scope}: lint, type checks, tests, quick security scan, ` +
          `and performance smells. Summarize pass/fail per gate with a scoreboard and the top fixes.` +
          (fix ? FIX_SUFFIX : ""),
      },
      {
        id: "mobile",
        icon: Smartphone,
        build: (_scope, fix) =>
          `/mobile android` +
          (fix
            ? " Fix packager/device blockers until the app is healthy, then start the live preview."
            : ""),
      },
    ],
  },
  {
    id: "security",
    actions: [
      {
        id: "security",
        icon: ShieldCheck,
        build: (scope, fix) => `/fortify ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "vulnerabilities",
        icon: Bug,
        build: (scope, fix) => `/probe ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "secrets",
        icon: KeyRound,
        build: (scope, fix) => `/unmask ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "supplyChain",
        icon: PackageSearch,
        build: (scope, fix) => `/lineage ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "sast",
        icon: ScanSearch,
        build: (scope, fix) => `/xray ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "accessControl",
        icon: Fingerprint,
        build: (scope, fix) => `/gatekeeper ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "apiSurface",
        icon: Radar,
        build: (scope, fix) => `/perimeter ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "infra",
        icon: Server,
        build: (scope, fix) => `/bastion ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "dataPrivacy",
        icon: FileLock2,
        build: (scope, fix) => `/vault ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
    ],
  },
  {
    id: "offensive",
    actions: [
      {
        id: "recon",
        icon: Telescope,
        build: (scope) => `/recon ${scope}`,
      },
      {
        id: "threatModel",
        icon: Waypoints,
        build: (scope) => `/threatmap ${scope}`,
      },
      {
        id: "dast",
        icon: Globe,
        build: (scope) => `/dast ${scope}`,
      },
      {
        id: "redTeam",
        icon: Swords,
        build: (scope) => `/redteam ${scope}`,
      },
      {
        id: "pentest",
        icon: Crosshair,
        build: (scope) => `/pentest ${scope}`,
      },
      {
        id: "compliance",
        icon: FileCheck,
        build: (scope) => `/comply ${scope}`,
      },
      {
        id: "report",
        icon: ScrollText,
        build: (scope) => `/report ${scope}`,
      },
    ],
  },
  {
    id: "performance",
    actions: [
      {
        id: "performance",
        icon: Gauge,
        build: (scope, fix) => `/turbo ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
      {
        id: "monitoring",
        icon: Activity,
        build: (scope, fix) => `/pulse ${scope}${fix ? FIX_SUFFIX : ""}`,
      },
    ],
  },
  {
    id: "design",
    actions: [
      {
        id: "uxui",
        icon: LayoutDashboard,
        build: (scope, fix) =>
          `Audit the UX/UI of ${scope}: user flows, navigation clarity, empty/loading/error ` +
          `states, spacing and alignment, responsive behavior, and interaction feedback. ` +
          `Report concrete issues ordered by user impact, each with the file involved.` +
          (fix ? FIX_SUFFIX : PROPOSE_SUFFIX),
      },
      {
        id: "design",
        icon: Palette,
        build: (scope, fix) =>
          `Review the visual design consistency of ${scope}: color palette, typography scale, ` +
          `component variants, borders/radii/shadows, dark mode, and design-token usage. ` +
          `Flag inconsistencies with file references and a unified proposal.` +
          (fix ? FIX_SUFFIX : PROPOSE_SUFFIX),
      },
      {
        id: "accessibility",
        icon: Accessibility,
        build: (scope, fix) =>
          `Run an accessibility audit on ${scope} (WCAG 2.2 AA): color contrast, keyboard ` +
          `navigation, focus management, aria labels/roles, form labels, alt texts, and ` +
          `screen-reader flow. List violations by severity with file references.` +
          (fix ? FIX_SUFFIX : PROPOSE_SUFFIX),
      },
    ],
  },
  {
    id: "maintenance",
    actions: [
      {
        id: "refactor",
        icon: Recycle,
        build: (scope, fix) =>
          `Identify refactoring opportunities in ${scope}: dead code, duplication, oversized ` +
          `functions/components, tangled dependencies, and naming issues. Order by payoff vs risk.` +
          (fix ? FIX_SUFFIX : PROPOSE_SUFFIX),
      },
      {
        id: "docs",
        icon: BookOpen,
        build: (scope, fix) =>
          `Audit the documentation of ${scope}: README accuracy, setup instructions, missing ` +
          `docstrings/comments on complex logic, API documentation, and outdated sections.` +
          (fix
            ? " Then write or update the missing documentation directly."
            : PROPOSE_SUFFIX),
      },
    ],
  },
];

export function DevProjectActions({
  activeFilePath,
  disabled,
  onRun,
  compact,
  asSubmenu = false,
}: {
  activeFilePath: string | null;
  disabled?: boolean;
  onRun: (text: string) => void;
  compact?: boolean;
  /** Nested under the Code "Other" overflow instead of a top-level trigger. */
  asSubmenu?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<"project" | "file">("project");
  const [autoFix, setAutoFix] = useState(false);

  const fileName = useMemo(() => {
    if (!activeFilePath) return null;
    return activeFilePath.replace(/[/\\]+$/, "").split(/[/\\]/).pop() || activeFilePath;
  }, [activeFilePath]);

  const effectiveTarget = target === "file" && activeFilePath ? "file" : "project";
  const scope =
    effectiveTarget === "file" && activeFilePath ? activeFilePath : "the whole project";
  const actionsLabel = t("dev.actions.button", { defaultValue: "Actions" });
  const catalogClass =
    "max-h-[min(34rem,calc(100vh-7rem))] w-[min(20rem,calc(100vw-2rem))] overflow-y-auto rounded-lg border-border bg-background p-1";

  const catalog = (
    <>
        <div className="flex items-center gap-1 px-1 pb-1 pt-0.5">
          <button
            type="button"
            onClick={() => setTarget("project")}
            className={cn(
              "flex-1 rounded-md border px-2 py-1 text-[12px] transition-colors",
              effectiveTarget === "project"
                ? "border-foreground/60 font-semibold text-foreground"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {t("dev.actions.wholeProject", { defaultValue: "Whole project" })}
          </button>
          <button
            type="button"
            disabled={!activeFilePath}
            onClick={() => setTarget("file")}
            title={activeFilePath ?? undefined}
            className={cn(
              "flex-1 truncate rounded-md border px-2 py-1 text-[12px] transition-colors",
              effectiveTarget === "file"
                ? "border-foreground/60 font-semibold text-foreground"
                : "border-border text-muted-foreground hover:text-foreground",
              "disabled:pointer-events-none disabled:opacity-45",
            )}
          >
            {fileName
              ? t("dev.actions.activeFile", {
                  defaultValue: "File: {{name}}",
                  name: fileName,
                })
              : t("dev.actions.noFile", { defaultValue: "No file open" })}
          </button>
        </div>
        {GROUPS.map((group, groupIndex) => (
          <div key={group.id}>
            {groupIndex > 0 ? <DropdownMenuSeparator className="my-1" /> : null}
            <DropdownMenuLabel className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">
              {t(`dev.actions.groups.${group.id}`)}
            </DropdownMenuLabel>
            {group.actions.map((action) => {
              const Icon = action.icon;
              return (
                <DropdownMenuItem
                  key={action.id}
                  onSelect={() => {
                    const text = action.build(scope, autoFix);
                    onRun(text.startsWith("/") ? text : text + TRACKED_SUFFIX);
                    setOpen(false);
                  }}
                  className="flex cursor-default items-center gap-2.5 rounded-md px-2 py-1.5"
                >
                  <Icon className="h-4 w-4 shrink-0 text-foreground/70" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12.5px] font-medium text-foreground">
                      {t(`dev.actions.items.${action.id}.label`)}
                    </span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {t(`dev.actions.items.${action.id}.description`)}
                    </span>
                  </span>
                </DropdownMenuItem>
              );
            })}
          </div>
        ))}
        <DropdownMenuSeparator className="my-1" />
        <label className="flex w-full cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors hover:bg-muted/50">
          <input
            type="checkbox"
            checked={autoFix}
            onChange={(event) => setAutoFix(event.target.checked)}
            className="h-3.5 w-3.5 accent-foreground"
          />
          <span className="min-w-0 flex-1">
            <span className="block text-[12.5px] font-medium text-foreground">
              {t("dev.actions.autoFix", { defaultValue: "Auto-fix" })}
            </span>
            <span className="block text-[11px] text-muted-foreground">
              {t("dev.actions.autoFixDescription", {
                defaultValue: "Also apply fixes for confirmed issues",
              })}
            </span>
          </span>
        </label>
    </>
  );

  if (asSubmenu) {
    return (
      <DropdownMenuSub>
        <DropdownMenuSubTrigger disabled={disabled} data-testid="dev-other-actions">
          <ListChecks className="h-4 w-4 shrink-0" aria-hidden />
          {actionsLabel}
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent className={catalogClass}>{catalog}</DropdownMenuSubContent>
      </DropdownMenuSub>
    );
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "flex min-w-0 items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors",
            open
              ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
              : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            "disabled:pointer-events-none disabled:opacity-55",
          )}
          title={t("dev.actions.title", { defaultValue: "Project actions" })}
        >
          <ListChecks className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {compact ? null : <span className="truncate">{actionsLabel}</span>}
          <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="bottom"
        sideOffset={8}
        className={catalogClass}
      >
        {catalog}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
