// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { motion, useReducedMotion } from "framer-motion";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import type { UpdateStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

/** What the update is doing now, in the user's words, and how far (null: no measure). */
export function updateStep(status: UpdateStatus, t: ReturnType<typeof useTranslation>["t"]): {
  label: string;
  percent: number | null;
} {
  const percent = Math.max(0, Math.min(100, Math.round(status.progress || 0)));
  if (status.state === "restarting" || status.phase === "restarting") {
    return { label: t("updates.restarting", { defaultValue: "Restarting Navin..." }), percent: null };
  }
  if (status.state === "installing") {
    // The helper reports its copy from 1%; before that (or on an installer
    // that reports nothing) there is no measure, and a bar stuck at 0% reads
    // as frozen.
    if (status.phase === "preparing" && percent > 0) {
      return {
        label: t("updates.installingPercent", { defaultValue: "Installing {{percent}}%", percent }),
        percent,
      };
    }
    return { label: t("updates.installing", { defaultValue: "Installing..." }), percent: null };
  }
  if (status.state === "ready") {
    // Downloaded: stay full until the install starts instead of going back
    // to the "preparing" sweep for a poll or two.
    return { label: t("updates.downloading", { defaultValue: "Downloading {{percent}}%", percent: 100 }), percent: 100 };
  }
  if (status.state === "downloading" && status.totalBytes > 0) {
    return { label: t("updates.downloading", { defaultValue: "Downloading {{percent}}%", percent }), percent };
  }
  return { label: t("updates.preparing", { defaultValue: "Preparing the download..." }), percent: null };
}

/**
 * A thin bar: filled from the reading start to the percent, or a sweep while
 * there is nothing to measure.
 *
 * The fill and the sweep are separate keyed elements. Sharing one element
 * between the two kept the sweep's last inline position when the measure
 * arrived, so the fill grew from wherever the sweep stood (the middle, the
 * left or the right). A new phase that starts lower (download done, install
 * starting) resets at once instead of sliding backwards.
 */
export function ProgressBar({ percent, className }: { percent: number | null; className?: string }) {
  const reduced = useReducedMotion();
  const previous = useRef<number | null>(null);
  const backwards = percent !== null && previous.current !== null && percent < previous.current;
  useEffect(() => {
    previous.current = percent;
  }, [percent]);
  const measured = percent !== null;
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent ?? undefined}
      aria-busy={measured ? undefined : true}
      data-mode={measured ? "measured" : "sweep"}
      className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-foreground/10", className)}
    >
      {measured ? (
        <motion.div
          key="measured"
          data-part="fill"
          className="absolute inset-y-0 start-0 rounded-full bg-[#1d6af0]"
          initial={{ width: "0%" }}
          animate={{ width: `${Math.max(2, percent)}%` }}
          // About one status poll long and linear, so successive readings
          // join into one steady fill rather than a jump and a pause.
          transition={{ duration: reduced || backwards ? 0 : 0.9, ease: "linear" }}
        />
      ) : (
        // Flipped as a whole in right-to-left layouts, so the sweep runs in
        // the reading direction like the fill.
        <div key="sweep" data-part="sweep" className="absolute inset-0 rtl:-scale-x-100">
          <motion.div
            className="absolute inset-y-0 left-0 w-1/3 rounded-full bg-[#1d6af0]"
            initial={{ x: reduced ? "100%" : "-100%", opacity: 0 }}
            animate={reduced ? { x: "100%", opacity: 1 } : { x: ["-100%", "300%"], opacity: 1 }}
            transition={
              reduced
                ? { duration: 0 }
                : {
                    x: { duration: 1.4, ease: "easeInOut", repeat: Infinity },
                    opacity: { duration: 0.2 },
                  }
            }
          />
        </div>
      )}
    </div>
  );
}

export function UpdateProgress({ status }: { status: UpdateStatus | null }) {
  const { t } = useTranslation();
  if (!status || status.state === "error") return null;
  const step = updateStep(status, t);
  return (
    <div className="min-w-[160px] pt-2" aria-live="polite">
      <p className="mb-1.5 text-[12px] tabular-nums text-muted-foreground">{step.label}</p>
      <ProgressBar percent={step.percent} />
    </div>
  );
}
