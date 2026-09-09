// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createContext, useContext, useMemo, type ReactNode } from "react";

import type { NavinClient } from "@/lib/navin-client";
import type { WebUIIngressLimits } from "@/lib/types";

interface ClientContextValue {
  client: NavinClient;
  token: string;
  modelName: string | null;
  ingressLimits: WebUIIngressLimits | null;
}

const ClientContext = createContext<ClientContextValue | null>(null);

export function ClientProvider({
  client,
  token,
  modelName = null,
  ingressLimits = null,
  children,
}: {
  client: NavinClient;
  token: string;
  modelName?: string | null;
  ingressLimits?: WebUIIngressLimits | null;
  children: ReactNode;
}) {
  // A fresh object here re-renders every useClient() consumer whenever App
  // re-renders, which during a stream is every frame.
  const value = useMemo(
    () => ({ client, token, modelName, ingressLimits }),
    [client, token, modelName, ingressLimits],
  );
  return <ClientContext.Provider value={value}>{children}</ClientContext.Provider>;
}

export function useClient(): ClientContextValue {
  const ctx = useContext(ClientContext);
  if (!ctx) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return ctx;
}
