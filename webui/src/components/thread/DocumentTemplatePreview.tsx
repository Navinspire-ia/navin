// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronLeft, ChevronRight, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { DocumentTemplateInfo } from "@/lib/types";

/**
 * URL of one file inside a template folder, derived from the authenticated
 * thumbnail URL (`.../image.png?token=...`) so the API token rides along.
 */
export function templateFileUrl(
  template: DocumentTemplateInfo,
  file: string,
): string | null {
  const preview = template.preview_url;
  if (!preview) return null;
  const [path, query = ""] = preview.split("?");
  if (!path.endsWith("/image.png")) return null;
  const base = path.slice(0, -"image.png".length);
  const encoded = file.split("/").map(encodeURIComponent).join("/");
  return `${base}${encoded}${query ? `?${query}` : ""}`;
}

const DECK_WIDTH = 1920;
const DECK_HEIGHT = 1080;
// Natural content width of document templates: A4 pages for word/pdf,
// landscape sheet mockups for excel. Used to scale-to-fit the viewer.
const DOC_WIDTH: Record<string, number> = { word: 880, pdf: 880, excel: 1240 };

/**
 * Near-fullscreen navigable preview of an HTML document template: every slide
 * of a PPT deck (arrow keys / buttons) or the complete scrollable document for
 * Word / PDF / Excel templates. Covers Studio themes, filters and cards so the
 * slide/page is the only focus.
 */
export function DocumentTemplatePreviewDialog({
  template,
  onClose,
  onUse,
}: {
  template: DocumentTemplateInfo;
  onClose: () => void;
  onUse?: (template: DocumentTemplateInfo) => void;
}) {
  const { t } = useTranslation();
  const files = template.files ?? [];
  const isDeck = template.category === "ppt";
  const total = isDeck ? files.length : Math.min(files.length, 1);
  const [index, setIndex] = useState(0);
  const file = files[Math.min(index, Math.max(total - 1, 0))];
  const url = file ? templateFileUrl(template, file) : null;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
      if (!isDeck) return;
      if (event.key === "ArrowRight") setIndex((i) => Math.min(i + 1, total - 1));
      if (event.key === "ArrowLeft") setIndex((i) => Math.max(i - 1, 0));
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [isDeck, onClose, total]);

  // Lock body scroll while the overlay is open so Studio filters/themes do not
  // scroll underneath the preview.
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  // Templates are authored at a fixed width (1920 slides, ~880 A4 pages);
  // scale the iframe so the full slide/page fits the large viewer.
  const baseWidth = isDeck ? DECK_WIDTH : DOC_WIDTH[template.category] ?? 880;
  const frameWrapRef = useRef<HTMLDivElement>(null);
  const [frameBox, setFrameBox] = useState({ scale: 0, height: 0, width: 0 });
  useLayoutEffect(() => {
    const el = frameWrapRef.current;
    if (!el) return undefined;
    const update = () => {
      const width = el.clientWidth;
      const height = el.clientHeight;
      const scale = isDeck
        ? Math.min(width / DECK_WIDTH, height / DECK_HEIGHT)
        : Math.min(1, width / baseWidth, height > 0 ? height / (baseWidth * 1.414) : 1);
      setFrameBox({ scale, height, width });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [baseWidth, isDeck]);
  const scale = frameBox.scale;

  const countLabel = isDeck
    ? t("thread.composer.documentTemplate.slides", {
        count: template.item_count,
        defaultValue: "{{count}} slides",
      })
    : t("thread.composer.documentTemplate.pages", {
        count: template.item_count,
        defaultValue: "{{count}} pages",
      });

  return createPortal(
    // pointer-events-auto is required: this portal is a body child, and the
    // Radix dialog that can host the picker sets pointer-events:none on the
    // body, which would otherwise make every control here unclickable.
    // z-[100] sits above Studio preparation dialogs so themes/filters/cards
    // stay fully covered.
    <div
      className="pointer-events-auto fixed inset-0 z-[100] flex flex-col bg-black/80 p-2 sm:p-3"
      role="dialog"
      aria-modal="true"
      aria-label={template.title}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      data-testid="document-template-preview"
    >
      <div className="flex h-full min-h-0 w-full flex-1 flex-col overflow-hidden rounded-xl border border-border/50 bg-background shadow-[0_28px_80px_rgba(0,0,0,0.55)]">
        <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-3 py-2 sm:gap-3 sm:px-4">
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-semibold text-foreground sm:text-[14px]">
              {template.title}
            </div>
            <div className="truncate text-[11px] text-muted-foreground/80">
              {countLabel}
            </div>
          </div>
          {onUse ? (
            <Button
              type="button"
              size="sm"
              className="h-8 shrink-0 gap-1.5 rounded-full px-3 text-[12px]"
              onClick={() => onUse(template)}
              data-testid="document-template-preview-use"
            >
              <Check className="h-3.5 w-3.5" aria-hidden />
              {t("thread.composer.documentTemplate.useTemplate", {
                defaultValue: "Use this design",
              })}
            </Button>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close", { defaultValue: "Close" })}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
        {isDeck ? (
          <>
            <div
              ref={frameWrapRef}
              className="relative min-h-0 w-full flex-1 overflow-hidden bg-neutral-950"
            >
              {url && scale > 0 ? (
                <iframe
                  key={url}
                  src={url}
                  title={template.title}
                  className="pointer-events-none absolute border-0 bg-white shadow-2xl"
                  style={{
                    width: DECK_WIDTH,
                    height: DECK_HEIGHT,
                    left: Math.max(0, (frameBox.width - DECK_WIDTH * scale) / 2),
                    top: Math.max(0, (frameBox.height - DECK_HEIGHT * scale) / 2),
                    transform: `scale(${scale})`,
                    transformOrigin: "top left",
                  }}
                />
              ) : null}
            </div>
            <div className="flex shrink-0 items-center justify-center gap-3 border-t border-border/60 px-4 py-2">
              <button
                type="button"
                onClick={() => setIndex((i) => Math.max(i - 1, 0))}
                disabled={index <= 0}
                aria-label={t("thread.composer.documentTemplate.previousSlide", {
                  defaultValue: "Previous slide",
                })}
                className={cn(
                  "grid h-8 w-8 place-items-center rounded-full border border-border/60 text-foreground transition-colors",
                  index <= 0
                    ? "opacity-35"
                    : "hover:bg-muted",
                )}
                data-testid="document-template-preview-prev"
              >
                <ChevronLeft className="h-4 w-4" aria-hidden />
              </button>
              <span className="min-w-[4.5rem] text-center text-[12.5px] tabular-nums text-muted-foreground">
                {index + 1} / {total}
              </span>
              <button
                type="button"
                onClick={() => setIndex((i) => Math.min(i + 1, total - 1))}
                disabled={index >= total - 1}
                aria-label={t("thread.composer.documentTemplate.nextSlide", {
                  defaultValue: "Next slide",
                })}
                className={cn(
                  "grid h-8 w-8 place-items-center rounded-full border border-border/60 text-foreground transition-colors",
                  index >= total - 1
                    ? "opacity-35"
                    : "hover:bg-muted",
                )}
                data-testid="document-template-preview-next"
              >
                <ChevronRight className="h-4 w-4" aria-hidden />
              </button>
            </div>
          </>
        ) : (
          <div
            ref={frameWrapRef}
            className="relative min-h-0 w-full flex-1 overflow-hidden bg-muted/30"
          >
            {url && scale > 0 ? (
              <iframe
                key={url}
                src={url}
                title={template.title}
                className="absolute left-1/2 top-0 border-0 bg-white shadow-xl"
                style={{
                  width: baseWidth,
                  height: frameBox.height / Math.max(scale, 0.01),
                  transform: `translateX(-50%) scale(${scale})`,
                  transformOrigin: "top center",
                }}
              />
            ) : null}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
