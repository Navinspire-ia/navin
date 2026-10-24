// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * "Use a site login" for demo capture: pick a site saved under Settings >
 * Sites & logins and the run (or the chat draft) starts by logging in with
 * its secret refs. Only refs travel; the password never enters the chat.
 * "Add a login" opens the same form inline, so nobody leaves the desk.
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { KeyRound, Plus, Settings2, X } from "lucide-react";

import { SiteLoginsPanel } from "@/components/settings/SiteLoginsPanel";
import { Button } from "@/components/ui/button";
import {
  listBrowserCredentials,
  siteLoginInstruction,
  type BrowserCredentialSite,
  type BrowserCredentialsSnapshot,
} from "@/lib/browser-credentials-api";
import { cn } from "@/lib/utils";

export function SiteLoginPicker({
  token,
  value,
  onChange,
  onInsert,
  onAddSite,
  className,
}: {
  token: string | null | undefined;
  value: BrowserCredentialSite | null;
  onChange: (next: BrowserCredentialSite | null) => void;
  onInsert?: (text: string) => void;
  onAddSite?: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => String(t(`settings.sites.${key}`, { defaultValue: fallback }));
  const [sites, setSites] = useState<BrowserCredentialSite[] | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    listBrowserCredentials(token, controller.signal)
      .then((snapshot) => setSites(snapshot.sites.filter((entry) => entry.names.length > 0)))
      .catch(() => setSites([]));
    return () => controller.abort();
  }, [token]);

  // A site deleted in Settings must not stay selected here.
  useEffect(() => {
    if (value && sites && !sites.some((entry) => entry.site === value.site)) onChange(null);
  }, [onChange, sites, value]);

  const applySaved = (site: string, snapshot: BrowserCredentialsSnapshot) => {
    const usable = snapshot.sites.filter((entry) => entry.names.length > 0);
    setSites(usable);
    setAdding(false);
    const saved = usable.find((entry) => entry.site === site);
    if (saved) onChange(saved);
  };

  if (sites === null) return null;

  const addButton = (
    <Button
      type="button"
      size="sm"
      variant={adding ? "secondary" : "outline"}
      className="h-8 rounded-full px-3 text-[12px]"
      onClick={() => setAdding((open) => !open)}
      aria-expanded={adding}
      data-testid="site-login-picker-add"
    >
      {adding ? <X className="me-1.5 h-3.5 w-3.5" aria-hidden /> : <Plus className="me-1.5 h-3.5 w-3.5" aria-hidden />}
      {adding ? tx("pickerAddClose", "Close") : sites.length === 0 ? tx("pickerAdd", "Add a site login") : tx("pickerAddAnother", "Add a login")}
    </Button>
  );

  return (
    <div
      className={cn("flex flex-wrap items-center gap-2 rounded-2xl border border-border/55 bg-card/40 px-3.5 py-2.5", className)}
      data-testid="site-login-picker"
    >
      <KeyRound className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <label className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-[12.5px] font-medium text-foreground">
        <span>{tx("pickerLabel", "Use a site login")}</span>
        {sites.length > 0 ? (
          <select
            value={value?.site ?? ""}
            onChange={(event) => onChange(sites.find((entry) => entry.site === event.target.value) ?? null)}
            className="h-8 min-w-[12rem] rounded-full border border-input bg-background px-3 text-[12.5px] font-normal text-foreground shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            dir="ltr"
          >
            <option value="">{tx("pickerNone", "No site login")}</option>
            {sites.map((entry) => (
              <option key={entry.site} value={entry.site}>
                {entry.site}
              </option>
            ))}
          </select>
        ) : null}
      </label>
      {value && onInsert ? (
        <Button type="button" size="sm" variant="ghost" className="h-8 rounded-full px-3 text-[12px]" onClick={() => onInsert(siteLoginInstruction(value))}>
          {tx("pickerInsert", "Insert in chat")}
        </Button>
      ) : null}
      {token ? addButton : null}
      {onAddSite ? (
        <button
          type="button"
          onClick={onAddSite}
          className="inline-flex items-center gap-1 px-1 text-[11.5px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        >
          <Settings2 className="h-3 w-3" aria-hidden />
          {tx("pickerManage", "Manage in settings")}
        </button>
      ) : null}
      {adding && token ? (
        <SiteLoginsPanel compact hideList token={token} onSaved={applySaved} className="w-full" />
      ) : null}
      {value ? (
        <p className="w-full text-[11.5px] leading-relaxed text-muted-foreground">
          {tx("pickerHint", "Runs and chat drafts start by logging in with the saved refs. The password never enters the chat.")}
        </p>
      ) : null}
    </div>
  );
}
