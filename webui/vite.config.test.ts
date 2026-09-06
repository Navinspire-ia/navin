import { describe, expect, it } from "vitest";

import { gatewayTarget } from "./vite.config";

describe("gatewayTarget", () => {
  it("uses connectable IPv4 loopback for an IPv4 wildcard bind", () => {
    expect(gatewayTarget("0.0.0.0", 8765)).toBe("http://127.0.0.1:8765");
  });

  it("uses bracketed IPv6 loopback for an IPv6 wildcard bind", () => {
    expect(gatewayTarget("::", 8765)).toBe("http://[::1]:8765");
    expect(gatewayTarget("[2001:db8::1]", 8765)).toBe("http://[2001:db8::1]:8765");
  });
});
