import { useCallback, useRef, useState } from "react";

import { acceptedAttachmentKind } from "@/hooks/useAttachedImages";

/** Extract supported attachment ``File``s from a paste / drop event.
 *
 * Deliberate behaviour:
 *   - Only items whose ``kind === "file"`` and match the Composer whitelist
 *     are returned; HTML fragments are ignored (defending against remote URL
 *     fetch + XSS surfaces).
 *   - Plain text pasted alongside attachments is *not* consumed by this helper,
 *     so the caller can still let the textarea receive it naturally.
 */
/**
 * A file manager hands the path over as text next to the image itself
 * (WebKitGTK always does, Chromium sometimes). Inserting `file:///.../shot.png`
 * into the prompt on top of the attachment is never what the user meant.
 */
export function pasteTextIsRedundant(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return true;
  if (/\r|\n/.test(trimmed)) return false;
  return /^file:\/\//i.test(trimmed) || /^\/[^\s]*\.[a-z0-9]{1,8}$/i.test(trimmed);
}

/** True when paste is files only. Real text next to a file must stay in the box. */
export function consumePasteForAttachments(text: string, fileCount: number): boolean {
  return fileCount > 0 && pasteTextIsRedundant(text);
}

/**
 * Chromium puts a screenshot on both `items` and `files`. `getAsFile()` and
 * `files[0]` are two File objects: same bytes, different `lastModified` /
 * name (`image.png` vs `image.png` with a new clock). The old
 * name:size:mtime key then attached the picture twice.
 *
 * WebKitGTK still leaves `items` empty, so `files` is the fallback.
 */
export function filePasteKey(file: File): string {
  return `${file.type}:${file.size}`;
}

export function pasteFilesKey(files: readonly File[]): string {
  return files.map(filePasteKey).sort().join("|");
}

export const PASTE_BURST_MS = 400;

export function isDuplicatePasteBurst(
  prev: { at: number; key: string } | null,
  key: string,
  now: number,
): boolean {
  if (!key || !prev) return false;
  return prev.key === key && now - prev.at < PASTE_BURST_MS;
}

function collectAcceptedFiles(
  items: ArrayLike<DataTransferItem> | undefined,
  fileList: ArrayLike<File> | undefined,
): File[] {
  const seen = new Set<string>();
  const pushInto = (file: File | null, into: File[]): void => {
    if (!file || !acceptedAttachmentKind(file)) return;
    const key = filePasteKey(file);
    if (seen.has(key)) return;
    seen.add(key);
    into.push(file);
  };
  const fromItems: File[] = [];
  for (const item of Array.from(items ?? [])) {
    if (item.kind !== "file") continue;
    pushInto(item.getAsFile(), fromItems);
  }
  if (fromItems.length > 0) return fromItems;
  const fromFiles: File[] = [];
  for (const file of Array.from(fileList ?? [])) pushInto(file, fromFiles);
  return fromFiles;
}

export function extractImageFilesFromPaste(
  event: ClipboardEvent | React.ClipboardEvent,
): File[] {
  const clipboard = (event as ClipboardEvent).clipboardData
    ?? (event as React.ClipboardEvent).clipboardData;
  if (!clipboard) return [];
  return collectAcceptedFiles(clipboard.items, clipboard.files);
}

/** Extract dropped attachment files, mirroring ``extractImageFilesFromPaste``. */
export function extractImageFilesFromDrop(
  event: DragEvent | React.DragEvent,
): File[] {
  const dt = (event as DragEvent).dataTransfer
    ?? (event as React.DragEvent).dataTransfer;
  if (!dt) return [];
  return collectAcceptedFiles(dt.items, dt.files);
}

/**
 * Chromium announces a file drag as `Files`; WebKitGTK announces the GTK
 * types instead, so the old `Files`-only check skipped preventDefault, the
 * engine refused the drop and no `drop` event ever reached the composer.
 * Text selections carry `text/plain` without any URI type, so they stay out.
 */
export function dragCarriesFiles(types: readonly string[] | undefined): boolean {
  const list = Array.from(types ?? []);
  return list.some(
    (type) =>
      type === "Files"
      || type === "text/uri-list"
      || type === "public.file-url"
      || type.startsWith("application/x-moz-file"),
  );
}

export interface UseClipboardAndDropApi {
  /** Whether a drag is currently hovering the drop zone (toggle dragover UI). */
  isDragging: boolean;
  onPaste: (
    event: React.ClipboardEvent,
  ) => void;
  onDragEnter: (event: React.DragEvent) => void;
  onDragOver: (event: React.DragEvent) => void;
  onDragLeave: (event: React.DragEvent) => void;
  onDrop: (event: React.DragEvent) => void;
}

/** Wire paste + drag-and-drop to a callback.
 *
 * The hook owns ``isDragging`` state and the refcount that keeps it accurate
 * across nested ``dragenter`` / ``dragleave`` events (a known DOM gotcha: the
 * text cursor inside a textarea fires ``dragleave`` on entry, flicking the
 * highlight off otherwise). */
export function useClipboardAndDrop(
  onImageFiles: (files: File[]) => void,
): UseClipboardAndDropApi {
  const [isDragging, setIsDragging] = useState(false);
  const dragDepth = useRef(0);
  const lastPaste = useRef<{ at: number; key: string } | null>(null);

  const onPaste = useCallback(
    (event: React.ClipboardEvent) => {
      const files = extractImageFilesFromPaste(event);
      const text = event.clipboardData?.getData("text/plain") ?? "";
      const key = pasteFilesKey(files);
      const now = Date.now();
      if (files.length > 0 && isDuplicatePasteBurst(lastPaste.current, key, now)) {
        event.preventDefault();
        return;
      }
      if (files.length > 0) lastPaste.current = { at: now, key };
      if (!consumePasteForAttachments(text, files.length)) {
        if (files.length > 0) onImageFiles(files);
        return;
      }
      event.preventDefault();
      onImageFiles(files);
    },
    [onImageFiles],
  );

  const onDragEnter = useCallback((event: React.DragEvent) => {
    if (!dragCarriesFiles(event.dataTransfer.types)) return;
    event.preventDefault();
    dragDepth.current += 1;
    setIsDragging(true);
  }, []);

  const onDragOver = useCallback((event: React.DragEvent) => {
    if (!dragCarriesFiles(event.dataTransfer.types)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, []);

  const onDragLeave = useCallback((event: React.DragEvent) => {
    if (!dragCarriesFiles(event.dataTransfer.types)) return;
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setIsDragging(false);
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      dragDepth.current = 0;
      setIsDragging(false);
      if (!dragCarriesFiles(event.dataTransfer?.types)) return;
      // Swallow the drop even when nothing usable came out of it: letting it
      // through makes the webview navigate away to the dropped file.
      event.preventDefault();
      const files = extractImageFilesFromDrop(event);
      if (files.length === 0) return;
      onImageFiles(files);
    },
    [onImageFiles],
  );

  return { isDragging, onPaste, onDragEnter, onDragOver, onDragLeave, onDrop };
}
