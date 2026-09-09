// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  ArrowLeft,
  Circle,
  CornerDownLeft,
  Eraser,
  Home,
  Loader2,
  MessageSquarePlus,
  PanelRightClose,
  PanelRightOpen,
  Play,
  Power,
  RefreshCw,
  Send,
  Smartphone,
  Square,
  Volume1,
  Volume2,
  Wand2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { NavinClient } from "@/lib/navin-client";
import type { MobilePreviewStatus } from "@/lib/types";

type Metrics = {
  fps: number;
  memMb: number;
  cpuPct: number;
  width: number;
  height: number;
  seq: number;
};

type Point = { x: number; y: number };

const FPS_OPTIONS = [2, 4, 8, 12] as const;

function previewIdFor(chatId?: string | null): string {
  return `mobile-${chatId || "default"}`;
}

function serialFromDeviceLine(line: string): string {
  return line.trim().split(/\s+/)[0] || "";
}

function labelForDeviceSerial(serial: string): string {
  if (serial.startsWith("ios-sim:")) {
    const id = serial.slice("ios-sim:".length);
    return `iOS Simulator (${id.slice(0, 8)}...)`;
  }
  if (serial.startsWith("ios-usb:")) {
    const id = serial.slice("ios-usb:".length);
    return `iPhone USB (${id.slice(0, 8)}...)`;
  }
  if (serial.startsWith("emulator-")) return `Android Emulator (${serial})`;
  return serial;
}

/** Emulator closed / USB unplugged mid-preview (adb target gone). */
function isDeviceGoneError(detail: string): boolean {
  const t = detail.toLowerCase();
  if (t.includes("device disconnected")) return true;
  if (t.includes("device offline") || t.includes("device unauthorized")) return true;
  if (t.includes("no devices/emulators found") || t.includes("no android device")) {
    return true;
  }
  if (t.includes("not found") && (t.includes("emulator-") || t.includes("device '") || t.includes("screencap"))) {
    return true;
  }
  return false;
}

function readStoredFps(): number {
  try {
    const raw = localStorage.getItem("navin.mobile.fps");
    const n = raw ? Number(raw) : 4;
    if (FPS_OPTIONS.includes(n as (typeof FPS_OPTIONS)[number])) return n;
  } catch {
    /* ignore */
  }
  return 4;
}

function readShowLogs(): boolean {
  try {
    const raw = localStorage.getItem("navin.mobile.showLogs");
    // Default: logs hidden. Only show when explicitly set to "1".
    if (raw == null) return false;
    return raw === "1";
  } catch {
    return false;
  }
}

export function DevMobilePreview({
  client,
  chatId,
  onSeedChat,
  autoStart = false,
  className,
}: {
  client: NavinClient | null;
  chatId?: string | null;
  /** Puts a guided prompt in the chat composer without auto-sending. */
  onSeedChat?: (text: string) => void;
  /** When true, start the mirror once status.ready (agent or UI request). */
  autoStart?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const previewId = useMemo(() => previewIdFor(chatId), [chatId]);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const dragStart = useRef<Point | null>(null);
  const autoStartedRef = useRef(false);
  const autoRefreshTriedRef = useRef(false);

  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [statusBusy, setStatusBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backend, setBackend] = useState<string | null>(null);
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [showLogs, setShowLogs] = useState(readShowLogs);
  const [targetFps, setTargetFps] = useState(readStoredFps);
  const [selectedSerial, setSelectedSerial] = useState<string>("");
  const [textDraft, setTextDraft] = useState("");
  const [status, setStatus] = useState<MobilePreviewStatus | null>(null);
  const [selectedAvd, setSelectedAvd] = useState<string>("");
  const [avdBooting, setAvdBooting] = useState(false);
  const autoInstallTriedRef = useRef(false);
  const [metrics, setMetrics] = useState<Metrics>({
    fps: 0,
    memMb: 0,
    cpuPct: 0,
    width: 0,
    height: 0,
    seq: 0,
  });

  const deviceOptions = useMemo(() => {
    const lines = status?.devices ?? [];
    return lines
      .map((line) => {
        const serial = serialFromDeviceLine(line);
        return serial ? { serial, label: labelForDeviceSerial(serial) } : null;
      })
      .filter((row): row is { serial: string; label: string } => row != null);
  }, [status?.devices]);

  useEffect(() => {
    if (!deviceOptions.length) {
      setSelectedSerial("");
      return;
    }
    if (!selectedSerial || !deviceOptions.some((d) => d.serial === selectedSerial)) {
      setSelectedSerial(deviceOptions[0].serial);
    }
  }, [deviceOptions, selectedSerial]);

  const avdOptions = status?.avds ?? [];
  useEffect(() => {
    if (!avdOptions.length) {
      setSelectedAvd("");
      return;
    }
    if (!selectedAvd || !avdOptions.includes(selectedAvd)) {
      setSelectedAvd(avdOptions[0]);
    }
  }, [avdOptions, selectedAvd]);

  // The emulator finished booting: stop showing the "starting…" state.
  useEffect(() => {
    if (status?.ready) setAvdBooting(false);
  }, [status?.ready]);

  const refreshStatus = useCallback(
    async (opts?: { install?: boolean }) => {
      if (!client) return;
      setStatusBusy(true);
      try {
        const next = await client.requestMobilePreviewStatus({
          install: opts?.install,
          // Readiness may probe alternate adb binaries (Windows adb under
          // WSL), which can take a few extra seconds on the first scan.
          timeoutMs: opts?.install ? 120_000 : 30_000,
        });
        setStatus(next);
        if (next.install_attempt?.detail && !next.adb) {
          setError(next.install_attempt.detail);
        }
      } catch (err) {
        setStatus({
          ready: false,
          devices: [],
          avds: [],
          error: "status_failed",
          help: err instanceof Error ? err.message : String(err),
          fixes: [],
        });
      } finally {
        setStatusBusy(false);
      }
    },
    [client],
  );

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  // Poll while no device is ready so a phone plugged in (or an emulator
  // booting) shows up without the user hammering Refresh.
  const statusBusyRef = useRef(false);
  statusBusyRef.current = statusBusy;
  useEffect(() => {
    if (!client || running || status?.ready) return;
    const timer = window.setInterval(() => {
      if (!statusBusyRef.current) void refreshStatus();
    }, 8_000);
    return () => window.clearInterval(timer);
  }, [client, refreshStatus, running, status?.ready]);

  // Quietly install adb once if missing - no wizard UI.
  useEffect(() => {
    if (!client || !status || autoInstallTriedRef.current) return;
    if (status.error === "adb_missing" && status.can_auto_install !== false) {
      autoInstallTriedRef.current = true;
      void refreshStatus({ install: true });
    }
  }, [client, refreshStatus, status]);

  const startAvd = useCallback(
    (name: string) => {
      if (!client || !name) return;
      setError(null);
      setAvdBooting(true);
      client
        .startMobileAvd(name)
        .then((result) => {
          if (!result.ok) {
            setAvdBooting(false);
            setError(result.detail || tx("dev.mobile.avdStartFailed", "Could not start the emulator."));
            return;
          }
          // Booting: the status poll flips ready when adb sees the device.
          void refreshStatus();
        })
        .catch(() => {
          setAvdBooting(false);
          setError(tx("dev.mobile.avdStartFailed", "Could not start the emulator."));
        });
    },
    [client, refreshStatus, tx],
  );

  const openPreview = useCallback(() => {
    if (!client) return;
    setBusy(true);
    setError(null);
    setLogs([]);
    client.openMobilePreview({
      previewId,
      fps: targetFps,
      ...(selectedSerial ? { serial: selectedSerial } : {}),
    });
  }, [client, previewId, selectedSerial, targetFps]);

  useEffect(() => {
    if (!autoStart) {
      autoStartedRef.current = false;
      autoRefreshTriedRef.current = false;
      return;
    }
    if (!client || running || busy || autoStartedRef.current) return;
    if (status?.ready) {
      autoStartedRef.current = true;
      openPreview();
      return;
    }
    // Not ready yet - refresh once so auto-start can fire when the device appears.
    if (!autoRefreshTriedRef.current && (status == null || !status.ready)) {
      autoRefreshTriedRef.current = true;
      void refreshStatus();
    }
  }, [autoStart, busy, client, openPreview, refreshStatus, running, status]);

  useEffect(() => {
    if (!client) return;
    return client.onMobilePreview(previewId, (ev) => {
      if (ev.event === "mobile_preview_opened") {
        setRunning(true);
        setBusy(false);
        setError(null);
        setBackend(ev.backend ?? null);
        void refreshStatus();
      } else if (ev.event === "mobile_preview_frame") {
        if (ev.png_b64) {
          setFrameUrl(`data:image/png;base64,${ev.png_b64}`);
        }
        setMetrics({
          fps: ev.fps ?? 0,
          memMb: ev.mem_mb ?? 0,
          cpuPct: ev.cpu_pct ?? 0,
          width: ev.width ?? 0,
          height: ev.height ?? 0,
          seq: ev.seq ?? 0,
        });
        if (ev.error) setError(ev.error);
      } else if (ev.event === "mobile_preview_logs") {
        const lines = ev.lines ?? [];
        if (lines.length) {
          setLogs((prev) => [...prev, ...lines].slice(-300));
        }
      } else if (ev.event === "mobile_preview_error") {
        const detail = ev.detail || tx("dev.mobile.error", "Preview error");
        const gone = isDeviceGoneError(detail);
        setError(
          gone
            ? tx(
                "dev.mobile.deviceGone",
                "Device disconnected (emulator closed or phone unplugged). Start the emulator again, then Start preview.",
              )
            : detail,
        );
        setBusy(false);
        if (gone) {
          setRunning(false);
          setFrameUrl(null);
          setBackend(null);
          try {
            client.closeMobilePreview(previewId);
          } catch {
            /* ignore */
          }
        }
        void refreshStatus();
      } else if (ev.event === "mobile_preview_exit") {
        setRunning(false);
        setBusy(false);
        setFrameUrl(null);
        setBackend(null);
        void refreshStatus();
      }
    });
  }, [client, previewId, refreshStatus, tx]);

  useEffect(() => {
    return () => {
      if (client && running) {
        client.closeMobilePreview(previewId);
      }
    };
    // Only cleanup on unmount / previewId change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, previewId]);

  const start = () => openPreview();

  const stop = () => {
    if (!client) return;
    client.closeMobilePreview(previewId);
    setRunning(false);
    setBusy(false);
  };

  const prepareMobile = () => {
    if (!client) return;
    if (!status?.adb) {
      void refreshStatus({ install: true });
      return;
    }
    if (status.ready) {
      // Already set up - do not start preview from Prepare (use Start preview).
      return;
    }
    if (onSeedChat) {
      // The backend computes a prompt for THIS host OS (WSL / Windows /
      // macOS / Linux) - never seed a mixed multi-OS checklist.
      const fixPrompt =
        status.fixes?.find((f) => f.id === "ask_agent_stack")?.prompt ??
        status.fixes?.find((f) => f.id === "ask_agent_device")?.prompt ??
        status.fixes?.find((f) => Boolean(f.prompt))?.prompt;
      onSeedChat(
        fixPrompt ??
          tx(
            "dev.mobile.prepareAgentPrompt",
            "Prepare Mobile in Agent mode: run mobile(action=doctor), then mobile(action=bootstrap) if needed, then mobile(action=devices), and tell me when to Start preview. Never -accel off. Short replies.",
          ),
      );
    } else {
      setError(
        tx(
          "dev.mobile.hintDevice",
          "Connection tool is ready. Start an Android emulator or plug in a phone, then Refresh.",
        ),
      );
    }
  };

  const sendKey = (keycode: string) => {
    if (!client) {
      setError(tx("dev.mobile.noClient", "Not connected to the gateway."));
      return;
    }
    if (!running) {
      setError(
        tx(
          "dev.mobile.keysNeedPreview",
          "Hardware keys need an active preview. Click Start preview first.",
        ),
      );
      return;
    }
    setError(null);
    client.mobilePreviewKey(previewId, keycode);
  };

  const sendText = (event?: FormEvent) => {
    event?.preventDefault();
    const text = textDraft.trim();
    if (!text || !client || !running) return;
    client.mobilePreviewText(previewId, text);
    setTextDraft("");
  };

  const persistShowLogs = (next: boolean) => {
    setShowLogs(next);
    try {
      localStorage.setItem("navin.mobile.showLogs", next ? "1" : "0");
    } catch {
      /* ignore */
    }
  };

  const persistFps = (next: number) => {
    setTargetFps(next);
    try {
      localStorage.setItem("navin.mobile.fps", String(next));
    } catch {
      /* ignore */
    }
  };

  const pendingDevices = status?.pending_devices ?? [];

  const iosSupported = Boolean(status?.ios?.supported ?? status?.stacks?.includes("ios"));
  const emptyHint = avdBooting
    ? tx(
        "dev.mobile.hintAvdBooting",
        "Emulator booting… it comes online in about 30-60 seconds, then the preview unlocks.",
      )
    : !status
    ? tx("dev.mobile.checking", "Checking Mobile environment…")
    : !status.adb && !status.ios?.ready
      ? tx(
          "dev.mobile.hintInstall",
          "Preparing the connection tools for this OS in the background…",
        )
      : status.error === "device_unauthorized"
        ? tx(
            "dev.mobile.hintUnauthorized",
            "Phone detected. Unlock it and accept the 'Allow USB debugging' prompt - the preview unlocks automatically.",
          )
        : !status.ready
          ? iosSupported
            ? tx(
                "dev.mobile.hintDeviceMac",
                "Tools ready. Start an Android emulator / USB phone, or boot an iOS Simulator / plug an iPhone - Navin re-scans automatically.",
              )
            : tx(
                "dev.mobile.hintDevice",
                "Connection tool is ready. Start an Android emulator or plug in a phone with USB debugging - Navin re-scans automatically.",
              )
          : tx(
              "dev.mobile.hintReady",
              "A device is online. Click Start preview to mirror the screen.",
            );

  const devicePoint = (event: ReactPointerEvent<HTMLImageElement>): Point | null => {
    const img = imgRef.current;
    if (!img || !metrics.width || !metrics.height) return null;
    const rect = img.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    const relX = (event.clientX - rect.left) / rect.width;
    const relY = (event.clientY - rect.top) / rect.height;
    return {
      x: Math.round(Math.min(1, Math.max(0, relX)) * metrics.width),
      y: Math.round(Math.min(1, Math.max(0, relY)) * metrics.height),
    };
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLImageElement>) => {
    if (!running || !client) return;
    const point = devicePoint(event);
    if (!point) return;
    dragStart.current = point;
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerUp = (event: ReactPointerEvent<HTMLImageElement>) => {
    if (!running || !client) return;
    const startPt = dragStart.current;
    dragStart.current = null;
    const end = devicePoint(event);
    if (!startPt || !end) return;
    const dx = end.x - startPt.x;
    const dy = end.y - startPt.y;
    if (Math.hypot(dx, dy) < 12) {
      client.mobilePreviewTap(previewId, startPt.x, startPt.y);
    } else {
      client.mobilePreviewSwipe(previewId, startPt.x, startPt.y, end.x, end.y, 280);
    }
  };

  const selectClass =
    "h-7 rounded-md border border-border/50 bg-background/80 px-2 text-[11px] text-foreground outline-none disabled:opacity-50";

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col bg-muted/20", className)}>
      <div className="flex flex-nowrap items-center gap-1.5 overflow-x-auto border-b border-border/50 px-3 py-1.5">
        {!running ? (
          <Button
            type="button"
            size="sm"
            className="h-7 shrink-0 rounded-md px-2 text-[11px]"
            disabled={!client || busy || (status != null && !status.ready)}
            onClick={start}
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Smartphone className="h-3.5 w-3.5" aria-hidden />
            )}
            {tx("dev.mobile.start", "Start preview")}
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 shrink-0 rounded-md px-2 text-[11px]"
            onClick={stop}
          >
            <Square className="h-3.5 w-3.5" aria-hidden />
            {tx("dev.mobile.stop", "Stop")}
          </Button>
        )}

        {!running && status?.adb && !status.ready && avdOptions.length ? (
          <div className="flex shrink-0 items-center gap-1">
            {avdOptions.length > 1 ? (
              <select
                className={cn(selectClass, "h-7 max-w-[9rem]")}
                value={selectedAvd}
                disabled={avdBooting}
                onChange={(e) => setSelectedAvd(e.target.value)}
                title={tx("dev.mobile.avdTitle", "Android Virtual Device to launch")}
              >
                {avdOptions.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            ) : null}
            <Button
              type="button"
              size="sm"
              className="h-7 shrink-0 rounded-md px-2 text-[11px]"
              disabled={!client || avdBooting || !selectedAvd}
              onClick={() => startAvd(selectedAvd)}
              title={tx(
                "dev.mobile.startAvdTitle",
                "Launch this emulator - the preview unlocks when it is online",
              )}
            >
              {avdBooting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Play className="h-3.5 w-3.5" aria-hidden />
              )}
              {avdBooting
                ? tx("dev.mobile.avdBooting", "Emulator booting…")
                : tx("dev.mobile.startAvd", "Start emulator")}
            </Button>
          </div>
        ) : null}

        {status?.ready ? (
          <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className="sr-only">{tx("dev.mobile.device", "Device")}</span>
            <select
              className={cn(selectClass, "max-w-[11rem]")}
              value={selectedSerial}
              disabled={!deviceOptions.length || running}
              onChange={(e) => setSelectedSerial(e.target.value)}
              title={tx("dev.mobile.deviceTitle", "ADB device / emulator to mirror")}
            >
              {!deviceOptions.length ? (
                <option value="">
                  {tx("dev.mobile.noDeviceOption", "No device")}
                </option>
              ) : (
                deviceOptions.map((d) => (
                  <option key={d.serial} value={d.serial}>
                    {d.label}
                  </option>
                ))
              )}
            </select>
          </label>
        ) : null}

        {status?.ready ? (
          <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>{tx("dev.mobile.fpsLabel", "FPS")}</span>
            <select
              className={selectClass}
              value={targetFps}
              disabled={running}
              onChange={(e) => persistFps(Number(e.target.value))}
              title={tx(
                "dev.mobile.fpsTitle",
                "Mirror refresh rate (apply on next Start preview)",
              )}
            >
              {FPS_OPTIONS.map((fps) => (
                <option key={fps} value={fps}>
                  {fps}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {running ? (
          <div
            className="flex shrink-0 flex-nowrap items-center gap-0.5 rounded-md border border-border/50 bg-background/70 px-0.5"
            title={tx("dev.mobile.keysHint", "Send Android keyevents to the live device")}
          >
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-8 w-8 rounded-lg p-0"
              disabled={!client}
              onClick={() => sendKey("BACK")}
              title={tx("dev.mobile.back", "Back")}
              aria-label={tx("dev.mobile.back", "Back")}
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-8 w-8 rounded-lg p-0"
              disabled={!client}
              onClick={() => sendKey("HOME")}
              title={tx("dev.mobile.home", "Home")}
              aria-label={tx("dev.mobile.home", "Home")}
            >
              <Home className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-8 w-8 rounded-lg p-0"
              disabled={!client}
              onClick={() => sendKey("APP_SWITCH")}
              title={tx("dev.mobile.recents", "Recents")}
              aria-label={tx("dev.mobile.recents", "Recents")}
            >
              <Circle className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-8 w-8 rounded-lg p-0"
              disabled={!client}
              onClick={() => sendKey("VOLUME_DOWN")}
              title={tx("dev.mobile.volumeDown", "Volume down")}
              aria-label={tx("dev.mobile.volumeDown", "Volume down")}
            >
              <Volume1 className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-8 w-8 rounded-lg p-0"
              disabled={!client}
              onClick={() => sendKey("VOLUME_UP")}
              title={tx("dev.mobile.volumeUp", "Volume up")}
              aria-label={tx("dev.mobile.volumeUp", "Volume up")}
            >
              <Volume2 className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-8 w-8 rounded-lg p-0"
              disabled={!client}
              onClick={() => sendKey("POWER")}
              title={tx("dev.mobile.power", "Power")}
              aria-label={tx("dev.mobile.power", "Power")}
            >
              <Power className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-8 w-8 rounded-lg p-0"
              disabled={!client}
              onClick={() => sendKey("ENTER")}
              title={tx("dev.mobile.enter", "Enter")}
              aria-label={tx("dev.mobile.enter", "Enter")}
            >
              <CornerDownLeft className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
        ) : null}

        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          {running ? (
            <div className="mr-1 hidden items-center gap-2 font-mono text-[11px] text-muted-foreground sm:flex">
              {backend ? <span>{backend}</span> : null}
              <span>
                {metrics.width}x{metrics.height}
              </span>
              <span>{metrics.fps.toFixed(1)} fps</span>
            </div>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 w-7 shrink-0 rounded-md p-0"
            disabled={!client || statusBusy}
            onClick={() => void refreshStatus()}
            title={tx("dev.mobile.refreshTitle", "Re-scan adb, SDK and devices")}
            aria-label={tx("dev.mobile.refresh", "Refresh")}
          >
            {statusBusy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            )}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 w-7 shrink-0 rounded-md p-0"
            onClick={() => persistShowLogs(!showLogs)}
            title={
              showLogs
                ? tx("dev.mobile.hideLogsTitle", "Hide logcat panel")
                : tx("dev.mobile.showLogsTitle", "Show logcat panel")
            }
            aria-pressed={showLogs}
            aria-label={
              showLogs
                ? tx("dev.mobile.hideLogs", "Hide logs")
                : tx("dev.mobile.showLogs", "Show logs")
            }
          >
            {showLogs ? (
              <PanelRightClose className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <PanelRightOpen className="h-3.5 w-3.5" aria-hidden />
            )}
          </Button>
          {!running ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 shrink-0 rounded-md px-2 text-[11px]"
              disabled={!client || statusBusy || busy}
              onClick={prepareMobile}
              title={tx(
                "dev.mobile.prepareTitle",
                "Install tools if needed, or ask the agent to finish setup - without cluttering this panel",
              )}
            >
              {statusBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Wand2 className="h-3.5 w-3.5" aria-hidden />
              )}
              {tx("dev.mobile.prepare", "Prepare")}
            </Button>
          ) : null}
        </div>
      </div>

      {running ? (
        <form
          className="flex flex-wrap items-center gap-2 border-b border-border/40 px-3 py-1.5"
          onSubmit={sendText}
        >
          <input
            type="text"
            value={textDraft}
            onChange={(e) => setTextDraft(e.target.value)}
            placeholder={tx("dev.mobile.textPlaceholder", "Type text on the device…")}
            className="h-8 min-w-[12rem] flex-1 rounded-lg border border-border/50 bg-background/80 px-2.5 text-[12.5px] outline-none"
            disabled={!client}
          />
          <Button
            type="submit"
            size="sm"
            variant="outline"
            className="h-8 rounded-lg"
            disabled={!client || !textDraft.trim()}
            title={tx("dev.mobile.sendTextTitle", "Send text via adb input")}
          >
            <Send className="h-3.5 w-3.5" aria-hidden />
            {tx("dev.mobile.sendText", "Send text")}
          </Button>
        </form>
      ) : null}

      {error ? (
        <p className="border-b border-border/40 px-3 py-1.5 text-[12px] text-destructive" title={error}>
          {error}
        </p>
      ) : null}

      {!running && pendingDevices.length ? (
        <div className="space-y-1 border-b border-sky-500/30 bg-sky-500/10 px-3 py-2 text-[12px] text-sky-100">
          <p className="font-medium text-sky-50">
            {tx(
              "dev.mobile.pendingTitle",
              "Phone detected - one step left",
            )}
          </p>
          <p className="text-sky-100/90">
            {status?.help ||
              tx(
                "dev.mobile.pendingBody",
                "Unlock the phone and accept the 'Allow USB debugging' prompt. The preview unlocks automatically.",
              )}
          </p>
          <p className="font-mono text-[11px] text-sky-100/70">
            {pendingDevices.map(serialFromDeviceLine).join(", ")}
          </p>
        </div>
      ) : null}

      {status?.error === "unstable_emulator" ||
      (status?.block_local_emulator && !status.ready) ? (
        <div className="space-y-1 border-b border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-100">
          <p className="font-medium text-amber-50">
            {tx(
              "dev.mobile.hwGateTitle",
              "Hardware requirements not met - local soft emulator blocked",
            )}
          </p>
          <p className="text-amber-100/90">
            {status.help ||
              status.acceleration_detail ||
              tx(
                "dev.mobile.hwGateBody",
                "Without KVM / Hypervisor, a software emulator freezes (ANR, dead Back/Home, 0 fps). Use Android Studio on Windows or a USB phone.",
              )}
          </p>
          {(status.recommendations ?? []).length ? (
            <ul className="list-disc space-y-0.5 pl-4 text-amber-100/85">
              {status.recommendations!.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 items-center justify-center overflow-auto p-3">
          {frameUrl ? (
            <img
              ref={imgRef}
              src={frameUrl}
              alt={tx("dev.mobile.screen", "Device screen")}
              className="max-h-full max-w-full cursor-crosshair touch-none rounded-xl shadow-lg ring-1 ring-border/60"
              draggable={false}
              onPointerDown={onPointerDown}
              onPointerUp={onPointerUp}
            />
          ) : (
            <div className="flex max-w-sm flex-col items-center gap-3 px-4 text-center text-muted-foreground">
              <Smartphone className="h-8 w-8 opacity-40" aria-hidden />
              <p className="text-[13px] font-medium text-foreground">
                {tx("dev.mobile.emptyTitle", "Mobile device preview")}
              </p>
              <p className="text-[12.5px] leading-5">{emptyHint}</p>
              {!status?.ready ? (
                <div className="flex flex-wrap items-center justify-center gap-2">
                  {status?.adb && avdOptions.length ? (
                    <Button
                      type="button"
                      size="sm"
                      className="h-8 rounded-lg"
                      disabled={!client || avdBooting || !selectedAvd}
                      onClick={() => startAvd(selectedAvd)}
                      title={tx(
                        "dev.mobile.startAvdTitle",
                        "Launch this emulator - the preview unlocks when it is online",
                      )}
                    >
                      {avdBooting ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                      ) : (
                        <Play className="h-3.5 w-3.5" aria-hidden />
                      )}
                      {avdBooting
                        ? tx("dev.mobile.avdBooting", "Emulator booting…")
                        : `${tx("dev.mobile.startAvd", "Start emulator")}${selectedAvd ? ` (${selectedAvd})` : ""}`}
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    size="sm"
                    variant={status?.adb && avdOptions.length ? "outline" : "default"}
                    className="h-8 rounded-lg"
                    disabled={!client || statusBusy || busy}
                    onClick={prepareMobile}
                  >
                    {statusBusy ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Wand2 className="h-3.5 w-3.5" aria-hidden />
                    )}
                    {tx("dev.mobile.prepare", "Prepare")}
                  </Button>
                </div>
              ) : (
                <Button
                  type="button"
                  size="sm"
                  className="h-8 rounded-lg"
                  disabled={!client || busy}
                  onClick={start}
                >
                  {busy ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Smartphone className="h-3.5 w-3.5" aria-hidden />
                  )}
                  {tx("dev.mobile.start", "Start preview")}
                </Button>
              )}
              {status?.adb && !status.ready && status.help ? (
                <details className="w-full max-w-md text-left">
                  <summary className="cursor-pointer text-[12px] text-muted-foreground/90 hover:text-foreground">
                    {tx("dev.mobile.connectHelp", "How to connect a device on this system")}
                  </summary>
                  <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-border/40 bg-background/60 p-2.5 text-[11px] leading-4 text-muted-foreground">
                    {status.help}
                  </pre>
                </details>
              ) : null}
              {onSeedChat && status?.ready ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-8 rounded-lg"
                  onClick={() =>
                    onSeedChat(
                      "/mobile android\n\n" +
                        tx(
                          "dev.mobile.seedHint",
                          "Ask the agent to build and install the app on the emulator or a plugged phone. Review the command, then send.",
                        ) +
                        "\n",
                    )
                  }
                >
                  <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden />
                  {tx("dev.mobile.seedLaunch", "Prepare app launch in chat")}
                </Button>
              ) : null}
            </div>
          )}
        </div>
        {showLogs ? (
          <aside className="flex w-[min(22rem,40%)] shrink-0 flex-col border-l border-border/50 bg-background/60">
            <div className="flex items-center justify-between gap-2 border-b border-border/40 px-2 py-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                {tx("dev.mobile.logs", "Logcat")}
              </span>
              <div className="flex items-center gap-0.5">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 rounded-md px-1.5 text-[11px] text-muted-foreground"
                  onClick={() => setLogs([])}
                  title={tx("dev.mobile.clearLogsTitle", "Clear displayed log lines")}
                >
                  <Eraser className="h-3.5 w-3.5" aria-hidden />
                  {tx("dev.mobile.clearLogs", "Clear")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 rounded-md px-1.5 text-[11px] text-muted-foreground"
                  onClick={() => persistShowLogs(false)}
                  title={tx("dev.mobile.hideLogsTitle", "Hide logcat panel")}
                >
                  <PanelRightClose className="h-3.5 w-3.5" aria-hidden />
                  {tx("dev.mobile.hideLogs", "Hide logs")}
                </Button>
              </div>
            </div>
            <pre className="min-h-0 flex-1 overflow-auto p-2 font-mono text-[10.5px] leading-4 text-foreground/85">
              {logs.length ? logs.join("\n") : tx("dev.mobile.noLogs", "No log lines yet.")}
            </pre>
          </aside>
        ) : null}
      </div>
    </div>
  );
}
