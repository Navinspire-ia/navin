// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { type Dispatch, type PointerEvent as ReactPointerEvent, type SetStateAction } from "react";
import { useReducedMotion } from "framer-motion";
import {
  BadgeDollarSign,
  Briefcase,
  ChevronDown,
  ChevronUp,
  Clapperboard,
  FileText,
  Globe2,
  GripVertical,
  Landmark,
  Megaphone,
  Mic,
  NotebookPen,
  RotateCcw,
  ShieldAlert,
  Target,
  TrendingUp,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ToggleButton } from "@/components/settings/ToggleButton";
import { useStudioModuleDrag } from "@/hooks/useStudioModuleDrag";
import type { LocalPreferences } from "@/lib/local-preferences";
import {
  STUDIO_MODULE_IDS,
  STUDIO_MODULE_LABEL_KEYS,
  moveStudioModule,
  studioLayoutIsDefault,
  type StudioModuleId,
  toggleStudioHidden,
} from "@/lib/studio-modules";
import { cn } from "@/lib/utils";

const STUDIO_ICONS: Record<StudioModuleId, LucideIcon> = {
  tenders: Landmark,
  career: Briefcase,
  leads: Target,
  marketing: Megaphone,
  trading: Wallet,
  ads: BadgeDollarSign,
  seo: TrendingUp,
  scraping: Globe2,
  montage: Clapperboard,
  notes: NotebookPen,
  meeting: Mic,
  crm: Briefcase,
  content: FileText,
  risklens: ShieldAlert,
};

export function StudioSettings({
  localPrefs,
  onChangeLocalPrefs,
}: {
  localPrefs: LocalPreferences;
  onChangeLocalPrefs: Dispatch<SetStateAction<LocalPreferences>>;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, string | number>) =>
    t(key, { defaultValue: fallback, ...values });
  const reduceMotion = useReducedMotion();
  const hidden = new Set(localPrefs.studioHidden);
  const visibleCount = localPrefs.studioOrder.filter((id) => !hidden.has(id)).length;
  const isDefault = studioLayoutIsDefault(localPrefs.studioOrder, localPrefs.studioHidden);

  const setOrder = (studioOrder: StudioModuleId[]) => {
    onChangeLocalPrefs((prev) => ({ ...prev, studioOrder }));
  };

  const { listRef, draggingId, overId, startDrag, finishDrag } = useStudioModuleDrag({
    order: localPrefs.studioOrder,
    onReorder: setOrder,
  });

  const reset = () => {
    onChangeLocalPrefs((prev) => ({
      ...prev,
      studioOrder: [...STUDIO_MODULE_IDS],
      studioHidden: [],
    }));
  };

  return (
    <div className="space-y-7">
      <section>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3 px-1">
          <div className="min-w-0">
            <h2 className="text-balance text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
              {tx("settings.sections.studioModules", "Studio modules")}
            </h2>
            <p className="mt-1 max-w-[40rem] text-pretty text-[12px] leading-5 text-muted-foreground">
              {tx(
                "settings.studio.hint",
                "Press a row and slide it to reorder the Studio sidebar. Turn a module off to hide it. Hidden modules stay here so you can turn them back on.",
              )}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <p className="tabular-nums text-[12px] text-muted-foreground">
              {tx("settings.studio.visibleCount", "{{visible}} visible / {{total}}", {
                visible: visibleCount,
                total: STUDIO_MODULE_IDS.length,
              })}
            </p>
            <button
              type="button"
              onClick={reset}
              disabled={isDefault}
              className={cn(
                "inline-flex h-9 min-w-9 items-center gap-1.5 rounded-full px-3 text-[12px] font-medium outline-none transition-transform duration-200 focus-visible:ring-1 focus-visible:ring-ring/50",
                isDefault
                  ? "cursor-default text-muted-foreground/50"
                  : "text-foreground hover:bg-muted/70 active:scale-[0.96]",
              )}
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden />
              {tx("settings.studio.reset", "Reset order")}
            </button>
          </div>
        </div>

        <ul
          ref={(node) => {
            listRef.current = node;
          }}
          data-testid="studio-module-list"
          className="host-no-drag select-none overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.075)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.24)]"
        >
          {localPrefs.studioOrder.map((id, index) => (
            <StudioModuleRow
              key={id}
              id={id}
              index={index}
              total={localPrefs.studioOrder.length}
              hidden={hidden.has(id)}
              label={t(STUDIO_MODULE_LABEL_KEYS[id])}
              dragging={draggingId === id}
              over={overId === id && draggingId !== id}
              reduceMotion={Boolean(reduceMotion)}
              onPointerDown={(event) => startDrag(id, event)}
              onPointerUp={(event) => finishDrag(event.clientY)}
              onMove={(delta) => setOrder(moveStudioModule(localPrefs.studioOrder, id, delta))}
              onToggle={(show) =>
                onChangeLocalPrefs((prev) => ({
                  ...prev,
                  studioHidden: toggleStudioHidden(prev.studioHidden, id, !show),
                }))
              }
            />
          ))}
        </ul>
      </section>
    </div>
  );
}

function StudioModuleRow({
  id,
  index,
  total,
  hidden,
  label,
  dragging,
  over,
  reduceMotion,
  onPointerDown,
  onPointerUp,
  onMove,
  onToggle,
}: {
  id: StudioModuleId;
  index: number;
  total: number;
  hidden: boolean;
  label: string;
  dragging: boolean;
  over: boolean;
  reduceMotion: boolean;
  onPointerDown: (event: ReactPointerEvent<HTMLLIElement>) => void;
  onPointerUp: (event: ReactPointerEvent<HTMLLIElement>) => void;
  onMove: (delta: -1 | 1) => void;
  onToggle: (show: boolean) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const Icon = STUDIO_ICONS[id];

  return (
    <li
      data-studio-module={id}
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      aria-grabbed={dragging}
      className={cn(
        "host-no-drag touch-none border-b border-border/45 last:border-b-0",
        over && "bg-sidebar-accent/70",
        dragging && "opacity-45",
        !reduceMotion && "transition-colors duration-200",
      )}
    >
      <div
        className={cn(
          "flex min-h-11 cursor-grab items-center gap-2 px-3 py-2 active:cursor-grabbing sm:px-4",
          hidden && "opacity-55",
        )}
      >
        <span
          aria-hidden
          className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-muted-foreground"
          title={tx("settings.studio.drag", "Press and slide the row to reorder")}
        >
          <GripVertical className="h-4 w-4" aria-hidden />
        </span>
        <Icon className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[14px] font-medium leading-5 text-foreground">{label}</p>
          <p className="text-[12px] leading-4 text-muted-foreground">
            {hidden
              ? tx("settings.studio.hidden", "Hidden in the sidebar")
              : tx("settings.studio.visible", "Shown in the sidebar")}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            aria-label={tx("settings.studio.moveUp", "Move up")}
            disabled={index === 0}
            onClick={() => onMove(-1)}
            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-muted-foreground outline-none transition-transform duration-200 hover:bg-muted/60 hover:text-foreground focus-visible:ring-1 focus-visible:ring-ring/50 active:scale-[0.96] disabled:cursor-default disabled:opacity-35 disabled:hover:bg-transparent"
          >
            <ChevronUp className="h-4 w-4" aria-hidden />
          </button>
          <button
            type="button"
            aria-label={tx("settings.studio.moveDown", "Move down")}
            disabled={index === total - 1}
            onClick={() => onMove(1)}
            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-muted-foreground outline-none transition-transform duration-200 hover:bg-muted/60 hover:text-foreground focus-visible:ring-1 focus-visible:ring-ring/50 active:scale-[0.96] disabled:cursor-default disabled:opacity-35 disabled:hover:bg-transparent"
          >
            <ChevronDown className="h-4 w-4" aria-hidden />
          </button>
        </div>
        <ToggleButton
          checked={!hidden}
          onChange={onToggle}
          ariaLabel={
            hidden
              ? tx("settings.studio.show", "Show in sidebar")
              : tx("settings.studio.hide", "Hide from sidebar")
          }
          label={hidden ? tx("settings.values.off", "Off") : tx("settings.values.on", "On")}
        />
      </div>
    </li>
  );
}
