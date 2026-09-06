import { useState, type ReactNode } from "react";
import { Download, Eye, FileIcon, ImageIcon, ImagePlus, Loader2, Music2, PlaySquare } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ImageLightbox } from "@/components/ImageLightbox";
import { MediaPlayerLightbox } from "@/components/MediaPlayerLightbox";
import { downloadMediaAttachment } from "@/lib/media";
import { canReuseMedia, requestMediaReuse } from "@/lib/media-reuse";
import { publishNotification } from "@/lib/notification-bus";
import { cn } from "@/lib/utils";
import type { UIMediaAttachment } from "@/lib/types";

interface AttachmentTileProps {
  attachment: UIMediaAttachment;
  className?: string;
  inline?: boolean;
  variant?: "default" | "compact";
}

export function AttachmentTile({ attachment, className, inline = false, variant = "default" }: AttachmentTileProps) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const hasUrl = typeof attachment.url === "string" && attachment.url.length > 0;
  const label = attachmentLabel(attachment, t);

  if (attachment.kind === "image" && hasUrl && !failed) {
    return (
      <>
        <AttachmentFrame
          attachment={attachment}
          className={className}
          inline={inline}
          variant={variant}
          actions={
            <MediaActionBar
              attachment={attachment}
              onView={() => setLightboxOpen(true)}
              showReuse
            />
          }
        >
          <button
            type="button"
            onClick={() => setLightboxOpen(true)}
            className="block w-full cursor-zoom-in bg-muted/20 p-0 text-left"
            aria-label={
              attachment.name
                ? `${t("lightbox.open", { defaultValue: "Open image" })}: ${attachment.name}`
                : t("lightbox.open", { defaultValue: "Open image" })
            }
          >
            <img
              src={attachment.url}
              alt={attachment.name ?? ""}
              loading="lazy"
              decoding="async"
              draggable={false}
              onError={() => setFailed(true)}
              className={cn(
                "block h-auto max-w-full bg-background object-contain",
                variant === "compact" ? "max-h-40" : "max-h-[34rem]",
              )}
            />
          </button>
        </AttachmentFrame>
        <ImageLightbox
          images={[{ url: attachment.url, name: attachment.name }]}
          index={lightboxOpen ? 0 : null}
          onIndexChange={() => undefined}
          onOpenChange={(open) => {
            if (!open) setLightboxOpen(false);
          }}
        />
      </>
    );
  }

  if (attachment.kind === "video" && hasUrl) {
    const videoUrl = attachment.url as string;
    return (
      <>
        <AttachmentFrame
          attachment={attachment}
          className={className}
          inline={inline}
          variant={variant}
          actions={
            <MediaActionBar
              attachment={attachment}
              onView={() => setLightboxOpen(true)}
            />
          }
        >
          <video
            src={videoUrl}
            controls
            preload="metadata"
            className={cn(
              "block w-full bg-black",
              variant === "compact" ? "max-h-40" : "max-h-[26rem]",
            )}
            aria-label={
              attachment.name
                ? `${t("message.videoAttachment", { defaultValue: "Video attachment" })}: ${attachment.name}`
                : t("message.videoAttachment", { defaultValue: "Video attachment" })
            }
          >
            <track kind="captions" />
          </video>
        </AttachmentFrame>
        <MediaPlayerLightbox
          open={lightboxOpen}
          onOpenChange={setLightboxOpen}
          kind="video"
          url={videoUrl}
          name={attachment.name}
        />
      </>
    );
  }

  if (attachment.kind === "audio" && hasUrl) {
    const audioUrl = attachment.url as string;
    return (
      <>
        <AttachmentFrame
          attachment={attachment}
          className={className}
          inline={inline}
          variant={variant}
          actions={
            <MediaActionBar
              attachment={attachment}
              onView={() => setLightboxOpen(true)}
            />
          }
        >
          <div
            className={cn(
              "flex flex-col gap-2 bg-muted/25 px-3 py-3",
              variant === "compact" ? "min-w-[14rem]" : "min-w-[18rem]",
            )}
          >
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Music2 className="h-3.5 w-3.5 flex-none" aria-hidden />
              <span className="min-w-0 truncate">{attachment.name ?? label}</span>
            </div>
            <audio
              src={audioUrl}
              controls
              preload="metadata"
              className="w-full max-w-full"
              aria-label={
                attachment.name
                  ? `${t("message.audioAttachment", { defaultValue: "Audio attachment" })}: ${attachment.name}`
                  : t("message.audioAttachment", { defaultValue: "Audio attachment" })
              }
            >
              <track kind="captions" />
            </audio>
          </div>
        </AttachmentFrame>
        <MediaPlayerLightbox
          open={lightboxOpen}
          onOpenChange={setLightboxOpen}
          kind="audio"
          url={audioUrl}
          name={attachment.name}
        />
      </>
    );
  }

  const Icon =
    attachment.kind === "video"
      ? PlaySquare
      : attachment.kind === "audio"
        ? Music2
        : attachment.kind === "image"
          ? ImageIcon
          : FileIcon;
  const body = (
    <>
      <Icon className="h-4 w-4 flex-none" aria-hidden />
      <span className="min-w-0 truncate">{attachment.name ?? label}</span>
    </>
  );

  if (hasUrl && !failed) {
    return (
      <div
        className={cn(
          "group relative flex max-w-[18rem] items-center gap-2 rounded-[14px]",
          "border border-border/60 bg-muted/40 px-3 py-2 text-xs text-muted-foreground",
          variant === "compact" && "max-w-[14rem] rounded-xl px-2.5 py-1.5 text-[11.5px]",
          className,
        )}
      >
        <a
          href={attachment.url}
          target="_blank"
          rel="noreferrer noopener"
          title={attachment.name ?? undefined}
          aria-label={label}
          className="flex min-w-0 flex-1 items-center gap-2 transition-colors hover:text-foreground"
        >
          {body}
        </a>
        <MediaActionBar attachment={attachment} alwaysVisible />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex max-w-[18rem] items-center gap-2 rounded-[14px]",
        "border border-border/60 bg-muted/35 px-3 py-2 text-xs text-muted-foreground",
        variant === "compact" && "max-w-[14rem] rounded-xl px-2.5 py-1.5 text-[11.5px]",
        className,
      )}
      title={attachment.name ?? undefined}
      aria-label={label}
    >
      {body}
      <span className="sr-only">
        {t("message.attachmentUnavailable", { defaultValue: "Attachment unavailable" })}
      </span>
    </div>
  );
}

function AttachmentFrame({
  attachment,
  children,
  className,
  inline = false,
  variant = "default",
  actions,
}: {
  attachment: UIMediaAttachment;
  children: ReactNode;
  className?: string;
  inline?: boolean;
  variant?: "default" | "compact";
  actions?: ReactNode;
}) {
  const frameClassName = cn(
    "not-prose group relative my-3 block w-fit max-w-full overflow-hidden rounded-[14px]",
    "border border-border/60 bg-muted/40",
    attachment.kind === "image" && "bg-background/85",
    (attachment.kind === "video" || attachment.kind === "audio") && "w-[min(100%,32rem)]",
    variant === "compact" && "my-1 rounded-xl shadow-none",
    variant === "compact" &&
      (attachment.kind === "video" || attachment.kind === "audio") &&
      "w-[min(100%,20rem)]",
    className,
  );
  const bodyClassName = "block max-w-full";
  const body = inline ? (
    <span className={bodyClassName}>{children}</span>
  ) : (
    <div className={bodyClassName}>{children}</div>
  );
  return inline ? (
    <span className={frameClassName}>
      {body}
      {actions}
    </span>
  ) : (
    <figure className={frameClassName}>
      {body}
      {actions}
    </figure>
  );
}

function MediaActionBar({
  attachment,
  onView,
  showReuse = false,
  alwaysVisible = false,
}: {
  attachment: UIMediaAttachment;
  onView?: () => void;
  showReuse?: boolean;
  alwaysVisible?: boolean;
}) {
  const { t } = useTranslation();
  const [downloading, setDownloading] = useState(false);
  const url = attachment.url;
  if (!url) return null;

  const viewLabel = t("filePreview.open", { defaultValue: "Preview" });
  const downloadLabel = t("filePreview.download", { defaultValue: "Download file" });
  const reuseLabel = t("message.reuseMedia", { defaultValue: "Attach to next message" });
  const canReuse = showReuse && canReuseMedia(attachment);

  const handleDownload = async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      await downloadMediaAttachment(url, attachment.name);
    } finally {
      setDownloading(false);
    }
  };

  const onReuse = () => {
    if (requestMediaReuse({ url, name: attachment.name })) return;
    publishNotification({
      level: "info",
      source: "session",
      title: reuseLabel,
      detail: t("message.reuseMediaNoComposer", {
        defaultValue: "Open a chat to attach this image to a message.",
      }),
    });
  };

  return (
    <div
      className={cn(
        "absolute right-2 top-2 z-10 inline-flex items-center gap-0.5 rounded-full",
        "border border-border/60 bg-background/85 p-0.5 text-muted-foreground backdrop-blur-sm",
        "opacity-0 transition-opacity duration-150 motion-reduce:transition-none",
        "group-hover:opacity-100 group-focus-within:opacity-100",
        "[@media(hover:none)]:opacity-100",
        "focus-within:opacity-100",
        alwaysVisible && "static opacity-100",
      )}
      data-testid="media-attachment-actions"
    >
      {onView ? (
        <ActionIconButton
          label={attachment.name ? `${viewLabel}: ${attachment.name}` : viewLabel}
          onClick={onView}
          testId="media-attachment-view"
        >
          <Eye className="h-3.5 w-3.5" aria-hidden />
        </ActionIconButton>
      ) : null}
      <ActionIconButton
        label={attachment.name ? `${downloadLabel}: ${attachment.name}` : downloadLabel}
        onClick={() => void handleDownload()}
        disabled={downloading}
        testId="media-attachment-download"
      >
        {downloading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <Download className="h-3.5 w-3.5" aria-hidden />
        )}
      </ActionIconButton>
      {canReuse ? (
        <ActionIconButton
          label={attachment.name ? `${reuseLabel}: ${attachment.name}` : reuseLabel}
          onClick={onReuse}
          testId="media-attachment-reuse"
        >
          <ImagePlus className="h-3.5 w-3.5" aria-hidden />
        </ActionIconButton>
      ) : null}
    </div>
  );
}

function ActionIconButton({
  label,
  onClick,
  disabled,
  children,
  testId,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      data-testid={testId}
      className={cn(
        "inline-flex h-7 w-7 items-center justify-center rounded-full",
        "text-muted-foreground transition-colors hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
        "disabled:opacity-40",
      )}
    >
      {children}
    </button>
  );
}

function attachmentLabel(attachment: UIMediaAttachment, t: ReturnType<typeof useTranslation>["t"]): string {
  if (attachment.kind === "video") {
    return t("message.videoAttachment", { defaultValue: "Video attachment" });
  }
  if (attachment.kind === "audio") {
    return t("message.audioAttachment", { defaultValue: "Audio attachment" });
  }
  if (attachment.kind === "image") {
    return t("message.imageAttachment", { defaultValue: "Image attachment" });
  }
  return t("message.fileAttachment", { defaultValue: "File attachment" });
}
