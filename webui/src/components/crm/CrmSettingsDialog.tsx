import { useEffect, useMemo } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Controller, useForm } from "react-hook-form";

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
import type { CrmSettings } from "@/lib/api";
import {
  countryOptions,
  CRM_LOCALES,
  currencyOptions,
  normalizeCountryValue,
} from "@/lib/crm-catalog";
import { crmSettingsSchema, type CrmSettingsValues } from "@/lib/crm-schemas";

type Tx = (key: string, fallback: string) => string;

type Props = {
  open: boolean;
  required?: boolean;
  busy?: boolean;
  error?: string | null;
  settings: CrmSettings | null;
  tx: Tx;
  onClose: () => void;
  onSave: (body: Record<string, string>) => void;
};

const EMPTY: CrmSettingsValues = {
  companyName: "",
  legalName: "",
  industry: "",
  country: "",
  website: "",
  phone: "",
  email: "",
  currency: "EUR",
  locale: "fr-FR",
};

export function CrmSettingsDialog({
  open,
  required,
  busy,
  error,
  settings,
  tx,
  onClose,
  onSave,
}: Props) {
  const schema = useMemo(() => crmSettingsSchema(tx), [tx]);
  const form = useForm<CrmSettingsValues>({
    resolver: zodResolver(schema),
    defaultValues: EMPTY,
  });

  useEffect(() => {
    if (!open) return;
    form.reset({
      companyName: settings?.companyName || "",
      legalName: settings?.legalName || "",
      industry: settings?.industry || "",
      country: normalizeCountryValue(settings?.country || ""),
      website: settings?.website || "",
      phone: settings?.phone || "",
      email: settings?.email || "",
      currency: settings?.currency || "EUR",
      locale: settings?.locale || "fr-FR",
    });
  }, [form, open, settings]);

  const locale = form.watch("locale") || "fr-FR";
  const lang = locale.toLowerCase().startsWith("en") ? "en" : "fr";
  const firstError = Object.entries(form.formState.errors).find(
    ([key, issue]) => !["phone", "companyName", "currency"].includes(key) && issue?.message,
  )?.[1]?.message;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !required) onClose();
      }}
    >
      <DialogContent
        showCloseButton={!required}
        className="max-h-[90vh] max-w-lg overflow-y-auto rounded-[22px] border-border/70 bg-popover p-5 shadow-2xl"
        onPointerDownOutside={required ? (event) => event.preventDefault() : undefined}
        onEscapeKeyDown={required ? (event) => event.preventDefault() : undefined}
      >
        <form className="grid gap-4" onSubmit={form.handleSubmit((values) => onSave(values))}>
          <DialogHeader className="text-left">
            <DialogTitle style={{ textWrap: "balance" } as never}>
              {tx("crm.setupTitle", "Configurer le CRM")}
            </DialogTitle>
            <DialogDescription>
              {tx(
                "crm.setupHelp",
                "Renseignez votre entreprise et la devise. Tous les montants utiliseront cette devise.",
              )}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label={tx("crm.companyName", "Entreprise")}
              required
              error={form.formState.errors.companyName?.message}
            >
              <Input {...form.register("companyName")} autoFocus />
            </Field>
            <Field label={tx("crm.legalName", "Raison sociale")}>
              <Input {...form.register("legalName")} />
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
                    value={field.value}
                    onChange={field.onChange}
                    options={countryOptions(lang)}
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
                    value={field.value}
                    onChange={field.onChange}
                    defaultCountry={form.watch("country") || "FR"}
                    locale={lang}
                    tx={tx}
                  />
                )}
              />
            </Field>
            <Field label={tx("crm.email", "Email")}>
              <Input type="email" {...form.register("email")} />
            </Field>
            <Field
              label={tx("crm.currency", "Devise")}
              required
              error={form.formState.errors.currency?.message}
            >
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
            <Field label={tx("crm.locale", "Format des nombres")}>
              <Controller
                control={form.control}
                name="locale"
                render={({ field }) => (
                  <SearchableSelect
                    value={field.value}
                    onChange={field.onChange}
                    options={CRM_LOCALES.map((item) => ({
                      value: item.code,
                      label: item.label,
                      keywords: item.code.toLowerCase(),
                    }))}
                    searchPlaceholder={tx("crm.searchPlaceholder", "Rechercher...")}
                    emptyLabel={tx("crm.noResults", "Aucun resultat")}
                  />
                )}
              />
            </Field>
          </div>
          <p className="text-[12px] text-muted-foreground">
            {tx("crm.currencyHelp", "Tous les montants s'affichent dans cette devise.")}
          </p>
          {firstError || error ? (
            <p className="text-[12px] text-destructive">{String(firstError || error)}</p>
          ) : null}

          <DialogFooter className="gap-2 sm:space-x-0">
            {required ? null : (
              <Button type="button" variant="outline" onClick={onClose}>
                {tx("crm.cancel", "Annuler")}
              </Button>
            )}
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
  error,
  children,
}: {
  label: string;
  required?: boolean;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-1.5 text-left">
      <span className="text-[12px] font-medium text-foreground">
        {label}
        {required ? <span className="text-destructive"> *</span> : null}
      </span>
      {children}
      {error ? <span className="text-[11px] text-destructive">{error}</span> : null}
    </label>
  );
}
