import { describe, expect, it } from "vitest";

import type { UIMessage } from "../lib/types";
import { assistantSpeechCounts, claimSpeechKey } from "./useLiveVoice";

const strings = { codeOmitted: "code omitted", restInChat: "The rest is in the chat.", link: "link" };

function assistant(id: string, content: string, extra: Partial<UIMessage> = {}): UIMessage {
  return { id, role: "assistant", content, ...extra } as UIMessage;
}

describe("assistantSpeechCounts", () => {
  it("counts finished assistant messages by spoken text, ignoring traces and streaming", () => {
    const counts = assistantSpeechCounts(
      [
        assistant("a", "C'est fait."),
        assistant("b", "c'est   fait."),
        assistant("c", "Encore en cours", { isStreaming: true }),
        assistant("d", "trace", { kind: "trace" } as Partial<UIMessage>),
        { id: "u", role: "user", content: "C'est fait." } as UIMessage,
      ],
      strings,
    );
    expect(counts.get("c'est fait.")).toBe(2);
    expect(counts.size).toBe(1);
  });
});

describe("claimSpeechKey", () => {
  it("skips the same text under a new id (placeholder swap, history reload)", () => {
    const onScreen = new Map([["c'est fait.", 1]]);
    const spoken = new Map<string, number>();
    expect(claimSpeechKey(spoken, onScreen, "c'est fait.")).toBe(true);
    // The final message replaces the streaming placeholder: same text, new id.
    expect(claimSpeechKey(spoken, onScreen, "c'est fait.")).toBe(false);
    // Long after, the history reload renumbers everything: still silent.
    expect(claimSpeechKey(spoken, onScreen, "c'est fait.")).toBe(false);
  });

  it("reads a repeated short answer again when the transcript holds it once more", () => {
    const spoken = new Map<string, number>();
    expect(claimSpeechKey(spoken, new Map([["c'est fait.", 1]]), "c'est fait.")).toBe(true);
    expect(claimSpeechKey(spoken, new Map([["c'est fait.", 2]]), "c'est fait.")).toBe(true);
    expect(claimSpeechKey(spoken, new Map([["c'est fait.", 2]]), "c'est fait.")).toBe(false);
  });

  it("keeps history silent right after the call starts", () => {
    const history = [assistant("h1", "Un backend FastAPI."), assistant("h2", "Un backend FastAPI.")];
    const spoken = assistantSpeechCounts(history, strings);
    const onScreen = assistantSpeechCounts([...history, assistant("n", "Autre chose.")], strings);
    expect(claimSpeechKey(spoken, onScreen, "un backend fastapi.")).toBe(false);
    expect(claimSpeechKey(spoken, onScreen, "autre chose.")).toBe(true);
  });
});
