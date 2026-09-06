import { describe, expect, it } from "vitest";

import { splitCapabilityMentionSegments } from "./mention-parse";
import type { CliAppInfo, McpPresetInfo, ProjectFileMatch } from "./types";

function cliApp(name: string, installed = true): CliAppInfo {
  return {
    name,
    display_name: name,
    category: "cli",
    description: "",
    requires: "",
    source: "builtin",
    entry_point: name,
    install_supported: true,
    installed,
    available: true,
    status: installed ? "installed" : "not_installed",
    skill_installed: false,
  };
}

function mcpPreset(
  name: string,
  { installed = true, configured = true } = {},
): McpPresetInfo {
  return {
    name,
    display_name: name,
    category: "mcp",
    description: "",
    docs_url: "",
    transport: "stdio",
    requires: "",
    note: "",
    install_supported: true,
    installed,
    configured,
    available: true,
    status: configured ? "configured" : "not_installed",
    required_fields: [],
    connection_summary: "",
  };
}

function file(path: string, kind: ProjectFileMatch["kind"] = "file"): ProjectFileMatch {
  return { path, name: path.split("/").pop() ?? path, kind };
}

function symbol(name: string, path: string, line: number): ProjectFileMatch {
  return { path, name, kind: "symbol", line, symbolKind: "function" };
}

const CODEX = cliApp("codex");
const LINEAR = mcpPreset("linear");
const LOOP = file("navin/agent/loop.py");
const TOOLS = file("navin/agent/tools", "directory");

/** Reduces segments to a compact shape so assertions read as the rendered line. */
function shape(segments: ReturnType<typeof splitCapabilityMentionSegments>) {
  return segments.map((segment) => [segment.kind, segment.text]);
}

describe("splitCapabilityMentionSegments", () => {
  it("returns nothing for an empty string", () => {
    expect(splitCapabilityMentionSegments("", [CODEX])).toEqual([]);
  });

  it("leaves text alone when no capability is known", () => {
    expect(shape(splitCapabilityMentionSegments("ask @codex", []))).toEqual([
      ["text", "ask @codex"],
    ]);
  });

  it("leaves text alone when the only candidates are uninstalled", () => {
    const segments = splitCapabilityMentionSegments("ask @codex", [cliApp("codex", false)]);
    expect(shape(segments)).toEqual([["text", "ask @codex"]]);
  });

  it("recognizes an installed CLI app", () => {
    expect(shape(splitCapabilityMentionSegments("ask @codex now", [CODEX]))).toEqual([
      ["text", "ask "],
      ["cli", "@codex"],
      ["text", " now"],
    ]);
  });

  it("recognizes a mention at the very start", () => {
    expect(shape(splitCapabilityMentionSegments("@codex go", [CODEX]))).toEqual([
      ["cli", "@codex"],
      ["text", " go"],
    ]);
  });

  it("matches a CLI app case-insensitively but keeps the typed text", () => {
    expect(shape(splitCapabilityMentionSegments("@CoDeX", [CODEX]))).toEqual([
      ["cli", "@CoDeX"],
    ]);
  });

  it("recognizes a configured MCP preset", () => {
    const segments = splitCapabilityMentionSegments("check @linear", [], [LINEAR]);
    expect(shape(segments)).toEqual([["text", "check "], ["mcp", "@linear"]]);
  });

  it("ignores an MCP preset that is installed but not configured", () => {
    const preset = mcpPreset("linear", { configured: false });
    const segments = splitCapabilityMentionSegments("check @linear", [], [preset]);
    expect(shape(segments)).toEqual([["text", "check @linear"]]);
  });

  it("recognizes a picked file path", () => {
    const segments = splitCapabilityMentionSegments(
      "read @navin/agent/loop.py",
      [],
      [],
      [LOOP],
    );
    expect(shape(segments)).toEqual([
      ["text", "read "],
      ["file", "@navin/agent/loop.py"],
    ]);
  });

  it("carries the matched file through on the segment", () => {
    const segments = splitCapabilityMentionSegments("@navin/agent/tools", [], [], [TOOLS]);
    expect(segments).toHaveLength(1);
    const [segment] = segments;
    expect(segment.kind === "file" && segment.file.kind).toBe("directory");
  });

  it("does not turn an unpicked path into a file mention", () => {
    const segments = splitCapabilityMentionSegments("read @navin/agent/loop.py", [], [], []);
    expect(shape(segments)).toEqual([["text", "read @navin/agent/loop.py"]]);
  });

  it("does not turn a name in prose into a mention", () => {
    // The guard that matters: @alice must stay prose even with candidates loaded.
    const segments = splitCapabilityMentionSegments("thanks @alice", [CODEX], [LINEAR], [LOOP]);
    expect(shape(segments)).toEqual([["text", "thanks @alice"]]);
  });

  it("never reads a dotted or slashed token as a CLI app", () => {
    const segments = splitCapabilityMentionSegments("@codex.py and @a/codex", [CODEX]);
    expect(shape(segments)).toEqual([["text", "@codex.py and @a/codex"]]);
  });

  describe("trailing punctuation", () => {
    it("is trimmed off a CLI mention ending a sentence", () => {
      expect(shape(splitCapabilityMentionSegments("ask @codex.", [CODEX]))).toEqual([
        ["text", "ask "],
        ["cli", "@codex"],
        ["text", "."],
      ]);
    });

    it("is trimmed off a file path ending a sentence", () => {
      const segments = splitCapabilityMentionSegments(
        "see @navin/agent/loop.py.",
        [],
        [],
        [LOOP],
      );
      expect(shape(segments)).toEqual([
        ["text", "see "],
        ["file", "@navin/agent/loop.py"],
        ["text", "."],
      ]);
    });

    it("is trimmed one character at a time", () => {
      expect(shape(splitCapabilityMentionSegments("(@codex)!", [CODEX]))).toEqual([
        ["text", "("],
        ["cli", "@codex"],
        ["text", ")!"],
      ]);
    });

    it("does not eat a real extension", () => {
      const py = file("loop.py");
      const segments = splitCapabilityMentionSegments("@loop.py", [], [], [py]);
      expect(shape(segments)).toEqual([["file", "@loop.py"]]);
    });
  });

  describe("scanning", () => {
    it("finds several mentions of mixed kinds in one line", () => {
      const segments = splitCapabilityMentionSegments(
        "@codex read @navin/agent/loop.py then @linear",
        [CODEX],
        [LINEAR],
        [LOOP],
      );
      expect(shape(segments)).toEqual([
        ["cli", "@codex"],
        ["text", " read "],
        ["file", "@navin/agent/loop.py"],
        ["text", " then "],
        ["mcp", "@linear"],
      ]);
    });

    it("keeps looking after an unresolved mention", () => {
      const segments = splitCapabilityMentionSegments("@nope then @codex", [CODEX]);
      expect(shape(segments)).toEqual([
        ["text", "@nope then "],
        ["cli", "@codex"],
      ]);
    });

    it("resolves a comma-separated list of mentions", () => {
      const segments = splitCapabilityMentionSegments(
        "@navin/agent/loop.py,@codex",
        [CODEX],
        [],
        [LOOP],
      );
      expect(shape(segments)).toEqual([
        ["file", "@navin/agent/loop.py"],
        ["text", ","],
        ["cli", "@codex"],
      ]);
    });

    it("resolves two files separated by a comma", () => {
      const other = file("navin/agent/review.py");
      const segments = splitCapabilityMentionSegments(
        "@navin/agent/loop.py, @navin/agent/review.py",
        [],
        [],
        [LOOP, other],
      );
      expect(shape(segments)).toEqual([
        ["file", "@navin/agent/loop.py"],
        ["text", ", "],
        ["file", "@navin/agent/review.py"],
      ]);
    });

    it("prefers the longer file path when one is a prefix of another", () => {
      const segments = splitCapabilityMentionSegments(
        "@navin/agent/tools/loop.py",
        [],
        [],
        [file("navin/agent/tools/loop.py"), TOOLS],
      );
      expect(shape(segments)).toEqual([["file", "@navin/agent/tools/loop.py"]]);
    });

    it("requires a boundary before the @ so an email stays prose", () => {
      expect(shape(splitCapabilityMentionSegments("mail me@codex", [CODEX]))).toEqual([
        ["text", "mail me@codex"],
      ]);
      const dotted = splitCapabilityMentionSegments("a.b@navin/agent/loop.py", [], [], [LOOP]);
      expect(shape(dotted)).toEqual([["text", "a.b@navin/agent/loop.py"]]);
    });

    it("accepts an opening bracket as the boundary", () => {
      expect(shape(splitCapabilityMentionSegments("[@codex]", [CODEX]))).toEqual([
        ["text", "["],
        ["cli", "@codex"],
        ["text", "]"],
      ]);
    });

    it("reads a mention on a later line", () => {
      expect(shape(splitCapabilityMentionSegments("line\n@codex", [CODEX]))).toEqual([
        ["text", "line\n"],
        ["cli", "@codex"],
      ]);
    });
  });

  it("does not lose or duplicate any character of the input", () => {
    const value = "@codex read @navin/agent/loop.py, then @linear. thanks @alice";
    const segments = splitCapabilityMentionSegments(value, [CODEX], [LINEAR], [LOOP]);
    expect(segments.map((segment) => segment.text).join("")).toBe(value);
  });

  describe("symbols", () => {
    // Nobody writes @navin/agent/loop.py:412 to mean a function, so a symbol
    // is written by name and has to be found again by name.
    const RUN_TURN = symbol("run_turn", "navin/agent/loop.py", 412);

    it("resolves a symbol written by its bare name", () => {
      const segments = splitCapabilityMentionSegments("fix @run_turn", [], [], [RUN_TURN]);
      expect(shape(segments)).toEqual([
        ["text", "fix "],
        ["file", "@run_turn"],
      ]);
    });

    it("carries the definition site through to the segment", () => {
      const segments = splitCapabilityMentionSegments("@run_turn", [], [], [RUN_TURN]);
      const found = segments[0];
      expect(found.kind).toBe("file");
      if (found.kind !== "file") return;
      expect(found.file.path).toBe("navin/agent/loop.py");
      expect(found.file.line).toBe(412);
    });

    it("resolves a qualified name", () => {
      const method = symbol("Parser.parse", "navin/parse.py", 20);
      const segments = splitCapabilityMentionSegments("@Parser.parse", [], [], [method]);
      expect(shape(segments)).toEqual([["file", "@Parser.parse"]]);
    });

    it("still trims the period that ends a sentence", () => {
      const segments = splitCapabilityMentionSegments("see @run_turn.", [], [], [RUN_TURN]);
      expect(shape(segments)).toEqual([
        ["text", "see "],
        ["file", "@run_turn"],
        ["text", "."],
      ]);
    });

    it("leaves an unpicked identifier as prose", () => {
      expect(shape(splitCapabilityMentionSegments("@run_turn", [], [], []))).toEqual([
        ["text", "@run_turn"],
      ]);
    });

    it("resolves a symbol and a file in the same message", () => {
      const segments = splitCapabilityMentionSegments(
        "@run_turn in @navin/agent/loop.py",
        [],
        [],
        [RUN_TURN, LOOP],
      );
      expect(shape(segments)).toEqual([
        ["file", "@run_turn"],
        ["text", " in "],
        ["file", "@navin/agent/loop.py"],
      ]);
    });

    it("does not let a symbol shadow a CLI app of the same name", () => {
      const clash = symbol("codex", "vendor/codex.py", 1);
      const segments = splitCapabilityMentionSegments("@codex", [CODEX], [], [clash]);
      // The picked symbol wins: the user chose it from the palette.
      expect(shape(segments)).toEqual([["file", "@codex"]]);
    });
  });
});
