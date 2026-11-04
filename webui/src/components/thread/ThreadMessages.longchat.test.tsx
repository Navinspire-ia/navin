// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only
// @vitest-environment jsdom

import { memo, useRef } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { UIMessage } from "@/lib/types";

// Count row renders. The real components are memo() too, so the mocks keep
// the same bail-out rule: a row renders again only when its props change.
const renders = new Map<string, number>();
function bump(id: string) {
  renders.set(id, (renders.get(id) ?? 0) + 1);
}

vi.mock("@/components/MessageBubble", () => ({
  MessageBubble: memo(function MessageBubbleProbe({ message }: { message: UIMessage }) {
    bump(message.id);
    return <div data-probe="bubble">{message.content.length}</div>;
  }),
}));

vi.mock("@/components/thread/AgentActivityCluster", () => ({
  AgentActivityCluster: memo(function AgentActivityClusterProbe({ messages }: { messages: UIMessage[] }) {
    bump(`activity:${messages[0]?.id ?? "none"}`);
    return <div data-probe="activity">{messages.length}</div>;
  }),
}));

import { ThreadMessages } from "./ThreadMessages";

const ANSWER = [
  "Here is the change, with the reasoning behind it.",
  "",
  "```ts",
  "export function add(a: number, b: number): number {",
  "  return a + b;",
  "}",
  "```",
  "",
  "- first point with `inline code`",
  "- second point with **bold** text",
].join("\n");

/** 1500 messages in turns of 5: user, reasoning, tool trace, tool trace, answer. */
function longThread(): UIMessage[] {
  const out: UIMessage[] = [];
  for (let turn = 0; out.length < 1500; turn += 1) {
    const turnId = `t${turn}`;
    const at = 1_700_000_000_000 + turn * 60_000;
    out.push({ id: `u${turn}`, role: "user", content: `Question ${turn}`, createdAt: at, turnId } as UIMessage);
    out.push({
      id: `r${turn}`,
      role: "assistant",
      content: "",
      reasoning: "Thinking about the files to read.",
      createdAt: at + 1000,
      turnId,
    } as UIMessage);
    for (const step of [0, 1]) {
      out.push({
        id: `tr${turn}-${step}`,
        role: "assistant",
        kind: "trace",
        content: `read_file src/file${step}.ts`,
        traces: [`read_file src/file${step}.ts`],
        toolEvents: [{ name: "read_file", phase: "end" }],
        createdAt: at + 2000 + step,
        turnId,
      } as unknown as UIMessage);
    }
    out.push({
      id: `a${turn}`,
      role: "assistant",
      content: `${ANSWER}\n\nTurn ${turn}.`,
      // An answer that also carries reasoning: the timeline splits it in two.
      reasoning: turn % 2 === 0 ? "Short check before answering." : undefined,
      createdAt: at + 5000,
      latencyMs: 5000,
      turnId,
    } as UIMessage);
  }
  return out;
}

/** A scroll parent parked at the bottom, where a reader sits while a reply streams. */
function bottomScroller(): HTMLDivElement {
  const el = document.createElement("div");
  Object.defineProperty(el, "clientHeight", { configurable: true, value: 800 });
  Object.defineProperty(el, "scrollTop", { configurable: true, value: 10_000_000 });
  return el;
}

function Harness({ messages, windowed }: { messages: UIMessage[]; windowed: boolean }) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  if (scrollRef.current === null) scrollRef.current = bottomScroller();
  return (
    <div>
      <ThreadMessages
        messages={messages}
        isStreaming
        scrollParentRef={windowed ? scrollRef : undefined}
      />
    </div>
  );
}

/** Same update the stream hook makes per delta: copy the array, replace one row. */
function withDelta(prev: UIMessage[], index: number, chunk: string): UIMessage[] {
  const next = prev.slice();
  next[index] = { ...prev[index], content: prev[index].content + chunk, isStreaming: true };
  return next;
}

function streamInto(windowed: boolean) {
  renders.clear();
  let messages = longThread();
  messages = [
    ...messages,
    { id: "live-user", role: "user", content: "Go on", createdAt: 1_800_000_000_000, turnId: "live" } as UIMessage,
    { id: "live", role: "assistant", content: "Sure, ", isStreaming: true, createdAt: 1_800_000_001_000, turnId: "live" } as UIMessage,
  ];
  const view = render(<Harness messages={messages} windowed={windowed} />);
  const mountedRows = renders.size;
  renders.clear();

  const liveIndex = messages.length - 1;
  const started = performance.now();
  for (let i = 0; i < 300; i += 1) {
    messages = withDelta(messages, liveIndex, i % 40 === 39 ? "\n\n```ts\nconst x = 1;\n```\n" : "word ");
    act(() => {
      view.rerender(<Harness messages={messages} windowed={windowed} />);
    });
  }
  const msPerDelta = (performance.now() - started) / 300;
  const staleRenders = [...renders.entries()]
    .filter(([id]) => id !== "live")
    .reduce((sum, [, count]) => sum + count, 0);
  // Printed so the cost per delta can be compared between runs.
  console.info(
    `[long-chat] windowed=${windowed} mountedRows=${mountedRows} `
      + `unchangedRowRenders=${staleRenders} liveRenders=${renders.get("live") ?? 0} `
      + `msPerDelta=${msPerDelta.toFixed(2)}`,
  );
  return { staleRenders, liveRenders: renders.get("live") ?? 0 };
}

afterEach(() => {
  cleanup();
  renders.clear();
});

describe("ThreadMessages on a 1500-message chat", () => {
  it("re-renders only the streaming row for each of 300 deltas", () => {
    const windowed = streamInto(true);
    const full = streamInto(false);
    expect(windowed.liveRenders).toBe(300);
    expect(windowed.staleRenders).toBe(0);
    expect(full.liveRenders).toBe(300);
    expect(full.staleRenders).toBe(0);
  }, 120_000);
});
