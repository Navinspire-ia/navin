// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchMarketplaceSkills, marketplaceBaseUrl } from "./marketplace";

describe("marketplace client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("normalizes base url", () => {
    expect(marketplaceBaseUrl("https://test-01.hp.navin.live/")).toBe(
      "https://test-01.hp.navin.live",
    );
  });

  it("soft-fails on network error instead of throwing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const result = await fetchMarketplaceSkills("https://test-01.hp.navin.live");
    expect(result.skills).toEqual([]);
    expect(result.errorCode).toBe("unavailable");
  });

  it("maps http errors without throwing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("nope", { status: 404 })),
    );
    const result = await fetchMarketplaceSkills("https://navin.live");
    expect(result.skills).toEqual([]);
    expect(result.errorCode).toBe("http_error");
    expect(result.status).toBe(404);
  });
});
