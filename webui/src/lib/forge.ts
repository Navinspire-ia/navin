// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Wording and setup hints for the forge behind `origin`.
 *
 * The Code panel talks to GitHub, GitLab and Forgejo over REST, so its
 * buttons cannot say "PR" everywhere: GitLab calls it a merge request, and
 * the old "gh CLI not installed" error has to become "no token for this
 * host, here is where to add one".
 */

import type { ForgeKind, GithubPrViewPayload } from "@/lib/types";

/** An i18n lookup the caller resolves with its own `t`. */
export interface ForgeCopy {
  key: string;
  fallback: string;
}

/** Env var the backend reads per forge, offered as the no-Settings shortcut. */
export const FORGE_ENV_VARS: Record<string, string> = {
  github: "GITHUB_TOKEN",
  gitlab: "GITLAB_TOKEN",
  forgejo: "FORGEJO_TOKEN",
};

export function isMergeRequestForge(kind: ForgeKind | undefined | null): boolean {
  return kind === "gitlab";
}

export function forgeEnvVar(kind: ForgeKind | undefined | null): string | null {
  return FORGE_ENV_VARS[kind ?? ""] ?? null;
}

export type ForgeCopySlot =
  | "draft"
  | "create"
  | "commitAndCreate"
  | "draftOpened"
  | "opened"
  | "reused"
  | "committed"
  | "failed";

const PULL_REQUEST_COPY: Record<ForgeCopySlot, ForgeCopy> = {
  draft: { key: "dev.git.draftPr", fallback: "Draft PR" },
  create: { key: "dev.git.createPr", fallback: "Create PR" },
  commitAndCreate: {
    key: "dev.git.commitAndCreatePr",
    fallback: "Commit & Create PR",
  },
  draftOpened: { key: "dev.git.prCreated", fallback: "Draft PR opened" },
  opened: { key: "dev.git.prOpenedNotice", fallback: "PR opened" },
  reused: { key: "dev.git.prReused", fallback: "Existing PR reused" },
  committed: {
    key: "dev.git.commitPrNotice",
    fallback: "Committed and opened PR",
  },
  failed: { key: "dev.git.prFailed", fallback: "Could not open PR" },
};

const MERGE_REQUEST_COPY: Record<ForgeCopySlot, ForgeCopy> = {
  draft: { key: "dev.git.draftMr", fallback: "Draft MR" },
  create: { key: "dev.git.createMr", fallback: "Create Merge Request" },
  commitAndCreate: {
    key: "dev.git.commitAndCreateMr",
    fallback: "Commit & Create MR",
  },
  draftOpened: {
    key: "dev.git.mrCreatedDraft",
    fallback: "Draft merge request opened",
  },
  opened: { key: "dev.git.mrOpenedNotice", fallback: "Merge request opened" },
  reused: {
    key: "dev.git.mrReused",
    fallback: "Existing merge request reused",
  },
  committed: {
    key: "dev.git.commitMrNotice",
    fallback: "Committed and opened merge request",
  },
  failed: {
    key: "dev.git.mrFailed",
    fallback: "Could not open merge request",
  },
};

export function forgeCopy(
  kind: ForgeKind | undefined | null,
): Record<ForgeCopySlot, ForgeCopy> {
  return isMergeRequestForge(kind) ? MERGE_REQUEST_COPY : PULL_REQUEST_COPY;
}

export interface ForgeSetupHint extends ForgeCopy {
  host: string;
  /** Offered next to the hint when the token can come from the shell. */
  env: string | null;
}

/**
 * The single setup step that used to surface as "gh CLI not installed".
 *
 * Returns `null` when nothing is missing, and also when there is no forge
 * remote at all: a local-only repository is a normal state, not a warning.
 */
export function forgeSetupHint(
  view: Pick<GithubPrViewPayload, "forge" | "host" | "token_configured"> | null
    | undefined,
): ForgeSetupHint | null {
  const host = view?.host ?? "";
  if (!host) return null;
  const kind = view?.forge ?? "unknown";
  if (kind === "unknown") {
    return {
      key: "dev.git.forgeUnknownHint",
      fallback: "Cannot tell what {{host}} runs. Pick its type in Settings > Git.",
      host,
      env: null,
    };
  }
  if (view?.token_configured) return null;
  return {
    key: "dev.git.forgeTokenMissing",
    fallback: "No token for {{host}}. Add one in Settings > Git.",
    host,
    env: forgeEnvVar(kind),
  };
}
