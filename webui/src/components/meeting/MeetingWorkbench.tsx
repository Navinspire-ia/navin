// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  DefaultButton,
  MessageBar,
  MessageBarType,
  SearchBox,
} from "@fluentui/react";
import "@/lib/fluent-icons";
import {
  Bell,
  Bot,
  HelpCircle,
  CalendarDays,
  ChevronDown,
  Download,
  FileAudio,
  FileText,
  LayoutTemplate,
  Loader2,
  Maximize2,
  MessageSquareText,
  Mic,
  Minimize2,
  Plus,
  Printer,
  ShieldCheck,
  Sparkles,
  Square,
  StickyNote,
  Trash2,
  Upload,
  Users,
  Video,
  Volume2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { TranscriptTurns } from "@/components/meeting/TranscriptTurns";
import {
  base64ToBlob,
  audioSeekTarget,
  botShouldPoll,
  fromDiskRecord,
  meetingMatches,
  readMeetingCache,
  toDiskRecord,
  writeMeetingCache,
  type PersistedMeeting,
} from "@/components/meeting/meetingPersistence";
import { MarkdownText } from "@/components/MarkdownText";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import {
  appendAudit,
  auditForMeeting,
  downloadAuditMarkdown,
  downloadTextFile,
} from "@/components/meeting/meetingAudit";
import {
  eventsStartingSoon,
  meetingJoinUrl,
  parseIcsEvents,
  upcomingEvents,
  type CalendarEvent,
} from "@/components/meeting/meetingIcs";
import {
  analyzeMeeting,
  buildReportHtml,
  buildReportMarkdown,
  formatDurationLabel,
  formatTimecode,
  renameSpeaker,
  rosterFromTranscript,
  type MeetingReport,
  type ReportLabels,
} from "@/components/meeting/meetingReport";
import {
  allMeetingTemplates,
  findMeetingTemplate,
  readCustomTemplates,
  writeCustomTemplates,
  type MeetingSummaryTemplate,
} from "@/components/meeting/meetingTemplates";
import {
  ApiError,
  fetchMeetingAnswer,
  fetchMeetingBot,
  fetchMeetingReport,
  fetchMeetingSpeakers,
  meetingStoreApi,
  openExternalUrl,
  type MeetingAudioSegment,
} from "@/lib/api";
import {
  audioContextConstructor,
  blobToDataUrl,
  convertBlobToWav,
  incrementalWavFromBlob,
  segmentAudioBlob,
} from "@/lib/audio";
import { isDesktopShell } from "@/lib/desktop";
import {
  describeMeetingSttError,
  meetingSttErrorDetail,
  transcriptHasSpeakerLabels,
} from "@/lib/meeting-stt";
import { notifyUser } from "@/lib/os-notification";
import type { SettingsPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const ICS_STORAGE_KEY = "navin.meeting.ics.v1";
const NOTIFIED_KEY = "navin.meeting.notified.v1";
const AUTOJOIN_KEY = "navin.meeting.autojoin.v1";
const DEFAULT_CHUNK_MS = 5_000;
/** Restart the recorder so decode stays cheap on long meetings. */
const ROTATE_MS = 90_000;
/** Providers that reject the browser webm/opus container. */
const WAV_ONLY_PROVIDERS = new Set(["xiaomi_mimo"]);
const NATIVE_DIARIZATION_PROVIDERS = new Set(["assemblyai"]);
/** Longest slice sent to the ingress; kept under the configured duration cap. */
const MAX_SEGMENT_SEC = 90;
const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };

type TabId =
  | "transcript"
  | "report"
  | "notes"
  | "actions"
  | "calendar"
  | "templates";

type MeetingRecord = PersistedMeeting;

/** Audio of one MediaRecorder run, pinned to the meeting it was started in. */
type LiveSegment = {
  meetingId: string;
  chunks: Blob[];
  /** 16 kHz frames already sent to STT from this segment. */
  transcribedFrames: number;
  mimeType: string;
  /** Offset of this segment inside the whole meeting, in ms. */
  baseMs: number;
};

type MeetingAction = {
  id: string;
  label: string;
  description: string;
  prompt: string;
};

const selectClassName =
  "h-8 rounded-full border border-border/60 bg-background px-3 text-[12px] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40";

function nowIso(): string {
  return new Date().toISOString();
}

function newId(): string {
  return crypto.randomUUID();
}

function defaultTitle(language: string): string {
  try {
    return new Intl.DateTimeFormat(language, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date());
  } catch {
    return nowIso();
  }
}

function slugify(title: string): string {
  return (
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 48) || "meeting"
  );
}

function readMeetings(): MeetingRecord[] {
  return readMeetingCache(window.localStorage);
}

function writeMeetings(meetings: MeetingRecord[]): boolean {
  return writeMeetingCache(window.localStorage, meetings);
}

function readIcsEvents(): CalendarEvent[] {
  try {
    const raw = window.localStorage.getItem(ICS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as CalendarEvent[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeIcsEvents(events: CalendarEvent[]) {
  try {
    window.localStorage.setItem(ICS_STORAGE_KEY, JSON.stringify(events));
  } catch {
    // ignore
  }
}

function pickMimeType(): string {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  for (const type of candidates) {
    if (
      typeof MediaRecorder !== "undefined" &&
      MediaRecorder.isTypeSupported(type)
    ) {
      return type;
    }
  }
  return "";
}

function wordCount(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

/** ISO date to the local `YYYY-MM-DDTHH:mm` a datetime-local input expects. */
function toDatetimeLocal(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function formatDay(iso: string, language: string): string {
  try {
    return new Intl.DateTimeFormat(language, {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso.slice(0, 16).replace("T", " ");
  }
}

function formatEventWhen(iso: string, language: string): string {
  try {
    return new Intl.DateTimeFormat(language, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

export function MeetingWorkbench({
  chatOpen,
  onSeed,
  onTranscribeAudio,
  transcription,
  onOpenVoiceSettings,
  onSpeak,
  focusMode = false,
  onToggleFocus,
  sessionKey,
  onOpenNote,
}: {
  chatOpen?: boolean;
  onSeed?: (text: string) => void;
  onTranscribeAudio?: (
    dataUrl: string,
    options?: { durationMs?: number },
  ) => Promise<string>;
  transcription?: SettingsPayload["transcription"];
  onOpenVoiceSettings?: () => void;
  /** Reads text aloud through the Navin TTS provider; absent when unavailable. */
  onSpeak?: (text: string) => { done: Promise<void>; stop: () => void };
  /** True when the desk covers the chat column. */
  focusMode?: boolean;
  onToggleFocus?: () => void;
  /** Session the desk runs in; required to synthesize without the chat. */
  sessionKey?: string | null;
  /** Jump to a note in the Notes module (the one this meeting was filed into). */
  onOpenNote?: (noteId: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const { token } = useClient();
  const reduceMotion = useReducedMotion();
  // Stable identity: the start-alert interval below lists it as a dependency.
  const tx = useCallback(
    (key: string, fallback: string, values?: Record<string, unknown>) =>
      t(key, { defaultValue: fallback, ...(values ?? {}) }),
    [t],
  );

  const [meetings, setMeetings] = useState<MeetingRecord[]>(() =>
    readMeetings(),
  );
  const [activeId, setActiveId] = useState<string | null>(
    () => readMeetings()[0]?.id ?? null,
  );
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [templates, setTemplates] = useState<MeetingSummaryTemplate[]>(() =>
    allMeetingTemplates(),
  );
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[]>(() =>
    readIcsEvents(),
  );
  const [askDraft, setAskDraft] = useState("");
  const [customTemplateName, setCustomTemplateName] = useState("");
  const [customTemplateBody, setCustomTemplateBody] = useState("");
  const [showAudit, setShowAudit] = useState(false);
  const [tab, setTab] = useState<TabId>("transcript");
  const [generating, setGenerating] = useState(false);
  const [answering, setAnswering] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [cacheError, setCacheError] = useState<string | null>(null);
  const [transcriptQuery, setTranscriptQuery] = useState("");
  const [translationLanguage, setTranslationLanguage] = useState(
    i18n.language.startsWith("fr") ? "English" : "French",
  );
  const [audioSegments, setAudioSegments] = useState<
    Array<MeetingAudioSegment & { url: string }>
  >([]);
  const [reportModel, setReportModel] = useState<string | null>(null);
  const [labelling, setLabelling] = useState(false);
  const [editTranscript, setEditTranscript] = useState(false);

  // Per-meeting view state. Without this reset the Q&A answer, the model
  // badge and the transcript filter of the previous meeting stayed on screen
  // under the next one's title.
  useEffect(() => {
    setAnswer(null);
    setAskDraft("");
    setReportModel(null);
    setEditTranscript(false);
    setTranscriptQuery("");
  }, [activeId]);
  const [query, setQuery] = useState("");
  const [briefId, setBriefId] = useState<string | null>(null);
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [autoJoin, setAutoJoin] = useState(() => {
    try {
      return window.localStorage.getItem(AUTOJOIN_KEY) === "1";
    } catch {
      return false;
    }
  });

  const sttProvider = transcription?.provider ?? "";
  const sttModel = transcription?.model ?? "";
  const sttReady = Boolean(
    onTranscribeAudio &&
    (transcription
      ? transcription.enabled && transcription.provider_configured
      : true),
  );
  const maxDurationSec = transcription?.max_duration_sec ?? 120;
  const wantsWav = WAV_ONLY_PROVIDERS.has(sttProvider);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  // Conference capture: raw display/mic streams mixed into streamRef via an
  // AudioContext. Both must be released when the recording stops.
  const captureStreamsRef = useRef<MediaStream[]>([]);
  const audioCtxRef = useRef<AudioContext | null>(null);
  /** Meeting the live capture (mic or conference) belongs to, until it stops. */
  const recordingMeetingIdRef = useRef<string | null>(null);
  const flushChainRef = useRef(Promise.resolve());
  const rotateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rotatingRef = useRef(false);
  const maybeAutoIdentifyRef = useRef<(meetingId?: string | null) => Promise<void>>(
    async () => {},
  );
  const startedAtRef = useRef(0);
  /** Audio already captured for this meeting before this session, in ms. */
  const recordBaseMsRef = useRef(0);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const icsInputRef = useRef<HTMLInputElement | null>(null);
  const activeIdRef = useRef<string | null>(activeId);
  const meetingsRef = useRef(meetings);
  const autoJoinRef = useRef(autoJoin);
  const speechRef = useRef<{ stop: () => void } | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const serverIdsRef = useRef(new Set<string>());
  /** Meeting object last written to disk, by id (identity = unchanged). */
  const syncedRef = useRef(new Map<string, MeetingRecord>());
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveChainRef = useRef(Promise.resolve());

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);
  useEffect(() => {
    meetingsRef.current = meetings;
  }, [meetings]);
  useEffect(() => {
    autoJoinRef.current = autoJoin;
    try {
      window.localStorage.setItem(AUTOJOIN_KEY, autoJoin ? "1" : "0");
    } catch {
      // ignore
    }
  }, [autoJoin]);
  useEffect(() => () => speechRef.current?.stop(), []);

  useEffect(() => {
    const controller = new AbortController();
    const cached = readMeetings();
    void (async () => {
      try {
        await meetingStoreApi.migrateV2(
          token,
          sessionKey,
          cached.map(toDiskRecord),
          controller.signal,
        );
        // Paged, full records: no 100-meeting ceiling and one request per
        // page instead of one per meeting.
        const records = await meetingStoreApi.listAll(
          token,
          sessionKey,
          controller.signal,
        );
        const restored = records.map(fromDiskRecord);
        serverIdsRef.current = new Set(restored.map((row) => row.id));
        syncedRef.current = new Map(restored.map((row) => [row.id, row]));
        setMeetings(restored);
        meetingsRef.current = restored;
        setActiveId((current) =>
          restored.some((row) => row.id === current)
            ? current
            : restored[0]?.id ?? null,
        );
        writeMeetings(restored);
        const calendar = await meetingStoreApi.calendar(
          token,
          sessionKey,
          undefined,
          controller.signal,
        );
        const events = Array.isArray(calendar.events)
          ? calendar.events as CalendarEvent[]
          : [];
        setCalendarEvents(events);
        writeIcsEvents(events);
        setSaveError(null);
      } catch (err) {
        if (controller.signal.aborted) return;
        setSaveError(
          tx(
            "meeting.persistence.offline",
            "Disk storage is unavailable. The local browser cache is open in offline mode.",
          ) + (err instanceof ApiError && err.message ? ` ${err.message}` : ""),
        );
      } finally {
        if (!controller.signal.aborted) setHydrated(true);
      }
    })();
    return () => controller.abort();
  }, [sessionKey, token, tx]);

  useEffect(() => {
    if (!hydrated) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      const snapshot = meetingsRef.current;
      // Only what changed: records are immutable, so a meeting whose object
      // is the one last synced has nothing new. Sending every meeting on each
      // keystroke re-uploaded every transcript the desk ever recorded.
      const dirty = snapshot.filter((meeting) => syncedRef.current.get(meeting.id) !== meeting);
      if (!dirty.length) return;
      saveChainRef.current = saveChainRef.current.then(async () => {
        setSaving(true);
        try {
          for (const meeting of dirty) {
            const disk = toDiskRecord(meeting);
            if (serverIdsRef.current.has(meeting.id)) {
              await meetingStoreApi.update(token, sessionKey, meeting.id, {
                meta: disk.meta,
                transcript: disk.transcript,
                notes: disk.notes,
                summary: disk.summary,
                chat: disk.chat,
              });
            } else {
              await meetingStoreApi.create(token, sessionKey, disk);
              serverIdsRef.current.add(meeting.id);
            }
            syncedRef.current.set(meeting.id, meeting);
          }
          setSaveError(null);
        } catch (err) {
          setSaveError(
            err instanceof ApiError && err.message
              ? err.message
              : tx(
                  "meeting.persistence.saveFailed",
                  "Meeting could not be saved to disk.",
                ),
          );
        } finally {
          setSaving(false);
        }
      });
    }, 650);
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, [hydrated, meetings, sessionKey, token, tx]);

  const active = useMemo(
    () => meetings.find((row) => row.id === activeId) ?? null,
    [activeId, meetings],
  );
  const template = findMeetingTemplate(active?.templateId);
  const filteredMeetings = useMemo(() => {
    return meetings.filter((row) => meetingMatches(row, query));
  }, [meetings, query]);
  const upcoming = useMemo(
    () => upcomingEvents(calendarEvents, { withinHours: 72 }),
    [calendarEvents],
  );
  const auditRows = useMemo(
    () => (active ? auditForMeeting(active.id) : []),
    [active, meetings, showAudit],
  );

  const persist = useCallback((next: MeetingRecord[]) => {
    setMeetings(next);
    meetingsRef.current = next;
    if (!writeMeetings(next)) {
      setCacheError(
        tx(
          "meeting.persistence.cacheQuota",
          "The offline cache is full. Disk saving will continue, but export an emergency backup now.",
        ),
      );
    }
  }, [tx]);

  const updateActive = useCallback(
    (patch: Partial<MeetingRecord>) => {
      const id = activeIdRef.current;
      if (!id) return;
      const next = meetingsRef.current.map((row) =>
        row.id === id ? { ...row, ...patch, updatedAt: nowIso() } : row,
      );
      persist(next);
    },
    [persist],
  );

  const track = useCallback(
    (action: string, detail?: string, meetingId?: string) => {
      const id = meetingId || activeIdRef.current;
      if (!id) return;
      appendAudit({ meetingId: id, action, detail });
    },
    [],
  );

  // Packaged desktop builds (exe / dmg / AppImage) run in a WebView where
  // window.open and target=_blank are blocked, so the gateway launches the OS
  // browser; the popup path only serves plain browser tabs.
  const openConference = useCallback(
    (url: string) => {
      const fallback = () => {
        if (window.open(url, "_blank", "noopener,noreferrer")) return;
        setError(
          tx(
            "meeting.calendar.openFailed",
            "Could not open the link: {{url}}",
            {
              url,
            },
          ),
        );
      };
      void openExternalUrl(token, url)
        .then((result) => {
          if (!result.opened) fallback();
        })
        .catch(fallback);
    },
    [token, tx],
  );

  const createMeeting = useCallback(
    (partial?: Partial<MeetingRecord>): string => {
      const row: MeetingRecord = {
        id: newId(),
        title: partial?.title || defaultTitle(i18n.language),
        createdAt: nowIso(),
        updatedAt: nowIso(),
        transcript: partial?.transcript || "",
        notes: partial?.notes || "",
        summary: partial?.summary || "",
        templateId: partial?.templateId || "standard",
        speakers: partial?.speakers || [],
        calendarUid: partial?.calendarUid,
        chatLog: partial?.chatLog || "",
      };
      persist([row, ...meetingsRef.current]);
      setActiveId(row.id);
      activeIdRef.current = row.id;
      setError(null);
      track("meeting.created", row.title, row.id);
      return row.id;
    },
    [i18n.language, persist, track],
  );

  // Meeting pending deletion; the transcript is irreplaceable, so the trash
  // button asks for confirmation instead of deleting on a stray click.
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const deleteMeeting = useCallback(
    (id: string) => {
      track("meeting.deleted", undefined, id);
      const next = meetingsRef.current.filter((row) => row.id !== id);
      persist(next);
      setActiveId(next[0]?.id ?? null);
      if (serverIdsRef.current.has(id)) {
        void meetingStoreApi.delete(token, sessionKey, id).then(() => {
          serverIdsRef.current.delete(id);
        }).catch((err: unknown) => {
          setSaveError(
            err instanceof ApiError && err.message
              ? err.message
              : tx(
                  "meeting.persistence.deleteFailed",
                  "The meeting was hidden locally but could not be deleted from disk.",
                ),
          );
        });
      }
    },
    [persist, sessionKey, token, track, tx],
  );

  const appendTranscript = useCallback(
    (text: string, offsetMs?: number, meetingId?: string | null) => {
      const cleaned = text.trim();
      // A capture pins the meeting it started in: switching the selection
      // mid-recording used to send the words into the newly selected meeting.
      const id = meetingId ?? activeIdRef.current;
      if (!cleaned || !id) return;
      // The capture knows when this chunk started inside the meeting, so every
      // appended block carries a [mm:ss] timecode the analysis can use.
      const stamped =
        typeof offsetMs === "number" && Number.isFinite(offsetMs)
          ? `[${formatTimecode(offsetMs)}] ${cleaned}`
          : cleaned;
      const current = meetingsRef.current.find((row) => row.id === id);
      const base = current?.transcript?.trim() ?? "";
      const nextTranscript = base ? `${base}\n${stamped}` : stamped;
      persist(
        meetingsRef.current.map((row) =>
          row.id === id
            ? { ...row, transcript: nextTranscript, updatedAt: nowIso() }
            : row,
        ),
      );
      track("transcript.appended", `${cleaned.length} chars`, id);
    },
    [persist, track],
  );

  /** Add measured audio time to the meeting so duration is real, not guessed. */
  const addAudioDuration = useCallback(
    (ms: number, meetingId?: string | null) => {
      const id = meetingId ?? activeIdRef.current;
      const seconds = Math.round(ms / 1000);
      if (!id || seconds <= 0) return;
      persist(
        meetingsRef.current.map((row) =>
          row.id === id
            ? {
                ...row,
                durationSec: (row.durationSec || 0) + seconds,
                updatedAt: nowIso(),
              }
            : row,
        ),
      );
    },
    [persist],
  );

  const describeSttError = useCallback(
    (err: unknown): string => describeMeetingSttError(err, tx),
    [tx],
  );

  const saveAudioData = useCallback(
    async (
      dataUrl: string,
      metadata: Record<string, unknown>,
      meetingId?: string | null,
    ) => {
      const id = meetingId ?? activeIdRef.current;
      if (!id) return;
      try {
        const segment = await meetingStoreApi.saveAudio(
          token,
          sessionKey,
          id,
          dataUrl,
          metadata,
        );
        // The player lists the segments of the meeting on screen only.
        if (activeIdRef.current === id) {
          setAudioSegments((current) => [
            ...current.filter((row) => row.id !== segment.id),
            { ...segment, url: dataUrl },
          ]);
        }
        setSaveError(null);
      } catch (err) {
        setSaveError(
          err instanceof ApiError && err.message
            ? err.message
            : tx(
                "meeting.persistence.audioFailed",
                "The audio segment could not be saved to disk.",
              ),
        );
      }
    },
    [sessionKey, token, tx],
  );

  /** Patch one meeting by id (updateActive only knows the selected one). */
  const patchMeeting = useCallback(
    (id: string, patch: Partial<MeetingRecord>) => {
      persist(
        meetingsRef.current.map((row) =>
          row.id === id ? { ...row, ...patch, updatedAt: nowIso() } : row,
        ),
      );
    },
    [persist],
  );

  const transcribeBlob = useCallback(
    async (blob: Blob, durationMs: number, meetingId?: string | null) => {
      if (!onTranscribeAudio) {
        setError(
          tx(
            "meeting.errors.sttUnavailable",
            "Speech-to-text is not available. Configure STT under Settings -> Voice.",
          ),
        );
        return;
      }
      const id = meetingId ?? activeIdRef.current;
      if (!id) return;
      setBusy(true);
      setError(null);
      try {
        const dataUrl = wantsWav
          ? await convertBlobToWav(blob)
          : await blobToDataUrl(blob);
        const baseMs =
          (meetingsRef.current.find((row) => row.id === id)?.durationSec || 0) * 1000;
        await saveAudioData(
          dataUrl,
          {
            mime: dataUrl.slice(5, dataUrl.indexOf(";")) || blob.type,
            duration_ms: durationMs,
            offset_ms: baseMs,
          },
          id,
        );
        const text = await onTranscribeAudio(dataUrl, { durationMs });
        appendTranscript(text, baseMs, id);
        if (
          NATIVE_DIARIZATION_PROVIDERS.has(sttProvider)
          && transcriptHasSpeakerLabels(text)
        ) {
          patchMeeting(id, { diarizationSource: "provider_native" });
        }
        if (durationMs > 0) addAudioDuration(durationMs, id);
      } catch (err) {
        setError(describeSttError(err));
      } finally {
        setBusy(false);
      }
    },
    [
      addAudioDuration,
      appendTranscript,
      describeSttError,
      onTranscribeAudio,
      patchMeeting,
      saveAudioData,
      sttProvider,
      tx,
      wantsWav,
    ],
  );

  const flushLiveAudio = useCallback(
    async (segment: LiveSegment, opts: { final: boolean }) => {
      if (!onTranscribeAudio || segment.chunks.length === 0) return;
      setBusy(true);
      try {
        const blob = new Blob(segment.chunks, { type: segment.mimeType });
        const startFrame = segment.transcribedFrames;
        const slice = await incrementalWavFromBlob(blob, {
          startFrame,
          sampleRate: 16_000,
          minDurationMs: opts.final ? 400 : 1_200,
        });
        if (!slice) return;
        segment.transcribedFrames = slice.nextFrame;
        // 16 kHz frames: frame / 16 = milliseconds inside this recorder
        // segment; baseMs anchors the segment inside the whole meeting.
        const offsetMs = segment.baseMs + startFrame / 16;
        const text = await onTranscribeAudio(slice.dataUrl, {
          durationMs: slice.durationMs,
        });
        await saveAudioData(
          slice.dataUrl,
          {
            mime: "audio/wav",
            duration_ms: slice.durationMs,
            offset_ms: offsetMs,
          },
          segment.meetingId,
        );
        appendTranscript(text, offsetMs, segment.meetingId);
        if (
          NATIVE_DIARIZATION_PROVIDERS.has(sttProvider)
          && transcriptHasSpeakerLabels(text)
        ) {
          patchMeeting(segment.meetingId, { diarizationSource: "provider_native" });
        }
        if (opts.final) setError(null);
      } catch (err) {
        const detail = meetingSttErrorDetail(err).toLowerCase();
        if (detail === "empty" && !opts.final) return;
        setError(describeSttError(err));
      } finally {
        setBusy(false);
      }
    },
    [
      appendTranscript,
      describeSttError,
      onTranscribeAudio,
      patchMeeting,
      saveAudioData,
      sttProvider,
    ],
  );

  const enqueueFlush = useCallback(
    (segment: LiveSegment, opts: { final: boolean }) => {
      flushChainRef.current = flushChainRef.current
        .then(() => flushLiveAudio(segment, opts))
        .catch(() => flushLiveAudio(segment, opts));
      return flushChainRef.current;
    },
    [flushLiveAudio],
  );

  const clearRotateTimer = useCallback(() => {
    if (rotateTimerRef.current) {
      clearTimeout(rotateTimerRef.current);
      rotateTimerRef.current = null;
    }
  }, []);

  const attachRecorder = useCallback(
    (stream: MediaStream) => {
      const meetingId = recordingMeetingIdRef.current ?? activeIdRef.current;
      if (!meetingId) return;
      const mimeType = pickMimeType();
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      // Each recorder owns its segment. The flush of a finished segment then
      // runs while the next recorder is already capturing: the old design
      // reset shared buffers and waited for the STT round trip before
      // re-arming, which lost every word spoken during that wait at each
      // 90 s rotation.
      const segment: LiveSegment = {
        meetingId,
        chunks: [],
        transcribedFrames: 0,
        mimeType: recorder.mimeType || mimeType || "audio/webm",
        // Anchor this recorder segment: audio captured in earlier sessions
        // plus time already elapsed in this one (rotations land mid-meeting).
        baseMs:
          recordBaseMsRef.current +
          (startedAtRef.current ? Date.now() - startedAtRef.current : 0),
      };
      const chunkMs = Math.max(
        1_000,
        Math.min(DEFAULT_CHUNK_MS, Math.max(1, maxDurationSec - 1) * 1000),
      );
      recorder.ondataavailable = (event) => {
        if (event.data.size <= 0) return;
        segment.chunks.push(event.data);
        void enqueueFlush(segment, { final: false });
      };
      recorder.onstop = () => {
        const finalFlush = enqueueFlush(segment, { final: true });
        if (rotatingRef.current && streamRef.current) {
          rotatingRef.current = false;
          attachRecorder(streamRef.current);
          return;
        }
        // Real stop: release the devices now, do not wait for the network.
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        for (const captured of captureStreamsRef.current) {
          captured.getTracks().forEach((track) => track.stop());
        }
        captureStreamsRef.current = [];
        void audioCtxRef.current?.close().catch(() => {});
        audioCtxRef.current = null;
        mediaRecorderRef.current = null;
        if (tickRef.current) {
          clearInterval(tickRef.current);
          tickRef.current = null;
        }
        clearRotateTimer();
        setRecording(false);
        if (startedAtRef.current) {
          addAudioDuration(Date.now() - startedAtRef.current, meetingId);
          startedAtRef.current = 0;
        }
        recordingMeetingIdRef.current = null;
        void finalFlush.then(() => maybeAutoIdentifyRef.current(meetingId));
      };
      mediaRecorderRef.current = recorder;
      recorder.start(chunkMs);
      clearRotateTimer();
      rotateTimerRef.current = setTimeout(() => {
        if (mediaRecorderRef.current?.state === "recording") {
          rotatingRef.current = true;
          mediaRecorderRef.current.requestData();
          mediaRecorderRef.current.stop();
        }
      }, ROTATE_MS);
    },
    [addAudioDuration, clearRotateTimer, enqueueFlush, maxDurationSec],
  );

  const stopRecording = useCallback(() => {
    rotatingRef.current = false;
    clearRotateTimer();
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.requestData();
      recorder.stop();
    }
    track("recording.stopped");
  }, [clearRotateTimer, track]);

  const startRecording = useCallback(
    async (source: "mic" | "conference" = "mic") => {
      // Pin the capture to this meeting for its whole life: every chunk,
      // timecode and audio segment lands here even if the user browses other
      // meetings in the list meanwhile.
      const meetingId = activeIdRef.current ?? createMeeting();
      setError(null);
      // getUserMedia is only exposed in a secure context (https or localhost /
      // 127.0.0.1). Opening via a WSL LAN IP (http://172.x.x.x) hides the API
      // entirely - that is not a Navin limitation.
      if (typeof window !== "undefined" && !window.isSecureContext) {
        setError(
          tx(
            "meeting.errors.insecureContext",
            "Microphone needs localhost or HTTPS. Open Navin at http://localhost:5173 (or the desktop app), or import an audio file.",
          ),
        );
        return;
      }
      if (
        !navigator.mediaDevices?.getUserMedia ||
        typeof MediaRecorder === "undefined"
      ) {
        setError(
          tx(
            "meeting.errors.unsupported",
            "Microphone recording is not available here. Open the desktop app or http://localhost:5173, or import an audio file.",
          ),
        );
        return;
      }
      if (source === "conference" && !navigator.mediaDevices.getDisplayMedia) {
        // Neither WKWebView nor WebKitGTK implements getDisplayMedia, so the
        // desktop app on macOS and Linux can never capture tab audio. Telling
        // those users to "use Chrome or Edge" would be nonsense: they are not
        // in a browser. Point them at the microphone instead.
        setError(
          isDesktopShell()
            ? tx(
                "meeting.conference.unsupportedDesktop",
                "This window cannot capture the meeting audio. Record with the microphone next to the speakers, or import the recording afterwards.",
              )
            : tx(
                "meeting.conference.unsupported",
                "Tab audio capture is not available in this browser. Use Chrome or Edge, or record with the microphone next to the speakers.",
              ),
        );
        return;
      }
      try {
        let stream: MediaStream;
        if (source === "conference") {
          // Everyone in the call is heard through the meeting tab, so grab
          // the tab audio; the user's own voice never plays back in that tab,
          // so mix the microphone in as well when it is available.
          const display = await navigator.mediaDevices.getDisplayMedia({
            video: true,
            audio: true,
          });
          if (!display.getAudioTracks().length) {
            display.getTracks().forEach((track) => track.stop());
            setError(
              tx(
                "meeting.conference.noTabAudio",
                'No audio in the shared tab. Pick the meeting tab in the "Chrome tab" list and tick "Also share tab audio".',
              ),
            );
            return;
          }
          let mic: MediaStream | null = null;
          try {
            mic = await navigator.mediaDevices.getUserMedia({ audio: true });
          } catch {
            mic = null;
          }
          const AudioContextCtor = audioContextConstructor();
          if (!AudioContextCtor) {
            display.getTracks().forEach((track) => track.stop());
            setError(
              tx(
                "meeting.conference.noAudioContext",
                "This window has no Web Audio support, so the meeting and microphone tracks cannot be mixed.",
              ),
            );
            return;
          }
          const ctx = new AudioContextCtor();
          const mixed = ctx.createMediaStreamDestination();
          ctx.createMediaStreamSource(display).connect(mixed);
          if (mic) ctx.createMediaStreamSource(mic).connect(mixed);
          audioCtxRef.current = ctx;
          captureStreamsRef.current = mic ? [display, mic] : [display];
          // Ending the share from the browser bar must stop the recording.
          for (const track of display.getTracks()) {
            track.addEventListener("ended", () => stopRecording(), {
              once: true,
            });
          }
          stream = mixed.stream;
        } else {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        }
        streamRef.current = stream;
        startedAtRef.current = Date.now();
        const current = meetingsRef.current.find((row) => row.id === meetingId);
        // Timecodes continue after previous sessions instead of restarting at 0.
        recordBaseMsRef.current = (current?.durationSec || 0) * 1000;
        if (current && !current.startedAt) {
          patchMeeting(meetingId, { startedAt: nowIso() });
        }
        recordingMeetingIdRef.current = meetingId;
        attachRecorder(stream);
        setRecording(true);
        setElapsedMs(0);
        track(
          source === "conference"
            ? "conference.capture.started"
            : "recording.started",
        );
        tickRef.current = setInterval(() => {
          setElapsedMs(Date.now() - startedAtRef.current);
        }, 500);
      } catch {
        recordingMeetingIdRef.current = null;
        setError(
          source === "conference"
            ? tx(
                "meeting.conference.cancelled",
                "Capture cancelled: no tab was shared.",
              )
            : tx(
                "meeting.errors.permission",
                "Microphone permission denied or unavailable.",
              ),
        );
      }
    },
    [attachRecorder, createMeeting, patchMeeting, stopRecording, track, tx],
  );

  useEffect(() => () => stopRecording(), [stopRecording]);

  // ---- autonomous meeting bot (headless browser joining the conference) ----
  type BotState =
    | "idle"
    | "starting"
    | "joining"
    | "waiting"
    | "live"
    | "ended"
    | "interrupted"
    | "error";
  const [botState, setBotState] = useState<BotState>("idle");
  const [botError, setBotError] = useState<string | null>(null);
  const [confHelpOpen, setConfHelpOpen] = useState(false);
  const botCursorRef = useRef(0);
  /** Meeting audio time before the bot session, so timecodes stay continuous. */
  const botBaseMsRef = useRef(0);
  const botPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const botActive = ["starting", "joining", "waiting", "live"].includes(
    botState,
  );

  const stopBotPolling = useCallback(() => {
    if (botPollRef.current) {
      clearInterval(botPollRef.current);
      botPollRef.current = null;
    }
  }, []);

  const pollBot = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) return;
    try {
      const payload = await fetchMeetingBot(token, sessionKey, "status", {
        botId: id,
        cursor: botCursorRef.current,
      });
      // The answer may land after the user selected another meeting: the bot
      // id is the meeting id, so everything below targets `id`, not the
      // current selection.
      if (activeIdRef.current !== id) return;
      botCursorRef.current = payload.cursor;
      for (const segment of payload.segments) {
        appendTranscript(segment.text, botBaseMsRef.current + segment.offset_ms, id);
        addAudioDuration(segment.duration_ms, id);
      }
      setBotState(payload.state);
      patchMeeting(id, { botCursor: payload.cursor });
      if (payload.state === "error") setBotError(payload.error || null);
      if (payload.state === "ended" || payload.state === "error") {
        stopBotPolling();
        void maybeAutoIdentifyRef.current(id);
      }
    } catch {
      // Transient poll failure (gateway restart, network blip): keep polling.
    }
  }, [
    addAudioDuration,
    appendTranscript,
    patchMeeting,
    sessionKey,
    stopBotPolling,
    token,
  ]);

  const startBot = useCallback(async () => {
    if (!activeIdRef.current) createMeeting();
    const current = meetingsRef.current.find(
      (row) => row.id === activeIdRef.current,
    );
    const url = current?.conferenceUrl?.trim();
    if (!current || !url) return;
    setBotError(null);
    setError(null);
    setBotState("starting");
    botCursorRef.current = 0;
    botBaseMsRef.current = (current.durationSec || 0) * 1000;
    if (!current.startedAt) updateActive({ startedAt: nowIso() });
    try {
      const payload = await fetchMeetingBot(token, sessionKey, "start", {
        botId: current.id,
        url,
        name: tx("meeting.bot.displayName", "Navin AI"),
        language: i18n.language,
      });
      setBotState(payload.state);
      track("bot.started", url, current.id);
      stopBotPolling();
      botPollRef.current = setInterval(() => void pollBot(), 3000);
    } catch (err) {
      setBotState("error");
      setBotError(
        err instanceof ApiError
          ? err.message
          : tx("meeting.bot.startFailed", "The bot could not start."),
      );
    }
  }, [
    createMeeting,
    i18n.language,
    pollBot,
    sessionKey,
    stopBotPolling,
    token,
    track,
    tx,
    updateActive,
  ]);

  const stopBot = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) return;
    try {
      await fetchMeetingBot(token, sessionKey, "stop", { botId: id });
    } catch {
      // The bot may already be gone (gateway restarted): still stop polling.
    }
    await pollBot();
    stopBotPolling();
    setBotState((prev) => (prev === "error" ? prev : "ended"));
    track("bot.stopped", undefined, id);
  }, [pollBot, sessionKey, stopBotPolling, token, track]);

  useEffect(() => () => stopBotPolling(), [stopBotPolling]);

  const botStateLabel = useMemo(() => {
    switch (botState) {
      case "starting":
        return tx("meeting.bot.state.starting", "Bot starting...");
      case "joining":
        return tx("meeting.bot.state.joining", "Bot joining the call...");
      case "waiting":
        return tx("meeting.bot.state.waiting", "Waiting to be admitted...");
      case "live":
        return tx("meeting.bot.state.live", "Bot in the meeting - transcribing");
      case "ended":
        return tx("meeting.bot.state.ended", "Bot left the meeting");
      case "interrupted":
        return tx(
          "meeting.bot.state.interrupted",
          "Bot session was interrupted when the gateway restarted",
        );
      case "error":
        return tx("meeting.bot.state.error", "Bot failed");
      default:
        return "";
    }
  }, [botState, tx]);

  useEffect(() => {
    if (!hydrated || !activeId) return;
    const current = meetingsRef.current.find((row) => row.id === activeId);
    botCursorRef.current = current?.botCursor || 0;
    botBaseMsRef.current = (current?.durationSec || 0) * 1000;
    void fetchMeetingBot(token, sessionKey, "status", {
      botId: activeId,
      cursor: botCursorRef.current,
    }).then((payload) => {
      botCursorRef.current = payload.cursor;
      setBotState(payload.state);
      setBotError(payload.error || null);
      if (botShouldPoll(payload.state)) {
        stopBotPolling();
        botPollRef.current = setInterval(() => void pollBot(), 3000);
      }
    }).catch(() => {
      setBotState("idle");
      setBotError(null);
    });
    return stopBotPolling;
  }, [
    activeId,
    hydrated,
    pollBot,
    sessionKey,
    stopBotPolling,
    token,
  ]);

  useEffect(() => {
    if (!hydrated || !activeId) {
      setAudioSegments([]);
      return;
    }
    let disposed = false;
    const urls: string[] = [];
    void meetingStoreApi.listAudio(token, sessionKey, activeId).then(
      async ({ segments }) => {
        const loaded = await Promise.all(
          segments.map(async (segment) => {
            const payload = await meetingStoreApi.getAudio(
              token,
              sessionKey,
              activeId,
              segment.id,
            );
            const url = URL.createObjectURL(
              base64ToBlob(payload.data_base64, payload.content_type),
            );
            urls.push(url);
            return { ...segment, url };
          }),
        );
        if (!disposed) setAudioSegments(loaded);
      },
    ).catch(() => {
      if (!disposed) setAudioSegments([]);
    });
    return () => {
      disposed = true;
      for (const url of urls) URL.revokeObjectURL(url);
    };
  }, [activeId, hydrated, sessionKey, token]);

  // Calendar auto-detect: notify when a linked/imported event starts soon.
  useEffect(() => {
    const timer = window.setInterval(() => {
      const soon = eventsStartingSoon(calendarEvents, { withinMinutes: 5 });
      if (!soon.length) return;
      let notified: Record<string, boolean>;
      try {
        notified = JSON.parse(
          window.localStorage.getItem(NOTIFIED_KEY) || "{}",
        ) as Record<string, boolean>;
      } catch {
        notified = {};
      }
      for (const event of soon) {
        if (notified[event.uid]) continue;
        const joinUrl = meetingJoinUrl(event);
        // `notifyUser` always leaves an entry in the in-app centre, so the
        // reminder still lands on macOS and Linux desktop where the webview
        // exposes no OS notification at all.
        void notifyUser({
          title: tx("meeting.calendar.startingTitle", "Meeting starting"),
          body: `${event.summary} · ${formatEventWhen(event.start, i18n.language)}`,
          key: `meeting-start:${event.uid}`,
          ...(joinUrl ? { onActivate: () => openConference(joinUrl) } : {}),
        });
        // Browsers block tab opening outside a user gesture unless the app is
        // focused, hence the opt-in toggle rather than a default.
        if (joinUrl && autoJoinRef.current) openConference(joinUrl);
        notified[event.uid] = true;
        window.localStorage.setItem(NOTIFIED_KEY, JSON.stringify(notified));
      }
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [calendarEvents, i18n.language, openConference, tx]);

  const onImportAudio = useCallback(
    async (file: File | null) => {
      if (!file) return;
      // An hour-long import runs for minutes: pin it to the meeting it was
      // started for, whatever the user selects in the meantime.
      const meetingId = activeIdRef.current ?? createMeeting();
      if (!onTranscribeAudio) {
        setError(
          tx(
            "meeting.errors.sttUnavailable",
            "Speech-to-text is not available. Configure STT under Settings -> Voice.",
          ),
        );
        return;
      }
      setBusy(true);
      setError(null);
      track("audio.imported", file.name, meetingId);

      // A one hour recording exceeds both the duration and the upload caps, so
      // the file is decoded and re-cut locally before hitting the ingress.
      let segments;
      try {
        segments = await segmentAudioBlob(file, {
          segmentSec: Math.max(
            20,
            Math.min(MAX_SEGMENT_SEC, maxDurationSec - 10),
          ),
        });
      } catch {
        await transcribeBlob(file, 0, meetingId);
        setBusy(false);
        return;
      }

      try {
        setProgress({ done: 0, total: segments.length });
        const current = meetingsRef.current.find((row) => row.id === meetingId);
        // Segment offsets are known exactly, so imported audio gets the same
        // [mm:ss] timecodes as a live recording, continuing after prior audio.
        const baseMs = (current?.durationSec || 0) * 1000;
        const offsets: number[] = [];
        let cursorMs = baseMs;
        for (const segment of segments) {
          offsets.push(cursorMs);
          cursorMs += segment.durationMs;
        }
        // Transcribe a few segments at a time (an hour of audio is ~45 STT
        // calls; strictly sequential made imports painfully slow), but append
        // strictly in order so the transcript reads top to bottom.
        const results: (string | null)[] = segments.map(() => null);
        let completed = 0;
        let appendedUpTo = 0;
        const appendReady = () => {
          while (
            appendedUpTo < segments.length &&
            results[appendedUpTo] !== null
          ) {
            appendTranscript(
              results[appendedUpTo] as string,
              offsets[appendedUpTo],
              meetingId,
            );
            appendedUpTo += 1;
          }
        };
        let nextIndex = 0;
        const worker = async () => {
          while (nextIndex < segments.length) {
            const index = nextIndex++;
            const segment = segments[index];
            await saveAudioData(
              segment.dataUrl,
              {
                mime: "audio/wav",
                duration_ms: segment.durationMs,
                offset_ms: offsets[index],
                source_name: file.name,
              },
              meetingId,
            );
            results[index] = await onTranscribeAudio(segment.dataUrl, {
              durationMs: segment.durationMs,
            });
            completed += 1;
            setProgress({ done: completed, total: segments.length });
            appendReady();
          }
        };
        const poolSize = Math.min(3, segments.length);
        await Promise.all(Array.from({ length: poolSize }, () => worker()));
        appendReady();
        if (
          NATIVE_DIARIZATION_PROVIDERS.has(sttProvider)
          && results.some((text) => text && transcriptHasSpeakerLabels(text))
        ) {
          patchMeeting(meetingId, { diarizationSource: "provider_native" });
        }
        addAudioDuration(cursorMs - baseMs, meetingId);
        track("audio.transcribed", `${segments.length} segments`, meetingId);
        void maybeAutoIdentifyRef.current(meetingId);
      } catch (err) {
        setError(describeSttError(err));
      } finally {
        setProgress(null);
        setBusy(false);
      }
    },
    [
      addAudioDuration,
      appendTranscript,
      createMeeting,
      describeSttError,
      maxDurationSec,
      onTranscribeAudio,
      patchMeeting,
      saveAudioData,
      sttProvider,
      track,
      transcribeBlob,
      tx,
    ],
  );

  const onImportIcs = useCallback(
    async (file: File | null) => {
      if (!file) return;
      const text = await file.text();
      const parsed = parseIcsEvents(text);
      if (!parsed.length) {
        setError(
          tx("meeting.calendar.emptyIcs", "No events found in this ICS file."),
        );
        return;
      }
      writeIcsEvents(parsed);
      setCalendarEvents(parsed);
      try {
        await meetingStoreApi.calendar(token, sessionKey, {
          provider: "ics",
          synced: false,
          fallback: "ics",
          events: parsed,
        });
      } catch (err) {
        setSaveError(
          err instanceof ApiError && err.message
            ? err.message
            : tx(
                "meeting.persistence.calendarFailed",
                "Calendar could not be saved to disk.",
              ),
        );
      }
      track(
        "calendar.imported",
        `${parsed.length} events`,
        activeIdRef.current || "calendar",
      );
    },
    [sessionKey, token, track, tx],
  );

  const saveCustomTemplate = useCallback(() => {
    const name = customTemplateName.trim();
    const instructions = customTemplateBody.trim();
    if (!name || !instructions) return;
    const row: MeetingSummaryTemplate = {
      id: `custom-${newId().slice(0, 8)}`,
      name,
      builtin: false,
      instructions,
    };
    const next = [...readCustomTemplates(), row];
    writeCustomTemplates(next);
    setTemplates(allMeetingTemplates());
    setCustomTemplateName("");
    setCustomTemplateBody("");
    if (activeIdRef.current) updateActive({ templateId: row.id });
    track("template.created", name);
  }, [customTemplateBody, customTemplateName, track, updateActive]);

  const buildContextBlock = useCallback(() => {
    if (!active) return "";
    const speakers =
      active.speakers.length > 0
        ? `Speakers: ${active.speakers.join(", ")}`
        : "Speakers: not labeled yet";
    return [
      `Meeting: ${active.title}`,
      `Template: ${template.name}`,
      `Template instructions: ${template.instructions}`,
      speakers,
      active.calendarUid ? `Calendar event uid: ${active.calendarUid}` : null,
      active.notes?.trim() ? `## Notes\n${active.notes.trim()}` : null,
      active.transcript?.trim()
        ? `## Transcript\n${active.transcript.trim()}`
        : null,
      active.summary?.trim()
        ? `## Existing summary\n${active.summary.trim()}`
        : null,
      active.chatLog?.trim() ? `## Prior Q&A\n${active.chatLog.trim()}` : null,
    ]
      .filter(Boolean)
      .join("\n");
  }, [active, template]);

  // Seeded prompts land in the chat composer, which focus mode hides: leaving
  // focus is what makes the action visibly do something.
  const revealChat = useCallback(() => {
    if (focusMode) onToggleFocus?.();
  }, [focusMode, onToggleFocus]);

  const seedAction = useCallback(
    (action: MeetingAction) => {
      if (!onSeed || !active) return;
      if (!active.transcript.trim() && !active.notes.trim()) {
        setError(
          tx(
            "meeting.errors.needTranscript",
            "Add a transcript or notes before running an action.",
          ),
        );
        return;
      }
      const slug = slugify(active.title);
      const body = [
        `/meeting ${action.prompt}`,
        "",
        `Save under meetings/${slug}/ and ship meeting-report-*.html.`,
        "Prefer high-accuracy reading of the transcript; do not invent speakers or quotes.",
        "",
        buildContextBlock(),
        "",
      ].join("\n");
      onSeed(body);
      revealChat();
      track("action.seeded", action.id);
    },
    [active, buildContextBlock, onSeed, revealChat, track, tx],
  );

  const seedAsk = useCallback(() => {
    if (!onSeed || !active) return;
    const question = askDraft.trim();
    if (!question) return;
    if (!active.transcript.trim() && !active.notes.trim()) {
      setError(
        tx(
          "meeting.errors.needTranscript",
          "Add a transcript or notes before running an action.",
        ),
      );
      return;
    }
    const slug = slugify(active.title);
    onSeed(
      [
        `/meeting Chat with this meeting only. Answer the user question from the transcript/notes.`,
        `If unknown, say so. Append Q&A to meetings/${slug}/chat.md and refresh meeting-report-*.html.`,
        "",
        `Question: ${question}`,
        "",
        buildContextBlock(),
        "",
      ].join("\n"),
    );
    const line = `Q: ${question}`;
    updateActive({
      chatLog: active.chatLog?.trim()
        ? `${active.chatLog.trim()}\n${line}`
        : line,
    });
    setAskDraft("");
    revealChat();
    track("chat.asked", question.slice(0, 120));
  }, [
    active,
    askDraft,
    buildContextBlock,
    onSeed,
    revealChat,
    track,
    tx,
    updateActive,
  ]);

  const reportLabels: ReportLabels = useMemo(
    () => ({
      report: tx("meeting.report.title", "Meeting report"),
      metadata: tx("meeting.report.metadata", "Metadata"),
      summary: tx("meeting.report.summary", "Summary"),
      highlights: tx("meeting.report.highlights", "Decisions and actions"),
      transcript: tx("meeting.tabs.transcript", "Transcript"),
      notes: tx("meeting.notes", "Private notes"),
      speakers: tx("meeting.report.speakers", "Speakers"),
      questions: tx("meeting.report.questions", "Open questions"),
      template: tx("meeting.template", "Template"),
      date: tx("meeting.report.date", "Meeting date"),
      created: tx("meeting.report.created", "Created"),
      updated: tx("meeting.report.updated", "Updated"),
      words: tx("meeting.report.words", "Words"),
      duration: tx("meeting.report.duration", "Spoken time"),
      language: tx("meeting.report.language", "Language"),
      calendar: tx("meeting.report.calendar", "Calendar event"),
      kind: tx("meeting.report.kind", "Type"),
      detail: tx("meeting.report.detail", "Detail"),
      owner: tx("meeting.report.owner", "Owner"),
      due: tx("meeting.report.due", "Deadline"),
      decision: tx("meeting.report.decision", "Decision"),
      action: tx("meeting.report.action", "Action"),
      proposal: tx("meeting.report.proposal", "Proposal"),
      opinion: tx("meeting.report.opinion", "Opinion"),
      risk: tx("meeting.report.risk", "Risk"),
      question: tx("meeting.report.question", "Question"),
      unassigned: tx("meeting.report.unassigned", "unassigned"),
      noDue: tx("meeting.report.noDue", "no deadline"),
      none: tx("meeting.report.none", "none"),
      generatedBy: tx(
        "meeting.report.generatedBy",
        "Produced locally by Navin Meeting.",
      ),
    }),
    [tx],
  );

  const report: MeetingReport | null = useMemo(() => {
    if (!active) return null;
    return analyzeMeeting({
      title: active.title,
      createdAt: active.createdAt,
      updatedAt: active.updatedAt,
      transcript: active.transcript,
      notes: active.notes,
      summary: active.summary ?? "",
      speakers: active.speakers,
      templateName: template.name,
      calendarUid: active.calendarUid,
      chatLog: active.chatLog,
      startedAt: active.startedAt ?? null,
      durationSec: active.durationSec ?? null,
    });
  }, [active, template.name]);

  const synthesisError = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.message) return err.message;
      return tx("meeting.errors.reportFailed", "Could not build the report.");
    },
    [tx],
  );

  /** Minutes are computed in one model call and rendered here, not in the chat. */
  const generateReport = useCallback(async () => {
    if (!active) return;
    if (!active.transcript.trim() && !active.notes.trim()) {
      setError(
        tx(
          "meeting.errors.needTranscript",
          "Add a transcript or notes before running an action.",
        ),
      );
      return;
    }
    setError(null);
    setGenerating(true);
    try {
      const payload = await fetchMeetingReport(token, sessionKey, {
        title: active.title,
        templateName: template.name,
        templateInstructions: template.instructions,
        transcript: active.transcript,
        notes: active.notes,
        speakers: active.speakers,
        language: i18n.language,
        meetingDate: active.startedAt || active.createdAt,
        durationMin: active.durationSec
          ? Math.max(1, Math.round(active.durationSec / 60))
          : undefined,
      });
      updateActive({ summary: payload.markdown });
      setReportModel(payload.model ?? null);
      setTab("report");
      track("report.generated", payload.model ?? "");
    } catch (err) {
      setError(synthesisError(err));
    } finally {
      setGenerating(false);
    }
  }, [
    active,
    i18n.language,
    sessionKey,
    synthesisError,
    template.instructions,
    template.name,
    token,
    track,
    tx,
    updateActive,
  ]);

  /**
   * Diarization pass: the model splits the transcript into turns and adopts a
   * real name only where the transcript states one. Renaming afterwards is the
   * user's job, and it rewrites every turn of that speaker.
   */
  const identifySpeakers = useCallback(async (meetingId?: string | null) => {
    const id = meetingId ?? activeIdRef.current;
    const current = meetingsRef.current.find((row) => row.id === id);
    if (!current) return;
    if (!current.transcript.trim()) {
      setError(
        tx(
          "meeting.errors.needTranscript",
          "Add a transcript or notes before running an action.",
        ),
      );
      return;
    }
    setError(null);
    setLabelling(true);
    try {
      const payload = await fetchMeetingSpeakers(token, sessionKey, {
        transcript: current.transcript,
        speakers: current.speakers,
        language: i18n.language,
      });
      const roster = payload.speakers?.length
        ? payload.speakers
        : rosterFromTranscript(payload.markdown, current.speakers);
      // Labelling takes a while; write back to the meeting it was run on,
      // not to whichever one is selected when the answer arrives.
      patchMeeting(current.id, {
        transcript: payload.markdown,
        speakers: roster,
        diarizationSource: "llm_fallback",
      });
      if (activeIdRef.current === current.id) setEditTranscript(false);
      if (payload.truncated) {
        setError(
          tx(
            "meeting.errors.speakersTruncated",
            "The transcript was too long to label in full. The tail was left as captured.",
          ),
        );
      }
      track("speakers.identified", roster.join(", "), current.id);
    } catch (err) {
      setError(synthesisError(err));
    } finally {
      setLabelling(false);
    }
  }, [
    i18n.language,
    patchMeeting,
    sessionKey,
    synthesisError,
    token,
    track,
    tx,
  ]);

  const maybeAutoIdentify = useCallback(async (meetingId?: string | null) => {
    const id = meetingId ?? activeIdRef.current;
    const current = meetingsRef.current.find((row) => row.id === id);
    if (!current?.transcript.trim()) return;
    if (transcriptHasSpeakerLabels(current.transcript)) return;
    await identifySpeakers(current.id);
  }, [identifySpeakers]);

  maybeAutoIdentifyRef.current = maybeAutoIdentify;

  /** Renaming a label rewrites the transcript so exports stay consistent. */
  const applySpeakerRename = useCallback(
    (from: string, to: string) => {
      if (!active) return;
      const next = to.trim();
      if (!next || next === from) return;
      updateActive({
        transcript: renameSpeaker(active.transcript, from, next),
        speakers: active.speakers.map((name) => (name === from ? next : name)),
      });
      track("speakers.renamed", `${from} -> ${next}`);
    },
    [active, track, updateActive],
  );

  const askMeeting = useCallback(async () => {
    if (!active) return;
    const question = askDraft.trim();
    if (!question) return;
    if (!active.transcript.trim() && !active.notes.trim()) {
      setError(
        tx(
          "meeting.errors.needTranscript",
          "Add a transcript or notes before running an action.",
        ),
      );
      return;
    }
    setError(null);
    setAnswering(true);
    try {
      const payload = await fetchMeetingAnswer(token, sessionKey, {
        question,
        title: active.title,
        transcript: active.transcript,
        notes: active.notes,
        summary: active.summary ?? "",
        speakers: active.speakers,
        language: i18n.language,
      });
      setAnswer(payload.markdown);
      const entry = `Q: ${question}\nA: ${payload.markdown}`;
      updateActive({
        chatLog: active.chatLog?.trim()
          ? `${active.chatLog.trim()}\n\n${entry}`
          : entry,
      });
      setAskDraft("");
      track("chat.answered", question.slice(0, 120));
    } catch (err) {
      setError(synthesisError(err));
    } finally {
      setAnswering(false);
    }
  }, [
    active,
    askDraft,
    i18n.language,
    sessionKey,
    synthesisError,
    token,
    track,
    tx,
    updateActive,
  ]);

  const exportMarkdownPack = useCallback(() => {
    if (!active || !report) return;
    downloadTextFile(
      `${slugify(active.title)}.md`,
      buildReportMarkdown(report, reportLabels, i18n.language),
      "text/markdown;charset=utf-8",
    );
    track("export.markdown");
  }, [active, i18n.language, report, reportLabels, track]);

  const exportHtmlReport = useCallback(() => {
    if (!active || !report) return;
    downloadTextFile(
      `${slugify(active.title)}.html`,
      buildReportHtml(report, reportLabels, i18n.language),
      "text/html;charset=utf-8",
    );
    track("export.html");
  }, [active, i18n.language, report, reportLabels, track]);

  /** Print the standalone report, which is how the browser makes the PDF. */
  const printReport = useCallback(() => {
    if (!active || !report) return;
    const html = buildReportHtml(report, reportLabels, i18n.language);
    const frame = document.createElement("iframe");
    frame.setAttribute("aria-hidden", "true");
    frame.style.position = "fixed";
    frame.style.right = "0";
    frame.style.bottom = "0";
    frame.style.width = "0";
    frame.style.height = "0";
    frame.style.border = "0";
    document.body.appendChild(frame);
    const cleanup = () => {
      window.setTimeout(() => frame.remove(), 1000);
    };
    frame.onload = () => {
      try {
        frame.contentWindow?.focus();
        frame.contentWindow?.print();
      } catch {
        setError(
          tx(
            "meeting.errors.printFailed",
            "Printing is unavailable here. Download the HTML report and open it to print or export as PDF.",
          ),
        );
      } finally {
        cleanup();
      }
    };
    frame.srcdoc = html;
    track("export.print");
  }, [active, i18n.language, report, reportLabels, track, tx]);

  const toggleSpeech = useCallback(() => {
    if (speechRef.current) {
      speechRef.current.stop();
      speechRef.current = null;
      setSpeaking(false);
      return;
    }
    if (!onSpeak || !active) return;
    const text = [active.summary, active.notes, active.transcript]
      .map((value) => value?.trim() ?? "")
      .find(Boolean);
    if (!text) {
      setError(
        tx(
          "meeting.errors.needTranscript",
          "Add a transcript or notes before running an action.",
        ),
      );
      return;
    }
    setError(null);
    setSpeaking(true);
    const handle = onSpeak(text);
    speechRef.current = handle;
    track("summary.spoken");
    void handle.done
      .catch((err: unknown) => {
        const detail = err instanceof Error ? err.message : "";
        setError(
          detail === "plan_required"
            ? tx(
                "meeting.errors.ttsPlan",
                "Voice output needs a Pro, Ultra, or Team plan.",
              )
            : tx("meeting.errors.ttsFailed", "Could not read the text aloud."),
        );
      })
      .finally(() => {
        speechRef.current = null;
        setSpeaking(false);
      });
  }, [active, onSpeak, track, tx]);

  const downloadBinary = useCallback(
    (filename: string, contentType: string, dataBase64: string) => {
      const url = URL.createObjectURL(base64ToBlob(dataBase64, contentType));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    [],
  );

  const exportDocx = useCallback(async () => {
    if (!active) return;
    setBusy(true);
    setError(null);
    try {
      const payload = await meetingStoreApi.docx(token, sessionKey, {
        title: active.title,
        report: active.summary || "",
        transcript: active.transcript,
        notes: active.notes,
      });
      downloadBinary(
        payload.filename,
        payload.content_type,
        payload.data_base64,
      );
      track("export.docx");
    } catch (err) {
      setError(synthesisError(err));
    } finally {
      setBusy(false);
    }
  }, [
    active,
    downloadBinary,
    sessionKey,
    synthesisError,
    token,
    track,
  ]);

  const [filingNote, setFilingNote] = useState(false);
  const [noteToast, setNoteToast] = useState<string | null>(null);

  const saveToNotes = useCallback(async () => {
    if (!active) return;
    const meetingId = active.id;
    setFilingNote(true);
    setError(null);
    try {
      // The disk copy must carry the latest summary before the bridge reads
      // it: an autosave may still be pending in the debounce window.
      const pending = meetingsRef.current.find((row) => row.id === meetingId);
      if (pending && syncedRef.current.get(meetingId) !== pending) {
        const disk = toDiskRecord(pending);
        if (serverIdsRef.current.has(meetingId)) {
          await meetingStoreApi.update(token, sessionKey, meetingId, {
            meta: disk.meta,
            transcript: disk.transcript,
            notes: disk.notes,
            summary: disk.summary,
            chat: disk.chat,
          });
        } else {
          await meetingStoreApi.create(token, sessionKey, disk);
          serverIdsRef.current.add(meetingId);
        }
        syncedRef.current.set(meetingId, pending);
      }
      const link = await meetingStoreApi.toNote(token, sessionKey, meetingId);
      patchMeeting(meetingId, { noteId: link.note_id });
      setNoteToast(
        link.created
          ? tx("meeting.noteLink.created", "Note created: {{title}} ({{count}} action items)", {
              title: link.title,
              count: link.action_items,
            })
          : tx("meeting.noteLink.updated", "Note updated: {{title}} ({{count}} action items)", {
              title: link.title,
              count: link.action_items,
            }),
      );
      track("export.note");
    } catch (err) {
      setError(synthesisError(err));
    } finally {
      setFilingNote(false);
    }
  }, [active, patchMeeting, sessionKey, synthesisError, token, track, tx]);

  useEffect(() => {
    if (!noteToast) return;
    const timer = window.setTimeout(() => setNoteToast(null), 6000);
    return () => window.clearTimeout(timer);
  }, [noteToast]);

  const exportEmergency = useCallback(async () => {
    try {
      const payload = await meetingStoreApi.emergencyExport(token, sessionKey);
      downloadBinary(
        payload.filename,
        payload.content_type,
        payload.data_base64,
      );
    } catch (err) {
      const fallback = new Blob(
        [JSON.stringify({ meetings: meetingsRef.current }, null, 2)],
        { type: "application/json" },
      );
      const url = URL.createObjectURL(fallback);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "navin-meetings-offline-emergency.json";
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setError(
        tx(
          "meeting.persistence.exportFallback",
          "Disk export was unavailable. A browser-cache emergency backup was downloaded instead.",
        ) + (err instanceof ApiError && err.message ? ` ${err.message}` : ""),
      );
    }
  }, [downloadBinary, sessionKey, token, tx]);

  const translateMeeting = useCallback(
    async (kind: "transcript" | "report") => {
      if (!active) return;
      const text = kind === "transcript"
        ? active.transcript
        : active.summary || "";
      if (!text.trim()) return;
      setBusy(true);
      setError(null);
      try {
        const payload = await meetingStoreApi.translate(
          token,
          sessionKey,
          kind,
          text,
          translationLanguage,
        );
        updateActive(
          kind === "transcript"
            ? {
                translatedTranscript: payload.text,
                translationLanguage,
              }
            : {
                translatedSummary: payload.text,
                translationLanguage,
              },
        );
        track(`translate.${kind}`, translationLanguage);
      } catch (err) {
        setError(synthesisError(err));
      } finally {
        setBusy(false);
      }
    },
    [
      active,
      sessionKey,
      synthesisError,
      token,
      track,
      translationLanguage,
      updateActive,
    ],
  );

  const cleanupTranscript = useCallback(async () => {
    if (!active?.transcript.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const payload = await meetingStoreApi.cleanup(
        token,
        sessionKey,
        active.transcript,
        i18n.language,
      );
      updateActive({
        transcript: payload.text,
        diarizationSource: payload.acoustic_diarization
          ? "provider_native"
          : active.diarizationSource || "unknown",
      });
      track("transcript.high_accuracy", payload.accuracy);
    } catch (err) {
      setError(synthesisError(err));
    } finally {
      setBusy(false);
    }
  }, [
    active,
    i18n.language,
    sessionKey,
    synthesisError,
    token,
    track,
    updateActive,
  ]);

  const seekAudio = useCallback(
    (seconds: number) => {
      const target = audioSeekTarget(audioSegments, seconds);
      const player = audioRef.current;
      if (!target || !player) return;
      if (player.src !== target.segment.url) player.src = target.segment.url;
      player.currentTime = target.relativeSeconds;
      void player.play();
    },
    [audioSegments],
  );

  // No "Google Calendar" / "Outlook" buttons: the gateway has no OAuth
  // adapter for either, so they could only ever answer "not ready". The .ics
  // import above is the real path and both providers export it.

  const actions: MeetingAction[] = useMemo(
    () => [
      {
        id: "minutes",
        label: tx("meeting.actions.minutes", "Minutes"),
        description: tx(
          "meeting.actions.minutesDesc",
          "Structured CR with decisions and owned actions.",
        ),
        prompt: `Produce meeting minutes using the selected template instructions. ${template.instructions}`,
      },
      {
        id: "summary",
        label: tx("meeting.actions.summary", "Executive summary"),
        description: tx(
          "meeting.actions.summaryDesc",
          "Short summary for stakeholders.",
        ),
        prompt: `Write an executive summary using template instructions. ${template.instructions}`,
      },
      {
        id: "speakers",
        label: tx("meeting.actions.speakers", "Identify speakers"),
        description: tx(
          "meeting.actions.speakersDesc",
          "Label Speaker 1/2… from voice turns; do not invent names.",
        ),
        prompt:
          "Identify speakers in the transcript. If names are not explicit, use Speaker 1, Speaker 2, … Rewrite the transcript with labels. List speaker map. Never invent real names. Save meetings/<slug>/transcript.md + speakers.md + meeting-report-*.html.",
      },
      {
        id: "followup",
        label: tx("meeting.actions.followup", "Follow-up email"),
        description: tx(
          "meeting.actions.followupDesc",
          "Warm external email draft (human sends).",
        ),
        prompt:
          "Draft a same-day follow-up email (thanks, 3-5 retained points, next steps with owners/dates). Save meetings/<slug>/follow-up.md. Human sends.",
      },
      {
        id: "actions",
        label: tx("meeting.actions.actions", "Action list"),
        description: tx(
          "meeting.actions.actionsDesc",
          "Owner + deadline table only.",
        ),
        prompt:
          "Extract Action | Owner | Deadline | Evidence quote. Flag ambiguous items. Save meetings/<slug>/actions.md + meeting-report-*.html.",
      },
      {
        id: "accuracy",
        label: tx("meeting.actions.accuracy", "High-accuracy pass"),
        description: tx(
          "meeting.actions.accuracyDesc",
          "Re-read transcript carefully; fix obvious ASR errors; keep meaning.",
        ),
        prompt:
          "Do a high-accuracy cleanup pass on the transcript: fix obvious ASR errors, punctuation, and speaker turns when clear. Do not invent content. Save cleaned transcript + diff notes under meetings/<slug>/ then regenerate summary with the selected template.",
      },
      {
        id: "visual",
        label: tx("meeting.actions.visual", "Visual recap"),
        description: tx(
          "meeting.actions.visualDesc",
          "One-slide recap image plus a diagram of decisions.",
        ),
        prompt:
          "Build a visual recap: generate an image (one-slide recap of decisions and next steps) with the image model, plus a mermaid diagram of the decision flow. Embed both in meetings/<slug>/meeting-report-*.html.",
      },
      {
        id: "discovery",
        label: tx("meeting.actions.discovery", "Discovery digest"),
        description: tx(
          "meeting.actions.discoveryDesc",
          "Sales discovery notes and angles.",
        ),
        prompt:
          "Apply discovery-call-assistant + selected template. Save meetings/<slug>/discovery.md + meeting-report-*.html.",
      },
    ],
    [template.instructions, tx],
  );

  const elapsedLabel = useMemo(() => {
    const total = Math.floor(elapsedMs / 1000);
    const m = String(Math.floor(total / 60)).padStart(2, "0");
    const s = String(total % 60).padStart(2, "0");
    return `${m}:${s}`;
  }, [elapsedMs]);

  const speakerDraft = (active?.speakers ?? []).join("\n");
  const transcriptWords = wordCount(active?.transcript ?? "");

  const tabs: { id: TabId; label: string; icon: typeof Mic; badge?: number }[] =
    [
      {
        id: "transcript",
        label: tx("meeting.tabs.transcript", "Transcript"),
        icon: FileAudio,
      },
      {
        id: "report",
        label: tx("meeting.tabs.report", "Report"),
        icon: FileText,
      },
      {
        id: "notes",
        label: tx("meeting.tabs.notes", "Notes"),
        icon: StickyNote,
      },
      {
        id: "actions",
        label: tx("meeting.tabs.actions", "Actions"),
        icon: Sparkles,
      },
      {
        id: "calendar",
        label: tx("meeting.tabs.calendar", "Calendar"),
        icon: CalendarDays,
        badge: upcoming.length,
      },
      {
        id: "templates",
        label: tx("meeting.tabs.templates", "Templates"),
        icon: LayoutTemplate,
      },
    ];

  const panelMotion = reduceMotion
    ? {}
    : {
        initial: { opacity: 0, y: 6 },
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 0, y: -4 },
        transition: spring,
      };

  const sttPill = (
    <button
      type="button"
      onClick={sttReady ? undefined : onOpenVoiceSettings}
      disabled={sttReady || !onOpenVoiceSettings}
      title={
        sttReady
          ? tx(
              "meeting.stt.ready",
              "{{provider}} · {{model}}",
              {
                provider: sttProvider || "navin",
                model: sttModel || "default",
              },
            )
          : tx(
              "meeting.stt.unavailable",
              "Not ready: enable transcription and configure a provider to record or import audio.",
            )
      }
      className={cn(
        "hidden h-8 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[11px] transition-colors lg:inline-flex",
        sttReady
          ? "border-border/60 text-muted-foreground"
          : "cursor-pointer border-amber-500/45 bg-amber-500/10 text-foreground hover:bg-amber-500/15 active:scale-[0.96]",
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          sttReady ? "bg-emerald-500" : "bg-amber-500",
        )}
        aria-hidden
      />
      <span className="max-w-[10rem] truncate">
        {sttReady
          ? sttProvider || "navin"
          : tx("meeting.stt.configureModel", "Configure model")}
      </span>
    </button>
  );

  // Repeated under the transcript and the notes: exports must be reachable
  // from wherever the user finished working, not only from one tab.
  const exportBar = (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/55 bg-muted/15 p-2.5">
      <span className="mr-auto text-[11px] text-muted-foreground">
        {tx("meeting.exportTitle", "Exports")}
        {reportModel ? (
          <span className="ml-1.5 tabular-nums">· {reportModel}</span>
        ) : null}
      </span>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-7 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
        onClick={exportMarkdownPack}
      >
        <Download className="mr-1.5 h-3 w-3" aria-hidden />
        Markdown
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-7 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
        onClick={exportHtmlReport}
      >
        HTML
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-7 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
        onClick={printReport}
      >
        <Printer className="mr-1.5 h-3 w-3" aria-hidden />
        PDF
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-7 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
        onClick={() => void exportDocx()}
      >
        DOCX
      </Button>
      <Button
        type="button"
        size="sm"
        variant="secondary"
        className="h-7 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
        disabled={filingNote || !active}
        title={tx(
          "meeting.noteLink.saveHint",
          "Files the minutes, live notes and action items (as tasks) into the Notes module. Safe to repeat: the same note is refreshed.",
        )}
        onClick={() => void saveToNotes()}
        data-testid="meeting-save-to-notes"
      >
        {filingNote ? (
          <Loader2 className="mr-1.5 h-3 w-3 animate-spin" aria-hidden />
        ) : (
          <StickyNote className="mr-1.5 h-3 w-3" aria-hidden />
        )}
        {active?.noteId
          ? tx("meeting.noteLink.refresh", "Refresh note")
          : tx("meeting.noteLink.save", "Save to Notes")}
      </Button>
      {active?.noteId && onOpenNote ? (
        <button
          type="button"
          className="inline-flex h-7 items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2.5 text-[11px] text-foreground transition-colors hover:bg-emerald-500/15 active:scale-[0.96]"
          onClick={() => onOpenNote(active.noteId as string)}
          data-testid="meeting-open-note"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden />
          {tx("meeting.noteLink.open", "Open linked note")}
        </button>
      ) : null}
      {noteToast ? (
        <span
          className="basis-full text-[11px] text-emerald-600 dark:text-emerald-400"
          role="status"
          aria-live="polite"
        >
          {noteToast}
        </span>
      ) : null}
    </div>
  );

  return (
    <div
      className="flex h-full min-h-0 flex-col bg-background"
      data-testid="meeting-workbench"
    >
      <header
        className={cn(
          "flex h-11 shrink-0 items-center gap-2 border-b border-border/55 px-3",
          !chatOpen && NOTIFICATION_GUTTER,
        )}
      >
        <Mic className="h-4 w-4 shrink-0 text-foreground/80" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-semibold text-foreground">
            {tx("meeting.title", "Meeting")}
          </p>
          <p className="truncate text-[11px] text-muted-foreground">
            {active
              ? active.title
              : tx("meeting.headerEmpty", "No meeting selected")}
          </p>
        </div>

        {sttPill}
        <span
          className="hidden text-[10.5px] text-muted-foreground sm:inline"
          aria-live="polite"
        >
          {!hydrated
            ? tx("meeting.persistence.loading", "Loading disk...")
            : saving
              ? tx("meeting.persistence.saving", "Saving...")
              : tx("meeting.persistence.saved", "Saved to disk")}
        </span>

        <Button
          type="button"
          size="sm"
          variant={recording ? "destructive" : "default"}
          className="h-8 shrink-0 rounded-full px-3 active:scale-[0.96]"
          disabled={busy || (!sttReady && !recording)}
          onClick={() => {
            if (recording) stopRecording();
            else void startRecording();
          }}
        >
          {recording ? (
            <>
              <Square className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              <span className="tabular-nums">{elapsedLabel}</span>
            </>
          ) : (
            <>
              <Mic className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("meeting.record", "Record")}
            </>
          )}
        </Button>

        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-8 shrink-0 gap-1.5 rounded-full px-2 active:scale-[0.96]"
          disabled={busy || recording || !sttReady}
          onClick={() => fileInputRef.current?.click()}
          title={tx("meeting.import", "Import audio")}
          aria-label={tx("meeting.import", "Import audio")}
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <Upload className="h-3.5 w-3.5" aria-hidden />
          )}
          {progress ? (
            <span className="tabular-nums text-[11px]">
              {progress.done}/{progress.total}
            </span>
          ) : null}
        </Button>

        {onToggleFocus ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-8 w-8 shrink-0 p-0 active:scale-[0.96]"
            onClick={onToggleFocus}
            title={
              focusMode
                ? tx("meeting.focusExit", "Show chat")
                : tx("meeting.focusEnter", "Full width (hide chat)")
            }
            aria-label={
              focusMode
                ? tx("meeting.focusExit", "Show chat")
                : tx("meeting.focusEnter", "Full width (hide chat)")
            }
          >
            {focusMode ? (
              <Minimize2 className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" aria-hidden />
            )}
          </Button>
        ) : null}

        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-8 w-8 shrink-0 p-0 active:scale-[0.96]"
          onClick={() => createMeeting()}
          title={tx("meeting.new", "New meeting")}
          aria-label={tx("meeting.new", "New meeting")}
        >
          <Plus className="h-4 w-4" aria-hidden />
        </Button>

        <input
          ref={fileInputRef}
          type="file"
          accept="audio/*,video/*"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0] ?? null;
            event.target.value = "";
            void onImportAudio(file);
          }}
        />
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-[14rem] shrink-0 flex-col border-r border-border/55 md:flex">
          <div className="shrink-0 border-b border-border/45 p-2">
            <SearchBox
              value={query}
              onChange={(_, value) => setQuery(value || "")}
              placeholder={tx("meeting.search", "Search meetings")}
              ariaLabel={tx("meeting.search", "Search meetings")}
              underlined={false}
              styles={{ root: { borderRadius: 8, height: 32 } }}
            />
          </div>

          <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
            {filteredMeetings.length === 0 ? (
              <li className="rounded-xl border border-dashed border-border/60 px-3 py-6 text-center text-[11.5px] text-muted-foreground">
                {meetings.length === 0
                  ? tx("meeting.emptyList", "No meetings yet.")
                  : tx("meeting.noMatch", "No meeting matches this search.")}
              </li>
            ) : (
              filteredMeetings.map((row) => {
                const isActive = row.id === activeId;
                const briefOpen = briefId === row.id;
                const excerpt =
                  row.summary?.trim() ||
                  row.notes.trim() ||
                  row.transcript.trim();
                return (
                  <li key={row.id}>
                    <div
                      className={cn(
                        "rounded-xl border transition-colors",
                        isActive
                          ? "border-border bg-muted/55"
                          : "border-transparent hover:bg-muted/30",
                      )}
                    >
                      <div className="flex items-start gap-1 p-1">
                        <button
                          type="button"
                          onClick={() => setActiveId(row.id)}
                          className="min-w-0 flex-1 cursor-pointer rounded-lg px-2 py-1.5 text-left"
                        >
                          <span className="block truncate text-[12.5px] font-medium text-foreground">
                            {row.title}
                          </span>
                          <span className="mt-0.5 flex items-center gap-1.5 text-[11px] tabular-nums text-muted-foreground">
                            <span className="truncate">
                              {formatDay(row.createdAt, i18n.language)}
                              {" · "}
                              {tx("meeting.wordCount", "{{count}} words", {
                                count: wordCount(row.transcript),
                              })}
                            </span>
                            {row.summary?.trim() ? (
                              <span
                                className="shrink-0 rounded-md border border-border/60 px-1 py-px text-[10px] text-foreground"
                                title={tx(
                                  "meeting.report.saved",
                                  "Report saved",
                                )}
                              >
                                {tx("meeting.report.badge", "Report")}
                              </span>
                            ) : null}
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => setBriefId(briefOpen ? null : row.id)}
                          className="mt-1 shrink-0 cursor-pointer rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                          aria-expanded={briefOpen}
                          aria-label={tx("meeting.brief", "Brief")}
                          title={tx("meeting.brief", "Brief")}
                        >
                          <ChevronDown
                            className={cn(
                              "h-3.5 w-3.5 transition-transform duration-200",
                              briefOpen && "rotate-180",
                            )}
                            aria-hidden
                          />
                        </button>
                      </div>

                      <AnimatePresence initial={false}>
                        {briefOpen ? (
                          <motion.div
                            key="brief"
                            initial={
                              reduceMotion
                                ? undefined
                                : { height: 0, opacity: 0 }
                            }
                            animate={
                              reduceMotion
                                ? undefined
                                : { height: "auto", opacity: 1 }
                            }
                            exit={
                              reduceMotion
                                ? undefined
                                : { height: 0, opacity: 0 }
                            }
                            transition={spring}
                            className="overflow-hidden"
                          >
                            <div className="space-y-2 px-3 pb-2.5">
                              <p className="max-h-20 overflow-hidden whitespace-pre-wrap text-[11px] leading-4 text-muted-foreground">
                                {excerpt ||
                                  tx(
                                    "meeting.briefEmpty",
                                    "Nothing captured yet.",
                                  )}
                              </p>
                              <div className="flex flex-wrap gap-1 text-[10.5px] text-muted-foreground">
                                <span className="rounded-md bg-muted/60 px-1.5 py-0.5">
                                  {findMeetingTemplate(row.templateId).name}
                                </span>
                                <span className="rounded-md bg-muted/60 px-1.5 py-0.5 tabular-nums">
                                  {tx(
                                    "meeting.speakerCount",
                                    "{{count}} speakers",
                                    {
                                      count: row.speakers.length,
                                    },
                                  )}
                                </span>
                              </div>
                              <button
                                type="button"
                                onClick={() => setConfirmDeleteId(row.id)}
                                className="inline-flex cursor-pointer items-center gap-1 rounded-lg px-1.5 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                              >
                                <Trash2 className="h-3 w-3" aria-hidden />
                                {tx("meeting.delete", "Delete")}
                              </button>
                            </div>
                          </motion.div>
                        ) : null}
                      </AnimatePresence>
                    </div>
                  </li>
                );
              })
            )}
          </ul>
        </aside>

        <section className="flex min-w-0 flex-1 flex-col">
          {!active ? (
            <div className="flex min-h-0 flex-1 items-center justify-center p-6">
              <div className="max-w-sm rounded-2xl border border-dashed border-border/60 px-5 py-6 text-center">
                <Mic
                  className="mx-auto h-5 w-5 text-muted-foreground"
                  aria-hidden
                />
                <p className="mt-2 text-[13px] font-medium text-foreground">
                  {tx("meeting.emptyTitle", "No meeting open")}
                </p>
                <p className="mt-1 text-[12px] leading-5 text-muted-foreground">
                  {tx(
                    "meeting.emptyDesk",
                    "Create a meeting, then record, import audio, or paste a transcript.",
                  )}
                </p>
                <Button
                  type="button"
                  size="sm"
                  className="mt-3 h-8 rounded-full active:scale-[0.96]"
                  onClick={() => createMeeting()}
                >
                  <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                  {tx("meeting.new", "New meeting")}
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border/45 px-3 py-2">
                <input
                  value={active.title}
                  onChange={(event) =>
                    updateActive({ title: event.target.value })
                  }
                  className="h-8 min-w-[10rem] flex-1 rounded-lg border border-border/60 bg-background px-2.5 text-[13px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40"
                  aria-label={tx("meeting.titleField", "Meeting title")}
                />
                <select
                  className={selectClassName}
                  value={active.templateId}
                  aria-label={tx("meeting.template", "Template")}
                  onChange={(event) => {
                    updateActive({ templateId: event.target.value });
                    track("template.selected", event.target.value);
                  }}
                >
                  {templates.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </select>
                <select
                  className={cn(selectClassName, "md:hidden")}
                  value={active.id}
                  aria-label={tx("meeting.switch", "Switch meeting")}
                  onChange={(event) => setActiveId(event.target.value)}
                >
                  {meetings.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.title}
                    </option>
                  ))}
                </select>
              </div>

              <nav
                role="tablist"
                aria-label={tx("meeting.title", "Meeting")}
                className="flex shrink-0 gap-0.5 overflow-x-auto border-b border-border/45 px-2 py-1.5"
                onKeyDown={(event) => {
                  // Roving focus: arrows move between tabs, Home/End jump.
                  const index = tabs.findIndex((item) => item.id === tab);
                  if (index < 0) return;
                  let next: number;
                  if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
                  else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
                  else if (event.key === "Home") next = 0;
                  else if (event.key === "End") next = tabs.length - 1;
                  else return;
                  event.preventDefault();
                  setTab(tabs[next].id);
                  (
                    event.currentTarget.querySelector<HTMLButtonElement>(
                      `[data-tab-id="${tabs[next].id}"]`,
                    ) ?? null
                  )?.focus();
                }}
              >
                {tabs.map((item) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      role="tab"
                      id={`meeting-tab-${item.id}`}
                      data-tab-id={item.id}
                      aria-selected={tab === item.id}
                      aria-controls={`meeting-panel-${item.id}`}
                      tabIndex={tab === item.id ? 0 : -1}
                      onClick={() => setTab(item.id)}
                      className={cn(
                        "inline-flex h-7 shrink-0 cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-[11.5px] font-medium transition-colors active:scale-[0.96]",
                        tab === item.id
                          ? "bg-foreground text-background"
                          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" aria-hidden />
                      {item.label}
                      {item.badge ? (
                        <span className="tabular-nums opacity-80">
                          {item.badge}
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </nav>

              {error || saveError || cacheError ? (
                <div className="mx-3 mt-2 shrink-0">
                  <MessageBar
                    messageBarType={MessageBarType.error}
                    isMultiline
                    actions={
                      saveError || cacheError ? (
                        <DefaultButton
                          text={tx(
                            "meeting.persistence.emergencyExport",
                            "Emergency export",
                          )}
                          onClick={() => void exportEmergency()}
                        />
                      ) : undefined
                    }
                  >
                    {saveError || cacheError || error}
                  </MessageBar>
                </div>
              ) : null}

              <div
                className="min-h-0 flex-1 overflow-y-auto p-3"
                role="tabpanel"
                id={`meeting-panel-${tab}`}
                aria-labelledby={`meeting-tab-${tab}`}
              >
                <AnimatePresence mode="wait" initial={false}>
                  {tab === "report" && report ? (
                    <motion.div
                      key="report"
                      {...panelMotion}
                      className="space-y-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          type="button"
                          size="sm"
                          className="h-8 rounded-lg active:scale-[0.96]"
                          onClick={() => void generateReport()}
                          disabled={generating}
                        >
                          {generating ? (
                            <Loader2
                              className="mr-1.5 h-3.5 w-3.5 animate-spin"
                              aria-hidden
                            />
                          ) : (
                            <Sparkles
                              className="mr-1.5 h-3.5 w-3.5"
                              aria-hidden
                            />
                          )}
                          {active.summary?.trim()
                            ? tx("meeting.report.regenerate", "Regenerate")
                            : tx("meeting.report.generate", "Generate report")}
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-8 rounded-lg active:scale-[0.96]"
                          onClick={exportMarkdownPack}
                        >
                          <Download
                            className="mr-1.5 h-3.5 w-3.5"
                            aria-hidden
                          />
                          Markdown
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-8 rounded-lg active:scale-[0.96]"
                          onClick={printReport}
                        >
                          <Printer className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                          {tx("meeting.report.print", "Print / PDF")}
                        </Button>
                        <DefaultButton
                          text={tx(
                            "meeting.translation.report",
                            "Translate report",
                          )}
                          disabled={busy || !active.summary?.trim()}
                          onClick={() => void translateMeeting("report")}
                        />
                        {onSpeak ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="h-8 rounded-lg active:scale-[0.96]"
                            onClick={toggleSpeech}
                          >
                            {speaking ? (
                              <>
                                <Square
                                  className="mr-1.5 h-3.5 w-3.5"
                                  aria-hidden
                                />
                                {tx("meeting.speakStop", "Stop reading")}
                              </>
                            ) : (
                              <>
                                <Volume2
                                  className="mr-1.5 h-3.5 w-3.5"
                                  aria-hidden
                                />
                                {tx("meeting.speak", "Listen")}
                              </>
                            )}
                          </Button>
                        ) : null}
                        {reportModel ? (
                          <span className="text-[11px] text-muted-foreground">
                            {reportModel}
                          </span>
                        ) : null}
                      </div>

                      <section className="rounded-xl border border-border/55 p-3">
                        <h3 className="text-[13px] font-semibold text-foreground">
                          {active.title}
                        </h3>
                        <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
                          {(
                            [
                              [
                                reportLabels.date,
                                formatEventWhen(
                                  active.startedAt || active.createdAt,
                                  i18n.language,
                                ),
                              ],
                              [reportLabels.template, template.name],
                              [
                                reportLabels.speakers,
                                active.speakers.length
                                  ? active.speakers.join(", ")
                                  : reportLabels.none,
                              ],
                              [reportLabels.words, String(report.stats.words)],
                              [
                                reportLabels.duration,
                                report.stats.durationSec
                                  ? formatDurationLabel(report.stats.durationSec)
                                  : `~${report.stats.spokenMinutes} min`,
                              ],
                              [
                                reportLabels.language,
                                report.stats.language === "unknown"
                                  ? "-"
                                  : report.stats.language.toUpperCase(),
                              ],
                            ] as Array<[string, string]>
                          ).map(([term, value]) => (
                            <div
                              key={term}
                              className="rounded-lg border border-border/45 bg-muted/20 px-2.5 py-1.5"
                            >
                              <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">
                                {term}
                              </dt>
                              <dd className="truncate text-[12.5px] font-medium tabular-nums text-foreground">
                                {value}
                              </dd>
                            </div>
                          ))}
                        </dl>
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <label
                            htmlFor="meeting-started-at"
                            className="text-[10px] uppercase tracking-wide text-muted-foreground"
                          >
                            {reportLabels.date}
                          </label>
                          <input
                            id="meeting-started-at"
                            type="datetime-local"
                            className="h-7 rounded-lg border border-border/60 bg-background px-2 text-[12px] tabular-nums text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                            value={toDatetimeLocal(
                              active.startedAt || active.createdAt,
                            )}
                            onChange={(event) => {
                              const value = event.target.value;
                              if (!value) return;
                              const parsed = new Date(value);
                              if (Number.isNaN(parsed.getTime())) return;
                              updateActive({ startedAt: parsed.toISOString() });
                            }}
                          />
                        </div>
                      </section>

                      <section className="rounded-xl border border-border/55 p-3">
                        <h3 className="mb-2 text-[12px] font-medium text-foreground">
                          {reportLabels.summary}
                        </h3>
                        {active.summary?.trim() ? (
                          <>
                            <MarkdownText className="text-[13px] leading-6">
                              {active.summary}
                            </MarkdownText>
                            {active.translatedSummary?.trim() ? (
                              <div className="mt-3 border-t border-border/45 pt-3">
                                <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                                  {tx(
                                    "meeting.translation.result",
                                    "Translation",
                                  )}{" "}
                                  - {active.translationLanguage}
                                </p>
                                <MarkdownText className="text-[13px] leading-6">
                                  {active.translatedSummary}
                                </MarkdownText>
                              </div>
                            ) : null}
                          </>
                        ) : (
                          <p className="text-[12px] leading-5 text-muted-foreground">
                            {report.thin
                              ? tx(
                                  "meeting.report.thin",
                                  "This meeting has almost no content yet. Record or import audio, then generate the report.",
                                )
                              : tx(
                                  "meeting.report.empty",
                                  "No summary yet. Generate the report to write one from the transcript.",
                                )}
                          </p>
                        )}
                      </section>

                      {report.highlights.some(
                        (row) => row.kind !== "question",
                      ) ? (
                        <section className="rounded-xl border border-border/55 p-3">
                          <h3 className="mb-2 text-[12px] font-medium text-foreground">
                            {reportLabels.highlights}
                          </h3>
                          <p className="mb-2 text-[11px] leading-4 text-muted-foreground">
                            {tx(
                              "meeting.report.highlightsHint",
                              "Detected locally from the transcript wording. Quotes are verbatim, never rewritten.",
                            )}
                          </p>
                          <div className="overflow-x-auto">
                            <table className="w-full border-collapse text-[12px]">
                              <thead>
                                <tr className="text-left text-muted-foreground">
                                  <th className="border-b border-border/50 pb-1.5 pr-2 font-medium">
                                    {reportLabels.kind}
                                  </th>
                                  <th className="border-b border-border/50 pb-1.5 pr-2 font-medium">
                                    {reportLabels.detail}
                                  </th>
                                  <th className="border-b border-border/50 pb-1.5 pr-2 font-medium">
                                    {reportLabels.owner}
                                  </th>
                                  <th className="border-b border-border/50 pb-1.5 font-medium">
                                    {reportLabels.due}
                                  </th>
                                </tr>
                              </thead>
                              <tbody>
                                {report.highlights
                                  .filter((row) => row.kind !== "question")
                                  .map((row) => (
                                    <tr key={row.id} className="align-top">
                                      <td className="border-b border-border/30 py-1.5 pr-2 text-muted-foreground">
                                        {row.kind === "decision"
                                          ? reportLabels.decision
                                          : row.kind === "action"
                                            ? reportLabels.action
                                            : row.kind === "proposal"
                                              ? reportLabels.proposal
                                              : row.kind === "opinion"
                                                ? reportLabels.opinion
                                                : reportLabels.risk}
                                      </td>
                                      <td className="border-b border-border/30 py-1.5 pr-2 text-foreground">
                                        {row.text}
                                      </td>
                                      <td className="border-b border-border/30 py-1.5 pr-2 text-muted-foreground">
                                        {row.owner || reportLabels.unassigned}
                                      </td>
                                      <td className="border-b border-border/30 py-1.5 text-muted-foreground">
                                        {row.due || reportLabels.noDue}
                                      </td>
                                    </tr>
                                  ))}
                              </tbody>
                            </table>
                          </div>
                        </section>
                      ) : null}

                      {report.highlights.some(
                        (row) => row.kind === "question",
                      ) ? (
                        <section className="rounded-xl border border-border/55 p-3">
                          <h3 className="mb-2 text-[12px] font-medium text-foreground">
                            {reportLabels.questions}
                          </h3>
                          <ul className="space-y-1 text-[12.5px] leading-5 text-foreground">
                            {report.highlights
                              .filter((row) => row.kind === "question")
                              .map((row) => (
                                <li key={row.id} className="flex gap-2">
                                  <span
                                    className="text-muted-foreground"
                                    aria-hidden
                                  >
                                    ·
                                  </span>
                                  <span>{row.text}</span>
                                </li>
                              ))}
                          </ul>
                        </section>
                      ) : null}

                      {report.turns.length ? (
                        <section className="rounded-xl border border-border/55 p-3">
                          <h3 className="mb-2 text-[12px] font-medium text-foreground">
                            {reportLabels.transcript}
                          </h3>
                          <TranscriptTurns
                            turns={report.turns}
                            startedAt={active.startedAt || active.createdAt}
                            unknownSpeakerLabel={tx(
                              "meeting.unknownSpeaker",
                              "Speaker",
                            )}
                            locale={i18n.language}
                            query={transcriptQuery}
                            onSeek={audioSegments.length ? seekAudio : undefined}
                          />
                        </section>
                      ) : null}
                    </motion.div>
                  ) : null}

                  {tab === "transcript" ? (
                    <motion.div
                      key="transcript"
                      {...panelMotion}
                      className="space-y-2"
                    >
                      {!sttReady ? (
                        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11.5px]">
                          <span className="flex-1">
                            {tx(
                              "meeting.stt.unavailable",
                              "Not ready: enable transcription and configure a provider to record or import audio.",
                            )}
                          </span>
                          {onOpenVoiceSettings ? (
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="h-7 rounded-lg px-2.5 text-[11px] active:scale-[0.96]"
                              onClick={onOpenVoiceSettings}
                            >
                              {tx("meeting.stt.open", "Open Voice settings")}
                            </Button>
                          ) : null}
                        </div>
                      ) : null}

                      <div className="rounded-xl border border-border/55 bg-muted/20 px-3 py-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <Video
                            className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                            aria-hidden
                          />
                          <input
                            value={active.conferenceUrl || ""}
                            onChange={(event) =>
                              updateActive({
                                conferenceUrl: event.target.value,
                              })
                            }
                            placeholder={tx(
                              "meeting.conference.placeholder",
                              "Zoom / Google Meet / Teams link",
                            )}
                            aria-label={tx(
                              "meeting.conference.placeholder",
                              "Zoom / Google Meet / Teams link",
                            )}
                            className="h-8 min-w-[10rem] flex-1 rounded-lg border border-border/60 bg-background px-2.5 text-[12px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40"
                          />
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="h-8 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
                            disabled={!active.conferenceUrl?.trim()}
                            onClick={() => {
                              const url = active.conferenceUrl?.trim();
                              if (!url) return;
                              openConference(url);
                              track("conference.opened");
                            }}
                          >
                            {tx("meeting.conference.open", "Open the call")}
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            className="h-8 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
                            disabled={busy || recording || !sttReady}
                            onClick={() => void startRecording("conference")}
                          >
                            <Volume2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                            {tx("meeting.conference.capture", "Capture the call")}
                          </Button>
                          {botActive ? (
                            <Button
                              type="button"
                              size="sm"
                              variant="destructive"
                              className="h-8 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
                              onClick={() => void stopBot()}
                            >
                              <Square className="mr-1.5 h-3 w-3" aria-hidden />
                              {tx("meeting.bot.stop", "Recall the bot")}
                            </Button>
                          ) : (
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="h-8 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
                              disabled={
                                !sttReady || !active.conferenceUrl?.trim()
                              }
                              onClick={() => void startBot()}
                            >
                              <Bot className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                              {tx("meeting.bot.send", "Send the bot")}
                            </Button>
                          )}
                        </div>
                        {botState !== "idle" ? (
                          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11.5px]">
                            <span
                              className={
                                botState === "live"
                                  ? "inline-flex items-center gap-1.5 font-medium text-emerald-600 dark:text-emerald-400"
                                  : botState === "error"
                                    ? "inline-flex items-center gap-1.5 font-medium text-destructive"
                                    : "inline-flex items-center gap-1.5 text-muted-foreground"
                              }
                            >
                              {botActive ? (
                                botState === "live" ? (
                                  <span
                                    className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500"
                                    aria-hidden
                                  />
                                ) : (
                                  <Loader2
                                    className="h-3 w-3 animate-spin"
                                    aria-hidden
                                  />
                                )
                              ) : null}
                              {botStateLabel}
                            </span>
                            {botError ? (
                              <span className="text-destructive">
                                {botError}
                              </span>
                            ) : null}
                          </div>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => setConfHelpOpen((prev) => !prev)}
                          aria-expanded={confHelpOpen}
                          className="mt-1.5 inline-flex items-center gap-1 rounded-md text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground"
                        >
                          <HelpCircle className="h-3.5 w-3.5" aria-hidden />
                          {tx("meeting.conference.help", "How it works")}
                          <ChevronDown
                            className={`h-3 w-3 transition-transform ${confHelpOpen ? "rotate-180" : ""}`}
                            aria-hidden
                          />
                        </button>
                        <AnimatePresence initial={false}>
                          {confHelpOpen ? (
                            <motion.div
                              key="conf-help"
                              initial={
                                reduceMotion
                                  ? { opacity: 0 }
                                  : { opacity: 0, height: 0 }
                              }
                              animate={
                                reduceMotion
                                  ? { opacity: 1 }
                                  : { opacity: 1, height: "auto" }
                              }
                              exit={
                                reduceMotion
                                  ? { opacity: 0 }
                                  : { opacity: 0, height: 0 }
                              }
                              transition={{ duration: 0.18 }}
                              className="overflow-hidden"
                            >
                              <div className="mt-2 space-y-3 rounded-xl border border-border/60 bg-background/70 p-3 text-[12px] leading-5">
                                <div>
                                  <div className="mb-1.5 flex items-center gap-1.5 font-semibold text-foreground">
                                    <Volume2
                                      className="h-3.5 w-3.5 text-muted-foreground"
                                      aria-hidden
                                    />
                                    {tx(
                                      "meeting.conference.manualTitle",
                                      "Capture from your browser",
                                    )}
                                  </div>
                                  <ol className="ml-1 space-y-1">
                                    {[
                                      tx(
                                        "meeting.conference.step1",
                                        "Open the call and join the meeting.",
                                      ),
                                      tx(
                                        "meeting.conference.step2",
                                        'Come back here and click "Capture the call".',
                                      ),
                                      tx(
                                        "meeting.conference.step3",
                                        'Pick the meeting tab and tick "Also share tab audio".',
                                      ),
                                    ].map((step, index) => (
                                      <li
                                        key={index}
                                        className="flex items-start gap-2 text-muted-foreground"
                                      >
                                        <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full bg-primary/12 text-[9.5px] font-bold text-primary">
                                          {index + 1}
                                        </span>
                                        <span>{step}</span>
                                      </li>
                                    ))}
                                  </ol>
                                  <p className="ml-1 mt-1.5 text-[11px] italic text-muted-foreground">
                                    {tx(
                                      "meeting.conference.manualNote",
                                      "Everyone is transcribed live, plus your own microphone.",
                                    )}
                                  </p>
                                </div>
                                <div className="border-t border-border/50 pt-2.5">
                                  <div className="mb-1 flex items-center gap-1.5 font-semibold text-foreground">
                                    <Bot
                                      className="h-3.5 w-3.5 text-muted-foreground"
                                      aria-hidden
                                    />
                                    {tx(
                                      "meeting.conference.botTitle",
                                      "Autonomous bot",
                                    )}
                                  </div>
                                  <p className="text-muted-foreground">
                                    {tx(
                                      "meeting.bot.hint",
                                      'Or send the bot: it joins the call on its own as "Navin AI" and transcribes everything once the host lets it in - no browser needed on your side.',
                                    )}
                                  </p>
                                </div>
                              </div>
                            </motion.div>
                          ) : null}
                        </AnimatePresence>
                      </div>

                      <div className="grid gap-2 rounded-xl border border-border/55 p-3 lg:grid-cols-[minmax(12rem,1fr)_auto]">
                        <SearchBox
                          value={transcriptQuery}
                          onChange={(_, value) => setTranscriptQuery(value || "")}
                          placeholder={tx(
                            "meeting.transcriptSearch",
                            "Search in transcript",
                          )}
                          ariaLabel={tx(
                            "meeting.transcriptSearch",
                            "Search in transcript",
                          )}
                          styles={{ root: { borderRadius: 8, height: 32 } }}
                        />
                        <div className="flex flex-wrap items-center gap-2">
                          <select
                            className={selectClassName}
                            value={translationLanguage}
                            aria-label={tx(
                              "meeting.translation.language",
                              "Translation language",
                            )}
                            onChange={(event) =>
                              setTranslationLanguage(event.target.value)
                            }
                          >
                            <option value="French">Français</option>
                            <option value="English">English</option>
                          </select>
                          <DefaultButton
                            text={tx(
                              "meeting.translation.transcript",
                              "Translate transcript",
                            )}
                            disabled={busy || !active.transcript.trim()}
                            onClick={() => void translateMeeting("transcript")}
                          />
                          <DefaultButton
                            text={tx(
                              "meeting.actions.accuracy",
                              "High-accuracy pass",
                            )}
                            disabled={busy || !active.transcript.trim()}
                            onClick={() => void cleanupTranscript()}
                          />
                        </div>
                        <div className="lg:col-span-2">
                          <span className="rounded-md border border-border/55 bg-muted/30 px-2 py-1 text-[11px] text-muted-foreground">
                            {active.diarizationSource === "provider_native"
                              ? tx(
                                  "meeting.diarization.native",
                                  "Diarization: provider-native acoustic labels",
                                )
                              : active.diarizationSource === "llm_fallback"
                                ? tx(
                                    "meeting.diarization.fallback",
                                    "Diarization: text-only AI fallback, not acoustic identification",
                                  )
                                : tx(
                                    "meeting.diarization.unknown",
                                    "Diarization: no verified speaker source",
                                  )}
                          </span>
                        </div>
                        {audioSegments.length ? (
                          <div className="space-y-1 lg:col-span-2">
                            <audio
                              ref={audioRef}
                              controls
                              preload="metadata"
                              src={audioSegments[0]?.url}
                              className="h-9 w-full"
                            />
                            <p className="text-[10.5px] text-muted-foreground">
                              {tx(
                                "meeting.audio.seekHint",
                                "Click a transcript timecode to seek the saved audio.",
                              )}
                            </p>
                          </div>
                        ) : null}
                        {active.translatedTranscript?.trim() ? (
                          <div className="whitespace-pre-wrap rounded-lg bg-muted/25 p-2.5 text-[12.5px] leading-5 lg:col-span-2">
                            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                              {tx(
                                "meeting.translation.result",
                                "Translation",
                              )}{" "}
                              - {active.translationLanguage}
                            </p>
                            {active.translatedTranscript}
                          </div>
                        ) : null}
                      </div>

                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-7 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
                          onClick={() => void identifySpeakers()}
                          disabled={labelling || !active.transcript.trim()}
                        >
                          {labelling ? (
                            <Loader2
                              className="mr-1.5 h-3 w-3 animate-spin"
                              aria-hidden
                            />
                          ) : (
                            <Users className="mr-1.5 h-3 w-3" aria-hidden />
                          )}
                          {tx("meeting.speakersIdentify", "Identify speakers")}
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          className="h-7 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
                          onClick={() => setEditTranscript((prev) => !prev)}
                        >
                          {editTranscript
                            ? tx("meeting.transcriptRead", "Reading view")
                            : tx("meeting.transcriptEdit", "Edit raw text")}
                        </Button>
                        {report && report.turns.length ? (
                          <span className="text-[11px] tabular-nums text-muted-foreground">
                            {tx("meeting.turnCount", "{{count}} turns", {
                              count: report.turns.length,
                            })}
                          </span>
                        ) : null}
                      </div>

                      {editTranscript || !report || !report.turns.length ? (
                        <Textarea
                          value={active.transcript}
                          onChange={(event) =>
                            updateActive({ transcript: event.target.value })
                          }
                          placeholder={tx(
                            "meeting.transcriptPlaceholder",
                            "Live transcript appears here. You can also paste notes.",
                          )}
                          className="min-h-[22rem] resize-y rounded-xl text-[13px] leading-6"
                        />
                      ) : (
                        <div className="min-h-[22rem] rounded-xl border border-border/55 p-3.5">
                          <TranscriptTurns
                            turns={report.turns}
                            startedAt={active.startedAt || active.createdAt}
                            unknownSpeakerLabel={tx(
                              "meeting.unknownSpeaker",
                              "Speaker",
                            )}
                            locale={i18n.language}
                            query={transcriptQuery}
                            onSeek={audioSegments.length ? seekAudio : undefined}
                          />
                        </div>
                      )}

                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                        <span className="tabular-nums">
                          {tx("meeting.wordCount", "{{count}} words", {
                            count: transcriptWords,
                          })}
                        </span>
                        {sttReady ? (
                          <span className="tabular-nums">
                            {tx(
                              "meeting.stt.ready",
                              "{{provider}} · {{model}}",
                              {
                                provider: sttProvider || "navin",
                                model: sttModel || "default",
                              },
                            )}
                          </span>
                        ) : onOpenVoiceSettings ? (
                          <button
                            type="button"
                            onClick={onOpenVoiceSettings}
                            className="inline-flex cursor-pointer items-center gap-1 rounded-md text-[11px] font-medium text-foreground underline-offset-2 transition-colors hover:underline"
                          >
                            {tx(
                              "meeting.stt.configureModel",
                              "Configure model",
                            )}
                          </button>
                        ) : (
                          <span>
                            {tx("meeting.stt.short", "STT not ready")}
                          </span>
                        )}
                        {progress ? (
                          <span className="tabular-nums text-foreground">
                            {tx(
                              "meeting.importProgress",
                              "Transcribing {{done}}/{{total}}",
                              {
                                done: progress.done,
                                total: progress.total,
                              },
                            )}
                          </span>
                        ) : null}
                      </div>

                      {exportBar}
                    </motion.div>
                  ) : null}

                  {tab === "notes" ? (
                    <motion.div
                      key="notes"
                      {...panelMotion}
                      className="space-y-3"
                    >
                      <div className="grid gap-3 xl:grid-cols-2">
                        <label className="block space-y-1.5">
                          <span className="flex items-center gap-1.5 text-[12px] font-medium text-foreground">
                            <StickyNote
                              className="h-3.5 w-3.5 text-muted-foreground"
                              aria-hidden
                            />
                            {tx("meeting.notes", "Private notes")}
                          </span>
                          <Textarea
                            value={active.notes}
                            onChange={(event) =>
                              updateActive({ notes: event.target.value })
                            }
                            placeholder={tx(
                              "meeting.notesPlaceholder",
                              "Attendees, goal, CRM context...",
                            )}
                            className="min-h-[14rem] resize-y rounded-xl text-[13px] leading-6"
                          />
                        </label>
                        <div className="space-y-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="flex items-center gap-1.5 text-[12px] font-medium text-foreground">
                              <Users
                                className="h-3.5 w-3.5 text-muted-foreground"
                                aria-hidden
                              />
                              {tx(
                                "meeting.speakers",
                                "Speakers (one per line)",
                              )}
                            </span>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="ml-auto h-7 rounded-lg px-2.5 text-[11.5px] active:scale-[0.96]"
                              onClick={() => void identifySpeakers()}
                              disabled={labelling || !active.transcript.trim()}
                            >
                              {labelling ? (
                                <Loader2
                                  className="mr-1.5 h-3 w-3 animate-spin"
                                  aria-hidden
                                />
                              ) : null}
                              {tx(
                                "meeting.speakersIdentify",
                                "Identify speakers",
                              )}
                            </Button>
                          </div>
                          <p className="text-[11px] leading-4 text-muted-foreground">
                            {tx(
                              "meeting.speakersHint",
                              "The pass labels turns and keeps a real name only when the transcript states it. Rename a speaker here and every one of their turns follows.",
                            )}
                          </p>
                          {active.speakers.length ? (
                            <ul className="space-y-1.5">
                              {active.speakers.map((name, index) => (
                                <li
                                  key={`${name}-${index}`}
                                  className="flex items-center gap-2"
                                >
                                  <span className="w-5 shrink-0 text-[11px] tabular-nums text-muted-foreground">
                                    {index + 1}
                                  </span>
                                  <input
                                    defaultValue={name}
                                    onBlur={(event) =>
                                      applySpeakerRename(
                                        name,
                                        event.target.value,
                                      )
                                    }
                                    onKeyDown={(event) => {
                                      if (event.key !== "Enter") return;
                                      event.preventDefault();
                                      event.currentTarget.blur();
                                    }}
                                    aria-label={tx(
                                      "meeting.speakerRename",
                                      "Rename speaker",
                                    )}
                                    className="h-8 min-w-0 flex-1 rounded-lg border border-border/60 bg-background px-2.5 text-[12.5px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40"
                                  />
                                </li>
                              ))}
                            </ul>
                          ) : null}
                          <Textarea
                            value={speakerDraft}
                            onChange={(event) =>
                              updateActive({
                                speakers: event.target.value
                                  .split("\n")
                                  .map((line) => line.trim())
                                  .filter(Boolean),
                              })
                            }
                            placeholder={tx(
                              "meeting.speakersPlaceholder",
                              "Speaker 1\nSpeaker 2",
                            )}
                            className="min-h-[8rem] resize-y rounded-xl text-[13px] leading-6"
                          />
                        </div>
                      </div>

                      {exportBar}

                      <div className="rounded-xl border border-border/55 p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <button
                            type="button"
                            className="inline-flex cursor-pointer items-center gap-1.5 text-[12px] font-medium text-foreground"
                            onClick={() => setShowAudit((prev) => !prev)}
                            aria-expanded={showAudit}
                          >
                            <ShieldCheck
                              className="h-3.5 w-3.5 text-muted-foreground"
                              aria-hidden
                            />
                            {tx("meeting.auditTitle", "Local audit trail")}
                            <ChevronDown
                              className={cn(
                                "h-3.5 w-3.5 text-muted-foreground transition-transform duration-200",
                                showAudit && "rotate-180",
                              )}
                              aria-hidden
                            />
                          </button>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="h-7 rounded-lg px-2 text-[11px] active:scale-[0.96]"
                            onClick={() => {
                              downloadAuditMarkdown(active.id, active.title);
                              track("audit.exported");
                            }}
                          >
                            <Download className="mr-1.5 h-3 w-3" aria-hidden />
                            {tx("meeting.auditExport", "Audit trail")}
                          </Button>
                        </div>
                        <AnimatePresence initial={false}>
                          {showAudit ? (
                            <motion.ul
                              key="audit"
                              initial={
                                reduceMotion
                                  ? undefined
                                  : { height: 0, opacity: 0 }
                              }
                              animate={
                                reduceMotion
                                  ? undefined
                                  : { height: "auto", opacity: 1 }
                              }
                              exit={
                                reduceMotion
                                  ? undefined
                                  : { height: 0, opacity: 0 }
                              }
                              transition={spring}
                              className="mt-2 max-h-48 space-y-1 overflow-auto text-[11.5px] text-muted-foreground"
                            >
                              {auditRows.length === 0 ? (
                                <li>
                                  {tx(
                                    "meeting.auditEmpty",
                                    "No audit events yet.",
                                  )}
                                </li>
                              ) : (
                                [...auditRows].reverse().map((row) => (
                                  <li
                                    key={`${row.at}-${row.action}`}
                                    className="tabular-nums"
                                  >
                                    {row.at} · {row.action}
                                    {row.detail ? ` - ${row.detail}` : ""}
                                  </li>
                                ))
                              )}
                            </motion.ul>
                          ) : null}
                        </AnimatePresence>
                      </div>
                    </motion.div>
                  ) : null}

                  {tab === "actions" ? (
                    <motion.div
                      key="actions"
                      {...panelMotion}
                      className="space-y-3"
                    >
                      <div className="rounded-xl border border-border/55 p-3">
                        <div className="flex items-center gap-1.5 text-[12px] font-medium text-foreground">
                          <MessageSquareText
                            className="h-3.5 w-3.5 text-muted-foreground"
                            aria-hidden
                          />
                          {tx("meeting.chatTitle", "Ask this meeting")}
                        </div>
                        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                          {tx(
                            "meeting.chatHint",
                            "Answered here from this meeting only. Send it to the chat when the agent also needs files or tools.",
                          )}
                        </p>
                        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                          <input
                            value={askDraft}
                            onChange={(event) =>
                              setAskDraft(event.target.value)
                            }
                            placeholder={tx(
                              "meeting.chatPlaceholder",
                              "e.g. What did we decide about the timeline?",
                            )}
                            className="h-8 min-w-0 flex-1 rounded-lg border border-border/60 bg-background px-2.5 text-[12.5px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40"
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault();
                                void askMeeting();
                              }
                            }}
                          />
                          <div className="flex gap-2">
                            <Button
                              type="button"
                              size="sm"
                              className="h-8 rounded-lg active:scale-[0.96]"
                              onClick={() => void askMeeting()}
                              disabled={!askDraft.trim() || answering}
                            >
                              {answering ? (
                                <Loader2
                                  className="mr-1.5 h-3.5 w-3.5 animate-spin"
                                  aria-hidden
                                />
                              ) : null}
                              {tx("meeting.chatAsk", "Ask")}
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              className="h-8 rounded-lg px-2 text-[11.5px] active:scale-[0.96]"
                              onClick={seedAsk}
                              disabled={!askDraft.trim()}
                            >
                              {tx("meeting.chatSeed", "Send to chat")}
                            </Button>
                          </div>
                        </div>
                        {answer ? (
                          <div className="mt-2 rounded-lg border border-border/50 bg-muted/20 px-2.5 py-2">
                            <MarkdownText className="text-[12.5px] leading-5">
                              {answer}
                            </MarkdownText>
                          </div>
                        ) : null}
                        {active.chatLog ? (
                          <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap rounded-lg bg-muted/30 px-2.5 py-2 text-[11.5px] text-muted-foreground">
                            {active.chatLog}
                          </pre>
                        ) : null}
                      </div>

                      <div>
                        <p className="mb-2 text-[12px] font-medium text-foreground">
                          {tx("meeting.actionsTitle", "Run with your models")}
                        </p>
                        <div className="grid gap-2 sm:grid-cols-2 2xl:grid-cols-3">
                          {actions.map((action, index) => (
                            <motion.button
                              key={action.id}
                              type="button"
                              onClick={() => {
                                if (action.id === "accuracy") {
                                  void cleanupTranscript();
                                } else if (action.id === "speakers") {
                                  void identifySpeakers();
                                } else {
                                  seedAction(action);
                                }
                              }}
                              initial={
                                reduceMotion ? undefined : { opacity: 0, y: 6 }
                              }
                              animate={
                                reduceMotion ? undefined : { opacity: 1, y: 0 }
                              }
                              transition={
                                reduceMotion
                                  ? undefined
                                  : { ...spring, delay: index * 0.03 }
                              }
                              className="cursor-pointer rounded-xl border border-border/55 bg-card/40 px-3 py-2.5 text-left transition-colors hover:border-border hover:bg-muted/30 active:scale-[0.96]"
                            >
                              <div className="text-[12.5px] font-medium text-foreground">
                                {action.label}
                              </div>
                              <p className="mt-1 text-[11.5px] leading-4 text-muted-foreground">
                                {action.description}
                              </p>
                            </motion.button>
                          ))}
                        </div>
                      </div>
                    </motion.div>
                  ) : null}


                  {tab === "calendar" ? (
                    <motion.div
                      key="calendar"
                      {...panelMotion}
                      className="space-y-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-[12px] font-medium text-foreground">
                          {tx("meeting.calendar.title", "Calendar (ICS)")}
                        </p>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-8 rounded-lg active:scale-[0.96]"
                          onClick={() => icsInputRef.current?.click()}
                        >
                          <Upload className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                          {tx("meeting.calendar.import", "Import .ics")}
                        </Button>
                        <input
                          ref={icsInputRef}
                          type="file"
                          accept=".ics,text/calendar"
                          className="hidden"
                          onChange={(event) => {
                            const file = event.target.files?.[0] ?? null;
                            event.target.value = "";
                            void onImportIcs(file);
                          }}
                        />
                      </div>

                      <p className="text-[11px] leading-4 text-muted-foreground">
                        {tx(
                          "meeting.calendar.help",
                          "Import a .ics export from Google/Outlook/Apple. Navin notifies you locally when a meeting starts and surfaces the conference link.",
                        )}
                      </p>

                      <ul className="grid gap-2 sm:grid-cols-2">
                        {upcoming.length === 0 ? (
                          <li className="rounded-xl border border-dashed border-border/60 px-3 py-6 text-center text-[11.5px] text-muted-foreground sm:col-span-2">
                            {tx(
                              "meeting.calendar.empty",
                              "No upcoming events loaded.",
                            )}
                          </li>
                        ) : (
                          upcoming.slice(0, 12).map((event) => {
                            const joinUrl = meetingJoinUrl(event);
                            const linked = active.calendarUid === event.uid;
                            return (
                              <li
                                key={event.uid}
                                className={cn(
                                  "rounded-xl border p-2.5 transition-colors",
                                  linked
                                    ? "border-foreground/45 bg-muted/40"
                                    : "border-border/60 hover:border-foreground/40 hover:bg-muted/25",
                                )}
                              >
                                <div className="text-[12.5px] font-medium text-foreground">
                                  {event.summary}
                                </div>
                                <div className="mt-0.5 text-[11px] tabular-nums text-muted-foreground">
                                  {formatEventWhen(event.start, i18n.language)}
                                </div>
                                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="ghost"
                                    className="h-7 rounded-lg px-2 text-[11px] active:scale-[0.96]"
                                    onClick={() => {
                                      updateActive({
                                        calendarUid: event.uid,
                                        conferenceUrl:
                                          joinUrl ||
                                          active.conferenceUrl ||
                                          undefined,
                                        title: active.title || event.summary,
                                        notes: active.notes
                                          ? active.notes
                                          : [
                                              event.location
                                                ? `Location: ${event.location}`
                                                : null,
                                              event.description || null,
                                            ]
                                              .filter(Boolean)
                                              .join("\n"),
                                      });
                                      track("calendar.linked", event.summary);
                                    }}
                                  >
                                    {linked
                                      ? tx("meeting.calendar.linked", "Linked")
                                      : tx(
                                          "meeting.calendar.link",
                                          "Link to meeting",
                                        )}
                                  </Button>
                                  {joinUrl ? (
                                    <Button
                                      type="button"
                                      size="sm"
                                      variant="outline"
                                      className="h-7 rounded-lg px-2 text-[11px] active:scale-[0.96]"
                                      onClick={() => {
                                        openConference(joinUrl);
                                        track("calendar.joined", event.summary);
                                      }}
                                    >
                                      <Video
                                        className="mr-1 h-3 w-3"
                                        aria-hidden
                                      />
                                      {tx("meeting.calendar.join", "Join")}
                                    </Button>
                                  ) : null}
                                </div>
                              </li>
                            );
                          })
                        )}
                      </ul>

                      <div className="space-y-2 rounded-xl bg-muted/25 px-3 py-2.5 text-[11px] text-muted-foreground">
                        <p>
                          <Bell className="mr-1 inline h-3 w-3" aria-hidden />
                          {tx(
                            "meeting.calendar.detectNote",
                            "Auto-detect: desktop notification ~5 min before imported events.",
                          )}
                        </p>
                        <label className="flex cursor-pointer items-start gap-2">
                          <input
                            type="checkbox"
                            checked={autoJoin}
                            onChange={(event) => {
                              setAutoJoin(event.target.checked);
                              track(
                                "calendar.autojoin",
                                event.target.checked ? "on" : "off",
                              );
                            }}
                            className="mt-0.5 h-3.5 w-3.5 accent-foreground"
                          />
                          <span>
                            {tx(
                              "meeting.calendar.autoJoin",
                              "Open the conference link automatically at start time.",
                            )}
                          </span>
                        </label>
                      </div>
                    </motion.div>
                  ) : null}

                  {tab === "templates" ? (
                    <motion.div
                      key="templates"
                      {...panelMotion}
                      className="space-y-3"
                    >
                      <ul className="grid gap-2 sm:grid-cols-2">
                        {templates.map((row) => (
                          <li key={row.id}>
                            <button
                              type="button"
                              onClick={() => {
                                updateActive({ templateId: row.id });
                                track("template.selected", row.id);
                              }}
                              className={cn(
                                "h-full w-full cursor-pointer rounded-xl border p-3 text-left transition-colors active:scale-[0.96]",
                                active.templateId === row.id
                                  ? "border-foreground/45 bg-muted/40"
                                  : "border-border/60 hover:border-foreground/40 hover:bg-muted/25",
                              )}
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-[12.5px] font-medium text-foreground">
                                  {row.name}
                                </span>
                                {!row.builtin ? (
                                  <span className="rounded-md bg-muted/60 px-1.5 py-0.5 text-[10.5px] text-muted-foreground">
                                    {tx("meeting.customBadge", "Custom")}
                                  </span>
                                ) : null}
                              </div>
                              <p className="mt-1 max-h-16 overflow-hidden text-[11.5px] leading-4 text-muted-foreground">
                                {row.instructions}
                              </p>
                            </button>
                          </li>
                        ))}
                      </ul>

                      <div className="rounded-xl border border-border/55 p-3">
                        <p className="text-[12px] font-medium text-foreground">
                          {tx(
                            "meeting.customTemplateTitle",
                            "Custom summary template",
                          )}
                        </p>
                        <div className="mt-2 grid gap-2">
                          <input
                            value={customTemplateName}
                            onChange={(event) =>
                              setCustomTemplateName(event.target.value)
                            }
                            placeholder={tx(
                              "meeting.customTemplateName",
                              "Template name",
                            )}
                            className="h-8 rounded-lg border border-border/60 bg-background px-2.5 text-[12.5px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40"
                          />
                          <Textarea
                            value={customTemplateBody}
                            onChange={(event) =>
                              setCustomTemplateBody(event.target.value)
                            }
                            placeholder={tx(
                              "meeting.customTemplateBody",
                              "Instructions: sections, tone, language, required fields...",
                            )}
                            className="min-h-[6rem] rounded-xl text-[12.5px] leading-5"
                          />
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="h-8 w-fit rounded-lg active:scale-[0.96]"
                            onClick={saveCustomTemplate}
                            disabled={
                              !customTemplateName.trim() ||
                              !customTemplateBody.trim()
                            }
                          >
                            {tx("meeting.customTemplateSave", "Save template")}
                          </Button>
                        </div>
                      </div>
                    </motion.div>
                  ) : null}
                </AnimatePresence>
              </div>
            </>
          )}
        </section>
      </div>
      <ConfirmDialog
        open={confirmDeleteId !== null}
        title={tx("meeting.deleteConfirmTitle", "Delete this meeting?")}
        description={tx(
          "meeting.deleteConfirmBody",
          "The transcript, notes and minutes will be lost. This cannot be undone.",
        )}
        confirmLabel={tx("meeting.delete", "Delete")}
        onCancel={() => setConfirmDeleteId(null)}
        onConfirm={() => {
          if (confirmDeleteId) deleteMeeting(confirmDeleteId);
          setConfirmDeleteId(null);
        }}
      />
    </div>
  );
}
