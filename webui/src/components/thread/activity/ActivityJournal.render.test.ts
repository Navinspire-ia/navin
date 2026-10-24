// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

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
  it("shows a setup card instead of raw Computer permission errors", () => {
    const setupEntries = buildJournalTimeline([{
      kind: "tool", name: "computer", args: '{"action":"screenshot"}', status: "error",
      error: "Error: computer screenshot failed: Accessibility is not granted. [Analyze the error above and try a different approach.]",
    }], { streaming: false });
    const html = renderToStaticMarkup(createElement(ActivityJournal, { entries: setupEntries, streaming: false }));
    expect(html).toContain('data-testid="computer-setup-notice"');
    expect(html).toContain("section=computer&amp;step=permissions");
    expect(html).not.toContain("screenshot");
    expect(html).not.toContain("Accessibility is not granted");
    expect(html).not.toContain("try a different approach");
    expect(summarizeJournal(setupEntries)).toMatchObject({ errors: 0, setup: 1 });
  });

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
    expect(html).toContain("Fixed");
    expect(html).not.toContain("Failed");
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

  it("renders ask_user as one question line and an answer line", () => {
    const html = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries: buildJournalTimeline([{
          kind: "tool",
          name: "ask_user",
          args: '{"question":"Which database should the app use?"}',
          status: "done",
          output: "The user did not pick a listed option. They wrote this instead:\nPostgres on Neon\nFollow that instruction.",
        }], { streaming: false }),
        streaming: false,
      }),
    );
    expect(html).toContain(">Asked<");
    expect(html).toContain("Which database should the app use?");
    expect(html).toContain('data-testid="activity-ask-answer"');
    expect(html).toContain("Postgres on Neon");
    expect(html).not.toContain("They wrote this instead");
  });

  it("opens shell output by default with all lines in a scrollable area", () => {
    const longCommand = `python3 - <<'EOF'\n${"print('x')\n".repeat(5)}EOF`;
    const html = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries: buildJournalTimeline([
          {
            kind: "shell",
            key: "c1",
            command: longCommand,
            status: "done",
            output: "line 1\nline 2\nline 3\nall good",
          },
          { kind: "shell", key: "c2", command: "false", status: "error", error: "exit 1" },
        ], { streaming: false }),
        streaming: false,
      }),
    );
    expect(html).toContain("whitespace-nowrap");
    expect(html).not.toContain("flex-wrap items-baseline");
    expect(html).toContain("+6 lines");
    expect(html).toContain('data-testid="activity-shell-run"');
    expect(html).toContain("line 1\nline 2\nline 3\nall good");
    expect(html).toContain("overflow-auto");
    expect(html).toContain('aria-expanded="true"');
    expect(count(html, 'data-testid="activity-journal-show-more"')).toBe(0);
    // Green check on success, red cross on the failed one, never the word.
    expect(count(html, 'data-testid="activity-journal-status-done"')).toBe(1);
    expect(count(html, 'data-testid="activity-journal-status-error"')).toBe(1);
    expect(html).not.toContain("Failed");
  });

  it("shows exploration and long test results without clicking", () => {
    const output = "FIRST RESULT\n" + "passed\n".repeat(3000) + "LAST RESULT";
    const entries = buildJournalTimeline([
      read("src/example.ts"), search("example"),
      { kind: "shell", key: "test", command: "npm test", status: "done", output },
    ], { streaming: false });
    const html = renderToStaticMarkup(createElement(ActivityJournal, { entries, streaming: false }));
    expect(html).toContain('data-testid="activity-explore-operation"');
    expect(html).toContain("FIRST RESULT");
    expect(html).toContain("LAST RESULT");
  });

  it("renders a thinking row in place with a line-preserving preview", () => {
    const html = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries: buildJournalTimeline([
          { kind: "reasoning", key: "r1", text: "Let me check:\n1. the route\n2. the test", status: "done" },
          shell("npm test"),
        ], { streaming: false }),
        streaming: false,
      }),
    );
    expect(html.indexOf('data-kind="reasoning"')).toBeLessThan(html.indexOf('data-kind="shell"'));
    expect(html).toContain('data-testid="activity-reasoning-preview"');
    expect(html).toContain("whitespace-pre-line");
    expect(html).toContain("Let me check:\n1. the route\n2. the test");
  });

  // The thread follows its tail: a live preview that went one line, two,
  // one at every token moved the whole screen with it ("l'ecran vibre").
  it("keeps a fixed two-line slot for live previews and the real height once settled", () => {
    const render = (streaming: boolean, step: JournalStep) =>
      renderToStaticMarkup(
        createElement(ActivityJournal, {
          entries: buildJournalTimeline([step], { streaming }),
          streaming,
        }),
      );
    const reasoning = (status: ActivityJournalStatus): JournalStep => (
      { kind: "reasoning", key: "r1", text: "Short thought", status }
    );
    const preview = (html: string) =>
      html.match(/data-testid="activity-reasoning-preview"[^>]*>(?:<[^>]+>)/)?.[0] ?? "";

    const live = preview(render(true, reasoning("running")));
    expect(live).toContain('data-live="true"');
    expect(live).toContain("h-12 overflow-hidden");

    const settled = preview(render(false, reasoning("done")));
    expect(settled).not.toBe("");
    expect(settled).not.toContain("data-live");
    expect(settled).not.toContain("h-12");
  });
});


describe("activity journal V3 controls", () => {
  it("offers the Essential / Standard / Debug switch with Standard checked by default", () => {
    const html = renderToStaticMarkup(
      createElement(ActivityJournal, {
        entries: buildJournalTimeline(
          Array.from({ length: 4 }, (_, index) => shell(`cmd-${index + 1}`, "done", `v${index + 1}`)),
          { streaming: false },
        ),
        streaming: false,
      }),
    );
    expect(html).toContain('data-testid="activity-verbosity"');
    expect(html).toMatch(/data-testid="activity-verbosity-standard"[^>]*aria-checked="true"|aria-checked="true"[^>]*data-testid="activity-verbosity-standard"/);
    expect(html).not.toContain('data-testid="activity-journal-virtual"');
  });
});
