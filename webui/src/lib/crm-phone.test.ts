// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { crmContactSchema, crmSettingsSchema } from "./crm-schemas";
import {
  crmPhoneStatus,
  isGarbageCrmPhone,
  normalizeCrmPhone,
  validCrmPhone,
} from "./crm-phone";

const tx = (_key: string, fallback: string) => fallback;

describe("crm phone helpers", () => {
  it("treats empty and calling-code-only as empty", () => {
    expect(crmPhoneStatus("")).toBe("empty");
    expect(crmPhoneStatus("+33")).toBe("empty");
    expect(normalizeCrmPhone("+33")).toBe("");
    expect(validCrmPhone("", "settings")).toBe(true);
  });

  it("treats an 8-digit French mobile as incomplete, not garbage", () => {
    expect(crmPhoneStatus("+3362562566", "FR")).toBe("incomplete");
    expect(isGarbageCrmPhone("+3362562566")).toBe(false);
    expect(normalizeCrmPhone("62562566", "FR")).toBe("+3362562566");
    expect(validCrmPhone("+3362562566", "settings")).toBe(true);
    expect(validCrmPhone("+3362562566", "record")).toBe(true);
  });

  it("accepts a complete French mobile as E.164", () => {
    expect(crmPhoneStatus("+33625625666", "FR")).toBe("valid");
    expect(normalizeCrmPhone("625625666", "FR")).toBe("+33625625666");
  });

  it("flags letter junk as garbage", () => {
    expect(isGarbageCrmPhone("hello")).toBe(true);
    expect(crmPhoneStatus("not-a-phone")).toBe("garbage");
    expect(validCrmPhone("hello", "record")).toBe(false);
    expect(validCrmPhone("hello", "settings")).toBe(true);
  });
});

describe("crm phone schemas", () => {
  it("lets settings save empty or incomplete French numbers", () => {
    const schema = crmSettingsSchema(tx);
    expect(schema.safeParse({
      companyName: "Atelier Navin",
      legalName: "",
      industry: "",
      country: "FR",
      website: "",
      phone: "",
      email: "",
      currency: "EUR",
      locale: "fr-FR",
    }).success).toBe(true);

    const incomplete = schema.safeParse({
      companyName: "Atelier Navin",
      legalName: "",
      industry: "",
      country: "FR",
      website: "",
      phone: "+3362562566",
      email: "",
      currency: "EUR",
      locale: "fr-FR",
    });
    expect(incomplete.success).toBe(true);
    if (incomplete.success) expect(incomplete.data.phone).toBe("+3362562566");
  });

  it("does not lock a contact on an incomplete number", () => {
    const schema = crmContactSchema(tx);
    const result = schema.safeParse({
      firstName: "Aymen",
      lastName: "",
      title: "",
      email: "",
      phone: "+3362562566",
      whatsapp: "",
      linkedin: "",
      companyId: "",
      country: "FR",
      source: "",
      status: "actif",
      owner: "",
    });
    expect(result.success).toBe(true);
  });

  it("rejects a garbage contact phone", () => {
    const schema = crmContactSchema(tx);
    const result = schema.safeParse({
      firstName: "Aymen",
      lastName: "",
      title: "",
      email: "",
      phone: "not-a-phone",
      whatsapp: "",
      linkedin: "",
      companyId: "",
      country: "FR",
      source: "",
      status: "actif",
      owner: "",
    });
    expect(result.success).toBe(false);
  });
});
