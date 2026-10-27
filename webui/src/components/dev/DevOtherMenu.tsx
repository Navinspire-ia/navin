// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Fragment, type ReactNode } from "react";
import { MoreHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
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
  /** Small pill after the label (e.g. NEW). */
  badge?: string;
  /** When set, the item opens a sub menu listing these entries. */
  children?: DevOtherMenuItem[];
  /** Muted second line under the label. */
  hint?: string;
};

function ItemBody({ item }: { item: DevOtherMenuItem }) {
  return (
    <>
      {item.icon}
      <span className="min-w-0 flex-1">
        <span className="block truncate">{item.label}</span>
        {item.hint ? (
          <span className="block truncate text-xs text-muted-foreground">{item.hint}</span>
        ) : null}
      </span>
      {item.badge ? (
        <span className="ms-auto rounded-full bg-amber-400/80 px-1.5 text-[9px] font-semibold leading-4 text-black">
          {item.badge}
        </span>
      ) : null}
    </>
  );
}

/**
 * Overflow for Code modules off the main rail/toolbar: Debug / Guardrails /
 * Graph / Tasks, then Processes, Project, Extensions. Items with children
 * open a sub menu (Mobile apps / Desktop apps).
 */
export function DevOtherMenu({
  triggerClassName,
  showTriggerLabel,
  items,
}: {
  triggerClassName: string;
  showTriggerLabel: boolean;
  items: DevOtherMenuItem[];
}) {
  const { t } = useTranslation();
  const label = t("dev.other", { defaultValue: "Other" });
  const anyActive = items.some((item) => item.active);

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
        {items.map((item) => (
          <Fragment key={item.key}>
            {item.separatorBefore ? <DropdownMenuSeparator /> : null}
            {item.children ? (
              <DropdownMenuSub>
                <DropdownMenuSubTrigger data-testid={`dev-other-${item.key}`}>
                  <ItemBody item={item} />
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent className="w-56">
                  {item.children.map((child) => (
                    <Fragment key={child.key}>
                      {child.separatorBefore ? <DropdownMenuSeparator /> : null}
                      <DropdownMenuItem
                        onSelect={child.onSelect}
                        data-testid={`dev-other-${item.key}-${child.key}`}
                      >
                        <ItemBody item={child} />
                      </DropdownMenuItem>
                    </Fragment>
                  ))}
                </DropdownMenuSubContent>
              </DropdownMenuSub>
            ) : (
              <DropdownMenuItem
                onSelect={item.onSelect}
                data-testid={`dev-other-${item.key}`}
                className={cn(item.active && "bg-muted text-foreground")}
              >
                <ItemBody item={item} />
              </DropdownMenuItem>
            )}
          </Fragment>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
