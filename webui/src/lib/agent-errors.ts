// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The backend persists its error bubbles in English (they are stored in
 * session history, where no UI language exists yet). The set is small and
 * stable - navin/providers/user_facing_errors.py plus the agent loop
 * fallbacks - so the chat localizes them at render time.
 *
 * Every entry here is pinned verbatim by tests on both sides:
 * agent-errors.test.ts (this table) and tests/test_user_facing_error_sentinels.py
 * (the backend output). Change one, and a test tells you to change the other.
 */

const SENTINELS: ReadonlyArray<{ text: string; key: string }> = [
  {
    text: "Sorry, I encountered an error calling the AI model.",
    key: "message.agentErrorModel",
  },
  {
    text: "Sorry, I encountered an error.",
    key: "message.agentErrorGeneric",
  },
  {
    text:
      "This model could not run with tools enabled "
      + "(needed for Agent, Review, Debug, etc.). "
      + "Ask mode may still work. Pick a tool-capable model "
      + "and try again.",
    key: "message.agentErrorToolUse",
  },
  {
    text: "Video download failed due to an authentication error. Please try again.",
    key: "message.agentErrorVideoAuth",
  },
  {
    text: "Audio failed due to an authentication error. Please try again.",
    key: "message.agentErrorAudioAuth",
  },
  {
    text:
      "The provider finished the job but produced no media. This is "
      + "usually a content-policy block (brand names, logos, real product "
      + "UI, people) or a transient provider failure. Rephrase the prompt "
      + "with neutral wording and try again, or switch to another media "
      + "model in Settings.",
    key: "message.agentErrorMediaEmpty",
  },
  {
    text:
      "The model is at capacity right now. Navin already retried "
      + "automatically. Wait a few seconds and send your message again, "
      + "or pick another model.",
    key: "message.agentErrorRateLimit",
  },
  {
    text:
      "Your own API key is out of credit. Top up at the provider, "
      + "or switch to a Navin plan model.",
    key: "message.agentErrorQuota",
  },
  {
    text:
      "Could not reach the model provider. Navin already retried "
      + "automatically. Check your internet connection, and any VPN, "
      + "proxy or firewall that could block the request, then send your "
      + "message again.",
    key: "message.agentErrorConnection",
  },
  {
    text:
      "The model provider hit a temporary error. Navin already retried "
      + "automatically. Send your message again, or pick another model.",
    key: "message.agentErrorUpstream",
  },
  {
    text:
      "This turn no longer fits in the model's context window. Start a "
      + "new conversation, or pick a model with a larger window.",
    key: "message.agentErrorContext",
  },
  {
    text:
      "Authentication with the model provider failed. Check the API key "
      + "in Settings, or switch to a Navin plan model.",
    key: "message.agentErrorAuth",
  },
  {
    text:
      "This model is no longer available. Pick another model from the "
      + "selector and try again.",
    key: "message.agentErrorNotFound",
  },
];

/** Exposed for the test that pins frontend and backend to the same strings. */
export const AGENT_ERROR_SENTINELS = SENTINELS;

/** i18n key for a known backend error sentinel, or null for normal content. */
export function agentErrorKey(
  content: string,
): { key: string; fallback: string } | null {
  const trimmed = content.trim().replace(/^(Error:\s*)+/i, "").trim();
  for (const sentinel of SENTINELS) {
    if (trimmed === sentinel.text) {
      return { key: sentinel.key, fallback: sentinel.text };
    }
  }
  return null;
}
