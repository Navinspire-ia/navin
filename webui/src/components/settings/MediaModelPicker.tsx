// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Customizer, DefaultButton, MessageBar, MessageBarType, Spinner, Stack, Text,
  createTheme,
} from "@fluentui/react";
import { useTranslation } from "react-i18next";

import { useThemeValue } from "@/hooks/useTheme";
import { fetchProviderModels } from "@/lib/api";
import { filterMediaModels } from "@/lib/media-models";
import type { MediaModelKind, ProviderModelInfo } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { SettingsPicker } from "./SettingsPicker";
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

export function MediaModelPicker({ kind, provider, configured, model, onModelChange, voice, onVoiceChange, voiceOnly = false }: {
  kind: MediaModelKind;
  provider: string;
  configured: boolean;
  model: string;
  onModelChange: (model: string) => void;
  voice?: string;
  onVoiceChange?: (voice: string) => void;
  /** The model is chosen elsewhere (MediaModelChoice): only voices and preview. */
  voiceOnly?: boolean;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const tx = (key: string) => t(`settings.live.${key}`);
  const [catalog, setCatalog] = useState<ProviderModelInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => { setError(null); }, [model, provider, voice]);

  useEffect(() => {
    if (!model && catalog[0]) onModelChange(catalog[0].id);
  }, [catalog, model, onModelChange]);

  const selected = catalog.find((row) => row.id === model);
  const voices = selected?.voices ?? [];
  const options = [
    ...(model && !selected ? [{ value: model, label: model }] : []),
    ...catalog.map((row) => ({ value: row.id, label: row.label || row.id })),
  ];
  const manual = unavailable && provider !== "navin";
  const modelUnavailable = configured && !loading && model && !selected && !unavailable;

  return (
    <MediaSettingsSurface>
      <Stack tokens={{ childrenGap: 10 }} styles={{ root: { width: 360, maxWidth: "100%" } }}>
        {voiceOnly ? null : (<>
        <SettingsPicker
          ariaLabel={tx(`${kind}Model`)}
          value={model}
          options={options}
          allowCustomValue={manual}
          searchable
          disabled={!provider || !configured || loading}
          placeholder={tx(loading ? "loadingModels" : "chooseModel")}
          onChange={(value) => {
            const next = value.trim();
            if (next) { onModelChange(next); onVoiceChange?.("auto"); }
          }}
        />
        {loading ? <Spinner label={tx("loadingModels")} /> : null}
        <Text variant="small">{tx("modelsFiltered")}</Text>
        {unavailable ? <Text variant="small">{tx(manual ? "manualModel" : "modelsUnavailable")}</Text> : null}
        {modelUnavailable ? <MessageBar messageBarType={MessageBarType.warning}>{tx("modelUnavailable")}: {model}</MessageBar> : null}
        <DefaultButton text={tx("refreshModels")} iconProps={{ iconName: "Refresh" }}
          disabled={!configured || loading} onClick={() => setRevision((value) => value + 1)} />
        </>)}
        {kind === "tts" ? (
          <>
            <SettingsPicker label={tx("voice")} ariaLabel={tx("voice")} value={voice || "auto"}
              options={[{ value: "auto", label: `${tx("automaticVoice")}${selected?.default_voice ? ` (${selected.default_voice})` : ""}` },
                ...voices.map((name) => ({ value: name, label: name }))]}
              allowCustomValue={!voices.length} searchable disabled={!model}
              onChange={(value) => onVoiceChange?.(value)} />
          </>
        ) : null}
        {error ? <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar> : null}
      </Stack>
    </MediaSettingsSurface>
  );
}
