/**
 * What the composer's round primary button does on click.
 *
 * Regression guard for "salut stopped my mission": while a run is live the
 * button used to become Stop unconditionally, so the natural "type a message,
 * click the round button" gesture silently sent /stop and killed the running
 * tasks. A typed ordinary message must never turn into a stop - with content
 * present the button sends/queues it, and Stop only acts on an empty composer.
 */
export type ComposerPrimaryAction = "stop" | "queue" | "configure" | "send";

export function composerPrimaryAction({
  isStreaming,
  hasStopHandler,
  hasComposerContent,
  canQueueGuidance,
  modelNeedsSetup,
}: {
  isStreaming: boolean;
  hasStopHandler: boolean;
  hasComposerContent: boolean;
  /** Streaming + plain text (no slash command): the message can queue as guidance. */
  canQueueGuidance: boolean;
  modelNeedsSetup: boolean;
}): ComposerPrimaryAction {
  if (isStreaming && hasStopHandler) {
    if (canQueueGuidance) return "queue";
    if (!hasComposerContent) return "stop";
    // Slash command typed mid-run: submit() owns its lifecycle (an explicit
    // "/stop" still stops; other commands dispatch without ending the run).
    return "send";
  }
  if (modelNeedsSetup) return "configure";
  return "send";
}
