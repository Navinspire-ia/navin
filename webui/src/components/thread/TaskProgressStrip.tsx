// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useTranslation } from "react-i18next";

import { TaskProgressBar } from "@/components/thread/activity/TaskProgressBar";
import type { TaskProgressData } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Compact active-task strip above the composer / under the Dev toolbar.
 */
export function TaskProgressStrip({
  progress,
  className,
}: {
  progress: TaskProgressData | null | undefined;
  className?: string;
}) {
  const { t } = useTranslation();
  if (!progress) return null;

  const stepLabel =
    progress.step_index != null && progress.steps_total != null
      ? t("thread.progress.stepOf", {
          defaultValue: "Step {{current}}/{{total}}",
          current: progress.step_index,
          total: progress.steps_total,
        })
      : null;

  const label = [stepLabel, progress.step || progress.label].filter(Boolean).join(" · ");

  // Flat inline strip: it sits in the conversation flow under the plan, so no
  // card chrome - just the thin bar and its label.
  return (
    <div
      data-testid="task-progress-strip"
      className={cn(
        "mt-1.5 w-full px-1 py-1",
        className,
      )}
    >
      <TaskProgressBar
        percent={progress.percent}
        indeterminate={progress.indeterminate ?? progress.percent == null}
        etaSeconds={progress.eta_s}
        label={label}
        compact
      />
    </div>
  );
}
