// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * What git needs on THIS machine, detected by the engine: OS, git version,
 * commit identity, credential helper, SSH keys, remotes. Each line says what
 * is fine, what was set up for the user, and gives the exact command for the
 * current OS (Windows, macOS, Linux, or the WSL distribution of the project)
 * when something is left to do.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Copy, Info, Loader2, RotateCw, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  fetchGitMachine,
  setGitIdentity,
  storeGitTokenInHelper,
  switchGitRemoteToHttps,
} from "@/lib/api";
import { copyTextToClipboard } from "@/lib/clipboard";
import type { GitMachineCheck, GitMachinePayload, GitSetupAction } from "@/lib/types";
import { cn } from "@/lib/utils";

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function str(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

function LevelIcon({ level }: { level: GitMachineCheck["level"] }) {
  const common = "mt-0.5 h-3.5 w-3.5 shrink-0";
  if (level === "ok") return <Check className={cn(common, "text-emerald-600 dark:text-emerald-400")} aria-hidden />;
  if (level === "warn") return <AlertTriangle className={cn(common, "text-amber-600 dark:text-amber-400")} aria-hidden />;
  if (level === "error") return <XCircle className={cn(common, "text-destructive")} aria-hidden />;
  return <Info className={cn(common, "text-muted-foreground")} aria-hidden />;
}

export interface GitMachineStatusProps {
  token: string | null | undefined;
  /** Session whose project git to inspect (WSL projects included). */
  session?: string | null;
  /** Bump to re-run the detection (after a connect). */
  refreshKey?: number;
  /** What `/git/setup` just did, shown above the checks. */
  actions?: GitSetupAction[];
  /** Hosts with a stored token, for "save in the system credential helper". */
  hosts?: string[];
  /** The user has to connect `host` (a remote has no token). */
  onConnect?: (host: string) => void;
  /** Called after a fix changed git, so the caller can reload its own state. */
  onChanged?: () => void;
  /** False when the engine has no machine route (older engine): hide the frame. */
  onAvailability?: (available: boolean) => void;
  compact?: boolean;
  className?: string;
}

export function GitMachineStatus({
  token,
  session,
  refreshKey = 0,
  actions,
  hosts,
  onConnect,
  onChanged,
  onAvailability,
  compact = false,
  className,
}: GitMachineStatusProps) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    String(t(key, { defaultValue: fallback, ...(values ?? {}) }));
  const [machine, setMachine] = useState<GitMachinePayload | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(!compact);
  const [editIdentity, setEditIdentity] = useState(false);
  const onAvailabilityRef = useRef(onAvailability);
  onAvailabilityRef.current = onAvailability;

  useEffect(() => {
    onAvailabilityRef.current?.(!unavailable);
  }, [unavailable]);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const next = await fetchGitMachine(token, { session });
      setMachine(next);
      setUnavailable(false);
      setName((prev) => prev || next.identity.name || "");
      setEmail((prev) => prev || next.identity.email || "");
    } catch (err) {
      // An engine without the route simply has no machine section.
      if (err instanceof ApiError && err.status === 404) setUnavailable(true);
      else setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [token, session]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const run = async (id: string, work: () => Promise<unknown>, done?: string) => {
    setBusy(id);
    setError(null);
    setNotice(null);
    try {
      await work();
      if (done) setNotice(done);
      await load();
      onChanged?.();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const copy = async (command: string) => {
    if (await copyTextToClipboard(command)) {
      setCopied(command);
      window.setTimeout(() => setCopied((prev) => (prev === command ? null : prev)), 1500);
    }
  };

  if (unavailable || !token) return null;
  const authToken: string = token;

  const osLabel = (id: string) =>
    id === "windows"
      ? "Windows"
      : id === "macos"
        ? "macOS"
        : tx("settings.git.machine.linux", "Linux");

  const describe = (check: GitMachineCheck): string => {
    const p = check.params;
    switch (check.id) {
      case "git_missing":
        return p.distro
          ? tx("settings.git.machine.gitMissingWsl", "git is not installed in {{distro}}.", { distro: str(p.distro) })
          : tx("settings.git.machine.gitMissing", "git is not installed on this machine.");
      case "git_old":
        return tx(
          "settings.git.machine.gitOld",
          "git {{version}} is too old to use your token (needs {{min}} or newer).",
          { version: str(p.version), min: str(p.min) },
        );
      case "git_ok":
        return p.distro
          ? tx("settings.git.machine.gitOkWsl", "git {{version}} in {{distro}}", { version: str(p.version), distro: str(p.distro) })
          : tx("settings.git.machine.gitOk", "git {{version}}", { version: str(p.version) });
      case "identity_missing":
        return tx(
          "settings.git.machine.identityMissing",
          "No commit identity (user.name / user.email). Commits would fail.",
        );
      case "identity_ok":
        return tx("settings.git.machine.identityOk", "Commits as {{name}} <{{email}}>", {
          name: str(p.name),
          email: str(p.email),
        });
      case "helper_plaintext":
        return tx(
          "settings.git.machine.helperPlaintext",
          "The credential helper \"store\" keeps passwords in clear text in your home folder.",
        );
      case "helper_missing":
        return tx(
          "settings.git.machine.helperMissing",
          "No credential helper for your own terminal. Navin does not need one; git in a terminal does.",
        );
      case "helper_ok":
        return tx("settings.git.machine.helperOk", "Credential helper: {{helper}}", { helper: str(p.helper) });
      case "remote_no_token":
        return tx("settings.git.machine.remoteNoToken", "Remote {{remote}} ({{host}}) has no token yet.", {
          remote: str(p.remote),
          host: str(p.host),
        });
      case "remote_ssh_no_key":
        return tx(
          "settings.git.machine.remoteSshNoKey",
          "Remote {{remote}} uses SSH but no SSH key was found on this machine.",
          { remote: str(p.remote) },
        );
      case "remote_ok":
        return p.auth === "ssh_key"
          ? tx("settings.git.machine.remoteOkSsh", "Remote {{remote}} ({{host}}) uses your SSH key", {
              remote: str(p.remote),
              host: str(p.host),
            })
          : tx("settings.git.machine.remoteOk", "Remote {{remote}} ({{host}}) is covered by your token", {
              remote: str(p.remote),
              host: str(p.host),
            });
      default:
        return check.id;
    }
  };

  const actionLine = (action: GitSetupAction): string | null => {
    if (action.id === "identity") {
      if (action.status === "configured")
        return tx("settings.git.machine.didIdentity", "Set the commit identity to {{name}} <{{email}}>.", {
          name: str(action.name),
          email: str(action.email),
        });
      if (action.status === "needs_input")
        return tx("settings.git.machine.needIdentity", "Navin could not guess your commit identity. Enter it below.");
      if (action.status === "failed") return str(action.error) || null;
      return null;
    }
    if (action.id === "scopes") {
      if (action.status === "missing")
        return tx("settings.git.machine.scopesMissing", "This token lacks the scopes: {{scopes}}.", {
          scopes: (action.missing ?? []).join(", "),
        });
      return null;
    }
    return null;
  };

  const fixButton = (check: GitMachineCheck) => {
    const fix = check.fix;
    if (check.id === "identity_ok" && !editIdentity) {
      return (
        <button
          type="button"
          onClick={() => {
            setName(str(check.params.name));
            setEmail(str(check.params.email));
            setEditIdentity(true);
          }}
          className="text-[11.5px] font-medium text-foreground/70 underline-offset-2 hover:text-foreground hover:underline"
        >
          {tx("settings.git.machine.changeIdentity", "Change")}
        </button>
      );
    }
    if (!fix) return null;
    if (fix.action === "connect" && fix.host && onConnect) {
      return (
        <Button type="button" variant="outline" className="h-7 rounded-full px-3 text-[12px]" onClick={() => onConnect(fix.host!)}>
          {tx("settings.git.machine.connectHost", "Connect {{host}}", { host: fix.host })}
        </Button>
      );
    }
    if (fix.action === "use_https" && fix.remote && session) {
      return (
        <Button
          type="button"
          variant="outline"
          disabled={busy !== null}
          className="h-7 rounded-full px-3 text-[12px]"
          onClick={() =>
            void run(
              `https:${fix.remote}`,
              () => switchGitRemoteToHttps(authToken, fix.remote!, session),
              tx("settings.git.machine.httpsDone", "{{remote}} now uses HTTPS.", { remote: fix.remote }),
            )
          }
        >
          {busy === `https:${fix.remote}` ? <Loader2 className="me-1 h-3 w-3 animate-spin" aria-hidden /> : null}
          {tx("settings.git.machine.useHttps", "Switch to HTTPS")}
        </Button>
      );
    }
    if (fix.action === "identity") {
      return null; // the identity form below is the fix
    }
    if (fix.command) {
      return (
        <div className="flex max-w-full items-center gap-1.5">
          <code className="min-w-0 truncate rounded-md bg-muted px-2 py-1 font-mono text-[11px]" dir="ltr" title={fix.command}>
            {fix.command}
          </code>
          <button
            type="button"
            onClick={() => void copy(fix.command!)}
            className="inline-flex shrink-0 items-center gap-1 text-[11.5px] font-medium text-foreground/80 hover:text-foreground"
          >
            {copied === fix.command ? <Check className="h-3 w-3" aria-hidden /> : <Copy className="h-3 w-3" aria-hidden />}
            {copied === fix.command ? tx("settings.git.copied", "Copied") : tx("settings.git.machine.copyCommand", "Copy")}
          </button>
        </div>
      );
    }
    return null;
  };

  const checks = machine?.checks ?? [];
  const systemLabel = (report: GitMachinePayload) => {
    const label = report.platform.label || osLabel(report.platform.os);
    return report.platform.wsl ? `${label} (WSL)` : label;
  };
  const needsIdentity = checks.some((check) => check.id === "identity_missing");
  const identityFormOpen = needsIdentity || editIdentity;
  const hasActions = (actions ?? []).some((action) => actionLine(action));
  const helperHosts = [...new Set((hosts ?? []).map((value) => value.trim()).filter(Boolean))];
  const helperList = machine?.credential_helper.helpers ?? [];
  const activeHelper = machine?.credential_helper.active || helperList[helperList.length - 1] || "";
  const canStoreInHelper =
    helperHosts.length > 0 &&
    Boolean(machine?.credential_helper.secure) &&
    helperList.length > 0 &&
    !helperList.includes("store");

  return (
    <div
      className={cn("flex flex-col gap-2.5", className)}
      data-testid="git-machine-status"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[12.5px] font-medium text-foreground/90">
          {machine
            ? tx("settings.git.machine.title", "Git on this machine: {{os}}", { os: systemLabel(machine) })
            : tx("settings.git.machine.titlePlain", "Git on this machine")}
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex items-center gap-1 text-[11.5px] text-muted-foreground hover:text-foreground"
          aria-label={tx("settings.git.machine.recheck", "Check again")}
        >
          {loading ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : <RotateCw className="h-3 w-3" aria-hidden />}
          {tx("settings.git.machine.recheck", "Check again")}
        </button>
      </div>

      {hasActions ? (
        <ul className="flex flex-col gap-1" data-testid="git-setup-actions">
          {(actions ?? []).map((action) => {
            const line = actionLine(action);
            if (!line) return null;
            const done = action.status === "configured";
            return (
              <li key={action.id} className="flex items-start gap-2 text-[12px] leading-5 text-foreground/90">
                {done ? (
                  <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
                ) : (
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
                )}
                <span>{line}</span>
              </li>
            );
          })}
        </ul>
      ) : null}

      {checks.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {checks
            .filter((check) => !compact || check.level !== "ok")
            .map((check, index) => {
              const fix = fixButton(check);
              return (
                <li key={`${check.id}:${index}`} className="flex flex-col gap-1.5" data-testid={`git-check-${check.id}`}>
                  <div className="flex items-start gap-2 text-[12px] leading-5 text-foreground/90">
                    <LevelIcon level={check.level} />
                    <span>{describe(check)}</span>
                  </div>
                  {fix ? <div className="ps-[22px]">{fix}</div> : null}
                </li>
              );
            })}
        </ul>
      ) : loading ? (
        <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          {t("settings.status.loading")}
        </div>
      ) : null}

      {identityFormOpen ? (
        <div className="flex flex-wrap items-center gap-2 ps-[22px]" data-testid="git-identity-form">
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={tx("settings.git.machine.namePlaceholder", "Your name")}
            aria-label={tx("settings.git.machine.nameLabel", "Commit name")}
            className="h-8 w-[160px] rounded-full text-[12px]"
          />
          <Input
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder={tx("settings.git.machine.emailPlaceholder", "you@example.com")}
            aria-label={tx("settings.git.machine.emailLabel", "Commit email")}
            dir="ltr"
            className="h-8 w-[200px] rounded-full text-[12px]"
          />
          <Button
            type="button"
            disabled={busy !== null || !name.trim() || !email.includes("@")}
            className="h-8 rounded-full px-3 text-[12px]"
            onClick={() =>
              void run(
                "identity",
                async () => {
                  const result = await setGitIdentity(authToken, { name, email, session });
                  if (!result.ok) throw new Error(result.error || "git config failed");
                  setEditIdentity(false);
                },
                tx("settings.git.machine.identitySaved", "Commit identity saved."),
              )
            }
          >
            {busy === "identity" ? <Loader2 className="me-1 h-3 w-3 animate-spin" aria-hidden /> : null}
            {tx("settings.git.machine.saveIdentity", "Save identity")}
          </Button>
          {editIdentity && !needsIdentity ? (
            <Button
              type="button"
              variant="ghost"
              className="h-8 rounded-full px-3 text-[12px] text-muted-foreground"
              onClick={() => setEditIdentity(false)}
            >
              {tx("settings.git.cancel", "Cancel")}
            </Button>
          ) : null}
        </div>
      ) : null}

      {canStoreInHelper && !compact ? (
        <div className="flex flex-wrap items-center gap-2 ps-[22px]" data-testid="git-helper-actions">
          {helperHosts.map((target) => (
            <Button
              key={target}
              type="button"
              variant="outline"
              disabled={busy !== null}
              className="h-7 rounded-full px-3 text-[12px]"
              onClick={() =>
                void run(
                  `helper:${target}`,
                  () => storeGitTokenInHelper(authToken, target, { session }),
                  tx(
                    "settings.git.machine.savedToHelper",
                    "Saved for {{host}} in {{helper}}. git in a terminal now uses it too.",
                    { host: target, helper: activeHelper },
                  ),
                )
              }
            >
              {busy === `helper:${target}` ? <Loader2 className="me-1 h-3 w-3 animate-spin" aria-hidden /> : null}
              {tx("settings.git.machine.saveToHelper", "Also use the {{host}} token in my terminal", { host: target })}
            </Button>
          ))}
        </div>
      ) : null}

      {machine ? (
        <div className="flex flex-col gap-1.5">
          <button
            type="button"
            onClick={() => setShowDetails((value) => !value)}
            className="self-start text-[11.5px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            aria-expanded={showDetails}
          >
            {showDetails
              ? tx("settings.git.machine.hideDetails", "Hide details")
              : tx("settings.git.machine.showDetails", "Show details")}
          </button>
          {showDetails ? (
            <dl
              className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-[12px] leading-5"
              data-testid="git-machine-details"
            >
              <dt className="text-muted-foreground">{tx("settings.git.machine.system", "System")}</dt>
              <dd className="min-w-0 break-words text-foreground/90">
                {[
                  machine.platform.label || osLabel(machine.platform.os),
                  machine.platform.arch,
                  machine.platform.wsl ? "WSL" : "",
                ].filter(Boolean).join(" · ")}
              </dd>
              <dt className="text-muted-foreground">git</dt>
              <dd className="min-w-0 break-words font-mono text-[11.5px] text-foreground/90" dir="ltr">
                {machine.git.available
                  ? [
                      machine.git.version,
                      machine.git.distro ? `WSL ${machine.git.distro}` : machine.git.path,
                    ].filter(Boolean).join(" · ")
                  : tx("settings.git.machine.notInstalled", "Not installed")}
              </dd>
              <dt className="text-muted-foreground">{tx("settings.git.machine.identity", "Commit identity")}</dt>
              <dd className="min-w-0 break-words text-foreground/90">
                {machine.identity.name || machine.identity.email
                  ? `${machine.identity.name ?? "?"} <${machine.identity.email ?? "?"}>`
                  : tx("settings.git.machine.none", "None")}
              </dd>
              <dt className="text-muted-foreground">{tx("settings.git.machine.helper", "Credential helper")}</dt>
              <dd className="min-w-0 break-words text-foreground/90">
                {machine.credential_helper.helpers.length
                  ? machine.credential_helper.helpers.join(", ")
                  : tx("settings.git.machine.none", "None")}
                <span className="text-muted-foreground">
                  {" "}
                  {tx("settings.git.machine.recommended", "(recommended here: {{helper}})", {
                    helper: machine.credential_helper.recommended.helper,
                  })}
                </span>
              </dd>
              <dt className="text-muted-foreground">{tx("settings.git.machine.sshKeys", "SSH keys")}</dt>
              <dd className="min-w-0 break-words font-mono text-[11.5px] text-foreground/90" dir="ltr">
                {machine.ssh.keys.length ? machine.ssh.keys.join(", ") : tx("settings.git.machine.none", "None")}
              </dd>
              <dt className="text-muted-foreground">{tx("settings.git.machine.ghCli", "GitHub CLI")}</dt>
              <dd className="text-foreground/90">
                {machine.gh.available
                  ? tx("settings.git.machine.installed", "Installed")
                  : tx("settings.git.machine.notInstalled", "Not installed")}
              </dd>
              {machine.remotes.map((remote) => (
                <div key={remote.name} className="contents">
                  <dt className="text-muted-foreground">{remote.name}</dt>
                  <dd className="min-w-0 break-all font-mono text-[11.5px] text-foreground/90" dir="ltr">
                    {remote.url}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
        </div>
      ) : null}

      {notice ? (
        <p className="inline-flex items-center gap-1.5 text-[12px] font-medium text-emerald-700 dark:text-emerald-300">
          <Check className="h-3.5 w-3.5" aria-hidden />
          {notice}
        </p>
      ) : null}

      {error ? (
        <p role="alert" className="text-[12px] font-medium text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
