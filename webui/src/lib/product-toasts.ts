/** Admin-published release card. Hidden when the install toast already covers it. */
export function isReleaseAnnouncement(item: { id: string; kind?: string }): boolean {
  return item.kind === "release" || item.id.startsWith("release-");
}
