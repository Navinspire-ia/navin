// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Sites & logins, embeddable: the URL and login of an app (yours or an
 * external one) so agents can sign in and record demo videos. Values go to
 * the gateway vault and never come back; the agent only ever sees refs such
 * as site:app.example.com/password, so a password never enters the chat.
 *
 * The full form is Settings > Sites & logins. The compact form drops the
 * same list and add form inline (Dev preview, Marketing, Montage) so a login
 * can be saved where it is needed, without leaving the desk.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy, Globe2, KeyRound, Loader2, LogOut, Pencil, Plus, Trash2, X } from "lucide-react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  deleteBrowserCredentialSite,
  forgetBrowserSession,
  listBrowserCredentials,
  normalizeSite,
  removeBrowserCredential,
  saveBrowserCredentials,
  SECRET_REF_PREFIX,
  type BrowserCredentialSite,
  type BrowserCredentialsSnapshot,
} from "@/lib/browser-credentials-api";
import { copyTextOrNotify } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="mb-2 px-1 text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">{children}</h2>;
}

function Group({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.075)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.24)]", className)}>
      <div className="divide-y divide-border/45">{children}</div>
    </div>
  );
}

function Row({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return (
    <div className="flex min-h-[62px] flex-col gap-3 px-4 py-3.5 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-x-9 sm:px-5">
      <div className="min-w-0 sm:flex-[1_1_12rem]">
        <div className="text-[14px] font-medium leading-5 text-foreground">{title}</div>
        {description ? <div className="mt-0.5 max-w-[40rem] text-[12px] leading-5 text-muted-foreground">{description}</div> : null}
      </div>
      {children ? <div className="min-w-0 max-w-full sm:shrink-0">{children}</div> : null}
    </div>
  );
}

function Field({ label, hint, children, className }: { label: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cn("block min-w-0", className)}>
      <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-[11.5px] leading-4 text-muted-foreground/85">{hint}</span> : null}
    </label>
  );
}

function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" }) {
  return (
    <span
      className={cn(
        "shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        tone === "good"
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : "border-border/60 text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}

export interface SiteLoginsPanelProps {
  token: string | null | undefined;
  /** Inline layout: condensed list, no vault row, smaller form. */
  compact?: boolean;
  /** Prefill the App URL field (e.g. the running preview). */
  site?: string;
  /** Hide the saved list and only show the add form. */
  hideList?: boolean;
  /** Fired after a successful save with the normalized site and the fresh snapshot. */
  onSaved?: (site: string, snapshot: BrowserCredentialsSnapshot) => void;
  /** Fired whenever the list is loaded or changes. */
  onChange?: (snapshot: BrowserCredentialsSnapshot) => void;
  className?: string;
}

export function SiteLoginsPanel({
  token: tokenProp,
  compact = false,
  site: prefill,
  hideList = false,
  onSaved,
  onChange,
  className,
}: SiteLoginsPanelProps) {
  const token = tokenProp ?? "";
  const { t, i18n } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string, values?: Record<string, unknown>) =>
      String(t(`settings.sites.${key}`, { defaultValue: fallback, ...(values ?? {}) })),
    [t],
  );

  const [snapshot, setSnapshot] = useState<BrowserCredentialsSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [siteInput, setSiteInput] = useState(prefill ?? "");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // A new prefill (another preview URL) replaces an untouched field only.
  const prefillRef = useRef(prefill ?? "");
  useEffect(() => {
    const next = prefill ?? "";
    setSiteInput((current) => (current === prefillRef.current ? next : current));
    prefillRef.current = next;
  }, [prefill]);

  const applySnapshot = useCallback((next: BrowserCredentialsSnapshot) => {
    setSnapshot(next);
    onChangeRef.current?.(next);
  }, []);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      applySnapshot(await listBrowserCredentials(token));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [applySnapshot, token]);

  useEffect(() => {
    void load();
  }, [load]);

  const resetForm = () => {
    setEditing(null);
    setSiteInput(prefill ?? "");
    setUsername("");
    setPassword("");
    setTotp("");
  };

  const lastSnapshot = useRef<BrowserCredentialsSnapshot | null>(null);
  const run = async (key: string, action: () => Promise<BrowserCredentialsSnapshot>, done?: string) => {
    if (!token || busy) return false;
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      const next = await action();
      applySnapshot(next);
      lastSnapshot.current = next;
      if (done) setNotice(done);
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const site = normalizeSite(siteInput);
  const hasValue = Boolean(username.trim() || password.trim() || totp.trim());
  const canSave = Boolean(site) && hasValue && !busy;

  const save = async () => {
    if (!canSave) return;
    const target = site;
    const ok = await run(
      "save",
      () => saveBrowserCredentials(token, target, { username, password, totp }),
      tx("saved", "Saved for {{site}}. The password is stored in the vault and not shown again.", { site: target }),
    );
    // Secrets never stay in the form (nor in the DOM) once they are saved.
    setPassword("");
    setTotp("");
    if (ok) {
      setUsername("");
      setSiteInput(compact ? "" : prefill ?? "");
      setEditing(null);
      if (lastSnapshot.current) onSaved?.(target, lastSnapshot.current);
    }
  };

  const removeLogin = (entry: BrowserCredentialSite) =>
    run(
      `login:${entry.site}`,
      async () => {
        let next: BrowserCredentialsSnapshot | null = null;
        for (const name of entry.names) next = await removeBrowserCredential(token, entry.site, name);
        return next ?? (await listBrowserCredentials(token));
      },
      tx("loginRemoved", "Login removed for {{site}}.", { site: entry.site }),
    );

  const forgetSession = (entry: BrowserCredentialSite) =>
    run(
      `session:${entry.site}`,
      () => forgetBrowserSession(token, entry.site),
      tx("sessionForgotten", "Saved session forgotten for {{site}}. The next run signs in again.", { site: entry.site }),
    );

  const deleteSite = async (target: string) => {
    setPendingDelete(null);
    const ok = await run(`delete:${target}`, () => deleteBrowserCredentialSite(token, target), tx("siteDeleted", "{{site}} deleted.", { site: target }));
    if (ok && editing === target) resetForm();
  };

  const startEdit = (entry: BrowserCredentialSite) => {
    setEditing(entry.site);
    setSiteInput(entry.site);
    setUsername("");
    setPassword("");
    setTotp("");
    setNotice(null);
    setError(null);
  };

  const copy = async (text: string) => {
    if (await copyTextOrNotify(text)) {
      setCopied(text);
      window.setTimeout(() => setCopied((current) => (current === text ? null : current)), 1600);
    }
  };

  const nameLabel = (name: string) =>
    name === "username"
      ? tx("names.username", "Username")
      : name === "password"
        ? tx("names.password", "Password")
        : name === "totp"
          ? tx("names.totp", "2FA")
          : name;

  const updatedLabel = (updated?: number | null) => {
    if (!updated) return null;
    try {
      return new Intl.DateTimeFormat(i18n.language === "ar" ? "ar-u-nu-latn" : i18n.language, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(updated * 1000));
    } catch {
      return null;
    }
  };

  const sites = snapshot?.sites ?? [];
  const vault = snapshot?.vault;

  const formNode = (
    <form
      className={cn("grid gap-4", compact ? "gap-3 sm:grid-cols-2" : "px-4 py-4 sm:grid-cols-2 sm:px-5")}
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
      autoComplete="off"
      data-testid="site-login-form"
    >
      <Field
        label={tx("url", "App URL")}
        className="sm:col-span-2"
        hint={
          siteInput.trim()
            ? site
              ? tx("savedAs", "Saved as {{site}}", { site })
              : tx("invalidUrl", "Enter a host or URL, e.g. https://app.example.com or localhost:5173.")
            : tx("urlHint", "Your app or an external one. Only the host (and a non-default port) is kept.")
        }
      >
        <Input
          value={siteInput}
          onChange={(event) => setSiteInput(event.target.value)}
          placeholder="https://app.example.com"
          className="h-9 rounded-full text-[13px]"
          dir="ltr"
          inputMode="url"
          disabled={Boolean(editing)}
          data-testid="site-url"
        />
      </Field>
      <Field label={tx("names.username", "Username")} hint={editing ? tx("keepHint", "Leave empty to keep the saved value.") : undefined}>
        <Input
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          placeholder={tx("usernamePlaceholder", "user@example.com")}
          className="h-9 rounded-full text-[13px]"
          dir="ltr"
          autoComplete="off"
          data-testid="site-username"
        />
      </Field>
      <Field label={tx("names.password", "Password")} hint={editing ? tx("keepHint", "Leave empty to keep the saved value.") : tx("passwordHint", "Stored in the vault, never shown again.")}>
        <Input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="h-9 rounded-full text-[13px]"
          dir="ltr"
          autoComplete="new-password"
          data-testid="site-password"
        />
      </Field>
      <Field
        label={tx("totp", "2FA seed or otpauth URL (optional)")}
        className="sm:col-span-2"
        hint={compact ? undefined : tx("totpHint", "The base32 secret or otpauth:// link from the app's 2FA setup. Agents get the 6-digit code, never the seed.")}
      >
        <Input
          type="password"
          value={totp}
          onChange={(event) => setTotp(event.target.value)}
          className="h-9 rounded-full text-[13px]"
          dir="ltr"
          autoComplete="off"
          data-testid="site-totp"
        />
      </Field>
      <div className="flex flex-col gap-3 sm:col-span-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 text-[13px] leading-5" aria-live="polite">
          {error ? (
            <span role="alert" className="font-medium text-destructive">{error}</span>
          ) : notice ? (
            <span className="font-medium text-blue-600 dark:text-blue-300">{notice}</span>
          ) : (
            <span className="text-muted-foreground">{tx("privacyNote", "Never paste a password in the chat: save it here and the agent logs in with the ref.")}</span>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          {editing ? (
            <Button type="button" size="sm" variant="ghost" className="rounded-full" onClick={resetForm} disabled={busy === "save"}>
              {t("settings.actions.cancel")}
            </Button>
          ) : null}
          <Button type="submit" size="sm" variant={canSave ? "default" : "outline"} className="rounded-full" disabled={!canSave} data-testid="site-save">
            {busy === "save" ? (
              <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : editing ? null : (
              <Plus className="me-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {busy === "save" ? t("settings.actions.saving") : t("settings.actions.save")}
          </Button>
        </div>
      </div>
    </form>
  );

  const deleteDialog = (
    <ConfirmDialog
      open={pendingDelete !== null}
      title={tx("deleteConfirmTitle", "Delete {{site}}?", { site: pendingDelete ?? "" })}
      description={tx("deleteConfirmBody", "Its login and saved browser session are removed. Agents can no longer sign in to it.")}
      confirmLabel={tx("deleteSite", "Delete site")}
      onCancel={() => setPendingDelete(null)}
      onConfirm={() => {
        if (pendingDelete) void deleteSite(pendingDelete);
      }}
    />
  );

  if (compact) {
    return (
      <div
        className={cn("flex flex-col gap-3 rounded-2xl border border-border/55 bg-card/70 px-3.5 py-3 shadow-sm", className)}
        data-testid="site-logins-panel"
      >
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-[10px] bg-muted text-foreground/80">
            <KeyRound className="h-3.5 w-3.5" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="text-[13px] font-medium leading-5 text-foreground">
              {editing ? tx("editTitle", "Edit {{site}}", { site: editing }) : tx("panelTitle", "Site logins")}
            </div>
            <div className="text-[12px] leading-5 text-muted-foreground">
              {tx("panelHelp", "Save a login once. Agents sign in with its refs and never see the password.")}
            </div>
          </div>
        </div>
        {hideList ? null : loading && !snapshot ? (
          <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {t("settings.status.loading")}
          </div>
        ) : sites.length > 0 ? (
          <ul className="flex flex-col divide-y divide-border/45 rounded-xl border border-border/45">
            {sites.map((entry) => (
              <li key={entry.site} className="flex flex-wrap items-center gap-2 px-3 py-2" data-testid="site-row">
                <Globe2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <span className="min-w-0 truncate text-[12.5px] font-medium text-foreground" dir="ltr">{entry.site}</span>
                {entry.names.map((name) => (
                  <Pill key={name}>{nameLabel(name)}</Pill>
                ))}
                {entry.session ? <Pill tone="good">{tx("sessionSaved", "Session saved")}</Pill> : null}
                <span className="ms-auto flex items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 rounded-full text-muted-foreground"
                    disabled={busy !== null}
                    onClick={() => startEdit(entry)}
                    aria-label={tx("editNamed", "Edit {{site}}", { site: entry.site })}
                    title={tx("edit", "Edit")}
                  >
                    <Pencil className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 rounded-full text-muted-foreground hover:text-destructive"
                    disabled={busy !== null}
                    onClick={() => setPendingDelete(entry.site)}
                    aria-label={tx("deleteNamed", "Delete {{site}}", { site: entry.site })}
                    title={tx("deleteSite", "Delete site")}
                  >
                    {busy === `delete:${entry.site}` ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    )}
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {formNode}
        {deleteDialog}
      </div>
    );
  }

  return (
    <div className={cn("space-y-7", className)} data-testid="sites-settings">
      <section>
        <SectionTitle>{tx("listTitle", "Saved sites")}</SectionTitle>
        <Group>
          <Row
            title={tx("vaultTitle", "Where logins are stored")}
            description={
              vault === "keyring"
                ? tx("vaultKeyring", "In your system keychain. Agents get refs like site:app.example.com/password, never the values.")
                : vault
                  ? tx("vaultFile", "In an encrypted file on this machine (no system keychain found). Agents get refs, never the values.")
                  : tx("vaultUnknown", "Agents get refs like site:app.example.com/password, never the values.")
            }
          >
            {vault ? (
              <Pill tone="good">{vault === "keyring" ? tx("vaultKeyringShort", "Keychain") : tx("vaultFileShort", "Encrypted file")}</Pill>
            ) : null}
          </Row>
          {loading && !snapshot ? (
            <div className="flex items-center gap-2 px-4 py-3.5 text-[13px] text-muted-foreground sm:px-5">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              {t("settings.status.loading")}
            </div>
          ) : sites.length === 0 ? (
            <Row
              title={tx("empty", "No site saved yet")}
              description={tx("emptyHelp", "Add the URL and login of an app below so agents can sign in and record a demo without you typing the password in the chat.")}
            />
          ) : (
            sites.map((entry) => {
              const updated = updatedLabel(entry.updated);
              const hint = tx("askHint", "Ask the agent: log in with {{ref}} and record a demo", { ref: `${SECRET_REF_PREFIX}${entry.site}` });
              const rowBusy = busy !== null;
              return (
                <div key={entry.site} className="flex flex-col gap-3 px-4 py-3.5 sm:px-5" data-testid="site-row">
                  <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between sm:gap-x-9">
                    <div className="flex min-w-0 items-start gap-3 sm:flex-[1_1_12rem]">
                      <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-[12px] bg-muted text-foreground/80">
                        <Globe2 className="h-4 w-4" aria-hidden />
                      </span>
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[14px] font-medium leading-5 text-foreground" dir="ltr">{entry.site}</span>
                          {entry.names.map((name) => (
                            <Pill key={name}>{nameLabel(name)}</Pill>
                          ))}
                          {entry.session ? <Pill tone="good">{tx("sessionSaved", "Session saved")}</Pill> : null}
                        </div>
                        {updated ? (
                          <div className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
                            {tx("updated", "Updated {{date}}", { date: updated })}
                          </div>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                      <Button type="button" variant="outline" size="sm" className="h-8 rounded-full px-3 text-[12px]" disabled={rowBusy} onClick={() => startEdit(entry)}>
                        <Pencil className="me-1.5 h-3.5 w-3.5" aria-hidden />
                        {tx("edit", "Edit")}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-8 rounded-full px-3 text-[12px]"
                        disabled={rowBusy || entry.names.length === 0}
                        onClick={() => void removeLogin(entry)}
                      >
                        {busy === `login:${entry.site}` ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <X className="me-1.5 h-3.5 w-3.5" aria-hidden />}
                        {tx("removeLogin", "Remove login")}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-8 rounded-full px-3 text-[12px]"
                        disabled={rowBusy || !entry.session}
                        onClick={() => void forgetSession(entry)}
                      >
                        {busy === `session:${entry.site}` ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <LogOut className="me-1.5 h-3.5 w-3.5" aria-hidden />}
                        {tx("forgetSession", "Forget saved session")}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-8 rounded-full px-3 text-[12px] text-destructive hover:text-destructive"
                        disabled={rowBusy}
                        onClick={() => setPendingDelete(entry.site)}
                      >
                        {busy === `delete:${entry.site}` ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <Trash2 className="me-1.5 h-3.5 w-3.5" aria-hidden />}
                        {tx("deleteSite", "Delete site")}
                      </Button>
                    </div>
                  </div>
                  {entry.refs.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5 sm:ps-11">
                      {entry.refs.map((ref) => (
                        <button
                          key={ref}
                          type="button"
                          onClick={() => void copy(ref)}
                          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border/60 bg-muted/40 px-2.5 py-1 font-mono text-[11.5px] text-foreground/85 transition-colors hover:bg-muted active:scale-[0.97]"
                          title={tx("copyRef", "Copy ref")}
                          aria-label={tx("copyRefNamed", "Copy {{ref}}", { ref })}
                          dir="ltr"
                        >
                          <span className="truncate">{ref}</span>
                          {copied === ref ? <Check className="h-3 w-3 shrink-0 text-emerald-600" aria-hidden /> : <Copy className="h-3 w-3 shrink-0 opacity-70" aria-hidden />}
                        </button>
                      ))}
                    </div>
                  ) : null}
                  <div className="flex flex-wrap items-center gap-2 text-[12px] leading-5 text-muted-foreground sm:ps-11">
                    <span>{hint}</span>
                    <button
                      type="button"
                      onClick={() => void copy(hint)}
                      className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 font-medium text-foreground/80 hover:bg-muted"
                      aria-label={tx("copyHint", "Copy this request")}
                    >
                      {copied === hint ? <Check className="h-3 w-3 text-emerald-600" aria-hidden /> : <Copy className="h-3 w-3" aria-hidden />}
                      {copied === hint ? tx("copied", "Copied") : tx("copy", "Copy")}
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </Group>
      </section>

      <section>
        <SectionTitle>{editing ? tx("editTitle", "Edit {{site}}", { site: editing }) : tx("addTitle", "Add a site login")}</SectionTitle>
        <Group>
          {formNode}
        </Group>
      </section>

      {deleteDialog}
    </div>
  );
}
