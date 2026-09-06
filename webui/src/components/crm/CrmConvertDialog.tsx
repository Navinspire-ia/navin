import { useEffect, useMemo } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Controller, useForm } from "react-hook-form";

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
import type { CrmMember, CrmRecord } from "@/lib/api";
import { contactName, OPP_STAGES } from "@/lib/crm-format";
import { currencyOptions, optionList } from "@/lib/crm-catalog";
import { crmConvertSchema, type CrmConvertValues } from "@/lib/crm-schemas";

type Tx = (key: string, fallback: string) => string;

type Props = {
  open: boolean;
  lead: CrmRecord | null;
  companies: CrmRecord[];
  contacts: CrmRecord[];
  members: CrmMember[];
  currency: string;
  locale: string;
  actor: string;
  busy?: boolean;
  error?: string | null;
  tx: Tx;
  onClose: () => void;
  onSubmit: (body: Record<string, unknown>) => void;
};

export function CrmConvertDialog({
  open,
  lead,
  companies,
  contacts,
  members,
  currency,
  actor,
  busy,
  error,
  tx,
  onClose,
  onSubmit,
}: Props) {
  const schema = useMemo(() => crmConvertSchema(tx), [tx]);
  const form = useForm<CrmConvertValues>({
    resolver: zodResolver(schema),
    defaultValues: emptyConvert(lead, companies, contacts, members, currency, actor),
  });

  useEffect(() => {
    if (!open) return;
    form.reset(emptyConvert(lead, companies, contacts, members, currency, actor));
  }, [actor, companies, contacts, currency, form, lead, members, open]);

  const companyOptions = companies.map((row) => ({
    value: row.id,
    label: String(row.name || row.id),
    keywords: String(row.name || "").toLowerCase(),
  }));
  const contactOptions = contacts.map((row) => ({
    value: row.id,
    label: contactName(row),
    keywords: `${contactName(row)} ${row.email || ""}`.toLowerCase(),
  }));
  const ownerOptions = members.map((row) => ({
    value: row.identity || row.email || row.id,
    label: row.displayName || row.identity || row.email || row.id,
    keywords: `${row.displayName} ${row.identity} ${row.email}`.toLowerCase(),
  }));
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto rounded-[22px] border-border/70 bg-popover p-5 shadow-2xl">
        <form
          className="grid gap-4"
          onSubmit={form.handleSubmit((values) => {
            onSubmit({
              opportunityName: values.opportunityName,
              companyId: values.companyId,
              contactId: values.contactId,
              amount: Number(values.amount || 0),
              currency: values.currency,
              stage: values.stage || "nouveau",
              expectedCloseDate: values.expectedCloseDate,
              ownerId: values.ownerId,
            });
          })}
        >
          <DialogHeader className="text-left">
            <DialogTitle>{tx("crm.convertLeadTitle", "Convertir le lead")}</DialogTitle>
            <DialogDescription>
              {tx(
                "crm.convertLeadHelp",
                "Cree ou reutilise le contact et l'entreprise, puis ouvre l'opportunite.",
              )}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Field label={tx("crm.dealName", "Nom du deal")} required>
                <Input {...form.register("opportunityName")} autoFocus />
              </Field>
            </div>
            <Field label={tx("crm.companyName", "Entreprise")}>
              <Controller
                control={form.control}
                name="companyId"
                render={({ field }) => (
                  <SearchableSelect
                    value={field.value}
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
            <Field label={tx("crm.linkContact", "Contact")}>
              <Controller
                control={form.control}
                name="contactId"
                render={({ field }) => (
                  <SearchableSelect
                    value={field.value}
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
            <Field label={tx("crm.amount", "Montant")}>
              <Input type="number" min={0} step="0.01" {...form.register("amount")} />
            </Field>
            <Field label={tx("crm.currency", "Devise")}>
              <Controller
                control={form.control}
                name="currency"
                render={({ field }) => (
                  <SearchableSelect
                    value={field.value}
                    onChange={field.onChange}
                    options={currencyOptions()}
                    placeholder={tx("crm.searchCurrency", "Rechercher une devise")}
                    searchPlaceholder={tx("crm.searchCurrency", "Rechercher une devise")}
                    emptyLabel={tx("crm.noResults", "Aucun resultat")}
                  />
                )}
              />
            </Field>
            <Field label={tx("crm.stage", "Etape")}>
              <Controller
                control={form.control}
                name="stage"
                render={({ field }) => (
                  <SearchableSelect
                    value={field.value}
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
              <Input type="date" {...form.register("expectedCloseDate")} />
            </Field>
            <div className="sm:col-span-2">
              <Field label={tx("crm.owner", "Proprietaire")}>
                <Controller
                  control={form.control}
                  name="ownerId"
                  render={({ field }) => (
                    <SearchableSelect
                      value={field.value}
                      onChange={field.onChange}
                      options={ownerOptions}
                      allowEmpty
                      emptyLabelOption={actor || tx("crm.owner", "Proprietaire")}
                      placeholder={tx("crm.searchOwner", "Rechercher un proprietaire")}
                      searchPlaceholder={tx("crm.searchOwner", "Rechercher un proprietaire")}
                      emptyLabel={tx("crm.noResults", "Aucun resultat")}
                    />
                  )}
                />
              </Field>
            </div>
          </div>

          {form.formState.errors.opportunityName ? (
            <p className="text-[12px] text-destructive">{form.formState.errors.opportunityName.message}</p>
          ) : null}
          {error ? <p className="text-[12px] text-destructive">{error}</p> : null}

          <DialogFooter className="gap-2 sm:space-x-0">
            <Button type="button" variant="outline" onClick={onClose}>
              {tx("crm.cancel", "Annuler")}
            </Button>
            <Button type="submit" disabled={busy} className="cursor-pointer">
              {tx("crm.convertAction", "Convertir")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function emptyConvert(
  lead: CrmRecord | null,
  companies: CrmRecord[],
  contacts: CrmRecord[],
  members: CrmMember[],
  currency: string,
  actor: string,
): CrmConvertValues {
  const companyName = String(lead?.company || "").trim().toLowerCase();
  const company = companies.find((row) => String(row.name || "").trim().toLowerCase() === companyName);
  const email = String(lead?.email || "").trim().toLowerCase();
  const contact = contacts.find((row) => String(row.email || "").trim().toLowerCase() === email);
  const owner =
    members.find((row) => {
      const keys = [row.identity, row.email, row.handle].map((item) => item.toLowerCase());
      return keys.includes(actor.toLowerCase());
    })?.identity || actor;
  const name = String(lead?.company || lead?.name || "").trim();
  return {
    opportunityName: name ? `${name} - Deal` : "",
    companyId: company?.id || "",
    contactId: contact?.id || "",
    amount: "",
    currency: currency || "EUR",
    stage: "nouveau",
    expectedCloseDate: "",
    ownerId: owner,
  };
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
