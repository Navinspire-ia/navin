import { z } from "zod";

import { normalizeCrmPhone, validCrmPhone } from "@/lib/crm-phone";

type Tx = (key: string, fallback: string) => string;

function settingsPhoneField() {
  return z.string().transform((value) => normalizeCrmPhone(value));
}

function recordPhoneField(tx: Tx) {
  return z
    .string()
    .refine((value) => validCrmPhone(value, "record"), {
      message: tx("crm.invalidPhone", "Numero de telephone invalide"),
    })
    .transform((value) => normalizeCrmPhone(value));
}

function requiredName(tx: Tx) {
  return z.string().trim().min(1, tx("crm.required", "Champ obligatoire"));
}

export function crmSettingsSchema(tx: Tx) {
  return z.object({
    companyName: requiredName(tx),
    legalName: z.string(),
    industry: z.string(),
    country: z.string(),
    website: z.string(),
    phone: settingsPhoneField(),
    email: z.string(),
    currency: z
      .string()
      .trim()
      .length(3, tx("crm.invalidCurrency", "Devise ISO invalide")),
    locale: z.string().min(2),
  });
}

export function crmContactSchema(tx: Tx) {
  return z
    .object({
      firstName: z.string(),
      lastName: z.string(),
      title: z.string(),
      email: z.string(),
      phone: recordPhoneField(tx),
      whatsapp: recordPhoneField(tx),
      linkedin: z.string(),
      companyId: z.string(),
      country: z.string(),
      source: z.string(),
      status: z.string(),
      owner: z.string(),
    })
    .superRefine((value, ctx) => {
      if (!value.firstName.trim() && !value.lastName.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["firstName"],
          message: tx("crm.requiredName", "Le prenom ou le nom est obligatoire."),
        });
      }
    });
}

export function crmCompanySchema(tx: Tx) {
  return z.object({
    name: requiredName(tx),
    industry: z.string(),
    country: z.string(),
    website: z.string(),
    phone: recordPhoneField(tx),
    owner: z.string(),
  });
}

export function crmLeadSchema(tx: Tx) {
  return z.object({
    name: requiredName(tx),
    company: z.string(),
    email: z.string(),
    phone: recordPhoneField(tx),
    country: z.string(),
    source: z.string(),
    score: z.string(),
    status: z.string(),
    owner: z.string(),
  });
}

export function crmOpportunitySchema(tx: Tx) {
  return z.object({
    name: requiredName(tx),
    amount: z.string(),
    probability: z.string(),
    stage: z.string(),
    closeDate: z.string(),
    nextAction: z.string(),
    companyId: z.string(),
    contactIds: z.string(),
    currency: z.string().trim().length(3),
    owner: z.string(),
  });
}

export function crmActivitySchema(tx: Tx) {
  return z.object({
    kind: z.string(),
    title: requiredName(tx),
    body: z.string(),
    at: z.string(),
    contactId: z.string(),
    companyId: z.string(),
    opportunityId: z.string(),
    leadId: z.string(),
  });
}

export function crmProductSchema(tx: Tx) {
  return z.object({
    name: requiredName(tx),
    defaultPrice: z.string(),
    currency: z.string().trim().length(3),
  });
}

export function crmConvertSchema(tx: Tx) {
  return z.object({
    opportunityName: requiredName(tx),
    companyId: z.string(),
    contactId: z.string(),
    amount: z.string(),
    currency: z.string().trim().length(3),
    stage: z.string(),
    expectedCloseDate: z.string(),
    ownerId: z.string(),
  });
}

export type CrmSettingsValues = z.infer<ReturnType<typeof crmSettingsSchema>>;
export type CrmConvertValues = z.infer<ReturnType<typeof crmConvertSchema>>;
