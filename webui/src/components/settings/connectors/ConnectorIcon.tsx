// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from "react";

import { cn } from "@/lib/utils";

// Bundled with the app: icons work offline and never call a third party.
// Connector icons go by file name ("slack.svg"), channel logos by
// "channels/<file>" (see CHANNEL_ICONS).
const FILES = import.meta.glob<string>(
  ["../../../assets/connectors/*.{svg,png,webp}", "../../../assets/channels/*.{svg,png,webp}"],
  { eager: true, query: "?url", import: "default" },
);

const BY_NAME: Record<string, string> = {};
for (const [path, url] of Object.entries(FILES)) {
  const [folder, name] = path.split("/").slice(-2);
  if (name) BY_NAME[folder === "channels" ? `channels/${name}` : name] = url;
}

export function connectorIconUrl(icon: string | null | undefined): string | undefined {
  return icon ? BY_NAME[icon] : undefined;
}

const SIZES = {
  sm: "h-7 w-7 rounded-lg text-[11px]",
  md: "h-10 w-10 rounded-xl text-[13px]",
  lg: "h-12 w-12 rounded-2xl text-[15px]",
} as const;

export function ConnectorIcon({
  icon,
  name,
  size = "md",
  className,
}: {
  icon: string | null | undefined;
  name: string;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const url = connectorIconUrl(icon);
  const initials = name
    .split(/[\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  return (
    <span
      aria-hidden
      data-testid="connector-icon"
      className={cn(
        // White tile + hairline so dark logos stay readable in dark mode.
        "relative grid shrink-0 select-none place-items-center overflow-hidden bg-white font-semibold text-neutral-700 shadow-[inset_0_0_0_1px_rgba(0,0,0,0.08)]",
        SIZES[size],
        className,
      )}
    >
      {url && !failed ? (
        <img
          src={url}
          alt=""
          loading="lazy"
          decoding="async"
          draggable={false}
          onError={() => setFailed(true)}
          className="h-[68%] w-[68%] object-contain"
        />
      ) : (
        initials || "?"
      )}
    </span>
  );
}
