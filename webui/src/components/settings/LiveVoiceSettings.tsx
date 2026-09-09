import { type Dispatch, type SetStateAction } from "react";
import {
  DefaultButton, Dropdown, Icon, MessageBar, MessageBarType, PrimaryButton,
  SpinButton, Stack, Text, TextField, Toggle,
} from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { MediaModelPicker, MediaSettingsSurface } from "./MediaModelPicker";
import { liveVoiceSetup } from "@/lib/live-voice-setup";
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
  const reduced = useReducedMotion();
  const setup = liveVoiceSetup(settings);
  const sttProviders = settings.transcription?.providers ?? [];
  const ttsProviders = settings.voice?.providers ?? [];
  const sttProvider = sttProviders.find((item) => item.name === transcription.provider);
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
                <Dropdown label={tx("listeningProvider")} selectedKey={transcription.provider || null}
                  placeholder={tx("chooseProvider")} options={sttProviders.map((p) => ({ key: p.name, text: p.label }))}
                  onChange={(_, option) => { if (option) onTranscription((prev) => ({ ...prev, provider: String(option.key), model: "" })); }} />
                {sttProvider && !sttProvider.configured ? (
                  <MessageBar messageBarType={MessageBarType.info}>
                    {tx("providerNeeded")}
                    <DefaultButton text={tx("connectProvider")} onClick={onOpenProviders} />
                  </MessageBar>
                ) : null}
                <MediaModelPicker key={`stt:${transcription.provider}`} kind="stt" provider={transcription.provider}
                  configured={Boolean(sttProvider?.configured)} model={transcription.model}
                  onModelChange={(model) => onTranscription((prev) => ({ ...prev, model }))} />
              </section>

              <section className="live-voice-settings__service" aria-labelledby="live-speaking-title">
                <Text variant="large" as="h3" id="live-speaking-title">{tx("speaking")}</Text>
                <Text>{tx("speakingHint")}</Text>
                <Dropdown label={tx("speakingProvider")} selectedKey={voice.ttsProvider || null}
                  placeholder={tx("chooseProvider")} options={ttsProviders.map((p) => ({ key: p.name, text: p.label }))}
                  onChange={(_, option) => { if (option) onVoice((prev) => ({ ...prev, ttsProvider: String(option.key), ttsModel: "", voice: "auto" })); }} />
                {ttsProvider && !ttsProvider.configured ? (
                  <MessageBar messageBarType={MessageBarType.info}>
                    {tx("providerNeeded")}
                    <DefaultButton text={tx("connectProvider")} onClick={onOpenProviders} />
                  </MessageBar>
                ) : null}
                <MediaModelPicker key={`tts:${voice.ttsProvider}`} kind="tts" provider={voice.ttsProvider}
                  configured={Boolean(ttsProvider?.configured)} model={voice.ttsModel} voice={voice.voice}
                  onModelChange={(ttsModel) => onVoice((prev) => ({ ...prev, ttsModel, voice: "auto" }))}
                  onVoiceChange={(next) => onVoice((prev) => ({ ...prev, voice: next }))} />
              </section>
            </div>

            <details className="live-voice-settings__advanced">
              <summary>{tx("moreOptions")}</summary>
              <Stack tokens={{ childrenGap: 18 }}>
                <Toggle label={tx("enableListening")} checked={transcription.enabled}
                  onChange={(_, checked) => onTranscription((prev) => ({ ...prev, enabled: Boolean(checked) }))} />
                <Toggle label={tx("enableLive")} checked={voice.realtimeEnabled !== false}
                  onChange={(_, checked) => onVoice((prev) => ({ ...prev, realtimeEnabled: checked ? null : false }))} />
                <TextField label={tx("language")} placeholder={tx("automaticLanguage")} value={transcription.language}
                  maxLength={3} onChange={(_, value) => onTranscription((prev) => ({ ...prev, language: value ?? "" }))} />
                <SpinButton label={tx("durationLimit")} value={String(transcription.maxDurationSec)} min={1} max={600} step={1}
                  onChange={(_, value) => { const next = Number(value); if (next >= 1 && next <= 600) onTranscription((prev) => ({ ...prev, maxDurationSec: next })); }} />
                <SpinButton label={tx("uploadLimit")} value={String(transcription.maxUploadMb)} min={1} max={100} step={1}
                  onChange={(_, value) => { const next = Number(value); if (next >= 1 && next <= 100) onTranscription((prev) => ({ ...prev, maxUploadMb: next })); }} />
                <Toggle label={tx("autoSpeak")} checked={voice.autoSpeak}
                  onChange={(_, checked) => onVoice((prev) => ({ ...prev, autoSpeak: Boolean(checked) }))} />
                <Dropdown label={tx("audioFormat")} selectedKey={voice.responseFormat}
                  options={[{ key: "mp3", text: "MP3" }, { key: "wav", text: "WAV" }]}
                  onChange={(_, option) => { if (option) onVoice((prev) => ({ ...prev, responseFormat: String(option.key) })); }} />
                {canUseNavin ? <DefaultButton text={tx("restoreNavin")} onClick={useNavin} /> : null}
              </Stack>
            </details>
          </fieldset>

          <Text variant="small">{tx("voiceIndependent")}</Text>
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
