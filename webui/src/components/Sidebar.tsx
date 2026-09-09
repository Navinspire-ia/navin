// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState, type ReactNode } from "react";
import {
  Archive,
  BadgeDollarSign,
  Briefcase,
  ChevronRight,
  Clapperboard,
  Code2,
  FileText,
  Globe2,
  Landmark,
  LayoutGrid,
  Megaphone,
  Mic,
  Moon,
  NotebookPen,
  PanelLeft,
  PanelLeftClose,
  Search,
  Settings,
  ShieldAlert,
  SquarePen,
  Sun,
  Target,
  TrendingUp,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ChatList } from "@/components/ChatList";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import NotificationCenter from "@/components/NotificationCenter";
import { SidebarAccountCard } from "@/components/SidebarAccountCard";
import type {
  ChatSummary,
  SidebarViewState,
} from "@/lib/types";
import { cn } from "@/lib/utils";

/** Entries under "Studio", in sidebar order. */
const OTHER_APPS = [
  "tenders",
  "career",
  "leads",
  "marketing",
  "trading",
  "ads",
  "seo",
  "scraping",
  "montage",
  "notes",
  "meeting",
  "crm",
  "content",
  "risklens",
] as const;

type StudioApp = (typeof OTHER_APPS)[number];

/**
 * Cursor-style rows: 28px tall, a single 16px glyph column, 13px text, flat
 * hover / selected tints. No pills, no borders, no cards.
 */
const ROW_BASE =
  "flex min-w-0 shrink-0 items-center rounded-md text-[13px] leading-4 outline-none transition-colors focus-visible:ring-1 focus-visible:ring-ring/50";
const ROW_IDLE =
  "text-sidebar-foreground/85 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground";
const ROW_ACTIVE = "bg-sidebar-accent text-sidebar-foreground";
const ICON_BUTTON =
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-foreground focus-visible:ring-1 focus-visible:ring-ring/50";

interface SidebarProps {
  sessions: ChatSummary[];
  activeKey: string | null;
  loading: boolean;
  onNewChat: () => void;
  onSelect: (key: string) => void;
  onRequestDelete: (key: string, label: string) => void;
  onTogglePin: (key: string) => void;
  onMoveChat?: (
    dragKey: string,
    targetKey: string,
    targetPinned: boolean,
    visibleUnpinnedKeys: string[],
  ) => void;
  onRequestRename: (key: string, label: string) => void;
  onToggleArchive: (key: string) => void;
  onForkChat?: (key: string) => void;
  onOpenInNewTab?: (key: string) => void;
  onToggleUnread?: (chatId: string) => void;
  onArchivePriorChats?: (key: string) => void;
  onToggleGroup: (groupId: string) => void;
  onRequestRenameProject: (projectKey: string, label: string) => void;
  onRequestDeleteProject?: (projectKey: string, label: string) => void;
  onArchiveProject?: (keys: string[]) => void;
  onNewChatInProject?: (projectPath: string, projectName: string) => void;
  onOpenProject: (projectPath: string, projectName: string) => void;
  onCreateProjectFolder?: () => void;
  onOpenSettings: () => void;
  onOpenAccount: () => void;
  settingsActive?: boolean;
  onOpenSearch?: () => void;
  onOpenRisklensStudio: () => void;
  onOpenDev: () => void;
  onOpenNotes: () => void;
  onOpenScrapingStudio: () => void;
  onOpenContentStudio: () => void;
  onOpenMarketingStudio: () => void;
  onOpenMontageStudio: () => void;
  onOpenAdsStudio: () => void;
  onOpenSeoStudio: () => void;
  onOpenTendersStudio: () => void;
  onOpenCareerStudio: () => void;
  onOpenTradingStudio: () => void;
  onOpenLeadsStudio: () => void;
  onOpenCrmStudio: () => void;
  onOpenMeetingStudio: () => void;
  theme?: "light" | "dark";
  onToggleTheme?: () => void;
  activeUtility?:
    | "apps"
    | "skills"
    | "templates"
    | "automations"
    | "project"
    | "risklens"
    | "dev"
    | "chat"
    | "scraping"
    | "content"
    | "marketing"
    | "montage"
    | "ads"
    | "seo"
    | "leads"
    | "tenders"
    | "career"
    | "trading"
    | "meeting"
    | "ops"
    | "notes"
    | "crm"
    | "tools"
    | null;
  onToggleArchived: () => void;
  onCollapse: () => void;
  onExpand?: () => void;
  onExpandPreviewEnter?: () => void;
  onExpandPreviewLeave?: () => void;
  containActionMenus?: boolean;
  collapsed?: boolean;
  pinnedKeys?: string[];
  chatOrder?: string[];
  archivedKeys?: string[];
  titleOverrides?: Record<string, string>;
  projectNameOverrides?: Record<string, string>;
  collapsedGroups?: Record<string, boolean>;
  runningChatIds?: string[];
  updatedChatIds?: string[];
  viewState?: SidebarViewState;
  showArchived?: boolean;
  archivedCount?: number;
  defaultWorkspacePath?: string | null;
  recentProjects?: Array<{ path: string; name?: string }>;
  hostChromeInset?: boolean;
}

export function Sidebar(props: SidebarProps) {
  const { t } = useTranslation();
  const [menuPortalContainer, setMenuPortalContainer] =
    useState<HTMLElement | null>(null);
  const otherAppActive =
    props.activeUtility != null &&
    (OTHER_APPS as readonly string[]).includes(props.activeUtility);
  const [otherAppsOpen, setOtherAppsOpen] = useState(otherAppActive);
  const collapsed = Boolean(props.collapsed);
  const toggleLabel = t("thread.header.toggleSidebar");
  const studioLabel = t("sidebar.otherApps", { defaultValue: "Studio" });

  useEffect(() => {
    if (otherAppActive) setOtherAppsOpen(true);
  }, [otherAppActive]);

  const studioApps: Array<{
    id: StudioApp;
    label: string;
    icon: LucideIcon;
    onClick: () => void;
  }> = [
    {
      id: "tenders",
      label: t("sidebar.tendersStudio", { defaultValue: "Tenders" }),
      icon: Landmark,
      onClick: props.onOpenTendersStudio,
    },
    {
      id: "career",
      label: t("sidebar.careerStudio", { defaultValue: "Career" }),
      icon: Briefcase,
      onClick: props.onOpenCareerStudio,
    },
    {
      id: "leads",
      label: t("sidebar.leadsStudio", { defaultValue: "Leads" }),
      icon: Target,
      onClick: props.onOpenLeadsStudio,
    },
    {
      id: "marketing",
      label: t("sidebar.marketingStudio", { defaultValue: "Marketing" }),
      icon: Megaphone,
      onClick: props.onOpenMarketingStudio,
    },
    {
      id: "trading",
      label: t("sidebar.tradingStudio", { defaultValue: "Trading" }),
      icon: Wallet,
      onClick: props.onOpenTradingStudio,
    },
    {
      id: "ads",
      label: t("sidebar.adsStudio", { defaultValue: "Ads" }),
      icon: BadgeDollarSign,
      onClick: props.onOpenAdsStudio,
    },
    {
      id: "seo",
      label: t("sidebar.seoStudio", { defaultValue: "SEO" }),
      icon: TrendingUp,
      onClick: props.onOpenSeoStudio,
    },
    {
      id: "scraping",
      label: t("sidebar.scrapingStudio", { defaultValue: "Scraping" }),
      icon: Globe2,
      onClick: props.onOpenScrapingStudio,
    },
    {
      id: "montage",
      label: t("sidebar.montageStudio", { defaultValue: "Montage" }),
      icon: Clapperboard,
      onClick: props.onOpenMontageStudio,
    },
    {
      id: "notes",
      label: t("sidebar.notes", { defaultValue: "Notes" }),
      icon: NotebookPen,
      onClick: props.onOpenNotes,
    },
    {
      id: "meeting",
      label: t("sidebar.meetingStudio", { defaultValue: "Meeting" }),
      icon: Mic,
      onClick: props.onOpenMeetingStudio,
    },
    {
      id: "crm",
      label: t("sidebar.crm", { defaultValue: "CRM" }),
      icon: Briefcase,
      onClick: props.onOpenCrmStudio,
    },
    {
      id: "content",
      label: t("sidebar.contentStudio", { defaultValue: "Documents" }),
      icon: FileText,
      onClick: props.onOpenContentStudio,
    },
    {
      id: "risklens",
      label: t("sidebar.risklensStudio", { defaultValue: "RiskLens" }),
      icon: ShieldAlert,
      onClick: props.onOpenRisklensStudio,
    },
  ];

  return (
    <nav
      ref={props.containActionMenus ? setMenuPortalContainer : undefined}
      aria-label={t("sidebar.navigation")}
      className={cn(
        "relative flex h-full w-full min-w-0 flex-col text-sidebar-foreground",
        props.hostChromeInset
          ? "bg-transparent"
          : "border-r border-sidebar-border/60 bg-sidebar",
      )}
    >
      {/* Header: hide/open + Search on the same row. */}
      <div
        className={cn(
          "flex shrink-0 items-center gap-1 host-no-drag",
          collapsed ? "w-14 flex-col justify-center pb-1.5 pt-3" : "h-10 px-2",
        )}
      >
        {collapsed ? (
          props.onExpand ? (
            <button
              type="button"
              aria-label={toggleLabel}
              title={toggleLabel}
              onClick={props.onExpand}
              data-testid="host-sidebar-toggle"
              className={ICON_BUTTON}
            >
              <PanelLeft className="h-4 w-4" strokeWidth={1.75} aria-hidden />
            </button>
          ) : null
        ) : (
          <button
            type="button"
            aria-label={t("sidebar.collapse")}
            title={t("sidebar.collapse")}
            onClick={props.onCollapse}
            data-testid="host-sidebar-toggle"
            className={ICON_BUTTON}
          >
            <PanelLeftClose className="h-4 w-4" strokeWidth={1.75} aria-hidden />
          </button>
        )}
        {props.onOpenSearch ? (
          collapsed ? (
            <button
              type="button"
              aria-label={t("sidebar.searchAria", { defaultValue: "Search" })}
              title={t("sidebar.searchAria", { defaultValue: "Search" })}
              onClick={props.onOpenSearch}
              data-testid="sidebar-search"
              className={ICON_BUTTON}
            >
              <Search className="h-4 w-4" strokeWidth={1.75} aria-hidden />
            </button>
          ) : (
            <button
              type="button"
              onClick={props.onOpenSearch}
              data-testid="sidebar-search"
              className={cn(ROW_BASE, ROW_IDLE, "h-7 min-w-0 flex-1 gap-2 px-2 text-left")}
            >
              <Search className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden />
              <span className="truncate">
                {t("sidebar.searchAria", { defaultValue: "Search" })}
              </span>
            </button>
          )
        ) : null}
      </div>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto overflow-x-hidden overscroll-contain scrollbar-thin scrollbar-track-transparent">
        <div
          className={cn(
            "shrink-0 space-y-1 pb-4",
            collapsed ? "flex w-14 flex-col items-center" : "px-2",
          )}
        >
          <SidebarRow
            collapsed={collapsed}
            icon={SquarePen}
            label={t("sidebar.newChat")}
            onClick={props.onNewChat}
          />
          <SidebarRow
            collapsed={collapsed}
            icon={Code2}
            label={t("sidebar.dev", { defaultValue: "Code" })}
            onClick={props.onOpenDev}
            active={props.activeUtility === "dev"}
          />
          <SidebarRow
            collapsed={collapsed}
            icon={LayoutGrid}
            label={studioLabel}
            onClick={() => setOtherAppsOpen((open) => !open)}
            active={otherAppActive && !otherAppsOpen}
            expanded={otherAppsOpen}
            controls="sidebar-other-apps"
            trailing={
              <ChevronRight
                className={cn(
                  "h-3.5 w-3.5 shrink-0 text-muted-foreground/70 transition-transform duration-200",
                  otherAppsOpen && "rotate-90",
                )}
                aria-hidden
              />
            }
          />
          {otherAppsOpen ? (
            <div
              id="sidebar-other-apps"
              className={cn(
                "space-y-px",
                collapsed && "flex w-full flex-col items-center",
              )}
            >
              {studioApps.map((app) => (
                <SidebarRow
                  key={app.id}
                  collapsed={collapsed}
                  indent
                  icon={app.icon}
                  label={app.label}
                  onClick={app.onClick}
                  active={props.activeUtility === app.id}
                />
              ))}
            </div>
          ) : null}
          {props.archivedCount ? (
            <SidebarRow
              collapsed={collapsed}
              icon={Archive}
              label={props.showArchived ? t("chat.hideArchived") : t("chat.showArchived")}
              onClick={props.onToggleArchived}
            />
          ) : null}
        </div>
        {!collapsed ? (
          <ChatList
            sessions={props.sessions}
            activeKey={props.activeKey}
            loading={props.loading}
            emptyLabel={t("chat.noSessions")}
            onSelect={props.onSelect}
            onRequestDelete={props.onRequestDelete}
            onTogglePin={props.onTogglePin}
            onMoveChat={props.onMoveChat}
            onRequestRename={props.onRequestRename}
            onToggleArchive={props.onToggleArchive}
            onForkChat={props.onForkChat}
            onOpenInNewTab={props.onOpenInNewTab}
            onToggleUnread={props.onToggleUnread}
            onArchivePriorChats={props.onArchivePriorChats}
            onToggleGroup={props.onToggleGroup}
            onRequestRenameProject={props.onRequestRenameProject}
            onRequestDeleteProject={props.onRequestDeleteProject}
            onArchiveProject={props.onArchiveProject}
            onNewChatInProject={props.onNewChatInProject}
            onOpenProject={props.onOpenProject}
            onCreateProjectFolder={props.onCreateProjectFolder}
            onNewChat={props.onNewChat}
            pinnedKeys={props.pinnedKeys}
            chatOrder={props.chatOrder}
            archivedKeys={props.archivedKeys}
            titleOverrides={props.titleOverrides}
            projectNameOverrides={props.projectNameOverrides}
            collapsedGroups={props.collapsedGroups}
            runningChatIds={props.runningChatIds}
            updatedChatIds={props.updatedChatIds}
            density={props.viewState?.density}
            showTimestamps
            sort={props.viewState?.sort}
            showArchived={props.showArchived}
            defaultWorkspacePath={props.defaultWorkspacePath}
            recentProjects={props.recentProjects}
            actionMenuPortalContainer={
              props.containActionMenus ? menuPortalContainer : undefined
            }
          />
        ) : null}
      </div>

      <div
        className={cn(
          "flex shrink-0 items-center gap-0.5 px-2 py-1.5",
          collapsed && "w-14 flex-col px-0",
        )}
      >
        <div className={cn("min-w-0", collapsed ? undefined : "flex-1")}>
          <SidebarAccountCard collapsed={collapsed} onClick={props.onOpenAccount} />
        </div>
        <button
          type="button"
          aria-label={t("sidebar.settings")}
          title={t("sidebar.settings")}
          aria-current={props.settingsActive ? "page" : undefined}
          onClick={() => props.onOpenSettings()}
          className={cn(ICON_BUTTON, props.settingsActive && ROW_ACTIVE)}
        >
          <Settings className="h-4 w-4" strokeWidth={1.75} aria-hidden />
        </button>
        {props.onToggleTheme ? (
          <button
            type="button"
            aria-label={t("thread.header.toggleTheme")}
            title={t("thread.header.toggleTheme")}
            onClick={props.onToggleTheme}
            className={ICON_BUTTON}
          >
            {props.theme === "dark" ? (
              <Sun className="h-4 w-4" strokeWidth={1.75} aria-hidden />
            ) : (
              <Moon className="h-4 w-4" strokeWidth={1.75} aria-hidden />
            )}
          </button>
        ) : null}
        <ConnectionBadge />
        <NotificationCenter placement="sidebar" />
      </div>
    </nav>
  );
}

function SidebarRow({
  collapsed,
  icon: Icon,
  label,
  onClick,
  active = false,
  indent = false,
  expanded,
  controls,
  trailing,
}: {
  collapsed: boolean;
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  active?: boolean;
  indent?: boolean;
  expanded?: boolean;
  controls?: string;
  trailing?: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-current={active ? "page" : undefined}
      aria-expanded={expanded}
      aria-controls={expanded ? controls : undefined}
      title={collapsed ? label : undefined}
      onClick={() => onClick()}
      className={cn(
        ROW_BASE,
        collapsed ? "h-8 w-8 justify-center" : "h-7 w-full gap-2 text-left",
        !collapsed && (indent ? "pl-8 pr-2" : "px-2"),
        active ? ROW_ACTIVE : ROW_IDLE,
      )}
    >
      <Icon className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden />
      {!collapsed ? <span className="min-w-0 flex-1 truncate">{label}</span> : null}
      {!collapsed ? trailing : null}
    </button>
  );
}
