// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Check, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ProgressBar, updateStep } from "@/components/UpdateProgress";
import type { UpdateStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

type StepState = "done" | "active" | "todo";

/**
 * From "Install" to the new version's window: what is happening and how far.
 * Navin closes for a few seconds at the end and reopens by itself; saying so
 * keeps people from reopening it by hand in the middle of the swap.
 */
export function UpdateOverlay({ status }: { status: UpdateStatus | null }) {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const shown = Boolean(status && (status.state === "installing" || status.state === "restarting"));
  const version = status?.latestVersion ?? status?.update?.latestVersion ?? "";
  const restarting = status?.state === "restarting" || status?.phase === "restarting";
  const steps: { label: string; state: StepState }[] = [
    { label: t("updates.overlay.download", { defaultValue: "Download" }), state: "done" },
    {
      label: t("updates.overlay.prepare", { defaultValue: "Installing the new version" }),
      state: restarting ? "done" : "active",
    },
    {
      label: t("updates.overlay.restart", { defaultValue: "Restarting Navin" }),
      state: restarting ? "active" : "todo",
    },
  ];
  const step = status ? updateStep(status, t) : null;

  return (
    <AnimatePresence>
      {shown && status && step ? (
        <motion.div
          key="update-overlay"
          role="dialog"
          aria-modal="true"
          aria-labelledby="update-overlay-title"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: reduced ? 0 : 0.2 }}
          className="fixed inset-0 z-[200] grid place-items-center bg-background/85 p-4 backdrop-blur-md"
        >
          <motion.div
            initial={reduced ? false : { opacity: 0, y: 10, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ type: "spring", duration: 0.4, bounce: 0 }}
            className="w-full max-w-sm rounded-2xl border border-border bg-card p-6 shadow-xl"
          >
            <div className="flex items-center gap-3">
              <img src="/logo/navin-mark-192.png" alt="" className="h-10 w-10 rounded-xl" draggable={false} />
              <div className="min-w-0">
                <h2 id="update-overlay-title" className="text-base font-semibold">
                  {version
                    ? t("updates.overlay.title", { defaultValue: "Updating to {{version}}", version })
                    : t("updates.overlay.titlePlain", { defaultValue: "Updating Navin" })}
                </h2>
                <p className="text-[12.5px] tabular-nums text-muted-foreground">{step.label}</p>
              </div>
            </div>

            <ProgressBar percent={restarting ? null : step.percent} className="mt-5" />

            <ol className="mt-5 space-y-2.5">
              {steps.map((item) => (
                <li key={item.label} className="flex items-center gap-2.5 text-[13px]">
                  <span
                    className={cn(
                      "grid h-5 w-5 shrink-0 place-items-center rounded-full border",
                      item.state === "done" && "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
                      item.state === "active" && "border-[#1d6af0]/50 text-[#1d6af0]",
                      item.state === "todo" && "border-border text-muted-foreground",
                    )}
                    aria-hidden
                  >
                    {item.state === "done" ? (
                      <Check className="h-3 w-3" />
                    ) : item.state === "active" ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : null}
                  </span>
                  <span className={cn(item.state === "todo" ? "text-muted-foreground" : "text-foreground")}>{item.label}</span>
                </li>
              ))}
            </ol>

            <p className="mt-5 rounded-lg bg-muted/60 px-3 py-2 text-[12.5px] leading-relaxed text-muted-foreground">
              {restarting
                ? t("updates.overlay.reopening", {
                    defaultValue: "Navin closes for a few seconds and reopens by itself. No need to open it again.",
                  })
                : t("updates.overlay.keepOpen", {
                    defaultValue: "Navin restarts by itself as soon as the new version is ready.",
                  })}
            </p>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
