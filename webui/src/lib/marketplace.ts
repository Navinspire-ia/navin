// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Client Marketplace Navin (catalogue first-party sur le site de licence).
 * Soft-fail: réseau / API absente → liste vide + erreur typée, jamais un crash UI.
 */

const DEFAULT_SITE_URL = "https://navin.live";

export type MarketplaceSkillSummary = {
  id: string;
  slug: string;
  name: string;
  description: string;
  latestVersion: string;
  featured: boolean;
  createdAt: string;
  packageUrl?: string | null;
  contentHash?: string | null;
  changelog?: string;
};

export type MarketplaceSignedPackage = {
  slug: string;
  name: string;
  version: string;
  packageUrl: string | null;
  contentHash: string | null;
  signature: string;
  changelog: string;
  verified: boolean;
  skillMd?: string | null;
};

export type MarketplaceInstallResult = {
  install: {
    id: string;
    skillId: string;
    version: string;
    installedAt: string;
  } | null;
  package: MarketplaceSignedPackage;
};

export type MarketplaceListResult = {
  skills: MarketplaceSkillSummary[];
  /** null = ok ; sinon code stable pour l'UI */
  errorCode: "unavailable" | "http_error" | null;
  status?: number;
};

export function marketplaceBaseUrl(serverUrl?: string | null): string {
  const raw = (serverUrl || DEFAULT_SITE_URL).trim() || DEFAULT_SITE_URL;
  return raw.replace(/\/+$/, "");
}

function isNetworkFailure(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  const msg = err.message.toLowerCase();
  return (
    err.name === "TypeError" ||
    msg.includes("failed to fetch") ||
    msg.includes("networkerror") ||
    msg.includes("load failed") ||
    msg.includes("network request failed")
  );
}

/**
 * Liste les skills featured. Ne throw jamais pour un problème réseau :
 * renvoie `{ skills: [], errorCode: "unavailable" }`.
 */
export async function fetchMarketplaceSkills(
  serverUrl?: string | null,
  signal?: AbortSignal,
): Promise<MarketplaceListResult> {
  const base = marketplaceBaseUrl(serverUrl);
  try {
    const res = await fetch(`${base}/api/marketplace/skills`, {
      method: "GET",
      signal,
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      return { skills: [], errorCode: "http_error", status: res.status };
    }
    const body = (await res.json()) as { skills?: MarketplaceSkillSummary[] };
    return {
      skills: Array.isArray(body.skills) ? body.skills : [],
      errorCode: null,
    };
  } catch (err) {
    if (signal?.aborted) throw err;
    if (isNetworkFailure(err)) {
      return { skills: [], errorCode: "unavailable" };
    }
    return { skills: [], errorCode: "unavailable" };
  }
}

export async function installMarketplaceSkill(
  slug: string,
  options?: {
    serverUrl?: string | null;
    orgId?: string | null;
    accessToken?: string | null;
    signal?: AbortSignal;
  },
): Promise<MarketplaceInstallResult> {
  const base = marketplaceBaseUrl(options?.serverUrl);
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (options?.accessToken) {
    headers.Authorization = `Bearer ${options.accessToken}`;
  }
  let res: Response;
  try {
    res = await fetch(
      `${base}/api/marketplace/skills/${encodeURIComponent(slug)}/install`,
      {
        method: "POST",
        signal: options?.signal,
        headers,
        body: JSON.stringify({
          orgId: options?.orgId ?? undefined,
        }),
      },
    );
  } catch (err) {
    if (isNetworkFailure(err)) {
      throw new Error("marketplace_unavailable", { cause: err });
    }
    throw err;
  }
  if (!res.ok) {
    throw new Error(`marketplace_install_failed:${res.status}`);
  }
  return (await res.json()) as MarketplaceInstallResult;
}
