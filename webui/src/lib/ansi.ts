// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export type AnsiSegment = {
  text: string;
  style?: AnsiStyle;
};

export type AnsiStyle = {
  backgroundColor?: string;
  color?: string;
  fontStyle?: "italic";
  fontWeight?: number;
  opacity?: number;
  textDecorationLine?: "underline";
};

type AnsiState = {
  backgroundColor?: string;
  bold: boolean;
  color?: string;
  dim: boolean;
  inverse: boolean;
  italic: boolean;
  underline: boolean;
};

const ESC = String.fromCharCode(27);
const BEL = String.fromCharCode(7);
const ANSI_PATTERN = new RegExp(`${ESC}\\[[0-?]*[ -/]*[@-~]`, "g");
// CSI (colours, cursor), OSC (window title, hyperlinks) and two-byte escapes.
const TERMINAL_ESCAPE_PATTERN = new RegExp(
  `${ESC}(?:\\[[0-?]*[ -/]*[@-~]|\\][^${BEL}${ESC}]*(?:${BEL}|${ESC}\\\\)|[@-Z\\\\-_])`,
  "g",
);
const LONE_ESC_PATTERN = new RegExp(ESC, "g");
// SGR colour codes whose ESC byte was dropped on the way: "[2m Tests [22m".
const ORPHAN_SGR_PATTERN = /\[\d{1,3}(?:;\d{1,3})*m/g;

const ANSI_COLORS = [
  "#000000",
  "#cd3131",
  "#0dbc79",
  "#e5e510",
  "#2472c8",
  "#bc3fbc",
  "#11a8cd",
  "#e5e5e5",
];

const ANSI_BRIGHT_COLORS = [
  "#666666",
  "#f14c4c",
  "#23d18b",
  "#f5f543",
  "#3b8eea",
  "#d670d6",
  "#29b8db",
  "#ffffff",
];

const RGB_STEPS = [0, 95, 135, 175, 215, 255];

export function hasAnsi(value: string): boolean {
  ANSI_PATTERN.lastIndex = 0;
  return ANSI_PATTERN.test(value);
}

export function stripAnsi(value: string): string {
  ANSI_PATTERN.lastIndex = 0;
  return value.replace(ANSI_PATTERN, "");
}

/**
 * Plain text of terminal output for display. Removes colour and cursor
 * escapes, OSC sequences, and SGR codes that lost their ESC byte, which would
 * otherwise show up as literal noise such as "[2m Test Files [22m".
 */
export function cleanTerminalText(value: string): string {
  if (!value) return value;
  return value
    .replace(TERMINAL_ESCAPE_PATTERN, "")
    .replace(LONE_ESC_PATTERN, "")
    .replace(ORPHAN_SGR_PATTERN, "");
}

/** A command result split into what the command printed and the runtime's notes about it. */
export type RunOutput = {
  body: string;
  /** The exit code the runtime appended, null when it did not say. */
  exitCode: number | null;
  /** Characters left out of a long output, null when nothing was shortened. */
  hiddenChars: number | null;
  /** Where the runtime saved the full output, when it did. */
  fullOutputPath: string | null;
};

const EXIT_LINE = /^Exit code:\s*(-?\d+)$/;
const COMPACTED_LINE = /^\[compacted ([\d,.\s\u00a0\u202f]+) chars(?:; full output in (.+)| \([^)]*\))?\]$/;
// Lines the runtime adds for the agent: stream labels and background-session
// bookkeeping. They are not part of what the command printed.
const BOOKKEEPING_LINE = new RegExp(
  "^(?:STDERR:|STDOUT:|Stdin closed\\.|\\(no output yet\\)|Elapsed: [\\d.]+s"
    + "|Process running\\. session_id: \\S+|You will be told when it finishes\\b.*"
    + "|Still running after \\d+s\\. The command was not killed\\b.*"
    + "|\\[sandboxed:[^\\]]*\\]|\\[ran outside the os sandbox[^\\]]*\\]?)$",
  "i",
);

/**
 * What a command printed, for display, with the runtime's own lines lifted
 * out: "Exit code: 1", "[compacted 1,515 chars; full output in ...]",
 * "STDERR:" and session notes. The exit code and the shortening are only read
 * from the end of the result, where the runtime appends them, so a command
 * that prints "Exit code: 7" itself keeps that line.
 */
export function tidyRunOutput(value: string | null | undefined): RunOutput {
  const lines = cleanTerminalText(value ?? "").replace(/\r\n?/g, "\n").split("\n");
  let exitCode: number | null = null;
  let hiddenChars: number | null = null;
  let fullOutputPath: string | null = null;
  let end = lines.length;
  while (end > 0) {
    const line = lines[end - 1].trim();
    const exit = EXIT_LINE.exec(line);
    const compacted = exit ? null : COMPACTED_LINE.exec(line);
    if (exit) {
      exitCode ??= Number.parseInt(exit[1], 10);
    } else if (compacted) {
      hiddenChars ??= Number.parseInt(compacted[1].replace(/\D/g, ""), 10) || null;
      fullOutputPath ??= compacted[2]?.trim() || null;
    } else if (line && !BOOKKEEPING_LINE.test(line)) {
      break;
    }
    end -= 1;
  }
  const body = lines
    .slice(0, end)
    .filter((line) => !BOOKKEEPING_LINE.test(line.trim()))
    .map((line) => line.replace(/\s+$/, ""))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/^\n+|\n+$/g, "");
  return { body, exitCode, hiddenChars, fullOutputPath };
}

/** True when the runtime notes are worth a line under the output. */
export function hasRunOutputNote(run: RunOutput): boolean {
  return (run.exitCode !== null && run.exitCode !== 0) || run.hiddenChars !== null;
}

function initialState(): AnsiState {
  return {
    bold: false,
    dim: false,
    inverse: false,
    italic: false,
    underline: false,
  };
}

function colorFrom256(value: number): string | undefined {
  if (value < 0 || value > 255) return undefined;
  if (value < 8) return ANSI_COLORS[value];
  if (value < 16) return ANSI_BRIGHT_COLORS[value - 8];
  if (value < 232) {
    const offset = value - 16;
    const red = RGB_STEPS[Math.floor(offset / 36)];
    const green = RGB_STEPS[Math.floor((offset % 36) / 6)];
    const blue = RGB_STEPS[offset % 6];
    return `rgb(${red}, ${green}, ${blue})`;
  }
  const gray = 8 + ((value - 232) * 10);
  return `rgb(${gray}, ${gray}, ${gray})`;
}

function colorFromRgb(red: number, green: number, blue: number): string | undefined {
  if ([red, green, blue].some((value) => !Number.isFinite(value) || value < 0 || value > 255)) {
    return undefined;
  }
  return `rgb(${red}, ${green}, ${blue})`;
}

function normalizedSgrParams(sequence: string): number[] | null {
  if (!sequence.endsWith("m")) return null;
  const body = sequence.slice(2, -1).trim();
  if (!body) return [0];
  return body.split(/[;:]/).map((part) => {
    const value = Number.parseInt(part || "0", 10);
    return Number.isFinite(value) ? value : 0;
  });
}

function applyExtendedColor(
  state: AnsiState,
  params: number[],
  index: number,
  key: "color" | "backgroundColor",
): number {
  const mode = params[index + 1];
  if (mode === 5) {
    const color = colorFrom256(params[index + 2]);
    if (color) state[key] = color;
    return index + 2;
  }
  if (mode === 2) {
    const color = colorFromRgb(params[index + 2], params[index + 3], params[index + 4]);
    if (color) state[key] = color;
    return index + 4;
  }
  return index;
}

function applySgrParams(state: AnsiState, params: number[]): void {
  for (let index = 0; index < params.length; index += 1) {
    const code = params[index];
    if (code === 0) {
      Object.assign(state, initialState());
    } else if (code === 1) {
      state.bold = true;
      state.dim = false;
    } else if (code === 2) {
      state.dim = true;
      state.bold = false;
    } else if (code === 3) {
      state.italic = true;
    } else if (code === 4) {
      state.underline = true;
    } else if (code === 7) {
      state.inverse = true;
    } else if (code === 22) {
      state.bold = false;
      state.dim = false;
    } else if (code === 23) {
      state.italic = false;
    } else if (code === 24) {
      state.underline = false;
    } else if (code === 27) {
      state.inverse = false;
    } else if (code === 39) {
      delete state.color;
    } else if (code === 49) {
      delete state.backgroundColor;
    } else if (code >= 30 && code <= 37) {
      state.color = ANSI_COLORS[code - 30];
    } else if (code >= 40 && code <= 47) {
      state.backgroundColor = ANSI_COLORS[code - 40];
    } else if (code >= 90 && code <= 97) {
      state.color = ANSI_BRIGHT_COLORS[code - 90];
    } else if (code >= 100 && code <= 107) {
      state.backgroundColor = ANSI_BRIGHT_COLORS[code - 100];
    } else if (code === 38) {
      index = applyExtendedColor(state, params, index, "color");
    } else if (code === 48) {
      index = applyExtendedColor(state, params, index, "backgroundColor");
    }
  }
}

function styleFromState(state: AnsiState): AnsiStyle | undefined {
  const foreground = state.inverse ? state.backgroundColor : state.color;
  const background = state.inverse ? state.color : state.backgroundColor;
  const style: AnsiStyle = {};
  if (foreground) style.color = foreground;
  if (background) style.backgroundColor = background;
  if (state.bold) style.fontWeight = 700;
  if (state.dim) style.opacity = 0.72;
  if (state.italic) style.fontStyle = "italic";
  if (state.underline) style.textDecorationLine = "underline";
  return Object.keys(style).length ? style : undefined;
}

export function parseAnsiSegments(value: string): AnsiSegment[] {
  const segments: AnsiSegment[] = [];
  const state = initialState();
  let cursor = 0;
  ANSI_PATTERN.lastIndex = 0;

  for (const match of value.matchAll(ANSI_PATTERN)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      segments.push({
        text: value.slice(cursor, index),
        style: styleFromState(state),
      });
    }
    const params = normalizedSgrParams(match[0]);
    if (params) applySgrParams(state, params);
    cursor = index + match[0].length;
  }

  if (cursor < value.length) {
    segments.push({
      text: value.slice(cursor),
      style: styleFromState(state),
    });
  }

  return segments.filter((segment) => segment.text.length > 0);
}
