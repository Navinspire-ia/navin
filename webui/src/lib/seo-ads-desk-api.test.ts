// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import { adsUrl, setAdsChangeStatus } from "./ads-desk-api";
import { runSeoAudit, saveSeoKeys, seoUrl } from "./seo-desk-api";

function decodeBody(init: RequestInit | undefined): unknown {
  const headers = (init?.headers ?? {}) as Record<string, string>;
  const chunks = Object.keys(headers)
    .filter((name) => name.startsWith("X-Navin-File-Body-"))
    .sort((a, b) => Number(a.split("-").pop()) - Number(b.split("-").pop()))
    .map((name) => headers[name]);
  const binary = atob(chunks.join(""));
  return JSON.parse(new TextDecoder().decode(Uint8Array.from(binary, (c) => c.charCodeAt(0))));
}

afterEach(() => vi.unstubAllGlobals());

describe("SEO and Ads desk api", () => {
  it("builds escaped action urls", () => {
    expect(seoUrl("audit.get")).toBe("/api/seo?action=audit.get");
    expect(adsUrl("changes.mark_applied")).toBe("/api/ads?action=changes.mark_applied");
  });

  it("sends JSON bodies as chunked headers, accents included", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    }));
    await runSeoAudit("tok", "https://café.test", 25);
    await saveSeoKeys("tok", { psi_api_key: "AIza" }, ["semrush_api_key"]);
    await setAdsChangeStatus("tok", "mark_applied", ["chg_1"], { proof: "Paused (vu à 10:42)" });
    expect(calls[0].url).toBe("/api/seo?action=pipeline");
    expect(decodeBody(calls[0].init)).toEqual({ url: "https://café.test", max_pages: 25 });
    expect(decodeBody(calls[1].init)).toEqual({ psi_api_key: "AIza", clear: ["semrush_api_key"] });
    expect(calls[2].url).toBe("/api/ads?action=changes.mark_applied");
    expect(decodeBody(calls[2].init)).toEqual({ ids: ["chg_1"], proof: "Paused (vu à 10:42)" });
  });
});
