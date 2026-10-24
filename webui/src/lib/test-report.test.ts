import { describe, expect, it } from "vitest";

import { parseTestReport } from "@/lib/test-report";

describe("parseTestReport", () => {
  it("groups pytest failures by file with their message", () => {
    const report = parseTestReport(
      [
        "....F.",
        "FAILED tests/test_desk_intent.py::CodeWorkbenchDesksTest::test_remembers - AssertionError: Items in the first set",
        "FAILED tests/test_desk_intent.py::test_other - KeyError: 'x'",
        "ERROR tests/test_loops.py::test_boot",
        "2 failed, 18 passed, 1 skipped, 1 error in 0.54s",
      ].join("\n"),
    );
    expect(report?.runner).toBe("pytest");
    expect([report?.passed, report?.failed, report?.skipped]).toEqual([18, 3, 1]);
    expect(report?.suites.map((s) => [s.name, s.failed])).toEqual([
      ["tests/test_desk_intent.py", 2],
      ["tests/test_loops.py", 1],
    ]);
    expect(report?.suites[0].failures[0].message).toContain("AssertionError");
  });

  it("reads a green pytest run", () => {
    const colored = parseTestReport(`${String.fromCharCode(27)}[32m5 passed in 0.10s${String.fromCharCode(27)}[0m`);
    expect(colored?.passed).toBe(5);
    const report = parseTestReport("...\n19 passed, 16 subtests passed in 0.30s");
    expect([report?.passed, report?.failed, report?.suites]).toEqual([19, 0, []]);
  });

  it("reads vitest counters and failures, without duplicates", () => {
    const report = parseTestReport(
      [
        " FAIL  src/lib/redact-secrets.test.ts > redactSecrets > masks url keys",
        " FAIL  src/lib/redact-secrets.test.ts > redactSecrets > masks url keys",
        " Test Files  1 failed | 6 passed (7)",
        "      Tests  1 failed | 81 passed (82)",
      ].join("\n"),
    );
    expect(report?.runner).toBe("vitest");
    expect([report?.passed, report?.failed]).toEqual([81, 1]);
    expect(report?.suites).toHaveLength(1);
    expect(report?.suites[0].failures[0].name).toBe("redactSecrets > masks url keys");
  });

  it("returns null for output that is not a test run", () => {
    expect(parseTestReport("added 3 packages in 4s")).toBeNull();
    expect(parseTestReport("")).toBeNull();
  });
});
