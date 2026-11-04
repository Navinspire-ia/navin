// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { agentStatusLine, isStopReply, lastUserRequest, sameRequest } from "./live-agent-status";

describe("the agent's state for the realtime voice", () => {
  it("recognizes the engine's replies to /stop, and only those", () => {
    expect(isStopReply("Stopped 1 task(s).")).toBe(true);
    expect(isStopReply("Stopped 2 task(s).")).toBe(true);
    expect(isStopReply(" Stopped the active goal. ")).toBe(true);
    expect(isStopReply("Stopped the running plan.")).toBe(true);
    expect(isStopReply("Nothing is running - everything is already stopped.")).toBe(true);
    expect(isStopReply("Stopped 1 task(s). Then I fixed the build.")).toBe(false);
    expect(isStopReply("The tests stopped at step 3.")).toBe(false);
  });

  it("tells the voice a stopped run is over, with the request left unfinished", () => {
    const line = agentStatusLine({ state: "stopped", request: "Cree le projet chatbot", from: "voice" });
    expect(line).toContain("stopped by the user");
    expect(line).toContain("nothing is running now");
    expect(line).toContain('"Cree le projet chatbot" is not finished');
    expect(line).toContain("Never say it is still working");
  });

  it("names the request the user typed in the chat during the call", () => {
    const messages = [
      { role: "user", content: "/forge Marc t es la" },
      { role: "assistant", content: "Oui" },
      { role: "user", content: "  Lance les tests  " },
    ];
    expect(lastUserRequest(messages)).toBe("Lance les tests");
    expect(lastUserRequest([{ role: "assistant", content: "x" }])).toBeNull();
    expect(sameRequest("Lance  les tests", "lance les tests ")).toBe(true);
    expect(sameRequest(null, null)).toBe(false);
    expect(agentStatusLine({ state: "working", request: "Lance les tests", from: "chat" })).toBe(
      'status: the agent is working on a request the user typed in the chat: "Lance les tests". Do not answer this line; use it when the user asks.',
    );
  });
});
