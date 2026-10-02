// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, ShieldAlert, ShieldCheck, ShieldOff, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { commitOnPointerDown, commitOnSelect } from "@/components/thread/menuChoice";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { fetchExecPolicy, updateExecPolicy } from "@/lib/api";
import type { ExecApprovalMode } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const MODES: Array<{
  mode: ExecApprovalMode;
  icon: LucideIcon;
  labelKey: string;
  labelDefault: string;
  hintKey: string;
  hintDefault: string;
}> = [
  {
    mode: "autonomous",
    icon: ShieldOff,
    labelKey: "thread.composer.permission.autonomous",
    labelDefault: "Full access",
    hintKey: "thread.composer.permission.autonomousHint",
    hintDefault: "Never asks",
  },
  {
    mode: "risky",
    icon: ShieldCheck,
    labelKey: "thread.composer.permission.risky",
    labelDefault: "Ask before deleting",
    hintKey: "thread.composer.permission.riskyHint",
    hintDefault: "Only irreversible actions",
  },
  {
    mode: "always",
    icon: ShieldAlert,
    labelKey: "thread.composer.permission.always",
    labelDefault: "Ask every command",
    hintKey: "thread.composer.permission.alwaysHint",
    hintDefault: "Every shell command",
  },
];

/**
 * The confirmation posture, visible where the work starts instead of three
 * levels deep in Settings, so nobody grants the same right turn after turn
 * without knowing a mode exists.
 */
export function ComposerPermissionMenu({
  disabled,
  compact = false,
}: {
  disabled?: boolean;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [mode, setMode] = useState<ExecApprovalMode | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    void fetchExecPolicy(token)
      .then((payload) => {
        if (!cancelled) setMode(payload.approval_mode ?? "autonomous");
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token]);

  const choose = useCallback(
    (next: ExecApprovalMode) => {
      if (!token || next === mode) return;
      const previous = mode;
      setMode(next);
      setSaving(true);
      void updateExecPolicy(token, { approval_mode: next })
        .then((payload) => setMode(payload.approval_mode ?? next))
        .catch(() => setMode(previous))
        .finally(() => setSaving(false));
    },
    [mode, token],
  );

  if (!mode) return null;
  const current = MODES.find((item) => item.mode === mode) ?? MODES[0];
  const Icon = current.icon;
  const label = t(current.labelKey, { defaultValue: current.labelDefault });

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild disabled={disabled || saving}>
        <Button
          type="button"
          variant="ghost"
          data-testid="composer-permission-mode"
          title={label}
          aria-label={t("thread.composer.permission.aria", { defaultValue: "Permissions" })}
          className={cn(
            "max-w-[11rem] rounded-lg border border-transparent bg-transparent font-medium text-muted-foreground shadow-none transition-colors hover:bg-foreground/[0.05] hover:text-foreground active:scale-[0.96] dark:hover:bg-white/[0.06]",
            compact ? "h-7 gap-1 px-2 text-[12px]" : "h-8 gap-1 px-2.5 text-[12.5px]",
          )}
        >
          <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {compact ? null : <span className="truncate">{label}</span>}
          <ChevronDown className="h-3 w-3 shrink-0 opacity-70" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" sideOffset={6} className="min-w-[13rem] p-1">
        {MODES.map((item) => {
          const ItemIcon = item.icon;
          const selected = item.mode === mode;
          return (
            <DropdownMenuItem
              key={item.mode}
              data-testid={`composer-permission-${item.mode}`}
              onPointerDown={(event) => commitOnPointerDown(event, () => choose(item.mode))}
              onSelect={() => commitOnSelect(() => choose(item.mode))}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[13px]",
                selected && "bg-foreground/[0.06]",
              )}
            >
              <ItemIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block font-medium">
                  {t(item.labelKey, { defaultValue: item.labelDefault })}
                </span>
                <span className="block text-[11.5px] text-muted-foreground">
                  {t(item.hintKey, { defaultValue: item.hintDefault })}
                </span>
              </span>
              {selected ? (
                <Check className="h-3.5 w-3.5 shrink-0 opacity-80" aria-hidden />
              ) : (
                <span className="h-3.5 w-3.5 shrink-0" aria-hidden />
              )}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
