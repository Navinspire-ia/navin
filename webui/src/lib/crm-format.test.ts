import { describe, expect, it } from "vitest";

import { companyName, contactName, crmHash, displayText, leadName, opportunityName } from "./crm-format";

describe("crmHash", () => {
  it("keeps the shell chat key when switching CRM tabs", () => {
    const current = new URLSearchParams("chat=websocket:abc");
    expect(crmHash("contacts", new URLSearchParams(), current)).toBe(
      "#/crm/contacts?chat=websocket%3Aabc",
    );
    expect(crmHash("dashboard", new URLSearchParams("view=table"), current)).toBe(
      "#/crm?view=table&chat=websocket%3Aabc",
    );
  });

  it("does not invent a chat key", () => {
    expect(crmHash("leads", new URLSearchParams())).toBe("#/crm/leads");
  });
});

describe("CRM display helpers", () => {
  it("never prints null or undefined", () => {
    expect(displayText(null)).toBe("");
    expect(displayText(undefined)).toBe("");
    expect(displayText("null")).toBe("");
    expect(displayText("undefined")).toBe("");
    expect(displayText("Tunisie")).toBe("Tunisie");
  });

  it("resolves contact / company / opportunity / lead names", () => {
    expect(contactName({ id: "ct-1", firstName: "Aymen", lastName: "K" })).toBe("Aymen K");
    expect(contactName({ id: "ct-1", firstName: null, lastName: null } as never)).toBe("ct-1");
    expect(companyName({ id: "co-1", name: null } as never)).toBe("");
    expect(companyName({ id: "co-1", name: "Navin" })).toBe("Navin");
    expect(opportunityName({ id: "op-1", name: null } as never)).toBe("");
    expect(leadName({ id: "ld-1", name: "Prospect" })).toBe("Prospect");
  });
});
