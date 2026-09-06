import { describe, expect, it } from "vitest";

import type { FileDiagnosticsPayload } from "@/lib/types";

function diag(
  severity: "error" | "warning",
  line: number,
  message: string,
): FileDiagnosticsPayload["diagnostics"][number] {
  return {
    severity,
    line,
    col: 1,
    end_line: line,
    end_col: 1,
    code: severity === "error" ? "E" : "W",
    message,
  };
}

/** Mirror of DevProblemsPanel aggregation for aggressive sorting contracts. */
function aggregateProblems(
  diagnosticsByPath: Record<string, FileDiagnosticsPayload>,
) {
  const out: Array<{
    path: string;
    severity: string;
    line: number;
    message: string;
  }> = [];
  for (const [path, payload] of Object.entries(diagnosticsByPath)) {
    for (const row of payload.diagnostics ?? []) {
      out.push({
        path,
        severity: row.severity,
        line: row.line,
        message: row.message,
      });
    }
  }
  out.sort((a, b) => {
    const sev = (s: string) => (s === "error" ? 0 : s === "warning" ? 1 : 2);
    return sev(a.severity) - sev(b.severity) || a.path.localeCompare(b.path) || a.line - b.line;
  });
  return out;
}

describe("DevProblemsPanel aggregation", () => {
  it("orders errors before warnings and sorts by path then line", () => {
    const rows = aggregateProblems({
      "b.ts": {
        path: "b.ts",
        supported: true,
        tool: "tsc",
        errors: 0,
        warnings: 1,
        diagnostics: [diag("warning", 1, "warn-b")],
      },
      "a.ts": {
        path: "a.ts",
        supported: true,
        tool: "tsc",
        errors: 2,
        warnings: 0,
        diagnostics: [diag("error", 20, "err-late"), diag("error", 2, "err-early")],
      },
    });
    expect(rows.map((r) => r.message)).toEqual(["err-early", "err-late", "warn-b"]);
  });

  it("handles empty open files", () => {
    expect(aggregateProblems({})).toEqual([]);
  });
});
