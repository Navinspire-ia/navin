// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Check,
  ChevronRight,
  Clock3,
  Copy,
  Download,
  ImageIcon,
  Loader2,
  Pencil,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { AttachmentTile } from "@/components/AttachmentTile";
import { CliAppMentionText } from "@/components/CliAppMentionText";
import { DocumentTemplateBadge } from "@/components/thread/DocumentTemplatePicker";
import {
  isProviderCreditMessage,
  isQuotaLimitMessage,
  ProviderCreditCard,
  QuotaLimitCard,
} from "@/components/thread/QuotaLimitCard";
import { ImageLightbox } from "@/components/ImageLightbox";
import { MarkdownText } from "@/components/MarkdownText";
import { ReasoningCard } from "@/components/thread/activity/ReasoningRow";
import { RevertPromptDialog } from "@/components/RevertPromptDialog";
import { SlashCommandText } from "@/components/SlashCommandText";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { agentErrorKey } from "@/lib/agent-errors";
import { copyTextToClipboard } from "@/lib/clipboard";
import { formatTurnLatency } from "@/lib/format";
import { downloadMediaAttachment, toMediaAttachment } from "@/lib/media";
import { matchingSlashCommand } from "@/lib/slash-command";
import { visibleUserChatText } from "@/lib/chat-visible-text";
import { stripModeRoutingSlash } from "@/components/thread/ComposerModeMenu";
import type {
  CliAppInfo,
  McpPresetInfo,
  SlashCommand,
  UICliAppAttachment,
  UIMcpPresetAttachment,
  UIImage,
  UIMediaAttachment,
  UIMessage,
} from "@/lib/types";

interface MessageBubbleProps {
  message: UIMessage;
  /** When false, hide this message's copy button. Default true. */
  showCopyAction?: boolean;
  cliApps?: CliAppInfo[];
  mcpPresets?: McpPresetInfo[];
  slashCommands?: SlashCommand[];
  onOpenFilePreview?: (path: string) => void;
  onForkFromHere?: () => void;
  /** Edit + resubmit a prior user prompt (forks history then sends). */
  onRevertResubmit?: (content: string) => void | Promise<void>;
  /**
   * A later user prompt follows this assistant message. Refusal cards (plan
   * quota, provider credit) then shrink to a past-tense line: the thread has
   * moved on and must not keep announcing a live problem.
   */
  superseded?: boolean;
}

function ForkArrowIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M16 3h5v5" />
      <path d="M8 3H3v5" />
      <path d="m21 3-7.536 7.536A5 5 0 0 0 12 14.07V21" />
      <path d="m3 3 7.536 7.536A5 5 0 0 1 12 14.07V15" />
    </svg>
  );
}

/** Icon-only action next to a message: small enough to sit beside the text
 * instead of claiming a row of its own. */
const MESSAGE_ACTION_CLASS = cn(
  "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
  "transition-colors hover:bg-muted/60 hover:text-foreground",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);
const MESSAGE_ACTION_ICON_CLASS = "h-3.5 w-3.5";

function MessageCopyButton({ content }: { content: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copyResetRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyResetRef.current !== null) {
        window.clearTimeout(copyResetRef.current);
      }
    };
  }, []);

  const onCopy = useCallback(() => {
    void copyTextToClipboard(content).then((ok) => {
      if (!ok) return;
      setCopied(true);
      if (copyResetRef.current !== null) {
        window.clearTimeout(copyResetRef.current);
      }
      copyResetRef.current = window.setTimeout(() => {
        setCopied(false);
        copyResetRef.current = null;
      }, 1_500);
    });
  }, [content]);

  const label = copied ? t("message.copiedReply") : t("message.copyReply");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={onCopy}
          aria-label={label}
          data-testid="message-copy"
          className={MESSAGE_ACTION_CLASS}
        >
          {copied ? (
            <Check className={MESSAGE_ACTION_ICON_CLASS} aria-hidden />
          ) : (
            <Copy className={MESSAGE_ACTION_ICON_CLASS} aria-hidden />
          )}
        </button>
      </TooltipTrigger>
      <TooltipContent side="top" align="center">{label}</TooltipContent>
    </Tooltip>
  );
}

/**
 * Render a single message. Following agent-chat-ui: user turns are a rounded
 * "pill" right-aligned with a muted fill; assistant turns render as bare
 * markdown so prose/code read like a document rather than a chat bubble.
 * Each turn fades+slides in for a touch of motion polish.
 *
 * Trace rows (tool-call hints, progress breadcrumbs) render as a subdued
 * collapsible group so intermediate steps never masquerade as replies.
 */
export const MessageBubble = memo(function MessageBubble({
  message,
  showCopyAction = true,
  cliApps = [],
  mcpPresets = [],
  slashCommands = [],
  onOpenFilePreview,
  onForkFromHere,
  onRevertResubmit,
  superseded = false,
}: MessageBubbleProps) {
  const { t } = useTranslation();
  // Enter animation only while the assistant is still streaming. Historical
  // rows (and a remount after session history hydrates with new ids) must not
  // fade in again - that is the "chat blinks" flicker.
  const baseAnim = message.isStreaming
    ? "animate-in fade-in-0 slide-in-from-bottom-1 duration-300 motion-reduce:animate-none"
    : "";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(visibleUserChatText(message.content));
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [revertBusy, setRevertBusy] = useState(false);
  const mentionCliApps = useMemo(
    () => mergeCliMentionApps(cliApps, message.cliApps),
    [cliApps, message.cliApps],
  );
  const mentionMcpPresets = useMemo(
    () => mergeMcpMentionPresets(mcpPresets, message.mcpPresets),
    [mcpPresets, message.mcpPresets],
  );
  // Hooks must run on every render: this used to sit below the trace/user
  // early returns, which breaks the hook order if a message ever changes
  // shape under the same key (and fails react-hooks/rules-of-hooks).
  const phaseTimingsMs = message.phaseTimingsMs;
  const phaseTimingEntries = useMemo(() => {
    if (!phaseTimingsMs) return [];
    return Object.entries(phaseTimingsMs)
      .filter(([, ms]) => typeof ms === "number" && Number.isFinite(ms))
      .sort(([a], [b]) => a.localeCompare(b));
  }, [phaseTimingsMs]);

  if (message.kind === "trace") {
    return <TraceGroup message={message} animClass={baseAnim} />;
  }

  if (message.role === "user") {
    const images = message.images ?? [];
    const media = message.media ?? [];
    const hasImages = images.length > 0;
    const hasMedia = media.length > 0;
    const visibleContent = visibleUserChatText(message.content);
    const hasText = visibleContent.trim().length > 0;
    const slashCommand = matchingSlashCommand(visibleContent, slashCommands);
    // Mode routing (/ask, /forge, /blueprint, …) is composer plumbing: show
    // only the prompt text. Checked on the raw first token so it works even
    // when the server command list is empty or the command declines args.
    // A bare command with no args keeps its pill.
    const routingRemainder = stripModeRoutingSlash(visibleContent);
    const messageText = routingRemainder ? (
      <CliAppMentionText
        text={routingRemainder}
        cliApps={mentionCliApps}
        mcpPresets={mentionMcpPresets}
      />
    ) : slashCommand ? (
      <>
        <SlashCommandText command={slashCommand.command} />
        <CliAppMentionText
          text={visibleContent.slice(slashCommand.command.length)}
          cliApps={mentionCliApps}
          mcpPresets={mentionMcpPresets}
        />
      </>
    ) : (
      <CliAppMentionText
        text={visibleContent}
        cliApps={mentionCliApps}
        mcpPresets={mentionMcpPresets}
      />
    );

    const submitEdit = () => {
      const next = draft.trim();
      if (!next || !onRevertResubmit) return;
      setConfirmOpen(true);
    };

    const confirmRevert = async () => {
      if (!onRevertResubmit) return;
      setRevertBusy(true);
      try {
        await onRevertResubmit(draft.trim());
        setConfirmOpen(false);
        setEditing(false);
      } finally {
        setRevertBusy(false);
      }
    };

    return (
      <div
        className={cn(
          "group ml-auto flex max-w-[min(85%,36rem)] flex-col items-end gap-1.5",
          baseAnim,
        )}
      >
        {message.documentTemplate ? (
          <DocumentTemplateBadge template={message.documentTemplate} />
        ) : null}
        {(message.mediaTemplates?.length
          ? message.mediaTemplates
          : message.mediaTemplate
            ? [message.mediaTemplate]
            : []
        ).map((mediaRef) => (
          <span
            key={mediaRef.id}
            className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border/55 bg-muted/45 px-2 py-0.5 text-[11px] text-muted-foreground"
            data-testid="media-template-badge"
          >
            <ImageIcon className="h-3 w-3 shrink-0" aria-hidden />
            <span className="truncate">{mediaRef.title || mediaRef.id}</span>
            {mediaRef.format ? (
              <span className="text-muted-foreground/80">{mediaRef.format}</span>
            ) : null}
          </span>
        ))}
        {hasImages ? <UserImages images={images} align="right" /> : null}
        {!hasImages && hasMedia ? (
          <MessageMedia media={media} align="right" />
        ) : null}
        {editing ? (
          <div className="w-full min-w-[16rem] rounded-[16px] border border-border/70 bg-secondary/40 p-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={Math.min(8, Math.max(2, draft.split("\n").length))}
              className="w-full resize-none bg-transparent px-1.5 py-1 text-[14px] leading-relaxed text-foreground outline-none"
              autoFocus
            />
            <div className="mt-1 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setDraft(visibleUserChatText(message.content));
                }}
                className="rounded-md px-2 py-1 text-[12px] text-muted-foreground hover:text-foreground"
              >
                {t("common.cancel", { defaultValue: "Cancel" })}
              </button>
              <button
                type="button"
                onClick={submitEdit}
                disabled={!draft.trim()}
                className="rounded-md bg-foreground px-2.5 py-1 text-[12px] font-medium text-background disabled:opacity-50"
              >
                {t("thread.revert.submit", { defaultValue: "Submit" })}
              </button>
            </div>
          </div>
        ) : hasText ? (
          <div className="flex w-full min-w-0 items-end justify-end gap-1">
            {showCopyAction || onRevertResubmit ? (
              <TooltipProvider delayDuration={220} skipDelayDuration={80}>
                <div
                  className={cn(
                    "mb-1 flex shrink-0 items-center text-muted-foreground/75",
                    "opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100",
                    "[@media(hover:none)]:opacity-100",
                  )}
                  data-testid="user-message-actions"
                >
                  {onRevertResubmit ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          onClick={() => {
                            setDraft(visibleUserChatText(message.content));
                            setEditing(true);
                          }}
                          className={MESSAGE_ACTION_CLASS}
                          aria-label={t("thread.revert.edit", { defaultValue: "Edit message" })}
                        >
                          <Pencil className={MESSAGE_ACTION_ICON_CLASS} aria-hidden />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">
                        {t("thread.revert.edit", { defaultValue: "Edit message" })}
                      </TooltipContent>
                    </Tooltip>
                  ) : null}
                  {showCopyAction ? <MessageCopyButton content={visibleContent} /> : null}
                </div>
              </TooltipProvider>
            ) : null}
            <p
              className={cn(
                "w-fit max-w-full min-w-0 rounded-[16px] bg-secondary/70 px-3.5 py-2",
                "text-left text-[length:var(--chat-font-size)] leading-[var(--chat-line-height)]",
                "whitespace-pre-wrap [overflow-wrap:anywhere]",
              )}
            >
              {messageText}
            </p>
          </div>
        ) : null}
        <RevertPromptDialog
          open={confirmOpen}
          busy={revertBusy}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void confirmRevert()}
        />
      </div>
    );
  }

  const empty = message.content.trim().length === 0;
  const media = message.media ?? [];
  const reasoning = message.role === "assistant" ? message.reasoning ?? "" : "";
  const reasoningStreaming = !!(message.role === "assistant" && message.reasoningStreaming);
  const hasReasoning = reasoning.length > 0 || reasoningStreaming;
  const quotaLimit =
    message.role === "assistant"
    && !message.isStreaming
    && !empty
    && isQuotaLimitMessage(message.content);
  const providerCredit =
    message.role === "assistant"
    && !message.isStreaming
    && !empty
    && isProviderCreditMessage(message.content);
  // Backend fallback errors are persisted in English; localize the stable
  // sentinels at render time so the bubble follows the UI language.
  const assistantErrorSentinel =
    message.role === "assistant" && !message.isStreaming && !empty
      ? agentErrorKey(message.content)
      : null;
  const assistantErrorOverride = assistantErrorSentinel
    ? t(assistantErrorSentinel.key, { defaultValue: assistantErrorSentinel.fallback })
    : null;
  const automationSourceKind = message.source?.kind;
  const automationSourceName = message.source?.label?.trim();
  const automationSourceLabel = (
    automationSourceKind === "cron"
    || automationSourceKind === "local_trigger"
    || automationSourceKind === "trigger"
  )
    ? (automationSourceName || t("message.automationSourceFallback"))
    : "";
  const automationTriggeredLabel = t("message.automationTriggered");

  const showAssistantActions =
    message.role === "assistant" && !message.isStreaming && !empty && !quotaLimit && !providerCredit;
  const showCopyButton = showCopyAction && showAssistantActions;
  const showForkButton = showAssistantActions && !!onForkFromHere;
  const forkLabel = t("message.forkFromHere");
  const latencyMs = message.latencyMs;
  const showLatencyFooter =
    message.role === "assistant"
    && latencyMs != null
    && !message.isStreaming
    && (!empty || hasReasoning || media.length > 0);
  const showAssistantFooterRow = showCopyButton || showForkButton || showLatencyFooter;
  return (
    <div
      className={cn(
        "group/msg w-full text-[length:var(--chat-font-size)] leading-[var(--chat-line-height)]",
        baseAnim,
      )}
    >
      {hasReasoning ? (
        <ReasoningCard
          parts={[reasoning]}
          streaming={reasoningStreaming}
          hasBodyBelow={!empty}
          onOpenFilePreview={onOpenFilePreview}
        />
      ) : null}
      {empty && message.isStreaming && !hasReasoning ? (
        <TypingDots />
      ) : empty && message.isStreaming ? null : (
        <>
          {automationSourceLabel ? (
            <AutomationSourceBadge
              label={automationSourceLabel}
              triggerLabel={automationTriggeredLabel}
            />
          ) : null}
          {providerCredit ? (
            <ProviderCreditCard
              content={message.content}
              modelLabel={message.modelLabel}
              compact={superseded}
            />
          ) : quotaLimit ? (
            <QuotaLimitCard compact={superseded} />
          ) : (
            <MarkdownText
              streaming={!!message.isStreaming}
              onOpenFilePreview={onOpenFilePreview}
            >
              {assistantErrorOverride ?? message.content}
            </MarkdownText>
          )}
          {media.length > 0 ? <MessageMedia media={media} align="left" /> : null}
          {showAssistantFooterRow ? (
            <TooltipProvider delayDuration={220} skipDelayDuration={80}>
              <div
                className="mt-1 flex min-h-6 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-muted-foreground"
                data-testid="assistant-message-footer"
              >
                {showCopyButton || showForkButton ? (
                  <span
                    className={cn(
                      "flex items-center text-muted-foreground/75",
                      "opacity-0 transition-opacity group-hover/msg:opacity-100 focus-within:opacity-100",
                      "[@media(hover:none)]:opacity-100",
                    )}
                    data-testid="assistant-message-actions"
                  >
                    {showCopyButton ? (
                      <MessageCopyButton content={message.content} />
                    ) : null}
                    {showForkButton ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            onClick={onForkFromHere}
                            aria-label={forkLabel}
                            data-testid="message-fork"
                            className={MESSAGE_ACTION_CLASS}
                          >
                            <ForkArrowIcon className={MESSAGE_ACTION_ICON_CLASS} />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="top" align="center">{forkLabel}</TooltipContent>
                      </Tooltip>
                    ) : null}
                  </span>
                ) : null}
                {showLatencyFooter ? (
                  <span
                    className="text-[11px] leading-none text-muted-foreground/70 tabular-nums"
                    title={t("message.turnLatencyTitle")}
                  >
                    {formatTurnLatency(latencyMs)}
                  </span>
                ) : null}
                {showLatencyFooter && phaseTimingEntries.length > 0 ? (
                  <details className="group/phases text-[11px] leading-none text-muted-foreground/60">
                    <summary
                      className={cn(
                        "cursor-pointer select-none list-none rounded px-1 py-0.5",
                        "hover:bg-muted/45 hover:text-muted-foreground",
                        "[&::-webkit-details-marker]:hidden",
                      )}
                      title={t("message.phaseTimingsTitle")}
                    >
                      {t("message.phaseTimingsLabel")}
                    </summary>
                    <dl className="mt-1.5 grid min-w-[10rem] grid-cols-[auto_auto] gap-x-3 gap-y-1 rounded-md border border-border/40 bg-muted/25 px-2 py-1.5 tabular-nums">
                      {phaseTimingEntries.map(([phase, ms]) => (
                        <div key={phase} className="contents">
                          <dt className="text-muted-foreground/70">{phase}</dt>
                          <dd className="text-right text-muted-foreground">
                            {Math.round(ms)} ms
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </details>
                ) : null}
              </div>
            </TooltipProvider>
          ) : null}
        </>
      )}
    </div>
  );
});

function AutomationSourceBadge({ label, triggerLabel }: { label: string; triggerLabel: string }) {
  return (
    <div
      className={cn(
        "mb-2 inline-flex max-w-full items-center gap-1.5 rounded-full px-2 py-1",
        "border border-sky-500/15 bg-sky-500/[0.06]",
        "text-[11px] font-medium leading-none text-sky-700",
        "dark:border-sky-300/15 dark:bg-sky-300/[0.08] dark:text-sky-200/80",
      )}
      title={triggerLabel}
    >
      <Clock3 className="h-3 w-3 shrink-0" aria-hidden />
      <span className="min-w-0 truncate">{label}</span>
      <span className="text-current/45" aria-hidden>·</span>
      <span className="shrink-0">{triggerLabel}</span>
    </div>
  );
}

function mergeMcpMentionPresets(
  presets: McpPresetInfo[],
  attachments: UIMcpPresetAttachment[] | undefined,
): McpPresetInfo[] {
  if (!attachments?.length) return presets;
  const byName = new Map(presets.map((preset) => [preset.name.toLowerCase(), preset]));
  for (const attachment of attachments) {
    const name = attachment.name?.trim();
    if (!name) continue;
    const existing = byName.get(name.toLowerCase());
    byName.set(name.toLowerCase(), {
      name,
      display_name: attachment.display_name || existing?.display_name || name,
      category: attachment.category || existing?.category || "mcp",
      description: existing?.description || "",
      docs_url: existing?.docs_url || "",
      transport: attachment.transport || existing?.transport || "mcp",
      requires: existing?.requires || "",
      note: existing?.note || "",
      install_supported: existing?.install_supported ?? true,
      installed: true,
      configured: attachment.configured ?? existing?.configured ?? true,
      available: existing?.available ?? true,
      status: attachment.status || existing?.status || "configured",
      logo_url: attachment.logo_url ?? existing?.logo_url ?? null,
      brand_color: attachment.brand_color ?? existing?.brand_color ?? null,
      required_fields: existing?.required_fields || [],
      connection_summary: existing?.connection_summary || "",
    });
  }
  return Array.from(byName.values());
}

function mergeCliMentionApps(
  cliApps: CliAppInfo[],
  attachments: UICliAppAttachment[] | undefined,
): CliAppInfo[] {
  if (!attachments?.length) return cliApps;
  const byName = new Map(cliApps.map((app) => [app.name.toLowerCase(), app]));
  for (const attachment of attachments) {
    const name = attachment.name?.trim();
    if (!name) continue;
    const existing = byName.get(name.toLowerCase());
    byName.set(name.toLowerCase(), {
      name,
      display_name: attachment.display_name || existing?.display_name || name,
      category: attachment.category || existing?.category || "cli",
      description: existing?.description || "",
      requires: existing?.requires || "",
      source: existing?.source || "attached",
      entry_point: attachment.entry_point || existing?.entry_point || "",
      install_supported: existing?.install_supported ?? true,
      installed: true,
      available: existing?.available ?? true,
      status: existing?.status || "installed",
      logo_url: attachment.logo_url ?? existing?.logo_url ?? null,
      brand_color: attachment.brand_color ?? existing?.brand_color ?? null,
      skill_installed: existing?.skill_installed ?? true,
    });
  }
  return Array.from(byName.values());
}

function MessageMedia({
  media,
  align,
}: {
  media: UIMediaAttachment[];
  align: "left" | "right";
}) {
  if (media.length === 0) return null;
  const images: UIImage[] = [];
  const nonImages: UIMediaAttachment[] = [];
  for (const item of media) {
    const normalized = toMediaAttachment(item);
    if (normalized.kind === "image") {
      images.push({ url: normalized.url, name: normalized.name });
    } else {
      nonImages.push(normalized);
    }
  }

  return (
    <div
      className={cn(
        "mt-2 flex flex-wrap gap-2",
        align === "right" ? "justify-end" : "justify-start",
      )}
    >
      {images.length > 0 ? (
        <UserImages images={images} align={align} size={align === "left" ? "large" : "compact"} />
      ) : null}
      {nonImages.map((item, i) => (
        <AttachmentTile key={`${item.url ?? item.name ?? item.kind}-${i}`} attachment={item} />
      ))}
    </div>
  );
}

/**
 * Right-aligned preview row for images attached to a user turn.
 *
 * Visual follows agent-chat-ui: a single wrapping row of fixed-size square
 * thumbnails that stay modest next to the text pill regardless of how many
 * images are attached.
 *
 * The URL is expected to be a self-contained ``data:`` URL (the Composer
 * hands the normalized base64 payload to the optimistic bubble so that the
 * preview survives React StrictMode double-mount - blob URLs would be
 * revoked by the Composer's cleanup before remount). Historical replays
 * have no URL (the backend strips data URLs before persisting), so we
 * render a labelled placeholder tile instead of a broken ``<img>``.
 */
function UserImages({
  images,
  align = "right",
  size = "compact",
}: {
  images: UIImage[];
  align?: "left" | "right";
  size?: "compact" | "large";
}) {
  const { t } = useTranslation();
  // Only real-URL images can open in the lightbox; historical-replay
  // placeholders (no URL) have nothing to zoom into.
  const viewableImages: UIImage[] = [];
  const originalToViewable = new Map<number, number>();
  for (let i = 0; i < images.length; i += 1) {
    const img = images[i];
    if (typeof img.url !== "string" || img.url.length === 0) continue;
    originalToViewable.set(i, viewableImages.length);
    viewableImages.push(img);
  }

  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  return (
    <>
      <div
        className={cn(
          "flex flex-wrap items-end gap-2",
          size === "large" && "gap-3",
          align === "right" ? "ml-auto justify-end" : "mr-auto justify-start",
        )}
      >
        {images.map((img, i) => (
          <UserImageCell
            key={`${img.url ?? "placeholder"}-${i}`}
            image={img}
            size={size}
            placeholderLabel={t("message.imageAttachment")}
            openLabel={t("lightbox.open")}
            onOpen={
              originalToViewable.has(i)
                ? () => setLightboxIndex(originalToViewable.get(i)!)
                : undefined
            }
          />
        ))}
      </div>
      <ImageLightbox
        images={viewableImages}
        index={lightboxIndex}
        onIndexChange={setLightboxIndex}
        onOpenChange={(open) => {
          if (!open) setLightboxIndex(null);
        }}
      />
    </>
  );
}

function UserImageCell({
  image,
  size,
  placeholderLabel,
  openLabel,
  onOpen,
}: {
  image: UIImage;
  size: "compact" | "large";
  placeholderLabel: string;
  openLabel: string;
  onOpen?: () => void;
}) {
  const { t } = useTranslation();
  const [downloading, setDownloading] = useState(false);
  const hasUrl = typeof image.url === "string" && image.url.length > 0;
  const tileClasses = cn(
    "relative overflow-hidden border border-border/60 bg-muted/40",
    size === "large"
      ? "w-[min(100%,34rem)] rounded-[20px] bg-transparent"
      : "h-24 w-24 rounded-[14px]",
    "shadow-[0_6px_18px_-14px_rgba(0,0,0,0.45)]",
  );
  const downloadLabel = t("filePreview.download", { defaultValue: "Download file" });

  const handleDownload = async (event: React.MouseEvent) => {
    event.stopPropagation();
    event.preventDefault();
    if (!image.url || downloading) return;
    setDownloading(true);
    try {
      await downloadMediaAttachment(image.url, image.name);
    } finally {
      setDownloading(false);
    }
  };

  if (hasUrl && onOpen) {
    return (
      <div className={cn("group relative", size === "large" ? "w-[min(100%,34rem)]" : "h-24 w-24")}>
        <button
          type="button"
          onClick={onOpen}
          aria-label={image.name ? `${openLabel}: ${image.name}` : openLabel}
          className={cn(
            tileClasses,
            "block w-full cursor-zoom-in p-0 transition-transform duration-150 motion-reduce:transition-none",
            "hover:scale-[1.01] hover:ring-2 hover:ring-primary/25",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
          )}
        >
          <img
            src={image.url}
            alt={image.name ?? ""}
            loading="lazy"
            decoding="async"
            draggable={false}
            className={cn(
              "block",
              size === "large"
                ? "h-auto max-h-[36rem] w-full rounded-[inherit] object-contain"
                : "h-full w-full object-cover",
            )}
          />
        </button>
        <button
          type="button"
          onClick={(event) => void handleDownload(event)}
          disabled={downloading}
          title={downloadLabel}
          aria-label={image.name ? `${downloadLabel}: ${image.name}` : downloadLabel}
          data-testid="user-image-download"
          className={cn(
            "absolute right-2 top-2 z-10 inline-flex h-7 w-7 items-center justify-center rounded-full",
            "border border-border/60 bg-background/85 text-muted-foreground backdrop-blur-sm",
            "opacity-0 transition-opacity duration-150 motion-reduce:transition-none",
            "hover:text-foreground group-hover:opacity-100 group-focus-within:opacity-100",
            "[@media(hover:none)]:opacity-100",
            "focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
            "disabled:opacity-40",
          )}
        >
          {downloading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <Download className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
      </div>
    );
  }

  return (
    <div className={tileClasses} title={image.name ?? undefined}>
      <div
        className="flex h-full w-full flex-col items-center justify-center gap-1 px-2 text-[11px] text-muted-foreground"
        aria-label={placeholderLabel}
      >
        <ImageIcon className="h-4 w-4 flex-none" aria-hidden />
        <span className="line-clamp-2 text-center leading-tight">
          {image.name ?? placeholderLabel}
        </span>
      </div>
    </div>
  );
}

/** Pre-token-arrival placeholder: three bouncing dots. */
function TypingDots() {
  const { t } = useTranslation();
  return (
    <span
      aria-label={t("message.assistantTyping")}
      className="inline-flex items-center gap-1 py-1"
    >
      <Dot delay="0ms" />
      <Dot delay="150ms" />
      <Dot delay="300ms" />
    </span>
  );
}

function Dot({ delay }: { delay: string }) {
  return (
    <span
      style={{ animationDelay: delay }}
      className={cn(
        "inline-block h-1.5 w-1.5 rounded-full bg-muted-foreground/60",
        "animate-bounce",
      )}
    />
  );
}

/** L→R sheen on the glyphs themselves; inactive labels stay solid muted text. */
export function StreamingLabelSheen({
  children,
  active,
  className,
  idleClassName = "text-muted-foreground",
}: {
  children: ReactNode;
  active: boolean;
  className?: string;
  idleClassName?: string;
}) {
  const sheenText =
    typeof children === "string" || typeof children === "number"
      ? String(children)
      : undefined;
  return (
    <span className={cn("block min-w-0 overflow-hidden py-px", className)}>
      <span
        data-sheen-text={active ? sheenText : undefined}
        className={cn(
          "block w-fit max-w-full truncate font-medium leading-normal",
          active ? "streaming-text-sheen" : idleClassName,
        )}
      >
        {children}
      </span>
    </span>
  );
}

interface ReasoningBubbleProps {
  text: string;
  streaming: boolean;
  hasBodyBelow: boolean;
  /** Unused: the card no longer lives inside the tool fold. */
  embeddedInCluster?: boolean;
  onOpenFilePreview?: (path: string) => void;
}

/** Compact thinking card with a summary and click-to-expand details. */
export function ReasoningBubble({
  text,
  streaming,
  hasBodyBelow,
  onOpenFilePreview,
}: ReasoningBubbleProps) {
  return (
    <ReasoningCard
      parts={[text]}
      streaming={streaming}
      hasBodyBelow={hasBodyBelow}
      onOpenFilePreview={onOpenFilePreview}
    />
  );
}

interface TraceGroupProps {
  message: UIMessage;
  animClass: string;
}

/**
 * Collapsible group of tool-call / progress breadcrumbs. Defaults to
 * collapsed because tool traces are supporting evidence, not the answer.
 * A single click expands the exact calls when the user wants details.
 */
export function TraceGroup({ message, animClass }: TraceGroupProps) {
  const { t } = useTranslation();
  const lines = message.traces ?? [message.content];
  const count = lines.length;
  const [open, setOpen] = useState(false);
  return (
    <div className={cn("w-full", animClass)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "group flex w-full items-center gap-2 rounded-md px-2 py-1.5",
          "text-xs text-muted-foreground transition-colors hover:bg-muted/45",
        )}
        aria-expanded={open}
      >
        <Wrench className="h-3.5 w-3.5" aria-hidden />
        <span className="font-medium">
          {count === 1
            ? t("message.toolSingle")
            : t("message.toolMany", { count })}
        </span>
        <ChevronRight
          aria-hidden
          className={cn(
            "ml-auto h-3.5 w-3.5 transition-transform duration-200",
            open && "rotate-90",
          )}
        />
      </button>
      {open && (
        <ul
          className={cn(
            "mt-1 space-y-0.5 border-l border-muted-foreground/20 pl-3",
            "animate-in fade-in-0 slide-in-from-top-1 duration-200",
          )}
        >
          {lines.map((line, i) => (
            <li
              key={i}
              className="whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-muted-foreground/90"
            >
              {line}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
