// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The agent's run as the realtime voice must know it. The voice model is a
 * second mind next to the chat agent: without these lines it went on saying
 * the agent was working after the user pressed Stop (2026-11-04).
 */

export type AgentRunState = "idle" | "working" | "done" | "stopped";

export interface AgentRun {
  state: AgentRunState;
  /** The request the run works on, as it was sent to the chat. */
  request: string | null;
  /** Who sent it: the voice (ask_agent) or the user typing in the chat. */
  from: "voice" | "chat" | null;
}

export const IDLE_RUN: AgentRun = { state: "idle", request: null, from: null };

/** The engine's own replies to /stop (navin/command/builtin.py). */
const STOP_REPLIES = [
  /^Stopped \d+ task\(s\)\.$/,
  /^Stopped the active goal\.$/,
  /^Stopped the running plan\.$/,
  /^Nothing is running - everything is already stopped\.$/,
];

export function isStopReply(text: string): boolean {
  const clean = text.trim();
  return STOP_REPLIES.some((pattern) => pattern.test(clean));
}

/** The last thing the user asked in the chat, typed or sent by the voice. */
export function lastUserRequest(messages: ReadonlyArray<{ role: string; content: string }>): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== "user") continue;
    const text = message.content.trim();
    if (text) return text;
  }
  return null;
}

/** The same request, whatever spacing or case the chat kept. */
export function sameRequest(a: string | null, b: string | null): boolean {
  const key = (text: string | null) => (text ?? "").replace(/\s+/g, " ").trim().toLowerCase();
  return key(a) !== "" && key(a) === key(b);
}

const MAX_REQUEST_CHARS = 240;

function quoted(request: string | null): string {
  if (!request) return "";
  const clean = request.replace(/\s+/g, " ").trim();
  return `"${clean.length > MAX_REQUEST_CHARS ? `${clean.slice(0, MAX_REQUEST_CHARS).trimEnd()}...` : clean}"`;
}

/** One "[Navin agent] status:" line: the state that holds from now on. */
export function agentStatusLine(run: AgentRun): string {
  const request = quoted(run.request);
  switch (run.state) {
    case "working":
      return run.from === "chat"
        ? `status: the agent is working on a request the user typed in the chat${request ? `: ${request}` : ""}. Do not answer this line; use it when the user asks.`
        : `status: the agent is working${request ? ` on ${request}` : ""}.`;
    case "stopped":
      return `status: the agent was stopped by the user and nothing is running now${request ? `; ${request} is not finished` : ""}. Never say it is still working: say in one short sentence that it stopped, and ask what to do next.`;
    case "done":
      return `status: the agent finished${request ? ` ${request}` : ""} and is waiting for the next request.`;
    default:
      return "status: the agent is idle and waiting for a request.";
  }
}
