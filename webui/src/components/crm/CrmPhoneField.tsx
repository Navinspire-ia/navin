import type { Country } from "react-phone-number-input";
import PhoneInput, { isSupportedCountry } from "react-phone-number-input";

import { SearchableSelect } from "@/components/crm/SearchableSelect";
import { callingCodeOptions } from "@/lib/crm-catalog";
import {
  crmPhoneHint,
  crmPhoneStatus,
  isCallingCodeOnly,
  normalizeCrmPhone,
  validCrmPhone,
} from "@/lib/crm-phone";
import { cn } from "@/lib/utils";

import "react-phone-number-input/style.css";
import "./crm-phone-field.css";

export { validCrmPhone };

type Tx = (key: string, fallback: string) => string;

type Props = {
  value: string;
  onChange: (value: string) => void;
  defaultCountry?: string;
  locale?: string;
  disabled?: boolean;
  tx: Tx;
};

type CountrySelectProps = {
  value?: Country;
  onChange: (value?: Country) => void;
  disabled?: boolean;
  locale?: string;
  tx: Tx;
};

function resolveCountry(code?: string): Country {
  const upper = String(code || "FR").toUpperCase();
  return isSupportedCountry(upper) ? (upper as Country) : "FR";
}

function PhoneCountrySelect({ value, onChange, disabled, locale = "fr", tx }: CountrySelectProps) {
  return (
    <div className="PhoneInputCountry">
      <SearchableSelect
        value={value || ""}
        onChange={(next) => onChange((next || undefined) as Country | undefined)}
        options={callingCodeOptions(locale)}
        disabled={disabled}
        placeholder={tx("crm.pickPhoneCountry", "Indicatif")}
        searchPlaceholder={tx("crm.searchPhone", "Pays, FR, +33...")}
        emptyLabel={tx("crm.noResults", "Aucun resultat")}
        className="crm-phone-country-select h-10 w-auto min-w-[7.25rem] max-w-[10.5rem] shrink-0"
      />
    </div>
  );
}

export function CrmPhoneField({
  value,
  onChange,
  defaultCountry = "FR",
  locale = "fr",
  disabled,
  tx,
}: Props) {
  const country = resolveCountry(defaultCountry);
  const status = crmPhoneStatus(value, country);
  const hint = crmPhoneHint(status, tx);
  return (
    <div className="grid min-w-0 gap-1">
      <PhoneInput
        international={false}
        countryCallingCodeEditable={false}
        addInternationalOption={false}
        defaultCountry={country}
        value={value || undefined}
        onChange={(next) => {
          const raw = next || "";
          onChange(isCallingCodeOnly(raw) ? "" : normalizeCrmPhone(raw, country));
        }}
        disabled={disabled}
        className={cn("crm-phone-field", disabled && "opacity-50")}
        numberInputProps={{
          className: "PhoneInputInput",
          placeholder: tx("crm.phoneNational", "Numero"),
        }}
        countrySelectComponent={(props) => (
          <PhoneCountrySelect {...props} locale={locale} tx={tx} />
        )}
      />
      {hint ? <span className="text-[11px] text-muted-foreground">{hint}</span> : null}
    </div>
  );
}
