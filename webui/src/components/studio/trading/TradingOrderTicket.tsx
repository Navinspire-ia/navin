// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { DefaultButton, MessageBar, MessageBarType, PrimaryButton, TextField } from "@fluentui/react";

import type { TradingPosition, TradingQuote } from "@/lib/trading-api";
import { formatMoney, formatQty, orderTicketBody } from "@/lib/trading-format";
import { cn } from "@/lib/utils";

type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

const BUTTON_STYLES = { root: { minHeight: 40, cursor: "pointer" as const } };

/**
 * Manual buy/sell. The desk still routes it through the risk engine and the
 * configured venue, so a typed order is never a bypass, only a shortcut.
 */
export function TradingOrderTicket({
  symbol,
  quotes,
  positions,
  bookCurrency,
  fx,
  busy,
  tx,
  onSubmit,
  onSymbolChange,
  compact = false,
}: {
  symbol: string;
  quotes: Record<string, TradingQuote>;
  positions: TradingPosition[];
  bookCurrency: string;
  fx?: Record<string, number>;
  busy: boolean;
  tx: Tx;
  onSubmit: (body: Record<string, unknown>) => Promise<boolean>;
  onSymbolChange?: (symbol: string) => void;
  compact?: boolean;
}) {
  const reduceMotion = useReducedMotion();
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [mode, setMode] = useState<"qty" | "notional">("notional");
  const [amount, setAmount] = useState("");
  const [thesis, setThesis] = useState("");
  const [localSymbol, setLocalSymbol] = useState(symbol);
  const [error, setError] = useState("");
  const [sent, setSent] = useState("");

  useEffect(() => {
    setLocalSymbol(symbol);
  }, [symbol]);

  const upper = localSymbol.trim().toUpperCase();
  const quote = quotes[upper];
  const held = positions.find((row) => row.symbol === upper);
  const quoteCcy = String(quote?.currency || bookCurrency).toUpperCase();
  const rate = fx?.[quoteCcy] ?? (quoteCcy === bookCurrency.toUpperCase() ? 1 : undefined);
  const bookPrice = quote && rate != null ? quote.price * rate : quote?.price;

  const estimate = useMemo(() => {
    const value = Number(String(amount).replace(",", "."));
    if (!Number.isFinite(value) || value <= 0 || !bookPrice) return null;
    return mode === "qty" ? { qty: value, notional: value * bookPrice } : { qty: value / bookPrice, notional: value };
  }, [amount, mode, bookPrice]);

  useEffect(() => {
    if (side === "sell" && !held) setSide("buy");
  }, [held, side]);

  const submit = async () => {
    const ticket = orderTicketBody({ side, symbol: upper, mode, amount, thesis });
    if (!ticket.ok) {
      setError(
        ticket.error === "symbol"
          ? tx("ticketNeedSymbol", "Type a symbol first.")
          : tx("ticketNeedAmount", "Amount must be a positive number."),
      );
      return;
    }
    setError("");
    const ok = await onSubmit(ticket.body);
    if (ok) {
      setSent(
        tx("ticketSent", "{{side}} {{symbol}} sent to the desk. The risk engine and the venue answer in Orders.", {
          side: side === "buy" ? tx("buy", "Buy") : tx("sell", "Sell"),
          symbol: upper,
        }),
      );
      setAmount("");
      setThesis("");
      window.setTimeout(() => setSent(""), 5000);
    }
  };

  return (
    <div className={cn("space-y-3", compact ? "" : "max-w-xl")} data-testid="trading-order-ticket">
      <div className="grid grid-cols-2 gap-1 rounded-xl bg-muted/60 p-1" role="tablist" aria-label={tx("ticketSide", "Side")}>
        {(["buy", "sell"] as const).map((item) => {
          const disabled = item === "sell" && !held;
          return (
            <motion.button
              key={item}
              type="button"
              role="tab"
              aria-selected={side === item}
              disabled={disabled}
              whileTap={reduceMotion || disabled ? undefined : { scale: 0.97 }}
              onClick={() => setSide(item)}
              data-testid={`trading-ticket-${item}`}
              className={cn(
                "min-h-10 cursor-pointer rounded-lg text-sm font-medium transition-[background-color,color] duration-150",
                side === item
                  ? item === "buy"
                    ? "bg-emerald-600 text-white shadow-[0_6px_16px_rgba(5,150,105,0.35)]"
                    : "bg-red-600 text-white shadow-[0_6px_16px_rgba(220,38,38,0.35)]"
                  : "text-muted-foreground hover:text-foreground",
                disabled ? "cursor-not-allowed opacity-40" : "",
              )}
            >
              {item === "buy" ? tx("buy", "Buy") : tx("sell", "Sell")}
            </motion.button>
          );
        })}
      </div>
      <div className={cn("grid gap-3", compact ? "" : "sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]")}>
        <TextField
          label={tx("symbol", "Symbol")}
          value={localSymbol}
          placeholder="NVDA, BTC-USD, MC.PA"
          onChange={(_, value) => {
            const next = (value || "").toUpperCase();
            setLocalSymbol(next);
            onSymbolChange?.(next);
          }}
          description={
            quote
              ? `${formatMoney(quote.price, quote.currency || bookCurrency)}${
                  bookPrice != null && quoteCcy !== bookCurrency.toUpperCase() ? ` = ${formatMoney(bookPrice, bookCurrency)}` : ""
                }${quote.market_state ? ` · ${quote.market_state}` : ""}`
              : tx("ticketNoQuote", "No live quote yet; the desk fetches one when you send.")
          }
        />
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="text-sm font-semibold">{tx("ticketAmount", "Amount")}</span>
            <div className="inline-flex rounded-lg bg-muted/60 p-0.5 text-xs">
              {(["notional", "qty"] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setMode(item)}
                  className={cn(
                    "min-h-7 cursor-pointer rounded-md px-2 font-medium transition-[background-color] duration-150",
                    mode === item ? "bg-background shadow-sm" : "text-muted-foreground",
                  )}
                >
                  {item === "notional" ? bookCurrency : tx("qty", "Qty")}
                </button>
              ))}
            </div>
          </div>
          <TextField
            value={amount}
            placeholder={mode === "notional" ? "1500" : "1"}
            inputMode="decimal"
            onChange={(_, value) => setAmount(value || "")}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void submit();
              }
            }}
            description={
              estimate
                ? `${tx("ticketEstimate", "About")} ${formatQty(estimate.qty)} × ${formatMoney(bookPrice, bookCurrency)} = ${formatMoney(estimate.notional, bookCurrency)}`
                : held && side === "sell"
                  ? tx("ticketHeld", "You hold {{qty}} {{symbol}}", { qty: formatQty(held.qty), symbol: held.symbol })
                  : ""
            }
          />
          {held && side === "sell" ? (
            <button
              type="button"
              className="mt-1 cursor-pointer text-xs font-medium text-amber-700 hover:underline dark:text-amber-300"
              onClick={() => {
                setMode("qty");
                setAmount(String(held.qty));
              }}
            >
              {tx("ticketAll", "Sell everything ({{qty}})", { qty: formatQty(held.qty) })}
            </button>
          ) : null}
        </div>
      </div>
      <TextField
        label={tx("ticketThesis", "Why (goes to the journal)")}
        value={thesis}
        multiline
        rows={2}
        onChange={(_, value) => setThesis(value || "")}
      />
      {error ? <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar> : null}
      {sent ? <MessageBar messageBarType={MessageBarType.success}>{sent}</MessageBar> : null}
      <div className="flex flex-wrap gap-2">
        <PrimaryButton
          text={side === "buy" ? tx("ticketSendBuy", "Send buy") : tx("ticketSendSell", "Send sell")}
          iconProps={{ iconName: side === "buy" ? "Add" : "Remove" }}
          disabled={busy || !upper}
          onClick={() => void submit()}
          styles={BUTTON_STYLES}
          data-testid="trading-ticket-send"
        />
        <DefaultButton
          text={tx("ticketClear", "Clear")}
          disabled={busy}
          onClick={() => {
            setAmount("");
            setThesis("");
            setError("");
          }}
          styles={BUTTON_STYLES}
        />
      </div>
      <p className="text-pretty text-xs text-muted-foreground" style={{ textWrap: "pretty" }}>
        {tx(
          "ticketHint",
          "Every ticket goes through the risk engine (size, drawdown, daily loss, asset lists). Buys above the approval cap wait in Orders; sells always pass when you hold the name.",
        )}
      </p>
    </div>
  );
}
