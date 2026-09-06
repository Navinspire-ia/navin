import { Fragment, type ReactNode } from "react";
import { MoreHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";

import { DevOpsActions } from "@/components/dev/DevOpsActions";
import { DevProjectActions } from "@/components/dev/DevProjectActions";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export type DevOtherMenuItem = {
  key: string;
  icon: ReactNode;
  label: string;
  active?: boolean;
  onSelect: () => void;
  separatorBefore?: boolean;
};

/**
 * Overflow for Code modules off the main rail/toolbar: Debug / Guardrails /
 * Graph / Tasks, then Ops, Actions, Tests, Processes, Project, Extensions.
 */
export function DevOtherMenu({
  triggerClassName,
  showTriggerLabel,
  items,
  onSeed,
  onRun,
  activeFilePath = null,
}: {
  triggerClassName: string;
  showTriggerLabel: boolean;
  items: DevOtherMenuItem[];
  onSeed?: (text: string) => void;
  onRun?: (text: string) => void;
  activeFilePath?: string | null;
}) {
  const { t } = useTranslation();
  const label = t("dev.other", { defaultValue: "Other" });
  const anyActive = items.some((item) => item.active);
  const hasOpsOrActions = Boolean(onSeed || onRun);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(triggerClassName, anyActive && "text-foreground")}
          title={label}
          aria-label={label}
          data-testid="dev-other-menu"
        >
          <MoreHorizontal className="h-4 w-4 shrink-0" aria-hidden />
          {showTriggerLabel ? <span className="truncate">{label}</span> : null}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="bottom"
        sideOffset={6}
        className="w-52"
        style={{ maxHeight: "min(28rem, calc(100dvh - 5.5rem))" }}
        data-testid="dev-other-menu-content"
      >
        {onSeed ? <DevOpsActions asSubmenu onSeed={onSeed} /> : null}
        {onRun ? (
          <DevProjectActions
            asSubmenu
            onRun={onRun}
            activeFilePath={activeFilePath}
          />
        ) : null}
        {hasOpsOrActions && items.length > 0 ? <DropdownMenuSeparator /> : null}
        {items.map((item) => (
          <Fragment key={item.key}>
            {item.separatorBefore ? <DropdownMenuSeparator /> : null}
            <DropdownMenuItem
              onSelect={item.onSelect}
              data-testid={`dev-other-${item.key}`}
              className={cn(item.active && "bg-muted text-foreground")}
            >
              {item.icon}
              <span className="truncate">{item.label}</span>
            </DropdownMenuItem>
          </Fragment>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
