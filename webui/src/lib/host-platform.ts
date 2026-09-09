// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export type HostPlatform = "macos" | "windows" | "linux";

type NavigatorWithUserAgentData = Navigator & {
  userAgentData?: { platform?: string };
};

/** OS of the running desktop / browser, for update and release announcements. */
export function hostPlatform(): HostPlatform {
  if (typeof navigator === "undefined") return "linux";
  const platform = navigator.platform || "";
  const ua = navigator.userAgent || "";
  const uaPlatform =
    (navigator as NavigatorWithUserAgentData).userAgentData?.platform || "";
  const blob = `${platform} ${uaPlatform} ${ua}`;
  if (/mac|iphone|ipad|ipod/i.test(blob)) return "macos";
  if (/win/i.test(blob)) return "windows";
  return "linux";
}
