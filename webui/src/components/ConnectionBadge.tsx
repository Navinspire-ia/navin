// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useGatewayPace, useGatewayStallSeconds } from "@/hooks/useGatewayPace";
import type { ConnectionStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

export type ConnectionBadgeState = "connected" | "busy" | "down";

/**
 * One state for the dot, from the socket status and the HTTP pace.
 *
 * An open socket while reads time out is still an engine the user is waiting
 * on, so a stall shows as "busy" (amber, pulsing) rather than green.
 */
export function connectionBadgeState(
  status: ConnectionStatus,
  stalled: boolean,
): ConnectionBadgeState {
  if (status === "closed" || status === "error") return "down";
  if (status === "connecting" || status === "reconnecting" || status === "idle") return "busy";
  return stalled ? "busy" : "connected";
}

const DOT: Record<ConnectionBadgeState, string> = {
  connected: "bg-emerald-500",
  busy: "bg-amber-500",
  down: "bg-red-500",
};

/**
 * Engine indicator for the sidebar footer: the chat views have no status bar,
 * so this is where they learn that the engine is connected, slow or gone.
 * Sized like the neighbouring icon buttons so the footer stays aligned.
 */
export function ConnectionBadge({ className }: { className?: string } = {}) {
  const { t } = useTranslation();
  const { client } = useClient();
  const [status, setStatus] = useState<ConnectionStatus>(client.status);
  useEffect(() => client.onStatus(setStatus), [client]);

  const pace = useGatewayPace();
  const stallSeconds = useGatewayStallSeconds(pace);
  const stalled = pace.stall !== null;
  const state = connectionBadgeState(status, stalled);

  const label =
    state !== "down" && stalled
      ? t(
          pace.stall === "unreachable"
            ? "transport.pace.unreachableHint"
            : "transport.pace.slowHint",
          { seconds: stallSeconds },
        )
      : t(`connection.${status}`);

  return (
    <span
      className={cn(
        "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
        className,
      )}
      role="status"
      aria-live="polite"
      title={label}
      data-testid="connection-badge"
      data-state={state}
      data-stall={stalled ? pace.stall : undefined}
    >
      <span className="relative flex h-2 w-2" aria-hidden>
        {state === "busy" ? (
          <span
            className={cn(
              "absolute inline-flex h-full w-full animate-ping rounded-full opacity-75",
              DOT[state],
            )}
          />
        ) : null}
        <span className={cn("relative inline-flex h-2 w-2 rounded-full", DOT[state])} />
      </span>
      <span className="sr-only">{label}</span>
    </span>
  );
}
