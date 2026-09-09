// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Reference pane for the Notes module: every slash command with its
 * description, plus the editor tricks that are otherwise easy to miss
 * (wikilinks, drag & drop, shortcuts). Pure documentation, always in sync
 * with the real catalog because it renders SLASH_COMMANDS directly.
 */

import {
  Bot,
  CheckSquare,
  Code2,
  FileUp,
  Heading1,
  Heading2,
  Heading3,
  ImagePlus,
  Keyboard,
  Link2,
  List,
  ListOrdered,
  Minus,
  MousePointerClick,
  Pilcrow,
  Quote,
  Sparkles,
  Table2,
} from "lucide-react";
import { motion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { SLASH_COMMANDS, type SlashCommandId } from "./slash-commands";

const ICONS: Record<SlashCommandId, typeof Heading1> = {
  title: Heading1,
  h1: Heading1,
  h2: Heading2,
  h3: Heading3,
  text: Pilcrow,
  todo: CheckSquare,
  bullet: List,
  numbered: ListOrdered,
  quote: Quote,
  code: Code2,
  table: Table2,
  divider: Minus,
  link: Link2,
  image: ImagePlus,
  file: FileUp,
  agent: Bot,
};

export function GuidePane() {
  const { t } = useTranslation();

  const tips: Array<{ icon: typeof Link2; label: string; hint: string }> = [
    {
      icon: Link2,
      label: t("notes.guide.wikilinks", { defaultValue: "[[Wikilinks]]" }),
      hint: t("notes.guide.wikilinksHint", {
        defaultValue:
          "Type [[note title]] to link another note; backlinks appear automatically.",
      }),
    },
    {
      icon: MousePointerClick,
      label: t("notes.guide.dragDrop", { defaultValue: "Drag & drop / paste" }),
      hint: t("notes.guide.dragDropHint", {
        defaultValue:
          "Drop a file into the note or paste an image (Ctrl+V): it uploads and embeds itself.",
      }),
    },
    {
      icon: Sparkles,
      label: t("notes.guide.askNavin", { defaultValue: "Ask Navin" }),
      hint: t("notes.guide.askNavinHint", {
        defaultValue:
          "From a note, ask Navin to summarize, translate, extract tasks or run an /agent block.",
      }),
    },
    {
      icon: Keyboard,
      label: t("notes.guide.shortcuts", { defaultValue: "Shortcuts" }),
      hint: t("notes.guide.shortcutsHint", {
        defaultValue: "Ctrl+K search all notes · Ctrl+S save now · Esc close menus.",
      }),
    },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-5">
      <p className="mb-4 text-[12.5px] text-muted-foreground">
        {t("notes.guide.intro", {
          defaultValue:
            "Type / at the start of a line in a note to insert one of these blocks.",
        })}
      </p>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-2">
        {SLASH_COMMANDS.map((item, index) => {
          const Icon = ICONS[item.id];
          return (
            <motion.div
              key={item.id}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.18, delay: Math.min(index * 0.02, 0.3) }}
              className="flex items-start gap-2.5 rounded-xl border border-border/60 p-3"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border/60 bg-muted/30">
                <Icon className="h-4 w-4" aria-hidden />
              </span>
              <span className="min-w-0">
                <span className="flex items-baseline gap-1.5">
                  <span className="text-[13px] font-medium">
                    {t(item.labelKey, { defaultValue: item.defaultLabel })}
                  </span>
                  <code className="rounded bg-muted/70 px-1 py-0.5 font-mono text-[10.5px] text-muted-foreground">
                    /{item.id}
                  </code>
                </span>
                <span className="mt-0.5 block text-[11.5px] leading-snug text-muted-foreground">
                  {t(`${item.labelKey}Hint`, { defaultValue: item.defaultHint })}
                </span>
              </span>
            </motion.div>
          );
        })}
      </div>

      <p className="mb-2 mt-6 text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t("notes.guide.tipsTitle", { defaultValue: "Tips" })}
      </p>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-2">
        {tips.map((tip) => (
          <div
            key={tip.label}
            className="flex items-start gap-2.5 rounded-xl border border-border/60 bg-muted/15 p-3"
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border/60 bg-background">
              <tip.icon className="h-4 w-4" aria-hidden />
            </span>
            <span className="min-w-0">
              <span className="block text-[13px] font-medium">{tip.label}</span>
              <span className="mt-0.5 block text-[11.5px] leading-snug text-muted-foreground">
                {tip.hint}
              </span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
