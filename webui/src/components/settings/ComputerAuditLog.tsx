// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import { DefaultButton, MessageBar, MessageBarType, Spinner, Text } from "@fluentui/react";
import { useTranslation } from "react-i18next";

import {
  fetchComputerAuditSession, fetchComputerAuditSessions,
  type ComputerAuditSession, type ComputerAuditSessionSummary,
} from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

function stepDetail(args: Record<string, unknown>, result: string, denied: string, rule: string): string {
  const parts: string[] = [];
  if (Object.keys(args).length) parts.push(JSON.stringify(args));
  if (rule) parts.push(`[${rule}]`);
  if (denied || result) parts.push(denied || result);
  return parts.join(" ");
}

/** Read-only view of the desktop audit trail: sessions, then the steps of one. */
export function ComputerAuditLog() {
  const { t } = useTranslation();
  const tx = (key: string, options?: Record<string, unknown>) => t(`settings.computer.audit.${key}`, options);
  const { token } = useClient();
  const [sessions, setSessions] = useState<ComputerAuditSessionSummary[] | null>(null);
  const [selected, setSelected] = useState<ComputerAuditSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) { setLoading(false); return; }
    setLoading(true);
    setError(null);
    try {
      setSessions((await fetchComputerAuditSessions(token)).sessions);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { void reload(); }, [reload]);

  const open = async (id: string) => {
    if (!token) return;
    setOpening(id);
    setError(null);
    try {
      setSelected((await fetchComputerAuditSession(token, id)).session);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setOpening(null);
    }
  };

  const list = sessions ?? [];
  return (
    <div className="computer-settings-audit">
      <Text variant="large" styles={{ root: { fontWeight: 600 } }}>{tx("title")}</Text>
      <Text block className="computer-settings-description">{tx("help")}</Text>
      {error ? <MessageBar messageBarType={MessageBarType.error} isMultiline={false}
        onDismiss={() => setError(null)} dismissButtonAriaLabel={tx("dismiss")}>{error}</MessageBar> : null}
      {loading ? <Spinner label={tx("loading")} /> : list.length === 0 && !error ? (
        <Text block className="computer-settings-description">{tx("empty")}</Text>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
          {list.map((s) => (
            <li key={s.id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <Text styles={{ root: { flex: 1, minWidth: 180, wordBreak: "break-all" } }}>
                {s.started || s.id}{s.chat ? ` - ${s.chat}` : ""}
              </Text>
              <Text variant="small">{tx("stepCount", { count: s.steps })}</Text>
              {s.denied ? <Text variant="small" styles={{ root: { color: "var(--color-danger, #a4262c)" } }}>
                {tx("deniedCount", { count: s.denied })}</Text> : null}
              <DefaultButton text={selected?.id === s.id ? tx("opened") : tx("open")}
                ariaLabel={`${tx("open")} ${s.id}`} disabled={opening !== null}
                onClick={() => { void open(s.id); }} />
            </li>
          ))}
        </ul>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <DefaultButton text={tx("reload")} disabled={loading || !token} onClick={() => { void reload(); }} />
        {selected ? <DefaultButton text={tx("close")} onClick={() => setSelected(null)} /> : null}
      </div>
      {opening ? <Spinner label={tx("loadingSteps")} /> : null}
      {selected ? (
        <div role="region" aria-label={`${tx("stepsOf")} ${selected.id}`}>
          <Text block styles={{ root: { fontWeight: 600 } }}>
            {tx("stepsOf")} {selected.id} ({tx("stepCount", { count: selected.total })})
          </Text>
          {selected.broken ? <Text block variant="small">{tx("broken", { count: selected.broken })}</Text> : null}
          {selected.steps.length === 0 ? <Text block className="computer-settings-description">{tx("noSteps")}</Text> : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "right", padding: 4 }}>#</th>
                  <th style={{ textAlign: "right", padding: 4 }}>{tx("time")}</th>
                  <th style={{ textAlign: "left", padding: 4 }}>{tx("action")}</th>
                  <th style={{ textAlign: "left", padding: 4 }}>{tx("detail")}</th>
                </tr>
              </thead>
              <tbody>
                {selected.steps.map((row, index) => (
                  <tr key={`${row.step ?? index}`} data-denied={row.denied ? "true" : undefined}>
                    <td style={{ textAlign: "right", padding: 4 }}>{row.step ?? ""}</td>
                    <td style={{ textAlign: "right", padding: 4 }}>{typeof row.t === "number" ? row.t.toFixed(1) : ""}</td>
                    <td style={{ padding: 4 }}>{row.action}{row.denied ? ` (${tx("denied")})` : ""}</td>
                    <td style={{ padding: 4, wordBreak: "break-word" }}>
                      {stepDetail(row.args, row.result, row.denied, row.rule)}
                      {row.screenshot ? <div style={{ opacity: 0.7 }}>{row.screenshot}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
    </div>
  );
}
