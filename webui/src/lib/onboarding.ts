// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * First-run onboarding state.
 * Prefer ~/.navin/webui/onboarding.json via the gateway (survives upgrades and
 * cleared browser storage). localStorage remains a sync cache / offline fallback.
 */

import { fetchOnboardingState, updateOnboardingState } from "@/lib/api";

export const ONBOARDING_STORAGE_KEY = "navin.onboarding.v1";

export const ONBOARDING_PATHS = [
  "account",
  "byok",
  "free",
  "omniroute",
  "ollama",
  "skip",
] as const;

export type OnboardingPath = (typeof ONBOARDING_PATHS)[number];

/** Paths that leave the install with a working model at no cost. */
export const FREE_ONBOARDING_PATHS: readonly OnboardingPath[] = ["free", "omniroute", "ollama"];

function isOnboardingPath(value: unknown): value is OnboardingPath {
  return typeof value === "string" && (ONBOARDING_PATHS as readonly string[]).includes(value);
}

export type OnboardingState = {
  /** Wizard completed (or explicitly skipped). */
  completed: boolean;
  /** ISO timestamp when the wizard was finished. */
  completedAt?: string;
  /** Preferred language chosen in step 1. */
  language?: string;
  /**
   * Path chosen: account connect, BYOK providers, the Free plan (navin.live +
   * OpenRouter), or one of its keyless local flavours (OmniRoute, Ollama).
   */
  path?: OnboardingPath;
  /** Demo workspace was offered / opened. */
  demoOpened?: boolean;
};

export const DEFAULT_ONBOARDING: OnboardingState = {
  completed: false,
};

export function normalizeOnboardingState(raw: unknown): OnboardingState {
  if (!raw || typeof raw !== "object") return { ...DEFAULT_ONBOARDING };
  const parsed = raw as Partial<OnboardingState> & {
    completed_at?: string;
    demo_opened?: boolean;
  };
  return {
    completed: Boolean(parsed.completed),
    completedAt:
      typeof parsed.completedAt === "string"
        ? parsed.completedAt
        : typeof parsed.completed_at === "string"
          ? parsed.completed_at
          : undefined,
    language: typeof parsed.language === "string" ? parsed.language : undefined,
    path: isOnboardingPath(parsed.path) ? parsed.path : undefined,
    demoOpened: Boolean(parsed.demoOpened ?? parsed.demo_opened),
  };
}

export function readOnboardingState(): OnboardingState {
  if (typeof window === "undefined") return { ...DEFAULT_ONBOARDING };
  try {
    const raw = window.localStorage.getItem(ONBOARDING_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_ONBOARDING };
    return normalizeOnboardingState(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_ONBOARDING };
  }
}

export function writeOnboardingState(next: OnboardingState): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ONBOARDING_STORAGE_KEY, JSON.stringify(next));
}

export function markOnboardingComplete(
  partial: Partial<Omit<OnboardingState, "completed" | "completedAt">> = {},
  token?: string,
): OnboardingState {
  const next: OnboardingState = {
    ...readOnboardingState(),
    ...partial,
    completed: true,
    completedAt: new Date().toISOString(),
  };
  writeOnboardingState(next);
  if (token) {
    void updateOnboardingState(token, next).catch(() => {
      // localStorage already updated; disk sync is best-effort.
    });
  }
  return next;
}

export function needsFirstRunWizard(): boolean {
  return !readOnboardingState().completed;
}

/** Load disk state from the gateway and mirror it into localStorage. */
export async function hydrateOnboardingFromServer(
  token: string,
): Promise<OnboardingState> {
  try {
    const remote = normalizeOnboardingState(await fetchOnboardingState(token));
    if (remote.completed) {
      writeOnboardingState(remote);
      return remote;
    }
    const local = readOnboardingState();
    if (local.completed) {
      // Browser already finished the wizard; push to disk for next upgrade.
      await updateOnboardingState(token, local).catch(() => undefined);
      return local;
    }
    return remote;
  } catch {
    return readOnboardingState();
  }
}
