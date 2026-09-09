// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { BoardTask, SessionPlan, SessionPlanItem } from "@/lib/api";

/** Mission ledger subset attached to board payloads when present. */
export type PlanMission = {
  goal?: string;
  version?: number;
  status?: string;
  constraints?: string[];
  facts?: string[];
  missing_info?: string[];
  acceptance_criteria?: string[];
  pause_reason?: string;
};

export type PlanMarkdownLabels = {
  overview: string;
  steps: string;
  acceptance: string;
  constraints: string;
  facts: string;
  missingInfo: string;
  validation: string;
  status: string;
  done: string;
  active: string;
  blocked: string;
  cancelled: string;
  pending: string;
};

const DEFAULT_LABELS: PlanMarkdownLabels = {
  overview: "Overview",
  steps: "Plan",
  acceptance: "Acceptance criteria",
  constraints: "Constraints",
  facts: "Facts",
  missingInfo: "Missing information",
  validation: "Validation",
  status: "Status",
  done: "Done",
  active: "In progress",
  blocked: "Blocked",
  cancelled: "Cancelled",
  pending: "Pending",
};

function bulletList(items: string[]): string {
  return items
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => `- ${item}`)
    .join("\n");
}

function statusLabel(item: SessionPlanItem, labels: PlanMarkdownLabels): string {
  if (item.cancelled) return labels.cancelled;
  if (item.done) return labels.done;
  if (item.active) return labels.active;
  if (item.blocked) return labels.blocked;
  return labels.pending;
}

function stepBody(
  item: SessionPlanItem,
  fullDescription: string,
  labels: PlanMarkdownLabels,
  index: number,
): string {
  const lines: string[] = [`### ${index}. ${item.title.trim() || "Step"}`];
  const description = fullDescription.trim() || (item.description ?? "").trim();
  if (description) {
    lines.push("", description);
  }
  const meta: string[] = [];
  meta.push(`**${labels.status}:** ${statusLabel(item, labels)}`);
  const acceptance = (item.acceptance ?? "").trim();
  if (acceptance) {
    meta.push(`**${labels.acceptance}:** ${acceptance}`);
  }
  const validation = (item.validation ?? "").trim();
  if (validation && validation !== "none") {
    meta.push(`**${labels.validation}:** \`${validation}\``);
  }
  if (meta.length) {
    lines.push("", meta.join("  \n"));
  }
  return lines.join("\n");
}

/**
 * Build a Cursor-style Markdown document for the session plan, using full
 * board task descriptions when available and mission ledger context.
 */
export function formatSessionPlanMarkdown(
  plan: SessionPlan,
  tasks: BoardTask[] = [],
  mission: PlanMission | null = null,
  labels: Partial<PlanMarkdownLabels> = {},
): string {
  const L = { ...DEFAULT_LABELS, ...labels };
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const title = (mission?.goal || plan.goal || plan.title || "Plan").trim();
  const sections: string[] = [`# ${title}`];

  if (plan.version != null || mission?.version != null) {
    const version = plan.version ?? mission?.version;
    sections.push("", `*v${version}*`);
  }

  const overviewBits: string[] = [];
  const pendingTitles = plan.items
    .filter((item) => !item.done && !item.cancelled)
    .map((item) => item.title.trim())
    .filter(Boolean);
  if (pendingTitles.length > 0) {
    overviewBits.push(
      pendingTitles.length === 1
        ? pendingTitles[0]
        : `${pendingTitles.length} steps: ${pendingTitles.slice(0, 4).join("; ")}${
            pendingTitles.length > 4 ? "…" : ""
          }`,
    );
  }
  if (plan.last_change?.reason?.trim()) {
    overviewBits.push(plan.last_change.reason.trim());
  }
  if (overviewBits.length) {
    sections.push("", `## ${L.overview}`, "", overviewBits.join("\n\n"));
  }

  const acceptance = [
    ...(plan.acceptance_criteria ?? []),
    ...(mission?.acceptance_criteria ?? []),
  ].filter((value, index, all) => value.trim() && all.indexOf(value) === index);
  if (acceptance.length) {
    sections.push("", `## ${L.acceptance}`, "", bulletList(acceptance));
  }

  const constraints = (mission?.constraints ?? []).filter((v) => v.trim());
  if (constraints.length) {
    sections.push("", `## ${L.constraints}`, "", bulletList(constraints));
  }

  const facts = (mission?.facts ?? []).filter((v) => v.trim());
  if (facts.length) {
    sections.push("", `## ${L.facts}`, "", bulletList(facts));
  }

  const missing = (mission?.missing_info ?? []).filter((v) => v.trim());
  if (missing.length) {
    sections.push("", `## ${L.missingInfo}`, "", bulletList(missing));
  }

  if (plan.items.length) {
    sections.push("", `## ${L.steps}`);
    plan.items.forEach((item, index) => {
      const task = byId.get(item.id);
      sections.push(
        "",
        stepBody(item, task?.description ?? "", L, index + 1),
      );
    });
  }

  return `${sections.join("\n").trim()}\n`;
}

/** Short teaser under the plan title (Cursor-style card blurb). */
export function planSummaryBlurb(plan: SessionPlan, maxLen = 220): string {
  const heading = (plan.goal || plan.title || "").trim();
  const parts = plan.items
    .filter((item) => !item.cancelled)
    .map((item) => (item.description || item.title || "").trim())
    .filter((text) => text && text !== heading);
  if (!parts.length) return "";
  const text = parts.slice(0, 2).join(" · ");
  return text.length > maxLen ? `${text.slice(0, maxLen - 1).trimEnd()}…` : text;
}
