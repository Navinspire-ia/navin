// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchSeoSnapshot: vi.fn(),
  fetchSeoAudit: vi.fn(),
  runSeoAudit: vi.fn(),
  fetchSerpHistory: vi.fn(async () => ({ snapshots: [] })),
  takeSerpSnapshot: vi.fn(),
  saveSeoKeys: vi.fn(),
  deleteSeoSite: vi.fn(),
  exportSeoReport: vi.fn(),
  downloadDeskFile: vi.fn(),
}));
vi.mock("@/lib/seo-desk-api", () => api);

import { SeoDesk, rankOf } from "./SeoDesk";

const keys = {
  psi_api_key: { configured: false, source: "", hint: "" },
  crux_api_key: { configured: false, source: "", hint: "" },
  dataforseo_login: { configured: false, source: "", hint: "" },
  dataforseo_password: { configured: false, source: "", hint: "" },
  semrush_api_key: { configured: false, source: "", hint: "" },
  psi_ready: false,
  crux_ready: false,
  serp_ready: false,
};
const entry = { id: "a1", at: "2026-10-19T10:00:00Z", url: "https://shop.test/", score: 72, grade: "C", findings: 2, counts: { high: 1, low: 1 }, pages: 3, performance_score: null, duration_s: 4 };
const report = {
  health: { score: 72, grade: "C", page_count: 3, finding_count: 2, counts: { high: 1, low: 1 }, penalties: {} },
  findings: [
    { code: "sitemap_unavailable", category: "indexability", severity: "high", title: "Sitemap Unavailable", message: "No sitemap answered.", url: "https://shop.test/sitemap.xml", recommendation: "Publish valid sitemap XML.", evidence: [{ kind: "http_status", value: 404, source: "x" }] },
    { code: "meta_short", category: "onpage", severity: "low", title: "Meta description too short", message: "40 characters.", url: "https://shop.test/", recommendation: "Write 120 to 160 characters.", evidence: [{ kind: "length", value: 40, source: "x" }] },
  ],
  pages: [{ url: "https://shop.test/", status: 200, title: "Shop" }],
  data_gaps: [{ service: "psi_v5", reason: "Missing optional credential" }],
  metadata: { performance: { psi: { status: "data_gap", service: "psi_v5" }, crux: { status: "data_gap", service: "crux_v1" } } },
};

afterEach(() => cleanup());

describe("SeoDesk", () => {
  it("runs an audit, shows the score, filters findings and explains missing Core Web Vitals", async () => {
    api.fetchSeoSnapshot
      .mockResolvedValueOnce({ sites: [], keys, serp_keywords: [], limits: { max_pages: 50, max_depth: 2 } })
      .mockResolvedValue({ sites: [{ site: "shop.test", url: "https://shop.test/", audits: [entry], last_audit_id: "a1", last_audit_at: entry.at, last_score: 72, last_grade: "C" }], keys, serp_keywords: [], limits: { max_pages: 50, max_depth: 2 } });
    api.runSeoAudit.mockResolvedValue({ site: "shop.test", audit_id: "a1", entry, report });
    api.fetchSeoAudit.mockResolvedValue({ site: "shop.test", audit_id: "a1", report });
    render(<SeoDesk token="t" />);
    await screen.findByText("No audit yet");
    fireEvent.change(screen.getByLabelText("Site URL"), { target: { value: "shop.test" } });
    fireEvent.click(screen.getByRole("button", { name: /Run audit/ }));
    await waitFor(() => expect(api.runSeoAudit).toHaveBeenCalledWith("t", "shop.test", 25));
    expect(await screen.findByText("Sitemap Unavailable")).toBeTruthy();
    expect(screen.getByRole("img", { name: /Health score: 72/ })).toBeTruthy();
    expect(screen.getAllByText(/add a PageSpeed Insights API key/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /^low 1/i }));
    expect(screen.queryByText("Sitemap Unavailable")).toBeNull();
    expect(screen.getByText("Meta description too short")).toBeTruthy();
  });

  it("finds the tracked site's rank in a SERP snapshot", () => {
    const snap = { captured_at: "", keyword: "k", location: "", language: "", source: "dataforseo", results: [
      { position: 1, url: "https://other.test/a", title: "", result_type: "organic" },
      { position: 4, url: "https://www.shop.test/p", title: "", result_type: "organic" },
    ] };
    expect(rankOf(snap, "shop.test")).toBe(4);
    expect(rankOf(snap, "absent.test")).toBeNull();
  });
});
