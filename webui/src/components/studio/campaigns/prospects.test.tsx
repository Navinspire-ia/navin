// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchProspectProviders: vi.fn(),
  searchProspects: vi.fn(),
  importProspects: vi.fn(),
  enrichContacts: vi.fn(),
}));
vi.mock("@/lib/campaigns-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/campaigns-api")>()),
  ...api,
}));

import type { ProspectProviders, ProspectSearch } from "@/lib/campaigns-types";
import { EnrichDialog, ProspectsView } from "./ProspectsView";
import type { Tx } from "./campaigns-ui";

const tx: Tx = (_key, fallback, values) =>
  Object.entries(values ?? {}).reduce((text, [k, v]) => text.replace(`{{${k}}}`, String(v)), fallback);

const providers = (overrides: Partial<ProspectProviders> = {}): ProspectProviders => ({
  free: ["sirene", "web", "hiring", "osm"],
  keys: { apollo: true, hunter: true, pdl: false, places: false, crunchbase: false, opencorporates: false, companies_house: false, pappers: false },
  hint: "",
  can_find_people: true,
  can_find_email: true,
  can_verify: true,
  can_enrich_company: false,
  ...overrides,
});

const search: ProspectSearch = {
  search_id: "s1",
  count: 2,
  with_email: 1,
  with_person: 1,
  providers: providers(),
  rows: [
    { company: "Acme", domain: "acme.io", website: "https://acme.io", country: "FR", sector: "SaaS", size: "50",
      first_name: "Ana", last_name: "Diaz", role: "CEO", email: "ana@acme.io", email_status: "verified", phone: "",
      linkedin_url: "", source: "apollo", signal: "hiring sales" },
    { company: "Globex", domain: "globex.io", website: "", country: "FR", sector: "", size: "", first_name: "",
      last_name: "", role: "", email: "", email_status: "", phone: "", linkedin_url: "", source: "web", signal: "" },
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ProspectsView", () => {
  it("searches by market with the Leads keys, then imports the picked rows", async () => {
    api.fetchProspectProviders.mockResolvedValue(providers());
    api.searchProspects.mockResolvedValue(search);
    api.importProspects.mockResolvedValue({ created: 1, updated: 0, skipped: 0, errors: [], list_id: "ls_1" });
    const onCreateCampaign = vi.fn();
    render(<ProspectsView token="t" tx={tx} lists={[]} onImported={vi.fn()} onOpenList={vi.fn()} onCreateCampaign={onCreateCampaign} />);

    await waitFor(() => expect(screen.getByTestId("prospect-providers").textContent).toContain("Apollo"));
    fireEvent.change(screen.getByPlaceholderText(/accounting firms/), { target: { value: "SaaS" } });
    fireEvent.click(screen.getByRole("button", { name: "MA" }));
    fireEvent.click(screen.getByTestId("prospects-run"));
    await screen.findByTestId("prospects-table");
    const criteria = api.searchProspects.mock.calls[0][1];
    expect(criteria).toMatchObject({ sector: "SaaS", countries: ["FR", "MA"], find_people: true, limit: 25 });
    expect(criteria.titles).toEqual(["CEO", "Founder", "Head of Sales"]);

    fireEvent.click(screen.getByRole("button", { name: "Only with email" }));
    fireEvent.click(screen.getByTestId("prospects-import"));
    await screen.findByRole("status");
    expect(api.importProspects.mock.calls[0][1]).toMatchObject({
      search_id: "s1", indexes: [0], only_with_email: true, new_list_name: "SaaS FR MA", tags: ["prospects"],
    });
    fireEvent.click(screen.getByRole("button", { name: "Create a campaign" }));
    expect(onCreateCampaign).toHaveBeenCalledWith("ls_1");
  });

  it("without a key, explains the free general contact and that a named decision maker needs Apollo or Hunter", async () => {
    api.fetchProspectProviders.mockResolvedValue(providers({ can_find_people: false, can_find_contact: true, keys: { ...providers().keys, apollo: false, hunter: false } }));
    api.searchProspects.mockResolvedValue({
      ...search,
      count: 1, with_email: 1, with_person: 0, with_general: 1,
      rows: [{ ...search.rows[1], email: "contact@globex.io", email_status: "unverified", phone: "+33140000000", contact_kind: "general" }],
    });
    render(<ProspectsView token="t" tx={tx} lists={[]} onImported={vi.fn()} onOpenList={vi.fn()} onCreateCampaign={vi.fn()} />);
    expect(await screen.findByText(/general contact \(contact@, switchboard\).*not a named decision maker.*Apollo or Hunter/)).toBeTruthy();
    expect((screen.getByRole("switch") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText(/accounting firms/), { target: { value: "SaaS" } });
    fireEvent.click(screen.getByTestId("prospects-run"));
    const table = await screen.findByTestId("prospects-table");
    expect(api.searchProspects.mock.calls[0][1]).toMatchObject({ find_people: false });
    expect(api.searchProspects.mock.calls[0][1].find_contact).not.toBe(false);
    expect(table.textContent).toContain("General contact");
    expect(table.textContent).toContain("contact@globex.io");
    expect(table.textContent).toContain("company");
    expect(screen.getByTestId("prospects-general-count").textContent).toContain("1 general contacts");

    fireEvent.click(screen.getByRole("tab", { name: "By companies" }));
    expect((screen.getByTestId("prospects-run") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("EnrichDialog", () => {
  it("only offers what the keys allow and shows the summary", async () => {
    api.fetchProspectProviders.mockResolvedValue(providers());
    api.enrichContacts.mockResolvedValue({ processed: 2, emails_found: 1, verified: 1, invalid: 1, companies: 0, skipped: 0, errors: [] });
    const onDone = vi.fn();
    render(<EnrichDialog token="t" tx={tx} ids={["c1", "c2"]} open onClose={vi.fn()} onDone={onDone} />);
    await waitFor(() => expect((screen.getByRole("checkbox", { name: /Company facts/ }) as HTMLInputElement).disabled).toBe(true));
    fireEvent.click(screen.getByTestId("enrich-run"));
    await screen.findByTestId("enrich-result");
    expect(api.enrichContacts).toHaveBeenCalledWith("t", ["c1", "c2"], { find_email: true, verify: true, company: false });
    expect(screen.getByTestId("enrich-result").textContent).toContain("invalid");
    expect(onDone).toHaveBeenCalled();
  });
});
