import type { TFunction } from "i18next";

import type { UIMessage } from "@/lib/types";

const SHORT_ROLE_FALLBACKS: Record<string, string> = {
  deep: "Complex",
  dev: "Coding",
  docs: "Docs",
  fast: "Simple",
  plan: "Planning",
  review: "Review",
  search: "Search",
  security: "Security",
  vision: "Vision",
  code: "Editor",
};

export function displayModelLeaf(label?: string | null, name?: string | null): string {
  if (label?.trim()) return label.trim();
  if (name?.trim()) {
    const leaf = name.trim().split("/").pop() ?? name.trim();
    return leaf || name.trim();
  }
  return "";
}

export function taskRoleShortLabel(role: string | undefined | null, t: TFunction): string {
  const key = (role || "").trim();
  if (!key) return "";
  return t(`thread.taskRoles.${key}`, {
    defaultValue: SHORT_ROLE_FALLBACKS[key] || key,
  });
}

export interface TaskHint {
  modelLabel?: string;
  taskRole?: string;
  /** True when Task routing picked this model over the one in the composer. */
  routed?: boolean;
}

export function formatTaskModelHint(
  modelLabel: string,
  taskRole: string | undefined | null,
  t: TFunction,
  routed = false,
): string {
  const role = taskRoleShortLabel(taskRole, t);
  const base = modelLabel && role ? `${modelLabel} · ${role}` : modelLabel || role;
  if (!base || !routed) return base;
  return `${base} · ${t("thread.taskRouted", { defaultValue: "auto-routed" })}`;
}

/** Tooltip for the "Model · Task" trace line: says who chose the model. */
export function taskHintTitle(t: TFunction): string {
  return t("thread.taskHintTitle", {
    defaultValue:
      "Model and task type of this turn. With no model locked in the composer, Settings > Models > Task routing chooses the model per task.",
  });
}

export function messageTaskHint(message: UIMessage | undefined): TaskHint | undefined {
  if (!message) return undefined;
  const modelLabel = displayModelLeaf(message.modelLabel, message.modelName);
  if (!modelLabel && !message.taskRole) return undefined;
  return { modelLabel, taskRole: message.taskRole };
}

export function taskHintFromMessages(
  messages: UIMessage[],
  t: TFunction,
  fallback?: TaskHint,
): string {
  let modelLabel = "";
  let taskRole: string | undefined;
  for (const message of messages) {
    const label = displayModelLeaf(message.modelLabel, message.modelName);
    if (label && !modelLabel) modelLabel = label;
    if (message.taskRole && !taskRole) taskRole = message.taskRole;
    if (modelLabel && taskRole) break;
  }
  if (!modelLabel && fallback?.modelLabel) modelLabel = fallback.modelLabel;
  if (!taskRole && fallback?.taskRole) taskRole = fallback.taskRole;
  return formatTaskModelHint(modelLabel, taskRole, t, Boolean(fallback?.routed));
}
