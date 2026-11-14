import { afterEach, describe, expect, it, vi } from "vitest";

const apiRequest = vi.fn();
vi.mock("@/lib/api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

import {
  categoryName,
  exportFilename,
  fetchCoverageMatrix,
  filterControls,
  formatElapsed,
  formatPercent,
  groupByCategory,
  runCoverageAudit,
  securityAuditUrl,
  type CoverageCategory,
  type CoverageControl,
} from "./cyber-coverage";

afterEach(() => apiRequest.mockReset());

const control = (id: string, category: string, status: CoverageControl["status"], task = "Tache"): CoverageControl => ({
  id, category, task, applicability: "web", applicability_detected: true, verification_mode: "read_only",
  status, capability: "automated", manual_review: false, executed_at: "", tools: [{ name: "semgrep", version: "1.0", status: "ok" }],
  target: "", environment: "", evidence_refs: [], findings: [], findings_count: 0, confidence: "", risk: "none", remediation: "", reason: "",
});
const cat = (id: string, number: number): CoverageCategory => ({
  id, number, name: { fr: `FR ${id}`, en: `EN ${id}`, ar: `AR ${id}` }, applicability: "", verification_mode: "",
  total: 1, applicable: 1, passed: 0, failed: 0, inconclusive: 0, blocked: 0, not_tested: 0, not_applicable: 0,
});

describe("cyber-coverage", () => {
  it("builds the endpoint URL and skips empty params", () => {
    expect(securityAuditUrl("run", { path: "/p q", base_url: "" })).toBe("/api/webui/security-audit?action=run&path=%2Fp+q");
  });

  it("fetches the matrix (null when none) and runs with options", async () => {
    apiRequest.mockResolvedValueOnce({ matrix: null });
    expect(await fetchCoverageMatrix("tok", "/proj")).toBeNull();
    expect(apiRequest.mock.calls[0][0]).toContain("action=matrix&path=%2Fproj");
    apiRequest.mockResolvedValueOnce({ matrix: { audit_id: "a1" } });
    const m = await runCoverageAudit("tok", "/proj", { scanners: false, offline: true, baseUrl: " https://s.example " });
    expect(m).toEqual({ audit_id: "a1" });
    const url = apiRequest.mock.calls[1][0] as string;
    expect(url).toContain("scanners=false");
    expect(url).toContain("offline=true");
    expect(url).toContain("base_url=https%3A%2F%2Fs.example");
  });

  it("formats percent and elapsed time", () => {
    expect(formatPercent(87.45, "en")).toBe("87.5 %");
    expect(formatPercent(undefined, "en")).toBe("0.0 %");
    expect(formatPercent(140, "en")).toBe("100.0 %");
    expect(formatElapsed(125_000)).toBe("2:05");
  });

  it("picks the category name by language with an English fallback", () => {
    expect(categoryName(cat("c1", 1), "fr-FR")).toBe("FR c1");
    expect(categoryName(cat("c1", 1), "ar")).toBe("AR c1");
    expect(categoryName(cat("c1", 1), "de")).toBe("EN c1");
  });

  it("filters and groups controls", () => {
    const list = [control("A-1", "c1", "passed", "Verifier TLS"), control("A-2", "c1", "failed"), control("B-1", "c2", "failed")];
    expect(filterControls(list, { query: "tls" }).map((c) => c.id)).toEqual(["A-1"]);
    expect(filterControls(list, { statuses: new Set(["failed"]) }).map((c) => c.id)).toEqual(["A-2", "B-1"]);
    expect(filterControls(list, { category: "c2" }).map((c) => c.id)).toEqual(["B-1"]);
    const groups = groupByCategory([cat("c2", 2), cat("c1", 1)], list);
    expect(groups.map((g) => [g.category.id, g.controls.length])).toEqual([["c1", 2], ["c2", 1]]);
  });

  it("sanitizes the export filename with a fallback", () => {
    expect(exportFilename("matrix.md", "x", "md")).toBe("matrix.md");
    expect(exportFilename("a/b.json", "x", "json")).toBe("a_b.json");
    expect(exportFilename("", "id 1", "json")).toBe("navin-cyber-coverage-id_1.json");
  });
});
