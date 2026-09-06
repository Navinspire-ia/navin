import { useCallback, useEffect, useMemo, useState } from "react";
import type { TFunction } from "i18next";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Folder,
  FolderOpen,
  FolderSearch,
  GitBranch,
  HardDrive,
  Home,
  Loader2,
  Monitor,
  Server,
  SquareTerminal,
  UserRound,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ApiError, cloneGitProject, fetchFsList, fetchFsRoots } from "@/lib/api";
import { nativeFolderPickerAvailable, pickNativeFolder } from "@/lib/native-dialog";
import type { FsRootEntry, FsRootsPayload, RecentProjectEntry } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  exampleProjectPath,
  isNavinInternalPath,
  pathCrumbs,
  projectEnvironmentLabel,
  projectNameFromPath,
  shortWorkspacePath,
} from "@/lib/workspace";
import { useClient } from "@/providers/ClientProvider";

export function rootIcon(kind: string) {
  switch (kind) {
    case "home":
      return Home;
    case "windows-home":
      return UserRound;
    case "windows":
      return Monitor;
    case "workspace":
      return FolderOpen;
    case "disk":
    case "drive":
    case "volume":
    case "volumes":
      return HardDrive;
    case "wsl":
      return SquareTerminal;
    case "system":
      return Server;
    default:
      return Folder;
  }
}

/**
 * The label of a quick root in the user's language. The backend sends the
 * English wording plus the varying part (`name`: drive letter, volume or
 * distribution); known kinds are reworded here, anything else keeps its label.
 */
export function rootLabel(root: FsRootEntry, t: TFunction): string {
  const name = root.name ?? "";
  switch (root.kind) {
    case "workspace":
      return t("dev.project.roots.projects", { defaultValue: "Projects" });
    case "home":
      return t("dev.project.roots.home", { defaultValue: "Home" });
    case "windows-home":
      return t("dev.project.roots.windowsHome", { defaultValue: "Windows home" });
    case "windows":
      return name
        ? t("dev.project.roots.windowsDrive", { defaultValue: "Windows ({{name}}:)", name })
        : root.label;
    case "disk":
      return name
        ? t("dev.project.roots.disk", { defaultValue: "Disk ({{name}}:)", name })
        : root.label;
    case "wsl":
      return name ? `WSL: ${name}` : root.label;
    default:
      return root.label;
  }
}

/** Path placeholder for the manual input, shaped like the gateway host's paths. */
export function rootsPathPlaceholder(roots: FsRootsPayload | null, fallback: string): string {
  const home = roots?.roots.find((root) => root.kind === "home")?.path;
  return exampleProjectPath(home) ?? fallback;
}

export function DevProjectSelector({
  projectPath,
  projectName,
  recentProjects,
  disabled,
  onSelectProject,
  compact,
  variant = "menu",
  triggerClassName,
}: {
  projectPath: string | null;
  projectName?: string | null;
  recentProjects: RecentProjectEntry[];
  disabled?: boolean;
  onSelectProject: (path: string, name?: string) => void;
  compact?: boolean;
  /** `empty` = Open folder + Import Git visible on the editor empty state. */
  variant?: "menu" | "empty";
  /** Overrides on the menu trigger, e.g. to render it as a plain sidebar row. */
  triggerClassName?: string;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [browseStartPath, setBrowseStartPath] = useState<string | null>(null);
  const [pathDraft, setPathDraft] = useState("");
  const [gitUrlDraft, setGitUrlDraft] = useState("");
  const [gitError, setGitError] = useState<string | null>(null);
  const [cloning, setCloning] = useState(false);
  const [roots, setRoots] = useState<FsRootsPayload | null>(null);

  // The internal .navin storage is never presented as a project: when the
  // session silently fell back to it, behave as if no project were open.
  const effectiveProjectPath =
    projectPath && !isNavinInternalPath(projectPath) ? projectPath : null;

  useEffect(() => {
    if ((!menuOpen && variant !== "empty") || roots || !token) return;
    void fetchFsRoots(token)
      .then(setRoots)
      .catch(() => setRoots(null));
  }, [menuOpen, roots, token, variant]);

  useEffect(() => {
    if (menuOpen) {
      setPathDraft("");
      setGitUrlDraft("");
      setGitError(null);
    }
  }, [menuOpen]);

  const label = effectiveProjectPath
    ? projectName || projectNameFromPath(effectiveProjectPath)
    : null;
  // "WSL: Ubuntu", "Windows (C:)" for a project on a mounted drive, "macOS",
  // "Linux", "Windows": the host the gateway runs on, never a fixed guess.
  const envLabel = roots
    ? projectEnvironmentLabel({
        projectPath: effectiveProjectPath,
        environment: roots.environment,
        distro: roots.distro,
      })
    : null;
  const projectsRoot = useMemo(
    () => roots?.roots.find((root) => root.kind === "workspace")?.path ?? null,
    [roots],
  );
  const pathPlaceholder = rootsPathPlaceholder(
    roots,
    t("dev.project.pathPlaceholder", { defaultValue: "/absolute/path/to/project" }),
  );

  const applyPath = useCallback(
    (path: string, name?: string) => {
      const trimmed = path.trim();
      if (!trimmed) return;
      onSelectProject(trimmed, name);
      setMenuOpen(false);
      setBrowseOpen(false);
    },
    [onSelectProject],
  );

  /** Native OS picker in the desktop shell, in-app browser everywhere else. */
  const openFolderPicker = useCallback(
    async (start: string | null) => {
      if (nativeFolderPickerAvailable()) {
        try {
          const picked = await pickNativeFolder(start);
          if (picked) applyPath(picked);
          // A cancel is an answer: opening the in-app browser behind it would
          // look like the dialog refused to close.
          setMenuOpen(false);
          return;
        } catch {
          // No bridge, or the command was refused: fall through.
        }
      }
      setBrowseStartPath(start);
      setBrowseOpen(true);
      setMenuOpen(false);
    },
    [applyPath],
  );

  const importFromGit = useCallback(async () => {
    const url = gitUrlDraft.trim();
    if (!url || !token || cloning) return;
    setCloning(true);
    setGitError(null);
    try {
      const result = await cloneGitProject(token, {
        url,
        parent: projectsRoot ?? undefined,
      });
      applyPath(result.path, result.name);
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
  }, [applyPath, cloning, gitUrlDraft, projectsRoot, t, token]);

  const visibleRecents = useMemo(
    () =>
      recentProjects
        .filter(
          (entry) =>
            entry.path !== effectiveProjectPath && !isNavinInternalPath(entry.path),
        )
        .slice(0, 6),
    [effectiveProjectPath, recentProjects],
  );

  if (variant === "empty") {
    return (
      <>
        <div className="flex w-full max-w-md flex-col items-stretch gap-2.5 text-left">
          <div className="flex flex-wrap items-center justify-center gap-2">
            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                void openFolderPicker(effectiveProjectPath ?? projectsRoot);
              }}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border/55 bg-background px-3 text-[12.5px] font-medium text-foreground transition-colors hover:bg-muted/70"
            >
              <FolderSearch className="h-4 w-4 shrink-0 text-primary" aria-hidden />
              {t("dev.project.openFolder", { defaultValue: "Open folder" })}
            </button>
            <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  disabled={disabled}
                  title={effectiveProjectPath ?? undefined}
                  aria-label={t("dev.project.selectorAria", {
                    defaultValue: "Select project",
                  })}
                  className={cn(
                    "inline-flex h-9 min-w-0 max-w-[14rem] items-center gap-1.5 rounded-lg border border-border/55",
                    "bg-background/70 px-2.5 text-[12.5px] font-medium text-foreground/85 transition-colors",
                    "hover:bg-muted/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-55",
                  )}
                >
                  <FolderOpen className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                  <span className="min-w-0 truncate">
                    {label ?? t("dev.project.none", { defaultValue: "Open project" })}
                  </span>
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="center"
                side="bottom"
                sideOffset={8}
                className="w-[min(22rem,calc(100vw-2rem))] rounded-2xl"
              >
                {visibleRecents.length ? (
                  <>
                    <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground">
                      {t("dev.project.recent", { defaultValue: "Recent projects" })}
                    </DropdownMenuLabel>
                    {visibleRecents.map((entry) => (
                      <DropdownMenuItem
                        key={entry.path}
                        onSelect={() => applyPath(entry.path, entry.name)}
                        className="flex cursor-default gap-2.5 rounded-xl px-2.5 py-2"
                      >
                        <Clock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                        <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium">
                          {entry.name || projectNameFromPath(entry.path)}
                        </span>
                      </DropdownMenuItem>
                    ))}
                  </>
                ) : (
                  <div className="px-2.5 py-2 text-[12px] text-muted-foreground">
                    {t("dev.project.noRecent", { defaultValue: "No recent projects yet." })}
                  </div>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          <form
            className="flex items-center gap-1.5"
            onSubmit={(event) => {
              event.preventDefault();
              void importFromGit();
            }}
          >
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
              className="h-9 rounded-lg border-border/55 bg-background/80 px-2.5 text-[12.5px]"
            />
            <Button
              type="submit"
              disabled={disabled || cloning || !gitUrlDraft.trim()}
              className="h-9 shrink-0 rounded-lg px-3 text-[12px]"
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
          </form>
          {projectsRoot ? (
            <p className="text-center text-[10.5px] text-muted-foreground">
              {t("dev.project.gitImportHint", {
                defaultValue: "Clones into {{folder}}",
                folder: shortWorkspacePath(projectsRoot),
              })}
            </p>
          ) : null}
          {gitError ? (
            <p role="alert" className="text-center text-[11.5px] font-medium text-destructive">
              {gitError}
            </p>
          ) : null}
        </div>
        <FolderBrowserDialog
          open={browseOpen}
          startPath={browseStartPath}
          onOpenChange={setBrowseOpen}
          onPick={(path) => applyPath(path)}
        />
      </>
    );
  }

  return (
    <>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            title={
              label && effectiveProjectPath
                ? `${label} - ${effectiveProjectPath}`
                : (effectiveProjectPath ?? label ?? undefined)
            }
            aria-label={
              label
                ? `${t("dev.project.selectorAria", { defaultValue: "Select project" })}: ${label}`
                : t("dev.project.selectorAria", { defaultValue: "Select project" })
            }
            className={cn(
              "inline-flex h-7 items-center rounded-lg border border-border/50",
              "bg-background/70 text-[12px] font-medium text-foreground/85 transition-colors",
              "hover:bg-muted/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-55",
              compact
                ? "gap-0.5 px-1.5"
                : "min-w-0 max-w-[16rem] gap-1.5 px-2.5",
              triggerClassName,
            )}
          >
            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
            {compact ? null : (
              <span className="min-w-0 truncate">
                {label ?? t("dev.project.none", { defaultValue: "Open project" })}
              </span>
            )}
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          side="bottom"
          sideOffset={8}
          className="max-h-[min(var(--radix-dropdown-menu-content-available-height),48rem)] w-[min(26rem,calc(100vw-2rem))] rounded-2xl"
        >
          {effectiveProjectPath ? (
            <>
              <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {t("dev.project.current", { defaultValue: "Current project" })}
                {envLabel ? ` - ${envLabel}` : ""}
              </DropdownMenuLabel>
              <div className="flex items-center gap-2 px-2 pb-1.5 text-[12.5px]">
                <Check className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
                <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                  {shortWorkspacePath(effectiveProjectPath)}
                </span>
              </div>
              <DropdownMenuSeparator />
            </>
          ) : null}

          {visibleRecents.length ? (
            <>
              <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {t("dev.project.recent", { defaultValue: "Recent projects" })}
              </DropdownMenuLabel>
              {visibleRecents.map((entry) => (
                <DropdownMenuItem
                  key={entry.path}
                  onSelect={() => applyPath(entry.path, entry.name)}
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
              <DropdownMenuSeparator />
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
                    onSelect={(event) => {
                      event.preventDefault();
                      void openFolderPicker(root.path);
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
              <DropdownMenuSeparator />
            </>
          ) : null}

          <div
            className="space-y-1.5 px-1.5 py-1.5"
            onKeyDown={(event) => {
              if (event.key !== "Escape") event.stopPropagation();
            }}
          >
            <form
              className="flex items-center gap-1.5"
              onSubmit={(event) => {
                event.preventDefault();
                applyPath(pathDraft);
              }}
            >
              <Input
                value={pathDraft}
                onChange={(event) => setPathDraft(event.target.value)}
                placeholder={pathPlaceholder}
                className="h-8 rounded-lg border-border/55 bg-background/80 px-2.5 text-[12px]"
              />
              <Button
                type="submit"
                disabled={!pathDraft.trim()}
                className="h-8 shrink-0 rounded-lg px-2.5 text-[11.5px]"
              >
                {t("dev.project.open", { defaultValue: "Open" })}
              </Button>
            </form>
            <Button
              type="button"
              variant="outline"
              className="h-8 w-full rounded-lg text-[12px]"
              onClick={() => {
                void openFolderPicker(effectiveProjectPath ?? null);
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
              <div className="flex items-center gap-1.5">
                <Input
                  value={gitUrlDraft}
                  disabled={cloning}
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
                  className="h-8 rounded-lg border-border/55 bg-background/80 px-2.5 text-[12px]"
                />
                <Button
                  type="submit"
                  disabled={cloning || !gitUrlDraft.trim()}
                  className="h-8 shrink-0 rounded-lg px-2.5 text-[11.5px]"
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
                <p className="px-0.5 text-[10.5px] text-muted-foreground">
                  {t("dev.project.gitImportHint", {
                    defaultValue: "Clones into {{folder}}",
                    folder: shortWorkspacePath(projectsRoot),
                  })}
                </p>
              ) : null}
              {gitError ? (
                <p role="alert" className="px-0.5 text-[11.5px] font-medium text-destructive">
                  {gitError}
                </p>
              ) : null}
            </form>
          </div>
        </DropdownMenuContent>
      </DropdownMenu>

      <FolderBrowserDialog
        open={browseOpen}
        startPath={browseStartPath}
        onOpenChange={setBrowseOpen}
        onPick={(path) => applyPath(path)}
      />
    </>
  );
}

export function FolderBrowserDialog({
  open,
  startPath,
  onOpenChange,
  onPick,
}: {
  open: boolean;
  startPath: string | null;
  onOpenChange: (open: boolean) => void;
  onPick: (path: string) => void;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const [currentPath, setCurrentPath] = useState<string | null>(null);
  const [parent, setParent] = useState<string | null>(null);
  const [dirs, setDirs] = useState<Array<{ name: string; path: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [roots, setRoots] = useState<FsRootsPayload | null>(null);

  const load = useCallback(
    async (path: string) => {
      if (!token) return;
      setLoading(true);
      setError(null);
      try {
        const payload = await fetchFsList(token, path);
        setCurrentPath(payload.path);
        setParent(payload.parent);
        setDirs(payload.directories);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [token],
  );

  useEffect(() => {
    if (!open) return;
    void load(startPath || "~");
  }, [load, open, startPath]);

  // Quick roots inside the dialog, not only in the dropdown that opened it. A
  // drive root has no parent, so without them Windows users who walked down to
  // C:\ had no way back out to D: without cancelling.
  useEffect(() => {
    if (!open || roots || !token) return;
    void fetchFsRoots(token)
      .then(setRoots)
      .catch(() => setRoots(null));
  }, [open, roots, token]);

  const crumbs = useMemo(() => pathCrumbs(currentPath).slice(-4), [currentPath]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[calc(100vw-2rem)] overflow-hidden sm:max-w-[520px]">
        <DialogHeader className="min-w-0">
          <DialogTitle>
            {t("dev.project.browseTitle", { defaultValue: "Select project folder" })}
          </DialogTitle>
          <DialogDescription className="truncate" title={currentPath ?? undefined}>
            {currentPath ?? "…"}
          </DialogDescription>
        </DialogHeader>
        <div className="min-w-0 space-y-2">
          {roots?.roots.length ? (
            <div className="flex flex-wrap items-center gap-1">
              {roots.roots.map((root: FsRootEntry) => {
                const Icon = rootIcon(root.kind);
                return (
                  <button
                    key={`${root.kind}:${root.path}`}
                    type="button"
                    title={root.path}
                    onClick={() => void load(root.path)}
                    className={cn(
                      "inline-flex h-7 max-w-[12rem] items-center gap-1.5 rounded-lg border px-2",
                      "text-[11.5px] transition-colors",
                      currentPath === root.path
                        ? "border-border bg-muted text-foreground"
                        : "border-border/50 text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
                    <span className="min-w-0 truncate">{rootLabel(root, t)}</span>
                  </button>
                );
              })}
            </div>
          ) : null}
          <div className="flex min-w-0 flex-wrap items-center gap-1 text-[12px] text-muted-foreground">
            {crumbs.map((crumb, index) => (
              <span key={crumb.path} className="flex min-w-0 items-center gap-1">
                {index > 0 ? <ChevronRight className="h-3 w-3 shrink-0" aria-hidden /> : null}
                <button
                  type="button"
                  className="max-w-[14rem] truncate rounded px-1 py-0.5 hover:bg-muted hover:text-foreground"
                  onClick={() => void load(crumb.path)}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
          </div>
          <div className="h-64 overflow-y-auto overflow-x-hidden rounded-xl border border-border/50 bg-background/60">
            {loading ? (
              <div className="flex items-center gap-2 px-3 py-4 text-[13px] text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {t("dev.project.loading", { defaultValue: "Loading…" })}
              </div>
            ) : error ? (
              <p className="px-3 py-4 text-[12.5px] text-destructive">{error}</p>
            ) : (
              <ul className="py-1">
                {parent ? (
                  <li>
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12.5px] text-muted-foreground hover:bg-muted/60"
                      onClick={() => void load(parent)}
                    >
                      <Folder className="h-3.5 w-3.5 shrink-0" aria-hidden />
                      ..
                    </button>
                  </li>
                ) : null}
                {dirs.map((dir) => (
                  <li key={dir.path}>
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12.5px] text-foreground hover:bg-muted/60"
                      onClick={() => void load(dir.path)}
                      onDoubleClick={() => onPick(dir.path)}
                    >
                      <Folder className="h-3.5 w-3.5 shrink-0 text-primary/70" aria-hidden />
                      {/* min-w-0 is what lets truncate win: a flex child keeps
                          its min-content width otherwise, and a long folder
                          name pushes the row past the dialog. */}
                      <span className="min-w-0 flex-1 truncate" title={dir.name}>
                        {dir.name}
                      </span>
                    </button>
                  </li>
                ))}
                {!dirs.length && !parent ? (
                  <li className="px-3 py-4 text-[12.5px] text-muted-foreground">
                    {t("dev.project.emptyDir", { defaultValue: "No sub-folders." })}
                  </li>
                ) : null}
              </ul>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            {t("common.cancel", { defaultValue: "Cancel" })}
          </Button>
          <Button
            type="button"
            disabled={!currentPath || loading}
            onClick={() => currentPath && onPick(currentPath)}
          >
            {t("dev.project.useFolder", { defaultValue: "Open this folder" })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
