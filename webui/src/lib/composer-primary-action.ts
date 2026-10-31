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
 * A message typed while the agent works goes to the queue: the user lines up
 * several messages and keeps the queue's options on each (edit, send now,
 * Start Multitasking, reorder). Sending one into the live turn is the explicit
 * gesture (Send now on the queued row, Alt+Enter, Enter on an empty composer
 * right after queueing). Making send the default (2.4.0) took that away.
 */
export type ComposerPrimaryAction = "stop" | "queue" | "configure" | "send";

export function composerPrimaryAction({
  isStreaming,
  hasStopHandler,
  hasComposerContent,
  canQueueGuidance = false,
  modelNeedsSetup,
}: {
  isStreaming: boolean;
  hasStopHandler: boolean;
  hasComposerContent: boolean;
  /** Streaming + plain text (no slash command): the message goes to the queue. */
  canQueueGuidance?: boolean;
  modelNeedsSetup: boolean;
}): ComposerPrimaryAction {
  if (isStreaming && hasStopHandler) {
    if (!hasComposerContent) return "stop";
    if (canQueueGuidance) return "queue";
    // Slash command typed mid-run: submit() owns its lifecycle (an explicit
    // "/stop" still stops; other commands dispatch without ending the run).
    return "send";
  }
  if (modelNeedsSetup) return "configure";
  return "send";
}
