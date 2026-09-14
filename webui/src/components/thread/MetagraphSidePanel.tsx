import { useEffect, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { DevMetagraph } from "@/components/dev/DevMetagraph";

const PANEL_WIDTH = 520;

/**
 * Chat-side graph panel. The user opens it from Graph. It never opens itself.
 */
export function MetagraphSidePanel({
  sessionKey,
  isClosing,
  onClose,
}: {
  sessionKey: string | null;
  isClosing?: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <aside
      aria-label={t("metagraphPanel.aria", { defaultValue: "Project graph" })}
      style={{
        "--metagraph-panel-width": `${PANEL_WIDTH}px`,
        "--metagraph-panel-slot-width": !entered || isClosing ? "0px" : `${PANEL_WIDTH}px`,
      } as CSSProperties}
      className={cn(
        "absolute inset-y-0 right-0 z-30 w-[min(100vw,var(--metagraph-panel-slot-width))] overflow-hidden",
        "transition-[width] duration-300 ease-out will-change-[width]",
        "md:relative md:z-auto md:w-[var(--metagraph-panel-slot-width)] md:min-w-0 md:shrink-0",
        isClosing && "pointer-events-none",
      )}
      data-testid="metagraph-side-panel"
    >
      <div
        className={cn(
          "absolute inset-y-0 right-0 flex w-[min(100vw,var(--metagraph-panel-width))] flex-col overflow-hidden pb-[env(safe-area-inset-bottom)] md:w-[var(--metagraph-panel-width)] md:pb-0",
          "border-l border-border/70 bg-background shadow-2xl md:shadow-none",
          "transition-[opacity,transform] duration-300 ease-out will-change-transform",
          !entered || isClosing ? "translate-x-full opacity-0" : "translate-x-0 opacity-100",
          "motion-reduce:translate-x-0",
        )}
      >
        <div className="flex items-center justify-between border-b border-border/60 px-3 py-2">
          <span className="text-sm font-medium text-foreground/90">
            {t("metagraphPanel.title", { defaultValue: "Project graph" })}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            {t("metagraphPanel.close", { defaultValue: "Close" })}
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          <DevMetagraph sessionKey={sessionKey} compact />
        </div>
      </div>
    </aside>
  );
}