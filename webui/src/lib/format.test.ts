import { describe, expect, it } from "vitest";

import { deriveTitle, limitTitleWords } from "@/lib/format";

describe("chat titles stay at three words", () => {
  it("cuts a long title", () => {
    expect(limitTitleWords("one two three four five")).toBe("one two three");
  });
  it("keeps a short title", () => {
    expect(limitTitleWords("  one   two ")).toBe("one two");
  });
  it("derives the name of an untitled chat from its first message", () => {
    expect(deriveTitle("corrige le bug du sidebar", "new chat")).toBe("corrige le bug");
  });
});
