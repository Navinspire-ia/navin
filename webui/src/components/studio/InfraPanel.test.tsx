// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchInfraState: vi.fn(),
  updateInfraPolicy: vi.fn(),
  discoverInfra: vi.fn(),
  searchInfraCatalog: vi.fn(),
  fetchInfraOperation: vi.fn(),
  fetchInfraGraph: vi.fn(),
  fetchInfraAudit: vi.fn(),
  fetchInfraRuns: vi.fn(),
}));
vi.mock("@/lib/api", () => api);

import { InfraPanel } from "./InfraPanel";

const state = {
  project: "/p",
  policy: {
    level: "observe",
    scopes: { aws_profiles: ["dev"], kube_contexts: [], namespaces: [], gcp_projects: [], azure_subscriptions: [] },
    production_markers: ["prod"],
    auth: { aws_profile: "", gcp_impersonate: "" },
  },
  levels: ["observe", "prepare", "execute"],
  tools: [
    { name: "kubectl", provider: "kubernetes", available: true, version: "1.30", path: "/usr/bin/kubectl" },
    { name: "aws", provider: "aws", available: false, version: "", path: "" },
  ],
  contexts: {
    kubernetes: { current: "prod-eu", contexts: ["prod-eu"], production: true },
    aws: { profile: "dev", profiles: ["dev"], production: false },
    gcp: { project: "", configuration: "", production: false },
    terraform: { workspace: "", production: false },
  },
  catalog: { known: 120, available: 80, authorized: 30, tested: 5, providers: [{ provider: "kubernetes", count: 120, source: "kubectl", version: "1.30", discovered_at: "", context: "" }] },
  warnings: ["Production context active"],
  graph: { nodes: 0, edges: 0, updated_at: "" },
  audit_count: 0,
};

const item = { id: "k8s.get.pods", provider: "kubernetes", service: "core", name: "get pods", command: "kubectl get pods", risk: "read", known: true, available: true, authorized: true, tested: false };

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
});
afterEach(() => cleanup());

describe("InfraPanel", () => {
  it("renders the overview from the state", async () => {
    api.fetchInfraState.mockResolvedValue(state);
    render(<InfraPanel token="t" sessionKey="s" defaultOpen />);
    expect(await screen.findByTestId("tier-known")).toHaveProperty("textContent", "120");
    expect(screen.getByText("prod-eu")).toBeTruthy();
    expect(screen.getByText("production")).toBeTruthy();
    expect(screen.getByText("Production context active")).toBeTruthy();
    expect(screen.getByText("kubectl")).toBeTruthy();
    expect(screen.getByText("missing")).toBeTruthy();
    expect(screen.getByText(/always ask you first/)).toBeTruthy();
  });

  it("changing the level calls updateInfraPolicy with {level}", async () => {
    api.fetchInfraState.mockResolvedValue(state);
    api.updateInfraPolicy.mockResolvedValue({ ...state, policy: { ...state.policy, level: "prepare" } });
    render(<InfraPanel token="t" sessionKey="s" defaultOpen />);
    const radios = await screen.findAllByRole("radio");
    fireEvent.click(radios[1]);
    await waitFor(() => expect(api.updateInfraPolicy).toHaveBeenCalledWith("t", "s", { level: "prepare" }));
    await waitFor(() => expect(screen.getAllByRole("radio")[1].getAttribute("aria-checked")).toBe("true"));
  });

  it("catalog search shows items and opens the detail", async () => {
    api.fetchInfraState.mockResolvedValue(state);
    api.searchInfraCatalog.mockResolvedValue({ total: 1, items: [item] });
    api.fetchInfraOperation.mockResolvedValue({
      ...item, version: "1.30", endpoint: "/api/v1/pods", required: [], optional: ["-n"], permissions: ["pods:list"],
      examples: ["kubectl get pods -A"], effects: "none", verify: [], rollback: [], compat: [], source: "kubectl",
      decision: { action: "allow", reasons: ["read-only"] },
    });
    render(<InfraPanel token="t" sessionKey="s" defaultOpen />);
    fireEvent.click(screen.getByRole("tab", { name: "Catalog" }));
    fireEvent.change(screen.getByLabelText(/Search operations/), { target: { value: "pods" } });
    await waitFor(() => expect(api.searchInfraCatalog).toHaveBeenCalledWith("t", "s", expect.objectContaining({ q: "pods" })), { timeout: 2000 });
    fireEvent.click(await screen.findByText("kubectl get pods"));
    expect(await screen.findByTestId("infra-operation")).toBeTruthy();
    expect(api.fetchInfraOperation).toHaveBeenCalledWith("t", "s", "k8s.get.pods");
    expect(screen.getByText("pods:list")).toBeTruthy();
    expect(screen.getByText("read-only")).toBeTruthy();
  });

  it("shows the error state when the API fails", async () => {
    api.fetchInfraState.mockRejectedValue(new Error("gateway down"));
    render(<InfraPanel token="t" sessionKey="s" defaultOpen />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText("gateway down")).toBeTruthy();
  });

  it("offers Discover only for providers the engine can read here", async () => {
    const withTools = {
      ...state,
      tools: [
        ...state.tools,
        { name: "ssh", provider: "linux", available: true, version: "OpenSSH_9", path: "/usr/bin/ssh" },
        { name: "psql", provider: "databases", available: true, version: "16", path: "/usr/bin/psql" },
      ],
      discoverable: ["kubernetes", "jenkins"],
    };
    api.fetchInfraState.mockResolvedValue(withTools);
    api.discoverInfra.mockResolvedValue({ results: [{ provider: "jenkins", count: 12, available: true, version: "Jenkins", source: "Jenkins REST API", errors: [], duration_ms: 40 }] });
    render(<InfraPanel token="t" sessionKey="s" defaultOpen />);
    await screen.findByTestId("tier-known");
    expect(screen.queryByRole("button", { name: "linux" })).toBeNull();
    expect(screen.queryByRole("button", { name: "databases" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "jenkins" }));
    await waitFor(() => expect(api.discoverInfra).toHaveBeenCalledWith("t", "s", "jenkins", false));
  });
});
