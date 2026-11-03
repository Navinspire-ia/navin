import { useSyncExternalStore } from "react";

/**
 * The teammates' uploaded photos (id -> data URL), as Settings > Team sends
 * them. A teammate only carries a marker ("custom:<hash>"): every face in the
 * chat, the team view and Settings reads its picture from here.
 */
let photos: Readonly<Record<string, string>> = {};
const listeners = new Set<() => void>();
const PICTURE = /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/;

export function setTeamPhotos(next: Record<string, string> | null | undefined): void {
  const clean: Record<string, string> = {};
  for (const [id, url] of Object.entries(next ?? {})) {
    if (typeof url === "string" && PICTURE.test(url)) clean[id] = url;
  }
  photos = clean;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const snapshot = () => photos;

/** The picture to show: a fresh data URL as is, a saved photo from the store. */
export function resolveTeamPhoto(
  store: Readonly<Record<string, string>>,
  id: string | undefined,
  photo: string | undefined,
): string | undefined {
  if (photo && PICTURE.test(photo)) return photo;
  if (!id || !photo?.startsWith("custom:")) return undefined;
  return store[id];
}

export function useTeamPhoto(id: string | undefined, photo: string | undefined): string | undefined {
  const store = useSyncExternalStore(subscribe, snapshot, snapshot);
  return resolveTeamPhoto(store, id, photo);
}
