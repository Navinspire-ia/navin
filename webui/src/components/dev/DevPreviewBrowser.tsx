import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Camera,
  Cookie,
  Copy,
  ExternalLink,
  Globe,
  History,
  Loader2,
  MoreHorizontal,
  Plus,
  RefreshCw,
  RotateCw,
  SquareDashedMousePointer,
  Star,
  TerminalSquare,
  Trash2,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { capturePreviewScreenshot } from "@/lib/api";
import { copyTextToClipboard } from "@/lib/clipboard";
import { publishNotification } from "@/lib/notification-bus";
import {
  appendCacheBust,
  clearPreviewRecents,
  displayPreviewHref,
  persistPreviewBookmarkBar,
  type PreviewBookmark,
  readPreviewBookmarkBar,
  readPreviewBookmarks,
  readPreviewRecents,
  rememberPreviewRecent,
  removePreviewBookmark,
  upsertPreviewBookmark,
} from "@/lib/preview-browser-storage";
import { saveBlob } from "@/lib/save-blob";
import type { ProjectFileMatch } from "@/lib/types";
import { cn } from "@/lib/utils";

import { DevDesignModePrompt } from "./DevDesignModePrompt";
import { DevPreviewConsole } from "./DevPreviewConsole";
import { parsePickedElement, type PickedElement } from "./designMode";

type ProbeCommand =
  | "screenshot"
  | "hard-reload"
  | "clear-cookies"
  | "clear-cache"
  | "pick-start"
  | "pick-resume"
  | "pick-stop";

type ProbeResult = { ok: boolean; dataUrl?: string; error?: string };

function dataUrlToBlob(dataUrl: string): Blob {
  const parts = dataUrl.split(",");
  const header = parts[0] || "";
  const data = parts[1] || "";
  const mime = /data:(.*?);/.exec(header)?.[1] || "image/png";
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

function iframeOrigin(src: string): string {
  try {
    return new URL(src, window.location.href).origin;
  } catch {
    return "*";
  }
}

/**
 * Code Preview simple browser: URL bar, back/forward, overflow menu
 * (screenshot, hard reload, recents, bookmarks) matching Cursor's browser.
 */
export function DevPreviewBrowser({
  token,
  url,
  src,
  nonce,
  error,
  starting,
  looksLikeWebApp,
  previewTargetPort,
  showConsole,
  problemCount,
  onUrlChange,
  onOpen,
  onRefresh,
  onOpenExternal,
  onRetry,
  onStartViaChat,
  onSeedChat,
  onRunChat,
  resolveSourceFile,
  onToggleConsole,
  onProblemCount,
  onClose,
}: {
  token: string;
  url: string;
  src: string | null;
  nonce: number;
  error: string | null;
  starting: boolean;
  looksLikeWebApp: boolean;
  previewTargetPort: number | null;
  showConsole: boolean;
  problemCount: number;
  onUrlChange: (value: string) => void;
  onOpen: (url?: string) => void;
  onRefresh: () => void;
  onOpenExternal: () => void;
  onRetry: () => void;
  onStartViaChat?: () => void;
  /** Puts text (and optional file chips) in the composer without sending. */
  onSeedChat?: (text: string, files?: ProjectFileMatch[]) => void;
  /** Sends a message to the agent right away (design mode prompts). */
  onRunChat?: (text: string) => void;
  /** Maps a source file reported by the previewed app onto a project file. */
  resolveSourceFile?: (file: string) => Promise<ProjectFileMatch | null>;
  onToggleConsole: () => void;
  onProblemCount: (count: number) => void;
  onClose?: () => void;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const skipHistoryRef = useRef(false);
  const historyIndexRef = useRef(-1);
  const commandIdRef = useRef(0);
  const pendingRef = useRef(
    new Map<number, { timer: number; resolve: (result: ProbeResult) => void }>(),
  );

  const [recents, setRecents] = useState<string[]>(() => readPreviewRecents());
  const [bookmarks, setBookmarks] = useState<PreviewBookmark[]>(() =>
    readPreviewBookmarks(),
  );
  const [bookmarkBar, setBookmarkBar] = useState(() => readPreviewBookmarkBar());
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [reloadToken, setReloadToken] = useState(0);
  const [busyCmd, setBusyCmd] = useState<ProbeCommand | null>(null);
  const [designMode, setDesignMode] = useState(false);
  const [picked, setPicked] = useState<PickedElement | null>(null);
  const [pickedSource, setPickedSource] = useState<string | null>(null);
  const [frameSize, setFrameSize] = useState({ width: 0, height: 0 });
  const frameBoxRef = useRef<HTMLDivElement | null>(null);
  const designModeRef = useRef(false);
  const pickSourceGenRef = useRef(0);

  const frameSrc = useMemo(
    () => (src ? appendCacheBust(src, reloadToken) : null),
    [src, reloadToken],
  );
  const canGoBack = historyIndex > 0;
  const canGoForward = historyIndex >= 0 && historyIndex < history.length - 1;
  const currentBookmarked = Boolean(
    url.trim() && bookmarks.some((row) => row.url === url.trim() || displayPreviewHref(row.url) === displayPreviewHref(url)),
  );

  useEffect(() => {
    historyIndexRef.current = historyIndex;
  }, [historyIndex]);

  useEffect(() => {
    const href = url.trim();
    if (!href || !src) return;
    setRecents(rememberPreviewRecent(href));
    if (skipHistoryRef.current) {
      skipHistoryRef.current = false;
      return;
    }
    setHistory((prev) => {
      const idx = historyIndexRef.current;
      const clipped = idx >= 0 ? prev.slice(0, idx + 1) : prev;
      if (clipped[clipped.length - 1] === href) return clipped === prev ? prev : clipped;
      const next = [...clipped, href].slice(-50);
      historyIndexRef.current = next.length - 1;
      setHistoryIndex(next.length - 1);
      return next;
    });
  }, [src, url]);

  useEffect(() => {
    setReloadToken(0);
  }, [src]);

  useEffect(() => {
    if (frameSrc) return;
    setDesignMode(false);
    setPicked(null);
  }, [frameSrc]);

  useEffect(() => {
    designModeRef.current = designMode;
  }, [designMode]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const data = event.data as
        | {
            source?: string;
            id?: number;
            ok?: boolean;
            dataUrl?: string;
            error?: string;
            event?: string;
            element?: unknown;
          }
        | null;
      if (!data || data.source !== "navin-preview-result") return;
      // Only the page in our own frame may drive the design mode.
      if (iframeRef.current && event.source !== iframeRef.current.contentWindow) return;
      if (data.event === "pick") {
        if (!designModeRef.current) return;
        const element = parsePickedElement(data.element);
        if (element) setPicked(element);
        return;
      }
      if (data.event === "pick-cancel") {
        setPicked(null);
        setDesignMode(false);
        return;
      }
      const pending = pendingRef.current.get(Number(data.id));
      if (!pending) return;
      window.clearTimeout(pending.timer);
      pendingRef.current.delete(Number(data.id));
      pending.resolve({
        ok: Boolean(data.ok),
        dataUrl: data.dataUrl,
        error: data.error,
      });
    };
    window.addEventListener("message", onMessage);
    return () => {
      window.removeEventListener("message", onMessage);
      for (const pending of pendingRef.current.values()) {
        window.clearTimeout(pending.timer);
      }
      pendingRef.current.clear();
    };
  }, []);

  const sendProbe = useCallback(
    (
      cmd: ProbeCommand,
      timeoutMs = 4000,
      extra?: Record<string, string>,
    ): Promise<ProbeResult> => {
      const frame = iframeRef.current?.contentWindow;
      const currentSrc = frameSrc;
      if (!frame || !currentSrc) {
        return Promise.resolve({ ok: false, error: "no-frame" });
      }
      const id = (commandIdRef.current += 1);
      return new Promise((resolve) => {
        const timer = window.setTimeout(() => {
          pendingRef.current.delete(id);
          resolve({ ok: false, error: "timeout" });
        }, timeoutMs);
        pendingRef.current.set(id, { timer, resolve });
        try {
          frame.postMessage(
            { source: "navin-preview", id, cmd, ...(extra ?? {}) },
            iframeOrigin(currentSrc),
          );
        } catch {
          window.clearTimeout(timer);
          pendingRef.current.delete(id);
          resolve({ ok: false, error: "postMessage" });
        }
      });
    },
    [frameSrc],
  );

  // ---- Design mode ---------------------------------------------------------

  const pickHint = tx("dev.designMode.pageHint", "Click to select \u00b7 Esc to exit");

  const armDesignMode = useCallback(async (): Promise<boolean> => {
    const result = await sendProbe("pick-start", 1500, { hint: pickHint });
    return result.ok;
  }, [pickHint, sendProbe]);

  const stopDesignMode = useCallback(() => {
    setDesignMode(false);
    setPicked(null);
    void sendProbe("pick-stop", 800);
  }, [sendProbe]);

  const toggleDesignMode = useCallback(async () => {
    if (designMode) {
      stopDesignMode();
      return;
    }
    setDesignMode(true);
    const armed = await armDesignMode();
    if (armed) return;
    setDesignMode(false);
    publishNotification({
      level: "error",
      source: "session",
      toast: true,
      title: tx("dev.designMode.unavailable", "Design mode is not available here"),
      detail:
        previewTargetPort == null
          ? tx(
              "dev.designMode.unavailableExternal",
              "It works on local previews served through the Navin preview proxy.",
            )
          : tx(
              "dev.designMode.notReady",
              "The preview page did not answer. Reload it and try again.",
            ),
    });
  }, [armDesignMode, designMode, previewTargetPort, stopDesignMode, tx]);

  // A full page load (navigation, HMR reload) drops the in-page state: arm it
  // again so the outline comes back without touching the toggle.
  const onFrameLoad = useCallback(() => {
    if (!designModeRef.current) return;
    setPicked(null);
    void armDesignMode();
  }, [armDesignMode]);

  // Escape or the close button: keep design mode on, drop the selection.
  const dismissPick = useCallback(() => {
    setPicked(null);
    void sendProbe("pick-resume", 800);
  }, [sendProbe]);

  useEffect(() => {
    if (!picked?.source?.file || !resolveSourceFile) {
      setPickedSource(null);
      return;
    }
    const gen = (pickSourceGenRef.current += 1);
    setPickedSource(null);
    void resolveSourceFile(picked.source.file)
      .then((match) => {
        if (pickSourceGenRef.current === gen) setPickedSource(match?.path ?? null);
      })
      .catch(() => {
        if (pickSourceGenRef.current === gen) setPickedSource(null);
      });
  }, [picked, resolveSourceFile]);

  useEffect(() => {
    const node = frameBoxRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const rect = node.getBoundingClientRect();
      setFrameSize((prev) =>
        Math.abs(prev.width - rect.width) < 1 && Math.abs(prev.height - rect.height) < 1
          ? prev
          : { width: rect.width, height: rect.height },
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [frameSrc]);

  const sendDesignPrompt = useCallback(
    (text: string) => {
      if (onRunChat) onRunChat(text);
      else onSeedChat?.(text);
      void sendProbe("pick-resume", 800);
    },
    [onRunChat, onSeedChat, sendProbe],
  );

  const addDesignPromptToChat = useMemo(
    () =>
      onSeedChat
        ? (text: string, sourcePath: string | null) => {
            const files: ProjectFileMatch[] | undefined = sourcePath
              ? [{ path: sourcePath, name: sourcePath.split("/").pop() ?? sourcePath, kind: "file" }]
              : undefined;
            onSeedChat(text, files);
            void sendProbe("pick-resume", 800);
          }
        : undefined,
    [onSeedChat, sendProbe],
  );

  const hardReload = useCallback(async () => {
    setBusyCmd("hard-reload");
    try {
      const result = await sendProbe("hard-reload", 800);
      if (!result.ok) setReloadToken(Date.now());
    } finally {
      setBusyCmd(null);
    }
  }, [sendProbe]);

  const takeScreenshot = useCallback(async () => {
    setBusyCmd("screenshot");
    try {
      // The URL actually shown in the iframe (loopback proxy / gateway preview)
      // is what we capture; fall back to the address-bar URL if there is none.
      const target = (src || url || "").trim();
      let dataUrl: string | null = null;
      if (target) {
        try {
          const shot = await capturePreviewScreenshot(token, target);
          dataUrl = shot.dataUrl || null;
        } catch {
          dataUrl = null;
        }
      }
      // Fallback: in-page rasterizer (only works for same-origin, asset-free pages).
      if (!dataUrl) {
        const result = await sendProbe("screenshot", 6000);
        if (result.ok && result.dataUrl) dataUrl = result.dataUrl;
      }
      if (!dataUrl) {
        publishNotification({
          level: "error",
          source: "session",
          toast: true,
          title: tx("dev.browserMenu.screenshotFailed", "Could not capture the preview"),
          detail: tx(
            "dev.browserMenu.screenshotFailedDetail",
            "No Chromium is available, or the page could not be reached.",
          ),
        });
        return;
      }
      const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      await saveBlob(dataUrlToBlob(dataUrl), `preview-${stamp}.png`);
    } finally {
      setBusyCmd(null);
    }
  }, [sendProbe, src, token, tx, url]);

  const copyCurrentUrl = useCallback(async () => {
    const href = url.trim();
    if (!href) return;
    const copied = await copyTextToClipboard(href);
    publishNotification({
      level: copied ? "success" : "error",
      source: "session",
      toast: true,
      title: copied
        ? tx("dev.browserMenu.urlCopied", "URL copied")
        : tx("dev.browserMenu.urlCopyFailed", "Could not copy the URL"),
    });
  }, [tx, url]);

  const clearHistory = useCallback(() => {
    clearPreviewRecents();
    setRecents([]);
    setHistory([]);
    setHistoryIndex(-1);
  }, []);

  const clearCookies = useCallback(async () => {
    setBusyCmd("clear-cookies");
    try {
      await sendProbe("clear-cookies");
      await hardReload();
    } finally {
      setBusyCmd(null);
    }
  }, [hardReload, sendProbe]);

  const clearCache = useCallback(async () => {
    setBusyCmd("clear-cache");
    try {
      await sendProbe("clear-cache");
      await hardReload();
    } finally {
      setBusyCmd(null);
    }
  }, [hardReload, sendProbe]);

  const toggleBookmarkBar = useCallback((on: boolean) => {
    setBookmarkBar(on);
    persistPreviewBookmarkBar(on);
  }, []);

  const toggleCurrentBookmark = useCallback(() => {
    const href = url.trim();
    if (!href) return;
    if (currentBookmarked) setBookmarks(removePreviewBookmark(href));
    else setBookmarks(upsertPreviewBookmark(href));
  }, [currentBookmarked, url]);

  const goHistory = useCallback(
    (delta: number) => {
      const next = historyIndex + delta;
      const target = history[next];
      if (!target) return;
      skipHistoryRef.current = true;
      setHistoryIndex(next);
      onUrlChange(target);
      onOpen(target);
    },
    [history, historyIndex, onOpen, onUrlChange],
  );

  const openFromList = useCallback(
    (href: string) => {
      onUrlChange(href);
      onOpen(href);
    },
    [onOpen, onUrlChange],
  );

  const hasFrame = Boolean(frameSrc);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-col gap-1.5 border-b border-border/50 px-3 py-2">
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-9 w-9 shrink-0 rounded-lg p-0"
            disabled={!canGoBack}
            onClick={() => goHistory(-1)}
            title={tx("dev.browserBack", "Back")}
            aria-label={tx("dev.browserBack", "Back")}
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-9 w-9 shrink-0 rounded-lg p-0"
            disabled={!canGoForward}
            onClick={() => goHistory(1)}
            title={tx("dev.browserForward", "Forward")}
            aria-label={tx("dev.browserForward", "Forward")}
          >
            <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </Button>
          <Input
            value={url}
            onChange={(event) => onUrlChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") onOpen(url || undefined);
            }}
            placeholder={tx(
              "dev.browserUrlPlaceholder",
              "App URL (detected automatically)",
            )}
            className="h-9 rounded-lg font-mono text-[12px]"
            aria-label={tx("dev.browserUrlAria", "Preview URL")}
          />
          <Button
            type="button"
            size="sm"
            className="h-9 rounded-lg"
            onClick={() => onOpen(url || undefined)}
          >
            {tx("dev.browserGo", "Open")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-9 shrink-0 rounded-lg px-2.5"
            onClick={() => onRefresh()}
            title={tx("dev.browserRefresh", "Refresh")}
            aria-label={tx("dev.browserRefresh", "Refresh")}
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-9 shrink-0 rounded-lg px-2.5"
            onClick={() => onOpenExternal()}
            disabled={!hasFrame && !url.trim()}
            title={tx("dev.browserOpenExternal", "Open in browser")}
            aria-label={tx("dev.browserOpenExternal", "Open in browser")}
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          </Button>
          <Button
            type="button"
            size="sm"
            variant={designMode ? "default" : "outline"}
            className={cn(
              "h-9 shrink-0 rounded-lg px-2.5",
              designMode && "bg-blue-600 text-white hover:bg-blue-600/90",
            )}
            disabled={!hasFrame || previewTargetPort == null}
            onClick={() => void toggleDesignMode()}
            aria-pressed={designMode}
            title={
              previewTargetPort == null
                ? tx(
                    "dev.designMode.unavailableExternal",
                    "It works on local previews served through the Navin preview proxy.",
                  )
                : tx(
                    "dev.designMode.toggleHint",
                    "Design mode: hover an element in the preview, click it, then tell the agent what to change.",
                  )
            }
            aria-label={tx("dev.designMode.toggle", "Design mode")}
            data-testid="design-mode-toggle"
          >
            <SquareDashedMousePointer className="h-3.5 w-3.5" aria-hidden />
          </Button>
          {previewTargetPort != null ? (
            <Button
              type="button"
              size="sm"
              variant={showConsole ? "secondary" : "outline"}
              className="relative h-9 shrink-0 rounded-lg px-2.5"
              onClick={onToggleConsole}
              title={tx("dev.previewConsole.toggle", "Preview console")}
              aria-label={tx("dev.previewConsole.toggle", "Preview console")}
            >
              <TerminalSquare className="h-3.5 w-3.5" aria-hidden />
              {problemCount > 0 ? (
                <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-semibold leading-none text-white">
                  {problemCount > 99 ? "99+" : problemCount}
                </span>
              ) : null}
            </Button>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-9 w-9 shrink-0 rounded-lg p-0"
                title={tx("dev.browserMenu.more", "More")}
                aria-label={tx("dev.browserMenu.more", "More")}
              >
                <MoreHorizontal className="h-4 w-4" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              className="w-64 border-border bg-popover shadow-lg backdrop-blur-none dark:border-border"
            >
              <DropdownMenuItem
                disabled={(!hasFrame && !url.trim()) || busyCmd === "screenshot"}
                onSelect={() => void takeScreenshot()}
              >
                <Camera className="h-3.5 w-3.5" aria-hidden />
                {tx("dev.browserMenu.screenshot", "Take Screenshot")}
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={!hasFrame || busyCmd === "hard-reload"}
                onSelect={() => void hardReload()}
              >
                <RotateCw className="h-3.5 w-3.5" aria-hidden />
                {tx("dev.browserMenu.hardReload", "Hard Reload")}
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!url.trim()} onSelect={() => void copyCurrentUrl()}>
                <Copy className="h-3.5 w-3.5" aria-hidden />
                {tx("dev.browserMenu.copyUrl", "Copy Current URL")}
              </DropdownMenuItem>
              <DropdownMenuCheckboxItem
                checked={bookmarkBar}
                onCheckedChange={(checked) => toggleBookmarkBar(Boolean(checked))}
              >
                {tx("dev.browserMenu.bookmarkBar", "Show Bookmark Bar")}
              </DropdownMenuCheckboxItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={clearHistory}>
                <History className="h-3.5 w-3.5" aria-hidden />
                {tx("dev.browserMenu.clearHistory", "Clear Browsing History")}
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={!hasFrame || busyCmd === "clear-cookies"}
                onSelect={() => void clearCookies()}
              >
                <Cookie className="h-3.5 w-3.5" aria-hidden />
                {tx("dev.browserMenu.clearCookies", "Clear Cookies")}
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={!hasFrame || busyCmd === "clear-cache"}
                onSelect={() => void clearCache()}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
                {tx("dev.browserMenu.clearCache", "Clear Cache")}
              </DropdownMenuItem>
              {recents.length ? (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuLabel>
                    {tx("dev.browserMenu.recents", "Recents")}
                  </DropdownMenuLabel>
                  {recents.map((href) => (
                    <DropdownMenuItem
                      key={href}
                      className="font-mono text-[12px] text-muted-foreground"
                      onSelect={() => openFromList(href)}
                    >
                      <Globe className="h-3.5 w-3.5 shrink-0 opacity-70" aria-hidden />
                      <span className="truncate">{displayPreviewHref(href)}</span>
                    </DropdownMenuItem>
                  ))}
                </>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
          {onClose ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-9 w-9 shrink-0 rounded-lg p-0 text-muted-foreground hover:text-foreground"
              onClick={onClose}
              title={tx("dev.browserClose", "Close preview")}
              aria-label={tx("dev.browserClose", "Close preview")}
            >
              <X className="h-4 w-4" aria-hidden />
            </Button>
          ) : null}
        </div>
        {bookmarkBar ? (
          <div className="flex min-h-8 items-center gap-1 overflow-x-auto">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 w-7 shrink-0 rounded-md p-0"
              disabled={!url.trim()}
              onClick={toggleCurrentBookmark}
              title={
                currentBookmarked
                  ? tx("dev.browserMenu.removeBookmark", "Remove bookmark")
                  : tx("dev.browserMenu.addBookmark", "Add bookmark")
              }
              aria-label={
                currentBookmarked
                  ? tx("dev.browserMenu.removeBookmark", "Remove bookmark")
                  : tx("dev.browserMenu.addBookmark", "Add bookmark")
              }
            >
              <Star
                className={cn(
                  "h-3.5 w-3.5",
                  currentBookmarked && "fill-amber-400 text-amber-400",
                )}
                aria-hidden
              />
            </Button>
            {bookmarks.length ? (
              bookmarks.map((row) => (
                <span key={row.url} className="group flex shrink-0 items-center">
                  <button
                    type="button"
                    className="max-w-[10rem] truncate rounded-md px-2 py-1 text-[11.5px] text-muted-foreground hover:bg-muted hover:text-foreground"
                    onClick={() => openFromList(row.url)}
                    title={row.url}
                  >
                    {row.title}
                  </button>
                  <button
                    type="button"
                    className="hidden h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground group-hover:inline-flex"
                    onClick={() => setBookmarks(removePreviewBookmark(row.url))}
                    aria-label={tx("dev.browserMenu.removeBookmark", "Remove bookmark")}
                  >
                    <X className="h-3 w-3" aria-hidden />
                  </button>
                </span>
              ))
            ) : (
              <span className="px-1 text-[11px] text-muted-foreground">
                {tx("dev.browserMenu.noBookmarks", "No bookmarks yet")}
              </span>
            )}
            {url.trim() && !currentBookmarked ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 shrink-0 gap-1 rounded-md px-2 text-[11px]"
                onClick={toggleCurrentBookmark}
              >
                <Plus className="h-3 w-3" aria-hidden />
                {tx("dev.browserMenu.addBookmark", "Add bookmark")}
              </Button>
            ) : null}
          </div>
        ) : null}
        {error ? (
          <p className="text-[11px] leading-4 text-destructive" title={error}>
            {error}
          </p>
        ) : null}
      </div>
      {frameSrc ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div
            ref={frameBoxRef}
            className={cn(
              "relative flex min-h-0 flex-1 flex-col",
              designMode && "ring-2 ring-inset ring-blue-500/70",
            )}
            data-design-mode={designMode ? "on" : undefined}
          >
            <iframe
              ref={iframeRef}
              key={`${nonce}-${reloadToken}`}
              src={frameSrc}
              title={tx("dev.browserTab", "Browser")}
              className="min-h-0 flex-1 border-0 bg-white"
              sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox"
              onLoad={onFrameLoad}
            />
            {designMode && !picked ? (
              <div
                className="pointer-events-none absolute left-1/2 top-2 z-10 -translate-x-1/2 rounded-full bg-blue-600 px-2.5 py-1 text-[11px] font-medium text-white shadow-md"
                data-testid="design-mode-banner"
              >
                {tx(
                  "dev.designMode.banner",
                  "Design mode: hover, then click the element to change",
                )}
              </div>
            ) : null}
            <DevDesignModePrompt
              element={designMode ? picked : null}
              sourcePath={pickedSource}
              frame={frameSize}
              canSend={Boolean(onRunChat)}
              onSend={sendDesignPrompt}
              onAddToChat={addDesignPromptToChat}
              onClose={dismissPick}
            />
          </div>
          {previewTargetPort != null ? (
            <div className={showConsole ? "h-56 shrink-0" : "hidden"}>
              <DevPreviewConsole
                token={token}
                port={previewTargetPort}
                onSeed={onSeedChat}
                onProblemCount={onProblemCount}
                onClose={() => {
                  if (showConsole) onToggleConsole();
                }}
              />
            </div>
          ) : null}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-auto px-6 py-8 text-muted-foreground">
          {!error && recents.length > 0 ? (
            <div className="mx-auto w-full max-w-md">
              <p className="mb-3 text-[12px] font-medium tracking-wide text-muted-foreground/80">
                {tx("dev.browserMenu.recents", "Recents")}
              </p>
              <ul className="space-y-0.5">
                {recents.map((href) => (
                  <li key={href}>
                    <button
                      type="button"
                      className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left font-mono text-[12.5px] text-foreground/85 hover:bg-muted/70"
                      onClick={() => openFromList(href)}
                      title={href}
                    >
                      <Globe className="h-3.5 w-3.5 shrink-0 opacity-50" aria-hidden />
                      <span className="truncate">{displayPreviewHref(href)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
              {starting || error ? (
                <Loader2 className="h-8 w-8 animate-spin opacity-50" aria-hidden />
              ) : (
                <Globe className="h-8 w-8 opacity-40" aria-hidden />
              )}
              <p className="max-w-sm text-[13px] leading-6">
                {error
                  ? error
                  : looksLikeWebApp
                    ? tx(
                        "dev.browserEmpty",
                        "No web app is running. Paste its URL, or ask the chat to start it.",
                      )
                    : tx(
                        "dev.browserEmptyNoWeb",
                        "Nothing to show here yet. This project does not look like it has a web UI to preview.",
                      )}
              </p>
              <div className="flex flex-wrap items-center justify-center gap-2">
                <button
                  type="button"
                  className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-[12px] font-medium text-foreground hover:bg-muted/60"
                  onClick={onRetry}
                >
                  {tx("dev.browserRetry", "Search for an app")}
                </button>
                {onStartViaChat && looksLikeWebApp ? (
                  <button
                    type="button"
                    className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-[12px] font-medium text-foreground hover:bg-muted/60"
                    onClick={onStartViaChat}
                  >
                    {tx("dev.browserStartViaChat", "Start via chat")}
                  </button>
                ) : null}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
