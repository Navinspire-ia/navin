import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Clock,
  Folder,
  FolderSearch,
  GitBranch,
  Hand,
  Loader2,
  Plus,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  FolderBrowserDialog,
  rootIcon,
  rootLabel,
  rootsPathPlaceholder,
} from "@/components/dev/DevProjectSelector";
import { ApiError, cloneGitProject, fetchFsRoots, fetchSidebarState } from "@/lib/api";
import type {
  FsRootEntry,
  FsRootsPayload,
  RecentProjectEntry,
  WorkspaceAccessMode,
  WorkspaceScopePayload,
  WorkspacesPayload,
} from "@/lib/types";
import { getRuntimeHost } from "@/lib/runtime";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";
import {
  isAbsoluteWorkspacePath,
  isNavinInternalPath,
  projectNameFromPath,
  scopeWithAccessMode,
  selectedProjectScope,
  shortWorkspacePath,
} from "@/lib/workspace";

/**
 * Whether the project row is on screen. Exported so the composer can move its
 * own controls into that row only when it actually renders.
 */
export function workspaceProjectPickerVisible({
  isHero,
  defaultScope,
  controls,
  onChange,
}: {
  isHero: boolean;
  defaultScope: WorkspaceScopePayload | null;
  controls: WorkspacesPayload["controls"] | null;
  onChange?: (scope: WorkspaceScopePayload) => void;
}): boolean {
  return isHero && !!defaultScope && !!onChange && controls?.can_change_project !== false;
}

export function WorkspaceProjectPicker({
  isHero,
  disabled,
  scope,
  defaultScope,
  controls,
  error,
  onChange,
  onNewChat,
  trailing,
}: {
  isHero: boolean;
  disabled?: boolean;
  scope: WorkspaceScopePayload | null;
  defaultScope: WorkspaceScopePayload | null;
  controls: WorkspacesPayload["controls"] | null;
  error?: string | null;
  onChange?: (scope: WorkspaceScopePayload) => void;
  onNewChat?: () => void;
  /** Composer controls parked at the right end of this row. */
  trailing?: ReactNode;
}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [open, setOpen] = useState(false);
  const [pathDraft, setPathDraft] = useState("");
  const [pathError, setPathError] = useState<string | null>(null);
  const [gitUrlDraft, setGitUrlDraft] = useState("");
  const [gitError, setGitError] = useState<string | null>(null);
  const [cloning, setCloning] = useState(false);
  const [pickingFolder, setPickingFolder] = useState(false);
  const [roots, setRoots] = useState<FsRootsPayload | null>(null);
  const [recents, setRecents] = useState<RecentProjectEntry[]>([]);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [browseStartPath, setBrowseStartPath] = useState<string | null>(null);
  const currentProjectScope = selectedProjectScope(scope, defaultScope);
  const projectLabel = currentProjectScope
    ? currentProjectScope.project_name || projectNameFromPath(currentProjectScope.project_path)
    : t("thread.composer.workspace.projectPlaceholder");
  const visible = workspaceProjectPickerVisible({ isHero, defaultScope, controls, onChange });
  const trailingTools = trailing ? (
    <div className="ml-auto flex shrink-0 items-center gap-0.5 pl-2">{trailing}</div>
  ) : null;
  const pickFolder = getRuntimeHost().pickFolder;
  const nativeProjectPicker = !!pickFolder;
  const newChatButton = onNewChat ? (
    <button
      type="button"
      disabled={disabled}
      aria-label={t("sidebar.newChat")}
      title={t("sidebar.newChat")}
      onClick={onNewChat}
      className={cn(
        "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2.5",
        "text-[12px] font-medium text-muted-foreground/90 transition-colors",
        "hover:bg-background/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-55",
      )}
    >
      <Plus className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="truncate">{t("sidebar.newChatShort", { defaultValue: "New" })}</span>
    </button>
  ) : null;

  useEffect(() => {
    if (!open) return;
    setPathDraft(currentProjectScope?.project_path ?? "");
    setPathError(null);
    setGitUrlDraft("");
    setGitError(null);
  }, [currentProjectScope?.project_path, open]);

  const projectsRoot = useMemo(
    () => roots?.roots.find((root) => root.kind === "workspace")?.path ?? null,
    [roots],
  );
  const pathPlaceholder = rootsPathPlaceholder(roots, t("workspace.dialog.manualPlaceholder"));

  // Same sources as the dev project selector: filesystem quick-access roots
  // and the shared recent-projects list persisted server-side.
  useEffect(() => {
    if (!open || !token) return;
    if (!roots) {
      void fetchFsRoots(token)
        .then(setRoots)
        .catch(() => setRoots(null));
    }
    void fetchSidebarState(token)
      .then((state) => setRecents(state.recent_projects ?? []))
      .catch(() => setRecents([]));
  }, [open, roots, token]);

  const currentPath = currentProjectScope?.project_path ?? defaultScope?.project_path;
  const visibleRecents = useMemo(
    () =>
      recents
        .filter(
          (entry) =>
            entry.path !== currentPath
            && entry.path !== defaultScope?.project_path
            && !isNavinInternalPath(entry.path),
        )
        .slice(0, 6),
    [currentPath, defaultScope?.project_path, recents],
  );

  useEffect(() => {
    if (error && visible) setOpen(true);
  }, [error, visible]);

  const applyProjectPath = useCallback(
    (projectPath: string, projectName?: string) => {
      const base = scope ?? defaultScope;
      const trimmed = projectPath.trim();
      if (!base || !onChange) return;
      if (!trimmed || !isAbsoluteWorkspacePath(trimmed)) {
        setPathError(t("workspace.dialog.absolutePathRequired"));
        return;
      }
      onChange({
        ...base,
        project_path: trimmed,
        project_name: projectName || projectNameFromPath(trimmed),
        restrict_to_workspace: base.access_mode === "restricted",
      });
      setPathError(null);
      setOpen(false);
    },
    [defaultScope, onChange, scope, t],
  );

  const pickNativeFolder = useCallback(async () => {
    if (!pickFolder || disabled) return;
    setPickingFolder(true);
    try {
      const picked = await pickFolder();
      if (picked) applyProjectPath(picked);
    } catch (err) {
      setPathError((err as Error).message);
    } finally {
      setPickingFolder(false);
    }
  }, [applyProjectPath, disabled, pickFolder]);

  const importFromGit = useCallback(async () => {
    const url = gitUrlDraft.trim();
    if (!url || !token || cloning || disabled) return;
    setCloning(true);
    setGitError(null);
    try {
      const result = await cloneGitProject(token, {
        url,
        parent: projectsRoot ?? undefined,
      });
      applyProjectPath(result.path, result.name);
    } catch (err) {
      let detail =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : String(err);
      if (
        err instanceof ApiError
        && err.status >= 500
        && (!detail || /^HTTP\s*5\d\d$/i.test(detail.trim()))
      ) {
        detail = t("dev.project.gitImportGatewayDown", {
          defaultValue:
            "Gateway unavailable (HTTP {{status}}). Wait a second and retry.",
          status: err.status,
        });
      }
      setGitError(
        t("dev.project.gitImportFailed", {
          defaultValue: "Import failed: {{detail}}",
          detail,
        }),
      );
    } finally {
      setCloning(false);
    }
  }, [applyProjectPath, cloning, disabled, gitUrlDraft, projectsRoot, t, token]);

  if (!visible || !defaultScope || !onChange) return null;

  if (nativeProjectPicker) {
    return (
      <div className="flex min-w-0 items-center rounded-b-[28px] border-t border-border/25 bg-muted/60 px-3 py-1.5 dark:bg-white/[0.055] sm:px-4">
        <button
          type="button"
          disabled={disabled || pickingFolder}
          aria-label={t("thread.composer.workspace.projectAria")}
          title={currentProjectScope?.project_path}
          onClick={() => void pickNativeFolder()}
          className={cn(
            "inline-flex h-7 max-w-full items-center gap-2 rounded-full px-2.5 sm:max-w-[18rem]",
            "text-[12px] font-medium text-muted-foreground/90 transition-colors",
            "hover:bg-background/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-55",
            currentProjectScope && "text-foreground/82",
          )}
        >
          <Folder className={cn("h-3.5 w-3.5 shrink-0", currentProjectScope && "text-primary")} />
          <span className="truncate">{projectLabel}</span>
        </button>
        {newChatButton}
        {pathError || error ? (
          <span role="alert" className="ml-2 min-w-0 truncate text-[11.5px] font-medium text-amber-700 dark:text-amber-300">
            {pathError ?? error}
          </span>
        ) : null}
        {trailingTools}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 items-center rounded-b-[28px] border-t border-border/25 bg-muted/60 px-3 py-1.5 dark:bg-white/[0.055] sm:px-4">
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            aria-label={t("thread.composer.workspace.projectAria")}
            className={cn(
              "inline-flex h-7 max-w-full items-center gap-2 rounded-full px-2.5 sm:max-w-[18rem]",
              "text-[12px] font-medium text-muted-foreground/90 transition-colors",
              "hover:bg-background/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-55",
              currentProjectScope && "text-foreground/82",
            )}
          >
            <Folder className={cn("h-3.5 w-3.5 shrink-0", currentProjectScope && "text-primary")} />
            <span className="truncate">{projectLabel}</span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          side="bottom"
          sideOffset={8}
          className="max-h-[min(var(--radix-dropdown-menu-content-available-height),48rem)] w-[min(25rem,calc(100vw-2rem))] overflow-y-auto rounded-[22px]"
        >
          <DropdownMenuItem
            onSelect={() => applyProjectPath(defaultScope.project_path, defaultScope.project_name)}
            className="flex min-h-[48px] cursor-default gap-3 rounded-[16px] px-3 py-2.5 focus:bg-muted/55"
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[12px] bg-muted text-foreground/80">
              <Folder className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-semibold text-foreground">
                {t("workspace.dialog.defaultProject")}
              </span>
              <span className="block truncate text-[11.5px] text-muted-foreground">
                {shortWorkspacePath(defaultScope.project_path)}
              </span>
            </span>
            {!currentProjectScope ? <Check className="h-4 w-4 text-foreground/80" /> : null}
          </DropdownMenuItem>
          <div className="my-1 h-px bg-border/45" />
          {visibleRecents.length ? (
            <>
              <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {t("dev.project.recent", { defaultValue: "Recent projects" })}
              </DropdownMenuLabel>
              {visibleRecents.map((entry) => (
                <DropdownMenuItem
                  key={entry.path}
                  disabled={disabled}
                  onSelect={() => applyProjectPath(entry.path, entry.name)}
                  className="flex cursor-default gap-2.5 rounded-xl px-2.5 py-2"
                >
                  <Clock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12.5px] font-medium text-foreground">
                      {entry.name || projectNameFromPath(entry.path)}
                    </span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {shortWorkspacePath(entry.path)}
                    </span>
                  </span>
                </DropdownMenuItem>
              ))}
              <div className="my-1 h-px bg-border/45" />
            </>
          ) : null}
          {roots?.roots.length ? (
            <>
              <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {t("dev.project.quickAccess", { defaultValue: "Quick access" })}
              </DropdownMenuLabel>
              {roots.roots.map((root: FsRootEntry) => {
                const Icon = rootIcon(root.kind);
                return (
                  <DropdownMenuItem
                    key={`${root.kind}:${root.path}`}
                    disabled={disabled}
                    onSelect={(event) => {
                      event.preventDefault();
                      setBrowseStartPath(root.path);
                      setBrowseOpen(true);
                      setOpen(false);
                    }}
                    className="flex cursor-default gap-2.5 rounded-xl px-2.5 py-1.5"
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-foreground">
                      {rootLabel(root, t)}
                    </span>
                    <span className="max-w-[9rem] truncate text-[11px] text-muted-foreground">
                      {root.path}
                    </span>
                  </DropdownMenuItem>
                );
              })}
              <div className="my-1 h-px bg-border/45" />
            </>
          ) : null}
          <div
            className="space-y-1.5 px-1.5 py-1.5"
            onKeyDown={(event) => {
              if (event.key !== "Escape") event.stopPropagation();
            }}
          >
            <form
              className="flex items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                applyProjectPath(pathDraft);
              }}
            >
              <Input
                value={pathDraft}
                disabled={disabled}
                onChange={(event) => {
                  setPathDraft(event.target.value);
                  setPathError(null);
                }}
                placeholder={pathPlaceholder}
                aria-label={t("workspace.dialog.manual")}
                className={cn(
                  "h-9 rounded-full border-border/55 bg-background/80 px-3 text-[12.5px]",
                  "focus-visible:ring-1 focus-visible:ring-foreground/10 focus-visible:ring-offset-0",
                )}
              />
              <Button
                type="submit"
                disabled={disabled || !pathDraft.trim()}
                className="h-9 shrink-0 rounded-full px-3 text-[12px]"
              >
                {t("workspace.dialog.usePath")}
              </Button>
            </form>
            {pathError || error ? (
              <p role="alert" className="px-1 text-[11.5px] font-medium text-amber-700 dark:text-amber-300">
                {pathError ?? error}
              </p>
            ) : null}
            <Button
              type="button"
              variant="outline"
              disabled={disabled}
              className="h-9 w-full rounded-full text-[12px]"
              onClick={() => {
                setBrowseStartPath(currentPath ?? null);
                setBrowseOpen(true);
                setOpen(false);
              }}
            >
              <FolderSearch className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              {t("dev.project.browse", { defaultValue: "Browse folders…" })}
            </Button>
            <form
              className="space-y-1.5"
              onSubmit={(event) => {
                event.preventDefault();
                void importFromGit();
              }}
            >
              <div className="flex items-center gap-2">
                <Input
                  value={gitUrlDraft}
                  disabled={disabled || cloning}
                  onChange={(event) => {
                    setGitUrlDraft(event.target.value);
                    setGitError(null);
                  }}
                  placeholder={t("dev.project.gitUrlPlaceholder", {
                    defaultValue: "https://github.com/org/repo.git",
                  })}
                  aria-label={t("dev.project.gitImport", {
                    defaultValue: "Import from Git",
                  })}
                  className={cn(
                    "h-9 rounded-full border-border/55 bg-background/80 px-3 text-[12.5px]",
                    "focus-visible:ring-1 focus-visible:ring-foreground/10 focus-visible:ring-offset-0",
                  )}
                />
                <Button
                  type="submit"
                  disabled={disabled || cloning || !gitUrlDraft.trim()}
                  className="h-9 shrink-0 rounded-full px-3 text-[12px]"
                >
                  {cloning ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <GitBranch className="mr-1 h-3.5 w-3.5" aria-hidden />
                  )}
                  {cloning
                    ? t("dev.project.gitImporting", { defaultValue: "Cloning…" })
                    : t("dev.project.gitImport", { defaultValue: "Import Git" })}
                </Button>
              </div>
              {projectsRoot ? (
                <p className="px-1 text-[10.5px] text-muted-foreground">
                  {t("dev.project.gitImportHint", {
                    defaultValue: "Clones into {{folder}}",
                    folder: shortWorkspacePath(projectsRoot),
                  })}
                </p>
              ) : null}
              {gitError ? (
                <p role="alert" className="px-1 text-[11.5px] font-medium text-amber-700 dark:text-amber-300">
                  {gitError}
                </p>
              ) : null}
            </form>
          </div>
        </DropdownMenuContent>
      </DropdownMenu>
      {newChatButton}
      {trailingTools}
      <FolderBrowserDialog
        open={browseOpen}
        startPath={browseStartPath}
        onOpenChange={setBrowseOpen}
        onPick={(path) => {
          applyProjectPath(path);
          setBrowseOpen(false);
        }}
      />
    </div>
  );
}

export function WorkspaceAccessMenu({
  scope,
  disabled,
  canUseFullAccess,
  isHero,
  onChange,
}: {
  scope: WorkspaceScopePayload;
  disabled?: boolean;
  canUseFullAccess: boolean;
  isHero: boolean;
  onChange?: (scope: WorkspaceScopePayload) => void;
}) {
  const { t } = useTranslation();
  const mode = scope.access_mode;
  const isFull = mode === "full";

  const setMode = (value: WorkspaceAccessMode) => {
    if (value === "full" && !canUseFullAccess) return;
    if (value === mode) return;
    onChange?.(scopeWithAccessMode(scope, value));
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={disabled || !onChange}>
        <Button
          type="button"
          variant="ghost"
          aria-label={t("thread.composer.workspace.accessAria")}
          className={cn(
            "max-w-[min(12.5rem,42vw)] rounded-[10px] border border-transparent font-semibold shadow-none",
            isHero ? "h-8 px-2.5 text-[12px]" : "h-9 px-3 text-[12.5px]",
            isFull
              ? "bg-transparent font-semibold text-foreground hover:bg-foreground/[0.06]"
              : "bg-transparent text-muted-foreground hover:bg-foreground/[0.045] hover:text-foreground dark:hover:bg-white/[0.06]",
          )}
        >
          {isFull ? (
            <AlertTriangle className={cn("mr-1.5 shrink-0", isHero ? "h-3.5 w-3.5" : "h-3.5 w-3.5")} />
          ) : (
            <Hand className={cn("mr-1.5 shrink-0", isHero ? "h-3.5 w-3.5" : "h-3.5 w-3.5")} />
          )}
          <span className="truncate">
            {t(isFull ? "thread.composer.workspace.full" : "thread.composer.workspace.default")}
          </span>
          <ChevronDown className={cn("ml-1.5 shrink-0", isHero ? "h-3 w-3" : "h-3 w-3")} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <AccessMenuItem
          icon={<Hand className="h-4 w-4" />}
          label={t("thread.composer.workspace.default")}
          selected={mode === "restricted"}
          onSelect={() => setMode("restricted")}
        />
        <AccessMenuItem
          icon={<AlertTriangle className="h-4 w-4" />}
          label={t("thread.composer.workspace.full")}
          selected={mode === "full"}
          disabled={!canUseFullAccess}
          warning
          onSelect={() => setMode("full")}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function AccessMenuItem({
  icon,
  label,
  selected,
  disabled,
  warning,
  onSelect,
}: {
  icon: ReactNode;
  label: string;
  selected: boolean;
  disabled?: boolean;
  warning?: boolean;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        "flex h-10 items-center gap-3 rounded-xl px-3 text-[13.5px] font-semibold",
        warning && "font-semibold text-foreground focus:text-foreground",
      )}
    >
      <span className="grid h-5 w-5 shrink-0 place-items-center text-current" aria-hidden>
        {icon}
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {selected ? <Check className="h-4 w-4 shrink-0" aria-hidden /> : null}
    </DropdownMenuItem>
  );
}
