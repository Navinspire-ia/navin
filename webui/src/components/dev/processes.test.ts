import { describe, expect, it } from "vitest";

import type { BackgroundProcess } from "@/lib/api";

import { formatElapsed, processStatus, shortCommand, tailLines } from "./processes";

function proc(overrides: Partial<BackgroundProcess> = {}): BackgroundProcess {
  return {
    id: "abc123",
    command: "npm run dev",
    cwd: "/tmp/app",
    elapsed_s: 12,
    idle_s: 3,
    returncode: null,
    owner: null,
    tail: "",
    ...overrides,
  };
}

describe("formatElapsed", () => {
  it("renders seconds below a minute", () => {
    expect(formatElapsed(0)).toBe("0s");
    expect(formatElapsed(59.9)).toBe("59s");
  });

  it("renders minutes with padded seconds", () => {
    expect(formatElapsed(65)).toBe("1m 05s");
    expect(formatElapsed(600)).toBe("10m 00s");
  });

  it("renders hours with padded minutes", () => {
    expect(formatElapsed(3660)).toBe("1h 01m");
    expect(formatElapsed(7200)).toBe("2h 00m");
  });

  it("clamps negatives to zero", () => {
    expect(formatElapsed(-5)).toBe("0s");
  });
});

describe("shortCommand", () => {
  it("keeps a simple command untouched", () => {
    expect(shortCommand("npm run dev")).toBe("npm run dev");
  });

  it("drops leading cd segments", () => {
    expect(shortCommand("cd app && npm run dev -- --port 3000")).toBe(
      "npm run dev -- --port 3000",
    );
  });

  it("keeps only the first line", () => {
    expect(shortCommand("python serve.py\nrest ignored")).toBe("python serve.py");
  });

  it("truncates very long commands with an ellipsis", () => {
    const long = "x".repeat(200);
    const out = shortCommand(long, 40);
    expect(out.length).toBe(40);
    expect(out.endsWith("…")).toBe(true);
  });
});

describe("processStatus", () => {
  it("is running while returncode is null", () => {
    expect(processStatus(proc())).toBe("running");
  });

  it("is exited on zero", () => {
    expect(processStatus(proc({ returncode: 0 }))).toBe("exited");
  });

  it("is failed on non-zero", () => {
    expect(processStatus(proc({ returncode: 1 }))).toBe("failed");
  });
});

describe("tailLines", () => {
  it("normalizes CRLF and strips trailing blank lines", () => {
    expect(tailLines("a\r\nb\r\n\r\n")).toEqual(["a", "b"]);
  });

  it("keeps only the last N lines", () => {
    const tail = Array.from({ length: 50 }, (_, i) => `line-${i}`).join("\n");
    const lines = tailLines(tail, 10);
    expect(lines).toHaveLength(10);
    expect(lines[0]).toBe("line-40");
    expect(lines[9]).toBe("line-49");
  });

  it("handles empty input", () => {
    expect(tailLines("")).toEqual([]);
  });
});
