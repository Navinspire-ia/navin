// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Monitor } from "lucide-react";

export type AppFactoryKind = "mobile" | "desktop";

/** One target; its mark is drawn by PlatformLogo from the same id. */
export type AppFactoryTarget = {
  id: string;
  kind: AppFactoryKind;
  labelKey: string;
  label: string;
  deliverables: string;
  folder: string;
};

const t = (
  id: string,
  kind: AppFactoryKind,
  label: string,
  deliverables: string,
): AppFactoryTarget => ({
  id,
  kind,
  labelKey: `dev.appFactory.targets.${id}`,
  label,
  deliverables,
  folder: `os/${id}`,
});

/** Targets the Code module offers, in display order. */
export const APP_FACTORY_TARGETS: AppFactoryTarget[] = [
  t("android", "mobile", "Android", ".apk / .aab"),
  t("ios", "mobile", "iOS", ".ipa"),
  t("windows", "desktop", "Windows", ".exe / .msi"),
  t("macos", "desktop", "macOS", ".dmg"),
  t("ubuntu", "desktop", "Ubuntu", ".deb"),
  t("centos", "desktop", "CentOS", ".rpm"),
  t("appimage", "desktop", "AppImage", ".AppImage"),
];

export const appFactoryTargetsOf = (kind: AppFactoryKind) =>
  APP_FACTORY_TARGETS.filter((target) => target.kind === kind);

export const appFactoryTarget = (id: string): AppFactoryTarget | undefined =>
  APP_FACTORY_TARGETS.find((target) => target.id === id);

/** Slash command registered by the engine for one target. */
export function appFactoryCommand(id: string): string {
  return `/apps ${id}`;
}

/**
 * The target of a message that is exactly the command the menus send
 * ("/apps android"), so the chat can show it as a request card instead of a
 * bare word. Anything longer stays a normal message.
 */
export function parseAppFactoryCommand(text: string): AppFactoryTarget | null {
  const match = /^\/apps\s+([a-z]+)$/i.exec(text.trim());
  return match ? appFactoryTarget(match[1].toLowerCase()) ?? null : null;
}

export const APP_FACTORY_DESKTOP_ICON = Monitor;
