// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { tidyRunOutput } from "@/lib/ansi";

import { HighlightedOutput, percentTone } from "./HighlightedOutput";
import { ShellRunCard } from "./ShellRunCard";

const render = (text: string) => renderToStaticMarkup(createElement(HighlightedOutput, { text }));

describe("HighlightedOutput", () => {
  it("colours percentages by level", () => {
    expect(percentTone(92)).toContain("emerald");
    expect(percentTone(64.5)).toContain("amber");
    expect(percentTone(12)).toContain("rose");
    const html = render("src/a.ts  92%\nsrc/b.ts  40%");
    expect(html).toMatch(/emerald[^>]*>92%</);
    expect(html).toMatch(/rose[^>]*>40%</);
  });

  it("colours passed, failed and skipped counts and keeps every line", () => {
    const html = render("tests/a.py ....  [100%]\n3 failed, 85 passed, 2 skipped in 1.2s");
    expect(html).toMatch(/rose[^>]*>3 failed</);
    expect(html).toMatch(/emerald[^>]*>85 passed</);
    expect(html).toMatch(/amber[^>]*>2 skipped</);
    expect(html).toContain("tests/a.py ....  ");
    expect(html).toContain("in 1.2s");
  });

  it("leaves plain output untouched", () => {
    expect(render("hello world")).toBe("hello world");
  });

  it("drops terminal colour codes from a vitest summary and still colours the counts", () => {
    const ESC = String.fromCharCode(27);
    const html = render(
      `${ESC}[2m Test Files ${ESC}[22m ${ESC}[1m${ESC}[32m2 passed${ESC}[39m${ESC}[22m${ESC}[90m (2)${ESC}[39m`,
    );
    expect(html).not.toContain(ESC);
    expect(html).not.toMatch(/\[\d+m/);
    expect(html).toMatch(/emerald[^>]*>2 passed</);
    expect(html).toContain("(2)");
  });

  it("drops colour codes that lost their ESC byte", () => {
    const html = render("[2m      Tests [22m [1m[32m18 passed[39m[22m[90m (18)[39m");
    expect(html).not.toMatch(/\[\d+m/);
    expect(html).toMatch(/emerald[^>]*>18 passed</);
    expect(html).toContain("(18)");
  });

  it("drops OSC hyperlinks but keeps their label", () => {
    const ESC = String.fromCharCode(27);
    expect(render(`see ${ESC}]8;;https://example.com${ESC}\\docs${ESC}]8;;${ESC}\\ now`)).toBe("see docs now");
  });

  it("keeps brackets that are not colour codes", () => {
    expect(render("arr[2] = x[i]")).toBe("arr[2] = x[i]");
    expect(render("tests/a.py ....  [100%]")).toMatch(/\[<span[^>]*>100%<\/span>\]/);
  });
});

// The exact result the user saw in a command card: the runtime's own lines
// read as if the command had printed them.
const FAILED_RUN = [
  "16:E       AssertionError: 'unknown' != 'blocked'",
  "STDERR:",
  "warning: slow",
  "",
  "",
  "Exit code: 1",
  "[compacted 1,515 chars; full output in C:\\Users\\me\\.navin\\tool-results\\exec\\run.log]",
].join("\n");

describe("tidyRunOutput", () => {
  it("keeps what the command printed and lifts the runtime notes out of it", () => {
    expect(tidyRunOutput(FAILED_RUN)).toEqual({
      body: "16:E       AssertionError: 'unknown' != 'blocked'\nwarning: slow",
      exitCode: 1,
      hiddenChars: 1515,
      fullOutputPath: "C:\\Users\\me\\.navin\\tool-results\\exec\\run.log",
    });
  });

  it("drops background-session bookkeeping", () => {
    const raw = [
      "building",
      "Process running. session_id: exec-1",
      "You will be told when it finishes; keep working meanwhile, or write_stdin with yield_time_ms to wait for it.",
      "Elapsed: 3.0s",
    ].join("\n");
    expect(tidyRunOutput(raw)).toEqual({ body: "building", exitCode: null, hiddenChars: null, fullOutputPath: null });
  });

  it("only reads the exit code the runtime appended at the end", () => {
    expect(tidyRunOutput("script says\nExit code: 7\nmore\n\nExit code: 0")).toMatchObject({
      body: "script says\nExit code: 7\nmore",
      exitCode: 0,
    });
  });
});

describe("ShellRunCard output", () => {
  const card = (output: string, hideCommand: boolean) =>
    renderToStaticMarkup(createElement(ShellRunCard, {
      run: { key: "r1", command: "cd /repo && pytest -q\npython - <<'EOF'\nEOF", status: "done", output },
      active: false,
      hideCommand,
    }));

  it.each([true, false])("shows the failing exit code as a note, not as output (hideCommand=%s)", (hideCommand) => {
    const html = card(FAILED_RUN, hideCommand);
    expect(html).toContain("AssertionError");
    for (const noise of ["Exit code: 1", "[compacted", "STDERR:", "tool-results\\exec\\run.log]"]) {
      expect(html).not.toContain(noise);
    }
    expect(html).toMatch(/data-testid="run-output-footer" data-exit-code="1"/);
  });

  it("says nothing extra when the command succeeded in full", () => {
    const html = card("18 passed\n\nExit code: 0", true);
    expect(html).toContain("18 passed");
    expect(html).not.toContain("Exit code");
    expect(html).not.toContain("run-output-footer");
  });
});
