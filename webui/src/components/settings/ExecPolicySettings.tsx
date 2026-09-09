// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Plus, ShieldBan, ShieldCheck, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ToggleButton } from "@/components/settings/ToggleButton";
import { fetchExecPolicy, updateExecPolicy } from "@/lib/api";
import type { ExecApprovalMode, ExecPolicyPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

type PolicyUpdate = Parameters<typeof updateExecPolicy>[1];

export function ExecPolicySettings() {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const { token } = useClient();

  const [policy, setPolicy] = useState<ExecPolicyPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<"live" | "restart" | null>(null);
  const [denyDraft, setDenyDraft] = useState("");
  const [allowDraft, setAllowDraft] = useState("");
  const [showBuiltin, setShowBuiltin] = useState(false);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    void fetchExecPolicy(token)
      .then((payload) => {
        if (!cancelled) setPolicy(payload);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const apply = useCallback(
    async (update: PolicyUpdate) => {
      if (!token) return;
      setSaving(true);
      setError(null);
      setStatus(null);
      try {
        const payload = await updateExecPolicy(token, update);
        setPolicy(payload);
        setStatus(payload.hot_reload?.ok && !payload.hot_reload?.requires_restart ? "live" : "restart");
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSaving(false);
      }
    },
    [token],
  );

  const addRule = useCallback(
    (kind: "deny" | "allow") => {
      if (!policy) return;
      const draft = (kind === "deny" ? denyDraft : allowDraft).trim();
      if (!draft) return;
      const key = kind === "deny" ? "deny_patterns" : "allow_patterns";
      void apply({ [key]: [...policy[key], draft] });
      if (kind === "deny") setDenyDraft("");
      else setAllowDraft("");
    },
    [policy, denyDraft, allowDraft, apply],
  );

  const removeRule = useCallback(
    (kind: "deny" | "allow", index: number) => {
      if (!policy) return;
      const key = kind === "deny" ? "deny_patterns" : "allow_patterns";
      void apply({ [key]: policy[key].filter((_, i) => i !== index) });
    },
    [policy, apply],
  );

  if (!policy) {
    return (
      <div className="flex items-center gap-2 px-1 py-2 text-[13px] text-muted-foreground">
        {error ? (
          <span className="text-destructive">{error}</span>
        ) : (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {tx("settings.execPolicy.loading", "Loading agent permissions…")}
          </>
        )}
      </div>
    );
  }

  const ruleList = (kind: "deny" | "allow") => {
    const rules = kind === "deny" ? policy.deny_patterns : policy.allow_patterns;
    const draft = kind === "deny" ? denyDraft : allowDraft;
    const setDraft = kind === "deny" ? setDenyDraft : setAllowDraft;
    return (
      <div className="space-y-1.5">
        {rules.length === 0 ? (
          <p className="px-0.5 text-[12px] text-muted-foreground">
            {kind === "deny"
              ? tx("settings.execPolicy.noDenyRules", "No custom blocked commands.")
              : tx("settings.execPolicy.noAllowRules", "No exceptions defined.")}
          </p>
        ) : (
          rules.map((rule, index) => (
            <div
              key={`${rule}-${index}`}
              className="group flex items-center gap-2 rounded-lg border border-border/55 bg-background px-2.5 py-1.5"
            >
              <code className="min-w-0 flex-1 truncate font-mono text-[12px] text-foreground">
                {rule}
              </code>
              <button
                type="button"
                onClick={() => removeRule(kind, index)}
                disabled={saving}
                aria-label={tx("settings.execPolicy.removeRule", "Remove rule")}
                title={tx("settings.execPolicy.removeRule", "Remove rule")}
                className="rounded-md p-1 text-muted-foreground opacity-0 transition-all hover:bg-muted hover:text-destructive group-hover:opacity-100"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              </button>
            </div>
          ))
        )}
        <div className="flex items-center gap-1.5">
          <input
            type="text"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") addRule(kind);
            }}
            placeholder={
              kind === "deny"
                ? tx("settings.execPolicy.denyPlaceholder", "e.g. git push --force, or a regex")
                : tx("settings.execPolicy.allowPlaceholder", "e.g. rm -rf ./build, or a regex")
            }
            className="h-8 min-w-0 flex-1 rounded-lg border border-border/60 bg-background px-2.5 font-mono text-[12px] text-foreground outline-none placeholder:font-sans placeholder:text-muted-foreground/70 focus:border-foreground/50"
          />
          <button
            type="button"
            onClick={() => addRule(kind)}
            disabled={saving || !draft.trim()}
            className="flex h-8 shrink-0 items-center gap-1 rounded-lg border border-border/60 px-2.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-foreground hover:text-background disabled:opacity-40"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
            {tx("settings.execPolicy.addRule", "Add")}
          </button>
        </div>
      </div>
    );
  };

  const approvalMode = policy.approval_mode ?? "autonomous";
  const approvalHelp =
    approvalMode === "always"
      ? tx(
          "settings.execPolicy.approvalModeHelpAlways",
          "Confirmation before every shell command. Full control, slower day to day.",
        )
      : approvalMode === "risky"
        ? tx(
            "settings.execPolicy.approvalModeHelpRisky",
            "Confirmation only for irreversible actions: rm -rf, git reset --hard, deleting unsaved files, terraform destroy, posting a GitHub review. Everyday work (read, write, install, test, commit) runs without asking.",
          )
        : tx(
            "settings.execPolicy.approvalModeHelpAutonomous",
            "No confirmation. The agent runs on its own. Blocked commands below stay hard refusals.",
          );

  return (
    <div className="space-y-4">
      <div className="space-y-3 rounded-2xl border border-border/55 bg-card/40 p-4">
        <div className="min-w-0">
          <p className="text-[13.5px] font-medium text-foreground">
            {tx("settings.execPolicy.approvalMode", "Command confirmation")}
          </p>
          <p className="mt-1 text-[12px] leading-5 text-muted-foreground">{approvalHelp}</p>
        </div>
        <div className="flex flex-wrap items-center gap-0.5 rounded-full bg-muted p-0.5">
          {(
            [
              ["autonomous", "settings.execPolicy.approvalAutonomous", "Autonomous"],
              ["risky", "settings.execPolicy.approvalRisky", "Risky actions"],
              ["always", "settings.execPolicy.approvalAlways", "Every command"],
            ] as const
          ).map(([value, key, fallback]) => (
            <button
              key={value}
              type="button"
              disabled={saving}
              onClick={() => void apply({ approval_mode: value as ExecApprovalMode })}
              className={cn(
                "rounded-full px-3 py-1 text-[12px] font-medium transition-colors",
                approvalMode === value
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {tx(key, fallback)}
            </button>
          ))}
        </div>
      </div>

      {/* Master toggles */}
      <div className="space-y-3 rounded-2xl border border-border/55 bg-card/40 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[13.5px] font-medium text-foreground">
              {tx("settings.execPolicy.execEnabled", "Shell execution")}
            </p>
            <p className="text-[12px] leading-5 text-muted-foreground">
              {tx(
                "settings.execPolicy.execEnabledHelp",
                "Master switch: allow the agent to run shell commands at all.",
              )}
            </p>
          </div>
          <ToggleButton
            checked={policy.exec_enabled}
            onChange={(exec_enabled) => void apply({ exec_enabled })}
            ariaLabel={tx("settings.execPolicy.execEnabled", "Shell execution")}
            label={policy.exec_enabled ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
          />
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-border/40 pt-3">
          <div className="min-w-0">
            <p className="text-[13.5px] font-medium text-foreground">
              {tx("settings.execPolicy.restrictWorkspace", "Restrict to workspace")}
            </p>
            <p className="whitespace-pre-line text-[12px] leading-5 text-muted-foreground">
              {tx(
                "settings.execPolicy.restrictWorkspaceHelp",
                "Does not switch project. It only says whether the agent may leave the folder already open.\nOn: stays inside the chat folder. This is the safe mode.\nOff: can read, write, and run commands elsewhere on this machine if you give it a path. That does not open another workspace by itself.",
              )}
            </p>
          </div>
          <ToggleButton
            checked={policy.restrict_to_workspace}
            onChange={(restrict_to_workspace) => void apply({ restrict_to_workspace })}
            ariaLabel={tx("settings.execPolicy.restrictWorkspace", "Restrict to workspace")}
            label={
              policy.restrict_to_workspace
                ? tx("settings.values.on", "On")
                : tx("settings.values.off", "Off")
            }
          />
        </div>
      </div>

      {/* Denied commands */}
      <div className="space-y-2 rounded-2xl border border-border/55 bg-card/40 p-4">
        <div className="flex items-center gap-2">
          <ShieldBan className="h-4 w-4 text-foreground" aria-hidden />
          <p className="text-[13.5px] font-medium text-foreground">
            {tx("settings.execPolicy.denyTitle", "Blocked commands")}
          </p>
        </div>
        <p className="text-[12px] leading-5 text-muted-foreground">
          {tx(
            "settings.execPolicy.denyHelp",
            "The agent can never run a command matching one of these rules. Plain text matches as a prefix; regex is supported.",
          )}
        </p>
        {ruleList("deny")}
      </div>

      {/* Allowed exceptions */}
      <div className="space-y-2 rounded-2xl border border-border/55 bg-card/40 p-4">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-foreground" aria-hidden />
          <p className="text-[13.5px] font-medium text-foreground">
            {tx("settings.execPolicy.allowTitle", "Allowed exceptions")}
          </p>
        </div>
        <p className="text-[12px] leading-5 text-muted-foreground">
          {tx(
            "settings.execPolicy.allowHelp",
            "Commands matching these rules bypass the blocked list (including built-in protections). If at least one rule exists, only matching commands are allowed.",
          )}
        </p>
        {ruleList("allow")}
      </div>

      {/* Built-in protections */}
      <div className="rounded-2xl border border-border/55 bg-card/40 p-4">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowBuiltin((prev) => !prev)}
            className="flex min-w-0 flex-1 items-center gap-2 text-left"
          >
            {showBuiltin ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden />
            )}
            <p className="text-[13.5px] font-medium text-foreground">
              {tx("settings.execPolicy.builtinTitle", "Built-in protections")}
            </p>
            <span className="text-[12px] text-muted-foreground">
              {policy.builtin_deny.length}
            </span>
          </button>
          <ToggleButton
            checked={policy.builtin_deny_enabled}
            onChange={(builtin_deny_enabled) => void apply({ builtin_deny_enabled })}
            ariaLabel={tx("settings.execPolicy.builtinTitle", "Built-in protections")}
            label={
              policy.builtin_deny_enabled
                ? tx("settings.values.on", "On")
                : tx("settings.values.off", "Off")
            }
          />
        </div>
        {showBuiltin ? (
          <div className="mt-2.5 space-y-1">
            {policy.builtin_deny.map((rule, index) => (
              <div
                key={index}
                className="flex items-center gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5"
              >
                <span className="shrink-0 text-[11.5px] font-medium text-muted-foreground">
                  {tx(`settings.execPolicy.builtin.${rule.label}`, rule.label)}
                </span>
                <code className="min-w-0 flex-1 truncate text-right font-mono text-[11px] text-muted-foreground/80">
                  {rule.pattern}
                </code>
              </div>
            ))}
            <p className="px-0.5 pt-1 text-[11.5px] text-muted-foreground">
              {tx(
                "settings.execPolicy.builtinHelp",
                "Off by default, so the agent can run these commands. Turn it on to block them all at once; an allowed exception is then the only way past one.",
              )}
            </p>
          </div>
        ) : null}
      </div>

      {/* Status line */}
      <div className="flex items-center gap-2 px-1 text-[12px]">
        {saving ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
            <span className="text-muted-foreground">
              {tx("settings.execPolicy.saving", "Saving…")}
            </span>
          </>
        ) : status === "live" ? (
          <span className="text-muted-foreground">
            {tx("settings.execPolicy.appliedLive", "Saved - applied to the running agent.")}
          </span>
        ) : status === "restart" ? (
          <span className={cn("text-foreground")}>
            {tx(
              "settings.execPolicy.appliedRestart",
              "Saved - restart navin to apply to the running agent.",
            )}
          </span>
        ) : null}
        {error ? <span className="text-destructive">{error}</span> : null}
      </div>
    </div>
  );
}
