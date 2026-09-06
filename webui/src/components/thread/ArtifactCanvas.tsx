import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { FileCode2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { MarkdownText } from "@/components/MarkdownText";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import type { ArtifactRecord } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface ArtifactCanvasProps {
  artifacts: ArtifactRecord[];
  selectedId: string | null;
  desktopWidth?: number;
  isClosing?: boolean;
  /**
   * Fill the workbench area instead of sliding in beside the conversation.
   *
   * A rendered result belongs where every other result already goes - the
   * centre - not in the chat column, where it competed with the messages for
   * width and won.
   */
  fill?: boolean;
  onSelect: (id: string) => void;
  onClose: () => void;
  onResizeStart?: (event: ReactPointerEvent<HTMLButtonElement>) => void;
}

function normalizeType(type: string | undefined): string {
  const raw = (type || "").trim().toLowerCase();
  if (raw === "md") return "markdown";
  return raw;
}

function ArtifactBody({ artifact }: { artifact: ArtifactRecord }) {
  const { t } = useTranslation();
  const kind = normalizeType(artifact.type);
  const content = artifact.content ?? "";

  if (kind === "html") {
    return (
      <iframe
        title={artifact.title || "HTML artifact"}
        sandbox=""
        srcDoc={content}
        className="h-full w-full border-0 bg-white"
        data-testid="artifact-canvas-html"
      />
    );
  }

  if (kind === "markdown") {
    return (
      <div
        className="h-full overflow-auto px-4 py-3"
        data-testid="artifact-canvas-markdown"
      >
        <MarkdownText>{content}</MarkdownText>
      </div>
    );
  }

  if (kind === "mermaid") {
    return (
      <div
        className="flex h-full flex-col gap-3 overflow-auto p-4"
        data-testid="artifact-canvas-mermaid"
      >
        <p className="text-xs text-muted-foreground">
          {t("artifactCanvas.mermaidPlaceholder", {
            defaultValue: "Mermaid source (live rendering not available in this canvas).",
          })}
        </p>
        <pre className="overflow-auto rounded-md border border-border/60 bg-muted/30 p-3 text-[12px] leading-5 text-foreground">
          {content}
        </pre>
      </div>
    );
  }

  return (
    <div
      className="flex h-full flex-col gap-3 overflow-auto p-4"
      data-testid="artifact-canvas-file"
    >
      <p className="text-sm text-muted-foreground">
        {t("artifactCanvas.filePlaceholder", {
          defaultValue: "File artifact stored on disk.",
        })}
      </p>
      {artifact.source_path ? (
        <p className="break-all font-mono text-xs text-foreground/80">
          {artifact.source_path}
        </p>
      ) : null}
      <pre className="overflow-auto rounded-md border border-border/60 bg-muted/30 p-3 text-[12px] leading-5 text-foreground">
        {content || t("artifactCanvas.empty", { defaultValue: "(empty)" })}
      </pre>
    </div>
  );
}

export function ArtifactCanvas({
  artifacts,
  selectedId,
  desktopWidth = 520,
  isClosing = false,
  fill = false,
  onSelect,
  onClose,
  onResizeStart,
}: ArtifactCanvasProps) {
  const { t } = useTranslation();
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const selected = useMemo(() => {
    if (!artifacts.length) return null;
    return (
      artifacts.find((item) => item.id === selectedId) ?? artifacts[artifacts.length - 1] ?? null
    );
  }, [artifacts, selectedId]);

  if (!artifacts.length || !selected) return null;

  const panel = (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        className={cn(
          "flex h-11 shrink-0 items-center gap-2 border-b border-border/60 pl-3",
          NOTIFICATION_GUTTER,
        )}
      >
        <FileCode2 className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {artifacts.map((item) => {
            const active = item.id === selected.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onSelect(item.id)}
                className={cn(
                  "max-w-[10rem] shrink-0 truncate rounded-md px-2 py-1 text-[12px] font-medium transition-colors",
                  active
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
                title={item.title}
                data-testid="artifact-canvas-tab"
              >
                {item.title || item.type}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          onClick={onClose}
          className={cn(
            "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
            "text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
          title={t("artifactCanvas.close", { defaultValue: "Close artifact canvas" })}
          aria-label={t("artifactCanvas.close", { defaultValue: "Close artifact canvas" })}
          data-testid="artifact-canvas-close"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        <ArtifactBody artifact={selected} />
      </div>
    </div>
  );

  if (fill) {
    return (
      <section
        aria-label={t("artifactCanvas.aria", { defaultValue: "Artifact canvas" })}
        className={cn(
          "flex h-full w-full flex-col overflow-hidden bg-background",
          "transition-opacity duration-200 ease-out",
          !entered || isClosing ? "opacity-0" : "opacity-100",
        )}
        data-testid="artifact-canvas"
        data-artifact-canvas-panel
        data-artifact-canvas-fill
      >
        {panel}
      </section>
    );
  }

  return (
    <aside
      aria-label={t("artifactCanvas.aria", { defaultValue: "Artifact canvas" })}
      style={{
        "--artifact-canvas-width": `${desktopWidth}px`,
        "--artifact-canvas-slot-width": !entered || isClosing ? "0px" : `${desktopWidth}px`,
      } as CSSProperties}
      className={cn(
        "absolute inset-y-0 right-0 z-30 w-[min(100vw,var(--artifact-canvas-slot-width))] overflow-hidden",
        "transition-[width] duration-300 ease-out will-change-[width]",
        "md:relative md:z-auto md:w-[var(--artifact-canvas-slot-width)] md:min-w-0 md:shrink-0",
        isClosing && "pointer-events-none",
      )}
      data-testid="artifact-canvas"
      data-artifact-canvas-panel
    >
      <div
        className={cn(
          "absolute inset-y-0 right-0 flex w-[min(100vw,var(--artifact-canvas-width))] flex-col overflow-hidden pb-[env(safe-area-inset-bottom)] md:w-[var(--artifact-canvas-width)] md:pb-0",
          "border-l border-border/70 bg-background shadow-2xl md:shadow-none",
          "transition-[opacity,transform] duration-300 ease-out will-change-transform",
          !entered || isClosing ? "translate-x-full opacity-0" : "translate-x-0 opacity-100",
          "motion-reduce:translate-x-0",
        )}
      >
        {onResizeStart ? (
          <button
            type="button"
            aria-label={t("artifactCanvas.resize", { defaultValue: "Resize artifact canvas" })}
            className={cn(
              "group absolute inset-y-0 left-0 z-20 hidden w-3 -translate-x-1/2 cursor-col-resize touch-none md:flex",
              "items-stretch justify-center focus-visible:outline-none",
            )}
            onPointerDown={onResizeStart}
          >
            <span
              aria-hidden
              className={cn(
                "h-full w-px bg-foreground/25 opacity-0 transition-opacity",
                "group-hover:opacity-100 group-focus-visible:bg-ring group-focus-visible:opacity-100",
              )}
            />
          </button>
        ) : null}

        {panel}
      </div>
    </aside>
  );
}
