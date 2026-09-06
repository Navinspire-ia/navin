import { Braces } from "lucide-react";
import { useMemo } from "react";

import { FileTypeIcon, FolderTypeIcon } from "@/components/dev/FileTypeIcon";

import {
  INLINE_TOKEN_HIGHLIGHT_COLOR,
  InlineTokenHighlight,
} from "@/components/InlineTokenHighlight";
import { useLogoFallback } from "@/hooks/useLogoFallback";
import {
  mentionToken,
  splitCapabilityMentionSegments,
  type CapabilityMentionSegment,
} from "@/lib/mention-parse";
import { logoFallbackUrls } from "@/lib/provider-brand";
import type { CliAppInfo, McpPresetInfo, ProjectFileMatch } from "@/lib/types";
import { cn } from "@/lib/utils";

export { mentionToken, splitCapabilityMentionSegments };
export type { CapabilityMentionSegment };

export function cliAppInitials(app: CliAppInfo): string {
  const value = app.display_name || app.name;
  return (
    value
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || app.name.slice(0, 2).toUpperCase()
  );
}
export function mcpPresetInitials(preset: Pick<McpPresetInfo, "name" | "display_name">): string {
  const value = preset.display_name || preset.name;
  return (
    value
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || preset.name.slice(0, 2).toUpperCase()
  );
}
export function CliAppMentionText({
  text,
  cliApps,
  mcpPresets = [],
  files = [],
}: {
  text: string;
  cliApps: CliAppInfo[];
  mcpPresets?: McpPresetInfo[];
  files?: ProjectFileMatch[];
}) {
  const segments = splitCapabilityMentionSegments(text, cliApps, mcpPresets, files);
  if (!segments.some((segment) => segment.kind !== "text")) return <>{text}</>;
  return (
    <>
      {segments.map((segment, index) => {
        if (segment.kind === "text") {
          return <span key={`text-${index}`}>{segment.text}</span>;
        }
        if (segment.kind === "cli") return (
          <CliAppMentionToken
            key={`cli-${segment.app.name}-${index}`}
            app={segment.app}
            label={segment.text}
            variant="message"
          />
        );
        if (segment.kind === "file") return (
          <FileMentionToken
            key={`file-${segment.file.path}-${index}`}
            file={segment.file}
            label={segment.text}
            variant="message"
          />
        );
        return (
          <McpPresetMentionToken
            key={`mcp-${segment.preset.name}-${index}`}
            preset={segment.preset}
            label={segment.text}
            variant="message"
          />
        );
      })}
    </>
  );
}

/** Hover text: a symbol needs its location, which its label does not carry. */
function mentionTitle(file: ProjectFileMatch): string {
  if (file.kind === "symbol") {
    const where = file.line ? `${file.path}:${file.line}` : file.path;
    return `${file.symbolKind || "Symbol"}: ${file.name} - ${where}`;
  }
  return `${file.kind === "directory" ? "Folder" : "File"}: ${file.path}`;
}

export function FileMentionToken({
  file,
  label,
  variant,
}: {
  file: ProjectFileMatch;
  label: string;
  variant: "composer" | "message";
}) {
  const testIdPrefix = variant === "composer" ? "composer" : "message";
  const shown = label.startsWith("@") ? label.slice(1) : label;
  const mentionIcon =
    file.kind === "directory" ? (
      <FolderTypeIcon name={file.name} className="h-[0.72em] w-[0.72em]" />
    ) : file.kind === "symbol" ? (
      <Braces aria-hidden className="h-[0.72em] w-[0.72em]" />
    ) : (
      <FileTypeIcon name={file.name || file.path} className="h-[0.72em] w-[0.72em]" />
    );

  return (
    <InlineTokenHighlight
      testId={`${testIdPrefix}-file-mention-${file.path}`}
      title={mentionTitle(file)}
      color={INLINE_TOKEN_HIGHLIGHT_COLOR}
    >
      <span className="relative inline-block text-transparent" style={{ lineHeight: "inherit" }}>
        @
        <span
          className={cn(
            "absolute left-1/2 top-1/2 grid place-items-center",
            "-translate-x-1/2 -translate-y-1/2 text-foreground/70",
          )}
        >
          {mentionIcon}
        </span>
      </span>
      {shown}
    </InlineTokenHighlight>
  );
}

export function CliAppMentionToken({
  app,
  label,
  variant,
  isHero = false,
}: {
  app: CliAppInfo;
  label: string;
  variant: "composer" | "message";
  isHero?: boolean;
}) {
  const color = app.brand_color || INLINE_TOKEN_HIGHLIGHT_COLOR;
  const mentionName = label.startsWith("@") ? label.slice(1) : label;
  const logoUrls = useMemo(() => logoFallbackUrls(app.logo_url), [app.logo_url]);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);
  const showLogo = Boolean(logoUrl);
  const testIdPrefix = variant === "composer" ? "composer" : "message";

  return (
    <InlineTokenHighlight
      testId={`${testIdPrefix}-cli-mention-${app.name}`}
      title={`CLI app: ${app.display_name || app.name}`}
      color={color}
    >
      <span
        className={cn("relative inline-block", showLogo && "text-transparent")}
        style={{ lineHeight: "inherit" }}
      >
        @
        {showLogo ? (
          <span
            data-testid={`${testIdPrefix}-cli-mention-logo-${app.name}`}
            className={cn(
              "absolute left-1/2 top-1/2 grid place-items-center overflow-hidden rounded-[3px]",
              "-translate-x-1/2 -translate-y-1/2",
              isHero ? "h-[0.74em] w-[0.74em]" : "h-[0.72em] w-[0.72em]",
            )}
          >
            <img
              src={logoUrl ?? ""}
              alt=""
              className="h-full w-full object-contain"
              decoding="async"
              loading="lazy"
              onLoad={onLogoLoad}
              onError={onLogoError}
            />
          </span>
        ) : null}
      </span>
      {mentionName}
    </InlineTokenHighlight>
  );
}

export function McpPresetMentionToken({
  preset,
  label,
  variant,
  isHero = false,
}: {
  preset: McpPresetInfo;
  label: string;
  variant: "composer" | "message";
  isHero?: boolean;
}) {
  const color = preset.brand_color || INLINE_TOKEN_HIGHLIGHT_COLOR;
  const mentionName = label.startsWith("@") ? label.slice(1) : label;
  const logoUrls = useMemo(() => logoFallbackUrls(preset.logo_url), [preset.logo_url]);
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);
  const showLogo = Boolean(logoUrl);
  const testIdPrefix = variant === "composer" ? "composer" : "message";

  return (
    <InlineTokenHighlight
      testId={`${testIdPrefix}-mcp-mention-${preset.name}`}
      title={`MCP server: ${preset.display_name || preset.name}`}
      color={color}
    >
      <span
        className={cn("relative inline-block", showLogo && "text-transparent")}
        style={{ lineHeight: "inherit" }}
      >
        @
        {showLogo ? (
          <span
            data-testid={`${testIdPrefix}-mcp-mention-logo-${preset.name}`}
            className={cn(
              "absolute left-1/2 top-1/2 grid place-items-center overflow-hidden rounded-[3px]",
              "-translate-x-1/2 -translate-y-1/2",
              isHero ? "h-[0.74em] w-[0.74em]" : "h-[0.72em] w-[0.72em]",
            )}
          >
            <img
              src={logoUrl ?? ""}
              alt=""
              className="h-full w-full object-contain"
              decoding="async"
              loading="lazy"
              onLoad={onLogoLoad}
              onError={onLogoError}
            />
          </span>
        ) : null}
      </span>
      {mentionName}
    </InlineTokenHighlight>
  );
}
