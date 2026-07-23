import { useMemo, useState } from "react";
import {
  Accessibility,
  Activity,
  BadgeCheck,
  BookOpen,
  Bug,
  ChevronDown,
  ClipboardCheck,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  ListChecks,
  Palette,
  Recycle,
  ShieldCheck,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
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
}: {
  activeFilePath: string | null;
  disabled?: boolean;
  onRun: (text: string) => void;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<"project" | "file">("project");
  const [autoFix, setAutoFix] = useState(false);

  const fileName = useMemo(() => {
    if (!activeFilePath) return null;
    return activeFilePath.replace(/\/+$/, "").split("/").pop() || activeFilePath;
  }, [activeFilePath]);

  const effectiveTarget = target === "file" && activeFilePath ? "file" : "project";
  const scope =
    effectiveTarget === "file" && activeFilePath ? activeFilePath : "the whole project";

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors",
            open
              ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
              : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            "disabled:pointer-events-none disabled:opacity-55",
          )}
          title={t("dev.actions.title", { defaultValue: "Project actions" })}
        >
          <ListChecks className="h-3.5 w-3.5" aria-hidden />
          {compact ? null : t("dev.actions.button", { defaultValue: "Actions" })}
          <ChevronDown className="h-3 w-3" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="bottom"
        sideOffset={8}
        className="max-h-[min(34rem,calc(100vh-7rem))] w-[min(20rem,calc(100vw-2rem))] overflow-y-auto rounded-lg border-border bg-background p-1"
      >
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
                    onRun(action.build(scope, autoFix));
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
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
