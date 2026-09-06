import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchMarketingQAReport,
  fetchMarketingQAReports,
  overrideMarketingQAReport,
  runMarketingQA,
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

function headerBody(headers: Record<string, string>): unknown {
  const encoded = Object.entries(headers)
    .filter(([key]) => key.startsWith("X-Navin-File-Body-"))
    .sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true }))
    .map(([, value]) => value)
    .join("");
  return JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
}

describe("Marketing visual QA API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("loads recent reports and detail in the selected workspace", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 0, reports: [] }))
      .mockResolvedValueOnce(jsonResponse({ id: "qa-1", findings: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchMarketingQAReports("token", "/workspace/brand", "http://local");
    await fetchMarketingQAReport(
      "token",
      "qa-1",
      "/workspace/brand",
      "http://local",
    );

    expect(fetchMock.mock.calls[0][0]).toContain(
      "/api/webui/marketing-qa/reports?path=%2Fworkspace%2Fbrand",
    );
    expect(fetchMock.mock.calls[1][0]).toContain(
      "/api/webui/marketing-qa/reports/qa-1?path=%2Fworkspace%2Fbrand",
    );
  });

  it("sends a real QA run and audited override through gateway headers", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ id: "qa-1", findings: [] }))
      .mockResolvedValueOnce(jsonResponse({ id: "qa-1", effective_verdict: "PASS" }));
    vi.stubGlobal("fetch", fetchMock);

    await runMarketingQA(
      "token",
      {
        candidate: "marketing/candidate.png",
        references: ["brand/reference.png"],
        claims: ["product_fidelity"],
      },
      "/workspace/brand",
      "http://local",
    );
    await overrideMarketingQAReport(
      "token",
      "qa-1",
      "Brand owner approved the crop.",
      "PASS",
      "/workspace/brand",
      "http://local",
    );

    const [runUrl, runInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(runUrl).toContain("/api/webui/marketing-qa/run?");
    expect(runInit.method).toBeUndefined();
    expect(headerBody(runInit.headers as Record<string, string>)).toEqual({
      candidate: "marketing/candidate.png",
      references: ["brand/reference.png"],
      claims: ["product_fidelity"],
    });

    const [overrideUrl, overrideInit] = fetchMock.mock.calls[1] as [
      string,
      RequestInit,
    ];
    expect(overrideUrl).toContain("/reports/qa-1/override?");
    expect(headerBody(overrideInit.headers as Record<string, string>)).toEqual({
      reason: "Brand owner approved the crop.",
      verdict: "PASS",
    });
  });
});
