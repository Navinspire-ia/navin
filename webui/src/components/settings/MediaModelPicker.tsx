import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ComboBox, Customizer, DefaultButton, MessageBar, MessageBarType, Spinner, Stack, Text,
  createTheme,
} from "@fluentui/react";
import { useTranslation } from "react-i18next";

import { useThemeValue } from "@/hooks/useTheme";
import { fetchProviderModels, previewVoice } from "@/lib/api";
import { filterMediaModels } from "@/lib/media-models";
import type { MediaModelKind, ProviderModelInfo } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import "@/lib/fluent-icons";

export function MediaSettingsSurface({ children }: { children: ReactNode }) {
  const dark = useThemeValue() === "dark";
  const theme = useMemo(() => createTheme({
    isInverted: dark,
    palette: dark ? {
      themePrimary: "#62abf5", white: "#17191d", black: "#f5f5f5",
      neutralLighterAlt: "#1b1e23", neutralLighter: "#22262c", neutralLight: "#303640",
      neutralPrimary: "#f5f5f5", neutralSecondary: "#c2c8d0", neutralTertiary: "#a1aab5",
    } : { themePrimary: "#0078d4" },
    defaultFontStyle: { fontFamily: "inherit" },
  }), [dark]);
  return <Customizer settings={{ theme }}>{children}</Customizer>;
}

// Provider catalogs run to dozens of rows: the list scrolls inside the callout
// instead of running past the top or bottom of the window.
const MODEL_LIST_MAX_HEIGHT = 320;
const modelListCallout = { calloutMaxHeight: MODEL_LIST_MAX_HEIGHT };

export function MediaModelPicker({ kind, provider, configured, model, onModelChange, voice, onVoiceChange }: {
  kind: MediaModelKind;
  provider: string;
  configured: boolean;
  model: string;
  onModelChange: (model: string) => void;
  voice?: string;
  onVoiceChange?: (voice: string) => void;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const tx = (key: string) => t(`settings.live.${key}`);
  const [catalog, setCatalog] = useState<ProviderModelInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [revision, setRevision] = useState(0);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<{ url: string; voice: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    let current = true;
    setCatalog([]);
    setUnavailable(false);
    if (!configured || !provider) { setLoading(false); return; }
    setLoading(true);
    void fetchProviderModels(token, provider, "", { modality: kind }).then((result) => {
      if (!current) return;
      const models = filterMediaModels(result.models, kind);
      setCatalog(models);
      setUnavailable(result.status !== "available" || models.length === 0);
    }).catch(() => { if (current) setUnavailable(true); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [configured, kind, provider, revision, token]);

  useEffect(() => {
    if (!model && catalog[0]) onModelChange(catalog[0].id);
  }, [catalog, model, onModelChange]);

  useEffect(() => {
    generation.current += 1;
    setPreview(null);
    setPreviewing(false);
    setError(null);
    return () => { generation.current += 1; };
  }, [model, provider, voice]);

  useEffect(() => {
    if (preview) void audioRef.current?.play().catch(() => undefined);
  }, [preview]);

  const selected = catalog.find((row) => row.id === model);
  const voices = selected?.voices ?? [];
  const options = [
    ...(model && !selected ? [{ key: model, text: model }] : []),
    ...catalog.map((row) => ({ key: row.id, text: row.label || row.id })),
  ];
  const manual = unavailable && provider !== "navin";
  const modelUnavailable = configured && !loading && model && !selected && !unavailable;

  const audition = async () => {
    const request = ++generation.current;
    setPreview(null);
    setError(null);
    setPreviewing(true);
    try {
      const result = await previewVoice(token, {
        provider, model, voice: voice || "auto", text: tx("previewText"),
      });
      if (request !== generation.current) return;
      setPreview({ url: `data:${result.mime};base64,${result.audio_base64}`, voice: result.voice });
    } catch {
      if (request === generation.current) setError(tx("previewFailed"));
    } finally {
      if (request === generation.current) setPreviewing(false);
    }
  };

  return (
    <MediaSettingsSurface>
      <Stack tokens={{ childrenGap: 10 }} styles={{ root: { width: 360, maxWidth: "100%" } }}>
        <ComboBox
          ariaLabel={tx(`${kind}Model`)}
          selectedKey={model || null}
          text={manual ? model : undefined}
          options={options}
          allowFreeform={manual}
          autoComplete="on"
          useComboBoxAsMenuWidth
          calloutProps={modelListCallout}
          disabled={!provider || !configured || loading}
          placeholder={tx(loading ? "loadingModels" : "chooseModel")}
          onChange={(_, option, __, value) => {
            const next = String(option?.key ?? value ?? "").trim();
            if (next) { onModelChange(next); onVoiceChange?.("auto"); }
          }}
        />
        {loading ? <Spinner label={tx("loadingModels")} /> : null}
        <Text variant="small">{tx("modelsFiltered")}</Text>
        {unavailable ? <Text variant="small">{tx(manual ? "manualModel" : "modelsUnavailable")}</Text> : null}
        {modelUnavailable ? <MessageBar messageBarType={MessageBarType.warning}>{tx("modelUnavailable")}: {model}</MessageBar> : null}
        <DefaultButton text={tx("refreshModels")} iconProps={{ iconName: "Refresh" }}
          disabled={!configured || loading} onClick={() => setRevision((value) => value + 1)} />
        {kind === "tts" ? (
          <>
            <ComboBox label={tx("voice")} selectedKey={voices.includes(voice ?? "") ? voice : "auto"}
              text={!voices.length && voice && voice !== "auto" ? voice : undefined}
              options={[{ key: "auto", text: `${tx("automaticVoice")}${selected?.default_voice ? ` (${selected.default_voice})` : ""}` },
                ...voices.map((name) => ({ key: name, text: name }))]}
              allowFreeform={!voices.length} autoComplete="on" disabled={!model}
              useComboBoxAsMenuWidth calloutProps={modelListCallout}
              onChange={(_, option, __, value) => onVoiceChange?.(String(option?.key ?? value ?? "auto"))} />
            <Text variant="small">{tx("voiceIndependent")}</Text>
            <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
              <DefaultButton text={tx(previewing ? "previewLoading" : "preview")}
                iconProps={{ iconName: "Volume2" }} disabled={!configured || !model || Boolean(modelUnavailable) || previewing}
                onClick={() => { void audition(); }} />
            </Stack>
            {preview ? <>
              <Text variant="small">{tx("previewVoice")}: {preview.voice}</Text>
              <audio ref={audioRef} controls src={preview.url} aria-label={tx("preview")}
                style={{ width: "100%" }} onError={() => setError(tx("previewFailed"))} />
            </> : null}
          </>
        ) : null}
        {error ? <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar> : null}
      </Stack>
    </MediaSettingsSurface>
  );
}
