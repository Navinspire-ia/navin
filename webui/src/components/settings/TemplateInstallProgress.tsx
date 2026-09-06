import { useEffect, useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Check, Download, FolderCog, Play, Puzzle } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

type StepId = "download" | "install" | "config" | "start";

const CREATE_STEPS: StepId[] = ["download", "install", "config", "start"];
const INSTALL_STEPS: StepId[] = ["install", "config", "start"];

const STEP_ICONS = {
  download: Download,
  install: Puzzle,
  config: FolderCog,
  start: Play,
} as const;

const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };

function stepFromPercent(percent: number, steps: StepId[]): number {
  if (percent >= 100) return steps.length;
  const span = 100 / steps.length;
  return Math.min(steps.length - 1, Math.floor(percent / span));
}

function DownloadGraph({
  active,
  reduceMotion,
}: {
  active: boolean;
  reduceMotion: boolean | null;
}) {
  const bars = useMemo(() => {
    return Array.from({ length: 22 }, (_, index) => {
      const wave = 0.28 + Math.sin(index * 0.55) * 0.22 + ((index * 7) % 5) * 0.07;
      return Math.min(1, Math.max(0.16, wave));
    });
  }, []);

  return (
    <div
      className="flex h-14 items-end gap-[3px] rounded-xl bg-muted/40 px-2.5 py-2"
      aria-hidden
    >
      {bars.map((base, index) => (
        <motion.span
          key={index}
          className="block h-full w-full origin-bottom rounded-[2px] bg-primary/80"
          initial={{ scaleY: reduceMotion ? base : 0.12 }}
          animate={
            reduceMotion
              ? { scaleY: base }
              : active
                ? {
                    scaleY: [base * 0.45, Math.min(1, base * 1.15), base * 0.7],
                  }
                : { scaleY: base * 0.35 }
          }
          transition={
            reduceMotion
              ? { duration: 0 }
              : {
                  duration: 1.1,
                  repeat: active ? Infinity : 0,
                  repeatType: "mirror",
                  delay: index * 0.04,
                  ease: "easeInOut",
                }
          }
          style={{ height: "100%" }}
        />
      ))}
    </div>
  );
}

export function TemplateInstallProgress({
  mode,
  running,
  finished,
  failed,
  onSettled,
}: {
  mode: "install" | "create";
  running: boolean;
  finished: boolean;
  failed: boolean;
  onSettled?: () => void;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const steps = mode === "create" ? CREATE_STEPS : INSTALL_STEPS;
  const [percent, setPercent] = useState(0);

  useEffect(() => {
    if (!running && !finished) {
      setPercent(0);
      return;
    }
    if (finished) {
      setPercent(100);
      return;
    }
    if (failed) return;
    const started = Date.now();
    const ceiling = 92;
    const duration = mode === "create" ? 14000 : 7000;
    const tick = window.setInterval(() => {
      const elapsed = Date.now() - started;
      const ratio = Math.min(1, elapsed / duration);
      const eased = 1 - (1 - ratio) * (1 - ratio);
      setPercent(Math.round(eased * ceiling));
    }, 80);
    return () => window.clearInterval(tick);
  }, [failed, finished, mode, running]);

  useEffect(() => {
    if (!finished || percent < 100) return;
    const wait = window.setTimeout(() => onSettled?.(), reduceMotion ? 200 : 700);
    return () => window.clearTimeout(wait);
  }, [finished, onSettled, percent, reduceMotion]);

  const current = failed
    ? Math.min(steps.length - 1, stepFromPercent(percent, steps))
    : stepFromPercent(percent, steps);
  const labels: Record<StepId, { title: string; detail: string }> = {
    download: {
      title: t("settings.templates.stepDownload", { defaultValue: "Download" }),
      detail: t("settings.templates.stepDownloadDetail", {
        defaultValue: "Fetching the S3 package",
      }),
    },
    install: {
      title: t("settings.templates.stepInstall", { defaultValue: "Install" }),
      detail: t("settings.templates.stepInstallDetail", {
        defaultValue: "Writing files and dependencies",
      }),
    },
    config: {
      title: t("settings.templates.stepConfig", { defaultValue: "Config" }),
      detail: t("settings.templates.stepConfigDetail", {
        defaultValue: "Env, playbook and overlay",
      }),
    },
    start: {
      title: t("settings.templates.stepStart", { defaultValue: "Start" }),
      detail: t("settings.templates.stepStartDetail", {
        defaultValue: "Database and app process",
      }),
    },
  };

  const activeStep = steps[Math.min(current, steps.length - 1)];
  const showGraph = mode === "create" && (running || finished) && current <= 0;

  return (
    <div className="space-y-3 rounded-2xl bg-muted/35 px-3.5 py-3">
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
            {failed
              ? t("settings.templates.stepFailed", { defaultValue: "Stopped" })
              : finished
                ? t("settings.templates.stepDone", { defaultValue: "Ready" })
                : t("settings.templates.stepProgress", { defaultValue: "In progress" })}
          </p>
          <p className="mt-0.5 text-[13px] font-medium text-foreground [text-wrap:pretty]">
            {failed
              ? t("settings.templates.stepFailedHint", {
                  defaultValue: "The current step did not finish.",
                })
              : labels[activeStep].detail}
          </p>
        </div>
        <p className="shrink-0 text-[15px] font-semibold tabular-nums text-foreground">
          {percent}%
        </p>
      </div>

      {mode === "create" ? <DownloadGraph active={showGraph && !failed} reduceMotion={reduceMotion} /> : null}

      <div className="relative h-1.5 overflow-hidden rounded-full bg-background/80">
        <motion.div
          className={cn(
            "absolute inset-y-0 left-0 rounded-full",
            failed ? "bg-destructive" : "bg-primary",
          )}
          initial={false}
          animate={{ width: `${percent}%` }}
          transition={reduceMotion ? { duration: 0 } : { type: "tween", duration: 0.2, ease: "easeOut" }}
        />
      </div>

      <ol className="grid gap-2" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}>
        {steps.map((id, index) => {
          const Icon = STEP_ICONS[id];
          const done = !failed && (finished || index < current);
          const currentStep = !finished && index === current;
          return (
            <li key={id} className="min-w-0">
              <div className="flex items-center gap-1.5">
                <motion.span
                  className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
                    done
                      ? "bg-primary text-primary-foreground"
                      : currentStep
                        ? "bg-primary/15 text-primary"
                        : "bg-background/80 text-muted-foreground",
                    failed && currentStep && "bg-destructive/15 text-destructive",
                  )}
                  initial={false}
                  animate={
                    reduceMotion
                      ? { scale: 1 }
                      : done
                        ? { scale: [0.25, 1], opacity: [0.4, 1] }
                        : { scale: 1 }
                  }
                  transition={spring}
                >
                  {done ? <Check className="h-3.5 w-3.5" strokeWidth={2.4} aria-hidden /> : <Icon className="h-3.5 w-3.5" aria-hidden />}
                </motion.span>
                <span
                  className={cn(
                    "truncate text-[11px] font-medium",
                    done || currentStep ? "text-foreground" : "text-muted-foreground",
                  )}
                >
                  {labels[id].title}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
