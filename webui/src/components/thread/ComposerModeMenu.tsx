// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Bug,
  Check,
  ChevronDown,
  Clapperboard,
  Hammer,
  Map as MapIcon,
  MessageCircle,
  SearchCode,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { commitOnPointerDown, commitOnSelect } from "@/components/thread/menuChoice";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/**
 * Composer turn mode.
 *
 * Ask is read-only Q&A. Plan / Agent are the Cursor-style design → build pair.
 * Review / Security / Debug prefix free text with real workflow slash commands
 * so the agent runs the matching brief + tools and closes with a report.
 */
export type ComposerTurnMode =
  | "ask"
  | "plan"
  | "agent"
  | "review"
  | "security"
  | "debug"
  | "montage";

export type ComposerModeAccent =
  | "ask"
  | "plan"
  | "review"
  | "security"
  | "debug"
  | "montage";

export const COMPOSER_TURN_MODE_STORAGE_KEY = "navin.composer.turnMode";

const VALID_MODES: ReadonlySet<ComposerTurnMode> = new Set([
  "ask",
  "plan",
  "agent",
  "review",
  "security",
  "debug",
  "montage",
]);

/** Free-text prefix applied when the user did not type a slash command. */
const MODE_SLASH: Record<ComposerTurnMode, string> = {
  ask: "/ask",
  agent: "/forge",
  plan: "/blueprint",
  review: "/inspect",
  security: "/fortify",
  debug: "/debug",
  montage: "/montage",
};

const MODE_FROM_SLASH: Record<string, ComposerTurnMode> = {
  "/ask": "ask",
  "/forge": "agent",
  "/mobile": "agent",
  "/blueprint": "plan",
  "/board": "plan",
  "/inspect": "review",
  "/turbo": "review",
  "/fortify": "security",
  "/probe": "security",
  "/unmask": "security",
  "/lineage": "security",
  "/xray": "security",
  "/gatekeeper": "security",
  "/perimeter": "security",
  "/bastion": "security",
  "/vault": "security",
  "/recon": "security",
  "/threatmap": "security",
  "/dast": "security",
  "/redteam": "security",
  "/pentest": "security",
  "/comply": "security",
  "/debug": "debug",
  "/montage": "montage",
};

export function isComposerTurnMode(value: unknown): value is ComposerTurnMode {
  return typeof value === "string" && VALID_MODES.has(value as ComposerTurnMode);
}

export function readComposerTurnMode(): ComposerTurnMode {
  try {
    const raw = window.localStorage.getItem(COMPOSER_TURN_MODE_STORAGE_KEY);
    return isComposerTurnMode(raw) ? raw : "agent";
  } catch {
    return "agent";
  }
}

export function writeComposerTurnMode(mode: ComposerTurnMode) {
  try {
    window.localStorage.setItem(COMPOSER_TURN_MODE_STORAGE_KEY, mode);
  } catch {
    /* ignore */
  }
}

/**
 * Infer a mode switch from an explicit slash command the user (or Build) sent.
 * Free text does not change the mode.
 */
export function modeFromOutgoingContent(
  content: string,
): ComposerTurnMode | null {
  const head = content.trim().split(/\s/, 1)[0]?.toLowerCase() ?? "";
  return MODE_FROM_SLASH[head] ?? null;
}

/**
 * True for workflow slashes the composer injects for mode routing (/forge,
 * /blueprint, …). Message bubbles hide these so a free-text prompt sent in
 * Agent mode does not show the internal "/forge" plumbing to the user.
 */
export function isModeRoutingSlash(command: string): boolean {
  return command.toLowerCase() in MODE_FROM_SLASH;
}

/**
 * Strip a leading mode-routing slash from an outgoing/user message for
 * display. Returns the prompt text without the command, or null when the
 * content does not start with a routing slash (or has no text after it,
 * so a bare "/ask" keeps its command pill).
 */
export function stripModeRoutingSlash(content: string): string | null {
  const trimmed = content.trim();
  const first = trimmed.split(/\s/, 1)[0] ?? "";
  if (!isModeRoutingSlash(first)) return null;
  const remainder = trimmed.slice(first.length).trimStart();
  return remainder.length > 0 ? remainder : null;
}

/**
 * One-shot work that must start now, even if the composer is in Plan.
 * Build stays for multi-file systems, migrations, and "make a plan" asks.
 */
const PLAN_ONLY_RE =
  /\b(fais(?:[- ]moi)? un plan|fait un plan|planifie(?:r)?|make a plan|write a plan|propose (?:un |an )?plan|architecture)\b/i;
const COMPLEX_PROJECT_RE =
  /\b(migrat|refactor|rebuild|from scratch|from zero|toute l['’ ]app|end[- ]to[- ]end|multi[- ]module|rearchitecture|microservices?)\b/i;
const SIMPLE_DELIVERABLE_RE =
  /\b(pptx?|slides?|deck|pitch|powerpoint|docx?|m[eé]mo(?:ire)?s?|word|xlsx?|excel|pdf|csv|image|logo|affiche|one[- ]pagers?|script|readme|lettres?|emails?|mails?)\b/i;
const SMALL_EDIT_RE =
  /\b(typos?|rename|renomm|corrige [cç]a|fix this|juste (?:un|une|ce|cette))\b/i;

export function isSimpleStartNowPrompt(content: string): boolean {
  const text = content.trim();
  if (!text || text.startsWith("/")) return false;
  if (PLAN_ONLY_RE.test(text) || COMPLEX_PROJECT_RE.test(text)) return false;
  return SIMPLE_DELIVERABLE_RE.test(text) || SMALL_EDIT_RE.test(text);
}

/** Prefix free-text turns with the workflow slash for the active mode. */
export function applyComposerTurnMode(
  content: string,
  mode: ComposerTurnMode,
  options?: { startNow?: boolean },
): string {
  const trimmed = content.trim();
  if (!trimmed) return trimmed;
  // Already a slash command (user picked /forge, /inspect, …) - leave alone.
  if (trimmed.startsWith("/")) return trimmed;
  // Simple one-shots (deck, memo, one file, typo) must not sit on Build.
  // Plan mode would inject /blueprint and wait. Route them through /forge.
  const startNow = Boolean(options?.startNow) || isSimpleStartNowPrompt(trimmed);
  const effective = startNow && mode === "plan" ? "agent" : mode;
  // Agent uses /forge so fullstack-dev + ui-ux-pro-max + framer-motion load
  // as Active Skills (plain Agent text only listed them in the catalog).
  const slash = MODE_SLASH[effective];
  return `${slash} ${trimmed}`;
}

export function composerModeAccent(
  mode: ComposerTurnMode,
): ComposerModeAccent | null {
  if (mode === "ask") return "ask";
  if (mode === "plan") return "plan";
  if (mode === "review") return "review";
  if (mode === "security") return "security";
  if (mode === "debug") return "debug";
  if (mode === "montage") return "montage";
  return null;
}

type ModeMeta = {
  mode: ComposerTurnMode;
  icon: LucideIcon;
  accent: ComposerModeAccent | null;
  labelKey: string;
  labelDefault: string;
};

const MODE_ITEMS: ModeMeta[] = [
  {
    mode: "ask",
    icon: MessageCircle,
    accent: "ask",
    labelKey: "thread.composer.mode.ask",
    labelDefault: "Ask",
  },
  {
    mode: "agent",
    icon: Hammer,
    accent: null,
    labelKey: "thread.composer.mode.agent",
    labelDefault: "Agent",
  },
  {
    mode: "plan",
    icon: MapIcon,
    accent: "plan",
    labelKey: "thread.composer.mode.plan",
    labelDefault: "Plan",
  },
  {
    mode: "review",
    icon: SearchCode,
    accent: "review",
    labelKey: "thread.composer.mode.review",
    labelDefault: "Review",
  },
  {
    mode: "security",
    icon: ShieldCheck,
    accent: "security",
    labelKey: "thread.composer.mode.security",
    labelDefault: "Security",
  },
  {
    mode: "debug",
    icon: Bug,
    accent: "debug",
    labelKey: "thread.composer.mode.debug",
    labelDefault: "Debug",
  },
  {
    mode: "montage",
    icon: Clapperboard,
    accent: "montage",
    labelKey: "thread.composer.mode.montage",
    labelDefault: "Montage",
  },
];

/**
 * Compact mode switcher next to the composer (names only, Cursor-style).
 * Investigate modes tint the trigger and shell; Agent stays neutral.
 */
export function ComposerModeMenu({
  mode,
  disabled,
  isHero,
  compact = false,
  onChange,
}: {
  mode: ComposerTurnMode;
  disabled?: boolean;
  isHero: boolean;
  /** Narrow side-panel chat: denser control sizing. */
  compact?: boolean;
  onChange: (mode: ComposerTurnMode) => void;
}) {
  const { t } = useTranslation();
  const current = MODE_ITEMS.find((item) => item.mode === mode) ?? MODE_ITEMS[0];
  const ModeIcon = current.icon;
  const accent = current.accent;
  const modeLabel = t(current.labelKey, { defaultValue: current.labelDefault });

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <Button
          type="button"
          variant="ghost"
          title={modeLabel}
          aria-label={t("thread.composer.mode.aria", {
            defaultValue: "Turn mode",
          })}
          className={cn(
            "max-w-[8.5rem] rounded-lg border font-medium shadow-none transition-colors active:scale-[0.96]",
            compact || isHero
              ? "h-7 gap-1 px-2 text-[12px]"
              : "h-8 gap-1 px-2.5 text-[12.5px]",
            accent
              ? cn(
                  "border-[hsl(var(--composer-mode)/0.4)] bg-[hsl(var(--composer-mode-soft))] text-[hsl(var(--composer-mode-fg))] hover:bg-[hsl(var(--composer-mode)/0.14)]",
                  `composer-mode-${accent}`,
                )
              : "border-transparent bg-transparent text-muted-foreground hover:bg-foreground/[0.05] hover:text-foreground dark:hover:bg-white/[0.06]",
          )}
        >
          <ModeIcon
            className={cn(
              "h-3.5 w-3.5 shrink-0",
              accent && "text-[hsl(var(--composer-mode-fg))]",
            )}
            aria-hidden
          />
          <span className="truncate">{modeLabel}</span>
          <ChevronDown className="h-3 w-3 shrink-0 opacity-70" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        sideOffset={6}
        className="min-w-[8.5rem] p-1"
      >
        {MODE_ITEMS.slice(0, 2).map((item) => (
          <ModeItem
            key={item.mode}
            icon={<item.icon className="h-3.5 w-3.5" />}
            label={t(item.labelKey, { defaultValue: item.labelDefault })}
            selected={mode === item.mode}
            accent={item.accent}
            onSelect={() => onChange(item.mode)}
          />
        ))}
        <DropdownMenuSeparator className="my-1" />
        {MODE_ITEMS.slice(2).map((item) => (
          <ModeItem
            key={item.mode}
            icon={<item.icon className="h-3.5 w-3.5" />}
            label={t(item.labelKey, { defaultValue: item.labelDefault })}
            selected={mode === item.mode}
            accent={item.accent}
            onSelect={() => onChange(item.mode)}
          />
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ModeItem({
  icon,
  label,
  selected,
  accent,
  onSelect,
}: {
  icon: React.ReactNode;
  label: string;
  selected: boolean;
  accent?: ComposerModeAccent | null;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      onPointerDown={(event) => {
        commitOnPointerDown(event, onSelect);
      }}
      onSelect={() => {
        commitOnSelect(onSelect);
      }}
      className={cn(
        "flex h-8 cursor-pointer items-center gap-2 rounded-md px-2 py-0 text-[13px]",
        accent && `composer-mode-${accent}`,
        selected && accent && "bg-[hsl(var(--composer-mode-soft))]",
        selected && !accent && "bg-foreground/[0.06]",
      )}
    >
      <span
        className={cn(
          "grid h-4 w-4 shrink-0 place-items-center",
          accent ? "text-[hsl(var(--composer-mode-fg))]" : "text-muted-foreground",
        )}
        aria-hidden
      >
        {icon}
      </span>
      <span
        className={cn(
          "min-w-0 flex-1 font-medium",
          accent && "text-[hsl(var(--composer-mode-fg))]",
        )}
      >
        {label}
      </span>
      {selected ? (
        <Check
          className={cn(
            "h-3.5 w-3.5 shrink-0 opacity-80",
            accent && "text-[hsl(var(--composer-mode-fg))]",
          )}
          aria-hidden
        />
      ) : (
        <span className="h-3.5 w-3.5 shrink-0" aria-hidden />
      )}
    </DropdownMenuItem>
  );
}
