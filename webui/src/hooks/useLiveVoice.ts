// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useVoiceSession, type VoiceSessionErrorKey } from "@/hooks/useVoiceSession";
import type { PendingChoice } from "@/lib/choices";
import {
  choiceAnswerFromMatch,
  completionCueWanted,
  liveVoiceStateFrom,
  matchChoiceFromSpeech,
  answerExpected,
  speechDedupeKey,
  speechForChoice,
  speechTextFromMarkdown,
  spokenProgressFromEvent,
  isSpokenStop,
  transcriptIsMeaningful,
  type LiveVoiceErrorKey,
  type LiveVoiceState,
  type SpeechTextStrings,
} from "@/lib/live-voice";
import { playCompletionChime, playSentCue } from "@/lib/live-voice-chime";
import type { NavinClient } from "@/lib/navin-client";
import { notifyUser } from "@/lib/os-notification";
import type { UIMessage } from "@/lib/types";
import type { MicrophonePermission, VoiceDeviceList } from "@/lib/voice-devices";

/** Everything the voice bar and the composer toggle need. */
export interface LiveVoiceControl {
  state: LiveVoiceState;
  enabled: boolean;
  error: LiveVoiceErrorKey | null;
  errorDetail: string | null;
  muted: boolean;
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
  /** Send what the user said as a chat turn (flagged as a voice turn upstream). */
  onUtterance: (text: string) => void;
  wantsWav?: boolean;
  disabled?: boolean;
}

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
  onUtterance,
  wantsWav = false,
  disabled = false,
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
    const choice = pendingChoicesRef.current[0];
    const context = lastAssistantContext(messagesRef.current, speechStringsRef.current);
    // A bare "oui" / "yeah" is an answer when a question is on the table,
    // otherwise it is a breath the STT put words on.
    if (!transcriptIsMeaningful(raw, { answerExpected: Boolean(choice) || answerExpected(context) })) return;
    setLastHeard(raw);
    setLastPrompt(null);
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
  stopSessionRef.current = session.stop;
  requestPromptRef.current = session.requestPrompt;

  const { speak, start: startSession, stop: stopSession, stopSpeaking, setMuted } = session;

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
    setEnabled(true);
    void startSession();
  }, [disabled, isStreaming, speechStrings, startSession, stop]);

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
    if (!enabled || session.state !== "listening") return;
    let onScreen: Map<string, number> | null = null;
    for (const message of messages) {
      if (message.role !== "assistant" || message.kind === "trace" || message.isStreaming) continue;
      if (spokenIdsRef.current.has(message.id)) continue;
      spokenIdsRef.current.add(message.id);
      const text = speechTextFromMarkdown(message.content, speechStrings);
      if (!text) continue;
      if (spokenProgressRef.current.has(`${message.turnId ?? ""}:${speechDedupeKey(text)}`)) continue;
      onScreen ??= assistantSpeechCounts(messages, speechStrings);
      if (!claimSpeechKey(spokenKeysRef.current, onScreen, speechDedupeKey(text))) continue;
      speak(text);
    }
  }, [enabled, messages, session.state, speak, speechStrings]);

  // The transcript groups progress with tool traces. Hear the agent's actual
  // explanations as they arrive, while keeping raw tool output silent.
  useEffect(() => {
    if (!enabled || !chatId || session.state !== "listening") return;
    return client.onChat(chatId, (event) => {
      const progress = spokenProgressFromEvent(event);
      if (!progress || !enabledRef.current) return;
      const text = speechTextFromMarkdown(progress.text, speechStrings);
      const key = `${progress.turnId}:${speechDedupeKey(text)}`;
      if (!text || spokenProgressRef.current.has(key)) return;
      spokenProgressRef.current.add(key);
      speak(text);
    });
  }, [chatId, client, enabled, session.state, speak, speechStrings]);

  // A question card is read aloud once; the next utterance answers it.
  useEffect(() => {
    if (!enabled || session.state !== "listening") return;
    const choice = pendingChoices[0];
    if (!choice || announcedChoicesRef.current.has(choice.requestId)) return;
    announcedChoicesRef.current.add(choice.requestId);
    speak(speechForChoice(choice, choiceStrings));
  }, [choiceStrings, enabled, pendingChoices, session.state, speak]);

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
  }, [enabled, isStreaming, t]);

  // The session belongs to one chat: switching threads hangs up.
  const chatIdRef = useRef(chatId);
  useEffect(() => {
    if (chatIdRef.current === chatId) return;
    chatIdRef.current = chatId;
    if (enabledRef.current) stop();
  }, [chatId, stop]);

  const state = liveVoiceStateFrom({
    enabled,
    sessionState: session.state,
    muted: session.muted,
    ttsPlaying: session.ttsPlaying,
    agentWorking: isStreaming,
    error,
  });

  return {
    state,
    enabled,
    error,
    errorDetail,
    muted: session.muted,
    levels: session.levels,
    inputLevel: session.inputLevel,
    hearing: session.hearing,
    noiseFloorDb: session.noiseFloorDb,
    lastHeard,
    lastPrompt,
    isTranscribing: session.isTranscribing,
    isRewriting: rewriting > 0,
    userSpeaking: session.isSpeaking,
    awaitingPermission: session.awaitingPermission,
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
