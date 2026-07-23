import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Folder,
  FolderOpen,
  FolderSearch,
  HardDrive,
  Home,
  Loader2,
  Monitor,
  Server,
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
import { fetchFsList, fetchFsRoots } from "@/lib/api";
import type { FsRootEntry, FsRootsPayload, RecentProjectEntry } from "@/lib/types";
import { cn } from "@/lib/utils";
import { projectNameFromPath, shortWorkspacePath } from "@/lib/workspace";
import { useClient } from "@/providers/ClientProvider";

const ENV_LABELS: Record<string, string> = {
  wsl: "WSL",
  linux: "Linux",
  macos: "macOS",
  windows: "Windows",
};

function rootIcon(kind: string) {
  switch (kind) {
    case "home":
      return Home;
    case "windows":
      return Monitor;
    case "workspace":
      return FolderOpen;
    case "volumes":
      return HardDrive;
    case "system":
      return Server;
    default:
      return Folder;
  }
}

export function DevProjectSelector({
  projectPath,
  projectName,
  recentProjects,
  disabled,
  onSelectProject,
}: {
  projectPath: string | null;
  projectName?: string | null;
  recentProjects: RecentProjectEntry[];
  disabled?: boolean;
  onSelectProject: (path: string, name?: string) => void;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [browseStartPath, setBrowseStartPath] = useState<string | null>(null);
  const [pathDraft, setPathDraft] = useState("");
  const [roots, setRoots] = useState<FsRootsPayload | null>(null);

  useEffect(() => {
    if (!menuOpen || roots || !token) return;
    void fetchFsRoots(token)
      .then(setRoots)
      .catch(() => setRoots(null));
  }, [menuOpen, roots, token]);

  useEffect(() => {
    if (menuOpen) setPathDraft("");
  }, [menuOpen]);

  const label = projectName || (projectPath ? projectNameFromPath(projectPath) : null);
  const envLabel = roots ? ENV_LABELS[roots.environment] ?? roots.environment : null;

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

  const visibleRecents = useMemo(
    () => recentProjects.filter((entry) => entry.path !== projectPath).slice(0, 6),
    [projectPath, recentProjects],
  );

  return (
    <>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            title={projectPath ?? undefined}
            aria-label={t("dev.project.selectorAria", { defaultValue: "Select project" })}
            className={cn(
              "inline-flex h-7 max-w-[16rem] items-center gap-1.5 rounded-lg border border-border/50",
              "bg-background/70 px-2.5 text-[12px] font-medium text-foreground/85 transition-colors",
              "hover:bg-muted/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-55",
            )}
          >
            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
            <span className="truncate">
              {label ?? t("dev.project.none", { defaultValue: "Open project" })}
            </span>
            {envLabel ? (
              <span className="rounded bg-muted px-1 py-px text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {envLabel}
              </span>
            ) : null}
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          side="bottom"
          sideOffset={8}
          className="w-[min(24rem,calc(100vw-2rem))] rounded-2xl"
        >
          {projectPath ? (
            <>
              <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {t("dev.project.current", { defaultValue: "Current project" })}
              </DropdownMenuLabel>
              <div className="flex items-center gap-2 px-2 pb-1.5 text-[12.5px]">
                <Check className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
                <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                  {shortWorkspacePath(projectPath)}
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
                      setBrowseStartPath(root.path);
                      setBrowseOpen(true);
                      setMenuOpen(false);
                    }}
                    className="flex cursor-default gap-2.5 rounded-xl px-2.5 py-1.5"
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-foreground">
                      {root.label}
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
                placeholder={t("dev.project.pathPlaceholder", {
                  defaultValue: "/absolute/path/to/project",
                })}
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
                setBrowseStartPath(projectPath ?? null);
                setBrowseOpen(true);
                setMenuOpen(false);
              }}
            >
              <FolderSearch className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              {t("dev.project.browse", { defaultValue: "Browse folders…" })}
            </Button>
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

function FolderBrowserDialog({
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

  const crumbs = useMemo(() => {
    if (!currentPath) return [] as Array<{ label: string; path: string }>;
    const parts = currentPath.split("/").filter(Boolean);
    const out: Array<{ label: string; path: string }> = [{ label: "/", path: "/" }];
    let acc = "";
    for (const part of parts) {
      acc += `/${part}`;
      out.push({ label: part, path: acc });
    }
    return out.slice(-4);
  }, [currentPath]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>
            {t("dev.project.browseTitle", { defaultValue: "Select project folder" })}
          </DialogTitle>
          <DialogDescription className="truncate">
            {currentPath ?? "…"}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1 text-[12px] text-muted-foreground">
            {crumbs.map((crumb, index) => (
              <span key={crumb.path} className="flex items-center gap-1">
                {index > 0 ? <ChevronRight className="h-3 w-3" aria-hidden /> : null}
                <button
                  type="button"
                  className="rounded px-1 py-0.5 hover:bg-muted hover:text-foreground"
                  onClick={() => void load(crumb.path)}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
          </div>
          <div className="h-64 overflow-y-auto rounded-xl border border-border/50 bg-background/60">
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
                      <span className="truncate">{dir.name}</span>
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
