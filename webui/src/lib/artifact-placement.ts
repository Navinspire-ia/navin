/**
 * Where a rendered artifact belongs on screen.
 *
 * It used to be rendered inside the chat column, as a sibling of the message
 * list. At 520px inside a 497px column it won the width fight and the
 * conversation collapsed to nothing, so a Three.js scene looked like it had
 * replaced the chat. Results belong where every other result already goes: the
 * workbench area in the centre, with the conversation intact beside it.
 *
 * The chat-only view has no workbench to render into, so it keeps the old
 * resizable side panel - there, the whole width is the chat's anyway.
 */
export type ArtifactPlacement = "workbench" | "chat-side" | "hidden";

export interface ArtifactPlacementInput {
  /** The canvas is open (not closed or mid close-animation). */
  open: boolean;
  /** How many artifacts exist for this chat. */
  artifactCount: number;
  /** A workbench centre area exists and can host the canvas. */
  hasWorkbenchHost: boolean;
}

export function resolveArtifactPlacement({
  open,
  artifactCount,
  hasWorkbenchHost,
}: ArtifactPlacementInput): ArtifactPlacement {
  if (!open || artifactCount <= 0) return "hidden";
  return hasWorkbenchHost ? "workbench" : "chat-side";
}
