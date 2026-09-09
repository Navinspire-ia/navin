// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from "react";
import { Loader2, WifiOff } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import type { ConnectionStatus } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

export function ConnectionStatusBanner() {
  const { t } = useTranslation();
  const { client } = useClient();
  const [status, setStatus] = useState<ConnectionStatus>(client.status);

  useEffect(() => client.onStatus(setStatus), [client]);

  if (status === "open" || status === "idle") return null;
  const retrying = status === "connecting" || status === "reconnecting";
  const title = t(`connection.${status}`);
  const detail = retrying
    ? t("connection.retryingDetail")
    : t("connection.offlineDetail");

  return (
    <div
      role={status === "error" || status === "closed" ? "alert" : "status"}
      aria-live="assertive"
      className={cn(
        "mb-2 flex items-center gap-2 rounded-lg border px-3 py-2 text-[12px]",
        retrying
          ? "border-amber-500/35 bg-amber-500/[0.07] text-amber-800 dark:text-amber-200"
          : "border-destructive/35 bg-destructive/[0.07] text-destructive",
      )}
    >
      {retrying ? (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden />
      ) : (
        <WifiOff className="h-3.5 w-3.5 shrink-0" aria-hidden />
      )}
      <span className="font-semibold">{title}</span>
      <span className="min-w-0 text-current/75">{detail}</span>
    </div>
  );
}
