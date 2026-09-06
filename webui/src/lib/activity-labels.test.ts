import { describe, expect, it } from "vitest";

import {
  actionFromToolArgs,
  activityLabelForTool,
  isSpawnTool,
  spawnTargetFromArgs,
  GENERIC_ACTIVITY_LABELS,
} from "./activity-labels";

describe("activityLabelForTool", () => {
  it("names the tools that used to show as Using <tool>", () => {
    expect(activityLabelForTool("notes")?.defaultValue).toBe("Checked notes");
    expect(activityLabelForTool("grep")?.defaultValue).toBe("Searched codebase");
    expect(activityLabelForTool("browser")?.defaultValue).toBe("Opened browser");
    expect(activityLabelForTool("git")?.defaultValue).toBe("Checked git");
    expect(activityLabelForTool("open_terminal")?.defaultValue).toBe("Opened terminal");
  });

  it("picks the verb from the git action, and falls back when the action is unknown", () => {
    expect(activityLabelForTool("git", "status")?.defaultValue).toBe("Checked git status");
    expect(activityLabelForTool("git", "commit")?.defaultValue).toBe("Created commit");
    expect(activityLabelForTool("git", "push")?.defaultValue).toBe("Pushed commits");
    expect(activityLabelForTool("git", "not-an-action")?.defaultValue).toBe("Checked git");
  });

  it("names test_run detect separately from a real run", () => {
    expect(activityLabelForTool("test_run", "run")?.defaultValue).toBe("Ran tests");
    expect(activityLabelForTool("test_run", "detect")?.defaultValue).toBe(
      "Detected test suites",
    );
  });

  it("picks the verb from the browser action", () => {
    expect(activityLabelForTool("browser", "navigate")?.defaultValue).toBe("Opened page");
    expect(activityLabelForTool("browser", "click")?.defaultValue).toBe("Clicked");
    expect(activityLabelForTool("browser", "screenshot")?.defaultValue).toBe("Took screenshot");
  });

  it("matches the last dotted segment, so a namespaced call still works", () => {
    expect(activityLabelForTool("navin.grep")?.defaultValue).toBe("Searched codebase");
    expect(activityLabelForTool("navin.git", "commit")?.defaultValue).toBe("Created commit");
  });

  it("ignores case", () => {
    expect(activityLabelForTool("Grep")?.defaultValue).toBe("Searched codebase");
    expect(activityLabelForTool("git", "Commit")?.defaultValue).toBe("Created commit");
  });

  it("leaves specialized tools unmapped so search / read / shell rows stay as they are", () => {
    expect(activityLabelForTool("web_search")).toBeNull();
    expect(activityLabelForTool("web_fetch")).toBeNull();
    expect(activityLabelForTool("read_file")).toBeNull();
    expect(activityLabelForTool("exec")).toBeNull();
    expect(activityLabelForTool("run_cli_app")).toBeNull();
    expect(activityLabelForTool("write_file")).toBeNull();
  });

  it("returns null for an unknown tool instead of inventing a verb", () => {
    expect(activityLabelForTool("not_a_real_tool")).toBeNull();
    expect(activityLabelForTool("")).toBeNull();
  });

  it("keeps i18n keys under message.activityTool", () => {
    expect(activityLabelForTool("notes")?.key).toBe("message.activityTool.notes");
    expect(activityLabelForTool("git", "commit")?.key).toBe("message.activityTool.gitCommit");
    expect(GENERIC_ACTIVITY_LABELS.searching.key).toBe("message.activityTool.searching");
  });
});

describe("spawnTargetFromArgs", () => {
  it("names the background task, so the line is not just Started sub-agent", () => {
    expect(
      spawnTargetFromArgs('{"task":"Build a pitch deck","label":"Pitch deck"}'),
    ).toBe("Pitch deck");
  });

  it("falls back to the role, then to the task itself", () => {
    expect(spawnTargetFromArgs('{"task":"Audit the auth","agent":"reviewer"}')).toBe(
      "reviewer",
    );
    expect(spawnTargetFromArgs('{"task":"Audit the auth"}')).toBe("Audit the auth");
  });

  it("returns nothing usable rather than a broken preview", () => {
    expect(spawnTargetFromArgs("")).toBe("");
    expect(spawnTargetFromArgs("{")).toBe("");
    expect(spawnTargetFromArgs('["task"]')).toBe("");
    expect(spawnTargetFromArgs('{"label":"   "}')).toBe("");
  });
});

describe("isSpawnTool", () => {
  it("matches spawn, including namespaced and cased forms", () => {
    expect(isSpawnTool("spawn")).toBe(true);
    expect(isSpawnTool("navin.spawn")).toBe(true);
    expect(isSpawnTool("Spawn")).toBe(true);
    expect(isSpawnTool("exec_spawn")).toBe(false);
    expect(isSpawnTool("")).toBe(false);
  });
});

describe("actionFromToolArgs", () => {
  it("reads the action field from a JSON trace payload", () => {
    expect(actionFromToolArgs('{"action":"commit","message":"fix"}')).toBe("commit");
  });

  it("returns null when there is no action, so the generic tool verb stays", () => {
    expect(actionFromToolArgs("")).toBeNull();
    expect(actionFromToolArgs("{")).toBeNull();
    expect(actionFromToolArgs('{"path":"src"}')).toBeNull();
  });
});
