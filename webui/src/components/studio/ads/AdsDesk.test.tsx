// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchAdsSnapshot: vi.fn(),
  fetchAdsChanges: vi.fn(),
  analyzeAds: vi.fn(),
  ingestAdsExport: vi.fn(),
  deleteAdsExport: vi.fn(),
  toggleAdsExport: vi.fn(),
  setAdsChangeStatus: vi.fn(),
  exportAdsChanges: vi.fn(),
  exportAdsReport: vi.fn(),
  fileToBase64: vi.fn(async () => "Q2FtcGFpZ24="),
}));
vi.mock("@/lib/ads-desk-api", () => api);
vi.mock("@/lib/seo-desk-api", () => ({ downloadDeskFile: vi.fn() }));

import { AdsDesk } from "./AdsDesk";

const metrics = { impressions: 14400, clicks: 175, cost: 1010.5, conversions: 13, conversion_value: 1890, ctr: 0.012, cpa: 77.7, roas: 1.87 };
const analysis = {
  platforms: ["google"], currency: "EUR", period: {}, row_count: 3, totals: metrics,
  entities: { campaign: { count: 1, top_by_spend: [{ level: "campaign", key: "Generic", platform: "google", campaign: "Generic", metrics }] } },
  findings: [{ code: "wasted", category: "waste", severity: "high", title: "Spend without conversions", message: "Shoes spent 620 EUR, 0 conversions.", platform: "google", level: "ad_group", entity: {}, impact: { cost: 620 }, recommendation: "Pause or fix targeting.", change_ids: ["chg_1"] }],
  data_gaps: [], health: { score: 58, grade: "D", finding_count: 1, change_count: 1, wasted_cost: 620, waste_share: 0.61, estimated_monthly_savings: 620, counts: {} },
  summary: [], analyzed_at: "2026-10-19T10:00:00Z", export_ids: ["e1"],
};
const change = { id: "chg_1", platform: "google", action: "pause", level: "ad_group", target: { campaign: "Generic", ad_group: "Shoes" }, params: {}, rationale: "No conversion on 620 EUR.", finding_code: "wasted", estimated_monthly_savings: 620, status: "approved" as const };

afterEach(() => cleanup());

describe("AdsDesk", () => {
  it("shows KPIs, findings and requires a read-back to mark a change applied", async () => {
    api.fetchAdsSnapshot.mockResolvedValue({
      exports: [{ id: "e1", filename: "google.csv", platform: "google", rows: 3, levels: ["ad_group"], unknown_columns: [], warnings: [], uploaded_at: "", active: true }],
      analysis, changes: { count: 1, by_status: { approved: 1 } }, settings: { currency: "EUR", monthly_budget: 1500 },
    });
    api.fetchAdsChanges.mockResolvedValue({ changes: [change], count: 1, by_status: { approved: 1 } });
    api.setAdsChangeStatus.mockResolvedValue({ changes: [{ ...change, status: "applied", applied_proof: "Paused at 10:42" }], count: 1, by_status: { applied: 1 } });
    render(<AdsDesk token="t" />);
    expect(await screen.findByText("Spend without conversions")).toBeTruthy();
    expect(screen.getByText("google.csv")).toBeTruthy();
    expect(screen.getByRole("img", { name: /Account health: 58/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Mark applied" }));
    const confirm = await screen.findAllByRole("button", { name: "Mark applied" });
    const dialogConfirm = confirm[confirm.length - 1] as HTMLButtonElement;
    expect(dialogConfirm.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText(/Campaign 'Generic'/), { target: { value: "Paused at 10:42" } });
    fireEvent.click(dialogConfirm);
    await waitFor(() => expect(api.setAdsChangeStatus).toHaveBeenCalledWith("t", "mark_applied", ["chg_1"], { proof: "Paused at 10:42" }));
    expect(await screen.findByText(/Paused at 10:42/)).toBeTruthy();
  });
});
