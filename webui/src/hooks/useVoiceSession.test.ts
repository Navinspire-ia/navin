import { describe, expect, it } from "vitest";

import {
  attachTtsAudio,
  shouldCancelTtsOnSpeech,
  takePlayableTts,
  type TtsQueueItem,
} from "./useVoiceSession";

const AUDIO = { base64: "AAAA", mime: "audio/mpeg" };

describe("shouldCancelTtsOnSpeech", () => {
  it("only cancels TTS when it is playing and the user speaks", () => {
    expect(shouldCancelTtsOnSpeech(true, true)).toBe(true);
    expect(shouldCancelTtsOnSpeech(true, false)).toBe(false);
    expect(shouldCancelTtsOnSpeech(false, true)).toBe(false);
  });
});

describe("TTS queue", () => {
  const queue: TtsQueueItem[] = [{ requestId: "r1" }, { requestId: "r2" }, { requestId: "r3" }];

  it("attaches audio to its request id and keeps order", () => {
    const next = attachTtsAudio(queue, "r2", AUDIO);
    expect(next.map((item) => item.requestId)).toEqual(["r1", "r2", "r3"]);
    expect(next[1].audio).toEqual(AUDIO);
    expect(next[0].audio).toBeUndefined();
  });

  it("falls back to the oldest waiting request when the id is missing", () => {
    const next = attachTtsAudio(queue, undefined, AUDIO);
    expect(next[0].audio).toEqual(AUDIO);
  });

  it("ignores audio for requests that were skipped", () => {
    expect(attachTtsAudio(queue, "gone", AUDIO)).toEqual(queue);
  });

  it("blocks on a head whose audio has not arrived, so chunks stay in order", () => {
    const withSecond = attachTtsAudio(queue, "r2", AUDIO);
    const { item, rest } = takePlayableTts(withSecond);
    expect(item).toBeNull();
    expect(rest).toHaveLength(3);
  });

  it("plays the head once its audio is there and drops failed heads", () => {
    const ready = attachTtsAudio(queue, "r1", AUDIO);
    const first = takePlayableTts(ready);
    expect(first.item?.requestId).toBe("r1");
    expect(first.rest.map((item) => item.requestId)).toEqual(["r2", "r3"]);

    const failedHead: TtsQueueItem[] = [
      { requestId: "r2", failed: true },
      { requestId: "r3", audio: AUDIO },
    ];
    const second = takePlayableTts(failedHead);
    expect(second.item?.requestId).toBe("r3");
    expect(second.rest).toEqual([]);
  });

  it("returns nothing for an empty queue", () => {
    expect(takePlayableTts([])).toEqual({ item: null, rest: [] });
  });
});
