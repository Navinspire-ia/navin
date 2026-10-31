// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from "react";
import {
  AudioLines,
  Ellipsis,
  LayoutTemplate,
  ListPlus,
  Mic,
  Paperclip,
  Plug,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ComposerModeSubmenu, type ComposerTurnMode } from "@/components/thread/ComposerModeMenu";
import {
  ComposerPermissionSubmenu,
  useExecApprovalMode,
} from "@/components/thread/ComposerPermissionMenu";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/** One action of the More menu; absent when the composer does not offer it. */
export interface ComposerToolAction {
  label: string;
  disabled?: boolean;
  onSelect: () => void;
  /** Shown before the label instead of the default icon (team faces). */
  leading?: ReactNode;
  active?: boolean;
}

export interface ComposerToolsMenuProps {
  disabled?: boolean;
  mode: ComposerTurnMode;
  modeDisabled?: boolean;
  onModeChange: (mode: ComposerTurnMode) => void;
  attach?: ComposerToolAction | null;
  documentTemplate?: ComposerToolAction | null;
  connectors?: ComposerToolAction | null;
  voice?: ComposerToolAction | null;
  liveVoice?: ComposerToolAction | null;
  queue?: ComposerToolAction | null;
  team?: ComposerToolAction | null;
}

/**
 * The narrow composer keeps the model, the effort, the context size and the
 * send button on its row; every other control lives here, still working: the
 * turn mode and the permissions as submenus, the tools as plain rows.
 */
export function ComposerToolsMenu({
  disabled,
  mode,
  modeDisabled,
  onModeChange,
  attach,
  documentTemplate,
  connectors,
  voice,
  liveVoice,
  queue,
  team,
}: ComposerToolsMenuProps) {
  const { t } = useTranslation();
  const permission = useExecApprovalMode();
  const label = t("thread.composer.moreTools", { defaultValue: "More options" });
  const tools: Array<[string, LucideIcon, ComposerToolAction | null | undefined]> = [
    ["attach", Paperclip, attach],
    ["template", LayoutTemplate, documentTemplate],
    ["connectors", Plug, connectors],
    ["voice", Mic, voice],
    ["live-voice", AudioLines, liveVoice],
    ["queue", ListPlus, queue],
    ["team", Users, team],
  ];
  const shown = tools.filter((entry): entry is [string, LucideIcon, ComposerToolAction] => Boolean(entry[2]));

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label={label}
          title={label}
          data-testid="composer-tools-menu"
          className="h-7 w-7 shrink-0 rounded-md border-transparent text-muted-foreground hover:bg-muted/65 hover:text-foreground data-[state=open]:bg-muted/65 data-[state=open]:text-foreground"
        >
          <Ellipsis className="h-3.5 w-3.5" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="top"
        sideOffset={8}
        className="w-[min(16rem,calc(100vw-2rem))] p-1"
      >
        <ComposerModeSubmenu mode={mode} disabled={modeDisabled} onChange={onModeChange} />
        <ComposerPermissionSubmenu state={permission} disabled={disabled} />
        {shown.length > 0 ? <DropdownMenuSeparator className="my-1" /> : null}
        {shown.map(([id, Icon, action]) => (
          <DropdownMenuItem
            key={id}
            disabled={action.disabled}
            onSelect={action.onSelect}
            data-testid={`composer-tools-${id}`}
            className={cn(
              "gap-2 text-[13px]",
              action.active && "bg-foreground/[0.06]",
            )}
          >
            {action.leading ?? <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />}
            <span className="min-w-0 flex-1 truncate">{action.label}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
