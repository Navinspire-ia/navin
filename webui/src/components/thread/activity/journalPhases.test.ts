// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { JournalPhases } from "./JournalPhases";
import { buildJournalTimeline, groupJournalPhases, type JournalStep } from "./activityJournalModel";

const read = (path: string): JournalStep => ({ kind: "read", path, status: "done" });
const search = (query: string): JournalStep => ({ kind: "search", query, tool: "grep", status: "done" });
const edit = (path: string, added: number, deleted: number): JournalStep => ({
  kind: "edit",
  key: path,
  path,
  status: "done",
  added,
  deleted,
  hasStats: true,
});
const shell = (command: string, status: "done" | "error" = "done"): JournalStep => ({
  kind: "shell",
  key: command,
  command,
  status,
});

describe("journal phases", () => {
  it("reads a finished turn as exploration, changes and commands, in that order", () => {
    const entries = buildJournalTimeline(
      [read("a.ts"), search("locale"), read("b.ts"), edit("a.ts", 30, 2), shell("npm test"), edit("b.ts", 3, 0)],
      { streaming: false },
    );
    const phases = groupJournalPhases(entries);
    expect(phases.map((phase) => phase.key)).toEqual(["explore", "edit", "test"]);
    expect(phases[0].count).toBe(3);
    expect(phases[1]).toMatchObject({ count: 2, added: 33, deleted: 2 });
    expect(phases[2].count).toBe(1);
  });

  it("lists a step left in error again as a point that needs attention", () => {
    const entries = buildJournalTimeline([shell("npm run build", "error")], { streaming: false });
    const phases = groupJournalPhases(entries);
    expect(phases.map((phase) => phase.key)).toEqual(["check", "attention"]);
    expect(phases[1].count).toBe(1);
  });

  it("drops a failure the agent fixed by running the same command again", () => {
    const entries = buildJournalTimeline([shell("npm test", "error"), shell("npm test")], { streaming: false });
    expect(groupJournalPhases(entries).map((phase) => phase.key)).toEqual(["test"]);
  });

  it("has nothing to show for an empty turn", () => {
    expect(groupJournalPhases([])).toEqual([]);
  });
});

describe("journal phases render", () => {
  const render = (props: Partial<Parameters<typeof JournalPhases>[0]> = {}) =>
    renderToStaticMarkup(
      createElement(JournalPhases, {
        entries: buildJournalTimeline(
          [read("a.ts"), search("locale"), edit("a.ts", 30, 2), shell("npm run build", "error")],
          { streaming: false },
        ),
        fileEdits: [],
        fileEditDisplayMode: "diff",
        ...props,
      }),
    );

  it("shows quick/long, the usual minutes and the real percent on the Tests line", () => {
    const testRun = (extra: Record<string, unknown>): JournalStep => ({
      kind: "tool", name: "test_run", args: '{"action":"run"}', status: "running", ...extra,
    } as JournalStep);
    const html = render({
      live: true,
      entries: buildJournalTimeline([testRun({ percent: 42, expectedS: 240, testKind: "long" })], { streaming: true }),
    });
    expect(html).toContain("journal-test-progress");
    expect(html).toContain("Long tests");
    expect(html).toContain("~4 min");
    expect(html).toContain("42%");
  });

  it("says nothing about duration for a first run and shows no percent when none is known", () => {
    const html = render({
      live: true,
      entries: buildJournalTimeline(
        [{ kind: "tool", name: "test_run", args: '{"action":"run"}', status: "running" } as JournalStep],
        { streaming: true },
      ),
    });
    expect(html).not.toContain("Long tests");
    expect(html).not.toContain("Quick tests");
    expect(html).not.toContain("journal-test-percent");
  });

  it("shows long command ~minutes and the estimated percent while a piped command runs", () => {
    const step = {
      kind: "shell",
      key: "call:c9",
      command: "python -m pytest -q 2>&1 | tail -25",
      status: "running",
      run: {
        key: "call:c9",
        command: "python -m pytest -q 2>&1 | tail -25",
        status: "running",
        percent: 37,
        expectedS: 180,
        runKind: "long",
      },
    } as JournalStep;
    const html = render({ live: true, entries: buildJournalTimeline([step], { streaming: true }) });
    expect(html).toContain("journal-test-progress");
    expect(html).toMatch(/Long (command|tests)/);
    expect(html).toContain("~3 min");
    expect(html).toContain("37%");
  });

  it("drops the bar once a command has finished", () => {
    const step = {
      kind: "shell",
      key: "call:c10",
      command: "npm run build",
      status: "done",
      run: { key: "call:c10", command: "npm run build", status: "done", percent: 100, expectedS: 200, runKind: "long" },
    } as JournalStep;
    const html = render({ live: false, entries: buildJournalTimeline([step], { streaming: false }) });
    expect(html).not.toContain("journal-test-progress");
  });

  it("folds every section to one line with its count", () => {
    const html = render();
    for (const key of ["explore", "edit", "check", "attention"]) {
      expect(html).toContain(`data-testid="journal-phase-${key}"`);
      expect(html).not.toContain(`data-testid="journal-phase-${key}-body"`);
    }
    expect(html).toContain("2 operations");
    expect(html).toContain("1 file<");
    expect(html).toContain("+30");
  });

  it("opens every section when the user prefers the expanded journal", () => {
    const html = render({ defaultOpen: true });
    expect(html).toContain('data-testid="journal-phase-check-body"');
    expect(html).toContain("npm run build");
  });

  it("titles the answer only when one follows, on the same row as a section title", () => {
    expect(render()).not.toContain("journal-result-heading");
    const html = render({ resultHeading: true });
    expect(html).toContain("Final result");
    // Same geometry as a phase title: check mark in the icon column, details control. The
    // title alone is heavier (15px semibold) than the step titles (14.5px medium).
    const heading = html.slice(html.indexOf('data-testid="journal-result-heading"'));
    expect(heading).toContain("lucide-circle-check");
    expect(heading).toContain("text-[15px] font-semibold");
    expect(html.slice(0, html.indexOf('data-testid="journal-result-heading"'))).not.toContain("font-semibold text-foreground\"");
    expect(heading).toContain('data-testid="journal-result-details"');
    expect(html).toMatch(/class="[^"]*min-h-10[^"]*"[^>]*data-testid="journal-result-heading"/);
  });
});

describe("journal phases while the turn runs", () => {
  it("keeps the section being worked on open and the others folded", () => {
    const entries = buildJournalTimeline(
      [read("a.ts"), edit("a.ts", 3, 0), { kind: "shell", key: "t", command: "npm test", status: "running" }],
      { streaming: true },
    );
    const html = renderToStaticMarkup(
      createElement(JournalPhases, { entries, fileEdits: [], fileEditDisplayMode: "diff", live: true }),
    );
    expect(html).toContain('data-testid="journal-phase-test-body"');
    expect(html).not.toContain('data-testid="journal-phase-explore-body"');
    expect(html).toContain("npm test");
  });

  it("draws rows as quiet lines: a kind icon, no tone color", () => {
    const html = renderToStaticMarkup(
      createElement(JournalPhases, {
        entries: buildJournalTimeline([read("a.ts"), search("locale")], { streaming: false }),
        fileEdits: [],
        fileEditDisplayMode: "diff",
        defaultOpen: true,
      }),
    );
    expect(html).toContain('data-testid="activity-journal-kind-icon"');
    expect(html).not.toContain("--tone-explore");
  });
});

describe("files written by subagents", () => {
  it("count in the changes section and list as rows", () => {
    const html = renderToStaticMarkup(
      createElement(JournalPhases, {
        entries: buildJournalTimeline([read("a.ts"), edit("a.ts", 3, 0)], { streaming: false }),
        fileEdits: [],
        fileEditDisplayMode: "diff",
        defaultOpen: true,
        reviewFiles: [
          { path: "/w/restaurant/backend/app.py", display_path: "restaurant/backend/app.py", status: "modified", added: 200, deleted: 0 },
          { path: "/w/restaurant/frontend/src/main.tsx", display_path: "restaurant/frontend/src/main.tsx", status: "modified", added: 80, deleted: 1 },
        ] as never,
      }),
    );
    // The section lists the merged files: here the two from the review.
    expect(html).toContain("2 files");
    expect(html).toContain("+280");
    // Grouped by folder; the first level is open, deeper folders wait for a click.
    expect(html).toContain(">backend<");
    expect(html).toContain(">frontend/src<");
    expect(html.split('data-testid="journal-agent-folder"').length - 1).toBe(3);
    expect(html).not.toContain('data-testid="journal-agent-file"');
  });
});

describe("agent files tree", () => {
  it("groups files by folder, merges single-child chains and sums lines upward", async () => {
    const { buildFileTree } = await import("./JournalPhases");
    const change = (path: string, added: number) => ({ path, display_path: path, status: "modified", added, deleted: 0 }) as never;
    const tree = buildFileTree([
      change("restaurant/backend/app.py", 200),
      change("restaurant/backend/README.md", 56),
      change("restaurant/frontend/src/main.tsx", 33),
      change("restaurant/frontend/src/api.ts", 5),
      change("restaurant/frontend/index.html", 1),
    ]);
    const top = [...tree.folders.values()];
    expect(top.map((node) => node.name)).toEqual(["restaurant"]);
    const restaurant = top[0];
    expect(restaurant.total).toBe(5);
    expect(restaurant.added).toBe(295);
    expect([...restaurant.folders.keys()].sort()).toEqual(["backend", "frontend"]);
    expect(restaurant.folders.get("frontend")!.folders.get("src")!.files).toHaveLength(2);
  });
});

describe("exploration count", () => {
  it("lists every action, a read the log did not name included", () => {
    const html = renderToStaticMarkup(
      createElement(JournalPhases, {
        entries: buildJournalTimeline([read("a.ts"), read(""), read(""), search("x"), read("b.ts")], { streaming: false }),
        fileEdits: [],
        fileEditDisplayMode: "diff",
        defaultOpen: true,
      }),
    );
    expect(html).toContain("5 operations");
    expect(html.split('data-testid="activity-journal-row"').length - 1).toBe(5);
    expect(html).toContain("file not named in the log");
  });
});

describe("changed files merged with the review", () => {
  it("counts each file once and uses the review's numbers when it has them", async () => {
    const { mergeChangedFiles } = await import("./JournalPhases");
    const own = [
      { key: "a", path: "restaurant/backend/app.py", added: 14, deleted: 1, approximate: false, binary: false, status: "done", pending: false },
      { key: "b", path: "restaurant/start.sh", added: 14, deleted: 0, approximate: false, binary: false, status: "done", pending: false },
    ] as never;
    const review = [
      { path: "/w/restaurant/backend/app.py", display_path: "restaurant/backend/app.py", status: "added", added: 420, deleted: 0 },
      { path: "/w/restaurant/frontend/src/main.tsx", display_path: "restaurant/frontend/src/main.tsx", status: "added", added: 33, deleted: 0 },
    ] as never;
    const files = mergeChangedFiles(own, review);
    expect(files.map((file) => [file.display_path, file.added])).toEqual([
      ["restaurant/backend/app.py", 420],
      ["restaurant/start.sh", 14],
      ["restaurant/frontend/src/main.tsx", 33],
    ]);
  });
});

describe("batch reads", () => {
  it("name every file, even when the trace cut the arguments", async () => {
    const { pathsFromToolArgs } = await import("./activityJournalModel");
    expect(pathsFromToolArgs('{"files": [{"path": "a.py", "offset": 1}, {"path": "b/c.ts"}]}')).toEqual(["a.py", "b/c.ts"]);
    expect(pathsFromToolArgs('{"files": [{"path": "a.py", "offset": 1}, {"path": "b/c.ts"}, {"path": "navin/m')).toEqual(["a.py", "b/c.ts"]);
    expect(pathsFromToolArgs('{"path": "x.md"}')).toEqual(["x.md"]);
  });
});

describe("commands, checks and tests", () => {
  it("sorts each command by what it was for, with its error count", () => {
    const entries = buildJournalTimeline(
      [shell("ls -la"), shell("ruff check ."), shell("npx tsc --noEmit", "error"), shell("python -m pytest -q"), shell("npm run test:unit")],
      { streaming: false },
    );
    const phases = groupJournalPhases(entries);
    expect(phases.map((phase) => [phase.key, phase.count, phase.errors])).toEqual([
      ["command", 1, 0],
      ["check", 2, 1],
      ["test", 2, 0],
      ["attention", 1, 1],
    ]);
  });
});

describe("exploration groups", () => {
  it("puts each action in one group only", () => {
    const html = renderToStaticMarkup(
      createElement(JournalPhases, {
        entries: buildJournalTimeline(
          [read("a.ts"), { kind: "search", query: "*test*", tool: "glob", status: "done" }, { kind: "tool", name: "list_dir", args: '{"path": "."}', status: "done" }],
          { streaming: false },
        ),
        fileEdits: [],
        fileEditDisplayMode: "diff",
        defaultOpen: true,
      }),
    );
    expect(html.split('data-testid="journal-explore-group"').length - 1).toBe(3);
    expect(html.split('data-kind="search"').length - 1).toBe(1);
  });
});
