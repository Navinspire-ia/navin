// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { motion, useReducedMotion } from "framer-motion";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PlatformLogo } from "@/components/dev/PlatformLogo";
import {
  appFactoryTarget,
  appFactoryTargetsOf,
  type AppFactoryKind,
} from "@/components/dev/appFactoryTargets";
import { cn } from "@/lib/utils";

const STEPS = [
  ["analyze", "Project analysis"],
  ["adapt", "Code adaptation"],
  ["build", "Build"],
  ["test", "App tests"],
  ["deliver", "Delivery"],
] as const;

/** Analysis -> Adaptation -> Build -> Tests -> Delivery, revealed one by one. */
function AppFactorySteps({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  return (
    <ol
      className={cn("flex flex-wrap items-center gap-1", compact ? "text-[11px]" : "text-xs")}
      data-testid="app-factory-steps"
    >
      {STEPS.map(([key, fallback], index) => (
        <motion.li
          key={key}
          className="flex items-center gap-1"
          initial={reduced ? false : { opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={reduced ? { duration: 0 } : { delay: index * 0.06, duration: 0.2 }}
        >
          <span
            className={cn(
              "rounded-md border border-border bg-muted text-foreground",
              compact ? "px-1.5 py-0.5" : "px-2 py-1",
            )}
          >
            {t(`dev.appFactory.steps.${key}`, { defaultValue: fallback })}
          </span>
          {index < STEPS.length - 1 ? (
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground rtl:rotate-180" aria-hidden />
          ) : null}
        </motion.li>
      ))}
    </ol>
  );
}

export function AppFactoryDialog({
  open,
  onOpenChange,
  initialKind,
  onStart,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialKind: AppFactoryKind | null;
  /** A target was picked: the caller asks for confirmation before starting. */
  onStart: (targetId: string) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const kinds: AppFactoryKind[] =
    initialKind === "desktop" ? ["desktop", "mobile"] : ["mobile", "desktop"];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" data-testid="app-factory-dialog">
        <DialogHeader>
          <DialogTitle>{tx("dev.appFactory.title", "Turn your project into an app")}</DialogTitle>
          <DialogDescription>
            {tx(
              "dev.appFactory.intro",
              "Navin turns the current project into Windows, macOS, Linux, Android and iOS apps. The web project and the backend stay untouched: everything is written in a dedicated os/<platform> folder.",
            )}
          </DialogDescription>
        </DialogHeader>
        <AppFactorySteps />
        <div className="space-y-3">
          {kinds.map((kind, groupIndex) => (
            <section key={kind} data-testid={`app-factory-group-${kind}`}>
              <h3
                className={cn(
                  "mb-1.5 text-sm font-medium",
                  groupIndex === 0 ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {kind === "mobile"
                  ? tx("dev.appsMobile", "Mobile apps")
                  : tx("dev.appsDesktop", "Desktop apps")}
              </h3>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {appFactoryTargetsOf(kind).map((target) => (
                  <button
                    key={target.id}
                    type="button"
                    data-testid={`app-factory-target-${target.id}`}
                    onClick={() => {
                      onOpenChange(false);
                      onStart(target.id);
                    }}
                    className={cn(
                      "flex items-start gap-2 rounded-md border border-border bg-background p-2 text-start transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      groupIndex === 0 && "border-primary/40",
                    )}
                  >
                    <PlatformLogo id={target.id} className="mt-0.5" />
                    <span className="min-w-0">
                      <span className="block truncate text-sm">{target.label}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {target.deliverables} · {target.folder}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
        <DialogFooter>
          <p className="text-xs text-muted-foreground">
            {tx(
              "dev.appFactory.honest",
              "Navin reports which tests ran and which could not run on this machine.",
            )}
          </p>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * "Create the Android app of your project?": shown before anything starts, so
 * a click in a menu never launches a build by surprise.
 */
export function AppFactoryConfirmDialog({
  targetId,
  projectName,
  onCancel,
  onConfirm,
}: {
  targetId: string | null;
  projectName?: string | null;
  onCancel: () => void;
  onConfirm: (targetId: string) => void;
}) {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const target = targetId ? appFactoryTarget(targetId) : undefined;
  const platform = target?.label ?? "";
  return (
    <Dialog open={Boolean(target)} onOpenChange={(next) => (next ? undefined : onCancel())}>
      <DialogContent className="max-w-md" data-testid="app-factory-confirm">
        {target ? (
          <>
            <DialogHeader>
              <div className="flex items-start gap-3">
                <motion.span
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-border bg-muted/50"
                  initial={reduced ? false : { scale: 0.85, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 420, damping: 28 }}
                >
                  <PlatformLogo id={target.id} className="h-6 w-6" />
                </motion.span>
                <div className="min-w-0 space-y-1.5 text-start">
                  <DialogTitle>
                    {t("dev.appFactory.confirm.title", {
                      platform,
                      defaultValue: "Create the {{platform}} app of your project?",
                    })}
                  </DialogTitle>
                  <DialogDescription>
                    {t("dev.appFactory.confirm.body", {
                      folder: target.folder,
                      defaultValue:
                        "Navin analyses your project, adapts it, builds the app, tests it and gives you the package. Everything is written in {{folder}}: your website and your backend are not modified.",
                    })}
                  </DialogDescription>
                </div>
              </div>
            </DialogHeader>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
              {projectName ? (
                <>
                  <dt className="text-muted-foreground">
                    {t("dev.appFactory.confirm.project", { defaultValue: "Project" })}
                  </dt>
                  <dd className="min-w-0 truncate font-medium" data-testid="app-factory-confirm-project">
                    {projectName}
                  </dd>
                </>
              ) : null}
              <dt className="text-muted-foreground">
                {t("dev.appFactory.confirm.package", { defaultValue: "Package" })}
              </dt>
              <dd className="min-w-0 truncate">{target.deliverables}</dd>
              <dt className="text-muted-foreground">
                {t("dev.appFactory.confirm.folder", { defaultValue: "Folder" })}
              </dt>
              <dd className="min-w-0 truncate font-mono text-[13px]">{target.folder}</dd>
            </dl>
            <AppFactorySteps compact />
            <p className="text-xs text-muted-foreground">
              {t("dev.appFactory.confirm.tools", {
                defaultValue:
                  "Missing build tools are installed only after you agree. If this computer cannot build this app, Navin tells you and prepares a cloud build.",
              })}
            </p>
            <DialogFooter className="gap-2 sm:gap-2">
              <Button type="button" variant="outline" onClick={onCancel} data-testid="app-factory-confirm-cancel">
                {t("dev.appFactory.confirm.cancel", { defaultValue: "Cancel" })}
              </Button>
              <Button
                type="button"
                autoFocus
                onClick={() => onConfirm(target.id)}
                data-testid="app-factory-confirm-start"
              >
                <PlatformLogo id={target.id} className="me-1.5 h-4 w-4 text-current dark:text-current" />
                {t("dev.appFactory.confirm.start", {
                  platform,
                  defaultValue: "Create the {{platform}} app",
                })}
              </Button>
            </DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
