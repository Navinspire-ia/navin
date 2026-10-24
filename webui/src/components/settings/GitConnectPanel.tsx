// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Git & GitHub sign-in, embeddable. The full form is Settings > Git & GitHub
 * (connected accounts, test, remove, connect). The compact form is the same
 * connect step dropped inline where a push, a pull request or a clone just
 * needed it: device flow first, a token as the fallback, then `onConnected`
 * so the caller can retry right away.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import {
  Check,
  ExternalLink,
  GitBranch,
  Github,
  Gitlab,
  KeyRound,
  Loader2,
  Plus,
  RotateCcw,
  Settings2,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { GitDeviceFlow } from "@/components/settings/GitDeviceFlow";
import { GitMachineStatus } from "@/components/settings/GitMachineStatus";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  fetchGitAccounts,
  openInOsBrowser,
  setupGitAccount,
  testGitAccount,
  updateSettings,
} from "@/lib/api";
import {
  createTokenUrl,
  GIT_TOKEN_KINDS,
  guessForgeKind,
  type StoredGitTokenKind,
} from "@/lib/git-auth";
import type {
  GitAccount,
  GitAccountsPayload,
  GitAccountTestPayload,
  GitSetupAction,
  GitSetupPayload,
  SettingsPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * A credential check per host. `busy` while the forge answers, then the
 * test payload; a 404 (engine without the route) becomes a readable error.
 */
type GitTestResult = { busy: true } | ({ busy?: false } & GitAccountTestPayload);

type ForgeSettings = SettingsPayload["forge"];

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function cleanHost(value: string): string {
  return value.trim().replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
}

function GitKindIcon({ kind, className }: { kind: string; className?: string }) {
  const Icon = kind === "github" ? Github : kind === "gitlab" ? Gitlab : GitBranch;
  return <Icon className={className} aria-hidden />;
}

function gitAccountsFromForge(forge: ForgeSettings | undefined): GitAccount[] {
  return (forge?.hosts ?? []).map((row) => ({
    host: row.host,
    kind: row.kind,
    login: null,
    source: "settings" as const,
    scopes: null,
  }));
}

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="mb-2 px-1 text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
      {children}
    </h2>
  );
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
        {description ? (
          <div className="mt-0.5 max-w-[40rem] whitespace-pre-line text-[12px] leading-5 text-muted-foreground">
            {description}
          </div>
        ) : null}
      </div>
      {children ? <div className="min-w-0 max-w-full sm:shrink-0">{children}</div> : null}
    </div>
  );
}

export interface GitConnectPanelProps {
  token: string | null | undefined;
  /** Inline card (device flow + token) instead of the full Settings section. */
  compact?: boolean;
  /** Host to connect; prefills the token form. Defaults to github.com. */
  host?: string;
  /** Fired once a device sign-in or a saved token works for `host`. */
  onConnected?: (login: string | null) => void;
  /** Compact only: a secondary link to the full Settings section. */
  onOpenSettings?: () => void;
  /** Settings forge snapshot, for token hints and an engine without the accounts route. */
  forge?: ForgeSettings;
  /**
   * Session whose project git gets the setup (commit identity, checks); the
   * host git when omitted. Matters for a WSL project opened from Windows.
   */
  session?: string | null;
  /** What the automatic setup did after a connect (identity, scopes, machine). */
  onSetup?: (result: GitSetupPayload) => void;
  className?: string;
}

export function GitConnectPanel({
  token,
  compact = false,
  host: hostProp,
  onConnected,
  onOpenSettings,
  forge: forgeProp,
  session,
  onSetup,
  className,
}: GitConnectPanelProps) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    String(t(key, { defaultValue: fallback, ...(values ?? {}) }));
  const targetHost = cleanHost(hostProp ?? "") || "github.com";
  const [forge, setForge] = useState(forgeProp);
  const [payload, setPayload] = useState<GitAccountsPayload | null>(null);
  const [accountsUnavailable, setAccountsUnavailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, GitTestResult>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [host, setHost] = useState(targetHost);
  const [secret, setSecret] = useState("");
  const [kind, setKind] = useState<StoredGitTokenKind>("auto");
  const [tokenFormOpen, setTokenFormOpen] = useState(false);
  const [connectedLogin, setConnectedLogin] = useState<string | null | undefined>(undefined);
  const [setupActions, setSetupActions] = useState<GitSetupAction[] | undefined>(undefined);
  const [machineKey, setMachineKey] = useState(0);
  const [machineAvailable, setMachineAvailable] = useState(true);
  const onSetupRef = useRef(onSetup);
  onSetupRef.current = onSetup;

  useEffect(() => {
    setForge(forgeProp);
  }, [forgeProp]);

  useEffect(() => {
    setHost(targetHost);
  }, [targetHost]);

  const loadAccounts = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setLoadError(null);
    try {
      setPayload(await fetchGitAccounts(token));
      setAccountsUnavailable(false);
    } catch (err) {
      // An engine without the accounts route still has Settings tokens.
      if (err instanceof ApiError && err.status === 404) {
        setAccountsUnavailable(true);
      } else {
        setLoadError(errorText(err));
      }
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  const accounts: GitAccount[] = payload?.accounts ?? gitAccountsFromForge(forge);
  const deviceFlowGithub = payload?.device_flow?.github === true;
  const hostIsGithub = guessForgeKind(targetHost) === "github";
  const hintFor = (accountHost: string) =>
    forge?.hosts.find((row) => row.host === accountHost)?.token_hint ?? null;
  const envNames = accountsUnavailable ? (forge?.env_tokens ?? []).map((entry) => entry.name) : [];

  const runTest = useCallback(
    async (target: string): Promise<GitAccountTestPayload> => {
      if (!token) return { ok: false };
      setTests((prev) => ({ ...prev, [target]: { busy: true } }));
      // Test and set git up in one go: the engine checks the token, fills a
      // missing commit identity from the account and reports the machine.
      try {
        const setup = await setupGitAccount(token, target, { session });
        const result: GitAccountTestPayload = {
          ok: setup.ok,
          login: setup.login ?? undefined,
          scopes: setup.scopes ?? undefined,
          error: setup.error ?? undefined,
        };
        setTests((prev) => ({ ...prev, [target]: result }));
        setSetupActions(setup.actions);
        setMachineKey((value) => value + 1);
        onSetupRef.current?.(setup);
        return result;
      } catch (err) {
        // An engine without the setup route still answers the plain test.
        if (!(err instanceof ApiError && err.status === 404)) {
          const message = errorText(err);
          setTests((prev) => ({ ...prev, [target]: { ok: false, error: message } }));
          return { ok: false, error: message };
        }
      }
      try {
        const result = await testGitAccount(token, target);
        setTests((prev) => ({ ...prev, [target]: result }));
        return result;
      } catch (err) {
        const unavailable = err instanceof ApiError && err.status === 404;
        const message = unavailable
          ? tx("settings.git.testUnavailable", "Testing needs a newer engine. Restart Navin after updating.")
          : errorText(err);
        setTests((prev) => ({ ...prev, [target]: { ok: false, error: message } }));
        // An engine that cannot test still stored the token: let the caller retry.
        return unavailable ? { ok: true } : { ok: false, error: message };
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [token, session],
  );

  const markConnected = (login: string | null) => {
    setConnectedLogin(login);
    onConnected?.(login);
  };

  const removeAccount = async (target: string) => {
    if (!token) return;
    setBusy(`remove:${target}`);
    setError(null);
    try {
      const next = await updateSettings(token, { forgeHost: target, forgeRemove: true });
      setForge(next.forge);
      setTests((prev) => {
        const copy = { ...prev };
        delete copy[target];
        return copy;
      });
      await loadAccounts();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const saveToken = async () => {
    const cleaned = cleanHost(host);
    if (!token || !cleaned || !secret.trim()) return;
    setBusy("save");
    setError(null);
    try {
      const next = await updateSettings(token, {
        forgeHost: cleaned,
        forgeToken: secret.trim(),
        forgeKind: kind,
      });
      setForge(next.forge);
      setSecret("");
      setKind("auto");
      setHost(targetHost);
      setTokenFormOpen(false);
      await loadAccounts();
      // Say right away whether the token works, not at the first failed clone.
      const result = await runTest(cleaned);
      if (result.ok) markConnected(result.login ?? null);
      else if (compact) {
        setTokenFormOpen(true);
        setError(result.error || tx("settings.git.testFailed", "This credential was refused."));
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const tokenUrl = createTokenUrl(host, kind);

  // Compact cards are dropped inside other forms (the Import Git field): a
  // nested <form> would submit the host, so they get a group and Enter.
  const TokenFormTag = compact ? "div" : "form";
  const tokenForm = (
    <TokenFormTag
      className={cn("flex flex-col gap-2", compact ? "" : "px-4 py-3.5 sm:px-5")}
      {...(compact
        ? {
            role: "group",
            onKeyDown: (event: KeyboardEvent) => {
              if (event.key !== "Enter" || (event.target as HTMLElement).tagName !== "INPUT") return;
              event.preventDefault();
              event.stopPropagation();
              void saveToken();
            },
          }
        : {
            onSubmit: (event: FormEvent) => {
              event.preventDefault();
              event.stopPropagation();
              void saveToken();
            },
          })}
      data-testid="git-token-form"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={host}
          onChange={(event) => setHost(event.target.value)}
          placeholder="github.com"
          className={cn("h-9 rounded-full text-[13px]", compact ? "w-[160px]" : "w-[200px]")}
          dir="ltr"
          aria-label={tx("settings.rows.forgeHost", "Host")}
        />
        <select
          value={kind}
          onChange={(event) => setKind(event.target.value as StoredGitTokenKind)}
          className="h-9 rounded-full border border-input bg-background px-3 text-[13px]"
          aria-label={tx("settings.rows.forgeKind", "Forge type")}
        >
          {GIT_TOKEN_KINDS.map((option) => (
            <option key={option} value={option}>
              {option === "auto"
                ? tx("settings.rows.forgeKindAuto", "Detect automatically")
                : tx(`settings.git.kind.${option}`, option)}
            </option>
          ))}
        </select>
        <Input
          value={secret}
          onChange={(event) => setSecret(event.target.value)}
          type="password"
          autoComplete="off"
          dir="ltr"
          placeholder={tx("settings.rows.forgeTokenPlaceholder", "Access token")}
          className={cn("h-9 rounded-full text-[13px]", compact ? "min-w-[180px] flex-1" : "w-[240px]")}
          aria-label={tx("settings.rows.forgeTokenLabel", "Token")}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type={compact ? "button" : "submit"}
          onClick={compact ? () => void saveToken() : undefined}
          disabled={busy !== null || !host.trim() || !secret.trim()}
          className="h-9 rounded-full text-[13px]"
        >
          {busy === "save" ? (
            <Loader2 className="me-1 h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <Plus className="me-1 h-3.5 w-3.5" aria-hidden />
          )}
          {tx("settings.git.saveAndTest", "Save and test")}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            setTokenFormOpen(false);
            setSecret("");
          }}
          className="h-9 rounded-full text-[13px] text-muted-foreground"
        >
          {tx("settings.git.cancel", "Cancel")}
        </Button>
        {tokenUrl ? (
          <button
            type="button"
            onClick={() => token && void openInOsBrowser(token, tokenUrl)}
            className="inline-flex items-center gap-1 text-[12px] font-medium text-foreground/80 underline-offset-2 hover:text-foreground hover:underline"
          >
            {tx("settings.git.createToken", "Create a token")}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </button>
        ) : null}
      </div>
      <p className="text-[11.5px] leading-5 text-muted-foreground">
        {tx("settings.git.scopesHelp", "On GitHub, give it the scopes repo, read:org and workflow.")}
      </p>
    </TokenFormTag>
  );

  if (compact) {
    const showDeviceFlow = deviceFlowGithub && hostIsGithub;
    // Without a device flow the token is the only way in: show it right away.
    const showTokenForm = tokenFormOpen || (!loading && !showDeviceFlow);
    return (
      <div
        className={cn(
          "flex flex-col gap-3 rounded-2xl border border-border/55 bg-card/70 px-3.5 py-3 shadow-sm",
          className,
        )}
        data-testid="git-connect-panel"
      >
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-[10px] bg-muted text-foreground/80">
            <GitKindIcon kind={guessForgeKind(targetHost) ?? "git"} className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <div className="text-[13px] font-medium leading-5 text-foreground">
              {tx("settings.git.connectHostTitle", "Connect {{host}}", { host: targetHost })}
            </div>
            <div className="text-[12px] leading-5 text-muted-foreground">
              {tx(
                "settings.git.connectHostHelp",
                "Sign in once so Navin can clone, push, open pull requests and read CI for this host.",
              )}
            </div>
          </div>
        </div>
        {connectedLogin !== undefined ? (
          <>
            <div
              className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-emerald-700 dark:text-emerald-300"
              data-testid="git-connect-ok"
            >
              <Check className="h-4 w-4" aria-hidden />
              {connectedLogin
                ? tx("settings.git.deviceOk", "Signed in as {{login}}", { login: connectedLogin })
                : tx("settings.git.connectedNoLogin", "Connected")}
            </div>
            <GitMachineStatus
              token={token}
              session={session}
              compact
              refreshKey={machineKey}
              actions={setupActions}
              hosts={[targetHost]}
            />
          </>
        ) : (
          <>
            {loading && !payload ? (
              <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                {t("settings.status.loading")}
              </div>
            ) : null}
            {showDeviceFlow ? (
              <GitDeviceFlow
                token={token}
                host={targetHost}
                onConnected={(login) => {
                  void loadAccounts();
                  // Set git up for the new account before the caller retries.
                  void runTest(targetHost).finally(() => markConnected(login));
                }}
              />
            ) : null}
            {showTokenForm ? (
              tokenForm
            ) : !loading ? (
              <div>
                <button
                  type="button"
                  onClick={() => setTokenFormOpen(true)}
                  className="inline-flex items-center gap-1 text-[12px] font-medium text-foreground/80 underline-offset-2 hover:text-foreground hover:underline"
                >
                  <KeyRound className="h-3.5 w-3.5" aria-hidden />
                  {tx("settings.git.useTokenInstead", "Use a token instead")}
                </button>
              </div>
            ) : null}
          </>
        )}
        {error ? (
          <p role="alert" className="text-[12px] font-medium text-destructive">{error}</p>
        ) : null}
        {onOpenSettings ? (
          <div>
            <button
              type="button"
              onClick={onOpenSettings}
              className="inline-flex items-center gap-1 text-[11.5px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              <Settings2 className="h-3 w-3" aria-hidden />
              {tx("settings.git.openSettings", "Open Git settings")}
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  const sourceLabel = (source: GitAccount["source"]) =>
    source === "env"
      ? tx("settings.git.sourceEnv", "Environment")
      : source === "gh"
        ? tx("settings.git.sourceGh", "gh CLI")
        : tx("settings.git.sourceSettings", "Settings");

  return (
    <div className={cn("space-y-7", className)} data-testid="git-connect-panel-full">
      <section>
        <SectionTitle>{tx("settings.git.accountsTitle", "Connected accounts")}</SectionTitle>
        <Group>
          {loading && !payload && accounts.length === 0 ? (
            <div className="flex items-center gap-2 px-4 py-3.5 text-[13px] text-muted-foreground sm:px-5">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              {t("settings.status.loading")}
            </div>
          ) : accounts.length === 0 ? (
            <Row
              title={tx("settings.git.noAccounts", "No account connected yet")}
              description={tx(
                "settings.git.noAccountsHelp",
                "Sign in with GitHub or add a token below to clone private repositories and open pull requests.",
              )}
            />
          ) : (
            accounts.map((account) => {
              const test = tests[account.host];
              const hint = account.source === "settings" ? hintFor(account.host) : null;
              const details = [
                account.login ? `@${account.login}` : null,
                account.kind,
                hint,
                account.scopes?.length ? account.scopes.join(", ") : null,
              ].filter(Boolean).join(" · ");
              return (
                <div
                  key={`${account.source}:${account.host}`}
                  className="flex min-h-[62px] flex-col gap-3 px-4 py-3.5 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-x-9 sm:px-5"
                  data-testid="git-account-row"
                >
                  <div className="flex min-w-0 items-start gap-3 sm:flex-[1_1_12rem]">
                    <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-[12px] bg-muted text-foreground/80">
                      <GitKindIcon kind={account.kind} className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[14px] font-medium leading-5 text-foreground">{account.host}</span>
                        <span className="shrink-0 rounded-full border border-border/60 px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                          {sourceLabel(account.source)}
                        </span>
                      </div>
                      {details ? (
                        <div className="mt-0.5 text-[12px] leading-5 text-muted-foreground">{details}</div>
                      ) : null}
                      {test && !test.busy ? (
                        test.ok ? (
                          <div className="mt-1 inline-flex items-center gap-1 text-[12px] font-medium text-emerald-700 dark:text-emerald-300">
                            <Check className="h-3.5 w-3.5" aria-hidden />
                            {test.login
                              ? tx("settings.git.testOk", "Works, signed in as {{login}}", { login: test.login })
                              : tx("settings.git.testOkNoLogin", "Works")}
                            {test.scopes?.length ? ` (${test.scopes.join(", ")})` : ""}
                          </div>
                        ) : (
                          <div role="alert" className="mt-1 text-[12px] font-medium text-destructive">
                            {test.error || tx("settings.git.testFailed", "This credential was refused.")}
                          </div>
                        )
                      ) : null}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Button
                      type="button"
                      variant="outline"
                      disabled={Boolean(test?.busy) || accountsUnavailable}
                      title={
                        accountsUnavailable
                          ? tx("settings.git.testUnavailable", "Testing needs a newer engine. Restart Navin after updating.")
                          : undefined
                      }
                      onClick={() => void runTest(account.host)}
                      className="h-8 rounded-full px-3 text-[12px]"
                    >
                      {test?.busy ? (
                        <Loader2 className="me-1 h-3.5 w-3.5 animate-spin" aria-hidden />
                      ) : null}
                      {tx("settings.git.test", "Test")}
                    </Button>
                    {account.source === "settings" ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        disabled={busy !== null}
                        onClick={() => void removeAccount(account.host)}
                        aria-label={tx("settings.git.remove", "Remove {{host}}", { host: account.host })}
                        title={tx("settings.git.remove", "Remove {{host}}", { host: account.host })}
                        className="h-8 w-8 rounded-full text-muted-foreground hover:bg-muted hover:text-destructive"
                      >
                        {busy === `remove:${account.host}` ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5" aria-hidden />
                        )}
                      </Button>
                    ) : (
                      <span
                        className="max-w-[12rem] text-[11px] leading-4 text-muted-foreground"
                        title={tx("settings.git.externalHelp", "Managed outside Navin: change it where it was set.")}
                      >
                        {tx("settings.git.external", "Set outside Navin")}
                      </span>
                    )}
                  </div>
                </div>
              );
            })
          )}
          {envNames.length > 0 ? (
            <Row
              title={tx("settings.rows.forgeEnvTokens", "From the environment")}
              description={tx(
                "settings.help.forgeEnvTokens",
                "These variables are already set on this machine and are used when no token is stored above.",
              )}
            >
              <span className="font-mono text-[12px] text-muted-foreground">{envNames.join(", ")}</span>
            </Row>
          ) : null}
          {loadError ? (
            <div className="flex flex-wrap items-center gap-2 px-4 py-3 sm:px-5">
              <p role="alert" className="text-[12px] text-destructive">{loadError}</p>
              <Button
                type="button"
                variant="outline"
                onClick={() => void loadAccounts()}
                className="h-8 rounded-full px-3 text-[12px]"
              >
                <RotateCcw className="me-1 h-3.5 w-3.5" aria-hidden />
                {tx("settings.git.reload", "Reload")}
              </Button>
            </div>
          ) : null}
        </Group>
      </section>

      <section hidden={!machineAvailable}>
        <SectionTitle>{tx("settings.git.machine.sectionTitle", "This machine")}</SectionTitle>
        <Group>
          <div className="px-4 py-3.5 sm:px-5">
            <GitMachineStatus
              token={token}
              session={session}
              refreshKey={machineKey}
              actions={setupActions}
              hosts={accounts.map((account) => account.host)}
              onAvailability={setMachineAvailable}
            />
          </div>
        </Group>
      </section>

      <section>
        <SectionTitle>{tx("settings.git.connectTitle", "Connect")}</SectionTitle>
        <Group>
          {deviceFlowGithub ? (
            <div className="flex flex-col gap-3 px-4 py-3.5 sm:px-5">
              <div>
                <div className="text-[14px] font-medium leading-5 text-foreground">
                  {tx("settings.git.githubTitle", "GitHub")}
                </div>
                <div className="mt-0.5 max-w-[40rem] text-[12px] leading-5 text-muted-foreground">
                  {tx(
                    "settings.git.githubHelp",
                    "Sign in once in your browser. Navin can then clone your private repositories and open pull requests.",
                  )}
                </div>
              </div>
              <GitDeviceFlow
                token={token}
                onConnected={(login) => {
                  void loadAccounts();
                  void runTest("github.com");
                  onConnected?.(login);
                }}
              />
            </div>
          ) : null}
          <Row
            title={tx("settings.git.tokenTitle", "Use a token")}
            description={tx(
              "settings.git.tokenHelp",
              "One access token per host: GitHub, a company GitHub, GitLab, Forgejo/Gitea or Bitbucket. It stays on this machine and is never shown again.",
            )}
          >
            {!tokenFormOpen ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => setTokenFormOpen(true)}
                className="h-9 rounded-full text-[13px]"
              >
                <KeyRound className="me-1 h-3.5 w-3.5" aria-hidden />
                {tx("settings.git.addToken", "Add a token")}
              </Button>
            ) : null}
          </Row>
          {tokenFormOpen ? tokenForm : null}
          {error ? (
            <p role="alert" className="px-4 py-3 text-[12px] text-destructive sm:px-5">{error}</p>
          ) : null}
        </Group>
      </section>
    </div>
  );
}
