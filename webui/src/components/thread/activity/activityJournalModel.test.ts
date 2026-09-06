import { describe, expect, it } from "vitest";

import {
  buildJournalTimeline,
  currentJournalEntry,
  journalFactsFromArgs,
  journalTargetFromArgs,
  journalTaskIdFromArgs,
  journalTaskLink,
  journalToneForTool,
  summarizeJournal,
  type ActivityJournalStatus,
  type JournalStep,
} from "./activityJournalModel";

const read = (path: string): JournalStep => ({ kind: "read", path, status: "done" });
const search = (query: string): JournalStep => ({ kind: "search", query, tool: "grep", status: "done" });
const shell = (command: string, status: ActivityJournalStatus = "done", key = command): JournalStep => ({
  kind: "shell",
  key,
  command,
  status,
});
const tool = (name: string, args: string, status: ActivityJournalStatus = "done"): JournalStep => ({
  kind: "tool",
  name,
  args,
  status,
});
const edit = (path: string): JournalStep => ({
  kind: "edit",
  key: path,
  path,
  status: "done",
  operation: "edit",
  added: 3,
  deleted: 1,
  hasStats: true,
});

describe("buildJournalTimeline", () => {
  it("keeps the real order and folds consecutive reads and searches into one explore row", () => {
    const entries = buildJournalTimeline([
      read("webui/src/a.ts"),
      search("navin"),
      read("webui/src/b.ts"),
      shell("npm test"),
      edit("webui/src/a.ts"),
      read("webui/src/c.ts"),
      tool("board", '{"action":"create","title":"Open Preview"}'),
    ], { streaming: false });
    expect(entries.map((entry) => entry.kind)).toEqual(["explore", "shell", "edit", "read", "tool"]);
    const explore = entries[0];
    expect(explore.files).toBe(2);
    expect(explore.searches).toBe(1);
    expect(explore.filesList).toEqual(["webui/src/a.ts", "webui/src/b.ts"]);
    expect(explore.queriesList).toEqual(["navin"]);
    expect(entries[2].added).toBe(3);
    expect(entries[3].file).toBe("c.ts");
    expect(entries[3].path).toBe("webui/src/c.ts");
    expect(entries[4].tone).toBe("task");
    expect(entries[4].target).toBe("Open Preview");
  });

  it("shows a lone read or search as its own row", () => {
    const entries = buildJournalTimeline([read("a.ts"), shell("ls"), search("x")], { streaming: false });
    expect(entries.map((entry) => entry.kind)).toEqual(["read", "shell", "search"]);
    expect(entries[0].file).toBe("a.ts");
    expect(entries[2].query).toBe("x");
  });

  it("folds a failed command re-run until it passes into one recovered row", () => {
    const entries = buildJournalTimeline([
      shell("npm test", "error", "s1"),
      shell("npm test", "done", "s2"),
    ], { streaming: false });
    expect(entries).toHaveLength(1);
    expect(entries[0].status).toBe("done");
    expect(entries[0].recovered).toBe(true);
    expect(entries[0].attempts).toBe(2);
  });

  it("counts consecutive identical tool calls and keeps each call in the detail", () => {
    const entries = buildJournalTimeline([
      tool("board", '{"action":"update","id":"1"}'),
      tool("board", '{"action":"update","id":"2"}'),
      tool("git", '{"action":"status"}'),
    ], { streaming: false });
    expect(entries).toHaveLength(2);
    expect(entries[0].count).toBe(2);
    expect(entries[0].occurrences).toEqual(["1", "2"]);
    expect(entries[1].tone).toBe("git");
    expect(entries[1].target).toBe("");
    expect(entries[1].facts).toEqual([{ label: "action", value: "status" }]);
  });

  it("marks the tail as running while streaming and settles it once done", () => {
    const steps = [shell("npm test", "running")];
    const live = buildJournalTimeline(steps, { streaming: true });
    expect(live[0].status).toBe("running");
    expect(currentJournalEntry(live)?.id).toBe(live[0].id);
    const settled = buildJournalTimeline(steps, { streaming: false });
    expect(settled[0].status).toBe("done");
    expect(settled[0].live).toBe(false);
  });
});

describe("summarizeJournal", () => {
  it("adds colored counters for the folded digest", () => {
    const entries = buildJournalTimeline([
      read("a.ts"),
      search("x"),
      read("b.ts"),
      shell("npm test", "error", "s1"),
      shell("npm test", "done", "s2"),
      edit("a.ts"),
      tool("skill", '{"name":"code-principles"}'),
      shell("npm run lint", "error", "s3"),
    ], { streaming: false });
    expect(summarizeJournal(entries)).toEqual({
      files: 2,
      searches: 1,
      edits: 1,
      commands: 2,
      tools: 1,
      errors: 1,
      recovered: 1,
    });
  });
});

describe("journalTargetFromArgs", () => {
  it("keeps the human title and drops action: status noise", () => {
    expect(journalTargetFromArgs('{"action":"create","title":"Open Preview in Edge"}')).toBe(
      "Open Preview in Edge",
    );
    expect(journalTargetFromArgs('{"action":"status"}')).toBe("");
    expect(journalTargetFromArgs('{"name":"code-principles"}')).toBe("code-principles");
    expect(journalTargetFromArgs('{"url":"https://www.example.com/docs/a"}')).toBe("example.com/docs/a");
  });
});

describe("journalToneForTool", () => {
  it("gives each tool family its own tone", () => {
    expect(journalToneForTool("skill")).toBe("skill");
    expect(journalToneForTool("board")).toBe("task");
    expect(journalToneForTool("git")).toBe("git");
    expect(journalToneForTool("browser")).toBe("browser");
    expect(journalToneForTool("notes")).toBe("skill");
    expect(journalToneForTool("unknown_widget")).toBe("tool");
  });
});

describe("journalFactsFromArgs", () => {
  it("keeps action and title for the expand panel", () => {
    const facts = journalFactsFromArgs('{"action":"create","title":"Open Preview in Edge"}');
    expect(facts).toEqual([
      { label: "action", value: "create" },
      { label: "title", value: "Open Preview in Edge" },
    ]);
  });
});

describe("board task links", () => {
  it("reads the task id from board arguments", () => {
    expect(journalTaskIdFromArgs('{"action":"move","task_id":"t-42","status":"done"}')).toBe("t-42");
    expect(journalTaskIdFromArgs('{"action":"update","id":" t-7 "}')).toBe("t-7");
    expect(journalTaskIdFromArgs('{"action":"list"}')).toBe("");
  });

  it("links board rows to their card by id and title, and nothing else", () => {
    const entries = buildJournalTimeline([
      tool("board", '{"action":"create","title":"Open Preview in Edge"}'),
      tool("board", '{"action":"move","task_id":"t-42","status":"done"}'),
      tool("skill", '{"name":"code-principles"}'),
      tool("board", '{"action":"list"}'),
    ], { streaming: false });
    expect(entries.map((entry) => journalTaskLink(entry))).toEqual([
      { title: "Open Preview in Edge" },
      { id: "t-42" },
      null,
      null,
    ]);
    expect(entries[1].taskId).toBe("t-42");
  });
});
