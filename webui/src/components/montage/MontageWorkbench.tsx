import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Icon as FluentIcon } from "@fluentui/react";
import "@/lib/fluent-icons";
import {
  CheckCircle2,
  Clapperboard,
  ExternalLink,
  Film,
  Image as ImageIcon,
  Loader2,
  Mic,
  Music2,
  Play,
  RefreshCw,
  AlertTriangle,
  FileText,
  Download,
  Images,
  Languages,
  LayoutTemplate,
  Monitor,
  Sparkles,
  Volume2,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ImageLightbox } from "@/components/ImageLightbox";
import { MediaPlayerLightbox } from "@/components/MediaPlayerLightbox";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { DevProjectSelector } from "@/components/dev/DevProjectSelector";
import { MontageInstallConsole } from "@/components/montage/MontageInstallConsole";
import { MontageTimeline } from "@/components/montage/MontageTimeline";
import type { SettingsSectionKey } from "@/components/settings/SettingsView";
import { Button } from "@/components/ui/button";
import {
  fetchMontageAssets,
  fetchMontageStatus,
  fetchSettings,
  runMontageSetup,
  updateImageGenerationSettings,
  updateMusicGenerationSettings,
  updateTranscriptionSettings,
  updateVideoGenerationSettings,
  updateVoiceSettings,
  workspaceFileDownloadUrl,
  type MontageAsset,
  type MontageDoctorCheck,
  type MontageMediaToolStatus,
  type MontageProfile,
  type MontageStatusPayload,
} from "@/lib/api";
import { MediaTemplateLibrary, mediaTemplateSeedText } from "@/components/studio/MediaTemplateLibrary";
import type { MediaTemplateItem } from "@/lib/api";
import type { ProjectFileMatch, RecentProjectEntry, SettingsPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";

type TabId = "templates" | "timeline" | "system" | "media" | "actions" | "gallery" | "profiles";

const TAB_IDS: readonly TabId[] = ["templates", "timeline", "system", "media", "actions", "gallery", "profiles"];
const TAB_STORAGE_KEY = "navin.montage.tab";

// A reload (HMR, dependency re-optimisation, engine restart) must land the
// user back on the tab they were working in, not on the Templates wall.
function loadTab(): TabId {
  try {
    const saved = window.localStorage.getItem(TAB_STORAGE_KEY);
    if (saved && (TAB_IDS as readonly string[]).includes(saved)) return saved as TabId;
  } catch {
    /* ignore */
  }
  return "templates";
}

function persistTab(tab: TabId) {
  try {
    window.localStorage.setItem(TAB_STORAGE_KEY, tab);
  } catch {
    /* ignore */
  }
}

type MediaToolKey = "image" | "video" | "music" | "stt" | "tts";

type ActionCard = {
  id: string;
  label: string;
  description: string;
  prompt: string;
  /** Featured capability: amber accent + NEW badge to draw the eye. */
  highlight?: boolean;
};

type ManagedModelOption = {
  slug: string;
  name: string;
  unitPriceUsd?: number | null;
  priceNote?: string | null;
  isDefault?: boolean;
};

const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };

const PACKAGE_CHECK_NAMES = new Set([
  "ffmpeg",
  "chromium",
  "chrome",
  "hyperframes",
  "remotion",
  "stock",
]);

// Packages the product does not work without, so they get the Install all button.
const REQUIRED_PACKAGE_IDS = new Set(["ffmpeg", "chromium", "hyperframes"]);

const MEDIA_SETTINGS_SECTION: Record<MediaToolKey, SettingsSectionKey> = {
  image: "image",
  video: "video",
  music: "voice",
  stt: "voice",
  tts: "voice",
};

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function checkTone(status: MontageDoctorCheck["status"]): string {
  if (status === "ok") return "text-emerald-500";
  if (status === "warn") return "text-amber-500";
  return "text-rose-500";
}

function checkDot(status: MontageDoctorCheck["status"]): string {
  if (status === "ok") return "bg-emerald-500";
  if (status === "warn") return "bg-amber-500";
  return "bg-rose-500";
}

function mediaLabel(
  tool: MediaToolKey,
  t: (key: string, opts?: { defaultValue?: string }) => string,
): string {
  const keys: Record<MediaToolKey, [string, string]> = {
    image: ["montage.mediaTools.image", "Image"],
    video: ["montage.mediaTools.video", "Video"],
    music: ["montage.mediaTools.music", "Music"],
    stt: ["montage.mediaTools.stt", "Voice input"],
    tts: ["montage.mediaTools.tts", "Voice out"],
  };
  const [key, fallback] = keys[tool];
  return t(key, { defaultValue: fallback });
}

const MEDIA_ICONS: Record<MediaToolKey, LucideIcon> = {
  image: ImageIcon,
  video: Film,
  music: Music2,
  stt: Mic,
  tts: Volume2,
};

/** Hide vendor branding noise in Montage media pickers. */
function stripOpenRouterLabel(text: string): string {
  return text
    .replace(/\bopen\s*router\b/gi, "")
    .replace(/\s{2,}/g, " ")
    .replace(/\s*([·|/,-])\s*$/g, "")
    .replace(/^\s*([·|/,-])\s*/g, "")
    .trim();
}

function mediaSubtitle(provider: string | undefined, model: string): string {
  const prov = stripOpenRouterLabel((provider || "").trim());
  const mod = stripOpenRouterLabel(model.trim());
  if (prov && mod) return `${prov} · ${mod}`;
  return prov || mod || "-";
}

function modelsForTool(
  settings: SettingsPayload | null,
  tool: MediaToolKey,
): ManagedModelOption[] {
  if (!settings) return [];
  if (tool === "image") return settings.image_generation?.managed_models ?? [];
  if (tool === "video") return settings.video_generation?.managed_models ?? [];
  if (tool === "music") return settings.music_generation?.managed_models ?? [];
  if (tool === "stt") return settings.transcription?.managed_models ?? [];
  return settings.voice?.managed_models ?? [];
}

export function MontageWorkbench({
  sessionKey,
  chatOpen,
  onRun,
  onSeed,
  onOpenSettings,
  projectPath,
  projectName,
  recentProjects,
  onSelectProject,
}: {
  sessionKey?: string | null;
  chatOpen?: boolean;
  onRun?: (text: string) => void;
  onSeed?: (
    text: string,
    files?: ProjectFileMatch[],
    options?: {
      replace?: boolean;
      mediaTemplate?: {
        id: string;
        title?: string;
        kind?: string;
        format?: string;
        family?: string;
      };
      mediaTemplates?: {
        id: string;
        title?: string;
        kind?: string;
        format?: string;
        family?: string;
      }[];
      localFiles?: File[];
    },
  ) => void;
  onOpenSettings?: (section?: SettingsSectionKey) => void;
  projectPath?: string | null;
  projectName?: string | null;
  recentProjects?: RecentProjectEntry[];
  onSelectProject?: (path: string, name?: string) => void;
}) {
  const { t } = useTranslation();
  const { token, client } = useClient();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [tab, setTabState] = useState<TabId>(loadTab);
  const setTab = useCallback((next: TabId) => {
    persistTab(next);
    setTabState(next);
  }, []);
  const [status, setStatus] = useState<MontageStatusPayload | null>(null);
  const [assets, setAssets] = useState<MontageAsset[]>([]);
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [setupBusy, setSetupBusy] = useState<string | null>(null);
  const [installConsoleOpen, setInstallConsoleOpen] = useState(false);
  const [installSeedEvents, setInstallSeedEvents] = useState<
    import("@/lib/api").MontageSetupStreamEvent[] | null
  >(null);
  const [modelBusy, setModelBusy] = useState<MediaToolKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imagePreview, setImagePreview] = useState<{
    url: string;
    name: string;
  } | null>(null);
  const [mediaPreview, setMediaPreview] = useState<{
    kind: "video" | "audio";
    url: string;
    name: string;
  } | null>(null);
  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const opts = {
        sessionKey: sessionKey || undefined,
        path: projectPath || undefined,
      };
      const [nextStatus, nextAssets, nextSettings] = await Promise.all([
        fetchMontageStatus(token, opts),
        fetchMontageAssets(token, opts).catch(() => null),
        fetchSettings(token, "", { syncCatalog: true }).catch(() => null),
      ]);
      setStatus(nextStatus);
      setAssets(nextAssets?.assets ?? []);
      if (nextSettings) setSettings(nextSettings);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : tx("montage.loadFailed", "Could not load Montage studio status."),
      );
    } finally {
      setLoading(false);
    }
  }, [token, sessionKey, projectPath, tx]);

  useEffect(() => {
    void load();
  }, [load]);

  // Generated media land under marketing/montage/ while the agent works.
  // Refresh the gallery when this chat's run ends so new deliverables show
  // up without a manual Refresh click.
  const chatId = sessionKey?.startsWith("websocket:")
    ? sessionKey.slice("websocket:".length)
    : (sessionKey ?? null);
  const refreshAssets = useCallback(() => {
    if (!token) return;
    void fetchMontageAssets(token, {
      sessionKey: sessionKey || undefined,
      path: projectPath || undefined,
    })
      .then((next) => setAssets(next.assets ?? []))
      .catch(() => undefined);
  }, [projectPath, sessionKey, token]);

  useEffect(() => {
    if (!token || !chatId) return;
    return client.onRunStatus((runChatId, startedAt) => {
      if (runChatId !== chatId || startedAt !== null) return;
      refreshAssets();
    });
  }, [chatId, client, refreshAssets, token]);

  // A finished render (from the Timeline tab or the agent) lands in the
  // gallery immediately instead of waiting for the run to end.
  useEffect(() => {
    if (!token) return;
    return client.onMontageUpdate((update) => {
      if (update.kind !== "assets") return;
      if (
        update.projectPath &&
        projectPath &&
        update.projectPath.replace(/\/+$/, "") !== projectPath.replace(/\/+$/, "")
      ) {
        return;
      }
      refreshAssets();
    });
  }, [client, projectPath, refreshAssets, token]);

  const actionCards = useMemo<ActionCard[]>(
    () => [
      {
        id: "translateDub",
        highlight: true,
        label: tx("montage.actions.translateDub", "Translate & dub a video"),
        description: tx(
          "montage.actions.translateDubDesc",
          "Timed STT, cue-preserving translation, synchronized voice track, then dub.",
        ),
        prompt:
          "Translate and DUB a video end to end (all tools are local - never claim otherwise). "
          + "1) If no video is attached or in the workspace, ask ONCE for the file or URL (yt-dlp download if URL). "
          + "2) Ask (or infer from the request) the target language, e.g. English clip to French voice. "
          + "3) montage(action=doctor); montage(action=setup, package=ffmpeg) only if ffmpeg is missing. "
          + "4) montage(action=transcribe, path=<video>) - writes source.srt + transcript.txt under marketing/montage/localization/<stem>/. "
          + "5) Translate the SRT cues yourself into the target language: keep cue numbering and timing EXACTLY, natural spoken register, save as translated.srt in the same folder. "
          + "6) montage(action=voicetrack, srt=translated.srt, max_tempo=1.35) and inspect sync.gate, drift_ms, overlaps, and overruns. Never use generate_speech with the full translation because monolithic audio loses source pauses. "
          + "7) Only if sync.gate.passed is true, montage(action=dub, path=<video>, voice=<voice_track>, original_gain_db=-22); add srt=translated.srt if subtitles should be burned. "
          + "8) If mouth synchronization is requested, montage(action=lipsync, path=<video>, voice=<voice_track>) after credentials are configured. "
          + "9) Deliver the final MP4 with the message tool media parameter. Never fake a step - if STT, TTS, or lip-sync credentials are not configured, identify the missing setting and stop.",
      },
      {
        id: "translateSubs",
        highlight: true,
        label: tx("montage.actions.translateSubs", "Translated subtitles"),
        description: tx(
          "montage.actions.translateSubsDesc",
          "Keep the original voice, burn clean translated subtitles into the video.",
        ),
        prompt:
          "Add TRANSLATED subtitles to a video (keep the original audio). "
          + "1) If no video is attached or in the workspace, ask ONCE for the file or URL. "
          + "2) montage(action=transcribe, path=<video>) for timed source.srt. "
          + "3) Translate the cues to the target language: keep numbering and timing EXACTLY, max ~42 chars per line, split long cues on two lines, save as translated.srt next to source.srt. "
          + "4) Burn them: montage(action=assemble, visuals=<video>, srt=translated.srt, profile matching the source format). "
          + "5) Deliver the MP4 plus the .srt file path so the user can also upload it as closed captions. Never invent timings.",
      },
      {
        id: "doctor",
        label: tx("montage.actions.doctor", "Check toolchain"),
        description: tx(
          "montage.actions.doctorDesc",
          "Verify ffmpeg, browser, Node, HyperFrames.",
        ),
        prompt:
          "Run montage(action=doctor) then montage(action=detect). Summarize what is ready vs missing with exact fixes. Do not invent readiness.",
      },
      {
        id: "package",
        label: tx("montage.actions.package", "Package social exports"),
        description: tx(
          "montage.actions.packageDesc",
          "YouTube, Shorts, Reels, Feed, TikTok, LinkedIn.",
        ),
        prompt:
          "Package the latest demo with montage(action=package, path=…, profiles=default). Write brief under marketing/montage/exports/. Never auto-publish.",
      },
      {
        id: "pipeline",
        label: tx("montage.actions.pipeline", "Full montage pipeline"),
        description: tx(
          "montage.actions.pipelineDesc",
          "Doctor → demo → package → kit + calendar.",
        ),
        prompt:
          "Full Montage on the LINKED project only. browser + montage tools are available - never claim otherwise. 1) montage(action=doctor) then setup if needed. 2) browser record_start → happy path → record_stop → demo_register. 3) package profiles=default. 4) analyze + calendar 14 days. Never auto-publish. If no runnable UI, ask once for URL - do not invent other products.",
      },
      {
        id: "music",
        label: tx("montage.actions.music", "Music bed (Lyria Clip)"),
        description: tx(
          "montage.actions.musicDesc",
          "30s clip by default; full song on request.",
        ),
        prompt:
          "Generate a music bed with generate_music: default Lyria Clip 30s. Lyria Pro only if the user asks for a full track. Confirm Plus/BYOK first.",
      },
      {
        id: "hyperframes",
        label: tx("montage.actions.hyperframes", "Compose & render"),
        description: tx(
          "montage.actions.hyperframesDesc",
          "HyperFrames HTML → MP4 with a platform profile.",
        ),
        prompt:
          "If HyperFrames is missing, montage(action=setup). Author HTML under marketing/montage/compositions/, then montage(action=render, composition=…, profile=youtube_shorts). Never auto-publish.",
      },
    ],
    [tx],
  );

  const seedMediaTemplate = (items: MediaTemplateItem[]) => {
    if (items.length === 0) return;
    onSeed?.(mediaTemplateSeedText("montage", items), undefined, {
      replace: true,
      mediaTemplates: items.map((item) => ({
        id: item.id,
        title: item.title,
        kind: item.kind,
        format: item.format,
        family: item.family,
      })),
    });
  };

  const tabs: { id: TabId; label: string; icon: LucideIcon | null }[] = [
    { id: "templates", label: tx("montage.tabs.templates", "Templates"), icon: LayoutTemplate },
    { id: "timeline", label: tx("montage.tabs.timeline", "Timeline"), icon: null },
    { id: "system", label: tx("montage.tabs.system", "System"), icon: Wrench },
    { id: "media", label: tx("montage.tabs.media", "AI model"), icon: Sparkles },
    { id: "actions", label: tx("montage.tabs.actions", "Actions"), icon: Play },
    { id: "gallery", label: tx("montage.tabs.gallery", "Gallery"), icon: Images },
    { id: "profiles", label: tx("montage.tabs.profiles", "Profiles"), icon: Monitor },
  ];

  const onInstallPackage = async (packageId: string, force = false) => {
    if (!token || setupBusy) return;
    const command = `navin montage setup --package ${packageId}${force ? " --force" : ""}`;
    setInstallConsoleOpen(true);
    // Immediate local start so the console is never blank while HTTP/WS catch up.
    setInstallSeedEvents([
      {
        type: "start",
        id: `pending-${packageId}`,
        command,
      },
    ]);
    setSetupBusy(packageId);
    setError(null);
    try {
      const result = await runMontageSetup(token, {
        force,
        package: packageId,
        sessionKey: sessionKey || undefined,
        path: projectPath || undefined,
        stream: true,
      });
      setStatus(result.status);
      if (result.events?.length) {
        setInstallSeedEvents(result.events);
      }
      if (!result.setup.ok && (result.setup.error || result.setup.fix)) {
        setError(result.setup.fix || result.setup.error || "setup failed");
      }
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : tx("montage.setupFailed", "Package install failed.");
      setError(message);
      setInstallSeedEvents((prev) => [
        ...(prev ?? []),
        { type: "log", data: `ERROR: ${message}\n` },
        { type: "done", exit_code: 1 },
      ]);
    } finally {
      setSetupBusy(null);
    }
  };

  const changeMediaModel = async (tool: MediaToolKey, model: string) => {
    if (!token || !settings || !model || modelBusy) return;
    setModelBusy(tool);
    setError(null);
    try {
      let next: SettingsPayload;
      if (tool === "image") {
        const cur = settings.image_generation;
        next = await updateImageGenerationSettings(token, {
          enabled: cur.enabled,
          provider: cur.provider || "navin",
          model,
          defaultAspectRatio: cur.default_aspect_ratio,
          defaultImageSize: cur.default_image_size,
          maxImagesPerTurn: cur.max_images_per_turn,
        });
      } else if (tool === "video") {
        const cur = settings.video_generation!;
        next = await updateVideoGenerationSettings(token, {
          enabled: cur.enabled,
          provider: cur.provider || "navin",
          model,
          defaultAspectRatio: cur.default_aspect_ratio,
          defaultDurationSeconds: cur.default_duration_seconds,
          defaultResolution: cur.default_resolution,
        });
      } else if (tool === "music") {
        const cur = settings.music_generation!;
        next = await updateMusicGenerationSettings(token, {
          enabled: cur.enabled,
          provider: cur.provider || "navin",
          model,
        });
      } else if (tool === "stt") {
        const cur = settings.transcription!;
        next = await updateTranscriptionSettings(token, {
          enabled: cur.enabled,
          provider: cur.provider || "navin",
          model,
          language: cur.language || "",
          maxDurationSec: cur.max_duration_sec,
          maxUploadMb: cur.max_upload_mb,
        });
      } else {
        const cur = settings.voice!;
        next = await updateVoiceSettings(token, {
          ttsProvider: cur.tts_provider || "navin",
          ttsModel: model,
          voice: cur.voice,
          autoSpeak: cur.auto_speak,
          responseFormat: cur.response_format,
          realtimeEnabled: cur.realtime_enabled,
        });
      }
      setSettings(next);
      // Refresh montage readiness badges after model/provider change.
      const opts = {
        sessionKey: sessionKey || undefined,
        path: projectPath || undefined,
      };
      const refreshed = await fetchMontageStatus(token, opts);
      setStatus(refreshed);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : tx("montage.modelUpdateFailed", "Could not update the media model."),
      );
    } finally {
      setModelBusy(null);
    }
  };

  const runAction = (card: ActionCard, send: boolean) => {
    const text = `/montage ${card.prompt}`;
    if (send && onRun) {
      onRun(text);
      return;
    }
    onSeed?.(text);
  };

  const openAsset = (asset: MontageAsset) => {
    if (!token || !sessionKey) return;
    const url = workspaceFileDownloadUrl(token, sessionKey, asset.path, "", projectPath);
    if (asset.kind === "image") {
      setImagePreview({ url, name: asset.name });
      return;
    }
    if (asset.kind === "video" || asset.kind === "audio") {
      setMediaPreview({ kind: asset.kind, url, name: asset.name });
      return;
    }
    onSeed?.(
      `Open and summarize ${asset.name} at ${asset.path} (use open_file_preview).`,
    );
  };

  const checks = status?.doctor.checks ?? [];
  const mediaTools = status?.media.tools ?? {};
  const profiles = status?.profiles ?? [];
  const packageRows = status?.packages?.items ?? [];

  const actionablePackages = useMemo(() => {
    return packageRows.filter((row) => {
      if (row.ui_hidden) return false;
      if (row.tier === "builtin") return false;
      if (row.needs_action != null) return Boolean(row.needs_action);
      return !row.ready && Boolean(row.installable);
    });
  }, [packageRows]);

  const requiredInstallPackages = useMemo(
    () =>
      actionablePackages.filter(
        (row) => row.installable && !row.optional && REQUIRED_PACKAGE_IDS.has(row.id),
      ),
    [actionablePackages],
  );

  const actionableChecks = useMemo(() => {
    return checks.filter(
      (check) =>
        check.status !== "ok" && !PACKAGE_CHECK_NAMES.has(check.name.toLowerCase()),
    );
  }, [checks]);

  const readyPackageCount = useMemo(
    () =>
      packageRows.filter(
        (row) => !row.ui_hidden && row.tier !== "builtin" && row.ready,
      ).length,
    [packageRows],
  );

  return (
    <div className="flex h-full min-h-0 flex-col bg-background" data-testid="montage-workbench">
      <header
        className={cn(
          "grid h-11 shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-2 border-b border-border/55 px-3",
          !chatOpen && NOTIFICATION_GUTTER,
        )}
      >
        <div className="flex min-w-0 items-center gap-2">
          <Clapperboard
            className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400"
            aria-hidden
          />
          <p className="truncate text-[13px] font-semibold text-foreground">
            {tx("montage.title", "Montage Studio")}
          </p>
        </div>
        <nav className="flex min-w-0 items-center justify-center gap-0.5 overflow-x-auto">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              className={cn(
                "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] font-medium transition-colors active:scale-[0.96]",
                tab === item.id
                  ? "bg-amber-600 text-white"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              {item.icon ? (
                <item.icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
              ) : (
                <FluentIcon iconName="Timeline" className="text-[14px]" aria-hidden />
              )}
              {item.label}
              {item.id === "gallery" && assets.length > 0 ? (
                <span className="ml-1 tabular-nums opacity-80">{assets.length}</span>
              ) : null}
            </button>
          ))}
        </nav>
        <div className="flex min-w-0 items-center justify-end gap-1">
          {onSelectProject ? (
            <DevProjectSelector
              projectPath={projectPath ?? null}
              projectName={projectName ?? null}
              recentProjects={recentProjects ?? []}
              onSelectProject={onSelectProject}
              compact
            />
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-8 w-8 shrink-0 p-0"
            onClick={() => void load()}
            disabled={loading}
            title={tx("montage.refresh", "Refresh")}
            aria-label={tx("montage.refresh", "Refresh")}
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
          </Button>
        </div>
      </header>

      {error ? (
        <div className="mx-3 mt-2 flex items-start gap-2 rounded-xl border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-700 dark:text-rose-300">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <p className="min-w-0 flex-1">{error}</p>
        </div>
      ) : null}

      {tab === "timeline" ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <MontageTimeline
            token={token}
            sessionKey={sessionKey}
            projectPath={projectPath}
            assets={assets}
            onSeed={onSeed ? (text) => onSeed(text) : undefined}
          />
        </div>
      ) : tab === "templates" ? (
        <div className="min-h-0 flex-1">
          <MediaTemplateLibrary
            studio="montage"
            onUse={seedMediaTemplate}
            onWorkflow={(text, items) =>
              onSeed?.(text, undefined, {
                replace: true,
                mediaTemplates: items.map((item) => ({
                  id: item.id,
                  title: item.title,
                  kind: item.kind,
                  format: item.format,
                  family: item.family,
                })),
              })
            }
            onLocalFiles={(files) =>
              onSeed?.("", undefined, { replace: false, localFiles: files })
            }
            onSeed={(text, options) =>
              onSeed?.(text, undefined, { replace: options?.replace ?? false })
            }
          />
        </div>
      ) : (
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        <AnimatePresence mode="wait" initial={false}>
          {tab === "system" ? (
            <motion.div
              key="system"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={spring}
              className="space-y-4"
            >
              <section className="rounded-2xl border border-border/55 bg-card/40 p-4 shadow-[0_1px_0_rgba(255,255,255,0.04)_inset]">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <h2 className="text-[13.5px] font-semibold text-foreground">
                      {tx("montage.system.toolchain", "Toolchain")}
                    </h2>
                    <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                      {status?.ready_for_composition
                        ? tx(
                            "montage.system.compositionReady",
                            "Ready for HyperFrames composition renders.",
                          )
                        : tx(
                            "montage.system.compositionPending",
                            "Install missing tools below before composition renders.",
                          )}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {requiredInstallPackages.length > 0 ? (
                      <Button
                        type="button"
                        size="sm"
                        className="h-7 rounded-full px-2.5 text-[11px] active:scale-[0.96]"
                        disabled={setupBusy != null}
                        onClick={() => void onInstallPackage("all", false)}
                      >
                        {setupBusy === "all" ? (
                          <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                        ) : (
                          <Download className="mr-1 h-3 w-3" aria-hidden />
                        )}
                        {tx("montage.installAll", "Install all")}
                      </Button>
                    ) : null}
                    <Wrench className="h-4 w-4 text-muted-foreground" aria-hidden />
                  </div>
                </div>

                {readyPackageCount > 0 ? (
                  <p className="mt-3 text-[11.5px] text-muted-foreground">
                    {t("montage.system.readyCount", {
                      defaultValue: "{{count}} package(s) ready - only pending installs are listed.",
                      count: readyPackageCount,
                    })}
                  </p>
                ) : null}

                <ul className="mt-3 space-y-2">
                  {loading && actionablePackages.length === 0 && actionableChecks.length === 0
                    ? Array.from({ length: 3 }).map((_, i) => (
                        <li
                          key={i}
                          className="h-14 animate-pulse rounded-xl bg-muted/40"
                        />
                      ))
                    : null}

                  {actionablePackages.map((row, index) => (
                    <motion.li
                      key={row.id}
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ ...spring, delay: index * 0.04 }}
                      className="flex flex-wrap items-start gap-2 rounded-xl border border-border/40 bg-background/50 px-3 py-2.5"
                    >
                      <span
                        className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-amber-500"
                        aria-hidden
                      />
                      <div className="min-w-0 flex-1">
                        <p className="flex flex-wrap items-center gap-2 text-[12.5px] font-medium text-foreground">
                          <span>{row.label}</span>
                          {row.optional ? (
                            <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-600 dark:text-amber-400">
                              {tx("montage.optional", "optional")}
                            </span>
                          ) : null}
                          {row.size_mb > 0 ? (
                            <span className="tabular-nums text-[10px] text-muted-foreground">
                              ~{row.size_mb} MB
                            </span>
                          ) : null}
                        </p>
                        <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                          {row.description}
                        </p>
                        <p className="mt-1 text-[11px] text-muted-foreground/90">
                          {row.detail || row.install_hint}
                        </p>
                      </div>
                      {row.installable ? (
                        <Button
                          type="button"
                          size="sm"
                          className="h-7 shrink-0 rounded-full px-2.5 text-[11px] active:scale-[0.96]"
                          disabled={setupBusy != null}
                          onClick={() => void onInstallPackage(row.id, false)}
                        >
                          {setupBusy === row.id ? (
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                          ) : (
                            <Download className="mr-1 h-3 w-3" aria-hidden />
                          )}
                          {tx("montage.install", "Install")}
                        </Button>
                      ) : null}
                    </motion.li>
                  ))}

                  {actionableChecks.map((check, index) => (
                    <motion.li
                      key={check.name}
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{
                        ...spring,
                        delay: (actionablePackages.length + index) * 0.04,
                      }}
                      className="flex items-start gap-2.5 rounded-xl border border-border/40 bg-background/50 px-3 py-2"
                    >
                      <span
                        className={cn(
                          "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                          checkDot(check.status),
                        )}
                        aria-hidden
                      />
                      <div className="min-w-0 flex-1">
                        <p className="flex items-center gap-2 text-[12.5px] font-medium text-foreground">
                          <span className="capitalize">{check.name}</span>
                          <span
                            className={cn(
                              "text-[10px] font-semibold uppercase tracking-wide",
                              checkTone(check.status),
                            )}
                          >
                            {check.status}
                          </span>
                        </p>
                        <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                          {check.detail}
                        </p>
                        {check.fix ? (
                          <p className="mt-1 text-[11px] text-muted-foreground/90">
                            {check.fix}
                          </p>
                        ) : null}
                      </div>
                    </motion.li>
                  ))}

                  {!loading &&
                  actionablePackages.length === 0 &&
                  actionableChecks.length === 0 ? (
                    <li className="flex items-center gap-2 rounded-xl border border-emerald-500/20 bg-emerald-500/8 px-3 py-3 text-[12.5px] text-emerald-700 dark:text-emerald-300">
                      <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden />
                      {tx(
                        "montage.system.allReady",
                        "Nothing to install - toolchain looks ready.",
                      )}
                    </li>
                  ) : null}
                </ul>
              </section>
            </motion.div>
          ) : null}

          {tab === "media" ? (
            <motion.div
              key="media"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={spring}
              className="space-y-3"
            >
              {(["image", "video", "music", "stt", "tts"] as const).map((key, index) => {
                const row = mediaTools[key] as MontageMediaToolStatus | undefined;
                const models = modelsForTool(settings, key);
                const currentModel = row?.model || "";
                const section = MEDIA_SETTINGS_SECTION[key];
                const Icon = MEDIA_ICONS[key];
                return (
                  <motion.article
                    key={key}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ ...spring, delay: index * 0.04 }}
                    className="rounded-2xl border border-border/55 bg-card/40 p-3.5"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="flex items-center gap-2 text-[13px] font-semibold text-foreground">
                          <Icon className="h-4 w-4 shrink-0 text-foreground/80" aria-hidden />
                          <span>{mediaLabel(key, t)}</span>
                        </p>
                        <p className="mt-0.5 truncate pl-6 text-[11.5px] text-muted-foreground">
                          {mediaSubtitle(row?.provider, currentModel)}
                        </p>
                      </div>
                      <span
                        className={cn(
                          "rounded-full px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide",
                          row?.ready
                            ? "bg-emerald-500/12 text-emerald-600 dark:text-emerald-400"
                            : "bg-muted text-muted-foreground",
                        )}
                      >
                        {row?.ready
                          ? tx("montage.ready", "Ready")
                          : tx("montage.notReady", "Not ready")}
                      </span>
                    </div>

                    <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
                      {models.length > 0 ? (
                        <select
                          value={
                            models.some((m) => m.slug === currentModel)
                              ? currentModel
                              : models[0]?.slug || ""
                          }
                          disabled={modelBusy === key || !row?.provider}
                          onChange={(event) =>
                            void changeMediaModel(key, event.target.value)
                          }
                          className="h-8 min-w-0 flex-1 rounded-full border border-border bg-background px-3 text-[12.5px]"
                        >
                          {models.map((m) => (
                            <option key={m.slug} value={m.slug}>
                              {stripOpenRouterLabel(m.name) || m.slug}
                              {m.isDefault
                                ? ` (${tx("montage.modelDefault", "default")})`
                                : ""}
                            </option>
                          ))}
                          {currentModel &&
                          !models.some((m) => m.slug === currentModel) ? (
                            <option value={currentModel}>
                              {stripOpenRouterLabel(currentModel) || currentModel}
                            </option>
                          ) : null}
                        </select>
                      ) : (
                        <p className="flex-1 text-[11.5px] text-muted-foreground">
                          {tx(
                            "montage.media.noModels",
                            "No managed model list yet - open Settings to configure.",
                          )}
                        </p>
                      )}
                      {modelBusy === key ? (
                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
                      ) : null}
                      {onOpenSettings ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-8 shrink-0 rounded-full px-2.5 text-[11.5px]"
                          onClick={() => onOpenSettings(section)}
                        >
                          <ExternalLink className="mr-1 h-3 w-3" aria-hidden />
                          {tx("montage.media.openSettings", "Settings list")}
                        </Button>
                      ) : null}
                    </div>
                    {(() => {
                      const hit = models.find((m) => m.slug === currentModel);
                      const note = hit?.priceNote
                        ? stripOpenRouterLabel(hit.priceNote)
                        : "";
                      return note ? (
                        <p className="mt-2 text-[11px] text-muted-foreground">{note}</p>
                      ) : null;
                    })()}
                  </motion.article>
                );
              })}
            </motion.div>
          ) : null}

          {tab === "actions" ? (
            <motion.div
              key="actions"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={spring}
              className="grid gap-2 sm:grid-cols-2"
            >
              {actionCards.map((card, index) => (
                <motion.article
                  key={card.id}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ ...spring, delay: index * 0.04 }}
                  className={
                    card.highlight
                      ? "flex flex-col rounded-2xl border border-amber-500/45 bg-gradient-to-br from-amber-500/10 to-transparent p-3.5"
                      : "flex flex-col rounded-2xl border border-border/55 bg-card/40 p-3.5"
                  }
                >
                  <h3 className="flex items-center gap-2 text-[13px] font-semibold text-foreground">
                    {card.highlight ? (
                      <Languages
                        className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400"
                        strokeWidth={1.75}
                        aria-hidden
                      />
                    ) : null}
                    {card.label}
                    {card.highlight ? (
                      <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
                        {tx("montage.newBadge", "New")}
                      </span>
                    ) : null}
                  </h3>
                  <p className="mt-1 flex-1 text-[11.5px] leading-relaxed text-muted-foreground">
                    {card.description}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <Button
                      type="button"
                      size="sm"
                      className="h-8 rounded-full active:scale-[0.96]"
                      onClick={() => runAction(card, true)}
                    >
                      <Play className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                      {tx("montage.run", "Run")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="h-8 rounded-full"
                      onClick={() => runAction(card, false)}
                    >
                      {tx("montage.seed", "Edit in chat")}
                    </Button>
                  </div>
                </motion.article>
              ))}
            </motion.div>
          ) : null}

          {tab === "gallery" ? (
            <motion.div
              key="gallery"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={spring}
            >
              {assets.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-border/60 px-6 py-16 text-center">
                  <Film className="h-8 w-8 text-muted-foreground/70" aria-hidden />
                  <p className="text-[13px] font-medium text-foreground">
                    {tx("montage.galleryEmpty", "No montage assets yet")}
                  </p>
                  <p className="max-w-sm text-[12px] text-muted-foreground">
                    {tx(
                      "montage.galleryEmptyHint",
                      "Record a demo or run the pipeline - exports land under marketing/montage/.",
                    )}
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    className="mt-2 h-8 rounded-full"
                    onClick={() => setTab("actions")}
                  >
                    {tx("montage.goActions", "Go to actions")}
                  </Button>
                </div>
              ) : (
                <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {assets.map((asset, index) => (
                    <motion.li
                      key={asset.path}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ ...spring, delay: Math.min(index, 12) * 0.03 }}
                    >
                      <button
                        type="button"
                        onClick={() => openAsset(asset)}
                        className="flex w-full flex-col rounded-2xl border border-border/55 bg-card/40 p-3 text-left transition-colors hover:bg-muted/30 active:scale-[0.98]"
                      >
                        <span className="flex items-center gap-2">
                          <AssetIcon kind={asset.kind} />
                          <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-foreground">
                            {asset.name}
                          </span>
                        </span>
                        <span className="mt-2 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                          <span className="capitalize">{asset.bucket}</span>
                          <span className="tabular-nums">{formatBytes(asset.size)}</span>
                        </span>
                      </button>
                    </motion.li>
                  ))}
                </ul>
              )}
            </motion.div>
          ) : null}

          {tab === "profiles" ? (
            <motion.div
              key="profiles"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={spring}
              className="overflow-hidden rounded-2xl border border-border/55"
            >
              <table className="w-full text-left text-[12px]">
                <thead className="bg-muted/40 text-[11px] uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-semibold">
                      {tx("montage.profile", "Profile")}
                    </th>
                    <th className="px-3 py-2 font-semibold">
                      {tx("montage.resolution", "Resolution")}
                    </th>
                    <th className="px-3 py-2 font-semibold">
                      {tx("montage.aspect", "Aspect")}
                    </th>
                    <th className="px-3 py-2 font-semibold">
                      {tx("montage.defaultPkg", "Default")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {profiles.map((profile: MontageProfile) => (
                    <tr
                      key={profile.id}
                      className="border-t border-border/40 odd:bg-muted/15"
                    >
                      <td className="px-3 py-2 font-medium text-foreground">
                        {profile.label}
                      </td>
                      <td className="px-3 py-2 tabular-nums text-muted-foreground">
                        {profile.width}x{profile.height}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {profile.aspect_ratio}
                      </td>
                      <td className="px-3 py-2">
                        {profile.default_package ? (
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" aria-hidden />
                        ) : (
                          <span className="text-muted-foreground/60">-</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
      )}

      <ImageLightbox
        images={imagePreview ? [{ url: imagePreview.url, name: imagePreview.name }] : []}
        index={imagePreview ? 0 : null}
        onIndexChange={() => undefined}
        onOpenChange={(open) => {
          if (!open) setImagePreview(null);
        }}
      />
      <MediaPlayerLightbox
        open={mediaPreview != null}
        onOpenChange={(open) => {
          if (!open) setMediaPreview(null);
        }}
        kind={mediaPreview?.kind === "audio" ? "audio" : "video"}
        url={mediaPreview?.url ?? ""}
        name={mediaPreview?.name}
      />

      {/* Always mounted so agent_exec subscription is ready before setup starts. */}
      <MontageInstallConsole
        open={installConsoleOpen || setupBusy != null}
        busy={setupBusy != null}
        seedEvents={installSeedEvents}
        title={
          setupBusy === "all"
            ? t("montage.console.installAllTitle", {
                defaultValue: "Installing required tools",
              })
            : setupBusy
              ? t("montage.console.installTitle", {
                  defaultValue: "Installing {{package}}",
                  package: setupBusy,
                })
              : undefined
        }
        onClose={() => {
          if (setupBusy != null) return;
          setInstallConsoleOpen(false);
          setInstallSeedEvents(null);
        }}
      />
    </div>
  );
}

function AssetIcon({ kind }: { kind: string }) {
  if (kind === "video") return <Film className="h-3.5 w-3.5 shrink-0 text-violet-400" aria-hidden />;
  if (kind === "image") return <ImageIcon className="h-3.5 w-3.5 shrink-0 text-sky-400" aria-hidden />;
  if (kind === "audio") return <Music2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" aria-hidden />;
  return <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />;
}
