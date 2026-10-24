import { describe, expect, it } from "vitest";

import { redactSecrets } from "@/lib/redact-secrets";

describe("redactSecrets", () => {
  it("masks env assignments, bearer tokens and url keys", () => {
    expect(redactSecrets("API_KEY=abc123xyz")).toBe("API_KEY=[masque]");
    expect(redactSecrets("Authorization: Bearer abcdefghijklmnop")).toContain("Bearer [masque]");
    expect(redactSecrets("https://x.io/a?token=zzz&b=1")).toBe("https://x.io/a?token=[masque]&b=1");
  });
  it("leaves normal output alone", () => {
    expect(redactSecrets("158 passed in 4.8s")).toBe("158 passed in 4.8s");
  });
});
