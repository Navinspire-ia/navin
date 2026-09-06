/**
 * How an image the agent already delivered gets back into the composer.
 *
 * Generated media reaches the UI as a signed ``/api/media/...`` URL while the
 * composer only speaks base64 attachments, so reuse re-reads the bytes the
 * gateway already holds and hands them to the normal upload path: same MIME
 * allowlist, same size limits, same preview chip, nothing new trusted on
 * ingress. Once attached, the agent sees a regular image path and can pass it
 * to ``generate_image`` as a reference.
 *
 * The publishers - a markdown image in an assistant reply, an activity evidence
 * tile - sit outside the composer's subtree, so this is a module bus rather than
 * a prop chain, like ``notification-bus``.
 */

import { ACCEPTED_IMAGE_MIMES, mediaExtension } from "@/lib/media";
import type { UIMediaAttachment } from "@/lib/types";

export interface MediaReuseRequest {
  url: string;
  name?: string;
}

type Listener = (request: MediaReuseRequest) => void;

const listeners = new Set<Listener>();

export function onMediaReuse(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Ask the mounted composer to attach this media.
 *
 * Returns ``false`` when no composer is listening, so the caller can say
 * something instead of looking like it worked.
 */
export function requestMediaReuse(request: MediaReuseRequest): boolean {
  if (listeners.size === 0) return false;
  for (const listener of listeners) {
    try {
      listener(request);
    } catch {
      // A broken subscriber must not break the tile the user just clicked.
    }
  }
  return true;
}

/**
 * Only images qualify: the composer has no video attachment kind, and a
 * ``blob:`` URL belongs to a page that may already have revoked it.
 */
export function canReuseMedia(attachment: UIMediaAttachment): boolean {
  if (attachment.kind !== "image") return false;
  const url = attachment.url ?? "";
  return url.length > 0 && !url.startsWith("blob:");
}

const IMAGE_MIME_BY_EXTENSION: ReadonlyMap<string, string> = new Map([
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".webp", "image/webp"],
  [".gif", "image/gif"],
]);

const EXTENSION_BY_IMAGE_MIME: ReadonlyMap<string, string> = new Map([
  ["image/png", ".png"],
  ["image/jpeg", ".jpg"],
  ["image/webp", ".webp"],
  ["image/gif", ".gif"],
]);

/**
 * Resolve the MIME to attach with, or ``null`` when the composer would reject it.
 *
 * The served type wins; the name is the fallback because the media endpoint
 * degrades anything outside its own allowlist to ``application/octet-stream``.
 */
export function reusableImageMime(servedType: string, nameHint?: string): string | null {
  const served = servedType.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  if (ACCEPTED_IMAGE_MIMES.has(served)) return served;
  const byName = IMAGE_MIME_BY_EXTENSION.get(mediaExtension(nameHint));
  return byName ?? null;
}

/** A chip label that carries the right extension without carrying a path. */
export function reusedMediaFileName(nameHint: string | undefined, mime: string): string {
  const ext = EXTENSION_BY_IMAGE_MIME.get(mime) ?? "";
  const base = (nameHint ?? "").split(/[\\/]/).pop()?.trim() ?? "";
  if (!base) return `reused-image${ext}`;
  return IMAGE_MIME_BY_EXTENSION.get(mediaExtension(base)) === mime ? base : `${base}${ext}`;
}

/**
 * Fetch a delivered media and wrap it as a ``File`` the composer can enqueue.
 *
 * The signed URL is its own credential, so no token is attached, and it is
 * relative: the dev server proxies ``/api`` to the gateway like every other
 * call.
 */
export async function fetchReusableMediaFile(
  request: MediaReuseRequest,
  fetchImpl: typeof fetch = fetch,
): Promise<File> {
  const response = await fetchImpl(request.url);
  if (!response.ok) {
    throw new Error(`media request failed with HTTP ${response.status}`);
  }
  const blob = await response.blob();
  const mime = reusableImageMime(blob.type ?? "", request.name ?? request.url);
  if (!mime) {
    throw new Error(`cannot reuse media of type ${blob.type || "unknown"}`);
  }
  return new File([blob], reusedMediaFileName(request.name, mime), { type: mime });
}
