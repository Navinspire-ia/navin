// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Settings > Organization (Navin SM): the model gateway (LiteLLM) of the
 * user's organization. A personal key goes in; answers only say whether one
 * is set, with a masked hint.
 */

import { apiBodyHeaders, apiRequest } from "./api";

export interface OrganizationModel {
  id: string;
  label: string;
  preset: string;
  context_window: number | null;
  max_output_tokens: number | null;
  vision: boolean;
  reasoning_effort: string | null;
  default: boolean;
  active: boolean;
}

export interface OrganizationGateway {
  base_url: string;
  auth: "user_key" | "shared_key" | "none";
  header_names: string[];
  ca_bundle: string | null;
  default_model: string;
  routes: Record<string, string>;
  models: OrganizationModel[];
}

export interface OrganizationStatus {
  present: boolean;
  import_path: string;
  source?: "managed" | "imported";
  locked?: boolean;
  path?: string;
  error?: string;
  exclusive?: boolean;
  organization?: { id: string; name: string; support_contact: string };
  gateway?: OrganizationGateway | null;
  user_key?: { configured: boolean; hint: string | null } | null;
}

export interface OrganizationTestResult {
  ok: boolean;
  status?: number;
  served?: number;
  missing?: string[];
  error?: string;
}

const TIMEOUT_MS = 20_000;

function organizationAction<T>(token: string, action: string, body: Record<string, unknown> = {}): Promise<T> {
  return apiRequest<T>(
    `/api/organization?action=${encodeURIComponent(action)}`,
    token,
    { headers: apiBodyHeaders(JSON.stringify(body)) },
    TIMEOUT_MS,
  );
}

export function fetchOrganizationStatus(token: string): Promise<OrganizationStatus> {
  return organizationAction<OrganizationStatus>(token, "status");
}

export function importOrganizationFile(token: string, content: string): Promise<OrganizationStatus> {
  return organizationAction<OrganizationStatus>(token, "import", { content });
}

export function removeOrganizationFile(token: string): Promise<OrganizationStatus> {
  return organizationAction<OrganizationStatus>(token, "remove");
}

export function saveOrganizationKey(token: string, apiKey: string): Promise<OrganizationStatus> {
  return organizationAction<OrganizationStatus>(token, "key", { api_key: apiKey });
}

export function testOrganizationGateway(token: string): Promise<OrganizationTestResult> {
  return organizationAction<OrganizationTestResult>(token, "test");
}
