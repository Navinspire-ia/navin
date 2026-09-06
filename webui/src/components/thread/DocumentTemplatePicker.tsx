import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Check,
  Eye,
  FileDown,
  FileSpreadsheet,
  FileText,
  LayoutTemplate,
  Presentation,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { LucideIcon } from "lucide-react";

import { DocumentTemplatePreviewDialog } from "@/components/thread/DocumentTemplatePreview";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { DocumentTemplateInfo, UIDocumentTemplateAttachment } from "@/lib/types";

// One tab per deliverable. PDF templates are print-first designs (certificate,
// product sheet, agreement) with no Word counterpart; Word and PPT templates
// can still be printed to PDF from the HTML the agent filled.
const CATEGORY_ORDER = ["ppt", "word", "pdf", "excel"] as const;

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  ppt: Presentation,
  word: FileText,
  pdf: FileDown,
  excel: FileSpreadsheet,
};

function categoryLabel(category: string, t: TFunction): string {
  const labels: Record<string, string> = {
    ppt: "PPT",
    word: "Word",
    pdf: "PDF",
    excel: t("thread.composer.documentTemplate.excelTab", { defaultValue: "Excel/CSV" }),
  };
  return labels[category] ?? category.toUpperCase();
}

/**
 * Composer button + gallery popover to pick an HTML document template
 * (PPT / Word / Excel) for the deliverable the agent will generate.
 * The choice rides on the next message as ``document_template``.
 */
export function DocumentTemplatePicker({
  templates,
  selected,
  onSelect,
  disabled = false,
}: {
  templates: DocumentTemplateInfo[];
  selected: DocumentTemplateInfo | null;
  onSelect: (template: DocumentTemplateInfo | null) => void;
  disabled?: boolean;
  isHero?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [previewTemplate, setPreviewTemplate] = useState<DocumentTemplateInfo | null>(null);
  // The gallery is rendered in a portal with a fixed position anchored to the
  // button: side panels (dev/content workbench) clip absolutely-positioned
  // children with overflow, and the viewport itself can be narrow. On some
  // fullscreen layouts the popover grew past the top edge and clipped the
  // category tabs - so we clamp maxHeight to the free space and flip below
  // the trigger when there is more room there.
  const [galleryRect, setGalleryRect] = useState<{
    left: number;
    width: number;
    maxHeight: number;
    top?: number;
    bottom?: number;
  } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const galleryRef = useRef<HTMLDivElement>(null);

  const byCategory = useMemo(() => {
    const map = new Map<string, DocumentTemplateInfo[]>();
    for (const template of templates) {
      const list = map.get(template.category) ?? [];
      list.push(template);
      map.set(template.category, list);
    }
    return map;
  }, [templates]);
  const categories = useMemo(
    () => CATEGORY_ORDER.filter((category) => (byCategory.get(category)?.length ?? 0) > 0),
    [byCategory],
  );
  const [activeCategory, setActiveCategory] = useState<string>(categories[0] ?? "ppt");
  useEffect(() => {
    if (categories.length > 0 && !categories.includes(activeCategory as never)) {
      setActiveCategory(categories[0]);
    }
  }, [categories, activeCategory]);

  useEffect(() => {
    if (!open) {
      setGalleryRect(null);
      return undefined;
    }
    const MARGIN = 12;
    const GAP = 10;
    const MIN_PANEL = 220;
    const update = () => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.round(Math.min(464, window.innerWidth - MARGIN * 2));
      const left = Math.round(
        Math.min(Math.max(rect.left, MARGIN), window.innerWidth - width - MARGIN),
      );
      const spaceAbove = Math.max(0, rect.top - MARGIN);
      const spaceBelow = Math.max(0, window.innerHeight - rect.bottom - MARGIN);
      // Prefer opening upward (above the composer) unless the free space there
      // is clearly tighter than below - typical when the composer sits mid-screen
      // in fullscreen and the top chrome eats the upward room.
      const openAbove = spaceAbove >= MIN_PANEL || spaceAbove >= spaceBelow;
      if (openAbove) {
        setGalleryRect({
          left,
          width,
          bottom: Math.round(window.innerHeight - rect.top + GAP),
          maxHeight: Math.round(Math.max(MIN_PANEL, spaceAbove - GAP)),
        });
      } else {
        setGalleryRect({
          left,
          width,
          top: Math.round(rect.bottom + GAP),
          maxHeight: Math.round(Math.max(MIN_PANEL, spaceBelow - GAP)),
        });
      }
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    window.visualViewport?.addEventListener("resize", update);
    window.visualViewport?.addEventListener("scroll", update);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      window.visualViewport?.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("scroll", update);
    };
  }, [open]);

  const previewOpen = previewTemplate !== null;
  useEffect(() => {
    if (!open || previewOpen) return undefined;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (containerRef.current?.contains(target)) return;
      if (galleryRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, previewOpen]);

  if (templates.length === 0) return null;

  const activeTemplates = byCategory.get(activeCategory) ?? [];

  return (
    <div ref={containerRef} className="relative">
      <Button
        type="button"
        size="icon"
        variant="ghost"
        disabled={disabled}
        aria-label={t("thread.composer.documentTemplate.button", {
          defaultValue: "Choose a document template",
        })}
        aria-expanded={open}
        title={t("thread.composer.documentTemplate.button", {
          defaultValue: "Choose a document template",
        })}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "h-7 w-7 rounded-md border-transparent text-muted-foreground hover:bg-muted/65 hover:text-foreground",
          selected && "text-primary hover:text-primary",
        )}
        data-testid="document-template-picker-button"
      >
        <LayoutTemplate className="h-3.5 w-3.5" />
      </Button>
      {open && galleryRect ? createPortal(
        <div
          ref={galleryRef}
          role="dialog"
          aria-label={t("thread.composer.documentTemplate.title", {
            defaultValue: "Document templates",
          })}
          className={cn(
            "fixed z-[70] flex flex-col overflow-hidden",
            "rounded-2xl border border-border/70 bg-popover p-3 shadow-[0_18px_46px_rgba(15,23,42,0.18)]",
          )}
          style={{
            left: galleryRect.left,
            width: galleryRect.width,
            maxHeight: galleryRect.maxHeight,
            ...(galleryRect.top != null
              ? { top: galleryRect.top }
              : { bottom: galleryRect.bottom }),
          }}
          data-testid="document-template-gallery"
        >
          <div className="mb-2 flex shrink-0 items-center justify-between gap-2 px-0.5">
            <span className="shrink-0 text-[13px] font-semibold text-foreground">
              {t("thread.composer.documentTemplate.title", {
                defaultValue: "Document templates",
              })}
            </span>
            <span className="truncate text-[11.5px] text-muted-foreground/75">
              {t("thread.composer.documentTemplate.hint", {
                defaultValue: "The agent will use this design",
              })}
            </span>
          </div>
          <div className="mb-2.5 flex shrink-0 gap-1 rounded-xl bg-muted/50 p-1">
            {categories.map((category) => {
              const Icon = CATEGORY_ICONS[category] ?? LayoutTemplate;
              const isActive = category === activeCategory;
              return (
                <button
                  key={category}
                  type="button"
                  onClick={() => setActiveCategory(category)}
                  className={cn(
                    "flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[12px] font-medium transition-colors",
                    isActive
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  aria-pressed={isActive}
                  data-testid={`document-template-tab-${category}`}
                >
                  <Icon className="h-3.5 w-3.5" aria-hidden />
                  {categoryLabel(category, t)}
                  <span className="text-[10.5px] text-muted-foreground/65">
                    {byCategory.get(category)?.length ?? 0}
                  </span>
                </button>
              );
            })}
          </div>
          {/* Header + tabs stay pinned; only the grid scrolls inside the
              viewport-clamped panel so categories remain clickable. */}
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain pr-1 scrollbar-thin scrollbar-track-transparent">
          <div className="grid auto-rows-min grid-cols-2 items-start gap-2">
            {activeTemplates.map((template) => {
              const isSelected =
                selected?.category === template.category && selected?.name === template.name;
              return (
                <div
                  key={`${template.category}/${template.name}`}
                  className={cn(
                    "group relative overflow-hidden rounded-xl border transition-colors",
                    isSelected
                      ? "border-primary ring-1 ring-primary/45"
                      : "border-border/55 hover:border-border",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => {
                      onSelect(isSelected ? null : template);
                      setOpen(false);
                    }}
                    className="block w-full text-left"
                    title={template.description || template.title}
                    data-testid={`document-template-option-${template.category}-${template.name}`}
                  >
                    {template.preview_url ? (
                      <img
                        src={template.preview_url}
                        alt=""
                        loading="lazy"
                        decoding="async"
                        className="h-28 w-full shrink-0 bg-muted object-cover object-top"
                      />
                    ) : (
                      <div className="grid h-28 w-full shrink-0 place-items-center bg-muted">
                        <LayoutTemplate className="h-6 w-6 text-muted-foreground/60" aria-hidden />
                      </div>
                    )}
                    <div className="px-2 py-1.5">
                      <div className="truncate text-[12px] font-medium text-foreground">
                        {template.title}
                      </div>
                      <div className="text-[11px] text-muted-foreground/75">
                        {template.category === "ppt"
                          ? t("thread.composer.documentTemplate.slides", {
                              count: template.item_count,
                              defaultValue: "{{count}} slides",
                            })
                          : t("thread.composer.documentTemplate.pages", {
                              count: template.item_count,
                              defaultValue: "{{count}} pages",
                            })}
                      </div>
                    </div>
                  </button>
                  {isSelected ? (
                    <span className="pointer-events-none absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded-full bg-primary text-primary-foreground shadow">
                      <Check className="h-3 w-3" strokeWidth={3} aria-hidden />
                    </span>
                  ) : null}
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      setPreviewTemplate(template);
                    }}
                    aria-label={t("thread.composer.documentTemplate.preview", {
                      defaultValue: "Preview all slides",
                    })}
                    title={t("thread.composer.documentTemplate.preview", {
                      defaultValue: "Preview all slides",
                    })}
                    className={cn(
                      "absolute left-1.5 top-1.5 grid h-6 w-6 place-items-center rounded-full",
                      "bg-black/55 text-white opacity-0 shadow backdrop-blur-sm transition-opacity",
                      "hover:bg-black/75 focus-visible:opacity-100 group-hover:opacity-100",
                    )}
                    data-testid={`document-template-preview-${template.category}-${template.name}`}
                  >
                    <Eye className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </div>
              );
            })}
          </div>
          </div>
        </div>,
        document.body,
      ) : null}
      {previewTemplate ? (
        <DocumentTemplatePreviewDialog
          template={previewTemplate}
          onClose={() => setPreviewTemplate(null)}
          onUse={(template) => {
            onSelect(template);
            setPreviewTemplate(null);
            setOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}

/** Chip shown in the composer while a template is attached to the next message. */
export function DocumentTemplateChip({
  template,
  onRemove,
}: {
  template: DocumentTemplateInfo;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const Icon = CATEGORY_ICONS[template.category] ?? LayoutTemplate;
  return (
    <div
      className="flex items-center gap-2 rounded-xl border border-border/60 bg-muted/40 py-1.5 pl-1.5 pr-2"
      data-testid="document-template-chip"
    >
      {template.preview_url ? (
        <img
          src={template.preview_url}
          alt=""
          className="h-8 w-[3.5rem] rounded-lg border border-border/50 object-cover object-top"
        />
      ) : (
        <span className="grid h-8 w-[3.5rem] place-items-center rounded-lg border border-border/50 bg-background">
          <Icon className="h-4 w-4 text-muted-foreground/70" aria-hidden />
        </span>
      )}
      <div className="min-w-0">
        <div className="truncate text-[12px] font-medium text-foreground">{template.title}</div>
        <div className="text-[10.5px] text-muted-foreground/75">
          {t("thread.composer.documentTemplate.chipKind", {
            kind: categoryLabel(template.category, t),
            defaultValue: "{{kind}} template",
          })}
        </div>
      </div>
      <button
        type="button"
        onClick={onRemove}
        aria-label={t("thread.composer.remove", { defaultValue: "Remove" })}
        className="ml-1 grid h-5 w-5 shrink-0 place-items-center rounded-full text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );
}

/** Small badge on sent user messages that carried a template. */
export function DocumentTemplateBadge({
  template,
}: {
  template: UIDocumentTemplateAttachment;
}) {
  const { t } = useTranslation();
  const Icon = CATEGORY_ICONS[template.category] ?? LayoutTemplate;
  return (
    <span
      className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border/55 bg-muted/45 px-2 py-0.5 text-[11px] text-muted-foreground"
      title={t("thread.composer.documentTemplate.chipKind", {
        kind: categoryLabel(template.category, t),
        defaultValue: "{{kind}} template",
      })}
      data-testid="document-template-badge"
    >
      <Icon className="h-3 w-3 shrink-0" aria-hidden />
      <span className="truncate">{template.title || template.name}</span>
    </span>
  );
}
