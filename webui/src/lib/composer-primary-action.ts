// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * What the composer's round primary button does on click.
 *
 * Regression guard for "salut stopped my mission": while a run is live the
 * button used to become Stop unconditionally, so the natural "type a message,
 * click the round button" gesture silently sent /stop and killed the running
 * tasks. A typed ordinary message must never turn into a stop - with content
 * present the button sends it, and Stop only acts on an empty composer.
 *
 * A message typed while the agent works is sent right away: the gateway
 * injects it into the live turn, so the agent reads it at its next step.
 * Parking it in the queue until the turn ended (the old "queue" action) is
 * what made the agent look blind to anything typed mid-run. The queue stays
 * an explicit gesture (ListPlus button, Alt+Enter).
 */
export type ComposerPrimaryAction = "stop" | "queue" | "configure" | "send";

export function composerPrimaryAction({
  isStreaming,
  hasStopHandler,
  hasComposerContent,
  modelNeedsSetup,
}: {
  isStreaming: boolean;
  hasStopHandler: boolean;
  hasComposerContent: boolean;
  /** Streaming + plain text (no slash command): kept for callers, no longer
   * selects the queue; the mid-run default is to send. */
  canQueueGuidance?: boolean;
  modelNeedsSetup: boolean;
}): ComposerPrimaryAction {
  if (isStreaming && hasStopHandler) {
    if (!hasComposerContent) return "stop";
    // Content typed mid-run (plain text or a slash command) is sent: submit()
    // owns its lifecycle (an explicit "/stop" still stops; other commands
    // dispatch without ending the run; plain text joins the live turn).
    return "send";
  }
  if (modelNeedsSetup) return "configure";
  return "send";
}
