import { afterEach, describe, expect, it, vi } from "vitest";

const apiRequest = vi.fn(async () => ({}));
vi.mock("@/lib/api", () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...(args as [])),
  apiBodyHeaders: (content: string) => ({ "X-Body": content }),
}));

import {
  campaignsUrl,
  importCrmAudience,
  listContacts,
  previewCampaign,
  scheduleCampaign,
  updateReply,
} from "@/lib/campaigns-api";

afterEach(() => apiRequest.mockClear());

describe("campaigns api", () => {
  it("builds action urls and drops empty query values", () => {
    expect(campaignsUrl("dashboard")).toBe("/api/campaigns?action=dashboard");
    expect(campaignsUrl("contacts.list", { q: "acme", list_id: "", offset: 0, status: undefined })).toBe(
      "/api/campaigns?action=contacts.list&q=acme&offset=0",
    );
  });

  it("sends query for reads and a JSON body for writes", async () => {
    await listContacts("tok", { q: "a b", limit: 20 });
    expect(apiRequest).toHaveBeenLastCalledWith(
      "/api/campaigns?action=contacts.list&q=a+b&limit=20",
      "tok",
      { headers: { "X-Body": "{}" } },
    );
    await scheduleCampaign("tok", "c1", "");
    expect(apiRequest).toHaveBeenLastCalledWith("/api/campaigns?action=campaigns.schedule", "tok", {
      headers: { "X-Body": JSON.stringify({ id: "c1", schedule_at: "" }) },
    });
    await previewCampaign("tok", { subject: "Hi" }, "ct1");
    expect(JSON.parse((apiRequest.mock.calls.at(-1) as unknown as [string, string, { headers: { "X-Body": string } }])[2].headers["X-Body"])).toEqual({
      campaign: { subject: "Hi" },
      contact_id: "ct1",
    });
    await updateReply("tok", { id: "r1", status: "answered" });
    expect((apiRequest.mock.calls.at(-1) as unknown as [string])[0]).toBe("/api/campaigns?action=replies.update");
  });

  it("scopes CRM audiences to the active session", async () => {
    await importCrmAudience("tok", "websocket:abc", { kinds: ["leads"], filters: { status: ["qualified"] }, new_list_name: "Q" });
    const [url, , init] = apiRequest.mock.calls.at(-1) as unknown as [string, string, { headers: { "X-Body": string } }];
    expect(url).toBe("/api/campaigns?action=crm.import&session_key=websocket%3Aabc");
    expect(JSON.parse(init.headers["X-Body"]).kinds).toEqual(["leads"]);
  });
});
