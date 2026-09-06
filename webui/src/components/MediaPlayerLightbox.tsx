import { useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Download, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { downloadMediaAttachment } from "@/lib/media";
import { cn } from "@/lib/utils";

interface MediaPlayerLightboxProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  kind: "video" | "audio";
  url: string;
  name?: string;
}

/**
 * Fullscreen preview for video / audio attachments (mirrors ImageLightbox).
 */
export function MediaPlayerLightbox({
  open,
  onOpenChange,
  kind,
  url,
  name,
}: MediaPlayerLightboxProps) {
  const { t } = useTranslation();
  const [downloading, setDownloading] = useState(false);
  const title =
    name ??
    (kind === "video"
      ? t("message.videoAttachment", { defaultValue: "Video attachment" })
      : t("message.audioAttachment", { defaultValue: "Audio attachment" }));
  const downloadLabel = t("filePreview.download", { defaultValue: "Download file" });

  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (downloading) return;
    setDownloading(true);
    try {
      await downloadMediaAttachment(url, name);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-black/80 backdrop-blur-sm",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "motion-reduce:data-[state=open]:animate-none motion-reduce:data-[state=closed]:animate-none",
          )}
        />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          aria-label={title}
          className={cn(
            "fixed inset-0 z-50 flex items-center justify-center p-4",
            "focus:outline-none",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
            "motion-reduce:data-[state=open]:animate-none motion-reduce:data-[state=closed]:animate-none",
          )}
        >
          <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>

          <div className="relative flex w-full max-w-[min(94vw,56rem)] flex-col items-center gap-3">
            {kind === "video" ? (
              <video
                key={url}
                src={url}
                controls
                autoPlay
                playsInline
                className="max-h-[82vh] w-full rounded-[6px] bg-black object-contain shadow-2xl"
                aria-label={title}
              >
                <track kind="captions" />
              </video>
            ) : (
              <div className="flex w-full max-w-lg flex-col items-center gap-3 rounded-[14px] border border-white/10 bg-black/40 px-6 py-8">
                <p className="max-w-full truncate text-sm text-white/80">{title}</p>
                <audio key={url} src={url} controls autoPlay className="w-full" aria-label={title}>
                  <track kind="captions" />
                </audio>
              </div>
            )}
          </div>

          <div className="absolute right-4 top-4 flex items-center gap-2">
            <button
              type="button"
              onClick={(e) => void handleDownload(e)}
              disabled={downloading}
              aria-label={name ? `${downloadLabel}: ${name}` : downloadLabel}
              title={downloadLabel}
              data-testid="media-player-download"
              className={cn(
                "grid h-9 w-9 place-items-center rounded-full",
                "bg-black/55 text-white/90 hover:bg-black/70 hover:text-white",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70",
                "transition-colors motion-reduce:transition-none",
                "disabled:opacity-40",
              )}
            >
              {downloading ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Download className="h-4 w-4" aria-hidden />
              )}
            </button>
            <DialogPrimitive.Close
              aria-label={t("lightbox.close", { defaultValue: "Close" })}
              className={cn(
                "grid h-9 w-9 place-items-center rounded-full",
                "bg-black/55 text-white/90 hover:bg-black/70 hover:text-white",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70",
                "transition-colors motion-reduce:transition-none",
              )}
            >
              <X className="h-4 w-4" aria-hidden />
            </DialogPrimitive.Close>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
