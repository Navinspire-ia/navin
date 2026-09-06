import type { SettingsPayload } from "@/lib/types";

/** navin.live account + managed Navin provider. Off on main / navin-agi. */
export function liveAccountEnabled(settings?: SettingsPayload | null): boolean {
  return settings?.live_account === true;
}
