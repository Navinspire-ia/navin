// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import { meetingStoreApi } from "@/lib/api";

function response(payload: unknown = {}): Response {
  return {
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => payload,
  } as unknown as Response;
}

describe("meeting API actions", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("routes persistence, search, migration and audio through meeting modes", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      response({ meetings: [], segments: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await meetingStoreApi.list("token", "session");
    await meetingStoreApi.search("token", "session", "roadmap");
    await meetingStoreApi.migrateV2("token", "session", []);
    await meetingStoreApi.listAudio("token", "session", "one");
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/sessions/session/meeting?mode=store_list",
      "/api/sessions/session/meeting?mode=search",
      "/api/sessions/session/meeting?mode=migrate",
      "/api/sessions/session/meeting?mode=audio",
    ]);
  });

  it("routes translation, cleanup, DOCX and calendar actions", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => response({}));
    vi.stubGlobal("fetch", fetchMock);
    await meetingStoreApi.translate(
      "token", null, "transcript", "hello", "French",
    );
    await meetingStoreApi.cleanup("token", null, "hello", "en");
    await meetingStoreApi.docx("token", null, {
      title: "Weekly",
      report: "",
      transcript: "hello",
      notes: "",
    });
    await meetingStoreApi.calendarSync("token", null, "google", []);
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/meeting?mode=translate_transcript",
      "/api/meeting?mode=cleanup",
      "/api/meeting?mode=docx",
      "/api/meeting?mode=calendar_sync",
    ]);
  });
});
