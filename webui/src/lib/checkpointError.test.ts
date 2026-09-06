import { describe, expect, it } from "vitest";

import { parseCheckpointError } from "./checkpointError";

describe("parseCheckpointError", () => {
  it("keeps a plain-text gateway error as-is", () => {
    expect(parseCheckpointError(new Error("checkpoint not found"))).toEqual({
      code: null,
      message: "checkpoint not found",
    });
  });

  it("extracts the code and the English fallback from a named failure", () => {
    const body = JSON.stringify({
      error: "This project lives in a WSL distribution, but wsl.exe was not found.",
      code: "wslUnreachable",
    });
    expect(parseCheckpointError(new Error(body))).toEqual({
      code: "wslUnreachable",
      message:
        "This project lives in a WSL distribution, but wsl.exe was not found.",
    });
  });

  it("falls back to the raw body when the JSON is not a named failure", () => {
    const body = '{"unexpected": true}';
    expect(parseCheckpointError(new Error(body))).toEqual({
      code: null,
      message: body,
    });
  });

  it("survives a body that only looks like JSON", () => {
    expect(parseCheckpointError("{not json")).toEqual({
      code: null,
      message: "{not json",
    });
  });

  it("handles a non-Error rejection", () => {
    expect(parseCheckpointError(undefined)).toEqual({ code: null, message: "" });
  });
});
