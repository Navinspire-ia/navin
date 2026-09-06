import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  ActivityDigest,
  ActivityJournal,
  ActivityPhaseBar,
  JournalNowLine,
} from "@/components/thread/activity/ActivityJournal";
import {
  buildJournalTimeline,
  currentJournalEntry,
  summarizeJournal,
  type ActivityJournalStatus,
  type JournalStep,
} from "@/components/thread/activity/activityJournalModel";

const read = (path: string): JournalStep => ({ kind: "read", path, status: "done" });
const search = (query: string): JournalStep => ({ kind: "search", query, tool: "grep", status: "done" });
const shell = (command: string, status: ActivityJournalStatus = "done", key = command): JournalStep => ({
  kind: "shell",
  key,
  command,
  status,
  durationMs: 2300,
});
const tool = (name: string, args: string): JournalStep => ({ kind: "tool", name, args, status: "done" });
const edit = (path: string): JournalStep => ({
  kind: "edit",
  key: path,
  path,
  status: "done",
  operation: "edit",
  added: 12,
  deleted: 3,
  hasStats: true,
});

const count = (html: string, needle: string): number => html.split(needle).length - 1;

describe("ActivityJournal render", () => {
  const entries = buildJournalTimeline([
    read("webui/src/a.ts"),
    search("navin"),
    read("webui/src/b.ts"),
    shell("npm test", "error", "s1"),
    shell("npm test", "done", "s2"),
    edit("webui/src/a.ts"),
    tool("board", '{"action":"update","id":"1"}'),
    tool("board", '{"action":"update","id":"2"}'),
    tool("skill", '{"name":"code-principles"}'),
  ], { streaming: false });

  it("renders one colored row per action with outcome at the end", () => {
    const html = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries,
        streaming: false,
        onOpenFilePreview: () => {},
      }),
    );
    expect(count(html, 'data-testid="activity-journal-row"')).toBe(5);
    expect(html).toContain('data-tone="explore"');
    expect(html).toContain('data-tone="shell"');
    expect(html).toContain('data-tone="edit"');
    expect(html).toContain('data-tone="task"');
    expect(html).toContain('data-tone="skill"');
    expect(html).toContain("Explored");
    expect(html).toContain("Failed, then fixed");
    // Both attempts of the retried command add up in the trailing duration.
    expect(html).toContain(">5s<");
    expect(html).toContain("2 attempts");
    expect(html).toContain("×2");
    expect(html).toContain("+12");
    expect(html).toContain('data-testid="activity-journal-file"');
    expect(html).toContain('data-testid="activity-journal-copy"');
    expect(html).not.toContain("Show all");
  });

  it("turns a board task into a button that opens the card, other targets stay text", () => {
    const html = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries: buildJournalTimeline([
          tool("board", '{"action":"create","title":"Open Preview in Edge"}'),
          tool("skill", '{"name":"code-principles"}'),
        ], { streaming: false }),
        streaming: false,
      }),
    );
    expect(count(html, 'data-testid="activity-journal-task"')).toBe(1);
    expect(html).toContain("Open on the board");
    expect(html).toContain(">Open Preview in Edge</button>");
    expect(html).toContain(">code-principles</span>");
  });

  it("honors a smaller preview for the compact activity preference", () => {
    const html = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries: buildJournalTimeline(
          Array.from({ length: 6 }, (_, index) => shell(`cmd-${index + 1}`, "done", `k${index + 1}`)),
          { streaming: false },
        ),
        streaming: false,
        previewMax: 4,
      }),
    );
    expect(count(html, 'data-testid="activity-journal-row"')).toBe(4);
    expect(html).toContain("Show all · 2 more");
  });

  it("draws the time-by-phase bar with one segment and one legend entry per phase", () => {
    const html = renderToStaticMarkup(
      createElement(ActivityPhaseBar, {
        phases: [
          { key: "thinking", ms: 60_000 },
          { key: "explore", ms: 30_000 },
          { key: "edit", ms: 10_000 },
        ],
      }),
    );
    expect(html).toContain('data-testid="activity-phase-split"');
    expect(count(html, "data-phase=")).toBe(3);
    expect(html).toContain('data-phase="thinking"');
    expect(html).toContain("width:60%");
    expect(html).toContain("Thinking");
    expect(html).toContain("Exploring");
    expect(html).toContain("Editing");
    expect(html).toContain("1m");
    expect(html).toContain("30s");
    expect(html).toContain("--composer-ask");
    expect(html).toContain("--tone-explore");
    expect(renderToStaticMarkup(createElement(ActivityPhaseBar, { phases: [] }))).toBe("");
  });

  it("keeps a plain dot on rows without details and a chevron on the others", () => {
    const html = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries: buildJournalTimeline([
          { kind: "tool", name: "", args: "", text: "Thinking about it", status: "done" },
          shell("ls"),
        ], { streaming: false }),
        streaming: false,
      }),
    );
    expect(count(html, 'data-testid="activity-journal-dot"')).toBe(1);
    expect(count(html, 'data-testid="activity-journal-toggle"')).toBe(1);
  });

  it("follows the tail while streaming and leads with the head once done", () => {
    const many = Array.from({ length: 10 }, (_, index) =>
      shell(`cmd-${index + 1}`, index === 9 ? "running" : "done", `k${index + 1}`));
    const live = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries: buildJournalTimeline(many, { streaming: true }),
        streaming: true,
      }),
    );
    expect(live).toContain("cmd-10");
    expect(live).toContain("cmd-3");
    expect(live).not.toContain("cmd-2<");
    expect(live).toContain("2 earlier steps");
    expect(live).toContain("animate-spin");

    const done = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries: buildJournalTimeline(many, { streaming: false }),
        streaming: false,
      }),
    );
    expect(done).toContain("cmd-1<");
    expect(done).toContain("cmd-8");
    expect(done).not.toContain("cmd-9");
    expect(done).toContain("Show all · 2 more");
    expect(done).not.toContain("animate-spin");
  });

  it("renders the folded digest and the live now line", () => {
    const digest = renderToStaticMarkup(
      createElement(ActivityDigest, { digest: summarizeJournal(entries) }),
    );
    expect(digest).toContain('data-testid="activity-digest"');
    expect(digest).toContain("2 files");
    expect(digest).toContain("1 search");
    expect(digest).toContain("1 edit");
    expect(digest).toContain("1 command");
    expect(digest).toContain("3 tools");
    expect(digest).toContain("1 fixed");

    const liveEntries = buildJournalTimeline([read("a.ts"), shell("npm run dev", "running")], { streaming: true });
    const now = currentJournalEntry(liveEntries);
    expect(now?.kind).toBe("shell");
    const nowHtml = renderToStaticMarkup(createElement(JournalNowLine, { entry: now! }));
    expect(nowHtml).toContain('data-testid="activity-now-line"');
    expect(nowHtml).toContain('data-tone="shell"');
    expect(nowHtml).toContain("Running");
    expect(nowHtml).toContain("npm run dev");
  });

  it("shows Sandboxed on confined commands, an amber Unsandboxed on lifted ones, nothing otherwise", () => {
    const confined: JournalStep = {
      kind: "shell",
      key: "c1",
      command: "npm test",
      status: "done",
      run: { key: "c1", command: "npm test", status: "done", sandbox: "native" },
    };
    const lifted: JournalStep = {
      kind: "shell",
      key: "c2",
      command: "sudo apt install jq",
      status: "done",
      run: { key: "c2", command: "sudo apt install jq", status: "done", sandboxLifted: true },
    };
    const plain: JournalStep = {
      kind: "shell",
      key: "c3",
      command: "ls",
      status: "done",
      run: { key: "c3", command: "ls", status: "done" },
    };
    const html = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries: buildJournalTimeline([confined, lifted, plain], { streaming: false }),
        streaming: false,
      }),
    );
    expect(count(html, 'data-testid="activity-sandbox-badge"')).toBe(2);
    expect(html).toContain('data-sandbox="native"');
    expect(html).toContain('data-sandbox="lifted"');
    expect(html).toContain(">Sandboxed<");
    expect(html).toContain(">Unsandboxed<");
    expect(html).toContain("text-amber-700");
    expect(html).toContain("writes limited to the project");

    // The live header line carries the same badge, icon-only to stay short.
    const live = buildJournalTimeline([{ ...confined, status: "running" }], { streaming: true });
    const nowHtml = renderToStaticMarkup(createElement(JournalNowLine, { entry: currentJournalEntry(live)! }));
    expect(nowHtml).toContain('data-sandbox="native"');
    expect(nowHtml).not.toContain(">Sandboxed<");
  });
});
