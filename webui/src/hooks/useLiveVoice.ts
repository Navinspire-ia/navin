// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useRealtimeVoiceSession } from "@/hooks/useRealtimeVoiceSession";
import { useVoiceSession, type VoiceSessionErrorKey } from "@/hooks/useVoiceSession";
import { fetchLiveRealtimeStatus, searchNavinDocs } from "@/lib/api";
import type { PendingApproval } from "@/lib/approvals";
import type { PendingChoice } from "@/lib/choices";
import {
  approvalFromSpeech,
  choiceAnswerFromMatch,
  completionCueWanted,
  liveVoiceStateFrom,
  matchChoiceFromSpeech,
  answerExpected,
  speechDedupeKey,
  speechForApproval,
  speechForChoice,
  speechTextFromMarkdown,
  spokenProgressFromEvent,
  isSpokenStop,
  transcriptIsMeaningful,
  type LiveVoiceErrorKey,
  type LiveVoiceState,
  type SpeechTextStrings,
} from "@/lib/live-voice";
import { type LiveDigest, liveDigest, offerAnswer } from "@/lib/live-digest";
import { playCompletionChime, playSentCue } from "@/lib/live-voice-chime";
import type { NavinClient } from "@/lib/navin-client";
import { notifyUser } from "@/lib/os-notification";
import type { UIMessage } from "@/lib/types";
import { describeScreen, openPlace, showControl } from "@/lib/ui-tour";
import type { MicrophonePermission, VoiceDeviceList } from "@/lib/voice-devices";

/** Everything the voice bar and the composer toggle need. */
export interface LiveVoiceControl {
  state: LiveVoiceState;
  /** "realtime" = one voice-to-voice model; "pipeline" = STT -> chat -> TTS. */
  engine: LiveVoiceEngine;
  /** The realtime model in use, when engine is "realtime". */
  realtimeModel: string | null;
  enabled: boolean;
  error: LiveVoiceErrorKey | null;
  errorDetail: string | null;
  muted: boolean;
  /** Loudness of the agent's voice right now, 0..1; read per frame (Live avatar). */
  speechLevel: () => number;
  /** Recent meter history, 0..1 per frame, oldest first. */
  levels: number[];
  /** Current microphone level, 0..1, relative to the room's noise floor. */
  inputLevel: number;
  /** The microphone hears something loud enough to be speech right now. */
  hearing: boolean;
  noiseFloorDb: number | null;
  /** Raw transcript of the last utterance, shown so the user can trust the loop. */
  lastHeard: string | null;
  /** The cleaned-up message that was actually sent for the last utterance. */
  lastPrompt: string | null;
  isTranscribing: boolean;
  /** The gateway is turning the last transcript into a prompt. */
  isRewriting: boolean;
  userSpeaking: boolean;
  /** getUserMedia is waiting on the browser / OS permission dialog. */
  awaitingPermission: boolean;
  permission: MicrophonePermission;
  devices: VoiceDeviceList;
  inputDeviceId: string | null;
  outputDeviceId: string | null;
  /** Label of the microphone currently open, from the platform. */
  activeInputLabel: string | null;
  refreshDevices: () => Promise<VoiceDeviceList>;
  selectInputDevice: (deviceId: string | null) => Promise<void>;
  selectOutputDevice: (deviceId: string | null) => void;
  toggle: () => void;
  stop: () => void;
  setMuted: (value: boolean) => void;
  /** Cut the assistant voice short without leaving the conversation. */
  skipSpeech: () => void;
  dismissError: () => void;
}

export type LiveVoiceEngine = "pipeline" | "realtime";

const LIVE_DICTATION_KEY = "navin.live.dictation";

/** Dictation: every utterance goes to the chat as a typed prompt (no voice model deciding). */
export function liveDictationEnabled(): boolean {
  try { return globalThis.localStorage?.getItem(LIVE_DICTATION_KEY) === "1"; } catch { return false; }
}

export function setLiveDictation(on: boolean): void {
  try {
    if (on) globalThis.localStorage?.setItem(LIVE_DICTATION_KEY, "1");
    else globalThis.localStorage?.removeItem(LIVE_DICTATION_KEY);
  } catch { /* storage unavailable: keep the default */ }
}

/** Plain text of the last assistant message, as context for the prompt rewrite. */
export function lastAssistantContext(
  messages: readonly UIMessage[],
  strings: SpeechTextStrings,
  maxChars = 600,
): string | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== "assistant" || message.kind === "trace") continue;
    const text = speechTextFromMarkdown(message.content, strings, { maxChars: Math.max(80, maxChars) });
    if (text) return text.slice(0, maxChars);
  }
  return undefined;
}

interface UseLiveVoiceOptions {
  client: NavinClient;
  chatId: string | null;
  messages: UIMessage[];
  isStreaming: boolean;
  pendingChoices: PendingChoice[];
  respondToChoice: (
    requestId: string,
    optionId: string,
    skipped?: boolean,
    customText?: string,
  ) => void;
  /** Tool calls waiting for the user's go: read aloud, answered by voice. */
  pendingApprovals?: PendingApproval[];
  respondToApproval?: (requestId: string, allowed: boolean, remember?: boolean) => void;
  /** Send what the user said as a chat turn (flagged as a voice turn upstream). */
  onUtterance: (text: string) => void;
  wantsWav?: boolean;
  disabled?: boolean;
  /** Gateway API token: realtime availability, Navin docs search, realtime bridge. */
  token?: string;
  /** Voice of the realtime model (empty = the model's default). */
  realtimeVoice?: string;
}

const NO_APPROVALS: PendingApproval[] = [];

function assistantMessageIds(messages: readonly UIMessage[]): Set<string> {
  const ids = new Set<string>();
  for (const message of messages) {
    if (message.role === "assistant") ids.add(message.id);
  }
  return ids;
}

/**
 * How many finished assistant messages say each text. Message ids are not
 * stable (streaming placeholder swapped for the final message, history reload
 * after a run renumbers everything), so a message is identified by its spoken
 * text and counted: the same text is read again only when the transcript
 * really contains it one more time ("C'est fait." as a new answer).
 */
export function assistantSpeechCounts(
  messages: readonly UIMessage[],
  strings: SpeechTextStrings,
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const message of messages) {
    if (message.role !== "assistant" || message.kind === "trace" || message.isStreaming) continue;
    const text = speechTextFromMarkdown(message.content, strings);
    if (!text) continue;
    const key = speechDedupeKey(text);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

/**
 * Claim one reading of *key*: true when the transcript holds more copies of
 * that text than were read so far (the history seeded at call start counts
 * as read). Records the reading.
 */
export function claimSpeechKey(
  spoken: Map<string, number>,
  onScreen: ReadonlyMap<string, number>,
  key: string,
): boolean {
  const read = spoken.get(key) ?? 0;
  if (read >= (onScreen.get(key) ?? 0)) return false;
  spoken.set(key, read + 1);
  return true;
}

/**
 * Live voice conversation with the agent.
 *
 * Listens through the realtime voice session, sends each utterance as a turn
 * (or as the answer to the question card on screen), reads every finished
 * assistant message aloud as it lands, and rings when a long run ends.
 */
export function useLiveVoice({
  client,
  chatId,
  messages,
  isStreaming,
  pendingChoices,
  respondToChoice,
  pendingApprovals = NO_APPROVALS,
  respondToApproval,
  onUtterance,
  wantsWav = false,
  disabled = false,
  token = "",
  realtimeVoice = "",
}: UseLiveVoiceOptions): LiveVoiceControl {
  const { t, i18n } = useTranslation();
  const [enabled, setEnabled] = useState(false);
  const [error, setError] = useState<LiveVoiceErrorKey | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [lastHeard, setLastHeard] = useState<string | null>(null);
  const [lastPrompt, setLastPrompt] = useState<string | null>(null);
  const [rewriting, setRewriting] = useState(0);

  const enabledRef = useRef(false);
  enabledRef.current = enabled;
  const requestPromptRef = useRef<
    (raw: string, context?: string) => Promise<{ text: string; raw: string; rewritten: boolean }>
  >(async (raw) => ({ text: raw, raw, rewritten: false }));
  const spokenIdsRef = useRef<Set<string>>(new Set());
  // Ids are not stable across the streaming placeholder -> final message swap
  // or a history reload; the spoken text is the second, decisive guard.
  const spokenKeysRef = useRef<Map<string, number>>(new Map());
  const spokenProgressRef = useRef(new Set<string>());
  const conversationEpochRef = useRef(0);
  const announcedChoicesRef = useRef<Set<string>>(new Set());
  const pendingChoicesRef = useRef(pendingChoices);
  pendingChoicesRef.current = pendingChoices;
  const respondToChoiceRef = useRef(respondToChoice);
  respondToChoiceRef.current = respondToChoice;
  const announcedApprovalsRef = useRef<Set<string>>(new Set());
  const pendingApprovalsRef = useRef(pendingApprovals);
  pendingApprovalsRef.current = pendingApprovals;
  const respondToApprovalRef = useRef(respondToApproval);
  respondToApprovalRef.current = respondToApproval;
  const speakRef = useRef<(text: string) => void>(() => undefined);
  const onUtteranceRef = useRef(onUtterance);
  onUtteranceRef.current = onUtterance;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const wasStreamingRef = useRef(isStreaming);
  const runStartedAtRef = useRef<number | null>(null);
  const stopSessionRef = useRef<() => Promise<void>>(async () => undefined);

  const speechStrings = useMemo(
    () => ({
      language: i18n.resolvedLanguage || i18n.language,
      codeOmitted: t("thread.composer.liveVoice.speech.codeOmitted", {
        defaultValue: "code block, see the chat",
      }),
      restInChat: t("thread.composer.liveVoice.speech.restInChat", {
        defaultValue: "The rest is in the chat.",
      }),
      link: t("thread.composer.liveVoice.speech.link", { defaultValue: "link" }),
    }),
    [i18n.language, i18n.resolvedLanguage, t],
  );
  const speechStringsRef = useRef(speechStrings);
  speechStringsRef.current = speechStrings;
  const deviceLabels = useMemo(
    () => ({
      input: (index: number) =>
        t("thread.composer.liveVoice.devices.inputFallback", { index, defaultValue: `Microphone ${index}` }),
      output: (index: number) =>
        t("thread.composer.liveVoice.devices.outputFallback", { index, defaultValue: `Speaker ${index}` }),
    }),
    [t],
  );
  const choiceStrings = useMemo(
    () => ({
      question: t("thread.composer.liveVoice.speech.question", { defaultValue: "Question" }),
      option: (letter: string) =>
        t("thread.composer.liveVoice.speech.option", { letter, defaultValue: `Option ${letter}` }),
      recommended: t("thread.composer.liveVoice.speech.recommended", { defaultValue: "recommended" }),
      other: t("thread.composer.liveVoice.speech.other", {
        defaultValue: "You can also answer in your own words",
      }),
    }),
    [t],
  );
  const approvalStrings = useMemo(
    () => ({
      title: t("thread.composer.liveVoice.speech.approval", { defaultValue: "Approval needed" }),
      prompt: t("thread.composer.liveVoice.speech.approvalPrompt", {
        defaultValue: "Say yes to approve or no to refuse.",
      }),
    }),
    [t],
  );
  // A plan, a review or a long report is offered as a summary, not read out.
  const digestStrings = useMemo(() => {
    const phrase = (when: "last" | "new", digest: LiveDigest) =>
      t(`thread.composer.liveVoice.digest.${when}.${digest.kind}`, {
        count: digest.count,
        defaultValue: when === "last"
          ? "The last message is long. Shall I sum it up?"
          : "Done. It is a long answer. Shall I sum it up?",
      });
    return {
      last: (digest: LiveDigest) => phrase("last", digest),
      fresh: (digest: LiveDigest) => phrase("new", digest),
      request: (digest: LiveDigest) =>
        t("thread.composer.liveVoice.digest.request", {
          what: t(`thread.composer.liveVoice.digest.what.${digest.kind}`, { defaultValue: "the message" }),
          defaultValue: "Sum up the message above for me out loud, like a colleague would.",
        }),
      ok: t("thread.composer.liveVoice.digest.ok", { defaultValue: "Okay." }),
    };
  }, [t]);
  const digestStringsRef = useRef(digestStrings);
  digestStringsRef.current = digestStrings;
  /** The summary the voice just offered; the next yes or no answers it. */
  const offerRef = useRef<LiveDigest | null>(null);
  /** The agent is giving the summary it was asked for: read it, never offer to sum it up. */
  const summaryAskedRef = useRef(false);

  const approvalRetryRef = useRef("");
  approvalRetryRef.current = t("thread.composer.liveVoice.speech.approvalRetry", {
    defaultValue: "I did not catch that. Yes or no?",
  });

  // What the microphone heard is shown as-is; what goes to the agent is the
  // message the user would have typed. A spoken answer to the question card
  // on screen is matched right away (a letter, an option, "skip") and never
  // rewritten; anything else goes through the gateway rewrite first.
  const handleTranscript = useCallback((raw: string) => {
    if (!enabledRef.current) return;
    if (isSpokenStop(raw)) {
      conversationEpochRef.current += 1;
      setLastHeard(raw);
      setLastPrompt(raw);
      onUtteranceRef.current("/stop");
      return;
    }
    const epoch = conversationEpochRef.current;
    const approval = respondToApprovalRef.current ? pendingApprovalsRef.current[0] : undefined;
    const choice = pendingChoicesRef.current[0];
    const context = lastAssistantContext(messagesRef.current, speechStringsRef.current);
    // A bare "oui" / "yeah" is an answer when a question is on the table,
    // otherwise it is a breath the STT put words on.
    const expected = Boolean(approval) || Boolean(choice) || Boolean(offerRef.current) || answerExpected(context);
    if (!transcriptIsMeaningful(raw, { answerExpected: expected })) return;
    setLastHeard(raw);
    setLastPrompt(null);
    if (approval) {
      // A suspended tool call waits on this answer: a clear yes or no settles
      // it (never "always allow" by voice); anything else asks again instead
      // of starting a turn the agent cannot run until the card is answered.
      const decision = approvalFromSpeech(raw);
      if (!decision) {
        speakRef.current(approvalRetryRef.current);
        return;
      }
      respondToApprovalRef.current?.(approval.requestId, decision === "allow", false);
      setLastPrompt(raw);
      void playSentCue();
      return;
    }
    if (choice) {
      const match = matchChoiceFromSpeech(raw, choice);
      if (match && match.kind !== "other") {
        const answer = choiceAnswerFromMatch(match, choice);
        respondToChoiceRef.current(choice.requestId, answer.optionId, answer.skipped, answer.customText);
        setLastPrompt(raw);
        void playSentCue();
        return;
      }
    }
    const offer = offerRef.current;
    if (offer) {
      offerRef.current = null;
      const answer = offerAnswer(raw);
      if (answer === "yes") {
        // Like asking a colleague: the agent explains its own plan or report.
        const request = digestStringsRef.current.request(offer);
        summaryAskedRef.current = true;
        onUtteranceRef.current(request);
        setLastPrompt(request);
        void playSentCue();
        return;
      }
      if (answer === "no") {
        speakRef.current(digestStringsRef.current.ok);
        return;
      }
    }
    setRewriting((count) => count + 1);
    void requestPromptRef
      .current(raw, context)
      .then((result) => {
        if (!enabledRef.current || epoch !== conversationEpochRef.current) return;
        const text = result.text.trim() || raw;
        const choiceNow = pendingChoicesRef.current[0];
        if (choiceNow) {
          const match = matchChoiceFromSpeech(text, choiceNow) ?? { kind: "other" as const, text };
          const answer = choiceAnswerFromMatch(match, choiceNow);
          respondToChoiceRef.current(choiceNow.requestId, answer.optionId, answer.skipped, answer.customText);
        } else {
          onUtteranceRef.current(text);
        }
        setLastPrompt(text);
        void playSentCue();
      })
      .finally(() => setRewriting((count) => Math.max(0, count - 1)));
  }, []);

  const handleError = useCallback((key: VoiceSessionErrorKey, detail?: string) => {
    // A failed utterance is a hiccup (the session hook quietly re-registers a
    // forgotten server session); a session that could not be revived, a
    // missing plan or a denied microphone means the conversation cannot go on.
    const sessionLost = key === "failed" && detail === "session_lost";
    setError(sessionLost ? "sessionLost" : key);
    setErrorDetail(sessionLost ? null : (detail ?? null));
    if (key !== "failed" || sessionLost) {
      conversationEpochRef.current += 1;
      enabledRef.current = false;
      setEnabled(false);
      void stopSessionRef.current();
    }
  }, []);

  const session = useVoiceSession({
    client,
    disabled,
    wantsWav,
    deviceFallbackLabels: deviceLabels,
    onTranscript: handleTranscript,
    onError: handleError,
  });
  requestPromptRef.current = session.requestPrompt;

  // Realtime engine: a voice-to-voice model converses; the agent works. Its
  // tools are the same actions the pipeline performs from transcripts.
  const [engine, setEngine] = useState<LiveVoiceEngine>("pipeline");
  const engineRef = useRef<LiveVoiceEngine>(engine);
  engineRef.current = engine;
  const [realtimeModel, setRealtimeModel] = useState<string | null>(null);
  const pipelineReadyRef = useRef<boolean | undefined>(undefined);
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const isStreamingRef = useRef(isStreaming);
  isStreamingRef.current = isStreaming;
  const choiceStringsRef = useRef(choiceStrings);
  choiceStringsRef.current = choiceStrings;
  const approvalStringsRef = useRef(approvalStrings);
  approvalStringsRef.current = approvalStrings;

  const realtimeTool = useCallback(async (name: string, args: Record<string, unknown>): Promise<string> => {
    switch (name) {
      case "ask_agent": {
        const request = String(args.request ?? "").trim();
        if (!request) return "error: empty request";
        onUtteranceRef.current(request);
        setLastPrompt(request);
        void playSentCue();
        return "Sent to the agent, which is working on it. Its updates will arrive as [Navin agent] messages.";
      }
      case "stop_agent":
        onUtteranceRef.current("/stop");
        return "The agent was asked to stop.";
      case "answer_approval": {
        const approval = respondToApprovalRef.current ? pendingApprovalsRef.current[0] : undefined;
        if (!approval) return "No approval is waiting.";
        const allow = args.allow === true;
        respondToApprovalRef.current?.(approval.requestId, allow, false);
        void playSentCue();
        return allow ? "Approved: the agent goes on." : "Refused: the agent will not do it.";
      }
      case "answer_question": {
        const choice = pendingChoicesRef.current[0];
        if (!choice) return "No question is waiting.";
        const text = String(args.answer ?? "").trim();
        const match = matchChoiceFromSpeech(text, choice) ?? { kind: "other" as const, text };
        const answer = choiceAnswerFromMatch(match, choice);
        respondToChoiceRef.current(choice.requestId, answer.optionId, answer.skipped, answer.customText);
        void playSentCue();
        return "Answer sent to the agent.";
      }
      case "chat_status": {
        const approval = respondToApprovalRef.current ? pendingApprovalsRef.current[0] : undefined;
        const choice = pendingChoicesRef.current[0];
        return JSON.stringify({
          agentWorking: isStreamingRef.current,
          // Enough for the voice to sum up a plan or a report itself.
          lastAgentMessage: lastAssistantContext(messagesRef.current, speechStringsRef.current, 4000) ?? null,
          pendingApproval: approval ? speechForApproval(approval, approvalStringsRef.current) : null,
          pendingQuestion: choice ? speechForChoice(choice, choiceStringsRef.current) : null,
        });
      }
      case "navin_docs": {
        const query = String(args.query ?? "").trim();
        if (!query || !tokenRef.current) return "The documentation is not reachable right now.";
        const results = await searchNavinDocs(tokenRef.current, query);
        if (!results.length) return "Nothing in the documentation matches. Say so rather than guessing.";
        return results
          .map((result) => `## ${result.title} > ${result.section}\n${result.excerpt}\n(${result.url})`)
          .join("\n\n")
          .slice(0, 6000);
      }
      case "open_in_app":
        return openPlace(String(args.place ?? "").trim());
      case "screen_controls":
        return describeScreen();
      case "show_control": {
        const label = String(args.label ?? "").trim();
        if (!label) return "error: which control?";
        return showControl(label, args.click === true);
      }
      default:
        return `Unknown tool ${name}.`;
    }
  }, []);

  const startPipelineRef = useRef<() => Promise<void>>(async () => undefined);
  const realtime = useRealtimeVoiceSession({
    client,
    token,
    language: speechStrings.language,
    voice: realtimeVoice || undefined,
    inputDeviceId: session.inputDeviceId,
    outputDeviceId: session.outputDeviceId,
    onTool: realtimeTool,
    onUserTranscript: (text) => setLastHeard(text),
    // The realtime model is unavailable (busy, unreachable, dropped): the
    // conversation goes on with the pipeline, without a dead end.
    onFallback: (_code, detail) => {
      if (!enabledRef.current) return;
      if (pipelineReadyRef.current === false) {
        conversationEpochRef.current += 1;
        enabledRef.current = false;
        setEnabled(false);
        setError("realtimeUnavailable");
        // The provider's own words when there are some; never a raw error code.
        setErrorDetail(detail || null);
        void stopSessionRef.current();
        return;
      }
      engineRef.current = "pipeline";
      setEngine("pipeline");
      setRealtimeModel(null);
      void startPipelineRef.current();
    },
    onError: (key, detail) => handleError(key, detail),
  });

  const { start: startSession } = session;
  startPipelineRef.current = startSession;
  const stopSession = useCallback(async () => {
    await Promise.all([session.stop(), realtime.stop()]);
  }, [realtime.stop, session.stop]);
  stopSessionRef.current = stopSession;
  const speak = useCallback(
    (text: string) => (engineRef.current === "realtime" ? realtime.speak(text) : session.speak(text)),
    [realtime.speak, session.speak],
  );
  const stopSpeaking = useCallback(
    () => (engineRef.current === "realtime" ? realtime.stopSpeaking() : session.stopSpeaking()),
    [realtime.stopSpeaking, session.stopSpeaking],
  );
  const setMuted = useCallback(
    (value: boolean) => {
      session.setMuted(value);
      realtime.setMuted(value);
    },
    [realtime.setMuted, session.setMuted],
  );
  const activeState = engine === "realtime" ? realtime.state : session.state;

  const stop = useCallback(() => {
    conversationEpochRef.current += 1;
    enabledRef.current = false;
    setEnabled(false);
    setLastHeard(null);
    setLastPrompt(null);
    void stopSession();
  }, [stopSession]);

  const toggle = useCallback(() => {
    if (enabledRef.current) {
      stop();
      return;
    }
    if (disabled) return;
    setError(null);
    setErrorDetail(null);
    // History stays silent: only what the agent says from now on is read aloud.
    spokenIdsRef.current = assistantMessageIds(messagesRef.current);
    spokenKeysRef.current = assistantSpeechCounts(messagesRef.current, speechStrings);
    spokenProgressRef.current = new Set();
    conversationEpochRef.current += 1;
    announcedChoicesRef.current = new Set();
    runStartedAtRef.current = isStreaming ? Date.now() : null;
    wasStreamingRef.current = isStreaming;
    enabledRef.current = true;
    offerRef.current = null;
    setEnabled(true);
    void (async () => {
      // Realtime when the gateway has a voice-to-voice model and the user did
      // not pick the pipeline; the pipeline otherwise (and on any failure).
      let useRealtime = false;
      pipelineReadyRef.current = undefined;
      if (tokenRef.current && !liveDictationEnabled()) {
        try {
          const status = await fetchLiveRealtimeStatus(tokenRef.current);
          pipelineReadyRef.current = status.pipeline_ready;
          useRealtime = status.available && status.engine !== "pipeline";
          setRealtimeModel(useRealtime ? status.model ?? null : null);
        } catch {
          useRealtime = false;
        }
      }
      if (!enabledRef.current) return;
      engineRef.current = useRealtime ? "realtime" : "pipeline";
      setEngine(engineRef.current);
      if (useRealtime) void realtime.start();
      else void startSession();
    })();
  }, [disabled, isStreaming, realtime.start, speechStrings, startSession, stop]);

  const dismissError = useCallback(() => {
    setError(null);
    setErrorDetail(null);
  }, []);

  // One utterance the server could not transcribe: show it, then move on.
  useEffect(() => {
    if (error !== "failed" || !enabled) return;
    const timer = window.setTimeout(() => {
      setError(null);
      setErrorDetail(null);
    }, 6_000);
    return () => window.clearTimeout(timer);
  }, [enabled, error, errorDetail]);

  // Read each finished assistant message once, in order, as soon as it lands:
  // the plan before the work, the report after it.
  useEffect(() => {
    if (!enabled || activeState !== "listening") return;
    let onScreen: Map<string, number> | null = null;
    for (const message of messages) {
      if (message.role !== "assistant" || message.kind === "trace" || message.isStreaming) continue;
      if (spokenIdsRef.current.has(message.id)) continue;
      spokenIdsRef.current.add(message.id);
      const digest = summaryAskedRef.current ? null : liveDigest(message.content);
      summaryAskedRef.current = false;
      if (digest) {
        // A plan, review or long report: say what it is, offer the summary.
        offerRef.current = digest;
        speak(digestStringsRef.current.fresh(digest));
        continue;
      }
      const text = speechTextFromMarkdown(message.content, speechStrings);
      if (!text) continue;
      if (spokenProgressRef.current.has(`${message.turnId ?? ""}:${speechDedupeKey(text)}`)) continue;
      onScreen ??= assistantSpeechCounts(messages, speechStrings);
      if (!claimSpeechKey(spokenKeysRef.current, onScreen, speechDedupeKey(text))) continue;
      speak(text);
    }
  }, [activeState, enabled, messages, speak, speechStrings]);

  // The transcript groups progress with tool traces. Hear the agent's actual
  // explanations as they arrive, while keeping raw tool output silent.
  useEffect(() => {
    if (!enabled || !chatId || activeState !== "listening") return;
    return client.onChat(chatId, (event) => {
      const progress = spokenProgressFromEvent(event);
      if (!progress || !enabledRef.current) return;
      const text = speechTextFromMarkdown(progress.text, speechStrings);
      const key = `${progress.turnId}:${speechDedupeKey(text)}`;
      if (!text || spokenProgressRef.current.has(key)) return;
      spokenProgressRef.current.add(key);
      speak(text);
    });
  }, [activeState, chatId, client, enabled, speak, speechStrings]);

  speakRef.current = speak;

  // The agent "calls" the user when it needs them and they are elsewhere
  // (another window, another app): a ring and an OS banner that brings the
  // chat back. In front of the chat, the spoken question is the call.
  const callUser = useCallback((key: string, body: string) => {
    if (typeof document === "undefined" || !document.hidden) return;
    void playCompletionChime();
    void notifyUser({
      key: `live-voice-call:${key}`,
      title: t("thread.composer.liveVoice.callTitle", { defaultValue: "Navin needs you" }),
      body: body.length > 180 ? `${body.slice(0, 180).trimEnd()}...` : body,
      onActivate: () => window.focus(),
    });
  }, [t]);

  // An approval card is read aloud once; the next utterance answers it. It
  // comes before any question card: the run is suspended on it.
  useEffect(() => {
    if (!enabled || activeState !== "listening") return;
    const approval = respondToApproval ? pendingApprovals[0] : undefined;
    if (!approval || announcedApprovalsRef.current.has(approval.requestId)) return;
    announcedApprovalsRef.current.add(approval.requestId);
    const spoken = speechForApproval(approval, approvalStrings);
    speak(spoken);
    callUser(approval.requestId, spoken);
  }, [activeState, approvalStrings, callUser, enabled, pendingApprovals, respondToApproval, speak]);

  // A question card is read aloud once; the next utterance answers it.
  useEffect(() => {
    if (!enabled || activeState !== "listening") return;
    const choice = pendingChoices[0];
    if (!choice || announcedChoicesRef.current.has(choice.requestId)) return;
    announcedChoicesRef.current.add(choice.requestId);
    speak(speechForChoice(choice, choiceStrings));
    callUser(choice.requestId, choice.question);
  }, [activeState, callUser, choiceStrings, enabled, pendingChoices, speak]);

  // "Calls" the user back after a long run: chime, plus an OS banner when the
  // tab is in the background.
  useEffect(() => {
    const was = wasStreamingRef.current;
    wasStreamingRef.current = isStreaming;
    if (!enabled) return;
    if (isStreaming && !was) {
      runStartedAtRef.current = Date.now();
      return;
    }
    if (!isStreaming && was) {
      const startedAt = runStartedAtRef.current;
      runStartedAtRef.current = null;
      const duration = startedAt === null ? null : Date.now() - startedAt;
      // A turn that ends on a question (blocked, a decision to make) calls
      // the user back whatever its length.
      const lastWords = lastAssistantContext(messagesRef.current, speechStringsRef.current);
      if (answerExpected(lastWords)) {
        callUser(`question:${Date.now()}`, lastWords ?? "");
        return;
      }
      if (!completionCueWanted(duration)) return;
      void playCompletionChime();
      if (typeof document !== "undefined" && document.hidden) {
        void notifyUser({
          key: "live-voice-done",
          title: t("thread.composer.liveVoice.doneTitle", { defaultValue: "Navin is done" }),
          body: t("thread.composer.liveVoice.doneBody", {
            defaultValue: "The agent finished and is waiting for you.",
          }),
          onActivate: () => window.focus(),
        });
      }
    }
  }, [callUser, enabled, isStreaming, t]);

  // The session belongs to one chat: switching threads hangs up.
  const chatIdRef = useRef(chatId);
  useEffect(() => {
    if (chatIdRef.current === chatId) return;
    chatIdRef.current = chatId;
    if (enabledRef.current) stop();
  }, [chatId, stop]);

  const rt = engine === "realtime";
  const state = liveVoiceStateFrom({
    enabled,
    sessionState: activeState,
    muted: rt ? realtime.muted : session.muted,
    ttsPlaying: rt ? realtime.ttsPlaying : session.ttsPlaying,
    agentWorking: isStreaming,
    error,
  });

  return {
    state,
    engine,
    realtimeModel: rt ? realtimeModel : null,
    enabled,
    error,
    errorDetail,
    muted: rt ? realtime.muted : session.muted,
    speechLevel: rt ? realtime.speechLevel : session.speechLevel,
    levels: rt ? realtime.levels : session.levels,
    inputLevel: rt ? realtime.inputLevel : session.inputLevel,
    hearing: rt ? realtime.hearing : session.hearing,
    noiseFloorDb: rt ? null : session.noiseFloorDb,
    lastHeard,
    lastPrompt,
    isTranscribing: rt ? realtime.isTranscribing : session.isTranscribing,
    isRewriting: rewriting > 0,
    userSpeaking: rt ? realtime.isSpeaking : session.isSpeaking,
    awaitingPermission: rt ? realtime.awaitingPermission : session.awaitingPermission,
    permission: session.permission,
    devices: session.devices,
    inputDeviceId: session.inputDeviceId,
    outputDeviceId: session.outputDeviceId,
    activeInputLabel: session.activeInputLabel,
    refreshDevices: session.refreshDevices,
    selectInputDevice: session.selectInputDevice,
    selectOutputDevice: session.selectOutputDevice,
    toggle,
    stop,
    setMuted,
    skipSpeech: stopSpeaking,
    dismissError,
  };
}
