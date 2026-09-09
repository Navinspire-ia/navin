// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Controller, useForm } from "react-hook-form";
import type { ZodTypeAny } from "zod";

import { CrmPhoneField } from "@/components/crm/CrmPhoneField";
import { SearchableSelect } from "@/components/crm/SearchableSelect";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { CrmMember, CrmRecord } from "@/lib/api";
import {
  countryOptions,
  currencyOptions,
  LEAD_SOURCES,
  normalizeCountryValue,
  optionList,
} from "@/lib/crm-catalog";
import {
  companyName,
  contactName,
  leadName,
  localToUnix,
  opportunityName,
  unixToLocal,
  type CrmEditorKind,
} from "@/lib/crm-format";
import {
  crmActivitySchema,
  crmCompanySchema,
  crmContactSchema,
  crmLeadSchema,
  crmOpportunitySchema,
  crmProductSchema,
} from "@/lib/crm-schemas";

export type { CrmEditorKind };

type Tx = (key: string, fallback: string) => string;

const OPP_STAGES = ["nouveau", "qualifie", "proposition", "negociation", "gagne", "perdu"] as const;
const ACTIVITY_KINDS = ["appel", "email", "whatsapp", "reunion", "tache", "note", "teams", "document"] as const;
const CONTACT_STATUSES = ["actif", "inactif"] as const;
const LEAD_STATUSES = ["nouveau", "contacte", "qualifie", "converti", "perdu"] as const;

type Props = {
  open: boolean;
  kind: CrmEditorKind;
  record?: CrmRecord | null;
  busy?: boolean;
  error?: string | null;
  currency: string;
  locale?: string;
  defaultCountry?: string;
  companies: CrmRecord[];
  contacts?: CrmRecord[];
  opportunities?: CrmRecord[];
  leads?: CrmRecord[];
  members?: CrmMember[];
  actor?: string;
  tx: Tx;
  onClose: () => void;
  onSubmit: (body: Record<string, unknown>) => void;
};

function titles(kind: CrmEditorKind, editing: boolean, tx: Tx) {
  const map: Record<CrmEditorKind, [string, string]> = {
    contacts: [tx("crm.createTitleContact", "Nouveau contact"), tx("crm.editTitleContact", "Modifier le contact")],
    companies: [
      tx("crm.createTitleCompany", "Nouvelle entreprise"),
      tx("crm.editTitleCompany", "Modifier l'entreprise"),
    ],
    leads: [tx("crm.createTitleLead", "Nouveau lead"), tx("crm.editTitleLead", "Modifier le lead")],
    opportunities: [
      tx("crm.createTitleDeal", "Nouvelle opportunite"),
      tx("crm.editTitleDeal", "Modifier l'opportunite"),
    ],
    activities: [
      tx("crm.createTitleActivity", "Nouvelle activite"),
      tx("crm.editTitleActivity", "Modifier l'activite"),
    ],
    products: [tx("crm.createTitleProduct", "Nouveau produit"), tx("crm.editTitleProduct", "Modifier le produit")],
  };
  return editing ? map[kind][1] : map[kind][0];
}

function schemaFor(kind: CrmEditorKind, tx: Tx): ZodTypeAny {
  if (kind === "contacts") return crmContactSchema(tx);
  if (kind === "companies") return crmCompanySchema(tx);
  if (kind === "leads") return crmLeadSchema(tx);
  if (kind === "opportunities") return crmOpportunitySchema(tx);
  if (kind === "products") return crmProductSchema(tx);
  return crmActivitySchema(tx);
}

function seed(
  kind: CrmEditorKind,
  record: CrmRecord | null | undefined,
  currency: string,
  actor: string,
): Record<string, string> {
  const row = record || { id: "" };
  const owner = String(row.owner || actor || "");
  if (kind === "contacts") {
    return {
      firstName: String(row.firstName || ""),
      lastName: String(row.lastName || ""),
      title: String(row.title || ""),
      email: String(row.email || ""),
      phone: String(row.phone || ""),
      whatsapp: String(row.whatsapp || ""),
      linkedin: String(row.linkedin || ""),
      companyId: String(row.companyId || ""),
      country: normalizeCountryValue(String(row.country || "")),
      source: String(row.source || ""),
      status: String(row.status || "actif"),
      owner,
    };
  }
  if (kind === "companies") {
    return {
      name: String(row.name || ""),
      industry: String(row.industry || ""),
      country: normalizeCountryValue(String(row.country || "")),
      website: String(row.website || ""),
      phone: String(row.phone || ""),
      owner,
    };
  }
  if (kind === "leads") {
    return {
      name: String(row.name || ""),
      company: String(row.company || ""),
      email: String(row.email || ""),
      phone: String(row.phone || ""),
      country: normalizeCountryValue(String(row.country || "")),
      source: String(row.source || ""),
      score: String(row.score ?? ""),
      status: String(row.status || "nouveau"),
      owner,
    };
  }
  if (kind === "opportunities") {
    return {
      name: String(row.name || ""),
      amount: row.amount == null ? "" : String(row.amount),
      probability: row.probability == null ? "20" : String(row.probability),
      stage: String(row.stage || "nouveau"),
      closeDate: String(row.closeDate || ""),
      nextAction: String(row.nextAction || ""),
      companyId: String(row.companyId || ""),
      contactIds: Array.isArray(row.contactIds) ? (row.contactIds as string[]).join(",") : "",
      currency: String(row.currency || currency),
      owner,
    };
  }
  if (kind === "products") {
    return {
      name: String(row.name || ""),
      defaultPrice: row.defaultPrice == null ? "" : String(row.defaultPrice),
      currency: String(row.currency || currency),
    };
  }
  return {
    kind: String(row.kind || "tache"),
    title: String(row.title || ""),
    body: String(row.body || ""),
    at: unixToLocal(row.at as number | undefined),
    contactId: String(row.contactId || ""),
    companyId: String(row.companyId || ""),
    opportunityId: String(row.opportunityId || ""),
    leadId: String(row.leadId || ""),
  };
}

function toBody(kind: CrmEditorKind, form: Record<string, string>, currency: string): Record<string, unknown> {
  if (kind === "contacts") {
    return {
      firstName: form.firstName,
      lastName: form.lastName,
      title: form.title,
      email: form.email,
      phone: form.phone,
      whatsapp: form.whatsapp,
      linkedin: form.linkedin,
      companyId: form.companyId,
      country: form.country,
      source: form.source,
      status: form.status || "actif",
      owner: form.owner,
    };
  }
  if (kind === "companies") {
    return {
      name: form.name,
      industry: form.industry,
      country: form.country,
      website: form.website,
      phone: form.phone,
      owner: form.owner,
    };
  }
  if (kind === "leads") {
    return {
      name: form.name,
      company: form.company,
      email: form.email,
      phone: form.phone,
      country: form.country,
      source: form.source,
      score: Number(form.score || 0),
      status: form.status || "nouveau",
      owner: form.owner,
    };
  }
  if (kind === "opportunities") {
    return {
      name: form.name,
      amount: Number(form.amount || 0),
      probability: Number(form.probability || 20),
      stage: form.stage || "nouveau",
      closeDate: form.closeDate,
      nextAction: form.nextAction,
      companyId: form.companyId,
      contactIds: (form.contactIds || "").split(",").map((item) => item.trim()).filter(Boolean),
      currency: form.currency || currency,
      owner: form.owner,
    };
  }
  if (kind === "products") {
    return {
      name: form.name,
      defaultPrice: Number(form.defaultPrice || 0),
      currency: form.currency || currency,
    };
  }
  const at = localToUnix(form.at || "");
  return {
    kind: form.kind || "tache",
    title: form.title,
    body: form.body,
    ...(at ? { at } : {}),
    contactId: form.contactId,
    companyId: form.companyId,
    opportunityId: form.opportunityId,
    leadId: form.leadId,
  };
}

export function CrmRecordDialog({
  open,
  kind,
  record,
  busy,
  error,
  currency,
  locale = "fr-FR",
  defaultCountry = "FR",
  companies,
  contacts = [],
  opportunities = [],
  leads = [],
  members = [],
  actor = "",
  tx,
  onClose,
  onSubmit,
}: Props) {
  const editing = Boolean(record?.id);
  const title = useMemo(() => titles(kind, editing, tx), [kind, editing, tx]);
  const schema = useMemo(() => schemaFor(kind, tx), [kind, tx]);
  const form = useForm<Record<string, string>>({
    resolver: zodResolver(schema as never),
    defaultValues: seed(kind, record, currency, actor),
  });

  useEffect(() => {
    if (!open) return;
    form.reset(seed(kind, record, currency, actor));
  }, [actor, currency, form, kind, open, record]);

  const lang = locale.toLowerCase().startsWith("en") ? "en" : "fr";
  const countries = useMemo(() => countryOptions(lang), [lang]);
  const companyOptions = companies.map((item) => ({
    value: item.id,
    label: companyName(item) || item.id,
    keywords: companyName(item).toLowerCase(),
  }));
  const contactOptions = contacts.map((item) => ({
    value: item.id,
    label: contactName(item),
    keywords: contactName(item).toLowerCase(),
  }));
  const opportunityOptions = opportunities.map((item) => ({
    value: item.id,
    label: opportunityName(item) || item.id,
    keywords: opportunityName(item).toLowerCase(),
  }));
  const leadOptions = leads.map((item) => ({
    value: item.id,
    label: leadName(item) || item.id,
    keywords: leadName(item).toLowerCase(),
  }));
  const ownerOptions = members.map((row) => ({
    value: row.identity || row.email || row.id,
    label: row.displayName || row.identity || row.email || row.id,
    keywords: `${row.displayName} ${row.identity} ${row.email}`.toLowerCase(),
  }));
  const sourceOptions = optionList(LEAD_SOURCES, (value) => tx(`crm.sources.${value}`, value));
  const currentSource = form.watch("source");
  if (currentSource && !sourceOptions.some((item) => item.value === currentSource)) {
    sourceOptions.push({ value: currentSource, label: currentSource, keywords: currentSource.toLowerCase() });
  }

  const firstError = Object.entries(form.formState.errors).find(
    ([key, issue]) => key !== "phone" && key !== "whatsapp" && issue?.message,
  )?.[1]?.message;

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto rounded-[22px] border-border/70 bg-popover p-5 shadow-2xl">
        <form
          className="grid gap-4"
          onSubmit={form.handleSubmit((values) => onSubmit(toBody(kind, values, currency)))}
        >
          <DialogHeader className="text-left">
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>
              {tx("crm.modalHelp", "Les champs marques * sont obligatoires.")}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 sm:grid-cols-2">
            {kind === "contacts" ? (
              <>
                <Field label={tx("crm.firstName", "Prenom")} required>
                  <Input {...form.register("firstName")} autoFocus />
                </Field>
                <Field label={tx("crm.lastName", "Nom")}>
                  <Input {...form.register("lastName")} />
                </Field>
                <Field label={tx("crm.title", "Fonction")}>
                  <Input {...form.register("title")} />
                </Field>
                <Field label={tx("crm.email", "Email")}>
                  <Input type="email" {...form.register("email")} />
                </Field>
                <Field label={tx("crm.phone", "Telephone")}>
                  <Controller
                    control={form.control}
                    name="phone"
                    render={({ field }) => (
                      <CrmPhoneField
                        value={field.value || ""}
                        onChange={field.onChange}
                        defaultCountry={form.watch("country") || defaultCountry}
                        locale={lang}
                        tx={tx}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.whatsapp", "WhatsApp")}>
                  <Controller
                    control={form.control}
                    name="whatsapp"
                    render={({ field }) => (
                      <CrmPhoneField
                        value={field.value || ""}
                        onChange={field.onChange}
                        defaultCountry={form.watch("country") || defaultCountry}
                        locale={lang}
                        tx={tx}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.country", "Pays")}>
                  <Controller
                    control={form.control}
                    name="country"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={countries}
                        allowEmpty
                        placeholder={tx("crm.searchCountry", "Rechercher un pays")}
                        searchPlaceholder={tx("crm.searchCountry", "Rechercher un pays")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.linkedin", "LinkedIn")}>
                  <Input {...form.register("linkedin")} />
                </Field>
                <Field label={tx("crm.source", "Source")}>
                  <Controller
                    control={form.control}
                    name="source"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={sourceOptions}
                        allowEmpty
                        placeholder={tx("crm.searchSource", "Rechercher une source")}
                        searchPlaceholder={tx("crm.searchSource", "Rechercher une source")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.companyName", "Entreprise")}>
                  <Controller
                    control={form.control}
                    name="companyId"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={companyOptions}
                        allowEmpty
                        emptyLabelOption={tx("crm.noCompany", "Sans entreprise")}
                        placeholder={tx("crm.searchCompany", "Rechercher une entreprise")}
                        searchPlaceholder={tx("crm.searchCompany", "Rechercher une entreprise")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.status", "Statut")}>
                  <Controller
                    control={form.control}
                    name="status"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || "actif"}
                        onChange={field.onChange}
                        options={optionList(CONTACT_STATUSES, (status) => tx(`crm.statuses.${status}`, status))}
                        searchPlaceholder={tx("crm.searchPlaceholder", "Rechercher...")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                {ownerOptions.length ? (
                  <Field label={tx("crm.owner", "Proprietaire")}>
                    <Controller
                      control={form.control}
                      name="owner"
                      render={({ field }) => (
                        <SearchableSelect
                          value={field.value || ""}
                          onChange={field.onChange}
                          options={ownerOptions}
                          allowEmpty
                          placeholder={tx("crm.searchOwner", "Rechercher un proprietaire")}
                          searchPlaceholder={tx("crm.searchOwner", "Rechercher un proprietaire")}
                          emptyLabel={tx("crm.noResults", "Aucun resultat")}
                        />
                      )}
                    />
                  </Field>
                ) : null}
              </>
            ) : null}

            {kind === "companies" ? (
              <>
                <Field label={tx("crm.companyName", "Entreprise")} required>
                  <Input {...form.register("name")} autoFocus />
                </Field>
                <Field label={tx("crm.industry", "Industrie")}>
                  <Input {...form.register("industry")} />
                </Field>
                <Field label={tx("crm.country", "Pays")}>
                  <Controller
                    control={form.control}
                    name="country"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={countries}
                        allowEmpty
                        placeholder={tx("crm.searchCountry", "Rechercher un pays")}
                        searchPlaceholder={tx("crm.searchCountry", "Rechercher un pays")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.website", "Site")}>
                  <Input {...form.register("website")} />
                </Field>
                <Field label={tx("crm.phone", "Telephone")}>
                  <Controller
                    control={form.control}
                    name="phone"
                    render={({ field }) => (
                      <CrmPhoneField
                        value={field.value || ""}
                        onChange={field.onChange}
                        defaultCountry={form.watch("country") || defaultCountry}
                        locale={lang}
                        tx={tx}
                      />
                    )}
                  />
                </Field>
              </>
            ) : null}

            {kind === "leads" ? (
              <>
                <Field label={tx("crm.leadName", "Nom")} required>
                  <Input {...form.register("name")} autoFocus />
                </Field>
                <Field label={tx("crm.companyName", "Entreprise")}>
                  <Input {...form.register("company")} />
                </Field>
                <Field label={tx("crm.email", "Email")}>
                  <Input type="email" {...form.register("email")} />
                </Field>
                <Field label={tx("crm.phone", "Telephone")}>
                  <Controller
                    control={form.control}
                    name="phone"
                    render={({ field }) => (
                      <CrmPhoneField
                        value={field.value || ""}
                        onChange={field.onChange}
                        defaultCountry={form.watch("country") || defaultCountry}
                        locale={lang}
                        tx={tx}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.country", "Pays")}>
                  <Controller
                    control={form.control}
                    name="country"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={countries}
                        allowEmpty
                        placeholder={tx("crm.searchCountry", "Rechercher un pays")}
                        searchPlaceholder={tx("crm.searchCountry", "Rechercher un pays")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.source", "Source")}>
                  <Controller
                    control={form.control}
                    name="source"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={sourceOptions}
                        allowEmpty
                        placeholder={tx("crm.searchSource", "Rechercher une source")}
                        searchPlaceholder={tx("crm.searchSource", "Rechercher une source")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.score", "Score")}>
                  <Input type="number" min={0} max={100} {...form.register("score")} />
                </Field>
                <Field label={tx("crm.status", "Statut")}>
                  <Controller
                    control={form.control}
                    name="status"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || "nouveau"}
                        onChange={field.onChange}
                        options={optionList(LEAD_STATUSES, (status) => tx(`crm.leadStages.${status}`, status))}
                        searchPlaceholder={tx("crm.searchPlaceholder", "Rechercher...")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
              </>
            ) : null}

            {kind === "opportunities" ? (
              <>
                <Field label={tx("crm.dealName", "Nom du deal")} required>
                  <Input {...form.register("name")} autoFocus />
                </Field>
                <Field label={tx("crm.amount", "Montant")}>
                  <Input type="number" min={0} step="0.01" {...form.register("amount")} />
                </Field>
                <Field label={tx("crm.currency", "Devise")}>
                  <Controller
                    control={form.control}
                    name="currency"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || currency}
                        onChange={field.onChange}
                        options={currencyOptions()}
                        placeholder={tx("crm.searchCurrency", "Rechercher une devise")}
                        searchPlaceholder={tx("crm.searchCurrency", "Rechercher une devise")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.probability", "Probabilite")}>
                  <Input type="number" min={0} max={100} {...form.register("probability")} />
                </Field>
                <Field label={tx("crm.stage", "Etape")}>
                  <Controller
                    control={form.control}
                    name="stage"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || "nouveau"}
                        onChange={field.onChange}
                        options={optionList(OPP_STAGES, (stage) => tx(`crm.stages.${stage}`, stage))}
                        placeholder={tx("crm.searchStage", "Rechercher une etape")}
                        searchPlaceholder={tx("crm.searchStage", "Rechercher une etape")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.closeDate", "Date de cloture")}>
                  <Input type="date" {...form.register("closeDate")} />
                </Field>
                <Field label={tx("crm.companyName", "Entreprise")}>
                  <Controller
                    control={form.control}
                    name="companyId"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={companyOptions}
                        allowEmpty
                        emptyLabelOption={tx("crm.noCompany", "Sans entreprise")}
                        placeholder={tx("crm.searchCompany", "Rechercher une entreprise")}
                        searchPlaceholder={tx("crm.searchCompany", "Rechercher une entreprise")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                {ownerOptions.length ? (
                  <Field label={tx("crm.owner", "Proprietaire")}>
                    <Controller
                      control={form.control}
                      name="owner"
                      render={({ field }) => (
                        <SearchableSelect
                          value={field.value || ""}
                          onChange={field.onChange}
                          options={ownerOptions}
                          allowEmpty
                          placeholder={tx("crm.searchOwner", "Rechercher un proprietaire")}
                          searchPlaceholder={tx("crm.searchOwner", "Rechercher un proprietaire")}
                          emptyLabel={tx("crm.noResults", "Aucun resultat")}
                        />
                      )}
                    />
                  </Field>
                ) : null}
                <div className="sm:col-span-2">
                  <Field label={tx("crm.nextAction", "Prochaine action")}>
                    <Input {...form.register("nextAction")} />
                  </Field>
                </div>
                <div className="sm:col-span-2">
                  <Field label={tx("crm.linkContacts", "Contacts lies")}>
                    <div className="max-h-32 space-y-1 overflow-y-auto rounded-md border border-input p-2">
                      {contacts.length === 0 ? (
                        <p className="text-[12px] text-muted-foreground">{tx("crm.noContact", "Aucun contact")}</p>
                      ) : (
                        contacts.map((item) => {
                          const selected = (form.watch("contactIds") || "").split(",").filter(Boolean);
                          const checked = selected.includes(item.id);
                          return (
                            <label key={item.id} className="flex cursor-pointer items-center gap-2 text-[12px]">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => {
                                  const next = checked
                                    ? selected.filter((id) => id !== item.id)
                                    : [...selected, item.id];
                                  form.setValue("contactIds", next.join(","));
                                }}
                              />
                              {contactName(item)}
                            </label>
                          );
                        })
                      )}
                    </div>
                  </Field>
                </div>
              </>
            ) : null}

            {kind === "activities" ? (
              <>
                <Field label={tx("crm.activityKind", "Type")}>
                  <Controller
                    control={form.control}
                    name="kind"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || "tache"}
                        onChange={field.onChange}
                        options={optionList(ACTIVITY_KINDS, (item) => tx(`crm.kinds.${item}`, item))}
                        searchPlaceholder={tx("crm.searchPlaceholder", "Rechercher...")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.activityTitle", "Titre")} required>
                  <Input {...form.register("title")} autoFocus />
                </Field>
                <Field label={tx("crm.activityAt", "Date et heure")}>
                  <Input type="datetime-local" {...form.register("at")} />
                </Field>
                <Field label={tx("crm.linkContact", "Contact")}>
                  <Controller
                    control={form.control}
                    name="contactId"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={contactOptions}
                        allowEmpty
                        emptyLabelOption={tx("crm.noContact", "Aucun contact")}
                        placeholder={tx("crm.searchContact", "Rechercher un contact")}
                        searchPlaceholder={tx("crm.searchContact", "Rechercher un contact")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.companyName", "Entreprise")}>
                  <Controller
                    control={form.control}
                    name="companyId"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={companyOptions}
                        allowEmpty
                        emptyLabelOption={tx("crm.noCompany", "Sans entreprise")}
                        placeholder={tx("crm.searchCompany", "Rechercher une entreprise")}
                        searchPlaceholder={tx("crm.searchCompany", "Rechercher une entreprise")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.linkDeal", "Opportunite")}>
                  <Controller
                    control={form.control}
                    name="opportunityId"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={opportunityOptions}
                        allowEmpty
                        emptyLabelOption={tx("crm.noDeal", "Sans opportunite")}
                        placeholder={tx("crm.searchPlaceholder", "Rechercher...")}
                        searchPlaceholder={tx("crm.searchPlaceholder", "Rechercher...")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <Field label={tx("crm.linkLead", "Lead")}>
                  <Controller
                    control={form.control}
                    name="leadId"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={field.onChange}
                        options={leadOptions}
                        allowEmpty
                        emptyLabelOption={tx("crm.noLead", "Sans lead")}
                        placeholder={tx("crm.searchPlaceholder", "Rechercher...")}
                        searchPlaceholder={tx("crm.searchPlaceholder", "Rechercher...")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
                <div className="sm:col-span-2">
                  <Field label={tx("crm.activityBody", "Detail")}>
                    <Textarea {...form.register("body")} rows={4} />
                  </Field>
                </div>
              </>
            ) : null}

            {kind === "products" ? (
              <>
                <Field label={tx("crm.productName", "Produit")} required>
                  <Input {...form.register("name")} autoFocus />
                </Field>
                <Field label={tx("crm.defaultPrice", "Prix")}>
                  <Input type="number" min={0} step="0.01" {...form.register("defaultPrice")} />
                </Field>
                <Field label={tx("crm.currency", "Devise")}>
                  <Controller
                    control={form.control}
                    name="currency"
                    render={({ field }) => (
                      <SearchableSelect
                        value={field.value || currency}
                        onChange={field.onChange}
                        options={currencyOptions()}
                        placeholder={tx("crm.searchCurrency", "Rechercher une devise")}
                        searchPlaceholder={tx("crm.searchCurrency", "Rechercher une devise")}
                        emptyLabel={tx("crm.noResults", "Aucun resultat")}
                      />
                    )}
                  />
                </Field>
              </>
            ) : null}
          </div>

          {firstError || error ? (
            <p className="text-[12px] text-destructive">{String(firstError || error)}</p>
          ) : null}

          <DialogFooter className="gap-2 sm:space-x-0">
            <Button type="button" variant="outline" onClick={onClose}>
              {tx("crm.cancel", "Annuler")}
            </Button>
            <Button type="submit" disabled={busy} className="cursor-pointer">
              {tx("crm.save", "Enregistrer")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-1.5 text-left">
      <span className="text-[12px] font-medium text-foreground">
        {label}
        {required ? <span className="text-destructive"> *</span> : null}
      </span>
      {children}
    </label>
  );
}
