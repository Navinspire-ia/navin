// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    i18n: { language: "fr" },
    t: (key: string, options?: Record<string, unknown> & { defaultValue?: string }) =>
      (options?.defaultValue ?? key).replace(/\{\{(\w+)\}\}/g, (_, name) => String(options?.[name] ?? "")),
  }),
}));

const apiRequest = vi.fn();
vi.mock("@/lib/api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));
const saveTextFile = vi.fn();
vi.mock("@/lib/save-blob", () => ({ saveTextFile: (...args: unknown[]) => saveTextFile(...args) }));

import { CyberCoveragePanel } from "./CyberCoveragePanel";

afterEach(() => {
  cleanup();
  apiRequest.mockReset();
  saveTextFile.mockReset();
});

const matrix = {
  referential: { id: "navin-cyber-appsec", version: "1.0.0", title: "Ref", controls: 302, categories: 24 },
  audit_id: "a1", audited_at: "2026-01-02T03:04:05Z", root: "/proj", levels_reached: ["L1"],
  summary: { total: 302, applicable: 10, not_applicable: 292, passed: 1, failed: 1, inconclusive: 0, blocked: 0, not_tested: 8,
    verified: 2, verified_coverage: 20, manual_review: 0, audit_complete: false, complete_blockers: 1 },
  applicability: [{ tag: "web", detected: true, reason: "" }, { tag: "mobile", detected: false, reason: "pas d'app" }],
  categories: [{ id: "c1", number: 1, name: { fr: "Gouvernance", en: "Governance", ar: "x" }, applicability: "", verification_mode: "",
    total: 2, applicable: 2, passed: 1, failed: 1, inconclusive: 0, blocked: 0, not_tested: 0, not_applicable: 0 }],
  controls: [
    { id: "GOV-01", category: "c1", task: "Politique de securite", applicability: "", applicability_detected: true, verification_mode: "read_only",
      status: "passed", capability: "automated", manual_review: false, executed_at: "", tools: [{ name: "semgrep", version: "1.2", status: "ok" }],
      target: "repo", environment: "local", evidence_refs: ["ev1"], findings: [], findings_count: 0, confidence: "high", risk: "none", remediation: "", reason: "OK verifie" },
    { id: "GOV-02", category: "c1", task: "Revue des acces", applicability: "", applicability_detected: true, verification_mode: "read_only",
      status: "failed", capability: "partial", manual_review: true, executed_at: "", tools: [],
      target: "", environment: "", evidence_refs: [], findings: ["f"], findings_count: 1, confidence: "", risk: "high", remediation: "Corriger", reason: "" },
  ],
  complete_blockers: ["API-07"],
  warning: "",
};

describe("CyberCoveragePanel", () => {
  it("goes from empty to running to data, filters and exports", async () => {
    let finishRun: (value: unknown) => void = () => {};
    apiRequest.mockImplementation((url: string) => {
      if (url.includes("action=matrix_export")) return Promise.resolve({ filename: "m.md", content: "# m", mime: "text/markdown" });
      if (url.includes("action=matrix")) return Promise.resolve({ matrix: null });
      return new Promise((resolve) => { finishRun = resolve; });
    });
    render(<CyberCoveragePanel open onOpenChange={() => {}} token="tok" root="/proj" />);
    await screen.findByTestId("cyber-coverage-empty");
    fireEvent.click(screen.getByTestId("cyber-coverage-run-button"));
    expect(await screen.findByTestId("cyber-coverage-running")).toBeTruthy();
    expect((screen.getByTestId("cyber-coverage-run-button") as HTMLButtonElement).disabled).toBe(true);
    expect(apiRequest.mock.calls.at(-1)?.[0]).toContain("action=run");
    finishRun({ audit: {}, checklist: {}, matrix });
    await screen.findByTestId("cyber-coverage-data");
    expect(screen.getByTestId("cyber-coverage-percent").textContent).toContain("20");
    expect(screen.getByTestId("cyber-coverage-incomplete").textContent).toContain("API-07");
    expect(screen.queryByTestId("cyber-coverage-complete")).toBeNull();

    const section = screen.getByTestId("cyber-category-c1");
    expect(section.getAttribute("aria-expanded")).toBe("false");
    expect(section.textContent).toContain("Gouvernance");
    fireEvent.click(section);
    expect(section.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("semgrep 1.2")).toBeTruthy();
    fireEvent.click(screen.getByTestId("cyber-control-GOV-01"));
    expect(screen.getByText("OK verifie")).toBeTruthy();

    fireEvent.click(screen.getByTestId("cyber-coverage-count-failed"));
    expect(screen.queryByTestId("cyber-control-GOV-01")).toBeNull();
    expect(screen.getByTestId("cyber-control-GOV-02")).toBeTruthy();

    fireEvent.click(screen.getByText("Export Markdown"));
    await waitFor(() => expect(saveTextFile).toHaveBeenCalledWith("m.md", "# m", "text/markdown"));
  });

  it("shows the error with a working retry", async () => {
    apiRequest.mockRejectedValueOnce(new Error("boom"));
    apiRequest.mockResolvedValueOnce({ matrix });
    render(<CyberCoveragePanel open onOpenChange={() => {}} token="tok" root="/proj" />);
    expect((await screen.findByTestId("cyber-coverage-error")).textContent).toContain("boom");
    fireEvent.click(screen.getByText("Retry"));
    await screen.findByTestId("cyber-coverage-data");
  });
});
