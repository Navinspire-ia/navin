import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getNoteHistorySnapshot,
  importNotesVault,
  listNoteHistory,
  rebuildNotesIndex,
  searchNotes,
} from "./notes-api";

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Notes API client", () => {
  it("uses the full-content search route", async () => {
    const request = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ results: [] }));
    await searchNotes("token", "prix juillet", 30);
    expect(String(request.mock.calls[0]?.[0])).toContain(
      "/api/notes/search?q=prix+juillet&limit=30",
    );
  });

  it("calls history list and preview routes", async () => {
    const request = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ snapshots: [] }))
      .mockResolvedValueOnce(
        jsonResponse({ stamp: "20260823T000000", title: "A", markdown: "body" }),
      );
    await listNoteHistory("token", "note-1");
    await getNoteHistorySnapshot("token", "note-1", "20260823T000000");
    expect(String(request.mock.calls[0]?.[0])).toContain("/api/notes/history?id=note-1");
    expect(String(request.mock.calls[1]?.[0])).toContain("/api/notes/history/get");
  });

  it("calls import and rebuild routes", async () => {
    const request = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse({
          ok: true,
          imported: 2,
          skipped: 0,
          overwritten: 0,
          indexed: 2,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ ok: true, manifest: { total: 2 }, search: { total: 2 } }),
      );
    await importNotesVault("token", "/vault/demo", "rename");
    await rebuildNotesIndex("token");
    expect(String(request.mock.calls[0]?.[0])).toContain(
      "/api/notes/import?path=%2Fvault%2Fdemo&conflict=rename",
    );
    expect(String(request.mock.calls[1]?.[0])).toContain("/api/notes/rebuild");
  });
});
