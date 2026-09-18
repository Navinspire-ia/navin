// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { memo, useEffect, useRef, useState } from "react";
import { Spinner, SpinnerSize, Text } from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

function formatTurnElapsed(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
  if (minutes) return `${minutes}m ${seconds % 60}s`;
  return `${seconds}s`;
}

/** A turn stays visible between answer segments, tool calls and reconnects. */
export const TurnWorkingStatus = memo(function TurnWorkingStatus({
  active,
  startedAt,
  activityText,
  wide = false,
}: {
  active: boolean;
  startedAt: number | null;
  activityText?: string | null;
  wide?: boolean;
}) {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const [now, setNow] = useState(Date.now);
  const fallbackStart = useRef<number | null>(null);
  const serverStart = typeof startedAt === "number" && Number.isFinite(startedAt) && startedAt > 0
    ? startedAt * 1000 : null;
  const running = active || serverStart !== null;

  useEffect(() => {
    if (!running) {
      fallbackStart.current = null;
      return;
    }
    fallbackStart.current ??= Date.now();
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  if (!running) return null;
  const duration = formatTurnElapsed(now - (serverStart ?? fallbackStart.current ?? now));
  const working = t("thread.activityGroupWorking", { defaultValue: "Working" });

  return (
    <motion.div
      data-testid="turn-working-status"
      role="status"
      aria-label={working}
      initial={false}
      animate={{ opacity: 1 }}
      transition={{ duration: reduced ? 0 : 0.12 }}
      style={{
        display: "flex", flexDirection: "column", gap: 2,
        width: "100%", maxWidth: wide ? "58rem" : "49.5rem",
        minHeight: 28, margin: "0 auto 6px", padding: "0 12px",
        color: "hsl(var(--muted-foreground))",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 28 }}>
        <Spinner size={SpinnerSize.xSmall} aria-hidden styles={{
          circle: {
            borderColor: "hsl(var(--muted-foreground) / 0.25)",
            borderTopColor: "currentColor",
            ...(reduced ? { animationName: "none" } : {}),
          },
        }} />
        <Text styles={{ root: { fontFamily: "inherit", fontSize: 13, color: "inherit" } }}>{working}</Text>
        <Text as="span" role="timer" aria-live="off" styles={{ root: {
          fontFamily: "inherit", fontSize: 12, color: "inherit", fontVariantNumeric: "tabular-nums",
        } }}>{duration}</Text>
      </div>
      {activityText ? <Text
        data-testid="turn-activity-text"
        title={activityText}
        styles={{ root: {
          fontFamily: "inherit", fontSize: 13, lineHeight: "18px", color: "inherit",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        } }}
      >{activityText}</Text> : null}
    </motion.div>
  );
});
