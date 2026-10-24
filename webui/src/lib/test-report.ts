// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export type TestFailure = { suite: string; name: string; message?: string };

export type TestSuite = { name: string; failed: number; failures: TestFailure[] };

export type TestReport = {
  runner: "pytest" | "vitest" | "jest";
  passed: number;
  failed: number;
  skipped: number;
  suites: TestSuite[];
};

const ANSI = new RegExp(`${String.fromCharCode(27)}\\[[0-9;]*m`, "g");

function count(text: string, re: RegExp): number {
  const m = text.match(re);
  return m ? Number(m[1]) : 0;
}

function groupBySuite(failures: TestFailure[]): TestSuite[] {
  const map = new Map<string, TestSuite>();
  for (const f of failures) {
    const suite = map.get(f.suite) ?? { name: f.suite, failed: 0, failures: [] };
    suite.failed += 1;
    suite.failures.push(f);
    map.set(f.suite, suite);
  }
  return [...map.values()];
}

/** Read a pytest / vitest / jest run and group its failures by suite (file). Null when it is not a test run. */
export function parseTestReport(raw: string): TestReport | null {
  const text = raw.replace(ANSI, "");
  const pytestSummary = text.match(/^=*\s*(?:\d+ (?:passed|failed|skipped|error|errors|deselected|xfailed|xpassed|warnings?)[, ]*)+.*in [\d.]+s/m)
    ?? text.match(/^\s*(?:\d+ (?:passed|failed|skipped)[, ]*)+(?:, \d+ \w+)* in [\d.]+s/m);
  if (pytestSummary) {
    const line = pytestSummary[0];
    const failures: TestFailure[] = [];
    for (const m of text.matchAll(/^(?:FAILED|ERROR) ([^\s:]+)::(\S+)(?: - (.*))?$/gm)) {
      failures.push({ suite: m[1], name: m[2], message: m[3]?.trim() || undefined });
    }
    return {
      runner: "pytest",
      passed: count(line, /(\d+) passed/),
      failed: count(line, /(\d+) failed/) + count(line, /(\d+) errors?/),
      skipped: count(line, /(\d+) skipped/),
      suites: groupBySuite(failures),
    };
  }
  const vitest = text.match(/^\s*Tests\s+(.*\(\d+\))\s*$/m);
  if (vitest) {
    const line = vitest[1];
    const failures: TestFailure[] = [];
    const seen = new Set<string>();
    for (const m of text.matchAll(/^\s*(?:FAIL|×|✗)\s+(\S+\.(?:[cm]?[jt]sx?))\s+>\s+(.+?)\s*(?:\d+ms)?$/gm)) {
      const key = `${m[1]}::${m[2]}`;
      if (seen.has(key)) continue;
      seen.add(key);
      failures.push({ suite: m[1], name: m[2] });
    }
    return {
      runner: "vitest",
      passed: count(line, /(\d+) passed/),
      failed: count(line, /(\d+) failed/),
      skipped: count(line, /(\d+) skipped/),
      suites: groupBySuite(failures),
    };
  }
  const jest = text.match(/^Tests:\s+(.*total)\s*$/m);
  if (jest) {
    const line = jest[1];
    const failures: TestFailure[] = [];
    for (const m of text.matchAll(/^\s*● (.+?) › (.+)$/gm)) failures.push({ suite: m[1], name: m[2] });
    return {
      runner: "jest",
      passed: count(line, /(\d+) passed/),
      failed: count(line, /(\d+) failed/),
      skipped: count(line, /(\d+) skipped/),
      suites: groupBySuite(failures),
    };
  }
  return null;
}
