// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Small pure helpers behind "Git & GitHub" in Settings and the Import Git
 * fields: who needs sign-in, what a shorthand means, where to make a token.
 */

import { ApiError } from "@/lib/api-error";

export type GitTokenKind = "auto" | "github" | "gitlab" | "forgejo" | "bitbucket";

// Kinds the engine stores a token as. Bitbucket has no forge API in the engine
// yet: its host stays "auto", which the credential bridge still recognizes
// for clone and push, and "bitbucket" only picks the "Create a token" page.
export type StoredGitTokenKind = Exclude<GitTokenKind, "bitbucket">;

export const GIT_TOKEN_KINDS: StoredGitTokenKind[] = ["auto", "github", "gitlab", "forgejo"];

/** Scopes the engine needs on GitHub: clone private repos, list orgs, push workflows. */
export const GITHUB_TOKEN_SCOPES = ["repo", "read:org", "workflow"];

export interface GitAuthRequired {
  host: string;
  message: string;
}

/** The clone route's 401 `{code: "auth_required", host, message}`, or null. */
export function gitAuthRequired(error: unknown): GitAuthRequired | null {
  if (!(error instanceof ApiError) || error.status !== 401) return null;
  if (error.code !== "auth_required") return null;
  const body = error.body ?? {};
  const host = typeof body.host === "string" && body.host.trim() ? body.host.trim() : "github.com";
  const message = typeof body.message === "string" ? body.message : error.message;
  return { host, message };
}

/**
 * Push, pull, PR and CI calls do not all answer with `auth_required`: git
 * itself says "Authentication failed" or "could not read Username", a forge
 * API says "Bad credentials". Any of these means "connect this host first".
 */
const GIT_AUTH_FAILURE =
  /auth(entication)?[ _]required|authentication failed|could not read username|bad credentials|invalid username or password|requires authentication|terminal prompts disabled|permission denied \(publickey\)|no token for|returned error: 40[13]|401 unauthorized/i;

export function looksLikeGitAuthFailure(error: unknown): boolean {
  if (gitAuthRequired(error)) return true;
  const text = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return GIT_AUTH_FAILURE.test(text);
}

const SHORTHAND = /^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9._-]+$/;

/** `owner/repo` becomes a GitHub URL; anything else is passed through trimmed. */
export function normalizeGitImportUrl(input: string): string {
  const trimmed = input.trim();
  if (SHORTHAND.test(trimmed)) {
    const repo = trimmed.replace(/\.git$/i, "");
    return `https://github.com/${repo}.git`;
  }
  return trimmed;
}

/** Best guess of the forge from its host name, for the "Create a token" link. */
export function guessForgeKind(host: string): Exclude<GitTokenKind, "auto"> | null {
  const h = host.trim().toLowerCase();
  if (!h) return null;
  if (h === "github.com" || h.includes("github")) return "github";
  if (h === "gitlab.com" || h.includes("gitlab")) return "gitlab";
  if (h === "bitbucket.org" || h.includes("bitbucket")) return "bitbucket";
  if (h === "codeberg.org" || h.includes("forgejo") || h.includes("gitea")) return "forgejo";
  return null;
}

function cleanHost(host: string): string {
  return host.trim().replace(/^https?:\/\//i, "").replace(/\/.*$/, "") || "github.com";
}

/** The forge page where a personal access token with the needed scopes is made. */
export function createTokenUrl(host: string, kind: GitTokenKind): string | null {
  const h = cleanHost(host);
  const resolved = kind === "auto" ? guessForgeKind(h) : kind;
  switch (resolved) {
    case "github": {
      const query = new URLSearchParams({
        scopes: GITHUB_TOKEN_SCOPES.join(","),
        description: "Navin",
      });
      return `https://${h}/settings/tokens/new?${query}`;
    }
    case "gitlab": {
      const query = new URLSearchParams({
        name: "Navin",
        scopes: "api,read_repository,write_repository",
      });
      return `https://${h}/-/user_settings/personal_access_tokens?${query}`;
    }
    case "forgejo":
      return `https://${h}/user/settings/applications`;
    case "bitbucket":
      return "https://id.atlassian.com/manage-profile/security/api-tokens";
    default:
      return null;
  }
}

/**
 * The repository a failed import was waiting on while the user went to sign
 * in. Settings replaces the view that held the field, so the field would come
 * back empty: this is what lets it offer Retry with the same URL.
 */
let pendingImport: { url: string; host: string } | null = null;

export function rememberPendingGitImport(url: string, host: string): void {
  pendingImport = { url, host };
}

export function pendingGitImport(): { url: string; host: string } | null {
  return pendingImport;
}

export function clearPendingGitImport(): void {
  pendingImport = null;
}
