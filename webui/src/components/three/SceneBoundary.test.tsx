// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SceneBoundary } from "./SceneBoundary";

afterEach(cleanup);

function NoWebGL(): never {
  throw new Error("THREE.WebGLRenderer: Error creating WebGL context.");
}

const SCENES = [
  "crm/CrmPipelineScene.tsx",
  "studio/trading/TradingScene.tsx",
  "studio/leads/LeadsScene.tsx",
  "studio/career/CareerScene.tsx",
  "studio/tenders/TendersScene.tsx",
  "studio/marketing/MarketingScene.tsx",
  "studio/MarketingQA.tsx",
  "dev/AgentDesktopScene.tsx",
  "montage/TimelineSpatialPreview.tsx",
];

describe("SceneBoundary", () => {
  it("keeps the page when a scene cannot get a WebGL context", () => {
    // React reports the caught error on console.error; the boundary logs it once.
    const reactLog = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    render(
      <main>
        <h1>Pipeline</h1>
        <SceneBoundary fallback={<p>no 3D</p>}>
          <NoWebGL />
        </SceneBoundary>
      </main>,
    );
    expect(screen.getByText("Pipeline")).toBeTruthy();
    expect(screen.getByText("no 3D")).toBeTruthy();
    expect(warn).toHaveBeenCalledWith("3D scene disabled:", expect.any(Error));
    reactLog.mockRestore();
    warn.mockRestore();
  });

  it("renders the scene when nothing fails", () => {
    render(
      <SceneBoundary>
        <span>scene</span>
      </SceneBoundary>,
    );
    expect(screen.getByText("scene")).toBeTruthy();
  });

  it.each(SCENES)("%s: guarded, and no HDR fetched from the network", (file) => {
    const source = readFileSync(resolve(__dirname, "..", file), "utf8");
    expect(source).toContain("<SceneBoundary");
    // drei presets download an HDR from raw.githack.com: offline, the scene
    // waited on it forever. StudioEnvironment lights the scene locally.
    expect(source).not.toMatch(/<Environment\s+preset=/);
  });
});
