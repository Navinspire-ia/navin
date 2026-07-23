import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  Code2,
  File,
  FileWarning,
  Folder,
  FolderOpen,
  GitBranch,
  Globe,
  Loader2,
  Maximize2,
  Minimize2,
  MonitorSmartphone,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RefreshCw,
  Save,
  Search,
  TerminalSquare,
  Waypoints,
  X,
  XCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DevProjectActions } from "@/components/dev/DevProjectActions";
import { DevProjectSelector } from "@/components/dev/DevProjectSelector";
import { DevGitPanel } from "@/components/dev/DevGitPanel";
import { DevSearchPanel } from "@/components/dev/DevSearchPanel";
import { DevDiffView } from "@/components/dev/DevDiffView";
import {
  fetchDevFilePreview,
  fetchFileDiagnostics,
  fetchFileTree,
  fetchFsRoots,
  fetchGitStatus,
  saveWorkspaceFile,
} from "@/lib/api";
import type {
  FileDiagnosticsPayload,
  FilePreviewPayload,
  FileTreeEntry,
  GitStatusPayload,
  RecentProjectEntry,
  TerminalShellInfo,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useThemeValue } from "@/hooks/useTheme";
import { useClient } from "@/providers/ClientProvider";

const CodeEditor = lazy(() => import("./CodeEditor"));
const DevTerminal = lazy(() => import("./DevTerminal"));
const DevMetagraph = lazy(() => import("./DevMetagraph"));

type TerminalTab = {
  id: string;
  shell?: string;
  title: string;
  exited: boolean;
};

let terminalCounter = 0;

type OpenTab = {
  path: string;
  displayPath: string;
  name: string;
};

type TreeNodeState = {
  entries: FileTreeEntry[];
  loading: boolean;
  error: string | null;
};

const PREVIEW_DEFAULT_URL = "http://127.0.0.1:5173";

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function DevWorkbench({
  sessionKey,
  projectPath,
  projectName,
  recentProjects,
  onSelectProject,
  openFileRequest,
  chatOpen,
  onToggleChat,
  onRunAction,
}: {
  sessionKey: string | null;
  projectPath: string | null;
  projectName?: string | null;
  recentProjects?: RecentProjectEntry[];
  onSelectProject?: (path: string, name?: string) => void;
  openFileRequest?: { path: string; nonce: number } | null;
  chatOpen?: boolean;
  onToggleChat?: () => void;
  onRunAction?: (text: string) => void;
}) {
  const { token, client } = useClient();
  const theme = useThemeValue();
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const treeKey = sessionKey ?? "websocket:webui-dev";

  const [rootPath, setRootPath] = useState<string | null>(null);
  const [nodes, setNodes] = useState<Record<string, TreeNodeState>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [tabs, setTabs] = useState<OpenTab[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [previews, setPreviews] = useState<Record<string, FilePreviewPayload>>({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [mode, setMode] = useState<"code" | "browser" | "graph" | "diff">("code");
  const [panelTab, setPanelTab] = useState<"files" | "search" | "git">("files");
  const [diffFile, setDiffFile] = useState<string | null>(null);
  const [reveal, setReveal] = useState<{ path: string; line: number; nonce: number } | null>(null);
  const [browserUrl, setBrowserUrl] = useState(PREVIEW_DEFAULT_URL);
  const [browserSrc, setBrowserSrc] = useState<string | null>(null);
  const [browserNonce, setBrowserNonce] = useState(0);
  const chatId = sessionKey?.startsWith("websocket:")
    ? sessionKey.slice("websocket:".length)
    : null;
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [terminals, setTerminals] = useState<TerminalTab[]>([]);
  const [activeTerminal, setActiveTerminal] = useState<string | null>(null);
  const [shells, setShells] = useState<TerminalShellInfo[]>([]);
  const [shellMenuOpen, setShellMenuOpen] = useState(false);

  // Status bar: environment, git, per-file diagnostics.
  const [environment, setEnvironment] = useState<string | null>(null);
  const [gitStatus, setGitStatus] = useState<GitStatusPayload | null>(null);
  const [diagnosticsByPath, setDiagnosticsByPath] = useState<
    Record<string, FileDiagnosticsPayload>
  >({});

  useEffect(() => {
    let cancelled = false;
    void fetchFsRoots(token)
      .then((payload) => {
        if (!cancelled) setEnvironment(payload.environment);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token]);

  const gitRoot = projectPath ?? rootPath;
  const refreshGitStatus = useCallback(() => {
    if (!gitRoot) return;
    void fetchGitStatus(token, gitRoot)
      .then(setGitStatus)
      .catch(() => setGitStatus(null));
  }, [gitRoot, token]);

  useEffect(() => {
    setGitStatus(null);
    refreshGitStatus();
    const timer = window.setInterval(refreshGitStatus, 12_000);
    return () => window.clearInterval(timer);
  }, [refreshGitStatus]);

  const refreshDiagnostics = useCallback(
    (path: string) => {
      void fetchFileDiagnostics(token, path)
        .then((payload) =>
          setDiagnosticsByPath((prev) => ({ ...prev, [path]: payload })),
        )
        .catch(() => undefined);
    },
    [token],
  );

  // Layout: collapsible + resizable panes (explorer width, terminal height).
  const [explorerOpen, setExplorerOpen] = useState(true);
  const [explorerWidth, setExplorerWidth] = useState(240);
  const [terminalHeight, setTerminalHeight] = useState(260);
  const [terminalMaximized, setTerminalMaximized] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const dragState = useRef<{ kind: "explorer" | "terminal"; startPos: number; startSize: number } | null>(null);

  const startDrag = useCallback(
    (kind: "explorer" | "terminal") => (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragState.current = {
        kind,
        startPos: kind === "explorer" ? event.clientX : event.clientY,
        startSize: kind === "explorer" ? explorerWidth : terminalHeight,
      };
      const onMove = (ev: PointerEvent) => {
        const drag = dragState.current;
        if (!drag) return;
        if (drag.kind === "explorer") {
          const next = drag.startSize + (ev.clientX - drag.startPos);
          setExplorerWidth(Math.min(520, Math.max(150, next)));
        } else {
          const next = drag.startSize - (ev.clientY - drag.startPos);
          const bound = containerRef.current?.clientHeight ?? 800;
          setTerminalHeight(Math.min(Math.max(120, bound - 120), Math.max(96, next)));
          setTerminalMaximized(false);
        }
      };
      const onUp = () => {
        dragState.current = null;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      document.body.style.cursor = kind === "explorer" ? "col-resize" : "row-resize";
      document.body.style.userSelect = "none";
    },
    [explorerWidth, terminalHeight],
  );

  const spawnTerminal = useCallback(
    (shell?: string) => {
      terminalCounter += 1;
      const id = `term-${Date.now().toString(36)}-${terminalCounter}`;
      const label = shell || shells.find((s) => s.default)?.name || "shell";
      setTerminals((prev) => [
        ...prev,
        { id, shell, title: `${label} ${terminalCounter}`, exited: false },
      ]);
      setActiveTerminal(id);
      setTerminalOpen(true);
      setShellMenuOpen(false);
    },
    [shells],
  );

  const openTerminalPanel = useCallback(() => {
    setTerminalOpen(true);
    setTerminals((prev) => {
      if (prev.length === 0) {
        terminalCounter += 1;
        const id = `term-${Date.now().toString(36)}-${terminalCounter}`;
        const label = shells.find((s) => s.default)?.name || "shell";
        setActiveTerminal(id);
        return [{ id, shell: undefined, title: `${label} ${terminalCounter}`, exited: false }];
      }
      return prev;
    });
  }, [shells]);

  const closeTerminal = useCallback(
    (id: string) => {
      client.closeTerminal(id);
      setTerminals((prev) => {
        const next = prev.filter((term) => term.id !== id);
        setActiveTerminal((current) =>
          current === id ? (next.length ? next[next.length - 1].id : null) : current,
        );
        if (next.length === 0) setTerminalOpen(false);
        return next;
      });
    },
    [client],
  );

  const handleTerminalExit = useCallback((id: string) => {
    setTerminals((prev) =>
      prev.map((term) => (term.id === id ? { ...term, exited: true } : term)),
    );
  }, []);

  useEffect(() => {
    let cancelled = false;
    client
      .requestTerminalShells()
      .then((rows) => {
        if (!cancelled) setShells(rows);
      })
      .catch(() => {
        if (!cancelled) setShells([]);
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  const loadDir = useCallback(
    async (path: string | null) => {
      const nodeKey = path ?? "__root__";
      setNodes((prev) => ({
        ...prev,
        [nodeKey]: {
          entries: prev[nodeKey]?.entries ?? [],
          loading: true,
          error: null,
        },
      }));
      try {
        const payload = await fetchFileTree(token, treeKey, path ?? undefined);
        if (!path) setRootPath(payload.path);
        setNodes((prev) => ({
          ...prev,
          [nodeKey]: { entries: payload.entries, loading: false, error: null },
        }));
      } catch (err) {
        setNodes((prev) => ({
          ...prev,
          [nodeKey]: {
            entries: prev[nodeKey]?.entries ?? [],
            loading: false,
            error: err instanceof Error ? err.message : String(err),
          },
        }));
      }
    },
    [token, treeKey],
  );

  useEffect(() => {
    setNodes({});
    setExpanded(new Set());
    setTabs([]);
    setActiveTab(null);
    setPreviews({});
    setDrafts({});
    void loadDir(null);
    // projectPath is a reload trigger: switching project root must rescan the tree.
  }, [loadDir, projectPath]);

  const toggleDir = useCallback(
    (path: string) => {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(path)) {
          next.delete(path);
        } else {
          next.add(path);
        }
        return next;
      });
      if (!nodes[path]) void loadDir(path);
    },
    [loadDir, nodes],
  );

  const openFile = useCallback(
    async (entry: FileTreeEntry) => {
      setMode("code");
      setActiveTab(entry.path);
      setSaveError(null);
      setTabs((prev) =>
        prev.some((tab) => tab.path === entry.path)
          ? prev
          : [...prev, { path: entry.path, displayPath: entry.path, name: entry.name }],
      );
      if (previews[entry.path]) return;
      setFileLoading(true);
      setPreviewError(null);
      try {
        const payload = await fetchDevFilePreview(token, treeKey, entry.path);
        setPreviews((prev) => ({ ...prev, [entry.path]: payload }));
        setTabs((prev) =>
          prev.map((tab) =>
            tab.path === entry.path ? { ...tab, displayPath: payload.display_path } : tab,
          ),
        );
        refreshDiagnostics(entry.path);
      } catch (err) {
        setPreviewError(err instanceof Error ? err.message : String(err));
      } finally {
        setFileLoading(false);
      }
    },
    [previews, refreshDiagnostics, token, treeKey],
  );

  const openFileByPath = useCallback(
    (path: string) => {
      const cleaned = path.trim();
      if (!cleaned) return;
      const name = cleaned.replace(/\/+$/, "").split("/").pop() || cleaned;
      void openFile({ path: cleaned, name } as FileTreeEntry);
    },
    [openFile],
  );

  const projectAbsolutePath = useCallback(
    (relativePath: string) => {
      const base = (projectPath ?? rootPath ?? "").replace(/\/+$/, "");
      return base ? `${base}/${relativePath}` : relativePath;
    },
    [projectPath, rootPath],
  );

  const openSearchMatch = useCallback(
    (relativePath: string, line: number) => {
      const absolute = projectAbsolutePath(relativePath);
      openFileByPath(absolute);
      setReveal((prev) => ({
        path: absolute,
        line,
        nonce: (prev?.nonce ?? 0) + 1,
      }));
    },
    [openFileByPath, projectAbsolutePath],
  );

  const showDiff = useCallback((relativePath: string) => {
    setDiffFile(relativePath);
    setMode("diff");
  }, []);

  // Chat → editor bridge: file chips clicked in the chat open here as tabs.
  useEffect(() => {
    if (!openFileRequest) return;
    openFileByPath(openFileRequest.path);
  }, [openFileByPath, openFileRequest]);

  const refreshActiveFile = useCallback(async () => {
    if (!activeTab) return;
    setFileLoading(true);
    setPreviewError(null);
    try {
      const payload = await fetchDevFilePreview(token, treeKey, activeTab);
      setPreviews((prev) => ({ ...prev, [activeTab]: payload }));
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[activeTab];
        return next;
      });
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : String(err));
    } finally {
      setFileLoading(false);
    }
  }, [activeTab, token, treeKey]);

  const closeTab = useCallback((path: string) => {
    setTabs((prev) => {
      const next = prev.filter((tab) => tab.path !== path);
      setActiveTab((current) =>
        current === path ? (next.length ? next[next.length - 1].path : null) : current,
      );
      return next;
    });
    setPreviews((prev) => {
      const next = { ...prev };
      delete next[path];
      return next;
    });
    setDrafts((prev) => {
      const next = { ...prev };
      delete next[path];
      return next;
    });
  }, []);

  const activePreview = activeTab ? previews[activeTab] : null;
  const activeKind = activePreview?.kind ?? "text";
  const activeDraft =
    activeTab != null && drafts[activeTab] !== undefined
      ? drafts[activeTab]
      : activePreview?.content ?? "";
  const activeDirty =
    activeTab != null &&
    drafts[activeTab] !== undefined &&
    drafts[activeTab] !== (activePreview?.content ?? "");
  const activeEditable =
    activeKind === "text" && activePreview != null && !activePreview.truncated;

  const saveActiveFile = useCallback(async () => {
    if (!activeTab || !activeEditable) return;
    const content = drafts[activeTab];
    if (content === undefined) return;
    setSaving(true);
    setSaveError(null);
    try {
      await saveWorkspaceFile(token, treeKey, activeTab, content);
      setPreviews((prev) => {
        const current = prev[activeTab];
        return current
          ? { ...prev, [activeTab]: { ...current, content } }
          : prev;
      });
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[activeTab];
        return next;
      });
      refreshDiagnostics(activeTab);
      refreshGitStatus();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [activeEditable, activeTab, drafts, refreshDiagnostics, refreshGitStatus, token, treeKey]);

  const openBrowser = useCallback(() => {
    const value = browserUrl.trim();
    if (!value) return;
    const normalized = /^https?:\/\//i.test(value) ? value : `http://${value}`;
    setBrowserSrc(normalized);
    setBrowserNonce((n) => n + 1);
    setMode("browser");
  }, [browserUrl]);

  const rootState = nodes["__root__"];
  const projectLabel = useMemo(() => {
    const raw = projectPath ?? rootPath ?? "";
    if (!raw) return tx("dev.workspace", "Workspace");
    const parts = raw.replace(/\/+$/, "").split("/");
    return parts[parts.length - 1] || raw;
  }, [projectPath, rootPath, tx]);

  const dirtyPaths = useMemo(() => {
    const set = new Set<string>();
    for (const [path, draft] of Object.entries(drafts)) {
      if (draft !== (previews[path]?.content ?? "")) set.add(path);
    }
    return set;
  }, [drafts, previews]);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
    <div ref={containerRef} className="flex min-h-0 min-w-0 flex-1">
      {/* Explorer */}
      {explorerOpen ? (
        <div
          className="flex shrink-0 flex-col border-r border-border/55 bg-muted/20"
          style={{ width: explorerWidth }}
        >
          <div className="flex items-center justify-between gap-2 border-b border-border/50 px-3 py-2.5">
            <div className="flex min-w-0 items-center gap-1.5">
              <Folder className="h-4 w-4 shrink-0 text-primary" aria-hidden />
              <span className="truncate text-[13px] font-semibold text-foreground">
                {projectLabel}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-0.5">
              <button
                type="button"
                onClick={() => void loadDir(null)}
                className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label={tx("dev.refreshTree", "Refresh files")}
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              </button>
              <button
                type="button"
                onClick={() => setExplorerOpen(false)}
                className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label={tx("dev.hideExplorer", "Hide explorer")}
              >
                <PanelLeftClose className="h-3.5 w-3.5" aria-hidden />
              </button>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-0.5 border-b border-border/50 px-1.5 py-1">
            {(
              [
                { id: "files" as const, icon: Folder, label: tx("dev.panel.files", "Files") },
                { id: "search" as const, icon: Search, label: tx("dev.panel.search", "Search") },
                { id: "git" as const, icon: GitBranch, label: tx("dev.panel.git", "Git") },
              ]
            ).map(({ id, icon: Icon, label }) => (
              <button
                key={id}
                type="button"
                onClick={() => setPanelTab(id)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium transition-colors",
                  panelTab === id
                    ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
                aria-pressed={panelTab === id}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden />
                {label}
              </button>
            ))}
          </div>
          {panelTab === "search" ? (
            <DevSearchPanel
              token={token}
              sessionKey={treeKey}
              onOpenMatch={openSearchMatch}
            />
          ) : panelTab === "git" ? (
            <DevGitPanel
              token={token}
              sessionKey={treeKey}
              selectedPath={mode === "diff" ? diffFile : null}
              onShowDiff={showDiff}
            />
          ) : (
          <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2">
            {rootState?.loading && !rootState.entries.length ? (
              <div className="flex items-center gap-2 px-2 py-1.5 text-[12px] text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                {tx("dev.loadingTree", "Loading files…")}
              </div>
            ) : rootState?.error ? (
              <p className="px-2 py-1.5 text-[12px] text-destructive">{rootState.error}</p>
            ) : (
              <TreeLevel
                parentKey="__root__"
                nodes={nodes}
                expanded={expanded}
                activePath={activeTab}
                dirtyPaths={dirtyPaths}
                depth={0}
                emptyLabel={tx("dev.emptyDir", "empty")}
                onToggleDir={toggleDir}
                onOpenFile={openFile}
              />
            )}
          </div>
          )}
        </div>
      ) : null}
      {explorerOpen ? (
        <div
          role="separator"
          aria-orientation="vertical"
          onPointerDown={startDrag("explorer")}
          className="w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-primary/40"
        />
      ) : null}

      {/* Editor / browser area + terminal */}
      <div className="flex min-w-0 flex-1 flex-col">
       <div className={cn("flex min-h-0 flex-1 flex-col", terminalOpen && terminalMaximized && "hidden")}>
        {/* Tab bar */}
        <div className="flex items-center gap-1 border-b border-border/55 bg-muted/15 px-2 py-1.5">
          {!explorerOpen ? (
            <button
              type="button"
              onClick={() => setExplorerOpen(true)}
              className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={tx("dev.showExplorer", "Show explorer")}
            >
              <PanelLeftOpen className="h-4 w-4" aria-hidden />
            </button>
          ) : null}
          <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
            {tabs.map((tab) => (
              <div
                key={tab.path}
                className={cn(
                  "group flex shrink-0 cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors",
                  mode === "code" && activeTab === tab.path
                    ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
                onClick={() => {
                  setMode("code");
                  setActiveTab(tab.path);
                }}
              >
                <File className="h-3.5 w-3.5 shrink-0" aria-hidden />
                <span className="max-w-[10rem] truncate">{tab.name}</span>
                {dirtyPaths.has(tab.path) ? (
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full bg-foreground/60"
                    aria-label={tx("dev.unsaved", "Unsaved changes")}
                  />
                ) : null}
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    closeTab(tab.path);
                  }}
                  className="rounded p-0.5 opacity-0 transition-opacity hover:bg-muted group-hover:opacity-100"
                  aria-label={tx("dev.closeTab", "Close tab")}
                >
                  <X className="h-3 w-3" aria-hidden />
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setMode("browser")}
            className={cn(
              "flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors",
              mode === "browser"
                ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            <Globe className="h-3.5 w-3.5" aria-hidden />
            {tx("dev.browserTab", "Preview")}
          </button>
          <button
            type="button"
            onClick={() => setMode("graph")}
            className={cn(
              "flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors",
              mode === "graph"
                ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            <Waypoints className="h-3.5 w-3.5" aria-hidden />
            {tx("dev.graphTab", "Graph")}
          </button>
          <button
            type="button"
            onClick={() => (terminalOpen ? setTerminalOpen(false) : openTerminalPanel())}
            className={cn(
              "flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors",
              terminalOpen
                ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
            title={tx("dev.toggleTerminal", "Toggle terminal")}
          >
            <TerminalSquare className="h-3.5 w-3.5" aria-hidden />
            {tx("dev.terminal", "Terminal")}
          </button>
          {onRunAction ? (
            <DevProjectActions
              activeFilePath={mode === "code" ? activeTab : null}
              onRun={onRunAction}
            />
          ) : null}
          {onSelectProject ? (
            <div className="ml-1 shrink-0 border-l border-border/50 pl-2">
              <DevProjectSelector
                projectPath={projectPath ?? rootPath}
                projectName={projectName}
                recentProjects={recentProjects ?? []}
                onSelectProject={onSelectProject}
              />
            </div>
          ) : null}
          {onToggleChat ? (
            <button
              type="button"
              onClick={onToggleChat}
              className="ml-1 shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label={
                chatOpen
                  ? tx("dev.hideChat", "Hide chat")
                  : tx("dev.showChat", "Show chat")
              }
              title={
                chatOpen
                  ? tx("dev.hideChat", "Hide chat")
                  : tx("dev.showChat", "Show chat")
              }
            >
              {chatOpen ? (
                <PanelRightClose className="h-4 w-4" aria-hidden />
              ) : (
                <PanelRightOpen className="h-4 w-4" aria-hidden />
              )}
            </button>
          ) : null}
        </div>

        {/* Content */}
        {mode === "diff" && diffFile ? (
          <DevDiffView
            token={token}
            sessionKey={treeKey}
            file={diffFile}
            onOpenFile={(relativePath) => {
              openFileByPath(projectAbsolutePath(relativePath));
            }}
          />
        ) : mode === "graph" ? (
          <Suspense
            fallback={
              <div className="flex flex-1 items-center justify-center text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              </div>
            }
          >
            <DevMetagraph
              sessionKey={sessionKey}
              onOpenFile={(absolutePath) => {
                openFileByPath(absolutePath);
                setMode("code");
              }}
              onRunAction={onRunAction}
            />
          </Suspense>
        ) : mode === "browser" ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center gap-2 border-b border-border/50 px-3 py-2">
              <Input
                value={browserUrl}
                onChange={(event) => setBrowserUrl(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") openBrowser();
                }}
                placeholder={PREVIEW_DEFAULT_URL}
                className="h-9 rounded-lg font-mono text-[12px]"
              />
              <Button type="button" size="sm" className="h-9 rounded-lg" onClick={openBrowser}>
                {tx("dev.browserGo", "Open")}
              </Button>
            </div>
            {browserSrc ? (
              <iframe
                key={browserNonce}
                src={browserSrc}
                title={tx("dev.browserTab", "Preview")}
                className="min-h-0 flex-1 border-0 bg-white"
                sandbox="allow-scripts allow-same-origin allow-forms allow-modals"
              />
            ) : (
              <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
                <Globe className="h-8 w-8 opacity-40" aria-hidden />
                <p className="text-[13px] leading-6">
                  {tx(
                    "dev.browserEmpty",
                    "Start a dev server with the agent (e.g. “run npm run dev”), then open its URL here.",
                  )}
                </p>
              </div>
            )}
          </div>
        ) : activeTab && activePreview ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between gap-2 border-b border-border/50 px-3 py-1.5">
              <span className="truncate font-mono text-[11px] text-muted-foreground">
                {activePreview.display_path}
                {activeDirty ? " •" : ""}
              </span>
              <div className="flex shrink-0 items-center gap-1.5">
                {saveError ? (
                  <span className="max-w-[16rem] truncate text-[11px] text-destructive">
                    {saveError}
                  </span>
                ) : null}
                {activeEditable ? (
                  <button
                    type="button"
                    onClick={() => void saveActiveFile()}
                    disabled={!activeDirty || saving}
                    className={cn(
                      "flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
                      activeDirty
                        ? "bg-primary text-primary-foreground hover:bg-primary/90"
                        : "text-muted-foreground",
                    )}
                  >
                    {saving ? (
                      <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                    ) : (
                      <Save className="h-3 w-3" aria-hidden />
                    )}
                    {tx("dev.save", "Save")}
                    <span className="opacity-60">⌘S</span>
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => void refreshActiveFile()}
                  className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  {fileLoading ? (
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                  ) : (
                    <RefreshCw className="h-3 w-3" aria-hidden />
                  )}
                  {tx("dev.refreshFile", "Reload")}
                </button>
              </div>
            </div>
            {activeKind === "image" && activePreview.data_url ? (
              <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-[repeating-conic-gradient(hsl(var(--muted))_0%_25%,transparent_0%_50%)] bg-[length:16px_16px] p-6">
                <img
                  src={activePreview.data_url}
                  alt={activePreview.display_path}
                  className="max-h-full max-w-full rounded-lg shadow-lg"
                />
              </div>
            ) : activeKind === "binary" ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
                <FileWarning className="h-8 w-8 opacity-40" aria-hidden />
                <p className="text-[13px]">
                  {tx("dev.binaryFile", "Binary file — cannot be displayed as text.")}
                </p>
                <p className="font-mono text-[12px]">{formatSize(activePreview.size)}</p>
              </div>
            ) : (
              <div className="min-h-0 flex-1">
                <Suspense
                  fallback={
                    <div className="flex h-full items-center justify-center">
                      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
                    </div>
                  }
                >
                  <CodeEditor
                    value={activeDraft}
                    language={activePreview.language}
                    isDark={theme === "dark"}
                    readOnly={!activeEditable}
                    reveal={
                      reveal && activeTab === reveal.path
                        ? { line: reveal.line, nonce: reveal.nonce }
                        : null
                    }
                    diagnostics={
                      activeTab
                        ? diagnosticsByPath[activeTab]?.diagnostics ?? undefined
                        : undefined
                    }
                    onChange={(next) => {
                      if (!activeTab) return;
                      setDrafts((prev) => ({ ...prev, [activeTab]: next }));
                    }}
                    onSave={() => void saveActiveFile()}
                  />
                </Suspense>
                {activePreview.truncated ? (
                  <p className="border-t border-border/50 px-3 py-2 text-[12px] text-muted-foreground">
                    {tx(
                      "dev.fileTruncated",
                      "File truncated for preview — editing is disabled.",
                    )}
                  </p>
                ) : null}
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
            {fileLoading ? (
              <Loader2 className="h-6 w-6 animate-spin" aria-hidden />
            ) : (
              <>
                <Code2 className="h-8 w-8 opacity-40" aria-hidden />
                <p className="max-w-md text-[13px] leading-6">
                  {tx(
                    "dev.editorEmpty",
                    "Open a file from the explorer, or ask the agent to build something — plan, code, run, and debug together.",
                  )}
                </p>
                {previewError ? (
                  <p className="text-[12px] text-destructive">{previewError}</p>
                ) : null}
              </>
            )}
          </div>
        )}
       </div>

        {/* Terminal panel */}
        {terminalOpen ? (
          <div
            className={cn(
              "flex shrink-0 flex-col border-t border-border/55 bg-[#111318] dark:bg-[#111318]",
              terminalMaximized && "min-h-0 flex-1",
            )}
            style={terminalMaximized ? undefined : { height: terminalHeight }}
          >
            {!terminalMaximized ? (
              <div
                role="separator"
                aria-orientation="horizontal"
                onPointerDown={startDrag("terminal")}
                className="h-1 shrink-0 cursor-row-resize bg-transparent transition-colors hover:bg-primary/40"
              />
            ) : null}
            <div className="flex items-center gap-1 border-b border-border/40 bg-muted/20 px-2 py-1">
              <TerminalSquare className="mr-1 h-3.5 w-3.5 text-muted-foreground" aria-hidden />
              <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
                {terminals.map((term) => (
                  <div
                    key={term.id}
                    className={cn(
                      "group flex shrink-0 cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-[11.5px] font-medium transition-colors",
                      activeTerminal === term.id
                        ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                    onClick={() => setActiveTerminal(term.id)}
                  >
                    <span className="max-w-[9rem] truncate">
                      {term.title}
                      {term.exited ? " ✓" : ""}
                    </span>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        closeTerminal(term.id);
                      }}
                      className="rounded p-0.5 opacity-0 transition-opacity hover:bg-muted group-hover:opacity-100"
                      aria-label={tx("dev.closeTab", "Close tab")}
                    >
                      <X className="h-3 w-3" aria-hidden />
                    </button>
                  </div>
                ))}
              </div>
              <div className="relative flex shrink-0 items-center gap-0.5">
                <button
                  type="button"
                  onClick={() => spawnTerminal()}
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label={tx("dev.newTerminal", "New terminal")}
                >
                  <Plus className="h-3.5 w-3.5" aria-hidden />
                </button>
                {shells.length > 1 ? (
                  <button
                    type="button"
                    onClick={() => setShellMenuOpen((open) => !open)}
                    className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label={tx("dev.selectShell", "Select shell")}
                  >
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => setTerminalMaximized((max) => !max)}
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label={
                    terminalMaximized
                      ? tx("dev.restoreTerminal", "Restore terminal size")
                      : tx("dev.maximizeTerminal", "Maximize terminal")
                  }
                >
                  {terminalMaximized ? (
                    <Minimize2 className="h-3.5 w-3.5" aria-hidden />
                  ) : (
                    <Maximize2 className="h-3.5 w-3.5" aria-hidden />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setTerminalOpen(false);
                    setTerminalMaximized(false);
                  }}
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label={tx("dev.hideTerminal", "Hide terminal")}
                >
                  <ChevronsDownUp className="h-3.5 w-3.5" aria-hidden />
                </button>
                {shellMenuOpen ? (
                  <div className="absolute right-0 top-8 z-20 min-w-[10rem] rounded-lg border border-border/60 bg-popover p-1 shadow-lg">
                    {shells.map((shell) => (
                      <button
                        key={shell.name}
                        type="button"
                        onClick={() => spawnTerminal(shell.name)}
                        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] text-foreground hover:bg-muted"
                      >
                        <TerminalSquare className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                        {shell.name}
                        {shell.default ? (
                          <span className="ml-auto text-[10px] text-muted-foreground">
                            {tx("dev.defaultShell", "default")}
                          </span>
                        ) : null}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
            <div className="relative min-h-0 flex-1">
              {terminals.map((term) => (
                <div key={term.id} className="absolute inset-0">
                  <Suspense fallback={null}>
                    <DevTerminal
                      terminalId={term.id}
                      shell={term.shell}
                      chatId={chatId}
                      isDark={theme === "dark"}
                      active={activeTerminal === term.id}
                      exitedLabel={tx("dev.terminalExited", "process exited")}
                      onExit={handleTerminalExit}
                    />
                  </Suspense>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
    <DevStatusBar
      environment={environment}
      git={gitStatus}
      projectLabel={projectLabel}
      activeDiagnostics={activeTab ? diagnosticsByPath[activeTab] ?? null : null}
      activeFileName={
        activeTab ? activeTab.replace(/\/+$/, "").split("/").pop() ?? null : null
      }
    />
    </div>
  );
}

function DevStatusBar({
  environment,
  git,
  projectLabel,
  activeDiagnostics,
  activeFileName,
}: {
  environment: string | null;
  git: GitStatusPayload | null;
  projectLabel: string;
  activeDiagnostics: FileDiagnosticsPayload | null;
  activeFileName: string | null;
}) {
  const { t } = useTranslation();
  const errors = activeDiagnostics?.errors ?? 0;
  const warnings = activeDiagnostics?.warnings ?? 0;
  const analyzed = activeDiagnostics?.supported === true;
  return (
    <div className="flex h-6 shrink-0 items-center gap-3 overflow-hidden border-t border-border/55 bg-muted/25 px-2.5 text-[11px] text-muted-foreground">
      {environment ? (
        <span className="flex shrink-0 items-center gap-1" title={t("dev.status.environment", { defaultValue: "Environment" })}>
          <MonitorSmartphone className="h-3 w-3" aria-hidden />
          {environment.toUpperCase()}
        </span>
      ) : null}
      {git?.is_repo ? (
        <span
          className="flex min-w-0 shrink-0 items-center gap-1"
          title={t("dev.status.gitTooltip", {
            defaultValue:
              "Branch {{branch}} — {{staged}} staged, {{unstaged}} modified, {{untracked}} untracked",
            branch: git.branch,
            staged: git.staged ?? 0,
            unstaged: git.unstaged ?? 0,
            untracked: git.untracked ?? 0,
          })}
        >
          <GitBranch className="h-3 w-3" aria-hidden />
          <span className="max-w-[10rem] truncate">
            {git.branch}
            {git.dirty ? "*" : ""}
          </span>
          {git.ahead ? <span>↑{git.ahead}</span> : null}
          {git.behind ? <span>↓{git.behind}</span> : null}
          {(git.staged ?? 0) + (git.unstaged ?? 0) + (git.untracked ?? 0) > 0 ? (
            <span className="font-medium text-foreground/80">
              {(git.staged ?? 0) + (git.unstaged ?? 0) + (git.untracked ?? 0)}{" "}
              {t("dev.status.changes", { defaultValue: "changes" })}
            </span>
          ) : null}
        </span>
      ) : git && git.available === true ? (
        <span className="flex shrink-0 items-center gap-1 opacity-70">
          <GitBranch className="h-3 w-3" aria-hidden />
          {t("dev.status.noRepo", { defaultValue: "no git" })}
        </span>
      ) : null}
      <span className="min-w-0 flex-1 truncate">{projectLabel}</span>
      {activeFileName ? (
        <span className="flex shrink-0 items-center gap-2">
          <span className="max-w-[12rem] truncate">{activeFileName}</span>
          <span
            className={cn("flex items-center gap-0.5", errors > 0 && "text-red-500")}
            title={t("dev.status.errors", { defaultValue: "Errors" })}
          >
            <XCircle className="h-3 w-3" aria-hidden />
            {errors}
          </span>
          <span
            className={cn("flex items-center gap-0.5", warnings > 0 && "font-medium text-foreground/80")}
            title={t("dev.status.warnings", { defaultValue: "Warnings" })}
          >
            <AlertTriangle className="h-3 w-3" aria-hidden />
            {warnings}
          </span>
          {!analyzed ? (
            <span className="opacity-60">
              {t("dev.status.notAnalyzed", { defaultValue: "no linter" })}
            </span>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}

function TreeLevel({
  parentKey,
  nodes,
  expanded,
  activePath,
  dirtyPaths,
  depth,
  emptyLabel,
  onToggleDir,
  onOpenFile,
}: {
  parentKey: string;
  nodes: Record<string, TreeNodeState>;
  expanded: Set<string>;
  activePath: string | null;
  dirtyPaths: Set<string>;
  depth: number;
  emptyLabel: string;
  onToggleDir: (path: string) => void;
  onOpenFile: (entry: FileTreeEntry) => void;
}) {
  const state = nodes[parentKey];
  if (!state) return null;
  if (!state.loading && !state.error && state.entries.length === 0) {
    return (
      <p
        className="py-1 text-[11px] italic text-muted-foreground/70"
        style={{ paddingLeft: `${26 + depth * 14}px` }}
      >
        {emptyLabel}
      </p>
    );
  }
  return (
    <div>
      {state.entries.map((entry) => {
        const isOpen = entry.type === "dir" && expanded.has(entry.path);
        return (
          <div key={entry.path}>
            <button
              type="button"
              onClick={() =>
                entry.type === "dir" ? onToggleDir(entry.path) : onOpenFile(entry)
              }
              style={{ paddingLeft: `${8 + depth * 14}px` }}
              className={cn(
                "flex w-full min-w-0 items-center gap-1.5 rounded-md py-1 pr-2 text-left text-[12.5px] leading-5 transition-colors",
                activePath === entry.path
                  ? "bg-primary/10 text-primary"
                  : "text-foreground/80 hover:bg-muted/60 hover:text-foreground",
                entry.heavy && "opacity-60",
              )}
            >
              {entry.type === "dir" ? (
                <>
                  {isOpen ? (
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  )}
                  {isOpen ? (
                    <FolderOpen className="h-3.5 w-3.5 shrink-0 text-primary/80" aria-hidden />
                  ) : (
                    <Folder className="h-3.5 w-3.5 shrink-0 text-primary/70" aria-hidden />
                  )}
                </>
              ) : (
                <File className="ml-[18px] h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              )}
              <span className="truncate">{entry.name}</span>
              {dirtyPaths.has(entry.path) ? (
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-foreground/60" aria-hidden />
              ) : null}
            </button>
            {isOpen ? (
              nodes[entry.path]?.loading && !nodes[entry.path]?.entries.length ? (
                <div
                  className="flex items-center gap-1.5 py-1 text-[11px] text-muted-foreground"
                  style={{ paddingLeft: `${26 + depth * 14}px` }}
                >
                  <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                </div>
              ) : (
                <TreeLevel
                  parentKey={entry.path}
                  nodes={nodes}
                  expanded={expanded}
                  activePath={activePath}
                  dirtyPaths={dirtyPaths}
                  depth={depth + 1}
                  emptyLabel={emptyLabel}
                  onToggleDir={onToggleDir}
                  onOpenFile={onOpenFile}
                />
              )
            ) : null}
          </div>
        );
      })}
      {state.error ? (
        <p className="px-2 py-1 text-[11px] text-destructive">{state.error}</p>
      ) : null}
    </div>
  );
}
