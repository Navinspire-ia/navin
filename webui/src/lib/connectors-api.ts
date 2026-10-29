// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * App connectors (Settings > Channels > Connectors and the journal card).
 *
 * Values only travel from these forms to the gateway, which checks them with
 * the app before saving them in the local vault. Answers carry statuses,
 * account names and non-secret fields, never a key or a token.
 */

import { apiBodyHeaders, apiRequest } from "./api";

export type ConnectorAuth = "oauth" | "apikey" | "both";
export type ConnectorMethodType = "oauth2" | "api_key" | "custom" | "service_account";
export type ConnectorAuthFilter = "" | "oauth" | "apikey";
export type ConnectorStatusFilter = "" | "connected" | "available";

export interface ConnectorStatus {
  connected: boolean;
  enabled?: boolean;
  method?: string;
  account?: string;
  account_id?: string;
  connected_at?: number;
  expires_at?: number;
  scopes?: string[];
  fields?: Record<string, string>;
  error?: string;
}

export interface ConnectorSummary {
  service: string;
  name: string;
  category: string;
  auth: ConnectorAuth;
  auth_types: ConnectorMethodType[];
  homepage: string;
  description: string;
  icon: string;
  actions: number;
  status: ConnectorStatus;
}

export interface ConnectorCategory {
  id: string;
  label: string;
  label_fr: string;
  count: number;
  connected: number;
}

export interface ConnectorCatalogPage {
  items: ConnectorSummary[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  categories: ConnectorCategory[];
  matching: number;
  catalog_total: number;
  connected_total: number;
}

export interface ConnectorField {
  key: string;
  label: string;
  inputType: string;
  required: boolean;
  secret: boolean;
  placeholder?: string;
  defaultValue?: string;
  description?: string;
}

export interface ConnectorMethod {
  type: ConnectorMethodType;
  label: string;
  description?: string;
  fields: ConnectorField[];
  family?: string;
  scopes?: string[];
  pkce?: boolean;
  app_url?: string;
}

export interface ConnectorOAuthApp {
  family: string;
  label: string;
  client_id: string;
  has_secret: boolean;
  configured: boolean;
  redirect_uri: string;
  suggested_redirect_uri: string;
  app_url: string;
  services: string[];
  scopes: string[];
}

/** [name, type, required (1|0), description] */
export type ConnectorActionParam = [string, string, number, string];

export interface ConnectorAction {
  name: string;
  description: string;
  op: "read" | "write" | "destructive" | string;
  params: ConnectorActionParam[];
  /** REST endpoint when the catalog names it, e.g. "POST /crm/v3/objects/contacts". */
  endpoint?: string;
}

export interface ConnectorActionsPage {
  service: string;
  items: ConnectorAction[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface ConnectorDetail extends ConnectorSummary {
  methods: ConnectorMethod[];
  docs: string;
  base_url: string;
  oauth_app?: ConnectorOAuthApp;
  actions_page: ConnectorActionsPage;
  vault: "keyring" | "encrypted-file" | string;
}

export interface ConnectorResult {
  service: string;
  name: string;
  status: ConnectorStatus;
  ok?: boolean;
}

export interface ConnectorOAuthStart {
  service: string;
  authorization_url: string;
  expires_at: number;
  redirect_uri: string;
}

const READ_TIMEOUT_MS = 20_000;

type Query = Record<string, string | number | undefined | null>;

export function connectorAction<T>(
  token: string,
  action: string,
  body: Record<string, unknown> = {},
  query: Query = {},
  signal?: AbortSignal,
  timeoutMs = 0,
): Promise<T> {
  const params = new URLSearchParams({ action });
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  return apiRequest<T>(
    `/api/connectors?${params.toString()}`,
    token,
    { headers: apiBodyHeaders(JSON.stringify(body)), signal },
    timeoutMs,
  );
}

export function fetchConnectorCatalog(
  token: string,
  filters: {
    q?: string;
    category?: string;
    auth?: ConnectorAuthFilter;
    status?: ConnectorStatusFilter;
    page?: number;
    page_size?: number;
  } = {},
  signal?: AbortSignal,
): Promise<ConnectorCatalogPage> {
  return connectorAction<ConnectorCatalogPage>(token, "catalog", {}, filters, signal, READ_TIMEOUT_MS);
}

export function fetchConnectorDetail(token: string, service: string, signal?: AbortSignal): Promise<ConnectorDetail> {
  return connectorAction<ConnectorDetail>(token, "detail", {}, { service }, signal, READ_TIMEOUT_MS);
}

export function fetchConnectorActions(
  token: string,
  service: string,
  filters: { q?: string; op?: string; page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<ConnectorActionsPage> {
  return connectorAction<ConnectorActionsPage>(token, "actions", {}, { service, ...filters }, signal, READ_TIMEOUT_MS);
}

export function fetchConnectorStatus(
  token: string,
  service: string,
  signal?: AbortSignal,
): Promise<{ service: string; status: ConnectorStatus }> {
  return connectorAction(token, "status", {}, { service }, signal, READ_TIMEOUT_MS);
}

/** Blank fields are left out: the gateway applies defaults and required checks. */
export function nonEmptyValues(values: Record<string, string>): Record<string, string> {
  const clean: Record<string, string> = {};
  for (const [key, value] of Object.entries(values)) {
    const text = typeof value === "string" ? value.trim() : "";
    if (text) clean[key] = text;
  }
  return clean;
}

export function connectConnector(
  token: string,
  service: string,
  method: string,
  values: Record<string, string>,
): Promise<ConnectorResult> {
  return connectorAction<ConnectorResult>(token, "connect", { service, method, values: nonEmptyValues(values) });
}

export function testConnector(token: string, service: string): Promise<ConnectorResult> {
  return connectorAction<ConnectorResult>(token, "test", { service });
}

export function disconnectConnector(token: string, service: string): Promise<ConnectorResult> {
  return connectorAction<ConnectorResult>(token, "disconnect", { service });
}

export function toggleConnector(token: string, service: string, enabled: boolean): Promise<ConnectorResult> {
  return connectorAction<ConnectorResult>(token, "toggle", { service, enabled });
}

export function saveConnectorOAuthApp(
  token: string,
  service: string,
  app: { client_id: string; client_secret?: string; redirect_uri?: string },
): Promise<ConnectorOAuthApp> {
  return connectorAction<ConnectorOAuthApp>(token, "oauth_app", { service, ...app });
}

export function startConnectorOAuth(
  token: string,
  service: string,
  fields: Record<string, string> = {},
): Promise<ConnectorOAuthStart> {
  return connectorAction<ConnectorOAuthStart>(token, "oauth_start", { service, fields: nonEmptyValues(fields) });
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return typeof error === "string" && error ? error : "Something went wrong.";
}

/** Page numbers to show around the current one: 1 ... 4 5 6 ... 12. */
export function pageWindow(page: number, pages: number): (number | "gap")[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, index) => index + 1);
  const around = [page - 1, page, page + 1].filter((value) => value > 1 && value < pages);
  const out: (number | "gap")[] = [1];
  if ((around[0] ?? pages) > 2) out.push("gap");
  out.push(...around);
  if ((around[around.length - 1] ?? 1) < pages - 1) out.push("gap");
  out.push(pages);
  return out;
}
