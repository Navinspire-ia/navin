import { describe, expect, it } from "vitest";

import { deriveTitle, limitTitleWords } from "@/lib/format";

describe("chat titles stay at five words at most", () => {
  it("cuts a long title", () => {
    expect(limitTitleWords("one two three four five six seven")).toBe("one two three four five");
  });
  it("keeps a title of five words or fewer", () => {
    expect(limitTitleWords("  one   two ")).toBe("one two");
    expect(limitTitleWords("one two three four five")).toBe("one two three four five");
  });
  it("derives the name of an untitled chat from its first message", () => {
    expect(deriveTitle("corrige le bug du sidebar gauche", "new chat")).toBe("corrige le bug du sidebar");
  });
});
