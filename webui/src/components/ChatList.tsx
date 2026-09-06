import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Archive,
  ArchiveRestore,
  ExternalLink,
  ChevronRight,
  Folder,
  FolderOpen,
  FolderPlus,
  GitFork,
  GripVertical,
  Mail,
  MailOpen,
  MoreHorizontal,
  Pencil,
  Plus,
  Pin,
  PinOff,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  ContextMenu,
  useContextMenu,
} from "@/components/ui/context-menu";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { compactRelativeTime, deriveTitle } from "@/lib/format";
import {
  GROUP_VISIBLE_INCREMENT,
  INITIAL_GROUP_VISIBLE_COUNT,
  SECTION_CHATS_ID,
  SECTION_PROJECTS_ID,
  displayTitle,
  groupSessions,
  isCollapsedProject,
  visibleSessionsForLimit,
  type ChatGroupLabels,
} from "@/lib/chat-groups";
import { cn } from "@/lib/utils";
import type { ChatSummary, SidebarDensity, SidebarSortMode } from "@/lib/types";

const ACTION_MENU_CONTENT_CLASS = "w-56 min-w-56";
const ACTION_MENU_ITEM_CLASS = "grid w-full grid-cols-[1rem_minmax(0,1fr)] items-center gap-2";

/**
 * Cursor-style list: every row is 28px with one 16px glyph column, so folder
 * names, chat titles and the "More" link all start at the same x. Nesting is
 * carried by the glyph (folder vs status dot), not by extra indentation.
 */
const ROW_CLASS =
  "group flex min-w-0 items-center gap-2 rounded-md pl-2 pr-1 text-[13px] leading-4 transition-colors";
const ROW_IDLE_CLASS =
  "text-sidebar-foreground/85 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground";
const ROW_ACTIVE_CLASS = "bg-sidebar-accent text-sidebar-foreground";
const ROW_ACTION_CLASS =
  "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground/70 outline-none transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground focus-visible:ring-1 focus-visible:ring-ring/50";

interface ChatListProps {
  sessions: ChatSummary[];
  activeKey: string | null;
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
  onToggleGroup?: (groupId: string) => void;
  onRequestRenameProject?: (projectKey: string, label: string) => void;
  onRequestDeleteProject?: (projectKey: string, label: string) => void;
  onArchiveProject?: (keys: string[]) => void;
  onNewChatInProject?: (projectPath: string, projectName: string) => void;
  onOpenProject?: (projectPath: string, projectName: string) => void;
  onCreateProjectFolder?: () => void;
  onNewChat?: () => void;
  pinnedKeys?: string[];
  chatOrder?: string[];
  archivedKeys?: string[];
  titleOverrides?: Record<string, string>;
  projectNameOverrides?: Record<string, string>;
  collapsedGroups?: Record<string, boolean>;
  runningChatIds?: string[];
  updatedChatIds?: string[];
  density?: SidebarDensity;
  showTimestamps?: boolean;
  sort?: SidebarSortMode;
  showArchived?: boolean;
  defaultWorkspacePath?: string | null;
  recentProjects?: Array<{ path: string; name?: string }>;
  actionMenuPortalContainer?: HTMLElement | null;
  loading?: boolean;
  emptyLabel?: string;
}

export const ChatList = memo(function ChatList({
  sessions,
  activeKey,
  onSelect,
  onRequestDelete,
  onTogglePin,
  onMoveChat,
  onRequestRename,
  onToggleArchive,
  onForkChat,
  onOpenInNewTab,
  onToggleUnread,
  onArchivePriorChats,
  onToggleGroup,
  onRequestRenameProject,
  onRequestDeleteProject,
  onArchiveProject,
  onNewChatInProject,
  onOpenProject,
  onCreateProjectFolder,
  onNewChat,
  pinnedKeys = [],
  chatOrder = [],
  archivedKeys = [],
  titleOverrides = {},
  projectNameOverrides = {},
  collapsedGroups = {},
  runningChatIds = [],
  updatedChatIds = [],
  density = "comfortable",
  showTimestamps = true,
  sort = "updated_desc",
  showArchived = false,
  defaultWorkspacePath,
  recentProjects = [],
  actionMenuPortalContainer,
  loading,
  emptyLabel,
}: ChatListProps) {
  const { t } = useTranslation();
  const sessionMenu = useContextMenu();
  const [extraByGroup, setExtraByGroup] = useState<Record<string, number>>({});
  const [draggingKey, setDraggingKey] = useState<string | null>(null);
  const [dragOverKey, setDragOverKey] = useState<string | null>(null);
  const didDragRef = useRef(false);
  const labels = useMemo<ChatGroupLabels>(() => ({
    pinned: t("chat.groups.pinned"),
    all: t("chat.groups.all"),
    today: t("chat.groups.today"),
    yesterday: t("chat.groups.yesterday"),
    last7Days: t("chat.groups.last7Days", { defaultValue: "Last 7 days" }),
    last30Days: t("chat.groups.last30Days", { defaultValue: "Last 30 days" }),
    older: t("chat.groups.older", { defaultValue: "Older" }),
    earlier: t("chat.groups.earlier"),
    archived: t("chat.groups.archived"),
    projects: t("chat.groups.projects"),
    fallbackTitle: t("chat.newChat"),
  }), [t]);
  const groups = useMemo(
    () => groupSessions(sessions, labels, {
      pinnedKeys,
      archivedKeys,
      chatOrder,
      titleOverrides,
      projectNameOverrides,
      showArchived,
      sort,
      defaultWorkspacePath,
      recentProjects,
    }),
    [
      archivedKeys,
      chatOrder,
      labels,
      pinnedKeys,
      sessions,
      showArchived,
      sort,
      titleOverrides,
      projectNameOverrides,
      defaultWorkspacePath,
      recentProjects,
    ],
  );
  useEffect(() => {
    setExtraByGroup({});
  }, [showArchived, sort]);

  const revealMore = (groupId: string) => {
    setExtraByGroup((current) => ({
      ...current,
      [groupId]: (current[groupId] ?? 0) + GROUP_VISIBLE_INCREMENT,
    }));
  };

  if (loading && sessions.length === 0) {
    return (
      <div className="px-4 py-3 text-[13px] text-muted-foreground/70">
        {t("chat.loading")}
      </div>
    );
  }

  if (sessions.length === 0 && recentProjects.length === 0) {
    return (
      <div className="px-4 py-3 text-[13px] leading-5 text-muted-foreground/70">
        {emptyLabel ?? t("chat.noSessions")}
      </div>
    );
  }

  const pinned = new Set(pinnedKeys);
  const archived = new Set(archivedKeys);
  const running = new Set(runningChatIds);
  const updated = new Set(updatedChatIds);
  const rowHeightClass = density === "compact" ? "h-6" : "h-7";
  const firstProjectGroupIndex = groups.findIndex((group) => group.kind === "project");
  const projectsHidden = Boolean(collapsedGroups[SECTION_PROJECTS_ID]);
  const chatsHidden = Boolean(collapsedGroups[SECTION_CHATS_ID]);

  return (
    <div className="min-w-0 overflow-x-hidden px-2 pb-2 pt-2">
      {groups.map((group, index) => {
        const isProject = group.kind === "project";
        const isChatsGroup = group.id === "workspace:chats" || group.id === "date:all";
        if (isProject && projectsHidden && index !== firstProjectGroupIndex) {
          return null;
        }
        const visibleCap =
          INITIAL_GROUP_VISIBLE_COUNT + (extraByGroup[group.id] ?? 0);
        const projectCollapsed = isCollapsedProject(group, collapsedGroups);
        const hideBody =
          (isProject && projectsHidden) || (isChatsGroup && chatsHidden) || projectCollapsed;
        const visibleSessions = hideBody
          ? []
          : visibleSessionsForLimit(group.sessions, visibleCap, activeKey);
        const hiddenInGroup = hideBody
          ? 0
          : Math.max(0, group.sessions.length - visibleCap);
        const startsSection = index === firstProjectGroupIndex || !isProject;

        return (
          <section
            key={group.id}
            aria-label={group.label}
            onDragOver={
              onMoveChat && group.id === "pinned"
                ? (event) => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                  }
                : undefined
            }
            onDrop={
              onMoveChat && group.id === "pinned"
                ? (event) => {
                    event.preventDefault();
                    const dragKey = event.dataTransfer.getData("text/plain");
                    const targetKey = group.sessions[0]?.key;
                    if (!dragKey || !targetKey || dragKey === targetKey) return;
                    onMoveChat(dragKey, targetKey, true, []);
                  }
                : undefined
            }
            className={cn(
              "min-w-0",
              startsSection && index > 0 && "mt-3",
              // Breathing room after an open folder so the next folder row
              // does not read as one of its chats.
              isProject && !projectCollapsed && visibleSessions.length > 0 && "mb-1.5",
            )}
          >
            {index === firstProjectGroupIndex ? (
              <SectionHeader
                label={labels.projects}
                collapsed={projectsHidden}
                onToggle={() => onToggleGroup?.(SECTION_PROJECTS_ID)}
                action={
                  onCreateProjectFolder
                    ? {
                        label: t("chat.newProjectFolderAria", {
                          defaultValue: "Create a project folder",
                        }),
                        icon: <FolderPlus className="h-4 w-4" strokeWidth={1.75} aria-hidden />,
                        onClick: onCreateProjectFolder,
                      }
                    : undefined
                }
              />
            ) : null}
            {isProject ? (
              projectsHidden ? null : (
              <ProjectGroupHeader
                label={group.label}
                path={group.projectPath}
                collapsed={projectCollapsed}
                onToggle={() => onToggleGroup?.(group.id)}
                onOpen={
                  group.projectPath && onOpenProject
                    ? () => {
                        if (collapsedGroups[group.id]) {
                          onToggleGroup?.(group.id);
                        }
                        onOpenProject(group.projectPath ?? "", group.label);
                      }
                    : undefined
                }
                onRequestRename={
                  group.projectKey && onRequestRenameProject
                    ? () => onRequestRenameProject(group.projectKey ?? "", group.label)
                    : undefined
                }
                onRequestDelete={
                  group.projectKey && onRequestDeleteProject
                    ? () => onRequestDeleteProject(group.projectKey ?? "", group.label)
                    : undefined
                }
                onRequestArchive={
                  onArchiveProject
                    ? () => onArchiveProject(group.sessions.map((session) => session.key))
                    : undefined
                }
                onNewChat={
                  group.projectPath && onNewChatInProject
                    ? () => {
                        if (collapsedGroups[group.id]) {
                          onToggleGroup?.(group.id);
                        }
                        onNewChatInProject(group.projectPath ?? "", group.label);
                      }
                    : undefined
                }
                actionMenuPortalContainer={actionMenuPortalContainer}
              />
              )
            ) : (
              <SectionHeader
                label={group.label}
                collapsed={isChatsGroup ? chatsHidden : undefined}
                onToggle={
                  isChatsGroup ? () => onToggleGroup?.(SECTION_CHATS_ID) : undefined
                }
                action={
                  onNewChat
                  && (group.id === "workspace:chats" || group.id === "date:all")
                    ? {
                        label: t("sidebar.newChat"),
                        icon: <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden />,
                        onClick: onNewChat,
                      }
                    : undefined
                }
              />
            )}
            {visibleSessions.length > 0 ? (
              <ul className="space-y-px">
                {visibleSessions.map((s) => {
                  const active = s.key === activeKey;
                  const fallbackTitle = t("chat.fallbackTitle", {
                    id: s.chatId.slice(0, 6),
                  });
                  const generatedTitle = s.title?.trim() || "";
                  const title = displayTitle(s, titleOverrides, t("chat.newChat"));
                  const tooltipTitle =
                    titleOverrides[s.key]?.trim() ||
                    generatedTitle ||
                    deriveTitle(s.preview, fallbackTitle);
                  const isPinned = pinned.has(s.key);
                  const isArchived = archived.has(s.key);
                  const isUnread = updated.has(s.chatId);
                  const isRunning = running.has(s.chatId);
                  const actionEntries = sessionActionEntries({
                    t,
                    title,
                    isPinned,
                    isArchived,
                    isUnread,
                    onTogglePin: () => onTogglePin(s.key),
                    onForkChat: onForkChat ? () => onForkChat(s.key) : undefined,
                    onOpenInNewTab: onOpenInNewTab
                      ? () => onOpenInNewTab(s.key)
                      : undefined,
                    onToggleUnread: onToggleUnread
                      ? () => onToggleUnread(s.chatId)
                      : undefined,
                    onRequestRename: () => onRequestRename(s.key, title),
                    onToggleArchive: () => onToggleArchive(s.key),
                    onArchivePriorChats: onArchivePriorChats
                      ? () => onArchivePriorChats(s.key)
                      : undefined,
                    onRequestDelete: () => onRequestDelete(s.key, title),
                  });
                  const groupIsPinned = group.id === "pinned";
                  const unpinnedKeys = groupIsPinned
                    ? []
                    : group.sessions.map((item) => item.key);
                  return (
                    <li key={s.key} className="min-w-0">
                      <div
                        draggable={Boolean(onMoveChat)}
                        onContextMenu={(event) => {
                          sessionMenu.open(event, actionEntries);
                        }}
                        onDragStart={(event) => {
                          if (!onMoveChat) return;
                          if ((event.target as HTMLElement).closest("[data-navin-debug=session], [role=menuitem]")) {
                            event.preventDefault();
                            return;
                          }
                          didDragRef.current = false;
                          event.dataTransfer.effectAllowed = "move";
                          event.dataTransfer.setData("text/plain", s.key);
                          event.dataTransfer.setData("text/navin-chat", s.key);
                          setDraggingKey(s.key);
                        }}
                        onDrag={() => {
                          didDragRef.current = true;
                        }}
                        onDragEnd={() => {
                          setDraggingKey(null);
                          setDragOverKey(null);
                        }}
                        onDragEnter={(event) => {
                          if (!onMoveChat || !draggingKey || draggingKey === s.key) return;
                          event.preventDefault();
                          setDragOverKey(s.key);
                        }}
                        onDragOver={(event) => {
                          if (!onMoveChat) return;
                          event.preventDefault();
                          event.dataTransfer.dropEffect = "move";
                        }}
                        onDrop={(event) => {
                          if (!onMoveChat) return;
                          event.preventDefault();
                          event.stopPropagation();
                          const dragKey = (
                            event.dataTransfer.getData("text/navin-chat")
                            || event.dataTransfer.getData("text/plain")
                          ).trim();
                          setDraggingKey(null);
                          setDragOverKey(null);
                          if (!dragKey || dragKey === s.key) return;
                          onMoveChat(dragKey, s.key, groupIsPinned, unpinnedKeys);
                        }}
                        className={cn(
                          ROW_CLASS,
                          rowHeightClass,
                          "host-no-drag",
                          active ? ROW_ACTIVE_CLASS : ROW_IDLE_CLASS,
                          draggingKey === s.key && "opacity-40",
                          dragOverKey === s.key && draggingKey && draggingKey !== s.key && "bg-sidebar-accent",
                        )}
                      >
                        <ChatRowHandle
                          unread={isUnread && !active}
                          pinned={isPinned}
                          active={active}
                          dragLabel={t("chat.dragToReorder", {
                            defaultValue: "Drag to reorder",
                          })}
                        />
                        <button
                          type="button"
                          onClick={() => {
                            if (didDragRef.current) {
                              didDragRef.current = false;
                              return;
                            }
                            onSelect(s.key);
                          }}
                          title={tooltipTitle}
                          className="flex h-full min-w-0 flex-1 items-center overflow-hidden rounded-md text-left outline-none focus-visible:ring-1 focus-visible:ring-ring/50"
                        >
                          <span
                            className={cn(
                              "min-w-0 flex-1 truncate",
                              isUnread && !active && "font-medium text-sidebar-foreground",
                            )}
                          >
                            {title}
                          </span>
                        </button>
                        {isRunning ? (
                          <RunningDots
                            label={t("chat.activity.running")}
                            className={cn(
                              "mr-1 group-hover:hidden",
                              active ? "text-sidebar-foreground/85" : "text-sidebar-foreground/65",
                            )}
                          />
                        ) : showTimestamps ? (
                          <span
                            className={cn(
                              "shrink-0 pr-1 text-[11.5px] tabular-nums text-muted-foreground/60",
                              "group-hover:hidden",
                            )}
                          >
                            {compactRelativeTime(s.updatedAt ?? s.createdAt)}
                          </span>
                        ) : null}
                        <DropdownMenu modal={false}>
                          <DropdownMenuTrigger
                            data-navin-debug="session"
                            className={cn(
                              ROW_ACTION_CLASS,
                              // Opacity, not `hidden`: `display: none` on close
                              // makes Radix measure a 0x0 trigger and the menu
                              // blinks at the origin while Presence is fading.
                              "pointer-events-none opacity-0",
                              "group-hover:pointer-events-auto group-hover:opacity-100",
                              "data-[state=open]:pointer-events-auto data-[state=open]:opacity-100",
                            )}
                            aria-label={t("chat.actions", { title })}
                          >
                            <MoreHorizontal className="h-3.5 w-3.5" aria-hidden />
                          </DropdownMenuTrigger>
                          <DropdownMenuContent
                            align="end"
                            className={ACTION_MENU_CONTENT_CLASS}
                            portalContainer={actionMenuPortalContainer}
                            onCloseAutoFocus={(event) => event.preventDefault()}
                          >
                            {actionEntries.map((item) => (
                              <div key={item.id}>
                                {item.separatorBefore ? <DropdownMenuSeparator /> : null}
                                <DropdownMenuItem
                                  onSelect={() => {
                                    if (item.danger) {
                                      window.setTimeout(() => item.onSelect?.(), 0);
                                      return;
                                    }
                                    item.onSelect?.();
                                  }}
                                  className={cn(
                                    ACTION_MENU_ITEM_CLASS,
                                    item.danger && "text-destructive focus:text-destructive",
                                  )}
                                >
                                  {item.icon}
                                  {item.label}
                                </DropdownMenuItem>
                              </div>
                            ))}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : null}
            {!projectCollapsed && hiddenInGroup > 0 ? (
              <ChatsFoldFooter
                hiddenCount={Math.min(GROUP_VISIBLE_INCREMENT, hiddenInGroup)}
                onToggle={() => revealMore(group.id)}
              />
            ) : null}
          </section>
        );
      })}
      <ContextMenu state={sessionMenu.state} onClose={sessionMenu.close} />
    </div>
  );
});

function sessionActionEntries({
  t,
  isPinned,
  isArchived,
  isUnread,
  onTogglePin,
  onForkChat,
  onOpenInNewTab,
  onToggleUnread,
  onRequestRename,
  onToggleArchive,
  onArchivePriorChats,
  onRequestDelete,
}: {
  t: ReturnType<typeof useTranslation>["t"];
  title: string;
  isPinned: boolean;
  isArchived: boolean;
  isUnread: boolean;
  onTogglePin: () => void;
  onForkChat?: () => void;
  onOpenInNewTab?: () => void;
  onToggleUnread?: () => void;
  onRequestRename: () => void;
  onToggleArchive: () => void;
  onArchivePriorChats?: () => void;
  onRequestDelete: () => void;
}) {
  const iconClass = "h-4 w-4 shrink-0";
  return [
    {
      id: "pin",
      label: isPinned ? t("chat.unpin") : t("chat.pin"),
      icon: isPinned ? <PinOff className={iconClass} /> : <Pin className={iconClass} />,
      onSelect: onTogglePin,
    },
    ...(onForkChat
      ? [{
          id: "fork",
          label: t("chat.forkChat", { defaultValue: "Fork chat" }),
          icon: <GitFork className={iconClass} />,
          onSelect: onForkChat,
        }]
      : []),
    ...(onOpenInNewTab
      ? [{
          id: "tab",
          label: t("chat.openInNewTab", { defaultValue: "Open in new tab" }),
          icon: <ExternalLink className={iconClass} />,
          onSelect: onOpenInNewTab,
        }]
      : []),
    ...(onToggleUnread
      ? [{
          id: "unread",
          label: isUnread
            ? t("chat.markRead", { defaultValue: "Mark as read" })
            : t("chat.markUnread", { defaultValue: "Mark as unread" }),
          icon: isUnread
            ? <MailOpen className={iconClass} />
            : <Mail className={iconClass} />,
          separatorBefore: true,
          onSelect: onToggleUnread,
        }]
      : []),
    {
      id: "delete",
      label: t("chat.delete"),
      icon: <Trash2 className={iconClass} />,
      danger: true,
      separatorBefore: true,
      onSelect: onRequestDelete,
    },
    {
      id: "rename",
      label: t("chat.rename"),
      icon: <Pencil className={iconClass} />,
      onSelect: onRequestRename,
    },
    {
      id: "archive",
      label: isArchived ? t("chat.unarchive") : t("chat.archive"),
      icon: isArchived
        ? <ArchiveRestore className={iconClass} />
        : <Archive className={iconClass} />,
      onSelect: onToggleArchive,
    },
    ...(onArchivePriorChats && !isArchived
      ? [{
          id: "archive-prior",
          label: t("chat.archivePrior", { defaultValue: "Archive prior chats" }),
          icon: <Archive className={iconClass} />,
          onSelect: onArchivePriorChats,
        }]
      : []),
  ];
}

/** Muted 12px caption with an optional icon action at the right edge. */
function SectionHeader({
  label,
  action,
  collapsed,
  onToggle,
}: {
  label: string;
  action?: { label: string; icon: ReactNode; onClick: () => void };
  collapsed?: boolean;
  onToggle?: () => void;
}) {
  const { t } = useTranslation();
  const toggleLabel = collapsed
    ? t("chat.expandSection", { defaultValue: "Show {{label}}", label })
    : t("chat.collapseSection", { defaultValue: "Hide {{label}}", label });
  const titleClass = "min-w-0 flex-1 truncate text-[12px] font-medium text-muted-foreground/75";

  return (
    <div className="flex h-7 items-center gap-1 pl-2 pr-1">
      {onToggle ? (
        <button
          type="button"
          aria-expanded={!collapsed}
          aria-label={toggleLabel}
          title={toggleLabel}
          onClick={onToggle}
          className="relative grid h-4 w-4 shrink-0 place-items-center rounded-sm text-muted-foreground/80 outline-none transition-colors before:absolute before:-inset-1.5 before:content-[''] hover:text-sidebar-foreground focus-visible:ring-1 focus-visible:ring-ring/50"
        >
          <ChevronRight
            className={cn(
              "h-3.5 w-3.5 transition-transform duration-200",
              !collapsed && "rotate-90",
            )}
            strokeWidth={1.75}
            aria-hidden
          />
        </button>
      ) : null}
      {onToggle ? (
        <button
          type="button"
          onClick={onToggle}
          className={cn(titleClass, "rounded-md text-left outline-none focus-visible:ring-1 focus-visible:ring-ring/50")}
        >
          {label}
        </button>
      ) : (
        <span className={titleClass}>{label}</span>
      )}
      {action ? (
        <button
          type="button"
          onClick={action.onClick}
          aria-label={action.label}
          title={action.label}
          className={ROW_ACTION_CLASS}
        >
          {action.icon}
        </button>
      ) : null}
    </div>
  );
}

function ProjectGroupHeader({
  label,
  path,
  collapsed,
  onToggle,
  onOpen,
  onNewChat,
  onRequestRename,
  onRequestArchive,
  onRequestDelete,
  actionMenuPortalContainer,
}: {
  label: string;
  path?: string;
  collapsed: boolean;
  onToggle: () => void;
  onOpen?: () => void;
  onNewChat?: () => void;
  onRequestRename?: () => void;
  onRequestArchive?: () => void;
  onRequestDelete?: () => void;
  actionMenuPortalContainer?: HTMLElement | null;
}) {
  const { t } = useTranslation();
  const FolderGlyph = collapsed ? Folder : FolderOpen;
  const toggleLabel = collapsed
    ? t("chat.expandProject", { defaultValue: "Expand project" })
    : t("chat.collapseProject", { defaultValue: "Collapse project" });
  const hasMenu = Boolean(onRequestRename || onRequestArchive || onRequestDelete);

  return (
    <div
      title={path}
      className={cn(ROW_CLASS, "h-7", ROW_IDLE_CLASS, "text-sidebar-foreground/90")}
    >
      {/* The folder glyph is the fold toggle; the pseudo-element widens its hit
          area to 28px without moving the shared text column. */}
      <button
        type="button"
        aria-expanded={!collapsed}
        aria-label={toggleLabel}
        onClick={onToggle}
        className="relative grid h-4 w-4 shrink-0 place-items-center rounded-sm text-muted-foreground/80 outline-none transition-colors before:absolute before:-inset-1.5 before:content-[''] hover:text-sidebar-foreground focus-visible:ring-1 focus-visible:ring-ring/50"
      >
        <FolderGlyph className="h-4 w-4" strokeWidth={1.75} aria-hidden />
      </button>
      <button
        type="button"
        onClick={onOpen ?? onToggle}
        className="h-full min-w-0 flex-1 truncate rounded-md text-left outline-none focus-visible:ring-1 focus-visible:ring-ring/50"
      >
        {label}
      </button>
      {onNewChat || hasMenu ? (
        <span
          className={cn(
            "ml-auto flex shrink-0 items-center gap-px opacity-0 transition-opacity",
            "group-hover:opacity-100 focus-within:opacity-100 has-[[data-state=open]]:opacity-100",
            "[@media(hover:none)]:opacity-100",
          )}
        >
          {onNewChat ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onNewChat();
              }}
              aria-label={t("sidebar.newAgent")}
              title={t("sidebar.newAgent")}
              className={ROW_ACTION_CLASS}
            >
              <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden />
            </button>
          ) : null}
          {hasMenu ? (
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger
                className={ROW_ACTION_CLASS}
                aria-label={t("chat.actions", { title: label })}
                onClick={(event) => event.stopPropagation()}
              >
                <MoreHorizontal className="h-3.5 w-3.5" aria-hidden />
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="end"
                className={ACTION_MENU_CONTENT_CLASS}
                portalContainer={actionMenuPortalContainer}
                onCloseAutoFocus={(event) => event.preventDefault()}
              >
                {onRequestRename ? (
                  <DropdownMenuItem onSelect={onRequestRename} className={ACTION_MENU_ITEM_CLASS}>
                    <Pencil className="h-4 w-4 shrink-0" />
                    {t("chat.rename")}
                  </DropdownMenuItem>
                ) : null}
                {onRequestArchive ? (
                  <DropdownMenuItem onSelect={onRequestArchive} className={ACTION_MENU_ITEM_CLASS}>
                    <Archive className="h-4 w-4 shrink-0" />
                    {t("chat.archive", { defaultValue: "Archiver" })}
                  </DropdownMenuItem>
                ) : null}
                {onRequestDelete ? (
                  <DropdownMenuItem
                    onSelect={() => {
                      window.setTimeout(() => onRequestDelete(), 0);
                    }}
                    className={cn(
                      ACTION_MENU_ITEM_CLASS,
                      "text-destructive focus:text-destructive",
                    )}
                  >
                    <Trash2 className="h-4 w-4 shrink-0" />
                    {t("chat.delete")}
                  </DropdownMenuItem>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}

/**
 * Cursor-style "agent running" glyph at the trailing edge of a chat row:
 * a 2x2 grid of dots where one dot dims at a time, going clockwise. Takes
 * the timestamp's slot while the run lasts. Static dots under reduced motion.
 */
const RUN_DOT_DELAYS_MS = [0, 300, 900, 600]; // DOM order TL, TR, BL, BR -> clockwise

export function RunningDots({
  label,
  className,
}: {
  label: string;
  className?: string;
}) {
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-testid="chat-running-dots"
      className={cn("grid h-4 w-4 shrink-0 place-items-center", className)}
    >
      <span className="grid grid-cols-2 gap-[3px]">
        {RUN_DOT_DELAYS_MS.map((delay) => (
          <span
            key={delay}
            className="h-[3px] w-[3px] rounded-full bg-current animate-[chat-run-dot_1.2s_ease-in-out_infinite] motion-reduce:animate-none"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </span>
    </span>
  );
}

/**
 * Leading 16px column: unread dot when the chat has new activity, otherwise
 * a grip so the row can be dragged. Pinned chats keep the grip visible.
 */
function ChatRowHandle({
  unread,
  pinned,
  active,
  dragLabel,
}: {
  unread: boolean;
  pinned: boolean;
  active: boolean;
  dragLabel: string;
}) {
  const { t } = useTranslation();

  if (unread) {
    const label = t("chat.activity.updated");
    return (
      <span
        role="img"
        aria-label={label}
        title={label}
        className="grid h-4 w-4 shrink-0 place-items-center"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-sidebar-foreground/80" />
      </span>
    );
  }

  return (
    <span
      aria-label={dragLabel}
      title={dragLabel}
      data-testid="chat-drag-handle"
      className={cn(
        "grid h-4 w-4 shrink-0 cursor-grab place-items-center active:cursor-grabbing",
        "text-muted-foreground/40 group-hover:text-muted-foreground/80",
        pinned && "text-muted-foreground/70",
        active && "text-sidebar-foreground/55",
      )}
    >
      <GripVertical className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
    </span>
  );
}

function ChatsFoldFooter({
  hiddenCount,
  onToggle,
}: {
  hiddenCount: number;
  onToggle: () => void;
}) {
  const { t } = useTranslation();

  return (
    <button
      type="button"
      onClick={onToggle}
      className={cn(
        ROW_CLASS,
        "h-7 w-full text-muted-foreground/70 outline-none hover:bg-sidebar-accent/50 hover:text-sidebar-foreground focus-visible:ring-1 focus-visible:ring-ring/50",
      )}
    >
      <span className="h-4 w-4 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 truncate text-left">
        {t("chat.showMore", { count: hiddenCount })}
      </span>
    </button>
  );
}
