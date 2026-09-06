import type { CSSProperties, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

import { StreamingLabelSheen } from "@/components/MessageBubble";
import { cn } from "@/lib/utils";

export type ActivityStepTone = "neutral" | "active" | "success" | "error";

export interface ActivityStepProps {
  as?: "div" | "li";
  icon?: LucideIcon;
  marker?: ReactNode;
  label: ReactNode;
  detail?: ReactNode;
  aside?: ReactNode;
  children?: ReactNode;
  active?: boolean;
  tone?: ActivityStepTone;
  title?: string;
  className?: string;
  contentClassName?: string;
  markerClassName?: string;
  labelClassName?: string;
  style?: CSSProperties;
}

export function ActivityStep({
  as: Component = "div",
  icon: Icon,
  marker,
  label,
  detail,
  aside,
  children,
  active = false,
  tone = active ? "active" : "neutral",
  title,
  className,
  contentClassName,
  markerClassName,
  labelClassName,
  style,
}: ActivityStepProps) {
  return (
    <Component
      className={cn(
        "group/activity-step relative grid min-w-0 grid-cols-[1.125rem_minmax(0,1fr)] gap-2 py-0.5 text-[12px] leading-[1.5]",
        className,
      )}
      title={title}
      style={style}
    >
      <span
        className={cn(
          "relative flex h-4 w-[1.125rem] shrink-0 items-start justify-center pt-px",
          "after:absolute after:left-1/2 after:top-4 after:h-[calc(100%+0.375rem)] after:w-px after:-translate-x-1/2 after:bg-muted-foreground/14 group-last/activity-step:after:hidden",
        )}
        aria-hidden
      >
        {marker ?? (
          <span
            className={cn(
              "grid h-3 w-3 place-items-center rounded-full border bg-background transition-colors",
              tone === "active" && "border-muted-foreground/28 text-muted-foreground/72",
              tone === "success" && "border-emerald-500/28 text-emerald-500/78",
              // Internal step hiccups are part of the agent's normal
              // self-correction loop: keep them discreet, never alarming red.
              tone === "error" && "border-muted-foreground/28 text-muted-foreground/60",
              tone === "neutral" && "border-muted-foreground/18 text-muted-foreground/50",
              markerClassName,
            )}
          >
            {Icon ? <Icon className="h-2 w-2" strokeWidth={2.15} /> : null}
          </span>
        )}
      </span>
      <div className={cn("min-w-0", contentClassName)}>
        <div className="flex min-w-0 items-baseline gap-1.5">
          <StreamingLabelSheen
            active={active}
            className="min-w-0 shrink-0 font-medium"
            idleClassName={cn(
              tone === "error" ? "text-muted-foreground/70" : "text-muted-foreground/85",
              labelClassName,
            )}
          >
            {label}
          </StreamingLabelSheen>
          {detail ? (
            <span className="min-w-0 break-words text-foreground/82">
              {detail}
            </span>
          ) : null}
          {aside ? <span className="ml-auto shrink-0">{aside}</span> : null}
        </div>
        {children ? <div className="mt-1 min-w-0">{children}</div> : null}
      </div>
    </Component>
  );
}
