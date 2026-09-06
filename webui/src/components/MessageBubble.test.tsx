import { describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";

type MemoComponent = { $$typeof: symbol; type: unknown };

describe("MessageBubble memo", () => {
  it("is a React.memo wrapper so scroll ticks skip unchanged bubbles", () => {
    const wrapped = MessageBubble as unknown as MemoComponent;
    expect(wrapped.$$typeof).toBe(Symbol.for("react.memo"));
    expect(typeof wrapped.type).toBe("function");
  });
});
