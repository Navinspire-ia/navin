import { describe, expect, it } from "vitest";

import cases from "../../../tests/fixtures/media-models.json";
import { filterMediaModels, supportsMediaModel } from "./media-models";
import { canBeChatDefault } from "./model-modality";
import type { MediaModelKind, ProviderModelInfo } from "./types";

const kinds: MediaModelKind[] = ["stt", "tts", "image", "video", "music"];

describe("media models shown by settings", () => {
  for (const row of cases) {
    it(`filters ${row.id} using its output capability`, () => {
      const actual = kinds.filter((kind) => supportsMediaModel(row as ProviderModelInfo, kind)).sort();
      expect(actual).toEqual([...row.expected].sort());
    });
  }

  it("filters a mixed response even when an old gateway ignores the modality query", () => {
    for (const kind of kinds) {
      const rows = cases as ProviderModelInfo[];
      const expected = cases.filter((row) => row.expected.some((expectedKind) => expectedKind === kind)).map((row) => row.id);
      expect(filterMediaModels(rows, kind).map((row) => row.id)).toEqual(expected);
    }
  });

  it("keeps media models out of ordinary chat routes even if a saved preset lost its modality", () => {
    for (const id of ["qwen/qwen-audio-3.0-tts-flash", "qwen/qwen-image-3", "openai/sora-2-pro", "openai/whisper-1"])
      expect(canBeChatDefault("text", id)).toBe(false);
    expect(canBeChatDefault("text", "qwen/qwen3.8-max")).toBe(true);
  });
});
