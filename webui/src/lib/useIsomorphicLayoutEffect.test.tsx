// @vitest-environment node
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useIsomorphicLayoutEffect } from "./useIsomorphicLayoutEffect";

afterEach(() => vi.restoreAllMocks());

describe("useIsomorphicLayoutEffect", () => {
  it("does not emit the useLayoutEffect SSR warning during server rendering", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const effect = vi.fn();
    function Probe() {
      useIsomorphicLayoutEffect(effect, []);
      return createElement("span", null, "ok");
    }
    expect(renderToString(createElement(Probe))).toContain("ok");
    expect(effect).not.toHaveBeenCalled();
    const text = errors.mock.calls.map((c) => c.map(String).join(" ")).join("\n");
    expect(text).not.toMatch(/useLayoutEffect does nothing on the server/);
  });
});
