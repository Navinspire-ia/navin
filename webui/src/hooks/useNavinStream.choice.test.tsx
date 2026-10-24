// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "@/i18n";
import { InteractionEcho } from "@/components/thread/InteractionEcho";
import { TurnWorkingStatus } from "@/components/thread/TurnWorkingStatus";
import { useNavinStream } from "@/hooks/useNavinStream";
import { toPendingChoice, type PendingChoice } from "@/lib/choices";
import type { NavinClient } from "@/lib/navin-client";
import type { InboundEvent } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: true, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const CHOICE_FRAME = {
  request_id: "r1",
  question: "Where do we start?",
  options: [
    { id: "a", label: "Full phase", recommended: true },
    { id: "b", label: "Profiler only" },
  ],
  allow_skip: true,
  recommended_id: "a",
};

function setup() {
  let chatEvent: (event: InboundEvent) => void = () => {};
  let pending: PendingChoice[] = [];
  const sendChoiceAnswer = vi.fn();
  const client = {
    status: "open",
    onError: () => () => {},
    onStatus: () => () => {},
    onChat: (_id: string, handler: typeof chatEvent) => { chatEvent = handler; return () => {}; },
    getRunStartedAt: () => null,
    getGoalState: () => undefined,
    getPendingApprovals: () => [],
    getPendingChoices: () => pending,
    sendMessage: vi.fn(),
    sendChoiceAnswer,
  } as unknown as NavinClient;
  function Chat() {
    const turn = useNavinStream("test");
    return <>
      <button onClick={() => turn.send("Plan")}>Start</button>
      <button onClick={() => turn.respondToChoice("r1", "b")}>Answer</button>
      <TurnWorkingStatus
        active={turn.isStreaming}
        startedAt={turn.runStartedAt}
        activityText={turn.activityText}
        thinking={turn.isThinking}
      />
      {turn.messages.map((message) => message.interaction
        ? <InteractionEcho key={message.id} echo={message.interaction} />
        : null)}
    </>;
  }
  render(<ClientProvider client={client} token=""><Chat /></ClientProvider>);
  return {
    event: (event: InboundEvent) => act(() => chatEvent(event)),
    setPending: (next: PendingChoice[]) => { pending = next; },
    sendChoiceAnswer,
  };
}

describe("ask_user exchange stays in the chat", () => {
  it("closes Thinking on the question and restates the answer after the click", () => {
    const app = setup();
    fireEvent.click(screen.getByText("Start"));
    app.event({ event: "reasoning_delta", chat_id: "test", text: "Which path?" });
    act(() => { vi.advanceTimersByTime(50); });
    expect(screen.getByRole("status").getAttribute("aria-label")).toBe("Working…");

    app.setPending([toPendingChoice(CHOICE_FRAME, Date.now())]);
    app.event({ event: "choice_request", chat_id: "test", ...CHOICE_FRAME });
    expect(screen.getByRole("status").getAttribute("aria-label")).toBe("Working");

    fireEvent.click(screen.getByText("Answer"));
    expect(app.sendChoiceAnswer).toHaveBeenCalledWith("r1", "b", false, "");
    expect(screen.getByTestId("choice-echo").textContent).toContain("Where do we start?");
    expect(screen.getByTestId("choice-echo-answer").textContent).toBe("Profiler only");

    // The backend close frame updates the same row, it does not add another.
    app.setPending([]);
    app.event({
      event: "choice_closed", chat_id: "test", request_id: "r1", option_id: "b",
      question: "Where do we start?", label: "Profiler only",
    });
    expect(screen.getAllByTestId("choice-echo")).toHaveLength(1);
    expect(screen.getByRole("status").getAttribute("aria-label")).toBe("Working");
  });

  it("restates a timeout from the close frame alone", () => {
    const app = setup();
    fireEvent.click(screen.getByText("Start"));
    app.event({
      event: "choice_closed", chat_id: "test", request_id: "r2", option_id: "a",
      question: "Ship now?", label: "Yes", skipped: true, timed_out: true,
    });
    expect(screen.getByTestId("choice-echo-answer").textContent).toBe(
      "No answer in time - used recommended: Yes",
    );
  });
});
