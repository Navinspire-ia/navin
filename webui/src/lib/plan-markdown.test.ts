// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { BoardTask, SessionPlan } from "@/lib/api";
import { formatSessionPlanMarkdown, planSummaryBlurb } from "./plan-markdown";

const plan: SessionPlan = {
  items: [
    {
      id: "t1",
      title: "Add autonomy toggle",
      description: "Opt-in switch on Tasks",
      status: "planned",
      priority: "high",
      done: false,
      active: false,
      blocked: false,
      acceptance: "Toggle persists per project",
      validation: "test",
    },
    {
      id: "t2",
      title: "Git branch per task",
      description: "Create branch on start",
      status: "planned",
      priority: "medium",
      done: false,
      active: false,
      blocked: false,
    },
  ],
  current_id: "t1",
  title: "Add autonomy toggle",
  done_count: 0,
  total_count: 2,
  complete: false,
  goal: "Autonomie board Git GitHub",
  version: 1,
  acceptance_criteria: ["PR opened for each task"],
};

const tasks: BoardTask[] = [
  {
    id: "t1",
    title: "Add autonomy toggle",
    description:
      "Add an opt-in autonomy mode to the Tasks module with automatic git branches.",
    status: "planned",
    priority: "high",
    labels: [],
    assignee: null,
    milestone_id: null,
    depends_on: [],
    created_by: { type: "agent", name: "navin" },
    created_at: "",
    updated_at: "",
    comments: [],
  },
];

describe("formatSessionPlanMarkdown", () => {
  it("builds a structured markdown plan with full task bodies", () => {
    const md = formatSessionPlanMarkdown(plan, tasks, {
      constraints: ["No force-push to main"],
      facts: ["Repo uses GitHub Actions"],
    });
    expect(md).toContain("# Autonomie board Git GitHub");
    expect(md).toContain("## Plan");
    expect(md).toContain("### 1. Add autonomy toggle");
    expect(md).toContain("automatic git branches");
    expect(md).toContain("## Constraints");
    expect(md).toContain("No force-push to main");
    expect(md).toContain("**Validation:** `test`");
  });
});

describe("planSummaryBlurb", () => {
  it("summarizes step descriptions under the goal heading", () => {
    expect(planSummaryBlurb(plan)).toContain("Opt-in switch on Tasks");
  });
});
