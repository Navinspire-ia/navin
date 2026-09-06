import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  type WheelEvent as ReactWheelEvent,
} from "react";
import {
  Activity,
  Beaker,
  Blocks,
  BookMarked,
  BrainCircuit,
  Bug,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsLeft,
  ChevronsRight,
  ClipboardCopy,
  ClipboardPaste,
  Code2,
  Columns2,
  Copy,
  CopyPlus,
  Download,
  Eye,
  File,
  FilePlus,
  FileWarning,
  FlaskConical,
  Folder,
  FolderKanban,
  FolderOpen,
  FolderPlus,
  GitBranch,
  GitCompare,
  Globe,
  Infinity as InfinityIcon,
  LayoutTemplate,
  ListTodo,
  ListTree,
  Loader2,
  Maximize2,
  MessageSquarePlus,
  Minimize2,
  MoreHorizontal,
  PackagePlus,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightOpen,
  PenLine,
  Plus,
  RefreshCw,
  Save,
  Scissors,
  Search,
  ShieldCheck,
  Smartphone,
  TerminalSquare,
  Trash2,
  Waypoints,
  Minus,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { parseCsv } from "@/components/FilePreviewBody";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ContextMenu,
  useContextMenu,
  type ContextMenuEntry,
} from "@/components/ui/context-menu";
import { DevOtherMenu, type DevOtherMenuItem } from "@/components/dev/DevOtherMenu";
import { DevPreviewBrowser } from "@/components/dev/DevPreviewBrowser";
import { matchSourceFile, sourceLookup } from "@/components/dev/designMode";
import { localPreviewPortFromUrl } from "@/components/dev/previewConsole";
import { RecentEditsTracker } from "@/components/dev/recentEdits";
import { DevMobilePreview } from "@/components/dev/DevMobilePreview";
import { MarkdownText } from "@/components/MarkdownText";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import { DevProjectSelector } from "@/components/dev/DevProjectSelector";
import { ProjectHomeView } from "@/components/project/ProjectHomeView";
import { AppTemplatesSettings } from "@/components/settings/AppTemplatesSettings";
import { InstallSkillDialog } from "@/components/settings/InstallSkillDialog";
import { DevGitPanel } from "@/components/dev/DevGitPanel";
import { DevCommandPalette } from "@/components/dev/DevCommandPalette";
import { DevLocationPicker, type DevLocationItem } from "@/components/dev/DevLocationPicker";
import { DevOutlinePanel } from "@/components/dev/DevOutlinePanel";
import { DevProcessesPanel } from "@/components/dev/DevProcessesPanel";
import { DevProblemsPanel } from "@/components/dev/DevProblemsPanel";
import { DevQuickOpen } from "@/components/dev/DevQuickOpen";
import { DevDebugPanel } from "@/components/dev/DevDebugPanel";
import { DevRulesPanel } from "@/components/dev/DevRulesPanel";
import { useRailCounts } from "@/hooks/useRailCounts";
import { railCountText } from "@/lib/rail-counts";
import { DevSearchPanel } from "@/components/dev/DevSearchPanel";
import { DevSymbolPicker } from "@/components/dev/DevSymbolPicker";
import { DevDiffView } from "@/components/dev/DevDiffView";
import {
  getBreakpointLines,
  subscribeBreakpoints,
  toggleBreakpoint,
} from "@/lib/debug-breakpoints";
import { breakpointsEnabledForPath } from "@/lib/debug-runtimes";
import type { DevCommand } from "@/components/dev/devPalette";
import {
  ApiError,
  createWorkspaceEntry,
  debugOp,
  deleteWorkspaceEntry,
  downloadWorkspaceFile,
  fetchContextUsage,
  fetchDevFilePreview,
  fetchDiscoverPreviewUrl,
  fetchFileDiagnostics,
  fetchWorkspaceDiagnostics,
  fetchFileTree,
  fetchFsRoots,
  fetchGithubChecks,
  fetchGitBlame,
  fetchGitChanges,
  fetchGitStatus,
  fetchGotoDefinition,
  fetchInlineCompletion,
  fetchInlineEdit,
  fetchLspCodeActions,
  fetchLspCompletion,
  fetchLspDefinition,
  fetchLspHover,
  fetchLspReferences,
  fetchLspRename,
  fetchLspSignatureHelp,
  fetchProjectFiles,
  fetchProjectSymbols,
  fetchResumeSeed,
  fetchReviewChanges,
  fetchReviewFile,
  fetchRuntimeHealth,
  leaveHandoff,
  openExternalUrl,
  type RuntimeHealth,
  pasteWorkspaceEntry,
  postReviewAction,
  renameWorkspaceEntry,
  formatWorkspaceFile,
  saveWorkspaceFile,
  startPreviewProxy,
} from "@/lib/api";
import { DiffPair } from "@/components/thread/activity/DiffPair";
import {
  OPEN_BOARD_TASK_EVENT,
  OPEN_CHECKPOINTS_EVENT,
  OPEN_SESSION_PLAN_EVENT,
  consumePendingOpenBoard,
  consumePendingOpenCheckpoints,
  consumePendingOpenSessionPlan,
  requestShowPendingReview,
} from "@/lib/workbench-events";
import { copyTextToClipboard } from "@/lib/clipboard";
import { registerDirtyFileFlusher } from "@/lib/dirty-file-flusher";
import { publishNotification } from "@/lib/notification-bus";
import type {
  AssistMeta,
  ChatSummary,
  ConnectionStatus,
  ContextUsagePayload,
  DebugStatePayload,
  FileDiagnosticsPayload,
  FilePreviewPayload,
  FileTreeEntry,
  GithubCiStatusPayload,
  GitBlameLine,
  GitChangesPayload,
  GitStatusPayload,
  ProjectFileMatch,
  ProjectSymbol,
  RecentProjectEntry,
  ReviewChangeEntry,
  ReviewFilePayload,
  TerminalShellInfo,
} from "@/lib/types";
import { PdfPane } from "@/components/dev/PdfPane";
import { fingerprintContent } from "@/lib/artifact-dedup";
import { setDevOpenFiles } from "@/lib/dev-open-files";
import {
  recordTabLatency,
  tabLatencyPercentiles,
} from "@/lib/tab-telemetry";
import { railWidthFor, type DevRailDensity } from "@/lib/dev-rail-layout";
import { cn } from "@/lib/utils";
import { isDesktopShell } from "@/lib/desktop";
import { shouldUseWindowOpenFallback } from "@/lib/external-url";
import {
  isNavinSelfPreviewUrl,
  isNavinSelfPreviewUrlSync,
  normalizePreviewUrl,
  osBrowserPreviewUrl,
} from "@/lib/preview-url";
import {
  projectEnvironmentLabel,
  sameWorkspacePath,
  shortWorkspacePath,
} from "@/lib/workspace";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useThemeValue } from "@/hooks/useTheme";
import { useClient } from "@/providers/ClientProvider";
import { lazyWithRetry } from "@/lib/lazy-retry";

import { SpreadsheetPreview } from "@/components/SpreadsheetPreview";

import { DevHtmlPreview } from "./DevHtmlPreview";
import { DevStatusBar } from "./DevStatusBar";
import { DevTreeLevel, type TreeNodeState } from "./DevTreeLevel";
import { FileTypeIcon } from "./FileTypeIcon";
import {
  agentTermTitle,
  applyOptimisticTreeFile,
  baseName,
  defaultEditorPreviewOn,
  dropPathKey,
  fileEditIsOnDisk,
  formatSize,
  isCsvEditorPath,
  isHtmlEditorPath,
  isMarkdownEditorPath,
  lookupTreeNode,
  mergeFileTreeListing,
  mergeReviewTabs,
  movePathKey,
  persistPreviewUrl,
  PREVIEW_URL_STORAGE_KEY,
  readStoredPreviewUrl,
  REVIEW_VISIBLE_FILE_LIMIT,
  reviewLineStats,
  reviewOpenableFiles,
  reviewStatusBadge,
  tabMatchesDiffFile,
  sameTreeDir,
  terminalStartDir,
  treeDirsToReload,
  treeDirIsExpanded,
  treeExpandedWith,
  treeExpandedWithout,
  treeFileAbsolutePath,
  treeNodeKeysToUpdate,
  treeSessionChatId,
} from "./devWorkbenchUtils";

const CodeEditor = lazyWithRetry(() => import("./CodeEditor"));
type HunkAction = (hunkId: string, action: "accept" | "reject") => void;
const DevTerminal = lazyWithRetry(() => import("./DevTerminal"));
const AgentExecTerminal = lazyWithRetry(() => import("./AgentExecTerminal"));
const DevMetagraph = lazyWithRetry(() => import("./DevMetagraph"));
const DevBoardPanel = lazyWithRetry(() => import("./DevBoardPanel"));
const DevGuardrailsPanel = lazyWithRetry(() => import("./DevGuardrailsPanel"));
// AGI: what the agent may learn on its own (skills evolution, episodic memory).
const DevAgiPanel = lazyWithRetry(() => import("./DevAgiPanel"));

/** Counter pill on a rail row (names shown). Tones add the colors. */
const RAIL_BADGE_CLASS =
  "shrink-0 rounded-full px-1.5 text-center text-[10px] font-semibold leading-4 tabular-nums min-w-[1.125rem]";
/** Counter pill on the icon's corner (toolbar, icon-only rail). */
const TOOLBAR_CORNER_BADGE_CLASS =
  "absolute -right-0.5 -top-0.5 min-w-[1rem] rounded-full px-1 text-center text-[9px] font-semibold leading-4 tabular-nums";
/** Neutral "how many are open" tone; amber / sky stay for attention. */
const RAIL_COUNT_TONE = "bg-foreground/[0.12] text-foreground/90";
const TestExplorerPanel = lazyWithRetry(() => import("./TestExplorerPanel"));
const DevExtensionsPanel = lazyWithRetry(() => import("./DevExtensionsPanel"));
const DevPlanPanel = lazyWithRetry(() => import("./DevPlanPanel"));
// Evolve is a Code surface: it acts on the project currently open here, so it
// is mounted in the center area rather than in a separate shell view.
const EvolveWorkbench = lazyWithRetry(() =>
  import("@/components/evolve/EvolveWorkbench").then((module) => ({
    default: module.EvolveWorkbench,
  })),
);

type TerminalTab = {
  id: string;
  shell?: string;
  title: string;
  exited: boolean;
  /** Folder the shell starts in; the project root when absent. */
  cwd?: string;
  /** "agent" tabs are read-only feeds of the agent's exec commands. */
  kind?: "pty" | "agent";
  /** Long-running agent command (exec background/session mode). */
  background?: boolean;
  exitCode?: number | null;
  /**
   * Isolated terminal: the shell runs inside the agent's OS sandbox (writes
   * limited to the project). Requested at open, confirmed by the gateway.
   */
  sandbox?: boolean;
};

/** Scrollback kept per agent terminal so a tab remounts with its history. */
const AGENT_TERM_BUFFER_CHARS = 200_000;


let terminalCounter = 0;

type OpenTab = {
  path: string;
  displayPath: string;
  name: string;
};

function PlainFileEditor({
  value,
  readOnly,
  onChange,
  onSave,
}: {
  value: string;
  readOnly: boolean;
  onChange: (next: string) => void;
  onSave: () => void;
}) {
  return (
    <textarea
      value={value}
      readOnly={readOnly}
      spellCheck={false}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "s") {
          event.preventDefault();
          onSave();
        }
      }}
      className="h-full min-h-0 w-full flex-1 resize-none border-0 bg-transparent px-4 py-3 font-mono text-[12px] leading-5 text-foreground outline-none"
    />
  );
}


export function DevWorkbench({
  sessionKey,
  projectPath,
  projectName,
  recentProjects,
  sessions,
  onSelectProject,
  openFileRequest,
  previewOpenRequest,
  projectHomeRequest,
  templatesRequest,
  evolveRequest,
  onOpenFileRequestHandled,
  onPreviewOpenRequestHandled,
  onProjectHomeRequestHandled,
  onTemplatesRequestHandled,
  onEvolveRequestHandled,
  chatOpen,
  onRunAction,
  onSeedChat,
  onOpenChat,
  onResumeProjectGoal,
  onMaximizeChat,
  onRevealWorkbench,
  collapsed = false,
  railDensity = "labels",
  onToggleRailDensity,
  dockRight = false,
  panelWidth = 620,
  projectsReady = true,
  onExplorerWidthChange,
}: {
  sessionKey: string | null;
  projectPath: string | null;
  projectName?: string | null;
  recentProjects?: RecentProjectEntry[];
  sessions?: ChatSummary[];
  onSelectProject?: (path: string, name?: string) => void;
  openFileRequest?: {
    path: string;
    nonce: number;
    /** "preview": open rendered (HTML iframe / Markdown) instead of source. */
    mode?: "preview" | "code" | "diff";
  } | null;
  /** App-level open_preview request (also works when Code was not mounted). */
  previewOpenRequest?: {
    kind: "web" | "mobile";
    url?: string;
    nonce: number;
  } | null;
  /** Open Project Home panel inside Code (from `#/project` or toolbar). */
  projectHomeRequest?: { nonce: number } | null;
  /** Open the app-template gallery in the Code center (`#/code?panel=templates`). */
  templatesRequest?: { nonce: number } | null;
  /** Open the Evolve engine in the Code center (`#/code?panel=evolve`). */
  evolveRequest?: { nonce: number } | null;
  /**
   * Told once a request above has been acted on, so the parent can drop it.
   *
   * These props are latched, not events: they keep their value after being
   * handled so a request survives until Code is mounted. Left in place, they
   * replay - reopening a tab the user has closed - every time the workbench
   * remounts or an effect dependency changes identity.
   */
  onOpenFileRequestHandled?: () => void;
  onPreviewOpenRequestHandled?: () => void;
  onProjectHomeRequestHandled?: () => void;
  onTemplatesRequestHandled?: () => void;
  onEvolveRequestHandled?: () => void;
  chatOpen?: boolean;
  onRunAction?: (
    text: string,
    documentTemplate?: { category: string; name: string; title?: string },
  ) => void;
  /** Puts text in the chat composer without sending it. */
  onSeedChat?: (text: string, files?: ProjectFileMatch[]) => void;
  onOpenChat?: (key: string) => void;
  onResumeProjectGoal?: (seed: string) => void;
  /** Hide the whole workbench column and give the chat the full width. */
  onMaximizeChat?: () => void;
  /** Show the editor column without toggling. Used when a chat file chip opens. */
  onRevealWorkbench?: () => void;
  /**
   * Chat-maximized mode: ONLY the center pane hides. The explorer keeps its
   * exact place and width, the status bar keeps spanning the bottom, and the
   * open-tabs rail docks where the center's right edge was. The chat (an app
   * shell sibling) overlays the freed center area. The component stays
   * mounted so drafts, terminal and previews survive the toggle.
   */
  collapsed?: boolean;
  /**
   * Width of the collapsed rail: `labels` shows icons + names, `icons` keeps
   * only the icons (names move to tooltips). Owned by the app shell because
   * the chat overlay is sized against the same width.
   */
  railDensity?: DevRailDensity;
  /** Flips the rail between the labeled and the icon-only density. */
  onToggleRailDensity?: () => void;
  /**
   * Code layout: the chat owns the center for good. The workbench docks on the
   * right - the submodule rail when `collapsed`, this panel at `panelWidth`
   * otherwise - so restoring the editor never pushes the chat aside.
   */
  dockRight?: boolean;
  /** Width of the docked panel, resized from the app shell separator. */
  panelWidth?: number;
  /**
   * False while the recent-projects roster is still loading, so the "open a
   * project" gate waits instead of flashing over a workspace that has projects.
   */
  projectsReady?: boolean;
  /**
   * Reports the explorer column width so the app shell can align the
   * maximized chat exactly on the center area (explorer stays resizable).
   */
  onExplorerWidthChange?: (width: number) => void;
}) {
  const { token, client } = useClient();
  const theme = useThemeValue();
  const isNarrow = useMediaQuery("(max-width: 1023px)");
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const treeKey = sessionKey ?? "websocket:webui-dev";

  const [rootPath, setRootPath] = useState<string | null>(null);
  // Rules / Tasks pills on the rail: fetched only while the rail shows.
  const railCounts = useRailCounts({
    sessionKey: treeKey,
    projectPath: projectPath ?? rootPath,
    enabled: Boolean(collapsed),
  });
  const [nodes, setNodes] = useState<Record<string, TreeNodeState>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [rootExpanded, setRootExpanded] = useState(true);
  const [tabs, setTabs] = useState<OpenTab[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  // Cursor-style split view: file shown in the right pane, drag ratio, and
  // which pane receives the next opened file.
  const [splitTab, setSplitTab] = useState<string | null>(null);
  const [splitRatio, setSplitRatio] = useState(0.5);
  const [focusedPane, setFocusedPane] = useState<"left" | "right">("left");
  const [previews, setPreviews] = useState<Record<string, FilePreviewPayload>>({});
  // Per-path load generation. Anything that writes `previews` by hand (save,
  // review sync, rejection) bumps it first, so a read started earlier cannot
  // land afterwards and put the stale content back on screen.
  const previewGenRef = useRef<Record<string, number>>({});
  const bumpPreviewGen = useCallback((path: string): number => {
    const next = (previewGenRef.current[path] ?? 0) + 1;
    previewGenRef.current[path] = next;
    return next;
  }, []);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const draftsRef = useRef(drafts);
  draftsRef.current = drafts;
  // Recent user edits feed the Tab completion prompt (next-edit prediction).
  const recentEditsRef = useRef(new RecentEditsTracker());
  const recordDraftEdit = useCallback((editPath: string, next: string) => {
    setDrafts((prev) => {
      const old = prev[editPath];
      if (typeof old === "string" && old !== next) {
        recentEditsRef.current.record(editPath, old, next);
      }
      return { ...prev, [editPath]: next };
    });
  }, []);
  // Markdown / HTML / CSV tabs render as Preview by default (Cursor-style);
  // these records only store an explicit user toggle back to source.
  const [mdPreviewPaths, setMdPreviewPaths] = useState<Record<string, boolean>>({});
  const [htmlPreviewPaths, setHtmlPreviewPaths] = useState<Record<string, boolean>>({});
  const [csvPreviewPaths, setCsvPreviewPaths] = useState<Record<string, boolean>>({});
  const [previewErrors, setPreviewErrors] = useState<Record<string, string>>({});
  const [loadingPaths, setLoadingPaths] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [mode, setMode] = useState<
    | "code"
    | "browser"
    | "agentBrowser"
    | "mobile"
    | "graph"
    | "diff"
    | "board"
    | "evolve"
    | "project"
    | "templates"
    | "tests"
    // Git, Rules, Processes, Extensions and the session plan used to live
    // in a side sheet or the explorer sidebar. They are full workbench
    // tabs now, like Preview or Tasks. Outline stayed in the explorer: it
    // reads as a companion to the file tree.
    | "git"
    | "rules"
    | "processes"
    | "skills"
    | "guardrails"
    | "agi"
    | "extensions"
    | "plan"
  >("code");
  const [panelTab, setPanelTab] = useState<"files" | "search" | "outline">(
    "files",
  );
  const [diffFile, setDiffFile] = useState<string | null>(null);
  // Bumped by the chat's checkpoint CTA; DevGitPanel switches to its
  // Checkpoints tab when it changes.
  const [checkpointsSignal, setCheckpointsSignal] = useState(0);
  const [reveal, setReveal] = useState<{ path: string; line: number; nonce: number } | null>(null);
  // Start empty: Preview only shows a URL once a project server is discovered
  // or the agent opens one via open_preview. Never invent a default port.
  const [browserUrl, setBrowserUrl] = useState("");
  const [browserSrc, setBrowserSrc] = useState<string | null>(null);
  const [browserNonce, setBrowserNonce] = useState(0);
  const [browserSelfError, setBrowserSelfError] = useState<string | null>(null);
  const [browserStarting, setBrowserStarting] = useState(false);
  // Instrumented preview: target port proxied for telemetry capture.
  const [previewTargetPort, setPreviewTargetPort] = useState<number | null>(null);
  const [showPreviewConsole, setShowPreviewConsole] = useState(false);
  const [previewProblemCount, setPreviewProblemCount] = useState(0);
  const previewAutoTriedRef = useRef(false);
  const [mobileAutoStart, setMobileAutoStart] = useState(false);
  // Chat checkpoint CTA: open Git > Checkpoints. The pending consumption
  // covers the arrival path where App had to switch to the Code module
  // first and this workbench mounted after the event was dispatched.
  useEffect(() => {
    const openCheckpoints = () => {
      consumePendingOpenCheckpoints();
      setMode("git");
      setCheckpointsSignal((n) => n + 1);
    };
    if (consumePendingOpenCheckpoints()) {
      setMode("git");
      setCheckpointsSignal((n) => n + 1);
    }
    window.addEventListener(OPEN_CHECKPOINTS_EVENT, openCheckpoints);
    return () => window.removeEventListener(OPEN_CHECKPOINTS_EVENT, openCheckpoints);
  }, []);
  // Chat "View plan": open the session plan in this column, not a Sheet.
  useEffect(() => {
    const openPlan = () => {
      consumePendingOpenSessionPlan();
      onRevealWorkbench?.();
      setMode("plan");
    };
    if (consumePendingOpenSessionPlan()) {
      onRevealWorkbench?.();
      setMode("plan");
    }
    window.addEventListener(OPEN_SESSION_PLAN_EVENT, openPlan);
    return () => window.removeEventListener(OPEN_SESSION_PLAN_EVENT, openPlan);
  }, [onRevealWorkbench]);
  // Journal task title: bring the board up; the panel selects the card itself.
  useEffect(() => {
    const openBoard = () => {
      consumePendingOpenBoard();
      onRevealWorkbench?.();
      setMode("board");
    };
    if (consumePendingOpenBoard()) {
      onRevealWorkbench?.();
      setMode("board");
    }
    window.addEventListener(OPEN_BOARD_TASK_EVENT, openBoard);
    return () => window.removeEventListener(OPEN_BOARD_TASK_EVENT, openBoard);
  }, [onRevealWorkbench]);

  const chatId = treeSessionChatId(sessionKey);
  // Live mirror of the agent's headless browser (CDP screencast frames).
  const [agentBrowser, setAgentBrowser] = useState<{
    id: string;
    live: boolean;
    url: string | null;
    frame: string | null;
    /** Size of the last frame, needed to map a click back onto the page. */
    frameWidth: number | null;
    frameHeight: number | null;
    actions: string[];
  } | null>(null);
  // While on, clicks and keystrokes go to the agent's browser instead of
  // being ignored. Off by default: the panel is a monitor first.
  const [agentBrowserTakeover, setAgentBrowserTakeover] = useState(false);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [problemsOpen, setProblemsOpen] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [debugState, setDebugState] = useState<DebugStatePayload | null>(null);
  // Re-render on breakpoint changes; the counter value itself is unused.
  const [, setBpTick] = useState(0);

  useEffect(() => subscribeBreakpoints(() => setBpTick((n) => n + 1)), []);
  const [quickOpen, setQuickOpen] = useState(false);
  const [symbolPicker, setSymbolPicker] = useState(false);
  const [commandPalette, setCommandPalette] = useState(false);
  const [blameEnabled, setBlameEnabled] = useState(true);
  const [blameByPath, setBlameByPath] = useState<
    Record<string, Record<number, GitBlameLine>>
  >({});
  const [locationPicker, setLocationPicker] = useState<{
    title: string;
    symbol?: string;
    items: DevLocationItem[];
  } | null>(null);
  const [terminals, setTerminals] = useState<TerminalTab[]>([]);
  const [activeTerminal, setActiveTerminal] = useState<string | null>(null);
  const [shells, setShells] = useState<TerminalShellInfo[]>([]);
  const [shellMenuOpen, setShellMenuOpen] = useState(false);

  // Status bar: connection, environment, git, per-file diagnostics.
  const [connection, setConnection] = useState<ConnectionStatus>(client.status);
  const [environment, setEnvironment] = useState<string | null>(null);
  const [environmentDistro, setEnvironmentDistro] = useState<string | null>(null);
  const [gitStatus, setGitStatus] = useState<GitStatusPayload | null>(null);
  const [ciStatus, setCiStatus] = useState<GithubCiStatusPayload | null>(null);
  const [gitChanges, setGitChanges] = useState<GitChangesPayload | null>(null);
  const [contextUsage, setContextUsage] = useState<ContextUsagePayload | null>(null);
  /** Last Tab / ghost completion timing shown in the status bar. */
  const [lastTabAssist, setLastTabAssist] = useState<{
    latencyMs: number;
    ttftMs?: number;
    route?: string;
    p50?: number;
    p90?: number;
    sampleCount?: number;
  } | null>(null);
  const [runtimeHealth, setRuntimeHealth] = useState<RuntimeHealth | null>(null);
  // Chats with a turn currently executing, across the whole gateway - the
  // "agents running" gauge, like Cursor's background-agent indicator.
  const [runningAgentChats, setRunningAgentChats] = useState<Set<string>>(
    () => new Set(),
  );
  const [diagnosticsByPath, setDiagnosticsByPath] = useState<
    Record<string, FileDiagnosticsPayload>
  >({});

  // The "workspace" root is navin's own ~/.navin/workspace. Knowing it is what
  // lets the workbench tell "no project is open" apart from "a project that
  // happens to be open", since the session scope silently falls back to it.
  const [internalWorkspacePath, setInternalWorkspacePath] = useState<string | null>(null);
  const [workspaceAccepted, setWorkspaceAccepted] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchFsRoots(token)
      .then((payload) => {
        if (cancelled) return;
        setEnvironment(payload.environment);
        setEnvironmentDistro(payload.distro ?? null);
        const workspace = payload.roots.find((entry) => entry.kind === "workspace");
        setInternalWorkspacePath(workspace?.path ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token]);

  const currentRoot = projectPath ?? rootPath;
  const onInternalWorkspace = sameWorkspacePath(currentRoot, internalWorkspacePath);
  // Nothing to go back to yet: the roster of real projects is empty, so the
  // only honest thing to show is how to open or create one.
  const hasKnownProject = (recentProjects ?? []).some(
    (entry) => !sameWorkspacePath(entry.path, internalWorkspacePath),
  );
  // `projectsReady` guards the load window: the roster arrives with the sidebar
  // state, and treating "not here yet" as "none" flashed the gate on every
  // Code open, even for someone with a dozen projects.
  const rosterReady = projectsReady !== false;
  const showProjectGate =
    rosterReady && onInternalWorkspace && !hasKnownProject && !workspaceAccepted;

  useEffect(() => {
    if (!onInternalWorkspace) setWorkspaceAccepted(false);
  }, [onInternalWorkspace]);

  const gitRoot = currentRoot;
  const environmentLabel = useMemo(
    () =>
      projectEnvironmentLabel({
        projectPath: currentRoot,
        environment,
        distro: environmentDistro,
      }),
    [currentRoot, environment, environmentDistro],
  );
  const gitRefreshInFlight = useRef(false);
  const refreshGitStatus = useCallback(() => {
    if (gitRefreshInFlight.current) return;
    gitRefreshInFlight.current = true;
    const jobs: Promise<unknown>[] = [
      fetchContextUsage(token, treeKey)
        .then(setContextUsage)
        .catch(() => setContextUsage(null)),
      fetchGithubChecks(token, treeKey)
        .then(setCiStatus)
        .catch(() => setCiStatus(null)),
    ];
    if (gitRoot) {
      jobs.push(
        fetchGitStatus(token, gitRoot)
          .then(setGitStatus)
          .catch(() => setGitStatus(null)),
        fetchGitChanges(token, treeKey)
          .then(setGitChanges)
          .catch(() => setGitChanges(null)),
      );
    }
    void Promise.allSettled(jobs).finally(() => {
      gitRefreshInFlight.current = false;
    });
  }, [gitRoot, token, treeKey]);

  useEffect(() => {
    setGitStatus(null);
    setGitChanges(null);
    setCiStatus(null);
    refreshGitStatus();
    // Polling alone leaves the footer lying for up to 12 s after a checkout
    // made elsewhere; refreshing on focus catches the common case of switching
    // branches in a terminal and coming back to the window.
    const timer = window.setInterval(refreshGitStatus, 12_000);
    window.addEventListener("focus", refreshGitStatus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshGitStatus);
    };
  }, [refreshGitStatus]);

  useEffect(() => client.onStatus(setConnection), [client]);

  useEffect(
    () =>
      client.onRunStatus((runChatId, startedAt) => {
        setRunningAgentChats((prev) => {
          const running = startedAt !== null;
          if (running === prev.has(runChatId)) return prev;
          const next = new Set(prev);
          if (running) next.add(runChatId);
          else next.delete(runChatId);
          return next;
        });
      }),
    [client],
  );

  const refreshDiagnostics = useCallback(
    (path: string) => {
      void fetchFileDiagnostics(token, path, currentRoot ?? "")
        .then((payload) =>
          setDiagnosticsByPath((prev) => ({ ...prev, [path]: payload })),
        )
        .catch(() => undefined);
    },
    [currentRoot, token],
  );

  // Workspace-mode LSP (pyright diagnosticMode=workspace) already computes
  // errors in files the editor never opened. Pull them so Problems stays honest
  // after agent edits that break a sibling import.
  const refreshWorkspaceDiagnostics = useCallback(() => {
    const root = currentRoot || projectPath;
    if (!root) return;
    const seeds = tabs.map((tab) => tab.path);
    if (splitTab && !seeds.includes(splitTab)) seeds.push(splitTab);
    void fetchWorkspaceDiagnostics(token, root, { seeds })
      .then((payload) => {
        setDiagnosticsByPath((prev) => {
          const next = { ...prev };
          for (const [path, filePayload] of Object.entries(payload.files ?? {})) {
            next[path] = filePayload;
          }
          return next;
        });
      })
      .catch(() => undefined);
  }, [currentRoot, projectPath, splitTab, tabs, token]);

  useEffect(() => {
    if (!problemsOpen) return;
    refreshWorkspaceDiagnostics();
  }, [problemsOpen, refreshWorkspaceDiagnostics]);

  // Agent edits can break a sibling file the user never opened - pull the
  // workspace diagnostic set when a write finishes.
  useEffect(() => {
    if (!chatId) return;
    return client.onChat(chatId, (ev) => {
      if (ev.event !== "file_edit") return;
      const done = (ev.edits ?? []).some(
        (edit) => fileEditIsOnDisk(edit),
      );
      if (done) refreshWorkspaceDiagnostics();
    });
  }, [chatId, client, refreshWorkspaceDiagnostics]);

  // The file the user is actually typing in: the right pane when the split
  // view has focus there, the regular active tab otherwise. Kept in a ref so
  // the editor callbacks below stay identity-stable across pane switches.
  const focusedFile =
    splitTab != null && focusedPane === "right" ? splitTab : activeTab;
  const focusedFileRef = useRef(focusedFile);
  focusedFileRef.current = focusedFile;
  const splitTabRef = useRef(splitTab);
  splitTabRef.current = splitTab;
  const focusedPaneRef = useRef(focusedPane);
  focusedPaneRef.current = focusedPane;

  // Publish open tabs so Agent Code turns can pack them into runtime context,
  // and so the artifact canvas can tell it is about to render something this
  // editor already shows. The fingerprint comes from the saved body rather
  // than the draft: it identifies the file the agent wrote, and hashing the
  // draft would mean rehashing the whole buffer on every keystroke.
  useEffect(() => {
    const paths = tabs.map((tab) => tab.path);
    if (splitTab && !paths.includes(splitTab)) paths.push(splitTab);
    setDevOpenFiles(
      paths.map((path) => ({
        path,
        hash: fingerprintContent(previews[path]?.content ?? ""),
      })),
    );
    return () => setDevOpenFiles([]);
  }, [previews, splitTab, tabs]);

  // Editor completion source: project symbols from the code index. Results are
  // memoized per prefix because CodeMirror queries on nearly every keystroke.
  const symbolCacheRef = useRef(new Map<string, ProjectSymbol[]>());
  useEffect(() => {
    symbolCacheRef.current.clear();
  }, [treeKey]);

  const lookupSymbols = useCallback(
    async (query: string): Promise<ProjectSymbol[]> => {
      const path = focusedFileRef.current;
      const cacheKey = `${path ?? ""}\u0000${query}`;
      const cached = symbolCacheRef.current.get(cacheKey);
      if (cached) return cached;
      const payload = await fetchProjectSymbols(token, treeKey, query, {
        path: path ?? undefined,
      });
      const items = payload.items ?? [];
      if (symbolCacheRef.current.size > 200) symbolCacheRef.current.clear();
      symbolCacheRef.current.set(cacheKey, items);
      return items;
    },
    [token, treeKey],
  );

  // Inline AI: ghost-text completion at the caret, and Cmd+K editing of a
  // selection. Both are disabled without an open file, since the model needs a
  // path and language to produce anything useful.
  const recordTabAssistMeta = useCallback(
    (meta: AssistMeta, clientLatencyMs: number) => {
      const latencyMs =
        typeof meta.latency_ms === "number" && meta.latency_ms >= 0
          ? meta.latency_ms
          : clientLatencyMs;
      const ttftMs =
        typeof meta.ttft_ms === "number" && meta.ttft_ms >= 0
          ? meta.ttft_ms
          : undefined;
      recordTabLatency({
        latencyMs,
        ...(ttftMs !== undefined ? { ttftMs } : {}),
        ...(meta.route ? { route: meta.route } : {}),
      });
      const pct = tabLatencyPercentiles();
      setLastTabAssist({
        latencyMs,
        ...(ttftMs !== undefined ? { ttftMs } : {}),
        ...(meta.route ? { route: meta.route } : {}),
        ...(pct
          ? { p50: pct.p50, p90: pct.p90, sampleCount: pct.count }
          : {}),
      });
    },
    [],
  );

  useEffect(() => {
    if (!token) {
      setRuntimeHealth(null);
      return;
    }
    let cancelled = false;
    const poll = () => {
      void fetchRuntimeHealth(token)
        .then((payload) => {
          if (!cancelled) setRuntimeHealth(payload);
        })
        .catch(() => {
          if (!cancelled) setRuntimeHealth(null);
        });
    };
    poll();
    const timer = window.setInterval(poll, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token]);

  const requestCompletion = useCallback(
    async (
      request: { prefix: string; suffix: string },
      signal: AbortSignal,
      onPartial?: (text: string) => void,
    ): Promise<string> => {
      const path = focusedFileRef.current;
      if (!path) return "";
      const started = performance.now();
      let serverMeta: AssistMeta = {};
      try {
        // Prefer WS streaming so the ghost paints token-by-token (Cursor feel).
        const text = await client.requestAssistComplete(
          {
            path,
            prefix: request.prefix,
            suffix: request.suffix,
            recentEdits: recentEditsRef.current.snapshot(),
          },
          {
            signal,
            onPartial,
            onMeta: (meta) => {
              serverMeta = meta;
            },
          },
        );
        recordTabAssistMeta(serverMeta, Math.round(performance.now() - started));
        return text;
      } catch {
        /* fall through to HTTP one-shot */
      }
      try {
        const payload = await fetchInlineCompletion(
          token,
          treeKey,
          {
            path,
            prefix: request.prefix,
            suffix: request.suffix,
            recentEdits: recentEditsRef.current.snapshot(),
          },
          signal,
        );
        recordTabAssistMeta(
          {
            ...(payload.model ? { model: payload.model } : {}),
            ...(payload.route ? { route: payload.route } : {}),
            ...(typeof payload.latency_ms === "number"
              ? { latency_ms: payload.latency_ms }
              : {}),
            ...(typeof payload.ttft_ms === "number" ? { ttft_ms: payload.ttft_ms } : {}),
          },
          Math.round(performance.now() - started),
        );
        return payload.completion ?? "";
      } catch {
        // A failed suggestion is not worth interrupting typing over.
        return "";
      }
    },
    [client, recordTabAssistMeta, token, treeKey],
  );

  const requestInlineEdit = useCallback(
    async (
      args: { selection: string; instruction: string; prefix: string; suffix: string },
      signal: AbortSignal,
      onPartial?: (text: string) => void,
    ): Promise<string> => {
      const path = focusedFileRef.current;
      if (!path) throw new Error("no file is open");
      try {
        return await client.requestAssistEdit(
          { path, ...args },
          { signal, onPartial, timeoutMs: 45_000 },
        );
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") throw error;
        /* fall through to HTTP one-shot */
      }
      const payload = await fetchInlineEdit(
        token,
        treeKey,
        { path, ...args },
        signal,
      );
      return payload.replacement;
    },
    [client, token, treeKey],
  );

  // Pending agent-edit review: files the agent changed stay provisional until
  // the user accepts (keep) or rejects (restore the pre-edit content) them.
  const [reviewChanges, setReviewChanges] = useState<ReviewChangeEntry[]>([]);
  const [rejectAllOpen, setRejectAllOpen] = useState(false);
  /** Shown after the last pending agent edit is accepted - keep the happy path on #/code. */
  const [postReviewNextSteps, setPostReviewNextSteps] = useState(false);

  useEffect(() => {
    if (reviewChanges.length > 0) {
      setPostReviewNextSteps(false);
    }
  }, [reviewChanges.length]);
  const [reviewFiles, setReviewFiles] = useState<Record<string, ReviewFilePayload>>({});
  const reviewUpdatedRef = useRef<Record<string, string>>({});
  // Accept/Reject can fail (a 409 racing the agent, a file gone, the gateway
  // restarting). Swallowing that left the hunk on screen with no explanation.
  const notifyReviewError = useCallback(
    (err: unknown) => {
      publishNotification({
        level: "error",
        source: "session",
        title: tx("dev.reviewActionFailed", "Review action failed"),
        detail: err instanceof Error ? err.message : String(err),
        key: "dev:review:error",
      });
    },
    [tx],
  );

  const reviewRefreshInFlight = useRef(false);
  const refreshReview = useCallback(() => {
    if (reviewRefreshInFlight.current) return;
    reviewRefreshInFlight.current = true;
    void fetchReviewChanges(token, treeKey)
      .then((payload) => setReviewChanges(payload.changes))
      .catch(() => undefined)
      .finally(() => {
        reviewRefreshInFlight.current = false;
      });
  }, [token, treeKey]);

  useEffect(() => {
    setReviewChanges([]);
    setReviewFiles({});
    reviewUpdatedRef.current = {};
    refreshReview();
    // 3s: the backend now streams agent edits into the pending store during
    // the turn (live flush), so a tighter poll makes them visibly "live".
    const timer = window.setInterval(refreshReview, 3_000);
    return () => window.clearInterval(timer);
  }, [refreshReview]);

  const reviewByPath = useMemo(() => {
    const map = new Map<string, ReviewChangeEntry>();
    for (const change of reviewChanges) map.set(change.path, change);
    return map;
  }, [reviewChanges]);

  const reviewStats = useMemo(() => reviewLineStats(reviewChanges), [reviewChanges]);

  const reviewDirs = useMemo(() => {
    const dirs = new Set<string>();
    for (const change of reviewChanges) {
      let idx = change.path.lastIndexOf("/");
      while (idx > 0) {
        dirs.add(change.path.slice(0, idx));
        idx = change.path.lastIndexOf("/", idx - 1);
      }
    }
    return dirs;
  }, [reviewChanges]);

  // Keep open editors in sync with agent edits: refresh clean previews when a
  // pending file first appears or is touched again by the agent.
  useEffect(() => {
    const previous = reviewUpdatedRef.current;
    const nextSeen: Record<string, string> = {};
    for (const change of reviewChanges) {
      const stamp = change.updated_at ?? "";
      nextSeen[change.path] = stamp;
      if (previous[change.path] === stamp) continue;
      if (previews[change.path] === undefined) continue;
      if (drafts[change.path] !== undefined) continue;
      const path = change.path;
      const gen = bumpPreviewGen(path);
      void fetchDevFilePreview(token, treeKey, path, "", currentRoot)
        .then((payload) => {
          if (previewGenRef.current[path] !== gen) return;
          setPreviews((prev) =>
            prev[path] !== undefined ? { ...prev, [path]: payload } : prev,
          );
        })
        .catch(() => undefined);
    }
    reviewUpdatedRef.current = nextSeen;
  }, [bumpPreviewGen, currentRoot, drafts, previews, reviewChanges, token, treeKey]);

  // Light git blame for the focused file (current-line attribution strip).
  useEffect(() => {
    if (!blameEnabled || !focusedFile) return;
    if (blameByPath[focusedFile]) return;
    if (previewErrors[focusedFile]) return;
    let cancelled = false;
    void fetchGitBlame(token, treeKey, focusedFile, "", currentRoot)
      .then((payload) => {
        if (cancelled) return;
        const map: Record<number, GitBlameLine> = {};
        for (const item of payload.items ?? []) {
          if (item.line > 0) map[item.line] = item;
        }
        setBlameByPath((prev) => ({ ...prev, [focusedFile]: map }));
      })
      .catch(() => {
        if (cancelled) return;
        setBlameByPath((prev) => ({ ...prev, [focusedFile]: {} }));
      });
    return () => {
      cancelled = true;
    };
  }, [blameByPath, blameEnabled, currentRoot, focusedFile, previewErrors, token, treeKey]);

  // Baseline (pre-edit content) of the active file, for inline highlighting.
  useEffect(() => {
    if (!activeTab) return;
    if (!reviewByPath.has(activeTab)) return;
    if (reviewFiles[activeTab] !== undefined) return;
    void fetchReviewFile(token, treeKey, activeTab)
      .then((payload) =>
        setReviewFiles((prev) => ({ ...prev, [activeTab]: payload })),
      )
      .catch(() => undefined);
  }, [activeTab, reviewByPath, reviewFiles, token, treeKey]);

  // Layout: collapsible + resizable panes (explorer width, terminal height).
  // Closed on first paint. Desktop used to open the tree automatically and it
  // ate the editor; the toolbar "Explorer" control turns it back on.
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [railFilesOpen, setRailFilesOpen] = useState(false);

  // Only auto-collapse when the viewport *becomes* narrow. Re-firing on every
  // true render would fight the user reopening the explorer on a small window.
  const wasNarrowRef = useRef(isNarrow);
  useEffect(() => {
    if (isNarrow && !wasNarrowRef.current) {
      setExplorerOpen(false);
    }
    wasNarrowRef.current = isNarrow;
  }, [isNarrow]);

  // Docked Code keeps explorer and editor independent: auto-toggling the tree
  // when the chat is maximized made the explorer look like it "ate" the shell
  // (especially after hiding the host sidebar and reclaiming width). Users open
  // or hide the tree themselves; widths are clamped below so three columns fit.
  const [explorerWidth, setExplorerWidth] = useState(240);
  // Lets the app shell align the maximized chat right after the explorer
  // column (which keeps its own width and stays resizable while collapsed).
  // Reports 0 when hidden so the chat can reclaim the freed space, and also
  // while the project gate is up: that screen has no explorer, so a stale
  // width would leave a dead band on the left of the chat.
  useEffect(() => {
    onExplorerWidthChange?.(explorerOpen && !showProjectGate ? explorerWidth : 0);
  }, [explorerOpen, explorerWidth, onExplorerWidthChange, showProjectGate]);
  // Chat-maximized: any navigation that targets the hidden center pane
  // (opening a file from the explorer, jumping to problems) restores the
  // workbench automatically instead of appearing to do nothing. Rail entries
  // also restore explicitly; by the time this effect runs after their click,
  // `collapsed` is already false, so nothing double-toggles.
  const collapsedNavRef = useRef({ activeTab, mode, problemsOpen });
  useEffect(() => {
    const prev = collapsedNavRef.current;
    collapsedNavRef.current = { activeTab, mode, problemsOpen };
    if (!collapsed) return;
    if (
      prev.activeTab !== activeTab
      || prev.mode !== mode
      || (!prev.problemsOpen && problemsOpen)
    ) {
      onRevealWorkbench?.();
    }
  }, [activeTab, mode, problemsOpen, collapsed, onRevealWorkbench]);
  const [terminalHeight, setTerminalHeight] = useState(260);
  const [terminalMaximized, setTerminalMaximized] = useState(false);
  const workbenchModeRef = useRef(mode);
  useEffect(() => {
    if (workbenchModeRef.current === mode) return;
    workbenchModeRef.current = mode;
    setTerminalOpen(false);
    setTerminalMaximized(false);
  }, [mode]);
  const [previewFullscreen, setPreviewFullscreen] = useState(false);
  useEffect(() => {
    setPreviewFullscreen(false);
  }, [activeTab]);
  useEffect(() => {
    if (!previewFullscreen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setPreviewFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [previewFullscreen]);
  const [isFullscreen, setIsFullscreen] = useState(false);
  useEffect(() => {
    const syncFullscreen = () =>
      setIsFullscreen(Boolean(
        document.fullscreenElement
        || (document as Document & { webkitFullscreenElement?: Element | null }).webkitFullscreenElement,
      ));
    syncFullscreen();
    document.addEventListener("fullscreenchange", syncFullscreen);
    document.addEventListener("webkitfullscreenchange", syncFullscreen);
    return () => {
      document.removeEventListener("fullscreenchange", syncFullscreen);
      document.removeEventListener("webkitfullscreenchange", syncFullscreen);
    };
  }, []);
  const toggleFullscreen = useCallback(() => {
    const doc = document as Document & {
      webkitExitFullscreen?: () => Promise<void> | void;
      webkitFullscreenElement?: Element | null;
    };
    const root = document.documentElement as HTMLElement & {
      webkitRequestFullscreen?: () => Promise<void> | void;
    };
    const active = document.fullscreenElement || doc.webkitFullscreenElement;
    if (active) {
      const exit = document.exitFullscreen?.bind(document) ?? doc.webkitExitFullscreen?.bind(doc);
      void Promise.resolve(exit?.()).catch(() => undefined);
      return;
    }
    // Fullscreen the app root, not the editor pane: the host sidebar stays.
    const enter = root.requestFullscreen?.bind(root) ?? root.webkitRequestFullscreen?.bind(root);
    void Promise.resolve(enter?.()).catch(() => undefined);
  }, []);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const railWidth = railWidthFor(railDensity);

  // When the host sidebar collapses, the shell grows: clamp the tree so it
  // cannot keep a width that leaves no room for chat + editor/rail.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const clamp = () => {
      const shell = el.clientWidth;
      if (shell <= 0) return;
      const right = dockRight
        ? (collapsed ? railWidth : Math.min(panelWidth, Math.max(240, shell * 0.4)))
        : 0;
      const maxW = Math.max(160, Math.min(520, shell - right - 280));
      setExplorerWidth((width) => (width > maxW ? maxW : width));
    };
    clamp();
    const observer = new ResizeObserver(clamp);
    observer.observe(el);
    return () => observer.disconnect();
  }, [collapsed, dockRight, panelWidth, railWidth]);

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
          // Leave room for the chat (or the flex center) and the docked panel /
          // rail so dragging the tree never swallows the shell.
          const shell = containerRef.current?.clientWidth ?? 800;
          const right = dockRight
            ? (collapsed ? railWidth : Math.min(panelWidth, Math.max(240, shell * 0.4)))
            : 0;
          const maxW = Math.max(160, Math.min(520, shell - right - 280));
          setExplorerWidth(Math.min(maxW, Math.max(160, next)));
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
    [collapsed, dockRight, explorerWidth, panelWidth, railWidth, terminalHeight],
  );

  const spawnTerminal = useCallback(
    (shell?: string, cwd?: string, options?: { sandbox?: boolean }) => {
      terminalCounter += 1;
      const id = `term-${Date.now().toString(36)}-${terminalCounter}`;
      const label = shell || shells.find((s) => s.default)?.name || "shell";
      const startDir = terminalStartDir(cwd, currentRoot);
      // A terminal opened on a folder is worth naming after it; the shell name
      // repeated across five tabs tells the user nothing.
      const folder = cwd ? cwd.split(/[\\/]/).filter(Boolean).pop() : null;
      setTerminals((prev) => [
        ...prev,
        {
          id,
          shell,
          cwd: startDir,
          title: folder ?? `${label} ${terminalCounter}`,
          exited: false,
          ...(options?.sandbox ? { sandbox: true } : {}),
        },
      ]);
      setActiveTerminal(id);
      setTerminalOpen(true);
      setTerminalMaximized(true);
      setShellMenuOpen(false);
    },
    [currentRoot, shells],
  );

  const openTerminalPanel = useCallback(() => {
    setTerminalOpen(true);
    setTerminalMaximized(true);
    setTerminals((prev) => {
      if (prev.length === 0) {
        terminalCounter += 1;
        const id = `term-${Date.now().toString(36)}-${terminalCounter}`;
        const label = shells.find((s) => s.default)?.name || "shell";
        setActiveTerminal(id);
        return [
          {
            id,
            shell: undefined,
            cwd: terminalStartDir(null, currentRoot),
            title: `${label} ${terminalCounter}`,
            exited: false,
          },
        ];
      }
      return prev;
    });
  }, [currentRoot, shells]);

  const closeTerminal = useCallback(
    (id: string) => {
      // Agent tabs have no PTY behind them; there is nothing to close
      // server-side, only the local scrollback to drop.
      if (id.startsWith("agent-")) {
        agentTermBuffersRef.current.delete(id);
        agentTermListenersRef.current.delete(id);
      } else {
      client.closeTerminal(id);
      }
      setTerminals((prev) => {
        const next = prev.filter((term) => term.id !== id);
        setActiveTerminal((current) =>
          current === id ? (next.length ? next[next.length - 1].id : null) : current,
        );
        if (next.length === 0) {
          setTerminalOpen(false);
          setTerminalMaximized(false);
        }
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

  // The gateway confirms whether an isolated terminal really is confined; a
  // host without the sandbox helper opens a plain shell and the tab must not
  // keep claiming otherwise.
  const handleTerminalSandbox = useCallback((id: string, confined: boolean) => {
    setTerminals((prev) =>
      prev.map((term) =>
        term.id === id && Boolean(term.sandbox) !== confined
          ? { ...term, sandbox: confined }
          : term,
      ),
    );
  }, []);

  // ---- Agent exec terminals ------------------------------------------------
  // Read-only tabs mirroring the commands the agent runs through its exec
  // tool (Cursor-style). Scrollback lives here so a tab that was hidden or
  // remounted replays its history; live chunks fan out to mounted terminals.
  const agentTermBuffersRef = useRef<Map<string, string>>(new Map());
  const agentTermListenersRef = useRef<Map<string, Set<(chunk: string) => void>>>(
    new Map(),
  );

  const subscribeAgentTerm = useCallback(
    (id: string, handler: (chunk: string) => void) => {
      let handlers = agentTermListenersRef.current.get(id);
      if (!handlers) {
        handlers = new Set();
        agentTermListenersRef.current.set(id, handlers);
      }
      handlers.add(handler);
      return () => {
        handlers.delete(handler);
      };
    },
    [],
  );

  const agentTermBacklog = useCallback(
    (id: string) => agentTermBuffersRef.current.get(id) ?? "",
    [],
  );

  const pushAgentTermChunk = useCallback((id: string, chunk: string) => {
    const buffers = agentTermBuffersRef.current;
    const next = (buffers.get(id) ?? "") + chunk;
    buffers.set(
      id,
      next.length > AGENT_TERM_BUFFER_CHARS
        ? next.slice(-AGENT_TERM_BUFFER_CHARS)
        : next,
    );
    for (const handler of agentTermListenersRef.current.get(id) ?? []) {
      handler(chunk);
    }
  }, []);

  useEffect(
    () =>
      client.onAgentExec((update) => {
        const tabId = `agent-${update.id}`;
        if (update.phase === "start") {
          agentTermBuffersRef.current.set(tabId, "");
          if (update.command) {
            const where = update.cwd ? `\x1b[2m${update.cwd}\x1b[0m\r\n` : "";
            pushAgentTermChunk(
              tabId,
              `${where}\x1b[1m$ ${update.command}\x1b[0m\r\n`,
            );
          }
          setTerminals((prev) =>
            prev.some((term) => term.id === tabId)
              ? prev
              : [
                  ...prev,
                  {
                    id: tabId,
                    kind: "agent",
                    background: update.background,
                    title: agentTermTitle(update.command),
                    exited: false,
                  },
                ],
          );
          setTerminalOpen(true);
          // Show the agent's command, but never steal the tab from a user
          // who is working in their own shell.
          setActiveTerminal((current) =>
            current && !current.startsWith("agent-") ? current : tabId,
          );
          return;
        }
        if (update.phase === "output") {
          if (update.data) pushAgentTermChunk(tabId, update.data);
          return;
        }
        const code = update.exitCode ?? null;
        pushAgentTermChunk(tabId, `\r\n\x1b[2m[exit ${code ?? "?"}]\x1b[0m\r\n`);
        setTerminals((prev) =>
          prev.map((term) =>
            term.id === tabId ? { ...term, exited: true, exitCode: code } : term,
          ),
        );
      }),
    [client, pushAgentTermChunk],
  );

  // Agent browser live view: frames + action feed, Cursor-style tab that
  // appears when the agent starts driving its headless browser.
  /**
   * Where a pointer event landed, in the streamed frame's own pixels.
   *
   * The frame is letterboxed by ``object-contain``, so the bars around it are
   * part of the element but not part of the page: a click there means nothing
   * and is dropped rather than mapped onto an edge.
   */
  const agentBrowserPoint = useCallback(
    (event: { currentTarget: HTMLImageElement; clientX: number; clientY: number }) => {
      const image = event.currentTarget;
      const rect = image.getBoundingClientRect();
      const width = image.naturalWidth;
      const height = image.naturalHeight;
      if (!width || !height || !rect.width || !rect.height) return null;
      const scale = Math.min(rect.width / width, rect.height / height);
      const x = (event.clientX - rect.left - (rect.width - width * scale) / 2) / scale;
      const y = (event.clientY - rect.top - (rect.height - height * scale) / 2) / scale;
      if (x < 0 || y < 0 || x > width || y > height) return null;
      return { x, y, width, height };
    },
    [],
  );

  const sendAgentBrowserClick = useCallback(
    (event: ReactMouseEvent<HTMLImageElement>) => {
      if (!chatId) return;
      const point = agentBrowserPoint(event);
      if (!point) return;
      event.currentTarget.focus();
      client.agentBrowserInput(chatId, "click", { ...point, count: event.detail || 1 });
    },
    [agentBrowserPoint, chatId, client],
  );

  const sendAgentBrowserScroll = useCallback(
    (event: ReactWheelEvent<HTMLImageElement>) => {
      if (!chatId) return;
      const point = agentBrowserPoint(event);
      if (!point) return;
      client.agentBrowserInput(chatId, "scroll", {
        ...point,
        dx: event.deltaX,
        dy: event.deltaY,
      });
    },
    [agentBrowserPoint, chatId, client],
  );

  const sendAgentBrowserKey = useCallback(
    (event: ReactKeyboardEvent<HTMLImageElement>) => {
      if (!chatId) return;
      const { key } = event;
      // A printable character is typed, so a field that watches each keystroke
      // reacts; anything named is pressed, modifiers included.
      if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        client.agentBrowserInput(chatId, "text", { text: key });
        return;
      }
      if (key === "Shift" || key === "Control" || key === "Alt" || key === "Meta") return;
      const combo = [
        event.ctrlKey ? "Control" : null,
        event.altKey ? "Alt" : null,
        event.metaKey ? "Meta" : null,
        event.shiftKey && key.length === 1 ? "Shift" : null,
        key,
      ]
        .filter(Boolean)
        .join("+");
      event.preventDefault();
      client.agentBrowserInput(chatId, "key", { key: combo });
    },
    [chatId, client],
  );

  useEffect(
    () =>
      client.onAgentBrowser((update) => {
        if (update.phase === "exit") {
          setAgentBrowser((prev) =>
            prev && prev.id === update.id ? { ...prev, live: false } : prev,
          );
          return;
        }
        setAgentBrowser((prev) => {
          const base =
            prev && prev.id === update.id
              ? prev
              : {
                  id: update.id,
                  live: true,
                  url: null,
                  frame: null,
                  frameWidth: null,
                  frameHeight: null,
                  actions: [],
                };
          const next = { ...base, live: true };
          if (update.url) next.url = update.url;
          if (update.phase === "frame" && update.data) {
            next.frame = update.data;
            if (update.width) next.frameWidth = update.width;
            if (update.height) next.frameHeight = update.height;
          }
          if (update.phase === "action" && update.action) {
            next.actions = [...base.actions.slice(-49), update.action];
          }
          return next;
        });
        if (update.phase === "start") {
          setMode("agentBrowser");
        }
      }),
    [client],
  );

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

  // The agent's open_terminal tool: opens the panel with a real PTY, exactly
  // as if the user had clicked the terminal button themselves.
  useEffect(
    () =>
      client.onTerminalOpenRequest((request) => {
        spawnTerminal(request.shell, request.cwd);
      }),
    [client, spawnTerminal],
  );

  // Per-directory scan generation. Switching project fires a root reload while
  // the previous one is still out; without this the slower answer wins and the
  // explorer shows the folder the user just left.
  const treeGenRef = useRef<Record<string, number>>({});
  // The root the cached folders were scanned under. When a root fetch comes
  // back for a different directory (the restored project confirmation landing
  // after the first fetch, a scope re-resolve), every cached folder belongs to
  // the old tree and is dropped; when the root is unchanged, the cache and the
  // expanded state survive so a background refresh never collapses the tree.
  const rootPathRef = useRef<string | null>(null);
  const loadDir = useCallback(
    async (path: string | null, opts?: { keepIfEmpty?: boolean; retainNames?: string[] }) => {
      const nodeKey = path ?? "__root__";
      const gen = (treeGenRef.current[nodeKey] ?? 0) + 1;
      treeGenRef.current[nodeKey] = gen;
      setNodes((prev) => {
        const keep = prev[nodeKey]?.entries ?? [];
        return {
          ...prev,
          [nodeKey]: {
            entries: keep,
            loading: keep.length === 0,
            error: null,
          },
        };
      });
      try {
        const payload = await fetchFileTree(token, treeKey, path ?? undefined);
        if (treeGenRef.current[nodeKey] !== gen) return;
        if (!path) {
          const prevRoot = rootPathRef.current;
          rootPathRef.current = payload.path;
          setRootPath(payload.path);
          if (prevRoot !== null && !sameTreeDir(prevRoot, payload.path)) {
            setNodes({
              [nodeKey]: { entries: payload.entries, loading: false, error: null },
            });
            setExpanded(new Set());
            return;
          }
        }
        setNodes((prev) => {
          const keys = path
            ? treeNodeKeysToUpdate(prev, path, payload.path, rootPathRef.current)
            : [nodeKey];
          const next = { ...prev };
          for (const key of keys) {
            const previous = next[key]?.entries ?? [];
            const incoming = payload.entries;
            next[key] = {
              entries: mergeFileTreeListing(previous, incoming, opts),
              loading: false,
              error: null,
            };
          }
          return next;
        });
      } catch (err) {
        if (treeGenRef.current[nodeKey] !== gen) return;
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

  // Reload the tree when the project changes or the fetch identity (token /
  // session) does. Only the project root changing may wipe anything: a token
  // refresh or sessionKey churn happens on a timer, and wiping the node cache
  // there collapsed the whole explorer every few minutes ("the project keeps
  // reloading"). On an unchanged root, loadDir refreshes entries in place and
  // the expanded folders stay open.
  const projectPathRef = useRef(projectPath);
  useEffect(() => {
    const prevPath = projectPathRef.current;
    const prevNorm = (prevPath ?? "").replace(/\\/g, "/").replace(/\/+$/, "");
    const nextNorm = (projectPath ?? "").replace(/\\/g, "/").replace(/\/+$/, "");
    const projectChanged = prevNorm !== nextNorm;
    projectPathRef.current = projectPath;

    if (projectChanged) {
      rootPathRef.current = null;
    setNodes({});
    setExpanded(new Set());
    setTabs([]);
    setActiveTab(null);
      // The split pane has to go with the tabs. Left behind, it still holds a
      // path from the old project (which no longer resolves) and, worse, keeps
      // the focus on the right pane: the next file click lands there while the
      // centre still shows the empty state, so opening a file looks dead.
      setSplitTab(null);
      setFocusedPane("left");
    setPreviews({});
      setMdPreviewPaths({});
      setHtmlPreviewPaths({});
      setCsvPreviewPaths({});
    setDrafts({});
      setPreviewErrors({});
      setLoadingPaths({});
      setBlameByPath({});
    }
    void loadDir(null);
  }, [loadDir, projectPath]);

  // Pre-chat, the tree is served for the shared "webui-dev" scope. App.tsx
  // persists the restored project there on startup; the confirmation can land
  // after our first fetch, so reload when it does or the explorer keeps
  // showing the gateway's internal workspace. loadDir itself notices when the
  // root actually moved and drops the stale cache then - session updates that
  // resolve to the same root (heartbeats, background turns) refresh in place
  // instead of collapsing the tree.
  useEffect(
    () =>
      client.onSessionUpdate((chatId) => {
        if (sessionKey || chatId !== "webui-dev") return;
        void loadDir(null);
      }),
    [client, loadDir, sessionKey],
  );

  // Kept above toggleDir / fs listeners so click handlers never read a TDZ ref.
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;

  const toggleDir = useCallback(
    (path: string) => {
      const root = rootPathRef.current;
      const wasOpen = treeDirIsExpanded(expandedRef.current, path, root);
      if (wasOpen) {
        setExpanded((prev) => treeExpandedWithout(prev, path, root));
        return;
      }
      const known = [
        ...expandedRef.current,
        ...Object.keys(nodesRef.current).filter((key) => key !== "__root__"),
      ];
      setExpanded((prev) => treeExpandedWith(prev, path, known, root));
      // Always re-list on open. A folder expanded while empty stays labelled
      // "empty" after the agent writes unless we fetch again.
      void loadDir(path);
    },
    [loadDir],
  );

  // If a folder is marked open but has no listing yet (missed fetch, race),
  // load it. Without this the chevron stays open and the row looks dead.
  useEffect(() => {
    const root = rootPathRef.current;
    for (const path of expanded) {
      if (!lookupTreeNode(nodes, path, root)) {
        void loadDir(path);
      }
    }
  }, [expanded, nodes, loadDir]);

  // External filesystem changes (another editor, git checkout, npm install)
  // arrive as coalesced fs_changed events from the gateway watcher. Reload
  // the root and the folders already open, in place, plus the Git panel -
  // debounced so a burst of writes (agent logs under .navin) does not
  // re-list every open folder on each tick.

  // Agent writes finish as `file_edit` long before the 3s fs poll. Insert the
  // file on the native explorer node first, then re-list only the folders
  // that contain it. User expand / collapse still uses a plain replace.
  useEffect(() => {
    if (!chatId) return;
    return client.onChat(chatId, (ev) => {
      if (ev.event !== "file_edit") return;
      const known = [
        ...expandedRef.current,
        ...Object.keys(nodesRef.current).filter((key) => key !== "__root__"),
      ];
      const reload = new Set<string | null>();
      const retainNames: string[] = [];
      let nextNodes = nodesRef.current;
      let touched = false;
      let anyDelete = false;
      for (const edit of ev.edits ?? []) {
        const deleted = edit.operation === "delete";
        if (!deleted && !fileEditIsOnDisk(edit)) continue;
        if (deleted && edit.status !== "done" && edit.phase !== "end") continue;
        const filePath = treeFileAbsolutePath(edit, rootPathRef.current);
        const displayPath = (edit.path ?? "").trim();
        touched = true;
        if (deleted) anyDelete = true;
        else retainNames.push(baseName(displayPath || filePath));
        const applied = applyOptimisticTreeFile(nextNodes, {
          rootPath: rootPathRef.current,
          fileAbs: filePath,
          displayPath,
          deleted,
        });
        nextNodes = applied.nodes;
        for (const dir of treeDirsToReload(
          filePath,
          rootPathRef.current,
          known,
          displayPath,
        )) {
          reload.add(dir);
        }
      }
      if (!touched) return;
      nodesRef.current = nextNodes;
      setNodes(nextNodes);
      for (const dir of reload) {
        void loadDir(dir, {
          keepIfEmpty: !anyDelete,
          retainNames: anyDelete ? undefined : retainNames,
        });
      }
      refreshGitStatus();
    });
  }, [chatId, client, loadDir, refreshGitStatus]);

  useEffect(() => {
    if (!chatId) return;
    let timer: number | null = null;
    const unsubscribe = client.onChat(chatId, (ev) => {
      if (ev.event !== "fs_changed") return;
      if (timer != null) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = null;
        void loadDir(null, { keepIfEmpty: true });
        for (const path of expandedRef.current) {
          if (lookupTreeNode(nodesRef.current, path, rootPathRef.current)) {
            void loadDir(path, { keepIfEmpty: true });
          }
        }
        refreshGitStatus();
      }, 1600);
    });
    return () => {
      if (timer != null) window.clearTimeout(timer);
      unsubscribe();
    };
  }, [chatId, client, loadDir, refreshGitStatus]);

  const previewsRef = useRef(previews);
  previewsRef.current = previews;
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;

  const previewErrorMessage = useCallback(
    (err: unknown, path: string) => {
      if (err instanceof ApiError && err.status === 404) {
        if (/API route not found/i.test(err.message)) {
          return tx(
            "dev.filePreviewRouteMissing",
            "File preview needs the latest gateway. Restart navin gateway and try again.",
          );
        }
        // Say what was looked for and where: "not on disk" alone reads as a
        // lost file when the chip simply named a file outside this project.
        const root = (rootPathRef.current ?? "").trim();
        if (root) {
          return t("dev.fileMissingOnDiskAt", {
            defaultValue:
              "{{name}} was not found under {{root}} or the chat workspace. It may have been moved or deleted, or written elsewhere: open it from its file edit row in the chat.",
            name: path,
            root,
          });
        }
        return tx(
          "dev.fileMissingOnDisk",
          "This file is not on disk. It may have been moved or deleted.",
        );
      }
      if (err instanceof ApiError && err.status === 403) {
        return t("dev.fileOutsideWorkspace", {
          defaultValue:
            "{{name}} is outside the current workspace. Switch the chat to full access or open the project that contains it.",
          name: path,
        });
      }
      return err instanceof Error ? err.message : String(err);
    },
    [t, tx],
  );

  const loadPreview = useCallback(
    async (path: string) => {
      const gen = (previewGenRef.current[path] ?? 0) + 1;
      previewGenRef.current[path] = gen;
      setLoadingPaths((prev) => ({ ...prev, [path]: true }));
      setPreviewErrors((prev) => {
        if (!(path in prev)) return prev;
        const next = { ...prev };
        delete next[path];
        return next;
      });
      try {
        const payload = await fetchDevFilePreview(token, treeKey, path, "", currentRoot);
        if (previewGenRef.current[path] !== gen) return;
        setPreviews((prev) => ({ ...prev, [path]: payload }));
        setTabs((prev) =>
          prev.map((tab) =>
            tab.path === path ? { ...tab, displayPath: payload.display_path } : tab,
          ),
        );
        refreshDiagnostics(path);
      } catch (err) {
        if (previewGenRef.current[path] !== gen) return;
        setPreviewErrors((prev) => ({ ...prev, [path]: previewErrorMessage(err, path) }));
      } finally {
        if (previewGenRef.current[path] === gen) {
          setLoadingPaths((prev) => {
            if (!prev[path]) return prev;
            const next = { ...prev };
            delete next[path];
            return next;
          });
        }
      }
    },
    [currentRoot, previewErrorMessage, refreshDiagnostics, token, treeKey],
  );

  const openFile = useCallback(
    async (entry: FileTreeEntry) => {
      onRevealWorkbench?.();
      setMode("code");
      // With the split view open, files land in the focused pane like Cursor.
      if (splitTabRef.current != null && focusedPaneRef.current === "right") {
        setSplitTab(entry.path);
      } else {
      setActiveTab(entry.path);
      }
      setSaveError(null);
      setTabs((prev) =>
        prev.some((tab) => tab.path === entry.path)
          ? prev
          : [...prev, { path: entry.path, displayPath: entry.path, name: entry.name }],
      );
      if (previewsRef.current[entry.path]) return;
      await loadPreview(entry.path);
    },
    [loadPreview, onRevealWorkbench],
  );

  /**
   * Pending "new file"/"new folder" entry: which folder it lands in, and what
   * kind. Null when the explorer is not asking for a name.
   */
  const [creating, setCreating] = useState<{
    parent: string | null;
    kind: "file" | "directory";
  } | null>(null);
  const [createName, setCreateName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [createBusy, setCreateBusy] = useState(false);

  const startCreating = useCallback((parent: string | null, kind: "file" | "directory") => {
    setCreating({ parent, kind });
    setCreateName("");
    setCreateError(null);
  }, []);

  const cancelCreating = useCallback(() => {
    setCreating(null);
    setCreateName("");
    setCreateError(null);
  }, []);

  const openFileByPath = useCallback(
    (path: string) => {
      const cleaned = path.trim();
      if (!cleaned) return;
      const name = baseName(cleaned);
      void openFile({ path: cleaned, name } as FileTreeEntry);
    },
    [openFile],
  );

  const submitCreate = useCallback(async () => {
    if (!creating) return;
    const name = createName.trim().replace(/^\/+/, "");
    if (!name) {
      cancelCreating();
      return;
    }
    const base = (creating.parent ?? rootPath ?? "").replace(/\/+$/, "");
    const target = base ? `${base}/${name}` : name;
    setCreateBusy(true);
    setCreateError(null);
    try {
      const payload = await createWorkspaceEntry(token, treeKey, target, creating.kind);
      setCreating(null);
      setCreateName("");
      // The new entry may be several levels down when the name carried a path,
      // so reload the folder it was asked for and open it to show the result.
      await loadDir(creating.parent);
      if (creating.parent) {
        setExpanded((prev) => new Set(prev).add(creating.parent!));
      }
      if (payload.kind === "file") {
        openFileByPath(payload.path);
      }
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreateBusy(false);
    }
  }, [cancelCreating, createName, creating, loadDir, openFileByPath, rootPath, token, treeKey]);

  const projectAbsolutePath = useCallback(
    (relativePath: string) => {
      const base = (projectPath ?? rootPath ?? "").replace(/\/+$/, "");
      return base ? `${base}/${relativePath}` : relativePath;
    },
    [projectPath, rootPath],
  );

  const projectRelativePath = useCallback(
    (absolutePath: string) => {
      const base = (projectPath ?? rootPath ?? "").replace(/\/+$/, "");
      if (base && absolutePath.startsWith(`${base}/`)) {
        return absolutePath.slice(base.length + 1);
      }
      return absolutePath;
    },
    [projectPath, rootPath],
  );

  // Design mode: the previewed app names its source as an absolute path
  // (React 18) or as the dev-server module URL (React 19, Vue). Only the
  // latter needs the index, matched on the path suffix.
  const resolvePickedSourceFile = useCallback(
    async (file: string): Promise<ProjectFileMatch | null> => {
      const lookup = sourceLookup(file, projectPath ?? rootPath);
      if (lookup.relative) return matchSourceFile(lookup, []);
      if (!lookup.name) return null;
      try {
        const result = await fetchProjectFiles(token, treeKey, lookup.name, { limit: 40 });
        return matchSourceFile(lookup, result.items);
      } catch {
        return null;
      }
    },
    [projectPath, rootPath, token, treeKey],
  );

  // Mod-L in the editor: hand the selection to the chat composer as a line
  // reference plus the snippet itself (Cursor's "Add selection to chat").
  // Above a screenful the snippet is dropped: the reference alone tells the
  // agent exactly what to read, without flooding the composer.
  const seedSelectionToChat = useCallback(
    (absPath: string, payload: { text: string; fromLine: number; toLine: number }) => {
      if (!onSeedChat) return;
      const rel = projectRelativePath(absPath);
      const range =
        payload.fromLine === payload.toLine
          ? `L${payload.fromLine}`
          : `L${payload.fromLine}-${payload.toLine}`;
      const header = `@${rel} (${range})`;
      const snippet = payload.text.replace(/\s+$/, "");
      const fence = snippet.includes("```") ? "````" : "```";
      const body =
        snippet && snippet.length <= 2000
          ? `\n\n${fence}\n${snippet}\n${fence}\n\n`
          : "\n\n";
      onSeedChat(`${header}${body}`);
    },
    [onSeedChat, projectRelativePath],
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

  const showLocations = useCallback(
    (title: string, symbol: string | undefined, items: DevLocationItem[]) => {
      if (!items.length) return;
      if (items.length === 1) {
        const only = items[0];
        openSearchMatch(only.path, Math.max(1, only.line || 1));
        setMode("code");
        return;
      }
      setLocationPicker({ title, symbol, items });
      setMode("code");
    },
    [openSearchMatch],
  );

  const gotoDefinition = useCallback(
    (target: { symbol: string; line: number; col: number }) => {
      const path = focusedFileRef.current;
      if (!path) return;
      void fetchLspDefinition(token, treeKey, {
        path,
        line: target.line,
        col: target.col,
        limit: 40,
        content: draftsRef.current[path],
      })
        .then((payload) => {
          const items = (payload.items ?? []).filter((item) => item.path);
          if (items.length) {
            showLocations(
              tx("dev.locations.definitions", "Definitions"),
              target.symbol,
              items,
            );
            return;
          }
          return fetchGotoDefinition(token, treeKey, target.symbol, {
            path,
            limit: 40,
          }).then((fallback) => {
            const hits = (fallback.items ?? []).filter((item) => item.path);
            showLocations(
              tx("dev.locations.definitions", "Definitions"),
              target.symbol,
              hits,
            );
          });
        })
        .catch(() => {
          /* silent: no definition is a normal outcome */
        });
    },
    [showLocations, token, treeKey, tx],
  );

  const findReferences = useCallback(
    (target: { symbol: string; line: number; col: number }) => {
      const path = focusedFileRef.current;
      if (!path) return;
      void fetchLspReferences(token, treeKey, {
        path,
        line: target.line,
        col: target.col,
        limit: 80,
        content: draftsRef.current[path],
      })
        .then((payload) => {
          const items = (payload.items ?? []).filter((item) => item.path);
          showLocations(
            tx("dev.locations.references", "References"),
            payload.symbol || target.symbol,
            items,
          );
        })
        .catch(() => {
          /* silent */
        });
    },
    [showLocations, token, treeKey, tx],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();
      if (key === "p" && event.shiftKey) {
        event.preventDefault();
        setCommandPalette(true);
        return;
      }
      if (key === "f" && event.shiftKey) {
        event.preventDefault();
        setExplorerOpen(true);
        setPanelTab("search");
        return;
      }
      if (key === "p") {
        event.preventDefault();
        setQuickOpen(true);
        return;
      }
      if (key === "t" && !event.shiftKey) {
        event.preventDefault();
        setSymbolPicker(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const hoverInfo = useCallback(
    async (
      target: { line: number; col: number },
      signal: AbortSignal,
    ): Promise<string> => {
      const path = focusedFileRef.current;
      if (!path) return "";
      try {
        const payload = await fetchLspHover(
          token,
          treeKey,
          {
            path,
            line: target.line,
            col: target.col,
            content: draftsRef.current[path],
          },
          signal,
        );
        return payload.contents || "";
      } catch {
        return "";
      }
    },
    [token, treeKey],
  );

  const lspCompletions = useCallback(
    async (
      target: { line: number; col: number; trigger?: string },
      signal: AbortSignal,
    ) => {
      const path = focusedFileRef.current;
      if (!path) return [];
      try {
        const payload = await fetchLspCompletion(
          token,
          treeKey,
          {
            path,
            line: target.line,
            col: target.col,
            trigger: target.trigger,
            content: draftsRef.current[path],
          },
          signal,
        );
        return payload.items ?? [];
      } catch {
        return [];
      }
    },
    [token, treeKey],
  );

  const signatureHelp = useCallback(
    async (
      target: { line: number; col: number; trigger?: string },
      signal: AbortSignal,
    ) => {
      const path = focusedFileRef.current;
      if (!path) return null;
      try {
        const payload = await fetchLspSignatureHelp(
          token,
          treeKey,
          {
            path,
            line: target.line,
            col: target.col,
            trigger: target.trigger,
            content: draftsRef.current[path],
          },
          signal,
        );
        return payload.signatures?.length ? payload : null;
      } catch {
        return null;
      }
    },
    [token, treeKey],
  );

  const codeActions = useCallback(
    async (
      target: { line: number; col: number; endLine: number; endCol: number },
      signal: AbortSignal,
    ) => {
      const path = focusedFileRef.current;
      if (!path) return [];
      try {
        const payload = await fetchLspCodeActions(
          token,
          treeKey,
          {
            path,
            line: target.line,
            col: target.col,
            endLine: target.endLine,
            endCol: target.endCol,
            content: draftsRef.current[path],
          },
          signal,
        );
        return payload.items ?? [];
      } catch {
        return [];
      }
    },
    [token, treeKey],
  );

  // The agent's open_in_editor tool: a file opens as a tab (optionally
  // scrolled to a line), a folder expands in the explorer - the same code
  // paths a user click goes through.
  useEffect(
    () =>
      client.onEditorOpenRequest((request) => {
        if (request.kind === "folder") {
          setExpanded((prev) => new Set(prev).add(request.path));
          void loadDir(request.path);
          return;
        }
        openFileByPath(request.path);
        if (request.line) {
          const line = request.line;
          setReveal((prev) => ({
            path: request.path,
            line,
            nonce: (prev?.nonce ?? 0) + 1,
          }));
        }
      }),
    [client, loadDir, openFileByPath],
  );

  /** The other side of a two-file comparison; null means diff against HEAD. */
  const [compareAgainst, setCompareAgainst] = useState<string | null>(null);

  const showDiff = useCallback((relativePath: string, against: string | null = null) => {
    setDiffFile(relativePath);
    setCompareAgainst(against);
    setMode("diff");
  }, []);

  // Chat → editor bridge: file chips clicked in the chat open here as tabs.
  // Agent previews (open_file_preview, HTML reports) arrive with
  // mode="preview" and force rendered mode back on even after a manual
  // "show source" toggle.
  //
  // Each request is consumed once, by nonce. The prop is latched, so without
  // this the effect reopens the file on every re-run - and it re-runs whenever
  // openFileByPath changes identity, which switching chat does through
  // treeKey. A file the user closed would come straight back.
  const handledOpenFileNonceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!openFileRequest) return;
    if (handledOpenFileNonceRef.current === openFileRequest.nonce) return;
    handledOpenFileNonceRef.current = openFileRequest.nonce;
    const path = openFileRequest.path.trim();
    if (!path) return;
    onRevealWorkbench?.();
    openFileByPath(path);
    onOpenFileRequestHandled?.();
    if (openFileRequest.mode === "diff") {
      showDiff(projectRelativePath(path));
      return;
    }
    if (openFileRequest.mode === "preview") {
      if (/\.(html?|xhtml)$/i.test(path)) {
        setHtmlPreviewPaths((prev) => ({ ...prev, [path]: true }));
      } else if (/\.(md|mdx|markdown)$/i.test(path)) {
        setMdPreviewPaths((prev) => ({ ...prev, [path]: true }));
      } else if (/\.(csv|tsv)$/i.test(path)) {
        setCsvPreviewPaths((prev) => ({ ...prev, [path]: true }));
      }
    }
  }, [
    onOpenFileRequestHandled,
    onRevealWorkbench,
    openFileByPath,
    openFileRequest,
    projectRelativePath,
    showDiff,
  ]);

  const refreshFile = useCallback(async (path: string) => {
    if (!path) return;
      setDrafts((prev) => {
      if (!(path in prev)) return prev;
        const next = { ...prev };
      delete next[path];
        return next;
      });
    await loadPreview(path);
  }, [loadPreview]);

  // Git rewrote tracked files on disk (discard, stash, pull, sync, undo): drop
  // any stale editor draft for the affected open files and reload from disk so
  // the change shows live, without closing and reopening the tab. When no path
  // is given (whole-tree ops), every open file is refreshed.
  const refreshWorkingTreeFiles = useCallback(
    (relPaths?: string[]) => {
      const open = [activeTab, splitTab].filter(
        (candidate): candidate is string => Boolean(candidate),
      );
      if (open.length === 0) return;
      const targets =
        relPaths && relPaths.length
          ? relPaths.map((rel) => projectAbsolutePath(rel))
          : open;
      for (const abs of targets) {
        if (open.includes(abs)) void refreshFile(abs);
      }
    },
    [activeTab, splitTab, projectAbsolutePath, refreshFile],
  );

  // Visible tabs must keep their own fetch. A global "latest open wins"
  // counter used to drop every other file, which left JSON / source tabs
  // spinning while the last HTML or Markdown preview still rendered.
  useEffect(() => {
    const visible = [activeTab, splitTab].filter((path): path is string => Boolean(path));
    for (const path of visible) {
      if (previews[path] || loadingPaths[path] || previewErrors[path]) continue;
      void loadPreview(path);
    }
  }, [activeTab, loadPreview, loadingPaths, previewErrors, previews, splitTab]);

  const renameSymbol = useCallback(
    async (target: {
      symbol: string;
      line: number;
      col: number;
      newName: string;
      apply?: boolean;
    }) => {
      const path = focusedFileRef.current;
      if (!path) {
        throw new Error("no file open");
      }
      const apply = target.apply === true;
      const payload = await fetchLspRename(token, treeKey, {
        path,
        line: target.line,
        col: target.col,
        newName: target.newName,
        apply,
        content: draftsRef.current[path],
      });
      if (!apply) {
        return {
          total: payload.total,
          files: payload.files ?? [],
          message: payload.message,
          applied: false,
        };
      }
      const written = payload.written?.length ? payload.written : payload.files;
      await Promise.all(
        written.map((rel) => refreshFile(projectAbsolutePath(rel)).catch(() => undefined)),
      );
      return {
        total: payload.total,
        files: written,
        message: payload.message,
        applied: true,
      };
    },
    [projectAbsolutePath, refreshFile, token, treeKey],
  );

  const closeTab = useCallback((path: string) => {
    // A file closed everywhere also leaves the split pane.
    setSplitTab((current) => (current === path ? null : current));
    // Retire any load still in flight: landing after the close it would refill
    // previews[path] for a tab that no longer exists, and reopening the file
    // would then show that stale content without ever fetching again.
    bumpPreviewGen(path);
    setTabs((prev) => {
      const index = prev.findIndex((tab) => tab.path === path);
      const next = prev.filter((tab) => tab.path !== path);
      setActiveTab((current) => {
        if (current !== path) return current;
        if (next.length === 0) return null;
        const neighbor = next[Math.min(Math.max(0, index - 1), next.length - 1)];
        return neighbor?.path ?? null;
      });
      return next;
    });
    setPreviews((prev) => {
      const next = { ...prev };
      delete next[path];
      return next;
    });
    // Every per-path view toggle goes too, so reopening the file starts from
    // the default (rendered preview) instead of whatever it was left on.
    setMdPreviewPaths((prev) => dropPathKey(prev, path));
    setHtmlPreviewPaths((prev) => dropPathKey(prev, path));
    setCsvPreviewPaths((prev) => dropPathKey(prev, path));
    setDrafts((prev) => dropPathKey(prev, path));
    setPreviewErrors((prev) => dropPathKey(prev, path));
    setLoadingPaths((prev) => dropPathKey(prev, path));
  }, [bumpPreviewGen]);

  // -- explorer entry management (rename, delete, context menu) -------------

  const contextMenu = useContextMenu();
  const [renaming, setRenaming] = useState<{ path: string; name: string } | null>(null);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<FileTreeEntry | null>(null);
  const [entryError, setEntryError] = useState<string | null>(null);
  // Handoff note draft; null = dialog closed. In-app dialog instead of
  // window.prompt, which does not exist in the packaged desktop builds.
  const [handoffDraft, setHandoffDraft] = useState<string | null>(null);

  const parentDirOf = useCallback(
    (path: string): string | null => {
      const trimmed = path.replace(/[\\/]+$/, "");
      const cut = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
      if (cut <= 0) return null;
      const parent = trimmed.slice(0, cut);
      // The root is addressed as null everywhere else in the tree, so a parent
      // that *is* the root has to come back as null or its reload is a no-op.
      return parent === (rootPath ?? "").replace(/[\\/]+$/, "") ? null : parent;
    },
    [rootPath],
  );

  /** Project-relative, forward-slashed - what the agent and globs both want. */
  const relativeOf = useCallback(
    (path: string): string => {
      const base = (rootPath ?? "").replace(/[\\/]+$/, "");
      const relative = base && path.startsWith(base) ? path.slice(base.length) : path;
      return relative.replace(/^[\\/]+/, "").replace(/\\/g, "/");
    },
    [rootPath],
  );

  const submitRename = useCallback(async () => {
    if (!renaming) return;
    const name = renaming.name.trim();
    const previous = renaming.path;
    if (!name) {
      setRenaming(null);
      return;
    }
    try {
      const payload = await renameWorkspaceEntry(token, treeKey, previous, name);
      setRenaming(null);
      setRenameError(null);
      if (!payload.renamed) return;
      // An open tab still points at the old path, and would 404 on its next
      // read; move it across rather than leaving a tab that cannot reload.
      setTabs((prev) =>
        prev.map((tab) =>
          tab.path === previous
            ? {
                path: payload.path,
                displayPath: payload.display_path,
                name: baseName(payload.display_path),
              }
            : tab,
        ),
      );
      setPreviews((prev) => {
        const next = { ...prev };
        const moved = next[previous];
        delete next[previous];
        if (moved) next[payload.path] = { ...moved, path: payload.path };
        return next;
      });
      setDrafts((prev) => {
        const next = { ...prev };
        const moved = next[previous];
        delete next[previous];
        if (moved !== undefined) next[payload.path] = moved;
        return next;
      });
      // The rest of the per-path state has to follow the file as well: left on
      // the old key, a view toggle is lost on rename and a past preview error
      // stays in memory for a path that no longer exists.
      const moved = payload.path;
      setMdPreviewPaths((prev) => movePathKey(prev, previous, moved));
      setHtmlPreviewPaths((prev) => movePathKey(prev, previous, moved));
      setCsvPreviewPaths((prev) => movePathKey(prev, previous, moved));
      setPreviewErrors((prev) => movePathKey(prev, previous, moved));
      setLoadingPaths((prev) => movePathKey(prev, previous, moved));
      bumpPreviewGen(payload.path);
      delete previewGenRef.current[previous];
      setSplitTab((current) => (current === previous ? payload.path : current));
      setActiveTab((current) => (current === previous ? payload.path : current));
      await loadDir(parentDirOf(previous));
      const destination = parentDirOf(payload.path);
      if (destination !== parentDirOf(previous)) await loadDir(destination);
    } catch (err) {
      setRenameError(err instanceof Error ? err.message : String(err));
    }
  }, [loadDir, parentDirOf, renaming, token, treeKey]);

  const confirmDelete = useCallback(async () => {
    const entry = pendingDelete;
    if (!entry) return;
    setPendingDelete(null);
    try {
      await deleteWorkspaceEntry(token, treeKey, entry.path);
      setEntryError(null);
      if (entry.type === "file") {
        closeTab(entry.path);
      } else {
        // Everything under a deleted folder is gone too, so its tabs have to go
        // with it - a stale tab here fails only when the user next clicks it.
        // Read from the ref rather than inside a setTabs updater: an updater has
        // to stay pure, and both separators count so Windows paths match too.
        const prefixes = [`${entry.path}/`, `${entry.path}\\`];
        for (const tab of tabsRef.current) {
          if (prefixes.some((prefix) => tab.path.startsWith(prefix))) {
            closeTab(tab.path);
          }
        }
        setExpanded((prev) => {
          const next = new Set(prev);
          next.delete(entry.path);
          return next;
        });
      }
      await loadDir(parentDirOf(entry.path));
    } catch (err) {
      setEntryError(err instanceof Error ? err.message : String(err));
    }
  }, [closeTab, loadDir, parentDirOf, pendingDelete, token, treeKey]);

  const [searchSeed, setSearchSeed] = useState<{ include: string; nonce: number } | null>(
    null,
  );

  const findInFolder = useCallback(
    (entry: FileTreeEntry) => {
      setPanelTab("search");
      setSearchSeed((prev) => ({
        include: `${relativeOf(entry.path)}/**`,
        nonce: (prev?.nonce ?? 0) + 1,
      }));
    },
    [relativeOf],
  );

  const copyToClipboard = useCallback(
    (text: string) => {
      void copyTextToClipboard(text).then((copied) => {
        if (!copied) {
          setEntryError(
            tx(
              "dev.clipboardBlocked",
              "This window would not let Navin write to the clipboard.",
            ),
          );
        }
      });
    },
    [tx],
  );

  // Explorer clipboard and compare selection: both are a single pending choice
  // waiting for a second click somewhere else in the tree.
  const [clipboard, setClipboard] = useState<
    { path: string; name: string; cut: boolean } | null
  >(null);
  const [compareLeft, setCompareLeft] = useState<{ path: string; name: string } | null>(null);

  const pasteInto = useCallback(
    async (parent: string | null) => {
      if (!clipboard) return;
      try {
        const payload = await pasteWorkspaceEntry(
          token,
          treeKey,
          clipboard.path,
          parent,
          clipboard.cut,
        );
        setEntryError(null);
        // A cut is consumed; a copy stays on the clipboard so it can be pasted
        // into several folders, which is what editors do.
        if (clipboard.cut) {
          setClipboard(null);
          if (payload.previous_path) closeTab(payload.previous_path);
          await loadDir(parentDirOf(clipboard.path));
        }
        await loadDir(parent);
        if (parent) setExpanded((prev) => new Set(prev).add(parent));
      } catch (err) {
        setEntryError(err instanceof Error ? err.message : String(err));
      }
    },
    [clipboard, closeTab, loadDir, parentDirOf, token, treeKey],
  );

  const downloadFile = useCallback(
    async (path: string, name?: string) => {
      try {
        await downloadWorkspaceFile(
          token,
          treeKey,
          path,
          name || path.split(/[/\\]/).pop() || "download",
          "",
          currentRoot,
        );
        setEntryError(null);
      } catch (err) {
        setEntryError(err instanceof Error ? err.message : String(err));
      }
    },
    [currentRoot, token, treeKey],
  );

  const downloadEntry = useCallback(
    async (entry: FileTreeEntry) => {
      await downloadFile(entry.path, entry.name);
    },
    [downloadFile],
  );

  const addToChat = useCallback(
    (entry: FileTreeEntry) => {
      const path = relativeOf(entry.path);
      // Sent as a mention rather than as prose so the turn carries the file as
      // an attachment, the same as picking it from the composer's palette.
      onSeedChat?.(`@${path} `, [
        { path, name: entry.name, kind: entry.type === "dir" ? "directory" : "file" },
      ]);
    },
    [onSeedChat, relativeOf],
  );

  const handleClipboardKey = useCallback(
    (entry: FileTreeEntry, key: "x" | "c" | "v") => {
      if (key === "v") {
        void pasteInto(entry.type === "dir" ? entry.path : parentDirOf(entry.path));
        return;
      }
      setClipboard({ path: entry.path, name: entry.name, cut: key === "x" });
    },
    [parentDirOf, pasteInto],
  );

  const duplicateEntry = useCallback(
    (entry: FileTreeEntry) => {
      // Pasting a copy beside itself is exactly what the backend suffixes, so
      // duplicate is the clipboard path with a temporary clipboard.
      const parent = parentDirOf(entry.path);
      return pasteWorkspaceEntry(token, treeKey, entry.path, parent, false)
        .then(() => {
          setEntryError(null);
          return loadDir(parent);
        })
        .catch((err) => {
          setEntryError(err instanceof Error ? err.message : String(err));
        });
    },
    [loadDir, parentDirOf, token, treeKey],
  );

  const compareWithSelected = useCallback(
    (entry: FileTreeEntry) => {
      if (!compareLeft) return;
      showDiff(relativeOf(entry.path), relativeOf(compareLeft.path));
    },
    [compareLeft, relativeOf, showDiff],
  );

  const runTestsFor = useCallback(
    (relative: string, kind: "run" | "debug" | "coverage") => {
      // Untranslated: this goes into an English instruction for the agent, not
      // onto the screen.
      const scope = relative || "the whole project";
      if (kind === "debug") {
        // Prefer the real DAP session when a concrete pytest node/path is known.
        setDebugOpen(true);
        setProblemsOpen(false);
        if (relative) {
          void (async () => {
            try {
              const lines = getBreakpointLines(relative);
              if (lines.length) {
                await debugOp(token, treeKey, "setBreakpoints", {
                  path: relative,
                  lines,
                });
              }
              const payload = await debugOp(token, treeKey, "start", {
                pytest: relative,
              });
              setDebugState(payload);
            } catch {
              onRunAction?.(
                `Run the tests for ${scope} under the debugger and fix failures from the stack trace.`,
              );
            }
          })();
          return;
        }
      }
      const instruction =
        kind === "debug"
          ? `Run the tests for ${scope} and debug every failure: for each one, explain the root cause `
            + `from the stack trace before proposing a fix.`
          : kind === "coverage"
            ? `Run the tests for ${scope} with coverage measurement. Report the coverage figure and `
              + `list the uncovered branches worth testing, most important first.`
            : `Run the test suite for ${scope}. Report failures with their root causes.`;
      onRunAction?.(instruction);
    },
    [onRunAction, token, treeKey],
  );

  const entryMenuItems = useCallback(
    (entry: FileTreeEntry | null): ContextMenuEntry[] => {
      const isDir = entry === null || entry.type === "dir";
      // Where a new or pasted entry lands: inside a folder, or beside a file.
      const parent = entry === null ? null : isDir ? entry.path : parentDirOf(entry.path);
      const relative = entry ? relativeOf(entry.path) : "";
      const items: ContextMenuEntry[] = [
        {
          id: "new-file",
          label: tx("dev.newFileMenu", "New File…"),
          icon: <FilePlus className="h-3.5 w-3.5" />,
          onSelect: () => startCreating(parent, "file"),
        },
        {
          id: "new-folder",
          label: tx("dev.newFolderMenu", "New Folder…"),
          icon: <FolderPlus className="h-3.5 w-3.5" />,
          onSelect: () => startCreating(parent, "directory"),
        },
      ];
      if (entry === null) {
        // The project folder itself: everything a folder offers except the four
        // verbs the backend refuses on it - cut, copy, duplicate, rename and
        // delete would each come back as "the project folder cannot be …".
        items.push({
          id: "terminal-here",
          label: tx("dev.openInTerminal", "Open in Integrated Terminal"),
          icon: <TerminalSquare className="h-3.5 w-3.5" />,
          separatorBefore: true,
          onSelect: () => spawnTerminal(undefined, currentRoot ?? undefined),
        });
        if (onSeedChat) {
          items.push({
            id: "add-to-chat",
            label: tx("dev.addProjectToChat", "Add Project to Chat"),
            icon: <MessageSquarePlus className="h-3.5 w-3.5" />,
            onSelect: () =>
              onSeedChat("@. ", [
                {
                  path: ".",
                  name: (currentRoot ?? "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || ".",
                  kind: "directory",
                },
              ]),
          });
        }
        items.push(
          {
            id: "find-in-project",
            label: tx("dev.findInProject", "Find in Project…"),
            icon: <Search className="h-3.5 w-3.5" />,
            onSelect: () => {
              setPanelTab("search");
              setSearchSeed((prev) => ({ include: "", nonce: (prev?.nonce ?? 0) + 1 }));
            },
          },
          {
            id: "paste",
            label: tx("dev.paste", "Paste"),
            icon: <ClipboardPaste className="h-3.5 w-3.5" />,
            shortcut: "Ctrl+V",
            separatorBefore: true,
            disabled: clipboard === null,
            onSelect: () => void pasteInto(null),
          },
        );
        if (onRunAction) {
          items.push(
            {
              id: "run-tests",
              label: tx("dev.runTests", "Run Tests"),
              icon: <FlaskConical className="h-3.5 w-3.5" />,
              separatorBefore: true,
              onSelect: () => runTestsFor("", "run"),
            },
            {
              id: "debug-tests",
              label: tx("dev.debugTests", "Debug Tests"),
              icon: <Bug className="h-3.5 w-3.5" />,
              onSelect: () => runTestsFor("", "debug"),
            },
            {
              id: "coverage-tests",
              label: tx("dev.testsWithCoverage", "Run Tests with Coverage"),
              icon: <ShieldCheck className="h-3.5 w-3.5" />,
              onSelect: () => runTestsFor("", "coverage"),
            },
          );
        }
        if (onSeedChat) {
          items.push({
            id: "run-mobile",
            label: tx("dev.runMobile", "Run Mobile"),
            icon: <Smartphone className="h-3.5 w-3.5" />,
            separatorBefore: !onRunAction,
            onSelect: () =>
              onSeedChat(
                "/mobile android\n\nDetect the mobile stack, doctor the toolchain, then start Android.",
              ),
          });
        }
        items.push(
          {
            id: "copy-path",
            label: tx("dev.copyPath", "Copy Path"),
            icon: <Copy className="h-3.5 w-3.5" />,
            separatorBefore: true,
          onSelect: () => copyToClipboard(currentRoot ?? ""),
          },
          {
            id: "refresh",
            label: tx("dev.refreshTree", "Refresh files"),
            icon: <RefreshCw className="h-3.5 w-3.5" />,
            separatorBefore: true,
            onSelect: () => void loadDir(null),
          },
        );
        return items;
      }
      if (entry.type === "file") {
        items.unshift({
          id: "open",
          label: tx("dev.openFile", "Open"),
          icon: <File className="h-3.5 w-3.5" />,
          onSelect: () => void openFile(entry),
        });
      }
      items.push({
        id: "terminal-here",
        label: tx("dev.openInTerminal", "Open in Integrated Terminal"),
        icon: <TerminalSquare className="h-3.5 w-3.5" />,
        separatorBefore: true,
        onSelect: () =>
          spawnTerminal(
            undefined,
            isDir ? entry.path : parentDirOf(entry.path) ?? currentRoot ?? undefined,
          ),
      });
      if (onSeedChat) {
        items.push({
          id: "add-to-chat",
          label: isDir
            ? tx("dev.addDirToChat", "Add Directory to Chat")
            : tx("dev.addFileToChat", "Add File to Chat"),
          icon: <MessageSquarePlus className="h-3.5 w-3.5" />,
          onSelect: () => addToChat(entry),
        });
      }
      if (entry.type === "dir") {
        items.push({
          id: "find-in-folder",
          label: tx("dev.findInFolder", "Find in Folder…"),
          icon: <Search className="h-3.5 w-3.5" />,
          onSelect: () => findInFolder(entry),
        });
      } else {
        items.push(
          {
            id: "compare-select",
            label: tx("dev.selectForCompare", "Select for Compare"),
            icon: <GitCompare className="h-3.5 w-3.5" />,
            separatorBefore: true,
            onSelect: () => setCompareLeft({ path: entry.path, name: entry.name }),
          },
          ...(compareLeft && compareLeft.path !== entry.path
            ? [
                {
                  id: "compare-with",
                  label: t("dev.compareWith", {
                    defaultValue: "Compare with {{name}}",
                    name: compareLeft.name,
                  }),
                  icon: <GitCompare className="h-3.5 w-3.5" />,
                  onSelect: () => compareWithSelected(entry),
                },
              ]
            : []),
        );
      }
      items.push(
        {
          id: "cut",
          label: tx("dev.cut", "Cut"),
          icon: <Scissors className="h-3.5 w-3.5" />,
          shortcut: "Ctrl+X",
          separatorBefore: true,
          onSelect: () => setClipboard({ path: entry.path, name: entry.name, cut: true }),
        },
        {
          id: "copy",
          label: tx("dev.copy", "Copy"),
          icon: <ClipboardCopy className="h-3.5 w-3.5" />,
          shortcut: "Ctrl+C",
          onSelect: () => setClipboard({ path: entry.path, name: entry.name, cut: false }),
        },
        {
          id: "paste",
          label: tx("dev.paste", "Paste"),
          icon: <ClipboardPaste className="h-3.5 w-3.5" />,
          shortcut: "Ctrl+V",
          disabled: clipboard === null,
          onSelect: () => void pasteInto(parent),
        },
        {
          id: "duplicate",
          label: tx("dev.duplicate", "Duplicate"),
          icon: <CopyPlus className="h-3.5 w-3.5" />,
          onSelect: () => void duplicateEntry(entry),
        },
      );
      if (entry.type === "file") {
        items.push({
          id: "download",
          label: tx("dev.download", "Download…"),
          icon: <Download className="h-3.5 w-3.5" />,
          separatorBefore: true,
          onSelect: () => void downloadEntry(entry),
        });
      }
      if (onRunAction) {
        items.push(
          {
            id: "run-tests",
            label: tx("dev.runTests", "Run Tests"),
            icon: <FlaskConical className="h-3.5 w-3.5" />,
            separatorBefore: true,
            onSelect: () => runTestsFor(relative, "run"),
          },
          {
            id: "debug-tests",
            label: tx("dev.debugTests", "Debug Tests"),
            icon: <Bug className="h-3.5 w-3.5" />,
            onSelect: () => runTestsFor(relative, "debug"),
          },
          {
            id: "coverage-tests",
            label: tx("dev.testsWithCoverage", "Run Tests with Coverage"),
            icon: <ShieldCheck className="h-3.5 w-3.5" />,
            onSelect: () => runTestsFor(relative, "coverage"),
          },
        );
      }
      items.push(
        {
          id: "rename",
          label: tx("dev.rename", "Rename…"),
          icon: <PenLine className="h-3.5 w-3.5" />,
          shortcut: "F2",
          separatorBefore: true,
          onSelect: () => {
            setRenameError(null);
            setRenaming({ path: entry.path, name: entry.name });
          },
        },
        {
          id: "delete",
          label: tx("dev.delete", "Delete"),
          icon: <Trash2 className="h-3.5 w-3.5" />,
          shortcut: "Del",
          danger: true,
          onSelect: () => setPendingDelete(entry),
        },
        {
          id: "copy-path",
          label: tx("dev.copyPath", "Copy Path"),
          icon: <Copy className="h-3.5 w-3.5" />,
          separatorBefore: true,
          onSelect: () => copyToClipboard(entry.path),
        },
        {
          id: "copy-relative-path",
          label: tx("dev.copyRelativePath", "Copy Relative Path"),
          icon: <Copy className="h-3.5 w-3.5" />,
          onSelect: () => copyToClipboard(relative),
        },
      );
      if (entry.type === "dir") {
        items.push({
          id: "refresh",
          label: tx("dev.refreshTree", "Refresh files"),
          icon: <RefreshCw className="h-3.5 w-3.5" />,
          separatorBefore: true,
          onSelect: () => void loadDir(entry.path),
        });
      }
      return items;
    },
    [
      addToChat,
      clipboard,
      compareLeft,
      compareWithSelected,
      copyToClipboard,
      currentRoot,
      downloadEntry,
      duplicateEntry,
      findInFolder,
      loadDir,
      onRunAction,
      onSeedChat,
      openFile,
      parentDirOf,
      pasteInto,
      relativeOf,
      runTestsFor,
      spawnTerminal,
      startCreating,
      t,
      tx,
    ],
  );

  const openEntryMenu = useCallback(
    (entry: FileTreeEntry | null, event: ReactMouseEvent) => {
      contextMenu.open(event, entryMenuItems(entry));
    },
    [contextMenu, entryMenuItems],
  );

  const applyReviewAction = useCallback(
    async (action: "accept" | "reject", path?: string | null) => {
      const affected = path
        ? [path]
        : reviewChanges.map((change) => change.path);
      try {
        await postReviewAction(token, treeKey, action, path ?? null);
        if (action === "reject") {
          // Disk content changed back: drop local drafts and reload previews.
          setDrafts((prev) => {
            const next = { ...prev };
            for (const p of affected) delete next[p];
            return next;
          });
          for (const p of affected) {
            if (!tabs.some((tab) => tab.path === p)) continue;
            const gen = bumpPreviewGen(p);
            try {
              const payload = await fetchDevFilePreview(token, treeKey, p, "", currentRoot);
              if (previewGenRef.current[p] !== gen) continue;
              setPreviews((prev) => ({ ...prev, [p]: payload }));
            } catch {
              // The agent created this file and the rejection removed it.
              closeTab(p);
            }
          }
          // Rejected creations/deletions change the tree: rescan loaded dirs.
          void loadDir(null);
          for (const nodeKey of Object.keys(nodes)) {
            if (nodeKey !== "__root__") void loadDir(nodeKey);
          }
        }
        setReviewFiles((prev) => {
          const next = { ...prev };
          for (const p of affected) delete next[p];
          return next;
        });
        setReviewChanges((prev) => {
          const next = prev.filter((change) => !affected.includes(change.path));
          if (action === "accept" && next.length === 0) {
            setPostReviewNextSteps(true);
          }
          return next;
        });
        refreshReview();
        refreshGitStatus();
      } catch (err) {
        notifyReviewError(err);
      }
    },
    [
      bumpPreviewGen,
      closeTab,
      loadDir,
      nodes,
      notifyReviewError,
      refreshGitStatus,
      refreshReview,
      reviewChanges,
      tabs,
      token,
      treeKey,
    ],
  );

  const applyHunkAction = useCallback(
    async (path: string, hunkId: string, action: "accept" | "reject") => {
      try {
        const result = await postReviewAction(token, treeKey, action, path, "", hunkId);
        if (action === "reject") {
          // Only a rejection rewrites the file, so only a rejection invalidates
          // the buffer. Accepting moves the baseline and leaves disk alone.
          setDrafts((prev) => {
            const next = { ...prev };
            delete next[path];
            return next;
          });
          const gen = bumpPreviewGen(path);
          try {
            const preview = await fetchDevFilePreview(token, treeKey, path, "", currentRoot);
            if (previewGenRef.current[path] === gen) {
            setPreviews((prev) => ({ ...prev, [path]: preview }));
            }
          } catch {
            // Undoing the last hunk of a file the agent created removes it.
            closeTab(path);
            void loadDir(null);
          }
        }
        if (result.done) {
          setReviewFiles((prev) => {
            const next = { ...prev };
            delete next[path];
            return next;
          });
          setReviewChanges((prev) => {
            const next = prev.filter((change) => change.path !== path);
            if (action === "accept" && next.length === 0) {
              setPostReviewNextSteps(true);
            }
            return next;
          });
        } else {
          const payload = await fetchReviewFile(token, treeKey, path);
          setReviewFiles((prev) => ({ ...prev, [path]: payload }));
        }
        refreshReview();
        refreshGitStatus();
      } catch (err) {
        // A 409 means the click raced the agent; the refresh below re-reads the
        // hunks so the next attempt acts on what is actually there.
        notifyReviewError(err);
        refreshReview();
      }
    },
    [closeTab, loadDir, notifyReviewError, refreshGitStatus, refreshReview, token, treeKey],
  );

  // Format on save is a per-user editor preference, like the Vim toggle.
  const [formatOnSave, setFormatOnSave] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem("navin.dev.formatOnSave") === "1";
    } catch {
      return false;
    }
  });
  const toggleFormatOnSave = useCallback(() => {
    setFormatOnSave((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem("navin.dev.formatOnSave", next ? "1" : "0");
      } catch {
        /* private mode: the toggle still works for the session */
      }
      return next;
    });
  }, []);

  // Format Document (Shift+Alt+F / palette): run the project's formatter
  // (Prettier, ruff, gofmt, rustfmt) over the buffer and put the result back
  // as a draft - the file on disk is untouched until the user saves.
  const formatDocument = useCallback(
    async (path: string) => {
      const content = drafts[path] ?? previews[path]?.content;
      if (typeof content !== "string") return;
      try {
        const payload = await formatWorkspaceFile(token, treeKey, path, content);
        if (payload.changed) recordDraftEdit(path, payload.formatted);
      } catch (err) {
        publishNotification({
          level: "warning",
          source: "session",
          title: tx("dev.formatFailed", "Could not format this file"),
          detail: err instanceof Error ? err.message : String(err),
          key: "dev:format:error",
        });
      }
    },
    [drafts, previews, recordDraftEdit, token, treeKey, tx],
  );

  const saveFile = useCallback(async (path: string): Promise<boolean> => {
    const preview = previewsRef.current[path];
    // The server tags previews with a rendering kind ("markdown", "json",
    // "csv", ...); anything that is not an image/binary/media payload is
    // plain text and stays editable.
    const editable =
      preview != null &&
      !preview.truncated &&
      !["image", "binary", "audio", "video", "pdf"].includes(preview.kind ?? "text");
    if (!editable) return true;
    let content = draftsRef.current[path];
    if (content === undefined) return true;
    setSaving(true);
    setSaveError(null);
    if (formatOnSave) {
      try {
        const formatted = await formatWorkspaceFile(token, treeKey, path, content);
        if (formatted.changed) content = formatted.formatted;
      } catch {
        // No formatter for this file type, or the formatter rejected the
        // buffer (syntax error): the save itself must still go through.
      }
    }
    // What is on disk is now what we just wrote, so a read that started before
    // the save must not come back and overwrite the buffer with the old text.
    bumpPreviewGen(path);
    try {
      await saveWorkspaceFile(token, treeKey, path, content);
      setPreviews((prev) => {
        const current = prev[path];
        return current
          ? { ...prev, [path]: { ...current, content } }
          : prev;
      });
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[path];
        return next;
      });
      refreshDiagnostics(path);
      refreshWorkspaceDiagnostics();
      refreshGitStatus();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setSaving(false);
    }
    return true;
  }, [
    bumpPreviewGen,
    formatOnSave,
    refreshDiagnostics,
    refreshGitStatus,
    refreshWorkspaceDiagnostics,
    token,
    treeKey,
  ]);

  useEffect(() => {
    registerDirtyFileFlusher(async () => {
      const failed: string[] = [];
      for (const path of Object.keys(draftsRef.current)) {
        const ok = await saveFile(path);
        if (!ok) failed.push(path);
      }
      if (failed.length > 0) {
        throw new Error(`Could not save ${failed.join(", ")} before the proof.`);
      }
    });
    return () => registerDirtyFileFlusher(null);
  }, [saveFile]);

  // Split view controls: toggle from the pane header, resize with the
  // separator between the two panes.
  const toggleSplit = useCallback(() => {
    if (splitTab != null) {
      setSplitTab(null);
      setFocusedPane("left");
    } else if (activeTab) {
      setSplitTab(activeTab);
      setFocusedPane("right");
    }
  }, [activeTab, splitTab]);

  const splitContainerRef = useRef<HTMLDivElement | null>(null);
  const startSplitDrag = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const container = splitContainerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const onMove = (ev: PointerEvent) => {
      if (rect.width <= 0) return;
      const ratio = (ev.clientX - rect.left) / rect.width;
      setSplitRatio(Math.min(0.8, Math.max(0.2, ratio)));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  const revealReviewedSource = useCallback(
    (path: string) => {
      setMdPreviewPaths((prev) => ({ ...prev, [path]: false }));
      setHtmlPreviewPaths((prev) => ({ ...prev, [path]: false }));
      setCsvPreviewPaths((prev) => ({ ...prev, [path]: false }));
      if (!reviewByPath.has(path) || reviewFiles[path] !== undefined) return;
      void fetchReviewFile(token, treeKey, path)
        .then((payload) =>
          setReviewFiles((current) =>
            current[path] !== undefined ? current : { ...current, [path]: payload },
          ),
        )
        .catch(() => undefined);
    },
    [reviewByPath, reviewFiles, token, treeKey],
  );

  const closeDiff = useCallback(() => {
    const relative = diffFile;
    setMode("code");
    setDiffFile(null);
    setCompareAgainst(null);
    if (!relative) return;
    const path =
      tabs.find((tab) => tabMatchesDiffFile(tab.path, relative, relativeOf))?.path
      ?? projectAbsolutePath(relative);
    setActiveTab(path);
    revealReviewedSource(path);
  }, [diffFile, projectAbsolutePath, relativeOf, revealReviewedSource, tabs]);

  // Open Changes: the git diff of one file, straight from the editor header.
  // Clicking again while that diff is open returns to the file (preview / edit).
  const openChangesFor = useCallback(
    (path: string) => {
      const preview = previewsRef.current[path];
      const relative = relativeOf(preview?.display_path ?? path);
      if (mode === "diff" && (diffFile === relative || diffFile === preview?.display_path)) {
        closeDiff();
        return;
      }
      showDiff(relative);
    },
    [closeDiff, diffFile, mode, relativeOf, showDiff],
  );

  const openReviewFile = useCallback(
    (change: ReviewChangeEntry) => {
      setTerminalOpen(false);
      setTerminalMaximized(false);
      setTabs((prev) => mergeReviewTabs(prev, [change]));
      setActiveTab(change.path);
      if (!previewsRef.current[change.path]) {
        void loadPreview(change.path);
      }
      showDiff(relativeOf(change.path) || change.display_path);
    },
    [loadPreview, relativeOf, showDiff],
  );

  const openReviewChanges = useCallback(() => {
    requestShowPendingReview();
    const files = reviewOpenableFiles(reviewChanges);
    if (files.length === 0) return;
    if (mode === "diff") {
      closeDiff();
      return;
    }
    // Only the first three become tabs. The rest stay in Other files so a
    // 20-file review cannot open every buffer at once.
    const initial = files.slice(0, REVIEW_VISIBLE_FILE_LIMIT);
    setTerminalOpen(false);
    setTerminalMaximized(false);
    setTabs((prev) => mergeReviewTabs(prev, initial));
    const first = initial[0];
    setActiveTab(first.path);
    if (!previewsRef.current[first.path]) {
      void loadPreview(first.path);
    }
    showDiff(relativeOf(first.path) || first.display_path);
  }, [closeDiff, loadPreview, mode, relativeOf, reviewChanges, showDiff]);

  const reviewDiffFiles = useMemo(
    () => reviewOpenableFiles(reviewChanges),
    [reviewChanges],
  );

  const workbenchCommands = useMemo((): DevCommand[] => {
    const mod =
      typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform)
        ? "⌘"
        : "Ctrl";
    return [
      {
        id: "quickOpen",
        label: tx("dev.commands.quickOpen", "Quick Open"),
        hint: `${mod}+P`,
        keywords: "files open",
        run: () => setQuickOpen(true),
      },
      {
        id: "goToSymbol",
        label: tx("dev.commands.goToSymbol", "Go to Symbol in Workspace"),
        hint: `${mod}+T`,
        keywords: "symbols outline",
        run: () => setSymbolPicker(true),
      },
      {
        id: "showOutline",
        label: tx("dev.commands.showOutline", "Show Outline"),
        keywords: "symbols document",
        run: () => {
          setExplorerOpen(true);
          setPanelTab("outline");
        },
      },
      {
        id: "toggleProblems",
        label: tx("dev.commands.toggleProblems", "Toggle Problems"),
        keywords: "errors diagnostics",
        run: () => setProblemsOpen((open) => !open),
      },
      {
        id: "toggleDebug",
        label: tx("dev.commands.toggleDebug", "Toggle Debug"),
        keywords: "dap debugger breakpoints",
        run: () => {
          setDebugOpen((open) => !open);
          setProblemsOpen(false);
        },
      },
      {
        id: "resumeProject",
        label: tx("dev.commands.resumeProject", "Resume Project"),
        keywords: "continuity handoff brief return",
        run: () => {
          void (async () => {
            try {
              const payload = await fetchResumeSeed(token, treeKey);
              onSeedChat?.(payload.seed);
            } catch {
              /* ignore - continuity API may be offline */
            }
          })();
        },
      },
      {
        id: "leaveHandoff",
        label: tx("dev.commands.leaveHandoff", "Leave Handoff Note"),
        keywords: "continuity resume decisions",
        run: () => {
          setHandoffDraft(
            "Goal: (what we were doing)\n"
              + "Done: (files changed)\n"
              + "Decisions: (durable choices + why)\n"
              + "Next: (concrete next action)\n",
          );
        },
      },
      {
        id: "debugCurrentFile",
        label: tx("dev.commands.debugCurrentFile", "Debug Current File"),
        keywords: "dap python debugpy node javascript typescript",
        run: () => {
          // The DAP backend launches both Python and Node files; the command
          // used to bail on anything but .py, a dead command for JS/TS.
          const runtime = activeTab && /\.pyw?$/i.test(activeTab)
            ? "python"
            : activeTab && /\.(js|mjs|cjs|ts|mts|cts)$/i.test(activeTab)
              ? "node"
              : null;
          if (!activeTab || !runtime) return;
          setDebugOpen(true);
          setProblemsOpen(false);
          void (async () => {
            try {
              const lines = getBreakpointLines(activeTab);
              await debugOp(token, treeKey, "setBreakpoints", {
                path: activeTab,
                lines,
              });
              const payload = await debugOp(token, treeKey, "start", {
                program: activeTab,
                runtime,
              });
              setDebugState(payload);
            } catch {
              /* panel shows the error on next poll */
            }
          })();
        },
      },
      {
        id: "toggleExplorer",
        label: tx("dev.commands.toggleExplorer", "Toggle Explorer"),
        run: () => setExplorerOpen((open) => !open),
      },
      {
        id: "focusFiles",
        label: tx("dev.commands.focusFiles", "Focus Files"),
        run: () => {
          setExplorerOpen(true);
          setPanelTab("files");
        },
      },
      {
        id: "focusSearch",
        label: tx("dev.commands.focusSearch", "Find in Project"),
        keywords: "search find",
        hint: "Ctrl+Shift+F",
        run: () => {
          setExplorerOpen(true);
          setPanelTab("search");
        },
      },
      {
        id: "focusGit",
        label: tx("dev.commands.focusGit", "Focus Source Control"),
        keywords: "git",
        run: () => setMode("git"),
      },
      {
        id: "toggleTerminal",
        label: tx("dev.commands.toggleTerminal", "Toggle Terminal"),
        run: () => {
          if (terminalOpen) {
            setTerminalOpen(false);
            setTerminalMaximized(false);
          } else {
            openTerminalPanel();
          }
        },
      },
      {
        id: "toggleSplit",
        label: tx("dev.commands.toggleSplit", "Toggle Split Editor"),
        run: () => toggleSplit(),
      },
      {
        id: "saveFile",
        label: tx("dev.commands.saveFile", "Save File"),
        hint: `${mod}+S`,
        run: () => {
          const path = focusedFileRef.current;
          if (path) void saveFile(path);
        },
      },
      {
        id: "formatDocument",
        label: tx("dev.commands.formatDocument", "Format Document"),
        hint: "Shift+Alt+F",
        keywords: "prettier ruff gofmt rustfmt beautify",
        run: () => {
          const path = focusedFileRef.current;
          if (path) void formatDocument(path);
        },
      },
      {
        id: "toggleFormatOnSave",
        label: formatOnSave
          ? tx("dev.commands.formatOnSaveOff", "Format on Save: Turn Off")
          : tx("dev.commands.formatOnSaveOn", "Format on Save: Turn On"),
        keywords: "prettier ruff format save",
        run: () => toggleFormatOnSave(),
      },
      {
        id: "inlineEdit",
        label: tx("dev.commands.inlineEdit", "Inline Edit"),
        hint: `${mod}+K`,
        keywords: "cmdk edit rewrite",
        run: () => {
          // Focus the editor; CodeMirror owns Mod-K. A synthetic keydown is
          // unreliable across hosts, so surface the shortcut in the hint.
          document
            .querySelector<HTMLElement>(".cm-content")
            ?.dispatchEvent(
              new KeyboardEvent("keydown", {
                key: "k",
                code: "KeyK",
                ctrlKey: mod === "Ctrl",
                metaKey: mod === "⌘",
                bubbles: true,
                cancelable: true,
              }),
            );
        },
      },
      {
        id: "acceptReviewFile",
        label: tx("dev.commands.acceptReviewFile", "Accept Review File"),
        keywords: "hunk accept",
        run: () => {
          const path = focusedFileRef.current;
          if (path && reviewByPath.has(path)) void applyReviewAction("accept", path);
        },
      },
      {
        id: "rejectReviewFile",
        label: tx("dev.commands.rejectReviewFile", "Reject Review File"),
        keywords: "hunk reject",
        run: () => {
          const path = focusedFileRef.current;
          if (path && reviewByPath.has(path)) void applyReviewAction("reject", path);
        },
      },
      {
        id: "acceptReviewAll",
        label: tx("dev.commands.acceptReviewAll", "Accept All Reviews"),
        run: () => {
          if (reviewChanges.length) void applyReviewAction("accept");
        },
      },
      {
        id: "restoreCheckpoint",
        label: tx(
          "dev.commands.restoreCheckpoint",
          "Restore Checkpoint (Undo Agent Edits)",
        ),
        keywords: "reject undo rollback checkpoint",
        run: () => {
          if (reviewChanges.length) void applyReviewAction("reject");
        },
      },
      {
        id: "modeCode",
        label: tx("dev.commands.modeCode", "Open Code"),
        run: () => setMode("code"),
      },
      {
        id: "modeExtensions",
        label: tx("dev.commands.modeExtensions", "Open Extensions"),
        keywords: "lsp vsix marketplace openvsx",
        run: () => setMode("extensions"),
      },
      {
        id: "modeTemplates",
        label: tx("dev.commands.modeTemplates", "Open Templates"),
        keywords: "marketplace apps crm gallery use",
        run: () => setMode("templates"),
      },
      {
        id: "modeDiff",
        label: tx("dev.commands.modeDiff", "Open Changes Diff"),
        keywords: "git diff",
        run: () => {
          const path = focusedFileRef.current;
          if (path) openChangesFor(path);
        },
      },
      {
        id: "toggleBlame",
        label: tx("dev.commands.toggleBlame", "Toggle Git Blame"),
        keywords: "gitlens author commit",
        run: () => setBlameEnabled((open) => !open),
      },
    ];
  }, [
    applyReviewAction,
    formatDocument,
    formatOnSave,
    openChangesFor,
    openTerminalPanel,
    reviewByPath,
    reviewChanges.length,
    terminalOpen,
    toggleFormatOnSave,
    toggleSplit,
    tx,
  ]);

  const requestAgentStartPreview = useCallback(() => {
    if (!onRunAction) return;
    setBrowserStarting(true);
    setBrowserSelfError(
      tx("dev.browserNoProjectServer", "Demarrage de l'application par le chat…"),
    );
    onRunAction(
      "Autonomie totale: demarre TOUTE l'application du workspace "
        + "(frontend + backend / docker / ce qu'il faut) avec start_app ou "
        + "open_preview. Verifie HTTP, ouvre l'Apercu sur le vrai port. "
        + "Ne me demande JAMAIS de lancer npm, docker, ou quoi que ce soit "
        + "moi-meme. Je dois pouvoir tester dans Apercu comme Cursor.",
    );
  }, [onRunAction, tx]);

  // Root-tree hint: only offer "start via chat" when the project looks like
  // it has a web UI worth previewing. Libraries / CLIs / data projects stay quiet.
  const looksLikeWebApp = useMemo(() => {
    const entries = nodes["__root__"]?.entries ?? [];
    if (entries.length === 0) return false;
    const webNames = new Set([
      "package.json",
      "index.html",
      "vite.config.ts",
      "vite.config.js",
      "vite.config.mjs",
      "next.config.js",
      "next.config.mjs",
      "next.config.ts",
      "astro.config.mjs",
      "nuxt.config.ts",
      "angular.json",
      "docker-compose.yml",
      "docker-compose.yaml",
      "compose.yml",
      "compose.yaml",
      "start.sh",
    ]);
    const webDirs = new Set(["frontend", "web", "client", "ui", "apps", "app"]);
    return entries.some((entry) => {
      const name = entry.name.toLowerCase();
      if (webNames.has(name) || webNames.has(entry.name)) return true;
      if (entry.type === "dir" && webDirs.has(name)) return true;
      if (/^vite\.config\./i.test(entry.name)) return true;
      if (/^next\.config\./i.test(entry.name)) return true;
      return false;
    });
  }, [nodes]);

  // No "trust this URL" escape hatch: the self check runs for every URL,
  // including the ones the agent asks to preview. Skipping it is exactly how
  // Preview ended up showing Navin's own editor instead of the project.
  const openBrowser = useCallback(
    async (urlOverride?: string) => {
      const value = (urlOverride ?? browserUrl).trim();
      setBrowserSelfError(null);
      setBrowserStarting(false);

      const openProjectUrl = async (url: string) => {
        const normalized = normalizePreviewUrl(url);
        setBrowserUrl(normalized);
        persistPreviewUrl(normalized);
        // Local apps go through the telemetry injection proxy so the console
        // panel (and the agent) can see console errors and failed requests.
        // On any proxy failure, fall back to the direct URL.
        let src = normalized;
        const targetPort = localPreviewPortFromUrl(normalized);
        if (targetPort != null) {
          try {
            const proxy = await startPreviewProxy(token, targetPort);
            src = proxy.url;
            setPreviewTargetPort(targetPort);
          } catch {
            setPreviewTargetPort(null);
          }
        } else {
          setPreviewTargetPort(null);
        }
        setBrowserSrc(src);
        setBrowserNonce((n) => n + 1);
        setBrowserSelfError(null);
        setBrowserStarting(false);
        setMode("browser");
      };

      const rejectSelfUrl = () => {
        try {
          localStorage.removeItem(PREVIEW_URL_STORAGE_KEY);
        } catch {
          // ignore
        }
        setBrowserSrc(null);
        setBrowserUrl("");
        setBrowserStarting(false);
        setBrowserSelfError(
          tx(
            "dev.browserSelfBlocked",
            "Apercu a refuse d'ouvrir Navin lui-meme. C'est l'editeur, pas le projet. Choisis un projet utilisateur, ou demande au chat de lancer son serveur.",
          ),
        );
      };

      const tryDiscover = async (): Promise<"ok" | "self" | "none"> => {
        // Gateway probe (no CORS). Prefer the last working URL when it still answers.
        const stored = readStoredPreviewUrl();
        try {
          const result = await fetchDiscoverPreviewUrl(token, {
            ...(stored ? { url: stored } : {}),
          });
          if (result.url) {
            const isSelf =
              isNavinSelfPreviewUrlSync(result.url)
              || (await isNavinSelfPreviewUrl(result.url));
            if (isSelf) {
              rejectSelfUrl();
              return "self";
            }
            await openProjectUrl(result.url);
            return "ok";
          }
        } catch {
          // fall through
        }
        if (stored) {
          try {
            localStorage.removeItem(PREVIEW_URL_STORAGE_KEY);
          } catch {
            // ignore
          }
        }
        return "none";
      };

      const leaveEmpty = () => {
        setBrowserSrc(null);
        setBrowserUrl("");
        setBrowserStarting(false);
        setBrowserSelfError(null);
        setMode("browser");
      };

      // Preview never seeds the chat. Discovery only; empty stays empty.
      if (!value) {
        setMode("browser");
        const found = await tryDiscover();
        if (found === "ok" || found === "self") return;
        leaveEmpty();
        return;
      }

      const normalized = normalizePreviewUrl(value);
      setBrowserUrl(normalized);

      const isSelf =
        isNavinSelfPreviewUrlSync(normalized)
        || (await isNavinSelfPreviewUrl(normalized));

      if (isSelf) {
        const found = await tryDiscover();
        if (found === "ok") return;
        rejectSelfUrl();
        setMode("browser");
        return;
      }

      // Typed / trusted URL: open in the iframe. Do not CORS-fetch from the
      // browser (local apps usually block it) and do not hijack to another port.
      await openProjectUrl(normalized);
    },
    [browserUrl, token, tx],
  );

  // Entering Preview: silent discovery only. Never touch the chat.
  useEffect(() => {
    if (mode !== "browser") return;
    if (browserSrc) {
      setBrowserStarting(false);
      return;
    }
    if (previewAutoTriedRef.current) return;
    previewAutoTriedRef.current = true;
    void openBrowser(undefined);
  }, [browserSrc, mode, openBrowser]);

  const refreshPreviewFrame = useCallback(() => {
    if (!browserSrc) {
      previewAutoTriedRef.current = false;
      void openBrowser(browserUrl || undefined);
      return;
    }
    setBrowserNonce((n) => n + 1);
  }, [browserSrc, browserUrl, openBrowser]);

  const openInOsBrowser = useCallback(
    async (raw: string) => {
      const url = normalizePreviewUrl(raw.trim());
      if (!url) return;
      setBrowserSelfError(null);
      // window.open in the desktop shell creates a second WebView that
      // covers the IDE. Only a real browser tab may use it as fallback.
      const fallbackWindow = () => {
        if (!shouldUseWindowOpenFallback(isDesktopShell())) return false;
        const opened = window.open(url, "_blank", "noopener,noreferrer");
        return Boolean(opened);
      };
      try {
        const result = await openExternalUrl(token, url);
        if (result.opened || fallbackWindow()) return;
      } catch {
        if (fallbackWindow()) return;
      }
      const copied = await copyTextToClipboard(url);
      setBrowserSelfError(
        copied
          ? tx(
              "dev.browserOpenCopied",
              "Ouverture bloquee dans cette fenetre. L'URL a ete copiee - colle-la dans le navigateur.",
            )
          : tx(
              "dev.browserOpenBlockedManual",
              "Ouverture bloquee dans cette fenetre. Copie l'URL ci-dessus dans le navigateur.",
            ),
      );
    },
    [token, tx],
  );

  const openPreviewExternal = useCallback(() => {
    const target = osBrowserPreviewUrl({
      browserUrl,
      browserSrc,
      previewTargetPort,
    });
    if (!target) return;
    void openInOsBrowser(target);
  }, [browserSrc, browserUrl, openInOsBrowser, previewTargetPort]);

  // Agent open_preview: App listens globally and passes previewOpenRequest so
  // Preview still opens when the user was on Chat (workbench unmounted).
  // Consumed once by nonce, like the open-file request above: openBrowser
  // changes identity on its own, and replaying would drag the user back to
  // Preview long after they moved on.
  const handledPreviewNonceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!previewOpenRequest) return;
    if (handledPreviewNonceRef.current === previewOpenRequest.nonce) return;
    handledPreviewNonceRef.current = previewOpenRequest.nonce;
    onPreviewOpenRequestHandled?.();
    if (previewOpenRequest.kind === "mobile") {
      setMobileAutoStart(true);
      setMode("mobile");
      return;
    }
    if (previewOpenRequest.url) {
      previewAutoTriedRef.current = true;
      void openBrowser(previewOpenRequest.url);
    }
  }, [onPreviewOpenRequestHandled, openBrowser, previewOpenRequest]);

  const handledProjectHomeNonceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!projectHomeRequest) return;
    if (handledProjectHomeNonceRef.current === projectHomeRequest.nonce) return;
    handledProjectHomeNonceRef.current = projectHomeRequest.nonce;
    onProjectHomeRequestHandled?.();
    setMode("project");
  }, [onProjectHomeRequestHandled, projectHomeRequest]);

  const handledTemplatesNonceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!templatesRequest) return;
    if (handledTemplatesNonceRef.current === templatesRequest.nonce) return;
    handledTemplatesNonceRef.current = templatesRequest.nonce;
    onTemplatesRequestHandled?.();
    setMode("templates");
  }, [onTemplatesRequestHandled, templatesRequest]);

  const handledEvolveNonceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!evolveRequest) return;
    if (handledEvolveNonceRef.current === evolveRequest.nonce) return;
    handledEvolveNonceRef.current = evolveRequest.nonce;
    onEvolveRequestHandled?.();
    setMode("evolve");
  }, [evolveRequest, onEvolveRequestHandled]);

  const rootState = nodes["__root__"];
  const projectLabel = useMemo(() => {
    const raw = currentRoot ?? "";
    if (!raw) return tx("dev.workspace", "Workspace");
    // The internal folder's last segment is just "workspace", which reads like
    // any other project. Name it for what it is.
    if (onInternalWorkspace) return tx("dev.internalWorkspace", "Navin projects");
    return baseName(raw);
  }, [currentRoot, onInternalWorkspace, tx]);

  const dirtyPaths = useMemo(() => {
    const set = new Set<string>();
    for (const [path, draft] of Object.entries(drafts)) {
      if (draft !== (previews[path]?.content ?? "")) set.add(path);
    }
    return set;
  }, [drafts, previews]);

  // Git working-tree state per absolute path, to tint tree entries like VS Code.
  const gitStatusByPath = useMemo(() => {
    const map = new Map<string, string>();
    if (!gitChanges?.is_repo) return map;
    for (const file of gitChanges.files) {
      map.set(projectAbsolutePath(file.path), file.status);
    }
    return map;
  }, [gitChanges, projectAbsolutePath]);

  const gitDirs = useMemo(() => {
    const dirs = new Set<string>();
    for (const path of gitStatusByPath.keys()) {
      let idx = path.lastIndexOf("/");
      while (idx > 0) {
        dirs.add(path.slice(0, idx));
        idx = path.lastIndexOf("/", idx - 1);
      }
    }
    return dirs;
  }, [gitStatusByPath]);

  /**
   * One editor pane: header (path, Open Changes, Split, Save, Reload, Close)
   * plus the file content. Rendered once normally, twice in split view, with
   * every derived value computed from the pane's own path.
   */
  const renderEditorPane = (
    path: string,
    pane: "left" | "right",
    variant: "full" | "header" | "body" = "full",
  ) => {
    const preview = previews[path] ?? null;
    const fileLoading = Boolean(loadingPaths[path]);
    const previewError = previewErrors[path] ?? null;
    const kind = preview?.kind ?? "text";
    const draft = drafts[path] !== undefined ? drafts[path] : preview?.content ?? "";
    const dirty =
      drafts[path] !== undefined && drafts[path] !== (preview?.content ?? "");
    const editable =
      preview != null &&
      !preview.truncated &&
      !["image", "binary", "audio", "video", "pdf", "office", "spreadsheet"].includes(kind);
    const reviewEntry = reviewByPath.get(path) ?? null;
    const reviewFile = reviewFiles[path];
    const paneReviewBaseline =
      reviewEntry && reviewFile && !reviewFile.binary
        ? reviewFile.status === "created"
          ? ""
          : reviewFile.baseline
        : null;
    // Hunk line numbers are the server's, so the controls are only shown while
    // the buffer still matches what the server diffed.
    const paneReviewHunks =
      paneReviewBaseline != null &&
      reviewFile?.current != null &&
      draft === reviewFile.current
        ? reviewFile.hunks ?? null
        : null;
    const isSplit = splitTab != null;
    // Preview is the default for renderable formats; the record only holds an
    // explicit user toggle back to source.
    const previewByDefault = defaultEditorPreviewOn(reviewEntry != null);
    const showMdPreview =
      isMarkdownEditorPath(path, preview) && (mdPreviewPaths[path] ?? previewByDefault);
    const showHtmlPreview =
      isHtmlEditorPath(path, preview) && (htmlPreviewPaths[path] ?? previewByDefault);
    const csvRows = isCsvEditorPath(path, preview)
      ? parseCsv(draft, 500)
      : null;
    const showCsvPreview =
      csvRows != null && csvRows.length > 0 && (csvPreviewPaths[path] ?? previewByDefault);
    const canPreviewFullscreen =
      isHtmlEditorPath(path, preview)
      || isMarkdownEditorPath(path, preview)
      || isCsvEditorPath(path, preview)
      || kind === "image"
      || kind === "pdf"
      || kind === "office"
      || kind === "spreadsheet"
      || kind === "video";
    const paneHunkAction: HunkAction = (hunkId, action) => {
      void applyHunkAction(path, hunkId, action);
    };
  return (
      <>
        {variant !== "body" ? (
        <div
          className={cn(
            "flex items-center justify-end gap-2 border-b border-border/50 px-3 py-1.5",
            isSplit && focusedPane === pane && "bg-muted/20",
          )}
        >
          <div className="flex shrink-0 items-center gap-1.5">
            {saveError && (!isSplit || focusedPane === pane) ? (
              <span className="max-w-[16rem] truncate text-[11px] text-destructive">
                {saveError}
              </span>
            ) : null}
            {paneReviewBaseline != null ? (
              <span
                className="hidden max-w-[12rem] truncate text-[11px] text-muted-foreground sm:inline"
                title={tx(
                  "dev.review.vsPreviousHint",
                  "Inline diff against the previous version: red lines with a - were removed, green lines with a + are new.",
                )}
              >
                {tx("dev.review.vsPrevious", "vs previous")}
              </span>
            ) : null}
            <button
              type="button"
              onClick={() => openChangesFor(path)}
              className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
              title={tx("dev.openChanges", "Open changes (git diff)")}
              aria-label={tx("dev.openChanges", "Open changes (git diff)")}
              data-testid="dev-editor-change"
            >
              <GitCompare className="h-3 w-3" aria-hidden />
              <span>{tx("dev.change", "Change")}</span>
            </button>
            <button
              type="button"
              onClick={toggleSplit}
              className={cn(
                "flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] transition-colors hover:bg-muted hover:text-foreground",
                isSplit ? "text-foreground" : "text-muted-foreground",
              )}
              title={
                isSplit
                  ? tx("dev.splitClose", "Close split view")
                  : tx("dev.splitEditor", "Split editor")
              }
              aria-label={
                isSplit
                  ? tx("dev.splitClose", "Close split view")
                  : tx("dev.splitEditor", "Split editor")
              }
            >
              <Columns2 className="h-3 w-3" aria-hidden />
            </button>
            {isHtmlEditorPath(path, preview) ? (
              <button
                type="button"
                onClick={() =>
                  setHtmlPreviewPaths((prev) => ({
                    ...prev,
                    [path]: !(prev[path] ?? previewByDefault),
                  }))
                }
                className={cn(
                  "flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
                  showHtmlPreview
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                title={
                  showHtmlPreview
                    ? tx("dev.htmlPreviewEdit", "Show source")
                    : tx("dev.htmlPreview", "Preview")
                }
                aria-label={
                  showHtmlPreview
                    ? tx("dev.htmlPreviewEdit", "Show source")
                    : tx("dev.htmlPreview", "Preview")
                }
                aria-pressed={showHtmlPreview}
              >
                {showHtmlPreview ? (
                  <Code2 className="h-3 w-3" aria-hidden />
                ) : (
                  <Eye className="h-3 w-3" aria-hidden />
                )}
                {showHtmlPreview
                  ? tx("dev.htmlPreviewEdit", "Source")
                  : tx("dev.htmlPreview", "Preview")}
              </button>
            ) : null}
            {isMarkdownEditorPath(path, preview) ? (
              <button
                type="button"
                onClick={() =>
                  setMdPreviewPaths((prev) => ({
                    ...prev,
                    [path]: !(prev[path] ?? previewByDefault),
                  }))
                }
                className={cn(
                  "flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
                  showMdPreview
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                title={
                  showMdPreview
                    ? tx("dev.markdownPreviewEdit", "Edit source")
                    : tx("dev.markdownPreview", "Preview")
                }
                aria-label={
                  showMdPreview
                    ? tx("dev.markdownPreviewEdit", "Edit source")
                    : tx("dev.markdownPreview", "Preview")
                }
                aria-pressed={showMdPreview}
              >
                {showMdPreview ? (
                  <Code2 className="h-3 w-3" aria-hidden />
                ) : (
                  <Eye className="h-3 w-3" aria-hidden />
                )}
                {showMdPreview
                  ? tx("dev.markdownPreviewEdit", "Edit")
                  : tx("dev.markdownPreview", "Preview")}
              </button>
            ) : null}
            {isCsvEditorPath(path, preview) ? (
              <button
                type="button"
                onClick={() =>
                  setCsvPreviewPaths((prev) => ({
                    ...prev,
                    [path]: !(prev[path] ?? previewByDefault),
                  }))
                }
                className={cn(
                  "flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
                  showCsvPreview
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                title={
                  showCsvPreview
                    ? tx("dev.csvPreviewEdit", "Show source")
                    : tx("dev.csvPreview", "Preview")
                }
                aria-label={
                  showCsvPreview
                    ? tx("dev.csvPreviewEdit", "Show source")
                    : tx("dev.csvPreview", "Preview")
                }
                aria-pressed={showCsvPreview}
              >
                {showCsvPreview ? (
                  <Code2 className="h-3 w-3" aria-hidden />
                ) : (
                  <Eye className="h-3 w-3" aria-hidden />
                )}
                {showCsvPreview
                  ? tx("dev.csvPreviewEdit", "Source")
                  : tx("dev.csvPreview", "Preview")}
              </button>
            ) : null}
            {canPreviewFullscreen ? (
              <button
                type="button"
                onClick={() => setPreviewFullscreen((open) => !open)}
                className={cn(
                  "flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium transition-colors",
                  previewFullscreen
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                title={
                  previewFullscreen
                    ? tx("dev.exitFullscreen", "Exit full screen")
                    : tx("dev.fullscreen", "Full screen")
                }
                aria-label={
                  previewFullscreen
                    ? tx("dev.exitFullscreen", "Exit full screen")
                    : tx("dev.fullscreen", "Full screen")
                }
                aria-pressed={previewFullscreen}
                data-testid="dev-preview-fullscreen"
              >
                {previewFullscreen ? (
                  <Minimize2 className="h-3 w-3" aria-hidden />
                ) : (
                  <Maximize2 className="h-3 w-3" aria-hidden />
                )}
                {previewFullscreen
                  ? tx("dev.exitFullscreen", "Exit")
                  : tx("dev.fullscreen", "Full screen")}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => void downloadFile(path, preview?.name)}
              disabled={!preview && !path}
              className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
              title={tx("dev.download", "Download")}
              aria-label={tx("dev.download", "Download")}
              data-testid="dev-file-download"
            >
              <Download className="h-3 w-3" aria-hidden />
              {tx("dev.download", "Download")}
            </button>
            {editable ? (
              <button
                type="button"
                onClick={() => void saveFile(path)}
                disabled={!dirty || saving}
                className={cn(
                  "flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
                  dirty
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
              onClick={() => void refreshFile(path)}
              className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              {fileLoading ? (
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="h-3 w-3" aria-hidden />
              )}
              {tx("dev.refreshFile", "Reload")}
            </button>
            {pane === "left" ? (
              <button
                type="button"
                onClick={() => closeTab(path)}
                className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label={tx("dev.closeTab", "Close tab")}
                title={tx("dev.closeTab", "Close tab")}
              >
                <X className="h-3 w-3" aria-hidden />
                {tx("dev.closeFile", "Close")}
              </button>
            ) : (
              <button
                type="button"
                onClick={toggleSplit}
                className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label={tx("dev.splitClose", "Close split view")}
                title={tx("dev.splitClose", "Close split view")}
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            )}
          </div>
        </div>
        ) : null}
        {variant !== "header" ? (
        <div
          className={cn(
            "flex min-h-0 flex-col",
            variant === "body" ? "min-w-0 flex-1 overflow-hidden" : "flex-1",
          )}
        >
        {!preview && fileLoading ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-muted-foreground">
            <Loader2 className="h-6 w-6 animate-spin" aria-hidden />
            <p className="text-[13px]">
              {tx("dev.loadingFile", "Opening file…")}
            </p>
          </div>
        ) : !preview ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
            <FileWarning className="h-8 w-8 text-destructive/70" aria-hidden />
            <p className="max-w-md text-[13px] text-destructive">
              {previewError || tx("dev.fileNotLoaded", "This file did not open. Retry.")}
            </p>
            <button
              type="button"
              onClick={() => void refreshFile(path)}
              className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-[12px] font-medium text-foreground hover:bg-muted/60"
            >
              {tx("dev.retryFile", "Retry")}
            </button>
          </div>
        ) : kind === "image" && preview?.data_url ? (
          <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-auto bg-[repeating-conic-gradient(hsl(var(--muted))_0%_25%,transparent_0%_50%)] bg-[length:16px_16px] p-6">
            <img
              src={preview.data_url}
              alt={preview.display_path}
              className="max-h-full max-w-full rounded-lg shadow-lg"
            />
            <button
              type="button"
              onClick={() => void downloadFile(path, preview?.name)}
              className="absolute right-3 top-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-background/90 px-2.5 py-1.5 text-[12px] font-medium text-foreground shadow-sm hover:bg-muted"
              data-testid="dev-image-download"
            >
              <Download className="h-3.5 w-3.5" aria-hidden />
              {tx("dev.download", "Download")}
            </button>
          </div>
        ) : kind === "spreadsheet" && preview?.sheets ? (
          <SpreadsheetPreview
            sheets={preview.sheets}
            density="compact"
            testId="dev-spreadsheet-preview"
          />
        ) : kind === "pdf" || kind === "office" ? (
          <PdfPane
            token={token}
            sessionKey={treeKey}
            path={path}
            title={preview?.display_path ?? path}
            size={preview?.size}
            root={currentRoot}
            office={kind === "office"}
            renderAvailable={preview?.render_available !== false}
            renderHint={preview?.render_hint}
          />
        ) : ["binary", "audio", "video"].includes(kind) ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-muted-foreground">
            <FileWarning className="h-8 w-8 opacity-40" aria-hidden />
            <p className="text-[13px]">
              {tx("dev.binaryFile", "Binary file - cannot be displayed as text.")}
            </p>
            <p className="font-mono text-[12px]">{formatSize(preview?.size ?? 0)}</p>
            <button
              type="button"
              onClick={() => void downloadFile(path, preview?.name)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-[12px] font-medium text-foreground hover:bg-muted/60"
              data-testid="dev-binary-download"
            >
              <Download className="h-3.5 w-3.5" aria-hidden />
              {tx("dev.download", "Download")}
            </button>
          </div>
        ) : preview ? (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            {showHtmlPreview ? (
              <DevHtmlPreview
                title={preview.display_path ?? path}
                html={draft}
                path={path}
                token={token}
                sessionKey={treeKey}
                root={currentRoot}
                live={dirty}
                revision={`${preview.size ?? 0}:${draft.length}:${fileLoading ? 1 : 0}`}
              />
            ) : showCsvPreview && csvRows ? (
              <div className="min-h-0 flex-1 overflow-auto">
                <table
                  className="w-max min-w-full border-collapse text-left text-xs"
                  data-testid="dev-csv-preview"
                >
                  <thead className="sticky top-0 bg-muted/90 backdrop-blur">
                    <tr>
                      {(csvRows[0] ?? []).map((cell, index) => (
                        <th
                          key={`h-${index}`}
                          className="border-b border-border/60 px-3 py-1.5 font-semibold text-foreground"
                        >
                          {cell}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {csvRows.slice(1).map((row, rowIndex) => (
                      <tr
                        key={`r-${rowIndex}`}
                        className={cn(rowIndex % 2 === 1 && "bg-muted/20")}
                      >
                        {row.map((cell, cellIndex) => (
                          <td
                            key={`c-${rowIndex}-${cellIndex}`}
                            className="border-b border-border/30 px-3 py-1 text-muted-foreground"
                          >
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : showMdPreview ? (
              <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
                <MarkdownText
                  className={cn(
                    "prose prose-sm max-w-none dark:prose-invert",
                    "prose-headings:scroll-mt-4 prose-headings:font-semibold",
                    "prose-p:leading-relaxed prose-pre:rounded-xl prose-pre:bg-muted/40",
                    "prose-a:text-blue-500 prose-a:underline",
                  )}
                >
                  {draft}
                </MarkdownText>
              </div>
            ) : (
              <PanelErrorBoundary resetKey={path}>
              <Suspense
                fallback={
                  <PlainFileEditor
                    value={draft}
                    readOnly={!editable}
                    onChange={(next) => recordDraftEdit(path, next)}
                    onSave={() => void saveFile(path)}
                  />
                }
              >
                <CodeEditor
                  value={draft}
                  language={preview.language}
                  isDark={theme === "dark"}
                  readOnly={!editable}
                  reveal={
                    reveal && path === reveal.path
                      ? { line: reveal.line, nonce: reveal.nonce }
                      : null
                  }
                  diagnostics={diagnosticsByPath[path]?.diagnostics ?? undefined}
                  reviewBaseline={paneReviewBaseline}
                  reviewHunks={paneReviewHunks}
                  onHunkAction={paneHunkAction}
                  onLookupSymbols={lookupSymbols}
                  onGotoDefinition={gotoDefinition}
                  onFindReferences={findReferences}
                  onHoverInfo={hoverInfo}
                  onLspCompletions={lspCompletions}
                  onSignatureHelp={signatureHelp}
                  onCodeActions={codeActions}
                  onRenameSymbol={renameSymbol}
                  onRequestCompletion={requestCompletion}
                  onRequestEdit={requestInlineEdit}
                  onAddSelectionToChat={
                    onSeedChat
                      ? (payload) => seedSelectionToChat(path, payload)
                      : undefined
                  }
                  onFormatDocument={() => void formatDocument(path)}
                  filePath={path}
                  blameByLine={
                    blameEnabled ? blameByPath[path] ?? null : null
                  }
                  breakpointLines={
                    breakpointsEnabledForPath(path)
                      ? getBreakpointLines(path)
                      : undefined
                  }
                  stoppedLine={
                    debugState?.stopped_frame?.path === path
                      ? debugState.stopped_frame.line
                      : null
                  }
                  onToggleBreakpoint={
                    breakpointsEnabledForPath(path)
                      ? (line) => {
                          const lines = toggleBreakpoint(path, line);
                          void debugOp(token, treeKey, "setBreakpoints", {
                            path,
                            lines,
                          }).catch(() => {
                            /* offline / no session yet - local store still wins */
                          });
                        }
                      : undefined
                  }
                  onChange={(next) => recordDraftEdit(path, next)}
                  onSave={() => void saveFile(path)}
                />
              </Suspense>
              </PanelErrorBoundary>
            )}
            {preview.truncated ? (
              <p className="border-t border-border/50 px-3 py-2 text-[12px] text-muted-foreground">
                {tx(
                  "dev.fileTruncated",
                  "File truncated for preview - editing is disabled.",
                )}
              </p>
            ) : null}
          </div>
        ) : null}
        </div>
        ) : null}
      </>
    );
  };

  const showMode = (next: typeof mode) => {
    setTerminalOpen(false);
    setTerminalMaximized(false);
    setMode(next);
  };
  const moreMenuActive =
    debugOpen ||
    mode === "tests" ||
    mode === "processes" ||
    mode === "project" ||
    mode === "extensions" ||
    mode === "templates" ||
    mode === "mobile" ||
    mode === "agentBrowser";
  const toolbarOtherActive =
    moreMenuActive ||
    mode === "skills" ||
    mode === "evolve" ||
    mode === "rules" ||
    mode === "guardrails" ||
    mode === "agi" ||
    mode === "graph" ||
    mode === "board";
  const overflowMenuItems = (
    wrap: (action: () => void) => () => void,
    exclude: readonly string[] = [],
  ): DevOtherMenuItem[] => [
    {
      key: "skills",
      icon: <PackagePlus className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.installSkill", "Install skill"),
      active: mode === "skills",
      onSelect: wrap(() => showMode("skills")),
    },
    {
      key: "evolve",
      icon: <FlaskConical className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.evolveTab", "Evolve"),
      active: mode === "evolve",
      onSelect: wrap(() => showMode("evolve")),
    },
    {
      key: "rules",
      icon: <BookMarked className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.panel.rules", "Rules"),
      active: mode === "rules",
      onSelect: wrap(() => showMode("rules")),
    },
    {
      key: "guardrails",
      icon: <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.guardrailsTab", "Guardrails"),
      active: mode === "guardrails",
      onSelect: wrap(() => showMode("guardrails")),
    },
    {
      key: "agi",
      icon: <BrainCircuit className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.agiTab", "AGI"),
      active: mode === "agi",
      onSelect: wrap(() => showMode("agi")),
    },
    {
      key: "debug",
      icon: <Bug className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.debug.title", "Debug"),
      active: debugOpen,
      onSelect: wrap(() => {
        setTerminalOpen(false);
        setTerminalMaximized(false);
        setDebugOpen(true);
        setProblemsOpen(false);
      }),
    },
    {
      key: "graph",
      icon: <Waypoints className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.graphTab", "Graph"),
      active: mode === "graph",
      onSelect: wrap(() => showMode("graph")),
    },
    {
      key: "board",
      icon: <ListTodo className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.boardTab", "Tasks"),
      active: mode === "board",
      onSelect: wrap(() => showMode("board")),
    },
    {
      key: "tests",
      separatorBefore: true,
      icon: <Beaker className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.testsTab", "Tests"),
      active: mode === "tests",
      onSelect: wrap(() => showMode("tests")),
    },
    {
      key: "processes",
      icon: <Activity className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.panel.processes", "Processes"),
      active: mode === "processes",
      onSelect: wrap(() => showMode("processes")),
    },
    {
      key: "project",
      icon: <FolderKanban className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.projectTab", "Project"),
      active: mode === "project",
      onSelect: wrap(() => showMode("project")),
    },
    {
      key: "extensions",
      icon: <Blocks className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.panel.extensions", "Extensions"),
      active: mode === "extensions",
      onSelect: wrap(() => showMode("extensions")),
    },
    {
      key: "templates",
      icon: <LayoutTemplate className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.templatesTab", "Templates"),
      active: mode === "templates",
      onSelect: wrap(() => showMode("templates")),
    },
    {
      key: "mobile",
      icon: <Smartphone className="h-3.5 w-3.5 shrink-0" aria-hidden />,
      label: tx("dev.mobileTab", "Mobile"),
      active: mode === "mobile",
      onSelect: wrap(() => showMode("mobile")),
    },
    ...(agentBrowser
      ? [
          {
            key: "agentBrowser",
            icon: (
              <span
                className={cn(
                  "h-1.5 w-1.5 shrink-0 rounded-full",
                  agentBrowser.live
                    ? "animate-pulse bg-emerald-500"
                    : "bg-muted-foreground/50",
                )}
                aria-hidden
              />
            ),
            label: tx("dev.agentBrowserTab", "Agent browser"),
            active: mode === "agentBrowser",
            onSelect: wrap(() => showMode("agentBrowser")),
          },
        ]
      : []),
  ].filter((item) => !exclude.includes(item.key));

  // Chat-maximized rail: the open tabs and submodules stay visible (icons +
  // names) where the center's right edge was, with one click to bring the
  // workbench back. Rendered inline in the main row, right after the hidden
  // center pane; the chat overlays the empty space in between.
  // Same tokens as the left sidebar rows (Sidebar.tsx): 28px tall, 13px text,
  // 16px icons, flat accent on hover/active. The icon-only density mirrors the
  // sidebar's collapsed rows: 32px squares, the name moves to the tooltip.
  const railIconsOnly = railDensity === "icons";
  const railItem = (active: boolean) =>
    cn(
      "flex shrink-0 items-center rounded-md text-[13px] leading-4 outline-none transition-colors focus-visible:ring-1 focus-visible:ring-ring/50",
      railIconsOnly ? "h-8 w-8 justify-center" : "h-7 w-full min-w-0 gap-2 px-2 text-left",
      "[&>svg]:h-4 [&>svg]:w-4 [&>svg]:shrink-0 [&>svg]:stroke-[1.75]",
      active
        ? "bg-sidebar-accent text-sidebar-foreground"
        : "text-sidebar-foreground/85 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
    );
  // "How many are open" pills (Terminal, Browser, File, Rules, Tasks): same
  // shape as the Git one, neutral tone - color stays for attention signals.
  const railCountBadge = (count: number | null | undefined, title: string) =>
    count && count > 0
      ? { text: railCountText(count), tone: RAIL_COUNT_TONE, title }
      : undefined;
  const railHeaderBtn = cn(
    "flex h-8 items-center rounded-md text-[13px] leading-4 text-sidebar-foreground/85 transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
    // Labeled: the one flexible item of the header row, so the icon buttons
    // (full screen, restore) never push the row into overflow.
    railIconsOnly ? "w-8 shrink-0 justify-center" : "min-w-0 gap-2 px-2",
  );
  const railHeaderIconBtn =
    "inline-flex h-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-foreground";
  const restoreAnd = (action: () => void) => () => {
    onMaximizeChat?.();
    action();
  };
  const collapsedRail = collapsed ? (
      <div
        className={cn(
          // No panel background: the rail sits in the shell like the toolbar.
          "ml-auto flex h-full min-w-0 shrink-0 flex-col overflow-y-auto bg-background pt-3",
          railIconsOnly && "items-center",
        )}
        style={{ width: railWidth }}
        data-testid="dev-collapsed-rail"
        data-rail-density={railDensity}
      >
        {/* Explorer heads the rail: the center toolbar that normally reopens
            it is hidden while the chat is maximized. Files is gone from here
            because it duplicated Explorer in this narrow column. */}
        <div
          className={cn(
            "flex shrink-0 items-center gap-0.5 px-2",
            railIconsOnly ? "flex-col py-1" : "h-10",
          )}
        >
          {explorerOpen ? (
            <button
              type="button"
              onClick={() => setExplorerOpen(false)}
              className={railHeaderBtn}
              title={tx("dev.hideExplorer", "Hide explorer")}
              aria-label={railIconsOnly ? tx("dev.hideExplorer", "Hide explorer") : undefined}
              data-testid="dev-rail-hide-explorer"
            >
              <PanelLeftClose className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden />
              {railIconsOnly ? null : (
                <span className="truncate">{tx("dev.panel.explorer", "Explorer")}</span>
              )}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setExplorerOpen(true)}
              className={railHeaderBtn}
              title={tx("dev.showExplorer", "Show explorer")}
              aria-label={railIconsOnly ? tx("dev.showExplorer", "Show explorer") : undefined}
              data-testid="dev-rail-show-explorer"
            >
              <PanelLeftOpen className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden />
              {railIconsOnly ? null : (
                <span className="truncate">{tx("dev.panel.explorer", "Explorer")}</span>
              )}
            </button>
          )}
          {railIconsOnly ? null : <span className="min-w-0 flex-1" aria-hidden />}
          <button
            type="button"
            onClick={toggleFullscreen}
            className={cn(railHeaderIconBtn, railIconsOnly ? "w-8" : "gap-1 px-1.5 text-[12px]")}
            aria-label={
              isFullscreen
                ? tx("dev.exitFullscreen", "Exit full screen")
                : tx("dev.fullscreen", "Full screen")
            }
            title={
              isFullscreen
                ? tx("dev.exitFullscreen", "Exit full screen")
                : tx("dev.fullscreen", "Full screen")
            }
            data-testid="dev-rail-fullscreen"
          >
            {isFullscreen ? (
              <Minimize2 className="h-4 w-4" strokeWidth={1.75} aria-hidden />
            ) : (
              <Maximize2 className="h-4 w-4" strokeWidth={1.75} aria-hidden />
            )}
          </button>
          {onMaximizeChat ? (
            <button
              type="button"
              onClick={onMaximizeChat}
              className={cn(railHeaderIconBtn, "w-8")}
              aria-label={tx("dev.rail.restore", "Restore the workbench to the center")}
              title={tx("dev.rail.restore", "Restore the workbench to the center")}
              data-testid="dev-rail-restore"
            >
              <Columns2 className="h-4 w-4" strokeWidth={1.75} aria-hidden />
            </button>
              ) : null}
            </div>
        {onSelectProject ? (
          <div className={cn("shrink-0 px-2 pb-1", railIconsOnly && "flex justify-center")}>
            <DevProjectSelector
              projectPath={projectPath ?? rootPath}
              projectName={projectName}
              recentProjects={recentProjects ?? []}
              onSelectProject={onSelectProject}
              compact={railIconsOnly}
              triggerClassName={cn(
                // Rendered like a project row of the left sidebar, not a pill.
                "rounded-md border-0 bg-transparent font-normal text-sidebar-foreground/85 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
                railIconsOnly
                  ? "h-8 w-8 justify-center gap-0 px-0 [&>svg:last-child]:hidden"
                  : "h-7 w-full max-w-none justify-start gap-2 px-2 text-[13px] [&>svg:last-child]:h-3.5 [&>svg:last-child]:w-3.5 [&>svg:last-child]:text-muted-foreground/70",
                "[&>svg:first-child]:h-4 [&>svg:first-child]:w-4 [&>svg:first-child]:stroke-[1.75] [&>svg:first-child]:text-muted-foreground/80",
              )}
            />
          </div>
        ) : null}
        <div className={cn("flex flex-col gap-px px-2 py-1", railIconsOnly && "items-center")}>
          {tabs.length > 0 ? (
            <>
              <button
                type="button"
                onClick={() => setRailFilesOpen((open) => !open)}
                className={railItem(railFilesOpen)}
                aria-expanded={railFilesOpen}
                aria-label={
                  railIconsOnly ? `${tx("dev.rail.file", "File")} (${tabs.length})` : undefined
                }
                title={railIconsOnly ? `${tx("dev.rail.file", "File")} (${tabs.length})` : undefined}
                data-testid="dev-rail-files"
              >
                <File aria-hidden />
                {railIconsOnly ? null : (
                  <>
                    <span className="min-w-0 flex-1 truncate">
                      {tx("dev.rail.file", "File")}
                    </span>
                    <span
                      className={cn(RAIL_BADGE_CLASS, RAIL_COUNT_TONE)}
                      title={t("dev.rail.openFiles", {
                        defaultValue: "{{n}} open file(s)",
                        n: tabs.length,
                      })}
                      data-testid="dev-rail-files-badge"
                    >
                      {railCountText(tabs.length)}
                    </span>
                    <ChevronRight
                      className={cn(
                        "!h-3.5 !w-3.5 text-muted-foreground/70 transition-transform",
                        railFilesOpen && "rotate-90",
                      )}
                      aria-hidden
                    />
                  </>
                )}
              </button>
              {railFilesOpen
                ? tabs.map((tab) => (
                    <button
                      key={tab.path}
                      type="button"
                      onClick={restoreAnd(() => openFileByPath(tab.path))}
                      className={cn(
                        railItem(mode === "code" && activeTab === tab.path),
                        !railIconsOnly && "pl-8",
                      )}
                      title={tab.displayPath}
                      aria-label={railIconsOnly ? tab.name : undefined}
                    >
                      <FileTypeIcon name={tab.name} />
                      {railIconsOnly ? null : <span className="truncate">{tab.name}</span>}
                    </button>
                  ))
                : null}
            </>
          ) : null}
          {(
            [
              {
                // First module after the project folder picker.
                key: "agi",
                icon: <BrainCircuit className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                label: tx("dev.agiTab", "AGI"),
                active: mode === "agi",
                go: () => showMode("agi"),
              },
              {
                key: "guardrails",
                icon: <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                label: tx("dev.guardrailsTab", "Guardrails"),
                active: mode === "guardrails",
                go: () => showMode("guardrails"),
              },
              reviewChanges.length > 0
                ? {
                    key: "changes",
                    icon: <GitCompare className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                    label: tx("dev.rail.changes", "Changes"),
                    extra:
                      reviewStats.added + reviewStats.deleted > 0 ? (
                        <DiffPair
                          added={reviewStats.added}
                          deleted={reviewStats.deleted}
                          hideZero
                        />
                      ) : null,
                    active: mode === "diff",
                    go: () => openReviewChanges(),
                  }
                : null,
              {
                key: "git",
                icon: <GitBranch className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                label: tx("dev.panel.git", "Git"),
                // Same signal as the toolbar button: amber for a dirty working
                // tree, sky for commits not pushed yet.
                badge:
                  gitChanges?.is_repo && gitChanges.files.length > 0
                    ? {
                        text: String(gitChanges.files.length),
                        tone: "bg-amber-500/90 text-white",
                        title: t("dev.git.filesChanged", {
                          defaultValue: "{{n}} file(s) changed",
                          n: gitChanges.files.length,
                        }),
                      }
                    : (gitChanges?.ahead ?? 0) > 0
                      ? {
                          text: `${gitChanges?.ahead}↑`,
                          tone: "bg-sky-500/90 text-white",
                          title: tx("dev.git.aheadHint", "Commits not pushed"),
                        }
                      : undefined,
                active: mode === "git",
                go: () => showMode("git"),
              },
              {
                key: "terminal",
                icon: <TerminalSquare className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                label: tx("dev.terminal", "Terminal"),
                badge: railCountBadge(
                  terminals.length,
                  t("dev.rail.openTerminals", {
                    defaultValue: "{{n}} open terminal(s)",
                    n: terminals.length,
                  }),
                ),
                active: terminalOpen,
                go: () => {
                  if (!terminalOpen) openTerminalPanel();
                },
              },
              {
                key: "browser",
                icon: <Globe className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                label: tx("dev.browserTab", "Browser"),
                badge: railCountBadge(
                  browserSrc ? 1 : 0,
                  tx("dev.rail.openBrowser", "1 page open"),
                ),
                active: mode === "browser",
                go: () => {
                  setTerminalOpen(false);
                  setTerminalMaximized(false);
                  if (!browserSrc) void openBrowser(undefined);
                  else setMode("browser");
                },
              },
              {
                key: "skills",
                icon: <PackagePlus className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                label: tx("dev.installSkill", "Install skill"),
                active: mode === "skills",
                go: () => showMode("skills"),
              },
              {
                key: "evolve",
                icon: <FlaskConical className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                label: tx("dev.evolveTab", "Evolve"),
                active: mode === "evolve",
                go: () => showMode("evolve"),
              },
              {
                key: "rules",
                icon: <BookMarked className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                label: tx("dev.panel.rules", "Rules"),
                badge: railCountBadge(
                  railCounts.rules,
                  t("dev.rail.rulesCount", {
                    defaultValue: "{{n}} rule(s)",
                    n: railCounts.rules ?? 0,
                  }),
                ),
                active: mode === "rules",
                go: () => showMode("rules"),
              },
              {
                key: "graph",
                icon: <Waypoints className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                label: tx("dev.graphTab", "Graph"),
                active: mode === "graph",
                go: () => showMode("graph"),
              },
              {
                key: "board",
                icon: <ListTodo className="h-3.5 w-3.5 shrink-0" aria-hidden />,
                label: tx("dev.boardTab", "Tasks"),
                // Open tasks; a blocked one turns the pill amber, it needs a
                // human before the agent can move on.
                badge: railCounts.tasks && railCounts.tasks.open > 0
                  ? {
                      text: railCountText(railCounts.tasks.open),
                      tone: railCounts.tasks.blocked > 0
                        ? "bg-amber-500/90 text-white"
                        : RAIL_COUNT_TONE,
                      title: railCounts.tasks.blocked > 0
                        ? t("dev.rail.openTasksBlocked", {
                            defaultValue: "{{n}} open task(s), {{blocked}} blocked",
                            n: railCounts.tasks.open,
                            blocked: railCounts.tasks.blocked,
                          })
                        : t("dev.rail.openTasks", {
                            defaultValue: "{{n}} open task(s), {{active}} in progress",
                            n: railCounts.tasks.open,
                            active: railCounts.tasks.inProgress,
                          }),
                    }
                  : undefined,
                active: mode === "board",
                go: () => showMode("board"),
              },
            ] as Array<{
              key: string;
              icon: ReactNode;
              label: string;
              extra?: ReactNode;
              badge?: { text: string; tone: string; title: string };
              active: boolean;
              go: () => void;
            } | null>
          )
            .filter((entry) => entry !== null)
            .map((entry) => (
              <button
                key={entry.key}
                type="button"
                onClick={restoreAnd(entry.go)}
                className={cn(railItem(entry.active), "relative")}
                title={railIconsOnly ? entry.label : undefined}
                aria-label={railIconsOnly ? entry.label : undefined}
                data-testid={`dev-rail-${entry.key}`}
              >
                {entry.icon}
                {railIconsOnly ? null : <span className="truncate">{entry.label}</span>}
                {railIconsOnly ? null : entry.extra}
                {entry.badge ? (
                  // Counter pill: at the end of the row when names show, on
                  // the icon's corner when the rail is folded to icons.
                  <span
                    className={cn(
                      RAIL_BADGE_CLASS,
                      entry.badge.tone,
                      railIconsOnly
                        ? "absolute -right-0.5 -top-0.5 min-w-[1rem] px-1 text-[9px] leading-4"
                        : "ml-auto",
                    )}
                    title={entry.badge.title}
                    data-testid={`dev-rail-${entry.key}-badge`}
                  >
                    {entry.badge.text}
                  </span>
                ) : null}
              </button>
            ))}
          <DevOtherMenu
            triggerClassName={railItem(moreMenuActive)}
            showTriggerLabel={!railIconsOnly}
            onSeed={onSeedChat}
            onRun={onRunAction}
            activeFilePath={mode === "code" ? activeTab : null}
            items={overflowMenuItems((action) => restoreAnd(action), [
              "skills",
              "evolve",
              "rules",
              "guardrails",
              "agi",
              "graph",
              "board",
            ])}
          />
          {onToggleRailDensity ? (
            // Closes the list, a small gap under Other so it reads as a control
            // rather than one more module: ">>" folds the names away and
            // leaves the icons, "<<" brings the names back. Persisted by the shell.
            <button
              type="button"
              onClick={onToggleRailDensity}
              className={cn(railItem(false), "mt-3")}
              title={
                railIconsOnly
                  ? tx("dev.rail.expand", "Show names")
                  : tx("dev.rail.collapse", "Collapse")
              }
              aria-label={railIconsOnly ? tx("dev.rail.expand", "Show names") : undefined}
              data-testid={railIconsOnly ? "dev-rail-expand" : "dev-rail-collapse"}
            >
              {railIconsOnly ? <ChevronsLeft aria-hidden /> : <ChevronsRight aria-hidden />}
              {railIconsOnly ? null : (
                <span className="truncate">{tx("dev.rail.collapse", "Collapse")}</span>
              )}
            </button>
          ) : null}
            </div>
          </div>
  ) : null;

  // Problems and Debug belong to the workbench, not to the shell: docked they
  // stack under the right panel, otherwise they span the center as before.
  const problemsPanel = problemsOpen && !collapsed ? (
    <div className="h-44 shrink-0">
      <DevProblemsPanel
        diagnosticsByPath={diagnosticsByPath}
        onOpen={(path, line) => {
          openFileByPath(path);
          setReveal((prev) => ({
            path,
            line,
            nonce: (prev?.nonce ?? 0) + 1,
          }));
          setMode("code");
        }}
        onClose={() => setProblemsOpen(false)}
      />
    </div>
  ) : null;

  const debugPanel = debugOpen && !collapsed ? (
    <div className="h-56 shrink-0 border-t border-border/55">
      <DevDebugPanel
        token={token}
        sessionKey={treeKey}
        activePath={activeTab}
        onState={setDebugState}
        onOpenFrame={(path, line) => {
          openFileByPath(path);
          setReveal((prev) => ({
            path,
            line,
            nonce: (prev?.nonce ?? 0) + 1,
          }));
          setMode("code");
        }}
        onClose={() => setDebugOpen(false)}
      />
    </div>
  ) : null;

  function renderStatusBar() {
    return (
      <DevStatusBar
        connection={connection}
        environment={environmentLabel}
        git={gitStatus}
        ci={ciStatus}
        projectLabel={projectLabel}
        activeDiagnostics={activeTab ? diagnosticsByPath[activeTab] ?? null : null}
        activeFileName={
          activeTab ? baseName(activeTab) : null
        }
        runningAgents={runningAgentChats.size}
        pendingReviewCount={reviewChanges.length}
        contextUsage={contextUsage}
        lastTabAssist={lastTabAssist}
        runtimeHealth={runtimeHealth}
        onProblemsClick={() => setProblemsOpen((open) => !open)}
        onPendingReviewClick={
          reviewChanges.length > 0 ? () => openReviewChanges() : undefined
        }
      />
    );
  }

  // Listing navin's internal workspace to someone who came here to write code
  // is a dead end: it holds the agent's own notes and skills, not their files.
  // Offer the project picker instead, with a way through for the rare case
  // where browsing the internal folder is the actual intent.
  if (showProjectGate) {
    const gate = (
      <div className="flex h-full min-h-0 min-w-0 flex-col items-center justify-center gap-5 overflow-y-auto p-6 text-center">
        <div className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-primary/10 text-primary">
          <FolderOpen className="h-6 w-6" aria-hidden />
        </div>
        <div className="flex flex-col items-center gap-1.5">
          <h2 className="text-[15px] font-semibold text-foreground">
            {tx("dev.gate.title", "Open a project to start coding")}
          </h2>
          <p className="max-w-md text-[12.5px] leading-relaxed text-muted-foreground">
            {tx(
              "dev.gate.body",
              "No project is open yet. Choose a folder on your machine, or ask the agent in the chat to scaffold a new app for you.",
            )}
          </p>
        </div>
        {onSelectProject ? (
          <DevProjectSelector
            projectPath={null}
            projectName={null}
            recentProjects={recentProjects ?? []}
            onSelectProject={onSelectProject}
          />
        ) : null}
        <div className="flex max-w-md flex-col items-center gap-1.5">
          <button
            type="button"
            onClick={() => setWorkspaceAccepted(true)}
            className="text-[12px] text-muted-foreground underline-offset-2 transition-colors hover:text-foreground hover:underline"
            data-testid="dev-gate-use-workspace"
          >
            {tx("dev.gate.useWorkspace", "Browse the Navin projects folder instead")}
          </button>
          <p className="text-[11px] leading-relaxed text-muted-foreground/70">
            {tx(
              "dev.gate.workspaceHint",
              "The Navin projects folder is where new apps land by default. Open one of your own folders to work on an existing project.",
            )}
            {internalWorkspacePath ? ` ${shortWorkspacePath(internalWorkspacePath)}` : ""}
          </p>
        </div>
      </div>
    );
    if (!dockRight) return gate;
    // Docked, the gate takes the same right column as the editor it stands in
    // for. Full width it slid under the centered chat: the project picker was
    // half covered and the right column looked empty.
    return (
      <div className="flex h-full min-h-0 min-w-0 flex-col">
        <div className="flex min-h-0 min-w-0 flex-1">
          <div className="min-w-0 flex-1" aria-hidden />
          <div
            className={cn(
              "flex min-h-0 min-w-0 shrink-0 flex-col overflow-hidden bg-background",
              collapsed && "hidden",
            )}
            style={{ width: panelWidth }}
          >
            {mode === "plan" ? (
              <PanelErrorBoundary>
                <Suspense
                  fallback={
                    <div className="flex flex-1 items-center justify-center text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    </div>
                  }
                >
                  <DevPlanPanel
                    sessionKey={sessionKey}
                    onRunAction={onRunAction}
                    onClose={() => setMode("code")}
                  />
                </Suspense>
              </PanelErrorBoundary>
            ) : (
              gate
            )}
          </div>
          {collapsedRail}
        </div>
        {renderStatusBar()}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
    <DevQuickOpen
      open={quickOpen}
      token={token}
      sessionKey={treeKey}
      onOpenChange={setQuickOpen}
      onSelect={(relativePath, line) => {
        openSearchMatch(relativePath, Math.max(1, line || 1));
        setMode("code");
      }}
    />
    <DevSymbolPicker
      open={symbolPicker}
      token={token}
      sessionKey={treeKey}
      onOpenChange={setSymbolPicker}
      onSelect={(relativePath, line) => {
        openSearchMatch(relativePath, Math.max(1, line || 1));
        setMode("code");
      }}
    />
    <DevCommandPalette
      open={commandPalette}
      commands={workbenchCommands}
      onOpenChange={setCommandPalette}
    />
    <DevLocationPicker
      open={locationPicker != null}
      title={locationPicker?.title ?? ""}
      symbol={locationPicker?.symbol}
      items={locationPicker?.items ?? []}
      onOpenChange={(open) => {
        if (!open) setLocationPicker(null);
      }}
      onSelect={(item) => {
        openSearchMatch(item.path, Math.max(1, item.line || 1));
        setMode("code");
      }}
    />
    <div ref={containerRef} className="relative flex min-h-0 min-w-0 flex-1">
      {/* Explorer. It never moves or resizes because of chat-maximized mode,
          and the hide button keeps working there too: the maximized chat
          reclaims the freed space (the app shell tracks the reported width). */}
      {explorerOpen ? (
        <div
          className={cn(
            "flex min-w-0 flex-none flex-col overflow-hidden bg-muted/20 host-no-drag",
            // Docked Code: the chat sits against this column. A border-r here
            // was a second line next to the chat. Keep the seam only between
            // explorer and editor when they share the same row.
            !dockRight && "border-r border-border/55",
          )}
          style={{ width: explorerWidth, maxWidth: "40%" }}
        >
          {/* Icons only - same height as the Code toolbar so the whole top
              chrome reads as one line. Project name lives in the file tree. */}
          <div className="flex h-10 shrink-0 items-center px-1">
            {/* The tabs scroll when the explorer is narrow; hiding the explorer
                must not scroll out of reach, so it stays outside that strip. */}
            <div className="flex min-w-0 flex-1 items-center overflow-x-auto [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {/* Companions to the file tree. Git, Rules and Processes left for
                the workbench tabs: they need more room than 240px. */}
            {(
              [
                { id: "files" as const, icon: Folder, label: tx("dev.panel.files", "Files") },
                { id: "search" as const, icon: Search, label: tx("dev.panel.search", "Search") },
                {
                  id: "outline" as const,
                  icon: ListTree,
                  label: tx("dev.panel.outline", "Outline"),
                },
              ]
            ).map(({ id, icon: Icon, label }) => (
              <button
                key={id}
                type="button"
                onClick={() => setPanelTab(id)}
                title={label}
                aria-label={label}
                className={cn(
                  "relative inline-flex h-8 w-7 shrink-0 items-center justify-center rounded-md transition-colors",
                  panelTab === id
                    ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
                aria-pressed={panelTab === id}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden />
              </button>
            ))}
            </div>
            <span className="mx-0.5 h-5 w-px shrink-0 bg-border" aria-hidden />
            {panelTab === "files" ? (
              <button
                type="button"
                onClick={() => void loadDir(null)}
                className="inline-flex h-8 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label={tx("dev.refreshTree", "Refresh files")}
                title={tx("dev.refreshTree", "Refresh files")}
              >
                <RefreshCw className="h-4 w-4" aria-hidden />
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setExplorerOpen(false)}
              className="inline-flex h-8 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={tx("dev.hideExplorer", "Hide explorer")}
              title={tx("dev.hideExplorer", "Hide explorer")}
            >
              <PanelLeftClose className="h-4 w-4" aria-hidden />
            </button>
          </div>
          {panelTab === "search" ? (
            <DevSearchPanel
              token={token}
              sessionKey={treeKey}
              onOpenMatch={openSearchMatch}
              seed={searchSeed}
            />
          ) : panelTab === "outline" ? (
            <DevOutlinePanel
              token={token}
              sessionKey={treeKey}
              path={focusedFile}
              activeLine={
                reveal && focusedFile && reveal.path === focusedFile
                  ? reveal.line
                  : null
              }
              onOpen={(relativePath, line) => {
                openSearchMatch(relativePath, line);
                setMode("code");
              }}
            />
          ) : (
          <>
          {/* Naming row for a new file or folder. Placed above the tree rather
              than inline at the target: the name accepts a path, so the folder
              it lands in is stated here instead of being implied by position. */}
          {creating ? (
            <div className="shrink-0 border-b border-border/50 px-2 py-1.5">
              <div className="mb-1 flex items-center gap-1.5 text-[10.5px] uppercase tracking-wide text-muted-foreground">
                {creating.kind === "file" ? (
                  <FilePlus className="h-3 w-3" aria-hidden />
                ) : (
                  <FolderPlus className="h-3 w-3" aria-hidden />
                )}
                <span className="min-w-0 flex-1 truncate">
                  {creating.parent
                    ? tx("dev.createIn", "in {{folder}}").replace(
                        "{{folder}}",
                        baseName(creating.parent),
                      )
                    : projectLabel}
                </span>
                <button
                  type="button"
                  onClick={cancelCreating}
                  className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label={tx("dev.cancel", "Cancel")}
                  title={tx("dev.cancel", "Cancel")}
                >
                  <X className="h-3 w-3" aria-hidden />
                </button>
              </div>
              <input
                autoFocus
                value={createName}
                disabled={createBusy}
                onChange={(event) => setCreateName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void submitCreate();
                  } else if (event.key === "Escape") {
                    event.preventDefault();
                    cancelCreating();
                  }
                }}
                placeholder={
                  creating.kind === "file"
                    ? tx("dev.newFilePlaceholder", "name.ts or folder/name.ts")
                    : tx("dev.newFolderPlaceholder", "name or parent/name")
                }
                className="w-full rounded-md border border-border/70 bg-background px-2 py-1 text-[12px] outline-none focus:border-primary/60"
              />
              <div className="mt-1 flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => void submitCreate()}
                  disabled={createBusy || !createName.trim()}
                  className="rounded-md bg-primary px-2 py-0.5 text-[11px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                >
                  {tx("dev.create", "Create")}
                </button>
                <button
                  type="button"
                  onClick={cancelCreating}
                  className="rounded-md px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  {tx("dev.cancel", "Cancel")}
                </button>
              </div>
              {createError ? (
                <p className="mt-1 text-[11px] text-destructive">{createError}</p>
              ) : null}
            </div>
          ) : null}
          {entryError ? (
            <p className="shrink-0 border-b border-border/50 px-2 py-1.5 text-[11px] text-destructive">
              {entryError}
            </p>
          ) : null}
          <div
            className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2"
            // Right-clicking the empty space below the tree targets the project
            // root, which is where "new file, no folder in mind" belongs.
            onContextMenu={(event) => {
              if (event.target !== event.currentTarget) return;
              openEntryMenu(null, event);
            }}
          >
            {/* The project itself is the first row, so the tree reads as one
                folder you can collapse rather than a bare list of its insides. */}
            <div className="group relative">
              <button
                type="button"
                onClick={() => setRootExpanded((prev) => !prev)}
                onContextMenu={(event) => openEntryMenu(null, event)}
                className="flex w-full min-w-0 items-center gap-1.5 rounded-md py-1 pl-2 pr-2 text-left text-[12.5px] font-medium leading-5 text-foreground/90 transition-colors hover:bg-muted/60 hover:text-foreground"
                aria-expanded={rootExpanded}
              >
                {rootExpanded ? (
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                )}
                {rootExpanded ? (
                  <FolderOpen className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
                ) : (
                  <Folder className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
                )}
                <span className="truncate" title={rootPath ?? projectLabel}>
                  {projectLabel}
                </span>
              </button>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  openEntryMenu(null, event);
                }}
                className="absolute right-1 top-1/2 -translate-y-1/2 rounded bg-background/95 p-0.5 text-muted-foreground opacity-0 shadow-sm transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
                aria-label={`${tx("dev.moreActions", "More actions")} - ${projectLabel}`}
                title={tx("dev.moreActions", "More actions")}
              >
                <MoreHorizontal className="h-3 w-3" aria-hidden />
              </button>
            </div>
            {!rootExpanded ? null : rootState?.loading && !rootState.entries.length ? (
              <div className="flex items-center gap-2 py-1.5 pl-[36px] text-[12px] text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                {tx("dev.loadingTree", "Loading files…")}
              </div>
            ) : rootState?.error ? (
              <p className="py-1.5 pl-[36px] text-[12px] text-destructive">{rootState.error}</p>
            ) : (
              <DevTreeLevel
                parentKey="__root__"
                nodes={nodes}
                expanded={expanded}
                rootPath={rootPath}
                activePath={activeTab}
                dirtyPaths={dirtyPaths}
                reviewByPath={reviewByPath}
                reviewDirs={reviewDirs}
                gitStatusByPath={gitStatusByPath}
                gitDirs={gitDirs}
                diagnostics={diagnosticsByPath}
                depth={1}
                emptyLabel={tx("dev.emptyDir", "empty")}
                onToggleDir={toggleDir}
                onOpenFile={openFile}
                onContextMenu={openEntryMenu}
                menuLabel={tx("dev.moreActions", "More actions")}
                renaming={renaming}
                renameError={renameError}
                onRenameChange={(name) =>
                  setRenaming((prev) => (prev ? { ...prev, name } : prev))
                }
                onRenameSubmit={() => void submitRename()}
                onRenameCancel={() => {
                  setRenaming(null);
                  setRenameError(null);
                }}
                onRequestRename={(entry) => {
                  setRenameError(null);
                  setRenaming({ path: entry.path, name: entry.name });
                }}
                onRequestDelete={setPendingDelete}
                onClipboardKey={handleClipboardKey}
              />
            )}
          </div>
          </>
          )}
        </div>
      ) : null}
      {explorerOpen ? (
        <div
          role="separator"
          aria-orientation="vertical"
          onPointerDown={startDrag("explorer")}
          className={cn(
            "cursor-col-resize bg-transparent transition-colors hover:bg-primary/40",
            // Overlay when the chat is the next column so the handle does not
            // leave a 4px gutter (that read as a second line).
            dockRight
              ? "absolute inset-y-0 z-20 w-1.5"
              : "w-1 shrink-0",
          )}
          style={dockRight ? { left: Math.max(0, explorerWidth - 3) } : undefined}
        />
      ) : null}

      {/* Docked: the chat keeps the center, so the workbench leaves it empty
          and takes a fixed column on the right. Nothing here is absolute - the
          chat simply paints over this gap. */}
      {dockRight ? <div className="min-w-0 flex-1" aria-hidden /> : null}

      {/* Editor / browser area + terminal - min-h-0 is required: without it the
          automatic minimum height of this flex item grows with its content, and
          every nested overflow-y-auto pane (kanban columns, detail drawers)
          silently stops scrolling and spills past the viewport instead.
          Hidden (not unmounted) while the chat is maximized: drafts, terminal
          and previews must survive the toggle. */}
      <div
        className={cn(
          "flex min-h-0 min-w-0 flex-col overflow-hidden",
          dockRight ? "shrink-0 bg-background" : "flex-1",
          collapsed && "hidden",
        )}
        style={dockRight ? { width: panelWidth } : undefined}
      >
        {/* Tab bar - min-w-0 + overflow keeps buttons inside the column so they
            never bleed under (and lose clicks to) the chat panel's tab strip.
            Stays visible when the terminal is maximized: full mode fills the
            space below this menu, it does not hide it. */}
        <div
          className={cn(
            "flex min-w-0 shrink-0 flex-col overflow-hidden bg-muted/15",
            // With the chat panel open it sits to our right and owns the
            // corner; with it closed this toolbar runs under the bell instead.
            chatOpen ? "pr-2" : NOTIFICATION_GUTTER,
          )}
        >
          {/* Icon + label, same type and colors as the Other menu. */}
          {(() => {
            const navBtn = (active: boolean) =>
              cn(
                "inline-flex h-8 shrink-0 items-center justify-center gap-2 rounded-[12px] px-2.5 text-[13px] leading-5 transition-colors",
                active
                  ? "bg-muted text-foreground"
                  : "text-foreground/80 hover:bg-foreground/[0.055] hover:text-foreground dark:hover:bg-white/[0.08]",
              );
            const label = (text: string) => (
              <span className="max-w-[8rem] truncate">{text}</span>
            );
            return (
              <div
                className="flex h-10 items-center gap-0.5 px-0.5"
                data-testid="dev-workbench-toolbar"
              >
                {explorerOpen ? (
                  <button
                    type="button"
                    onClick={() => setExplorerOpen(false)}
                    className="flex shrink-0 items-center gap-1.5 rounded-[12px] px-2 py-1.5 text-[13px] leading-5 text-foreground/80 hover:bg-foreground/[0.055] hover:text-foreground dark:hover:bg-white/[0.08]"
                    aria-label={tx("dev.hideExplorer", "Hide explorer")}
                    title={tx("dev.hideExplorer", "Hide explorer")}
                    data-testid="dev-hide-explorer"
                  >
                    <PanelLeftClose className="h-4 w-4 shrink-0" aria-hidden />
                    <span>{tx("dev.panel.explorer", "Explorer")}</span>
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => setExplorerOpen(true)}
                    className="flex shrink-0 items-center gap-1.5 rounded-[12px] px-2 py-1.5 text-[13px] leading-5 text-foreground/80 hover:bg-foreground/[0.055] hover:text-foreground dark:hover:bg-white/[0.08]"
                    aria-label={tx("dev.showExplorer", "Show explorer")}
                    title={tx("dev.showExplorer", "Show explorer")}
                    data-testid="dev-show-explorer"
                  >
                    <PanelLeftOpen className="h-4 w-4 shrink-0" aria-hidden />
                    <span>{tx("dev.panel.explorer", "Explorer")}</span>
                  </button>
                )}
                {/* Primary modules can scroll. Other stays pinned next to
                    them so Browser never slides under the project chip. */}
                <div className="flex min-w-0 flex-1 items-center gap-1">
                <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto overflow-y-hidden [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
                    <button
                      type="button"
                      onClick={() => showMode("agi")}
                      className={navBtn(mode === "agi")}
                      title={tx("dev.agiTab", "AGI")}
                      aria-label={tx("dev.agiTab", "AGI")}
                      data-testid="dev-toolbar-agi"
                    >
                      <BrainCircuit className="h-4 w-4 shrink-0" aria-hidden />
                      {label(tx("dev.agiTab", "AGI"))}
                    </button>
                    {reviewChanges.length > 0 ? (
                      <button
                        type="button"
                        onClick={() => openReviewChanges()}
                        className={navBtn(mode === "diff")}
                        title={tx("dev.rail.changes", "Changes")}
                        aria-label={tx("dev.rail.changes", "Changes")}
                        data-testid="dev-toolbar-changes"
                      >
                        <GitCompare className="h-4 w-4 shrink-0" aria-hidden />
                        {label(tx("dev.rail.changes", "Changes"))}
                        {reviewStats.added + reviewStats.deleted > 0 ? (
                          <DiffPair
                            added={reviewStats.added}
                            deleted={reviewStats.deleted}
                            hideZero
                          />
                        ) : null}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => showMode("git")}
                      className={cn(navBtn(mode === "git"), "relative")}
                      title={tx("dev.panel.git", "Git")}
                      aria-label={tx("dev.panel.git", "Git")}
                    >
                      <GitBranch className="h-4 w-4 shrink-0" aria-hidden />
                      {label(tx("dev.panel.git", "Git"))}
                      {gitChanges?.is_repo && gitChanges.files.length > 0 ? (
                        <span className={cn(TOOLBAR_CORNER_BADGE_CLASS, "bg-amber-500/90 text-white")}>
                          {gitChanges.files.length}
                        </span>
                      ) : (gitChanges?.ahead ?? 0) > 0 ? (
                        <span className={cn(TOOLBAR_CORNER_BADGE_CLASS, "bg-sky-500/90 text-white")}>
                          {gitChanges?.ahead}↑
                        </span>
                      ) : null}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (terminalOpen) {
                          setTerminalOpen(false);
                          setTerminalMaximized(false);
                        } else {
                          openTerminalPanel();
                        }
                      }}
                      className={cn(navBtn(terminalOpen), "relative")}
                      title={tx("dev.toggleTerminal", "Toggle terminal")}
                      aria-label={tx("dev.terminal", "Terminal")}
                    >
                      <TerminalSquare className="h-4 w-4 shrink-0" aria-hidden />
                      {label(tx("dev.terminal", "Terminal"))}
                      {terminals.length > 0 ? (
                        <span
                          className={cn(TOOLBAR_CORNER_BADGE_CLASS, RAIL_COUNT_TONE)}
                          title={t("dev.rail.openTerminals", {
                            defaultValue: "{{n}} open terminal(s)",
                            n: terminals.length,
                          })}
                          data-testid="dev-toolbar-terminal-badge"
                        >
                          {railCountText(terminals.length)}
                        </span>
                      ) : null}
                    </button>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setTerminalOpen(false);
                        setTerminalMaximized(false);
                        if (!browserSrc) {
                          void openBrowser(undefined);
                        } else {
                          setMode("browser");
                        }
                      }}
                      className={cn(navBtn(mode === "browser"), "relative")}
                      title={tx("dev.browserTab", "Browser")}
                      aria-label={tx("dev.browserTab", "Browser")}
                    >
                      <Globe className="h-4 w-4 shrink-0" aria-hidden />
                      {label(tx("dev.browserTab", "Browser"))}
                      {browserSrc ? (
                        <span
                          className={cn(TOOLBAR_CORNER_BADGE_CLASS, RAIL_COUNT_TONE)}
                          title={tx("dev.rail.openBrowser", "1 page open")}
                          data-testid="dev-toolbar-browser-badge"
                        >
                          1
                        </span>
                      ) : null}
                    </button>
                    <div className="shrink-0">
                    <DevOtherMenu
                      triggerClassName={navBtn(toolbarOtherActive)}
                      showTriggerLabel
                      onSeed={onSeedChat}
                      onRun={onRunAction}
                      activeFilePath={mode === "code" ? activeTab : null}
                      items={overflowMenuItems((action) => action, ["agi"])}
                    />
                    </div>
                    </div>
                {onSelectProject ? (
                  <div className="ml-0.5 flex shrink-0 items-center [&_button]:h-8">
                    <DevProjectSelector
                      projectPath={projectPath ?? rootPath}
                      projectName={projectName}
                      recentProjects={recentProjects ?? []}
                      onSelectProject={onSelectProject}
                      compact
                    />
                  </div>
                ) : null}
                <button
                  type="button"
                  onClick={toggleFullscreen}
                  className="ml-0.5 flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1.5 text-[12px] text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label={
                    isFullscreen
                      ? tx("dev.exitFullscreen", "Exit full screen")
                      : tx("dev.fullscreen", "Full screen")
                  }
                  title={
                    isFullscreen
                      ? tx("dev.exitFullscreen", "Exit full screen")
                      : tx("dev.fullscreen", "Full screen")
                  }
                  data-testid="dev-fullscreen"
                >
                  {isFullscreen ? (
                    <Minimize2 className="h-4 w-4" aria-hidden />
                  ) : (
                    <Maximize2 className="h-4 w-4" aria-hidden />
                  )}
                </button>
                {onMaximizeChat ? (
                  <button
                    type="button"
                    onClick={onMaximizeChat}
                    className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label={tx("dev.chatMaximize", "Expand the chat (hide the workbench)")}
                    title={tx("dev.chatMaximize", "Expand the chat (hide the workbench)")}
                    data-testid="dev-maximize-chat"
                  >
                    <PanelRightOpen className="h-4 w-4" aria-hidden />
                  </button>
                ) : null}
              </div>
            );
          })()}
        </div>

        <div
          className={cn(
            "flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden",
            terminalOpen && terminalMaximized && "hidden",
          )}
        >
        {/* Open files.
            These sat in the toolbar above until every control there was
            shrink-0 and the tab strip was the single flexible item, so a narrow
            column - the normal case with the chat panel open - handed the strip
            whatever the buttons left over, down to nothing. The tabs were still
            open and still in the DOM, merely zero pixels wide, which reads as
            "opening a file replaced the last one". A row of their own cannot be
            squeezed by the toolbar, and costs height only once a file is open. */}
        {tabs.length > 0 && mode !== "diff" ? (
          <div
            className="flex h-9 shrink-0 items-center gap-1 overflow-x-auto border-b border-border/55 bg-muted/10 px-2"
            data-testid="editor-tabs"
          >
            {tabs.map((tab) => {
              // Diff mode never renders this strip (the diff shell has its own
              // file switcher), so only the code view can mark a tab active.
              const isActive =
                mode === "code" &&
                (activeTab === tab.path ||
                  (splitTab != null && splitTab === tab.path));
              return (
              <div
                key={tab.path}
                className={cn(
                  "group flex shrink-0 cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12px] font-medium transition-colors",
                  isActive
                    ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
                onClick={() => {
                  setMode("code");
                  // With the split view open, the click targets the focused
                  // pane, mirroring how Cursor routes files between groups.
                  if (splitTab != null && focusedPane === "right") {
                    setSplitTab(tab.path);
                  } else {
                  setActiveTab(tab.path);
                  }
                  if (!previewsRef.current[tab.path] && !loadingPaths[tab.path]) {
                    void loadPreview(tab.path);
                  }
                }}
                onAuxClick={(event) => {
                  // Middle-click closes any tab, including the last one.
                  if (event.button === 1) {
                    event.preventDefault();
                    event.stopPropagation();
                    closeTab(tab.path);
                  }
                }}
              >
                <FileTypeIcon name={tab.name} />
                <span
                  className={cn(
                    "max-w-[10rem] truncate",
                    (diagnosticsByPath[tab.path]?.errors ?? 0) > 0
                      ? "text-red-500"
                      : (diagnosticsByPath[tab.path]?.warnings ?? 0) > 0
                        ? "text-orange-400"
                        : reviewByPath.has(tab.path)
                          ? reviewStatusBadge(reviewByPath.get(tab.path)!.status).className
                          : dirtyPaths.has(tab.path)
                            ? "text-amber-500"
                            : undefined,
                  )}
                  title={tab.displayPath}
                >
                  {tab.name}
                </span>
                {dirtyPaths.has(tab.path) ? (
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500/90"
                    aria-label={tx("dev.unsaved", "Unsaved changes")}
                  />
                ) : null}
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    closeTab(tab.path);
                  }}
                  className={cn(
                    "rounded p-0.5 transition-opacity hover:bg-muted",
                    // Always visible on the active tab so the last open file
                    // can still be closed (hover-only X was easy to miss).
                    isActive
                      ? "opacity-70 hover:opacity-100"
                      : "opacity-0 group-hover:opacity-100",
                  )}
                  aria-label={tx("dev.closeTab", "Close tab")}
                >
                  <X className="h-3 w-3" aria-hidden />
                </button>
              </div>
              );
            })}
          </div>
        ) : null}

        {/* After accept-all / last hunk: stay on #/code and offer run/preview. */}
        {postReviewNextSteps && reviewChanges.length === 0 ? (
          <div
            className="flex shrink-0 items-center gap-2 border-b border-border/55 bg-emerald-500/[0.07] px-3 py-1.5"
            data-testid="dev-post-review-next-steps"
          >
            <Check className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
            <span className="min-w-0 flex-1 truncate text-[12px] text-foreground/90">
              {tx(
                "dev.review.nextSteps",
                "Edits accepted. Run tests or open Preview without leaving Code.",
              )}
            </span>
            {onRunAction ? (
                  <button
                    type="button"
                onClick={() => {
                  runTestsFor("", "run");
                  setPostReviewNextSteps(false);
                }}
                className="flex shrink-0 items-center gap-1 rounded-md bg-foreground px-2 py-1 text-[11px] font-medium text-background transition-opacity hover:opacity-90"
              >
                <FlaskConical className="h-3 w-3" aria-hidden />
                {tx("dev.runTests", "Run Tests")}
                  </button>
            ) : null}
            <button
              type="button"
              onClick={() => {
                if (!browserSrc) {
                  void openBrowser(undefined);
                } else {
                  setMode("browser");
                }
                setPostReviewNextSteps(false);
              }}
              className="flex shrink-0 items-center gap-1 rounded-md border border-border/60 px-2 py-1 text-[11px] font-medium text-foreground transition-colors hover:bg-muted"
            >
              <Globe className="h-3 w-3" aria-hidden />
              {tx("dev.browserTab", "Browser")}
            </button>
            <button
              type="button"
              onClick={() => setPostReviewNextSteps(false)}
              className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={tx("dev.review.dismissNextSteps", "Dismiss")}
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
            </div>
            ) : null}

        {/* Content */}
        {mode === "diff" && diffFile ? (
          <div
            className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
            data-testid="dev-diff-shell"
          >
            {reviewDiffFiles.length > 0 ? (
              <div className="flex shrink-0 items-center gap-1 border-b border-border/50 bg-muted/15 px-2 py-1">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      className="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                      data-testid="dev-review-diff-files"
                    >
                      <File className="h-3.5 w-3.5 shrink-0" aria-hidden />
                      <span className="tabular-nums">{reviewDiffFiles.length}</span>
                      <span>{tx("dev.rail.file", "File")}</span>
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    align="start"
                    side="bottom"
                    sideOffset={6}
                    className="w-64"
                    data-testid="dev-review-files-menu"
                  >
                    {reviewDiffFiles.map((change) => (
                      <DropdownMenuItem
                        key={change.path}
                        onSelect={() => openReviewFile(change)}
                      >
                        <FileTypeIcon name={baseName(change.path)} />
                        <span className="min-w-0 flex-1 truncate">
                          {change.display_path}
                        </span>
                        {(change.added ?? 0) + (change.deleted ?? 0) > 0 ? (
                          <DiffPair
                            added={change.added ?? 0}
                            deleted={change.deleted ?? 0}
                            hideZero
                          />
                        ) : null}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            ) : null}
            <DevDiffView
              token={token}
              sessionKey={treeKey}
              file={diffFile}
              against={compareAgainst}
              onBack={closeDiff}
              onOpenFile={(relativePath) => {
                closeDiff();
                openFileByPath(projectAbsolutePath(relativePath));
              }}
            />
          </div>
        ) : mode === "graph" ? (
          <PanelErrorBoundary>
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
          </PanelErrorBoundary>
        ) : mode === "templates" ? (
          <PanelErrorBoundary>
            <div className="min-h-0 flex-1 overflow-auto">
              <AppTemplatesSettings
                embedded
                onCreated={(dest, _slug, previewUrl) => {
                  onSelectProject?.(dest);
                  setMode("code");
                  if (previewUrl) {
                    void openBrowser(previewUrl);
                  } else {
                    requestAgentStartPreview();
                  }
                }}
              />
            </div>
          </PanelErrorBoundary>
        ) : mode === "project" ? (
          <PanelErrorBoundary>
            <div className="min-h-0 flex-1 overflow-auto">
              <ProjectHomeView
                sessions={sessions ?? []}
                projectPath={projectPath ?? rootPath}
                projectName={projectName ?? null}
                recentProjects={recentProjects ?? []}
                onSelectProject={(path, name) => {
                  onSelectProject?.(path, name ?? undefined);
                }}
                onOpenCode={() => setMode("code")}
                onOpenChat={(key) => onOpenChat?.(key)}
                onResumeGoal={(seed) => {
                  onResumeProjectGoal?.(seed);
                  setMode("code");
                }}
              />
            </div>
          </PanelErrorBoundary>
        ) : mode === "plan" ? (
          <PanelErrorBoundary>
          <Suspense
            fallback={
              <div className="flex flex-1 items-center justify-center text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              </div>
            }
          >
            <DevPlanPanel
              sessionKey={sessionKey}
              onRunAction={onRunAction}
              onClose={() => setMode("code")}
            />
          </Suspense>
          </PanelErrorBoundary>
        ) : mode === "board" ? (
          <PanelErrorBoundary>
          <Suspense
            fallback={
              <div className="flex flex-1 items-center justify-center text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              </div>
            }
          >
            <DevBoardPanel
              sessionKey={sessionKey}
              projectPath={projectPath ?? rootPath}
              onRunAction={onRunAction}
            />
          </Suspense>
          </PanelErrorBoundary>
        ) : mode === "guardrails" ? (
          <PanelErrorBoundary>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <Suspense
            fallback={
              <div className="flex flex-1 items-center justify-center text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              </div>
            }
          >
            <DevGuardrailsPanel
              sessionKey={sessionKey}
              projectPath={projectPath ?? rootPath}
              onRunAction={onRunAction}
              onOpenAgi={() => showMode("agi")}
            />
          </Suspense>
          </div>
          </PanelErrorBoundary>
        ) : mode === "agi" ? (
          <PanelErrorBoundary>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <Suspense
            fallback={
              <div className="flex flex-1 items-center justify-center text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              </div>
            }
          >
            <DevAgiPanel
              sessionKey={sessionKey}
              onOpenSettings={() => {
                const params = new URLSearchParams();
                if (sessionKey) params.set("chat", sessionKey);
                params.set("section", "advanced");
                window.location.hash = `#/settings?${params.toString()}`;
              }}
            />
          </Suspense>
          </div>
          </PanelErrorBoundary>
        ) : mode === "tests" ? (
          <PanelErrorBoundary>
          <Suspense
            fallback={
              <div className="flex flex-1 items-center justify-center text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              </div>
            }
          >
            <TestExplorerPanel sessionKey={treeKey} />
          </Suspense>
          </PanelErrorBoundary>
        ) : mode === "extensions" ? (
          <PanelErrorBoundary>
            <Suspense
              fallback={
                <div className="flex flex-1 items-center justify-center text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                </div>
              }
            >
              <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
                <DevExtensionsPanel token={token} sessionKey={treeKey} />
              </div>
            </Suspense>
          </PanelErrorBoundary>
        ) : mode === "git" ? (
          <PanelErrorBoundary>
            <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
              <DevGitPanel
                token={token}
                sessionKey={treeKey}
                selectedPath={null}
                onShowDiff={(path) => showDiff(path)}
                onDidCommit={refreshGitStatus}
                onWorkingTreeChanged={refreshWorkingTreeFiles}
                onRunAction={onRunAction}
                projectPath={currentRoot}
                openCheckpointsSignal={checkpointsSignal}
              />
            </div>
          </PanelErrorBoundary>
        ) : mode === "rules" ? (
          <PanelErrorBoundary>
            <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
              <DevRulesPanel token={token} sessionKey={treeKey} />
            </div>
          </PanelErrorBoundary>
        ) : mode === "processes" ? (
          <PanelErrorBoundary>
            <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
              <DevProcessesPanel token={token} />
            </div>
          </PanelErrorBoundary>
        ) : mode === "evolve" ? (
          <PanelErrorBoundary>
          <Suspense
            fallback={
              <div className="flex flex-1 items-center justify-center text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              </div>
            }
          >
            <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
              <EvolveWorkbench
                chatOpen
              projectPath={projectPath ?? rootPath}
                projectName={projectName ?? null}
                recentProjects={recentProjects ?? []}
                onSelectProject={(path, name) => {
                  onSelectProject?.(path, name);
                }}
              />
            </div>
          </Suspense>
          </PanelErrorBoundary>
        ) : mode === "mobile" ? (
          <DevMobilePreview
            client={client}
            chatId={chatId}
            onSeedChat={onSeedChat}
            autoStart={mobileAutoStart}
            className="min-h-0 flex-1"
          />
        ) : mode === "browser" ? (
          <DevPreviewBrowser
            token={token}
            url={browserUrl}
                src={browserSrc}
            nonce={browserNonce}
            error={browserSelfError}
            starting={browserStarting}
            looksLikeWebApp={looksLikeWebApp}
            previewTargetPort={previewTargetPort}
            showConsole={showPreviewConsole}
            problemCount={previewProblemCount}
            onUrlChange={setBrowserUrl}
            onOpen={(next) => void openBrowser(next)}
            onRefresh={() => refreshPreviewFrame()}
            onOpenExternal={() => openPreviewExternal()}
            onRetry={() => {
                      previewAutoTriedRef.current = false;
                      void openBrowser(browserUrl || undefined);
                    }}
            onStartViaChat={
              onRunAction && looksLikeWebApp
                ? () => {
                        previewAutoTriedRef.current = true;
                        requestAgentStartPreview();
                  }
                : undefined
            }
            onSeedChat={onSeedChat}
            onRunChat={onRunAction ? (text) => onRunAction(text) : undefined}
            resolveSourceFile={resolvePickedSourceFile}
            onToggleConsole={() => setShowPreviewConsole((value) => !value)}
            onProblemCount={setPreviewProblemCount}
            onClose={() => setMode("code")}
          />
        ) : mode === "agentBrowser" && agentBrowser ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center gap-2 border-b border-border/50 px-3 py-2">
              <span
                className={cn(
                  "h-2 w-2 shrink-0 rounded-full",
                  agentBrowser.live
                    ? "animate-pulse bg-emerald-500"
                    : "bg-muted-foreground/40",
                )}
                aria-hidden
              />
              <span className="shrink-0 text-[11px] font-medium text-muted-foreground">
                {agentBrowser.live
                  ? tx("dev.agentBrowserLive", "Live")
                  : tx("dev.agentBrowserEnded", "Session closed")}
              </span>
              <span
                className="min-w-0 flex-1 truncate rounded-md bg-muted/40 px-2 py-1 font-mono text-[11px] text-muted-foreground"
                title={agentBrowser.url ?? undefined}
              >
                {agentBrowser.url ?? ""}
                  </span>
                  <button
                    type="button"
                onClick={() => setAgentBrowserTakeover((on) => !on)}
                disabled={!agentBrowser.live || !chatId}
                    className={cn(
                  "shrink-0 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors disabled:opacity-40",
                  agentBrowserTakeover
                    ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                    : "border-border/60 text-muted-foreground hover:text-foreground",
                )}
                title={tx(
                  "dev.agentBrowserTakeoverHint",
                  "Cliquez et tapez directement dans la page, par exemple pour resoudre un captcha.",
                )}
              >
                {agentBrowserTakeover
                  ? tx("dev.agentBrowserTakeoverOn", "Vous avez la main")
                  : tx("dev.agentBrowserTakeover", "Prendre la main")}
                  </button>
                <button
                  type="button"
                onClick={() => setMode("code")}
                className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                title={tx("dev.agentBrowserMinimize", "Reduire")}
              >
                <Minus className="h-3.5 w-3.5" aria-hidden />
                </button>
                  <button
                    type="button"
                onClick={() => {
                  if (chatId) client.agentBrowserClose(chatId);
                  setAgentBrowser(null);
                  setAgentBrowserTakeover(false);
                  setMode("code");
                }}
                className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-red-500"
                title={tx("dev.agentBrowserClose", "Fermer le navigateur")}
              >
                <X className="h-3.5 w-3.5" aria-hidden />
                  </button>
              </div>
            <div className="relative min-h-0 flex-1 bg-neutral-950">
              {agentBrowser.frame ? (
                <img
                  src={`data:image/jpeg;base64,${agentBrowser.frame}`}
                  alt={tx("dev.agentBrowserTab", "Agent browser")}
                  className={cn(
                    "absolute inset-0 h-full w-full object-contain",
                    agentBrowserTakeover && "cursor-crosshair",
                  )}
                  tabIndex={agentBrowserTakeover ? 0 : -1}
                  onClick={agentBrowserTakeover ? sendAgentBrowserClick : undefined}
                  onWheel={agentBrowserTakeover ? sendAgentBrowserScroll : undefined}
                  onKeyDown={agentBrowserTakeover ? sendAgentBrowserKey : undefined}
                  draggable={false}
                />
              ) : (
                <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center text-muted-foreground">
                  <Loader2 className="h-8 w-8 animate-spin opacity-50" aria-hidden />
                  <p className="max-w-sm text-[13px] leading-6">
                    {tx(
                      "dev.agentBrowserWaiting",
                      "L'agent ouvre son navigateur - le flux demarre dans un instant.",
                    )}
                  </p>
              </div>
              )}
                    </div>
            {agentBrowser.actions.length > 0 ? (
              <div className="max-h-24 shrink-0 overflow-y-auto border-t border-border/50 bg-muted/20 px-3 py-1.5">
                {agentBrowser.actions.slice(-8).map((line, index) => (
                  <p
                    key={`${index}-${line}`}
                    className="truncate font-mono text-[10.5px] leading-4 text-muted-foreground"
                    title={line}
                  >
                    {line}
                  </p>
                ))}
              </div>
                ) : null}
              </div>
        ) : mode === "skills" ? (
          <PanelErrorBoundary>
            <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
              <InstallSkillDialog
                variant="panel"
                open
                onOpenChange={(next) => {
                  if (!next) setMode("code");
                }}
                projectPath={projectPath ?? rootPath}
                onInstalled={() => {
                  publishNotification({
                    level: "success",
                    source: "session",
                    title: tx("dev.installSkillDone", "Skill installed"),
                    key: "skills:install",
                  });
                }}
              />
            </div>
          </PanelErrorBoundary>
        ) : activeTab ? (
          splitTab != null ? (
            <div className={cn(
              "flex min-h-0 min-w-0 flex-1 flex-col",
              previewFullscreen &&
                "fixed inset-y-0 right-0 z-[80] bg-background left-0 lg:left-[var(--navin-host-sidebar,0px)]",
            )}>
              {renderEditorPane(
                focusedPane === "right" ? splitTab : activeTab,
                focusedPane,
                "header",
              )}
              <div ref={splitContainerRef} className="flex min-h-0 min-w-0 flex-1">
                <div
                  className={cn(
                    "flex min-h-0 min-w-0 flex-col",
                    focusedPane === "left" && "bg-muted/10",
                  )}
                  style={{ width: `${splitRatio * 100}%` }}
                  onPointerDownCapture={() => setFocusedPane("left")}
                >
                  {renderEditorPane(activeTab, "left", "body")}
                </div>
                <div
                  role="separator"
                  aria-orientation="vertical"
                  onPointerDown={startSplitDrag}
                  className="w-1 shrink-0 cursor-col-resize bg-border/40 transition-colors hover:bg-primary/40"
                />
                <div
                  className={cn(
                    "flex min-h-0 min-w-0 flex-1 flex-col",
                    focusedPane === "right" && "bg-muted/10",
                  )}
                  onPointerDownCapture={() => setFocusedPane("right")}
                >
                  {renderEditorPane(splitTab, "right", "body")}
                </div>
              </div>
          </div>
        ) : (
            <div className={cn(
              "flex min-h-0 flex-1 flex-col",
              previewFullscreen &&
                "fixed inset-y-0 right-0 z-[80] bg-background left-0 lg:left-[var(--navin-host-sidebar,0px)]",
            )}>
              {renderEditorPane(activeTab, "left")}
            </div>
          )
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-muted-foreground">
                <Code2 className="h-8 w-8 opacity-40" aria-hidden />
                <p className="max-w-md text-[13px] leading-6">
                  {tx(
                    "dev.editorEmpty",
                    "Open a file from the explorer, or ask the agent to build something - plan, code, run, and debug together.",
                  )}
                </p>
            {onSeedChat ? (
              <button
                type="button"
                onClick={() =>
                  onSeedChat(
                    "Scaffold a small working app in this project, then run tests if a runner is present.",
                  )
                }
                className="rounded-md bg-foreground px-3 py-1.5 text-[12px] font-medium text-background transition-opacity hover:opacity-90"
                data-testid="dev-empty-ask-agent"
              >
                {tx("dev.askAgentScaffold", "Ask agent to scaffold")}
              </button>
            ) : null}
                {onSelectProject ? (
                  <div className="mt-2">
                    <DevProjectSelector
                      projectPath={projectPath ?? rootPath}
                      projectName={projectName}
                      recentProjects={recentProjects ?? []}
                      onSelectProject={onSelectProject}
                  compact
                    />
                  </div>
                ) : null}
          </div>
        )}
       </div>

        {/* Terminal panel */}
        {terminalOpen ? (
          <div
            className={cn(
              "flex shrink-0 flex-col border-t border-border/55 bg-[#f6f8fa] dark:bg-[#111318]",
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
                    {term.kind === "agent" ? (
                      term.background ? (
                        <InfinityIcon
                          className="h-3.5 w-3.5 shrink-0 text-sky-400"
                          aria-label={tx("dev.backgroundProcess", "Background process")}
                        />
                      ) : (
                        <Bot
                          className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                          aria-label={tx("dev.agentTerminal", "Agent terminal")}
                        />
                      )
                    ) : term.sandbox ? (
                      <ShieldCheck
                        className="h-3.5 w-3.5 shrink-0 text-emerald-500"
                        aria-label={tx("dev.isolatedTerminalTab", "Isolated terminal (sandbox)")}
                        data-testid="terminal-tab-sandbox"
                      />
                    ) : null}
                    <span className="max-w-[9rem] truncate">
                      {term.title}
                      {term.exited
                        ? term.kind === "agent" && term.exitCode
                          ? ` ✗ ${term.exitCode}`
                          : " ✓"
                        : ""}
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
                <button
                  type="button"
                  onClick={() => setShellMenuOpen((open) => !open)}
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label={tx("dev.selectShell", "Select shell")}
                  data-testid="terminal-shell-menu"
                >
                  <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                </button>
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
                    <div className="my-1 border-t border-border/50" role="separator" />
                    <button
                      type="button"
                      onClick={() => spawnTerminal(undefined, undefined, { sandbox: true })}
                      title={tx(
                        "dev.isolatedTerminalHint",
                        "Same OS sandbox as the agent's commands: writes limited to this project and toolchain caches, reads and network open.",
                      )}
                      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] text-foreground hover:bg-muted"
                      data-testid="terminal-new-isolated"
                    >
                      <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" aria-hidden />
                      {tx("dev.isolatedTerminal", "Isolated terminal")}
                      <span className="ml-auto text-[10px] text-muted-foreground">
                        {tx("dev.isolatedTerminalBadge", "sandbox")}
                      </span>
                    </button>
                  </div>
                ) : null}
              </div>
            </div>
            <div className="relative min-h-0 flex-1">
              {terminals.map((term) => (
                <div key={term.id} className="absolute inset-0">
                  <PanelErrorBoundary>
                  <Suspense fallback={null}>
                    {term.kind === "agent" ? (
                      <AgentExecTerminal
                        termId={term.id}
                        backlog={agentTermBacklog}
                        subscribe={subscribeAgentTerm}
                        isDark={theme === "dark"}
                        active={activeTerminal === term.id}
                      />
                    ) : (
                    <DevTerminal
                      terminalId={term.id}
                      shell={term.shell}
                      chatId={chatId}
                      cwd={term.cwd}
                      isDark={theme === "dark"}
                      active={activeTerminal === term.id}
                      exitedLabel={tx("dev.terminalExited", "process exited")}
                      restartedLabel={tx(
                        "dev.terminalRestarted",
                        "connection lost - new shell started",
                      )}
                      lostLabel={tx(
                        "dev.terminalLost",
                        "This terminal could not be restored. Close it and open a new one.",
                      )}
                      sandbox={term.sandbox}
                      sandboxLabel={tx(
                        "dev.terminalSandboxed",
                        "isolated terminal - writes limited to this project and toolchain caches",
                      )}
                      sandboxUnavailableLabel={tx(
                        "dev.terminalSandboxUnavailable",
                        "sandbox unavailable on this host - this shell runs unconfined",
                      )}
                      onSandbox={handleTerminalSandbox}
                      onExit={handleTerminalExit}
                    />
                    )}
                  </Suspense>
                  </PanelErrorBoundary>
                </div>
              ))}
            </div>
          </div>
        ) : null}
        {dockRight ? problemsPanel : null}
        {dockRight ? debugPanel : null}
      </div>
      {collapsedRail}
    </div>
    {dockRight ? null : problemsPanel}
    {dockRight ? null : debugPanel}
    {renderStatusBar()}
    <ContextMenu state={contextMenu.state} onClose={contextMenu.close} />
    <ConfirmDialog
      open={rejectAllOpen}
      title={tx("dev.review.rejectAllTitle", "Reject these edits?")}
      description={t("dev.review.rejectAllConfirm", {
        defaultValue:
          "Undo all {{count}} agent edit(s)? Files go back to their pre-edit state.",
        count: reviewChanges.length,
      })}
      confirmLabel={tx("dev.review.rejectAll", "Reject all")}
      onCancel={() => setRejectAllOpen(false)}
      onConfirm={() => {
        setRejectAllOpen(false);
        void applyReviewAction("reject", null);
      }}
    />
    <AlertDialog
      open={pendingDelete !== null}
      onOpenChange={(open: boolean) => {
        if (!open) setPendingDelete(null);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {tx("dev.deleteTitle", "Delete {{name}}?").replace(
              "{{name}}",
              pendingDelete?.name ?? "",
            )}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {pendingDelete?.type === "dir"
              ? tx(
                  "dev.deleteFolderBody",
                  "This folder and everything inside it will be removed from disk. This cannot be undone.",
                )
              : tx(
                  "dev.deleteFileBody",
                  "This file will be removed from disk. This cannot be undone.",
                )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{tx("dev.cancel", "Cancel")}</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => void confirmDelete()}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {tx("dev.delete", "Delete")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    <Dialog
      open={handoffDraft !== null}
      onOpenChange={(open: boolean) => {
        if (!open) setHandoffDraft(null);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{tx("dev.commands.leaveHandoff", "Leave Handoff Note")}</DialogTitle>
          <DialogDescription>
            {tx(
              "dev.leaveHandoffPrompt",
              "Leave a handoff note for the next session (saved to RESUME.md):",
            )}
          </DialogDescription>
        </DialogHeader>
        <Textarea
          value={handoffDraft ?? ""}
          onChange={(event) => setHandoffDraft(event.target.value)}
          rows={7}
                  autoFocus
          className="font-mono text-[12.5px]"
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => setHandoffDraft(null)}>
            {tx("dev.cancel", "Cancel")}
          </Button>
          <Button
            disabled={!handoffDraft?.trim()}
            onClick={() => {
              const body = (handoffDraft ?? "").trim();
              setHandoffDraft(null);
              if (!body) return;
              void leaveHandoff(token, treeKey, body)
                .then(() => {
                  publishNotification({
                    level: "success",
                    source: "session",
                    title: tx("dev.leaveHandoffSaved", "Handoff saved to RESUME.md"),
                    key: "continuity:handoff",
                  });
                })
                .catch((err) => {
                  publishNotification({
                    level: "error",
                    source: "session",
                    title: tx("dev.leaveHandoffFailed", "Could not save handoff"),
                    detail: err instanceof Error ? err.message : String(err),
                    key: "continuity:handoff:error",
                  });
                });
            }}
          >
            {tx("dev.leaveHandoffSave", "Save note")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </div>
  );
}

