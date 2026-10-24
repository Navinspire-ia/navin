// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import i18n from "@/i18n";
import type { ToolProgressEvent } from "@/lib/types";

const TARGET_MAX_CHARS = 72;

function compactToolName(name: string | undefined): string {
  return (name || "").toLowerCase().split(".").pop() || "";
}

function toolArguments(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw !== "string" || !raw.trim().startsWith("{")) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function firstString(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

function shortTarget(value: string): string {
  const line = value.replace(/\s+/g, " ").trim();
  return line.length > TARGET_MAX_CHARS ? `${line.slice(0, TARGET_MAX_CHARS - 3).trimEnd()}...` : line;
}

function fileName(path: string): string {
  const clean = path.trim().replace(/[\\/]+$/, "");
  return clean.split(/[\\/]/).pop() || clean;
}

/**
 * One line for the composer status while a tool runs ("Running python3 x.py",
 * "Reading app.ts"). Null when the event is not a running phase.
 */
export function liveToolActivityLabel(event: ToolProgressEvent): string | null {
  if (event.phase !== "start" && event.phase !== "output") return null;
  if (typeof event.label === "string" && event.label.trim()) return shortTarget(event.label);
  const name = compactToolName(event.name);
  if (!name) return null;
  const args = toolArguments(event.arguments);
  const t = i18n.t.bind(i18n);
  if (/(^|_)(exec|shell|bash|run_command|terminal)($|_)/.test(name)) {
    const command = firstString(args, ["command", "cmd", "script"]);
    return command
      ? t("thread.liveActivity.running", { target: shortTarget(command), defaultValue: "Running {{target}}" })
      : t("thread.liveActivity.runningCommand", { defaultValue: "Running a command" });
  }
  if (name.includes("search")) {
    const query = firstString(args, ["query", "q", "pattern"]);
    return query
      ? t("thread.liveActivity.searching", { target: shortTarget(query), defaultValue: "Searching {{target}}" })
      : t("thread.liveActivity.searchingGeneric", { defaultValue: "Searching" });
  }
  const path = firstString(args, ["path", "file_path", "file", "url"]);
  if (/^(read_file|read|view|cat|web_fetch|fetch|scrape)$/.test(name)) {
    return path
      ? t("thread.liveActivity.reading", { target: shortTarget(fileName(path)), defaultValue: "Reading {{target}}" })
      : t("thread.liveActivity.readingGeneric", { defaultValue: "Reading" });
  }
  if (/^(write_file|edit_file|apply_patch|edit|write)$/.test(name)) {
    return path
      ? t("thread.liveActivity.editing", { target: shortTarget(fileName(path)), defaultValue: "Editing {{target}}" })
      : t("thread.liveActivity.editingGeneric", { defaultValue: "Editing files" });
  }
  if (name === "ask_user") {
    return t("thread.liveActivity.waitingForAnswer", { defaultValue: "Waiting for your answer" });
  }
  return t("thread.liveActivity.using", {
    target: name.replace(/_/g, " "),
    defaultValue: "Using {{target}}",
  });
}

/**
 * The composer status for a batch of tool events: the newest running step.
 * Walks the batch from the end and stops at the first label. With parallel
 * tools, a call that already ended later in the batch is skipped in favour of
 * one still running; only when none is running does the newest label win.
 */
export function liveToolActivityLabelFor(events: readonly ToolProgressEvent[]): string | null {
  const ended = new Set<string>();
  let fallback: string | null = null;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.phase === "end" || event.phase === "error") {
      if (event.call_id) ended.add(event.call_id);
      continue;
    }
    const label = liveToolActivityLabel(event);
    if (!label) continue;
    if (!event.call_id || !ended.has(event.call_id)) return label;
    fallback ??= label;
  }
  return fallback;
}
