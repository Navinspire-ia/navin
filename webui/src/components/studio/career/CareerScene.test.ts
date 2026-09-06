import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@react-three/fiber", () => ({
  Canvas: ({ children }: { children?: unknown }) => createElement("div", { "data-testid": "career-canvas" }, children as never),
  useFrame: () => undefined,
}));

vi.mock("@react-three/drei", () => ({
  ContactShadows: () => null,
  Environment: () => null,
  OrbitControls: () => null,
}));

import { CareerScene } from "@/components/studio/career/CareerScene";

describe("career 3d scene", () => {
  it("exposes a labeled graphic, not a wallpaper", () => {
    const html = renderToStaticMarkup(
      createElement(CareerScene, {
        matchRatio: 0.8,
        active: true,
        label: "Three dimensional briefcase for the career pipeline",
      }),
    );
    expect(html).toContain("role=\"img\"");
    expect(html).toContain("Three dimensional briefcase for the career pipeline");
    expect(html).toContain("career-canvas");
  });
});
