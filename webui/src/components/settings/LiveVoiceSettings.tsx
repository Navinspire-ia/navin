// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { type Dispatch, type SetStateAction, useState } from "react";
import {
  DefaultButton, Icon, PrimaryButton,
  SpinButton, Stack, Text, TextField, Toggle,
} from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { MediaModelChoice } from "./MediaModelChoice";
import { MediaModelPicker, MediaSettingsSurface } from "./MediaModelPicker";
import { SettingsPicker } from "./SettingsPicker";
import { liveVoiceSetup } from "@/lib/live-voice-setup";
import { liveDictationEnabled, setLiveDictation } from "@/hooks/useLiveVoice";
import { realtimeVoiceOptions, type LiveVoiceOption } from "@/lib/live-voices";
import { intlLocale } from "@/i18n/intl";
import type { SettingsPayload, TranscriptionSettingsUpdate, VoiceSettingsUpdate } from "@/lib/types";
import "./live-voice-settings.css";

interface Props {
  settings: SettingsPayload;
  transcription: TranscriptionSettingsUpdate;
  voice: VoiceSettingsUpdate;
  dirty: boolean;
  saving: boolean;
  onTranscription: Dispatch<SetStateAction<TranscriptionSettingsUpdate>>;
  onVoice: Dispatch<SetStateAction<VoiceSettingsUpdate>>;
  onSave: () => void;
  onSaveAndReturn: () => void;
  onBackToChat: () => void;
  onOpenProviders: () => void;
  onOpenAccount: () => void;
}

export function LiveVoiceSettings({
  settings, transcription, voice, dirty, saving, onTranscription, onVoice,
  onSave, onSaveAndReturn, onBackToChat, onOpenProviders, onOpenAccount,
}: Props) {
  const { t } = useTranslation();
  const tx = (key: string) => t(`settings.live.${key}`);
  const realtimeVoices = realtimeVoiceOptions(voice.liveRealtime);
  const languageNames = new Intl.DisplayNames([intlLocale()], { type: "language" });
  const voiceLabel = (item: LiveVoiceOption) => {
    const name = item.id.charAt(0).toUpperCase() + item.id.slice(1);
    return item.lang ? `${name} · ${languageNames.of(item.lang) ?? item.lang}` : name;
  };
  const reduced = useReducedMotion();
  const [dictation, setDictation] = useState(liveDictationEnabled);
  const setup = liveVoiceSetup(settings);
  const sttProviders = settings.transcription?.providers ?? [];
  const ttsProviders = settings.voice?.providers ?? [];
  const ttsProvider = ttsProviders.find((item) => item.name === voice.ttsProvider);
  const defaults = settings.voice?.managed_defaults;
  const canUseNavin = Boolean(defaults && sttProviders.some((p) => p.name === "navin" && p.configured)
    && ttsProviders.some((p) => p.name === "navin" && p.configured));
  const usesNavin = transcription.provider === "navin" && voice.ttsProvider === "navin";
  const title = dirty ? "unsaved" : setup.ready ? "readyTitle"
    : setup.reason === "plan_required" ? "planTitle"
    : setup.reason === "voice_disabled" || setup.reason === "stt_disabled" ? "enableTitle" : "setupTitle";
  const description = setup.ready ? (setup.mode === "navin" ? "managedReady" : "byokReady")
    : setup.reason === "plan_required" ? "planHint"
    : setup.reason === "voice_disabled" ? "disabledHint"
    : setup.reason === "stt_disabled" ? "listeningDisabledHint" : "setupHint";

  const useNavin = () => {
    if (!defaults) return;
    onTranscription((prev) => ({ ...prev, enabled: true, provider: "navin", model: defaults.stt_model }));
    onVoice((prev) => ({ ...prev, ttsProvider: "navin", ttsModel: defaults.tts_model,
      voice: defaults.voice, realtimeEnabled: null }));
  };

  return (
    <MediaSettingsSurface>
      <motion.section className="live-voice-settings" aria-label={tx("title")}
        initial={reduced ? false : { opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2 }} data-testid="live-voice-settings">
        <Stack tokens={{ childrenGap: 22 }}>
          <Stack tokens={{ childrenGap: 10 }}>
            <Text variant="xLarge" as="h2">{tx("title")}</Text>
            <Stack horizontal verticalAlign="center" tokens={{ childrenGap: 8 }} role="status">
              <Icon iconName={setup.ready && !dirty ? "CheckMark" : "Microphone"} />
              <Text variant="mediumPlus" styles={{ root: { fontWeight: 600 } }}>{tx(title)}</Text>
            </Stack>
            <Text>{tx(description)}</Text>
            <Text variant="small">{tx("microphoneHint")}</Text>
            {setup.reason === "plan_required" ? (
              <DefaultButton text={tx("planAccess")} onClick={onOpenAccount} />
            ) : null}
            {canUseNavin && (!usesNavin || !setup.ready) ? (
              <DefaultButton text={tx("useNavin")} onClick={useNavin} disabled={saving} />
            ) : null}
          </Stack>

          <fieldset className="live-voice-settings__fields" disabled={saving} aria-busy={saving}>
            <legend className="live-voice-settings__legend">{tx("services")}</legend>
            <div className="live-voice-settings__services">
              <section className="live-voice-settings__service" aria-labelledby="live-listening-title">
                <Text variant="large" as="h3" id="live-listening-title">{tx("listening")}</Text>
                <Text>{tx("listeningHint")}</Text>
                <MediaModelChoice kind="stt" choices={settings.media_models} className="w-full items-stretch"
                  value={transcription.provider && transcription.model ? `${transcription.provider}::${transcription.model}` : ""}
                  onChange={({ provider, model }) => onTranscription((prev) => ({ ...prev, provider, model, enabled: true }))}
                  providers={settings.media_providers} onOpenProviders={onOpenProviders} />
              </section>

              <section className="live-voice-settings__service" aria-labelledby="live-speaking-title">
                <Text variant="large" as="h3" id="live-speaking-title">{tx("speaking")}</Text>
                <Text>{tx("speakingHint")}</Text>
                <MediaModelChoice kind="tts" choices={settings.media_models} className="w-full items-stretch"
                  value={voice.ttsProvider && voice.ttsModel ? `${voice.ttsProvider}::${voice.ttsModel}` : ""}
                  onChange={({ provider, model }) => onVoice((prev) => ({ ...prev, ttsProvider: provider, ttsModel: model, voice: "auto" }))}
                  providers={settings.media_providers} onOpenProviders={onOpenProviders} />
                <MediaModelPicker key={`tts:${voice.ttsProvider}:${voice.ttsModel}`} kind="tts" provider={voice.ttsProvider}
                  configured={Boolean(ttsProvider?.configured)} model={voice.ttsModel} voice={voice.voice} voiceOnly
                  onModelChange={(ttsModel) => onVoice((prev) => ({ ...prev, ttsModel, voice: "auto" }))}
                  onVoiceChange={(next) => onVoice((prev) => ({ ...prev, voice: next }))} />
              </section>
            </div>

            <section className="live-voice-settings__service" aria-labelledby="live-realtime-title">
              <Text variant="large" as="h3" id="live-realtime-title">{tx("realtimeTitle")}</Text>
              <Text>{tx("realtimeHint")}</Text>
              <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={tx("realtimeEngine")}>
                {(["auto", "realtime", "pipeline"] as const).map((engine) => (
                  <button key={engine} type="button" role="radio" aria-checked={(voice.liveEngine ?? "auto") === engine}
                    onClick={() => onVoice((prev) => ({ ...prev, liveEngine: engine }))}
                    className={`rounded-full border px-3 py-1.5 text-[12.5px] transition-colors ${
                      (voice.liveEngine ?? "auto") === engine ? "border-foreground/70 bg-muted font-medium" : "border-border/70 text-muted-foreground hover:bg-muted/50"}`}>
                    {tx(`engine_${engine}`)}
                  </button>
                ))}
              </div>
              <Text variant="small">{tx(`engineHint_${voice.liveEngine ?? "auto"}`)}</Text>
              {(voice.liveEngine ?? "auto") !== "pipeline" ? (
                <>
                  <MediaModelChoice kind="live" choices={settings.media_models} allowNone className="w-full items-stretch"
                    value={voice.liveRealtime ?? ""}
                    onChange={({ value }) => onVoice((prev) => ({ ...prev, liveRealtime: value }))}
                    providers={settings.media_providers} onOpenProviders={onOpenProviders} />
                  <label className="flex flex-col gap-1 text-[13px]">
                    <span className="font-medium">{tx("realtimeVoice")}</span>
                    {realtimeVoices ? (
                      // Gemini Live and Nova Sonic: their voices, women then men; the
                      // avatar of the call follows the one picked here.
                      <select value={voice.liveRealtimeVoice ?? ""} data-testid="live-realtime-voice"
                        onChange={(event) => onVoice((prev) => ({ ...prev, liveRealtimeVoice: event.target.value }))}
                        className="h-9 rounded-lg border border-border bg-background px-2 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring">
                        <option value="">{tx("realtimeVoiceDefault")}</option>
                        {voice.liveRealtimeVoice && !realtimeVoices.some((item) => item.id === voice.liveRealtimeVoice) ? (
                          <option value={voice.liveRealtimeVoice}>{voice.liveRealtimeVoice}</option>
                        ) : null}
                        {(["F", "M"] as const).map((gender) => (
                          <optgroup key={gender} label={tx(gender === "F" ? "voiceFemale" : "voiceMale")}>
                            {realtimeVoices.filter((item) => item.gender === gender).map((item) => (
                              <option key={item.id} value={item.id}>{voiceLabel(item)}</option>
                            ))}
                          </optgroup>
                        ))}
                      </select>
                    ) : (
                      <input value={voice.liveRealtimeVoice ?? ""} maxLength={64} placeholder={tx("realtimeVoicePlaceholder")}
                        onChange={(event) => onVoice((prev) => ({ ...prev, liveRealtimeVoice: event.target.value }))}
                        className="h-9 rounded-lg border border-border bg-background px-2.5 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring" />
                    )}
                  </label>
                </>
              ) : null}
            </section>

            <details className="live-voice-settings__advanced">
              <summary>{tx("moreOptions")}</summary>
              <Stack tokens={{ childrenGap: 18 }}>
                <Toggle label={tx("enableListening")} checked={transcription.enabled}
                  onChange={(_, checked) => onTranscription((prev) => ({ ...prev, enabled: Boolean(checked) }))} />
                <Toggle label={tx("enableLive")} checked={voice.realtimeEnabled !== false}
                  onChange={(_, checked) => onVoice((prev) => ({ ...prev, realtimeEnabled: checked ? null : false }))} />
                <Toggle label={tx("dictation")} onText={tx("dictationHint")} offText={tx("dictationHint")} checked={dictation}
                  onChange={(_, checked) => { setLiveDictation(Boolean(checked)); setDictation(Boolean(checked)); }} />
                <TextField label={tx("language")} placeholder={tx("automaticLanguage")} value={transcription.language}
                  maxLength={3} onChange={(_, value) => onTranscription((prev) => ({ ...prev, language: value ?? "" }))} />
                <SpinButton label={tx("durationLimit")} value={String(transcription.maxDurationSec)} min={1} max={600} step={1}
                  onChange={(_, value) => { const next = Number(value); if (next >= 1 && next <= 600) onTranscription((prev) => ({ ...prev, maxDurationSec: next })); }} />
                <SpinButton label={tx("uploadLimit")} value={String(transcription.maxUploadMb)} min={1} max={100} step={1}
                  onChange={(_, value) => { const next = Number(value); if (next >= 1 && next <= 100) onTranscription((prev) => ({ ...prev, maxUploadMb: next })); }} />
                <Toggle label={tx("autoSpeak")} checked={voice.autoSpeak}
                  onChange={(_, checked) => onVoice((prev) => ({ ...prev, autoSpeak: Boolean(checked) }))} />
                <SettingsPicker label={tx("audioFormat")} ariaLabel={tx("audioFormat")} value={voice.responseFormat}
                  options={[{ value: "mp3", label: "MP3" }, { value: "wav", label: "WAV" }]}
                  onChange={(responseFormat) => onVoice((prev) => ({ ...prev, responseFormat }))} />
                {canUseNavin ? <DefaultButton text={tx("restoreNavin")} onClick={useNavin} /> : null}
              </Stack>
            </details>
          </fieldset>

          <Stack horizontal wrap tokens={{ childrenGap: 10 }}>
            {dirty ? <>
              <PrimaryButton text={tx(saving ? "saving" : "saveAndReturn")} disabled={saving} onClick={onSaveAndReturn} />
              <DefaultButton text={tx("save")} disabled={saving} onClick={onSave} />
            </> : <PrimaryButton text={tx("backToChat")} onClick={onBackToChat} disabled={saving} />}
          </Stack>
          <Text variant="small" role="status">{tx(dirty ? "saveTogether" : "savedHint")}</Text>
        </Stack>
      </motion.section>
    </MediaSettingsSurface>
  );
}
