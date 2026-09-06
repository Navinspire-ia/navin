import { useCallback, useState } from "react";
import {
  Activity,
  Boxes,
  ChevronDown,
  Cloud,
  Server,
  ServerCog,
  Workflow,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { opsGroups, opsSeedText } from "@/components/studio/opsCatalog";
import { cn } from "@/lib/utils";

const GROUP_ICONS: Record<string, typeof ServerCog> = {
  containers: Boxes,
  delivery: Workflow,
  cloud: Cloud,
  incidents: Activity,
  servers: Server,
};

/**
 * "Ops" menu of the Dev workbench navbar, next to "Actions".
 *
 * Exposes the full ops catalog (Kubernetes, CI/CD, cloud, incidents, servers)
 * inside the code workbench, so the agent runs them with every code tool at
 * hand. Selecting an action does not send anything: it seeds the chat
 * composer with the instruction and lets the user complete it before sending.
 */
export function DevOpsActions({
  onSeed,
  compact,
  disabled,
  asSubmenu = false,
}: {
  /** Puts text in the chat composer without sending it. */
  onSeed: (text: string) => void;
  compact?: boolean;
  disabled?: boolean;
  /** Nested under the Code "Other" overflow instead of a top-level trigger. */
  asSubmenu?: boolean;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const [open, setOpen] = useState(false);
  const groups = opsGroups(tx);
  const opsLabel = tx("sidebar.opsStudio", "Ops");
  const catalogClass =
    "max-h-[min(34rem,calc(100vh-7rem))] w-[min(20rem,calc(100vw-2rem))] overflow-y-auto rounded-lg border-border bg-background p-1";
  const catalog = (
    <>
      <p className="px-2 pb-1 pt-0.5 text-[11px] leading-snug text-muted-foreground">
        {tx(
          "dev.ops.hint",
          "Picking an action puts the instruction in the chat - add your context, then send.",
        )}
      </p>
      {groups.map((group, groupIndex) => {
        const Icon = GROUP_ICONS[group.id] ?? ServerCog;
        return (
          <div key={group.id}>
            {groupIndex > 0 ? <DropdownMenuSeparator className="my-1" /> : null}
            <DropdownMenuLabel className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">
              {group.label}
            </DropdownMenuLabel>
            {group.cards.map((card) => (
              <DropdownMenuItem
                key={card.id}
                onSelect={() => {
                  onSeed(opsSeedText(card));
                  setOpen(false);
                }}
                className="flex cursor-default items-center gap-2.5 rounded-md px-2 py-1.5"
              >
                <Icon className="h-4 w-4 shrink-0 text-foreground/70" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] font-medium text-foreground">
                    {card.label}
                  </span>
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {card.description}
                  </span>
                </span>
              </DropdownMenuItem>
            ))}
          </div>
        );
      })}
    </>
  );

  if (asSubmenu) {
    return (
      <DropdownMenuSub>
        <DropdownMenuSubTrigger disabled={disabled} data-testid="dev-other-ops">
          <ServerCog className="h-4 w-4 shrink-0" aria-hidden />
          {opsLabel}
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent className={catalogClass}>{catalog}</DropdownMenuSubContent>
      </DropdownMenuSub>
    );
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "flex min-w-0 items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors",
            open
              ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
              : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            "disabled:pointer-events-none disabled:opacity-55",
          )}
          title={tx("studio.ops.title", "Ops studio")}
        >
          <ServerCog className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {compact ? null : <span className="truncate">{opsLabel}</span>}
          <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="bottom"
        sideOffset={8}
        className={catalogClass}
      >
        {catalog}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
