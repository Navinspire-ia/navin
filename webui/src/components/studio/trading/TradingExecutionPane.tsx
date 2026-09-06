import { useEffect, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { DefaultButton, Icon, MessageBar, MessageBarType, PrimaryButton, TextField, Toggle } from "@fluentui/react";

import type { TradingConnectionResult, TradingDesk } from "@/lib/trading-api";
import { formatMoney } from "@/lib/trading-format";
import { cn } from "@/lib/utils";

type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

const BUTTON_STYLES = { root: { minHeight: 40, cursor: "pointer" as const } };

const SECRET_LABELS: Record<string, string> = {
  alpaca_key: "APCA-API-KEY-ID",
  alpaca_secret: "APCA-API-SECRET-KEY",
  binance_key: "API key",
  binance_secret: "Secret key",
};

function Surface({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cn(
        "rounded-2xl shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 dark:outline-white/10",
        className,
      )}
    >
      {children}
    </div>
  );
}

function ago(ts: number | undefined, tx: Tx): string {
  if (!ts) return tx("guardNever", "never");
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (seconds < 90) return tx("guardSecondsAgo", "{{n}} s ago", { n: seconds });
  if (seconds < 5400) return tx("guardMinutesAgo", "{{n}} min ago", { n: Math.round(seconds / 60) });
  return tx("guardHoursAgo", "{{n}} h ago", { n: Math.round(seconds / 3600) });
}

/**
 * Where fills come from (paper, Alpaca, Binance), what they cost, how often
 * the intraday guard runs, and which model each AI task is routed to.
 */
export function TradingExecutionPane({
  desk,
  busy,
  tx,
  call,
}: {
  desk: TradingDesk;
  busy: boolean;
  tx: Tx;
  call: (action: string, body?: Record<string, unknown>) => Promise<Record<string, unknown> | null>;
}) {
  const reduceMotion = useReducedMotion();
  const status = desk.broker;
  const execution = desk.settings.execution || {};
  const [fee, setFee] = useState(String(execution.fee_bps ?? status?.fee_bps ?? 0));
  const [slip, setSlip] = useState(String(execution.slippage_bps ?? status?.slippage_bps ?? 0));
  const [guardEvery, setGuardEvery] = useState(String(Math.round(Number(execution.guard_interval_s ?? status?.guard_interval_s ?? 900) / 60)));
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [connection, setConnection] = useState<TradingConnectionResult | null>(null);
  const [flash, setFlash] = useState("");

  useEffect(() => {
    setFee(String(execution.fee_bps ?? status?.fee_bps ?? 0));
    setSlip(String(execution.slippage_bps ?? status?.slippage_bps ?? 0));
    setGuardEvery(String(Math.round(Number(execution.guard_interval_s ?? status?.guard_interval_s ?? 900) / 60)));
  }, [execution.fee_bps, execution.slippage_bps, execution.guard_interval_s, status?.fee_bps, status?.slippage_bps, status?.guard_interval_s]);

  useEffect(() => {
    if (!flash) return undefined;
    const id = window.setTimeout(() => setFlash(""), 4000);
    return () => window.clearTimeout(id);
  }, [flash]);

  const pickBroker = async (id: string) => {
    const out = await call("broker", { broker: id });
    if (out) setFlash(tx("brokerPicked", "Fills now come from {{broker}}.", { broker: id }));
  };

  const saveSecret = async (name: string) => {
    const value = secrets[name] || "";
    const out = await call("secret", { name, value });
    if (out) {
      setSecrets((current) => ({ ...current, [name]: "" }));
      setFlash(value ? tx("secretSaved", "Key stored locally in .navin/trading/secrets.json (0600).") : tx("secretCleared", "Key cleared."));
    }
  };

  const testConnection = async (id: string) => {
    setConnection(null);
    const out = await call("connection", { broker: id });
    if (out?.connection) setConnection(out.connection as TradingConnectionResult);
  };

  const saveCosts = async () => {
    const out = await call("broker", {
      fee_bps: Number(fee) || 0,
      slippage_bps: Number(slip) || 0,
      guard_interval_s: Math.max(60, Math.round((Number(guardEvery) || 15) * 60)),
    });
    if (out) setFlash(tx("costsSaved", "Costs and guard cadence saved."));
  };

  const runGuard = async () => {
    const out = await call("guard", { force: true });
    if (out) setFlash(tx("guardRan", "Guard pass done: {{result}}", { result: String(out.reason || "ok") }));
  };

  const aiOn = desk.settings.ai_assist !== false;

  return (
    <div className="space-y-4" data-testid="trading-execution-pane">
      {flash ? <MessageBar messageBarType={MessageBarType.success}>{flash}</MessageBar> : null}
      <MessageBar messageBarType={MessageBarType.info}>
        {tx(
          "executionInfo",
          "Paper needs no key and books every approved order at the live quote. Alpaca (US stocks, ETFs, crypto) and Binance (crypto spot) execute for real with your own keys; sandbox modes are on by default.",
        )}
      </MessageBar>

      <div className="grid gap-3 lg:grid-cols-3">
        {(status?.brokers || []).map((row) => {
          const active = row.active;
          const sandboxKey = row.id === "alpaca" ? "alpaca_paper" : row.id === "binance" ? "binance_testnet" : "";
          return (
            <motion.div
              key={row.id}
              layout
              initial={false}
              animate={{ scale: 1 }}
              whileHover={reduceMotion ? undefined : { y: -2 }}
              className={cn(
                "rounded-2xl p-4 shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 transition-[outline-color] duration-150",
                active ? "outline-amber-500/60 bg-amber-500/8" : "outline-black/10 dark:outline-white/10",
              )}
              data-testid={`trading-broker-${row.id}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-base font-semibold capitalize">{row.id}</p>
                  <p className="text-xs text-muted-foreground">{row.assets}</p>
                </div>
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 text-[11px] font-medium",
                    active ? "bg-amber-500/20 text-amber-800 dark:text-amber-200" : "bg-muted text-muted-foreground",
                  )}
                >
                  {active ? tx("brokerActive", "Active") : row.configured ? tx("brokerReady", "Keys set") : row.id === "paper" ? tx("brokerNoKeys", "No key needed") : tx("brokerMissing", "Keys missing")}
                </span>
              </div>
              {row.secrets.length ? (
                <div className="mt-3 space-y-2">
                  {row.secrets.map((secret) => (
                    <div key={secret.name} className="flex items-end gap-2">
                      <TextField
                        className="min-w-0 flex-1"
                        type="password"
                        canRevealPassword
                        label={SECRET_LABELS[secret.name] || secret.name}
                        placeholder={secret.set ? "••••••••" : ""}
                        value={secrets[secret.name] || ""}
                        onChange={(_, value) => setSecrets((current) => ({ ...current, [secret.name]: value || "" }))}
                        description={secret.set ? tx("secretSet", "stored") : tx("secretUnset", "not set")}
                      />
                      <DefaultButton
                        text={secrets[secret.name] ? tx("secretSave", "Save") : secret.set ? tx("secretClear", "Clear") : tx("secretSave", "Save")}
                        disabled={busy || (!secrets[secret.name] && !secret.set)}
                        onClick={() => void saveSecret(secret.name)}
                        styles={BUTTON_STYLES}
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-3 text-sm text-muted-foreground">
                  {tx("paperExplain", "Fills at the live quote with the fee and slippage below. The book, stops and journal work exactly like a live venue.")}
                </p>
              )}
              {sandboxKey ? (
                <Toggle
                  className="mt-3"
                  checked={row.sandbox}
                  disabled={busy}
                  onText={row.id === "alpaca" ? tx("alpacaPaper", "Paper account") : tx("binanceTestnet", "Testnet")}
                  offText={tx("brokerLiveMoney", "Live money")}
                  onChange={(_, checked) => void call("broker", { [sandboxKey]: Boolean(checked) })}
                />
              ) : null}
              <div className="mt-3 flex flex-wrap gap-2">
                {active ? null : (
                  <PrimaryButton
                    text={tx("brokerUse", "Use this venue")}
                    disabled={busy || (row.id !== "paper" && !row.configured)}
                    onClick={() => void pickBroker(row.id)}
                    styles={BUTTON_STYLES}
                  />
                )}
                <DefaultButton
                  text={tx("brokerTest", "Test connection")}
                  iconProps={{ iconName: "PlugConnected" }}
                  disabled={busy || (row.id !== "paper" && !row.configured)}
                  onClick={() => void testConnection(row.id)}
                  styles={BUTTON_STYLES}
                />
              </div>
              {connection && connection.broker === row.id ? (
                <div
                  className={cn(
                    "mt-3 rounded-xl px-3 py-2 text-sm",
                    connection.ok ? "bg-emerald-600/12 text-emerald-800 dark:text-emerald-200" : "bg-red-600/12 text-red-800 dark:text-red-200",
                  )}
                  data-testid="trading-connection-result"
                >
                  {connection.ok
                    ? tx("connectionOk", "Connected. {{detail}}", {
                        detail: [
                          connection.cash != null ? `${tx("cash", "Cash")} ${formatMoney(Number(connection.cash), String(connection.currency || "USD"))}` : "",
                          connection.equity != null ? `${tx("equity", "Equity")} ${formatMoney(Number(connection.equity), String(connection.currency || "USD"))}` : "",
                          connection.status ? String(connection.status) : "",
                        ]
                          .filter(Boolean)
                          .join(" · "),
                      })
                    : tx("connectionFailed", "Not connected: {{error}}", { error: String(connection.error || "unknown") })}
                </div>
              ) : null}
            </motion.div>
          );
        })}
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Surface className="p-4">
          <p className="text-xs uppercase text-muted-foreground">{tx("costsTitle", "Costs and intraday guard")}</p>
          <div className="mt-2 grid gap-3 sm:grid-cols-3">
            <TextField label={tx("feeBps", "Fee (bps)")} value={fee} inputMode="decimal" onChange={(_, value) => setFee(value || "")} />
            <TextField label={tx("slippageBps", "Slippage (bps)")} value={slip} inputMode="decimal" onChange={(_, value) => setSlip(value || "")} />
            <TextField
              label={tx("guardEvery", "Guard every (min)")}
              value={guardEvery}
              inputMode="numeric"
              onChange={(_, value) => setGuardEvery(value || "")}
            />
          </div>
          <p className="mt-2 text-pretty text-xs text-muted-foreground" style={{ textWrap: "pretty" }}>
            {tx(
              "costsHint",
              "Paper fills pay these costs so the book matches a real venue. The guard walks held names between cycles: stops, take-profits, venue fills and your alert thresholds.",
            )}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <PrimaryButton text={tx("saveCosts", "Save")} disabled={busy} onClick={() => void saveCosts()} styles={BUTTON_STYLES} />
            <DefaultButton
              text={tx("runGuard", "Run guard now")}
              iconProps={{ iconName: "Shield" }}
              disabled={busy}
              onClick={() => void runGuard()}
              styles={BUTTON_STYLES}
              data-testid="trading-run-guard"
            />
            <span className="text-xs tabular-nums text-muted-foreground">
              {tx("guardLast", "Last pass {{when}}", { when: ago(desk.guard?.last_guard, tx) })}
              {desk.guard?.last_guard_result ? ` · ${desk.guard.last_guard_result}` : ""}
            </span>
          </div>
        </Surface>

        <Surface className="p-4">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-xs uppercase text-muted-foreground">{tx("aiTitle", "Model assist")}</p>
              <p className="mt-1 text-pretty text-sm text-muted-foreground" style={{ textWrap: "pretty" }}>
                {tx(
                  "aiHint",
                  "Optional. When a model is routed, it reads headlines for sentiment and writes the research note. It never places or approves orders; the risk engine stays code.",
                )}
              </p>
            </div>
            <Toggle
              checked={aiOn}
              disabled={busy}
              onText={tx("aiOn", "On")}
              offText={tx("aiOff", "Off")}
              onChange={(_, checked) => void call("settings", { ai_assist: Boolean(checked) })}
            />
          </div>
          <ul className="mt-3 space-y-1.5 text-sm">
            {(desk.ai?.tasks || []).map((task) => (
              <li key={task.task} className="flex items-center justify-between gap-2 rounded-xl bg-muted/40 px-3 py-1.5">
                <span className="font-medium capitalize">{task.task}</span>
                <span className={cn("text-xs tabular-nums", task.preset ? "text-foreground" : "text-muted-foreground")}>
                  {task.preset ? `${task.preset}${task.model ? ` · ${task.model}` : ""}` : tx("aiUnrouted", "keywords only")}
                </span>
              </li>
            ))}
          </ul>
          {!desk.ai?.enabled || !desk.ai?.routed ? (
            <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <Icon iconName="Info" aria-hidden />
              {tx("aiNoRoute", "No model routed yet. Settings > Models > Routes, roles trading or research.")}
            </p>
          ) : null}
        </Surface>
      </div>
    </div>
  );
}
