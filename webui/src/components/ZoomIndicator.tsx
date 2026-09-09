// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { AnimatePresence, motion } from "framer-motion";
import { Minus, Plus, RotateCcw } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { MAX_ZOOM, MIN_ZOOM, zoomLabel } from "@/lib/ui-zoom";
import { cn } from "@/lib/utils";

interface ZoomIndicatorProps {
  zoom: number;
  visible: boolean;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onReset: () => void;
}

/**
 * Transient readout of the interface zoom, with the same controls as the
 * shortcuts: a level changed by Ctrl + wheel is otherwise invisible feedback.
 *
 * Positioned inside the app shell rather than `fixed`: under the CSS zoom
 * fallback, a fixed element resolves its offsets against the unzoomed viewport
 * and the pill drifts off-centre.
 */
export function ZoomIndicator({
  zoom,
  visible,
  onZoomIn,
  onZoomOut,
  onReset,
}: ZoomIndicatorProps) {
  const { t } = useTranslation();
  const atMin = zoom <= MIN_ZOOM + 0.001;
  const atMax = zoom >= MAX_ZOOM - 0.001;

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-6 z-[80] flex justify-center">
      <AnimatePresence>
        {visible ? (
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.96 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
            role="status"
            aria-live="polite"
            aria-label={t("app.zoom.label", { defaultValue: "Interface zoom" })}
            className="pointer-events-auto flex items-center gap-1 rounded-full border border-border/60 bg-background/95 px-2 py-1.5 shadow-lg backdrop-blur"
          >
            <ZoomButton
              label={t("app.zoom.out", { defaultValue: "Zoom out" })}
              disabled={atMin}
              onClick={onZoomOut}
            >
              <Minus className="h-3.5 w-3.5" aria-hidden />
            </ZoomButton>
            <span className="min-w-[3.25rem] text-center text-[13px] font-medium tabular-nums">
              {zoomLabel(zoom)}
            </span>
            <ZoomButton
              label={t("app.zoom.in", { defaultValue: "Zoom in" })}
              disabled={atMax}
              onClick={onZoomIn}
            >
              <Plus className="h-3.5 w-3.5" aria-hidden />
            </ZoomButton>
            <ZoomButton
              label={t("app.zoom.reset", { defaultValue: "Reset zoom (Ctrl+0)" })}
              disabled={Math.abs(zoom - 1) < 0.001}
              onClick={onReset}
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            </ZoomButton>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function ZoomButton({
  children,
  disabled,
  label,
  onClick,
}: {
  children: ReactNode;
  disabled?: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={cn(
        "flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors",
        "hover:bg-muted hover:text-foreground disabled:opacity-40 disabled:hover:bg-transparent",
      )}
    >
      {children}
    </button>
  );
}
