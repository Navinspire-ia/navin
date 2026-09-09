// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createTimeline,
} from "@/components/montage/timelineModel";
import {
  listMontageTimelines,
  previewMontageTimeline,
  renderMontageTimeline,
  saveMontageTimeline,
} from "./api";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

describe("Montage timeline API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists timelines with workspace context and auth", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ timelines: [], count: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    await listMontageTimelines(
      "token",
      { sessionKey: "websocket:chat", path: "/workspace/demo" },
      "http://local",
    );
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/webui/montage/timelines?");
    expect(url).toContain("session_key=websocket%3Achat");
    expect(url).toContain("path=%2Fworkspace%2Fdemo");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer token");
  });

  it("sends timeline JSON through gateway body headers", async () => {
    const timeline = createTimeline("launch");
    timeline.visuals.push({
      path: "media/intro.mp4",
      kind: "video",
      duration: null,
      start: 0,
      end: 4,
    });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(timeline));
    vi.stubGlobal("fetch", fetchMock);
    await saveMontageTimeline("token", timeline, undefined, "http://local");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://local/api/webui/montage/timelines/launch/put");
    expect(Object.keys(init.headers as object)).toContain("X-Navin-File-Body-0");
  });

  it("uses real preview and render routes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ ok: true, path: "preview.jpg" }))
      .mockResolvedValueOnce(jsonResponse({ status: "completed" }));
    vi.stubGlobal("fetch", fetchMock);
    await previewMontageTimeline("token", "launch", undefined, "http://local");
    await renderMontageTimeline(
      "token",
      "launch",
      { output: "marketing/montage/exports/launch.mp4" },
      "http://local",
    );
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://local/api/webui/montage/timelines/launch/preview",
    );
    expect(fetchMock.mock.calls[1][0]).toContain(
      "/api/webui/montage/timelines/launch/render?output=",
    );
  });
});
