// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, type Dispatch, type SetStateAction } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Check, Cloud, FileUp, Globe2, Laptop, Loader2, RotateCw, Terminal, Wallet } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { updateSettings } from "@/lib/api";
import type { SettingsPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

type Target = "local" | "aws";

/**
 * Settings > Execution: where the agent runs code, shell commands and its
 * browser. This computer by default; the AWS cloud sandbox (Bedrock
 * AgentCore) only after the user accepts what it implies.
 */
export function ExecutionSettings({ settings, onUpdated, onRestart, isRestarting }: {
  settings: SettingsPayload;
  onUpdated: Dispatch<SetStateAction<SettingsPayload | null>>;
  onRestart: () => Promise<void>;
  isRestarting: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, options?: Record<string, unknown>) =>
    t(`settings.execution.${key}`, { defaultValue: fallback, ...options });
  const { token } = useClient();
  const reduced = useReducedMotion();
  const execution = settings.execution ?? { target: "local", aws_accepted: false, licensed: false };
  const target: Target = execution.target === "aws" ? "aws" : "local";
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [restart, setRestart] = useState(false);

  async function persist(update: Parameters<typeof updateSettings>[1]) {
    if (!token) return;
    setSaving(true);
    setError("");
    try {
      const next = await updateSettings(token, update);
      onUpdated(next);
      if (next.requires_restart) setRestart(true);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  const choices: { id: Target; icon: typeof Laptop; title: string; body: string }[] = [
    {
      id: "local",
      icon: Laptop,
      title: tx("local.title", "This computer (default)"),
      body: tx(
        "local.body",
        "Commands, code and the browser run here, on your files, with your tools. Nothing leaves the computer.",
      ),
    },
    {
      id: "aws",
      icon: Cloud,
      title: tx("aws.title", "AWS cloud sandbox"),
      body: tx(
        "aws.body",
        "An isolated microVM per session for code and shell, and an isolated browser with live view. Your computer stays untouched.",
      ),
    },
  ];

  const terms = [
    { icon: Terminal, text: tx("terms.what", "Commands, Python/JavaScript/TypeScript and the browser run on AWS (Bedrock AgentCore), isolated per session.") },
    { icon: FileUp, text: tx("terms.files", "The sandbox does not see this computer: the agent sends the files it needs and brings results back explicitly. Sandbox files last for the session (up to 1 h).") },
    { icon: Wallet, text: tx("terms.cost", "Time in the sandbox comes off your plan's AI budget, like the models: one counter, never above your plan.") },
    { icon: Globe2, text: tx("terms.local", "You can switch back to this computer at any time.") },
  ];

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">{tx("title", "Execution")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {tx("subtitle", "Where the agent runs commands, code and its browser.")}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label={tx("title", "Execution")}>
        {choices.map((choice) => {
          const selected = target === choice.id;
          const locked = choice.id === "aws" && (!execution.aws_accepted || !execution.licensed);
          return (
            <motion.button
              key={choice.id}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={saving || (locked && !selected)}
              whileTap={reduced ? undefined : { scale: 0.98 }}
              onClick={() => !selected && void persist({ executionTarget: choice.id })}
              className={cn(
                "relative rounded-xl border p-4 text-start transition-colors disabled:cursor-not-allowed",
                selected ? "border-foreground/70 bg-muted/60" : "border-border/70 hover:bg-muted/40",
                locked && !selected && "opacity-60",
              )}
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                <choice.icon className="h-4 w-4 text-muted-foreground" aria-hidden />
                {choice.title}
                {selected ? <Check className="ms-auto h-4 w-4" aria-hidden /> : null}
              </span>
              <span className="mt-1.5 block text-[13px] leading-relaxed text-muted-foreground">{choice.body}</span>
            </motion.button>
          );
        })}
      </div>

      <section className="rounded-xl border border-border/70 p-4">
        <h3 className="text-sm font-medium">{tx("terms.title", "Before using the AWS sandbox")}</h3>
        <ul className="mt-3 space-y-2.5">
          {terms.map((item) => (
            <li key={item.text} className="flex gap-2.5 text-[13px] leading-relaxed text-muted-foreground">
              <item.icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <span>{item.text}</span>
            </li>
          ))}
        </ul>
        {!execution.licensed ? (
          <p className="mt-3 text-[13px] text-amber-700 dark:text-amber-300">
            {tx("needsPlan", "Cloud execution needs this device signed in to a paid Navin plan.")}
          </p>
        ) : null}
        <label className="mt-4 flex items-start gap-2.5 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4"
            checked={execution.aws_accepted}
            disabled={saving || !execution.licensed}
            onChange={(e) => void persist({ executionAwsAccepted: e.target.checked })}
          />
          <span>{tx("accept", "I accept: code and the browser may run in the AWS sandbox, files are sent explicitly, and its time is billed on my AI budget.")}</span>
        </label>
      </section>

      {saving ? (
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {tx("saving", "Saving...")}
        </p>
      ) : null}
      {restart ? (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border/70 bg-muted/40 px-4 py-3 text-[13px]">
          <span className="flex-1">
            {tx("restart", "Commands and the browser switch right away; restart to update the agent's file tools.")}
          </span>
          <Button size="sm" variant="outline" disabled={isRestarting} onClick={() => void onRestart()}>
            {isRestarting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RotateCw className="h-4 w-4" aria-hidden />}
            {tx("restartNow", "Restart now")}
          </Button>
        </div>
      ) : null}
      {error ? (
        <p className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">{error}</p>
      ) : null}
    </div>
  );
}
