// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Parsing for the composer's `@` mentions.
 *
 * A mention can name a CLI app, an MCP preset, a workspace path, or a symbol,
 * and they are told apart by looking the token up rather than by its shape:
 * only things the user actually picked from the palette are treated as
 * attachments, so `@alice` in prose never becomes a file.
 *
 * Kept free of React so it can be exercised on its own.
 */

import type { CliAppInfo, McpPresetInfo, ProjectFileMatch } from "./types";

export type CapabilityMentionSegment =
  | { kind: "text"; text: string }
  | { kind: "cli"; text: string; app: CliAppInfo }
  | { kind: "mcp"; text: string; preset: McpPresetInfo }
  | { kind: "file"; text: string; file: ProjectFileMatch };

/**
 * Characters a mention may contain, wide enough for a workspace path.
 *
 * The `@` must follow a boundary so an email address stays prose. Commas and
 * semicolons count as boundaries because listing attachments as
 * `@a.py,@b.py` is a natural way to name several files at once.
 */
const MENTION_TOKEN_RE = /(^|[\s([{,;])@([A-Za-z0-9_./-]+)/g;
const TRAILING_PUNCTUATION_RE = /[.,;:!?)\]}]$/;

type ResolvedMention =
  | { kind: "cli"; token: string; app: CliAppInfo }
  | { kind: "mcp"; token: string; preset: McpPresetInfo }
  | { kind: "file"; token: string; file: ProjectFileMatch };

/**
 * The text a picked result is written as, which is also how it is found again.
 *
 * A path identifies a file, but a symbol is written by its name: nobody types
 * `@navin/utils/git_state.py:77` to mean `repo_state`.
 */
export function mentionToken(match: ProjectFileMatch): string {
  return match.kind === "symbol" ? match.name : match.path;
}

function resolveMentionToken(
  raw: string,
  cliAppsByName: Map<string, CliAppInfo>,
  mcpPresetsByName: Map<string, McpPresetInfo>,
  filesByToken: Map<string, ProjectFileMatch>,
): ResolvedMention | null {
  // The token pattern is permissive enough to swallow the period ending a
  // sentence, so shrink from the right until something is recognized.
  let token = raw;
  while (token.length > 0) {
    const file = filesByToken.get(token);
    if (file) return { kind: "file", token, file };
    if (!token.includes("/") && !token.includes(".")) {
      const key = token.toLowerCase();
      const app = cliAppsByName.get(key);
      if (app) return { kind: "cli", token, app };
      const preset = mcpPresetsByName.get(key);
      if (preset) return { kind: "mcp", token, preset };
    }
    if (!TRAILING_PUNCTUATION_RE.test(token)) return null;
    token = token.slice(0, -1);
  }
  return null;
}

export function splitCapabilityMentionSegments(
  value: string,
  cliApps: CliAppInfo[],
  mcpPresets: McpPresetInfo[] = [],
  files: ProjectFileMatch[] = [],
): CapabilityMentionSegment[] {
  if (!value || (cliApps.length === 0 && mcpPresets.length === 0 && files.length === 0)) {
    return value ? [{ kind: "text", text: value }] : [];
  }
  const cliAppsByName = new Map(
    cliApps
      .filter((app) => app.installed)
      .map((app) => [app.name.toLowerCase(), app] as const),
  );
  const mcpPresetsByName = new Map(
    mcpPresets
      .filter((preset) => preset.installed && preset.configured)
      .map((preset) => [preset.name.toLowerCase(), preset] as const),
  );
  const filesByToken = new Map(files.map((file) => [mentionToken(file), file] as const));
  if (cliAppsByName.size === 0 && mcpPresetsByName.size === 0 && filesByToken.size === 0) {
    return [{ kind: "text", text: value }];
  }

  const segments: CapabilityMentionSegment[] = [];
  const mentionRe = new RegExp(MENTION_TOKEN_RE.source, MENTION_TOKEN_RE.flags);
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = mentionRe.exec(value)) !== null) {
    const prefix = match[1] ?? "";
    const resolved = resolveMentionToken(
      match[2] ?? "",
      cliAppsByName,
      mcpPresetsByName,
      filesByToken,
    );
    if (!resolved) continue;

    const mentionStart = match.index + prefix.length;
    const mentionEnd = mentionStart + resolved.token.length + 1;
    if (mentionStart > cursor) {
      segments.push({ kind: "text", text: value.slice(cursor, mentionStart) });
    }
    const text = value.slice(mentionStart, mentionEnd);
    if (resolved.kind === "cli") {
      segments.push({ kind: "cli", text, app: resolved.app });
    } else if (resolved.kind === "mcp") {
      segments.push({ kind: "mcp", text, preset: resolved.preset });
    } else {
      segments.push({ kind: "file", text, file: resolved.file });
    }
    cursor = mentionEnd;
    // A path mention can end mid-token after punctuation was trimmed, so the
    // scanner is repositioned rather than left where the match ended.
    mentionRe.lastIndex = mentionEnd;
  }
  if (cursor < value.length) {
    segments.push({ kind: "text", text: value.slice(cursor) });
  }
  return segments.length ? segments : [{ kind: "text", text: value }];
}
