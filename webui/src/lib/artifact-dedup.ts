/**
 * Keep one result on screen instead of two.
 *
 * The Code module is the only one with an editor that renders HTML itself, so
 * it is the only one where a result can appear twice: once in the editor tab
 * opened by `open_file_preview`, once in the artifact canvas beside it. The
 * editor tab wins - it is the surface with Source/Preview, Save and Reload -
 * and anything already visible there is dropped from the canvas.
 *
 * Matching on `source_path` alone is not enough. An artifact presented as
 * inline content has no source path at all, which is exactly how the duplicate
 * that prompted this was produced, so bodies are compared by fingerprint too.
 */

import type { DevOpenFile } from "@/lib/dev-open-files";
import type { ArtifactRecord } from "@/lib/types";

/**
 * FNV-1a, as hex. Not a security hash: it only has to be cheap enough to run
 * on every keystroke in the editor and stable across the two copies of a body.
 */
export function fingerprintContent(text: string): string {
  const normalized = text.replace(/\r\n/g, "\n").trim();
  if (!normalized) return "";
  let hash = 0x811c9dc5;
  for (let i = 0; i < normalized.length; i += 1) {
    hash ^= normalized.charCodeAt(i);
    // The shifts are the 32-bit FNV prime multiply, kept in integer range.
    hash = (hash + ((hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24))) >>> 0;
  }
  return `${normalized.length.toString(36)}-${hash.toString(16)}`;
}

function normalizePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/**
 * Drop the artifacts the Code editor is already showing.
 *
 * With no open tabs - every module other than Code - the list comes back
 * untouched, so this cannot affect Montage, Studio or the plain chat view.
 */
export function artifactsNotInEditor(
  artifacts: ArtifactRecord[],
  openFiles: DevOpenFile[],
): ArtifactRecord[] {
  if (!openFiles.length || !artifacts.length) return artifacts;

  const openPaths = new Set<string>();
  const openHashes = new Set<string>();
  for (const file of openFiles) {
    openPaths.add(normalizePath(file.path));
    if (file.hash) openHashes.add(file.hash);
  }

  const kept = artifacts.filter((artifact) => {
    const source = artifact.source_path;
    if (typeof source === "string" && source.trim() && openPaths.has(normalizePath(source))) {
      return false;
    }
    const body = artifact.content;
    if (typeof body === "string" && body.trim() && openHashes.has(fingerprintContent(body))) {
      return false;
    }
    return true;
  });

  // Identity matters upstream: an unfiltered list must stay the same array so
  // the canvas does not re-render on every parent pass.
  return kept.length === artifacts.length ? artifacts : kept;
}
