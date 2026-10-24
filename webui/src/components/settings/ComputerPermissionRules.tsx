// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import {
  DefaultButton, Dropdown, IconButton, MessageBar, MessageBarType, PrimaryButton, Spinner, Text, TextField,
} from "@fluentui/react";
import { useTranslation } from "react-i18next";

import {
  deleteComputerPermissionRule, fetchComputerPermissionRules, saveComputerPermissionRule,
  type ComputerPermissionLevel, type ComputerPermissionRules as Payload,
} from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import { COMPUTER_MODEL_CALLOUT } from "./computer-callout";

const LEVELS: ComputerPermissionLevel[] = ["see", "click", "type", "launch"];

export function ComputerPermissionRules({ disabled = false }: { disabled?: boolean }) {
  const { t } = useTranslation();
  const tx = (key: string) => t(`settings.computer.permRules.${key}`);
  const { token } = useClient();
  const [data, setData] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [app, setApp] = useState("");
  const [level, setLevel] = useState<ComputerPermissionLevel>("click");

  const run = useCallback(async (job: () => Promise<Payload>) => {
    setBusy(true);
    setError(null);
    try {
      setData(await job());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!token) { setLoading(false); return; }
    void run(() => fetchComputerPermissionRules(token));
  }, [token, run]);

  const options = LEVELS.map((key) => ({ key, text: tx(`level.${key}`) }));
  const rules = data?.rules ?? [];
  const locked = disabled || busy || !token;

  return (
    <div className="computer-settings-permission-rules">
      <Text variant="large" styles={{ root: { fontWeight: 600 } }}>{tx("title")}</Text>
      <Text block className="computer-settings-description">{tx("help")}</Text>
      {error ? <MessageBar messageBarType={MessageBarType.error} isMultiline={false}
        onDismiss={() => setError(null)} dismissButtonAriaLabel={tx("dismiss")}>{error}</MessageBar> : null}
      {loading ? <Spinner label={tx("loading")} /> : rules.length === 0 && !error ? (
        <Text block className="computer-settings-description">{tx("empty")}</Text>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
          {rules.map((rule) => (
            <li key={rule.app} style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
              <Text styles={{ root: { flex: 1, wordBreak: "break-all" } }}>{rule.app}</Text>
              <Dropdown ariaLabel={`${tx("levelFor")} ${rule.app}`} selectedKey={rule.level} options={options}
                calloutProps={COMPUTER_MODEL_CALLOUT} disabled={locked} styles={{ root: { width: 160 } }}
                onChange={(_, option) => {
                  if (option && token) void run(() => saveComputerPermissionRule(token, rule.app, option.key as ComputerPermissionLevel));
                }} />
              <IconButton iconProps={{ iconName: "Delete" }} title={tx("remove")} ariaLabel={`${tx("remove")} ${rule.app}`}
                disabled={locked} onClick={() => { if (token) void run(() => deleteComputerPermissionRule(token, rule.app)); }} />
            </li>
          ))}
        </ul>
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
        <TextField label={tx("appLabel")} placeholder={tx("appPlaceholder")} value={app} disabled={locked}
          onChange={(_, next) => setApp(next ?? "")} styles={{ root: { flex: 1, minWidth: 180 } }} />
        <Dropdown label={tx("levelLabel")} selectedKey={level} options={options}
          calloutProps={COMPUTER_MODEL_CALLOUT} disabled={locked} styles={{ root: { width: 160 } }}
          onChange={(_, option) => { if (option) setLevel(option.key as ComputerPermissionLevel); }} />
        <PrimaryButton text={tx("add")} disabled={locked || !app.trim()}
          onClick={() => {
            if (!token) return;
            const name = app.trim();
            void run(async () => { const next = await saveComputerPermissionRule(token, name, level); setApp(""); return next; });
          }} />
        <DefaultButton text={tx("reload")} disabled={locked}
          onClick={() => { if (token) void run(() => fetchComputerPermissionRules(token)); }} />
      </div>
    </div>
  );
}
