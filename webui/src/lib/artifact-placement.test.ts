import { describe, expect, it } from "vitest";

import { resolveArtifactPlacement } from "./artifact-placement";

describe("resolveArtifactPlacement", () => {
  it("renders in the workbench whenever there is one", () => {
    expect(
      resolveArtifactPlacement({ open: true, artifactCount: 1, hasWorkbenchHost: true }),
    ).toBe("workbench");
  });

  it("never takes the chat column when a workbench exists", () => {
    // The regression: a Three.js scene rendered beside the messages and
    // squeezed them to zero width.
    const placement = resolveArtifactPlacement({
      open: true,
      artifactCount: 3,
      hasWorkbenchHost: true,
    });
    expect(placement).not.toBe("chat-side");
  });

  it("falls back to the side panel in the plain chat view", () => {
    expect(
      resolveArtifactPlacement({ open: true, artifactCount: 1, hasWorkbenchHost: false }),
    ).toBe("chat-side");
  });

  it("stays hidden while closed", () => {
    expect(
      resolveArtifactPlacement({ open: false, artifactCount: 2, hasWorkbenchHost: true }),
    ).toBe("hidden");
  });

  it("stays hidden with nothing to show", () => {
    expect(
      resolveArtifactPlacement({ open: true, artifactCount: 0, hasWorkbenchHost: true }),
    ).toBe("hidden");
  });

  it("is hidden rather than empty when both conditions fail", () => {
    expect(
      resolveArtifactPlacement({ open: false, artifactCount: 0, hasWorkbenchHost: false }),
    ).toBe("hidden");
  });
});
