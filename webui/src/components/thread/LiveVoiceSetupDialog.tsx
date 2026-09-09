// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { DefaultButton, Dialog, DialogFooter, DialogType, PrimaryButton, Spinner, Stack, Text } from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { MediaSettingsSurface } from "@/components/settings/MediaModelPicker";
import { liveVoiceSetup } from "@/lib/live-voice-setup";
import type { SettingsPayload } from "@/lib/types";

export function LiveVoiceSetupDialog({ open, settings, busy = false, failed = false, onDismiss, onConfigure, onRetry }: {
  open: boolean;
  settings: SettingsPayload | null;
  busy?: boolean;
  failed?: boolean;
  onDismiss: () => void;
  onConfigure: () => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string) => t(`settings.live.${key}`);
  const reduced = useReducedMotion();
  const setup = liveVoiceSetup(settings);
  const title = failed ? "checkFailedTitle" : setup.reason === "plan_required" ? "planTitle"
    : setup.reason === "voice_disabled" || setup.reason === "stt_disabled" ? "enableTitle" : "setupTitle";
  const hint = failed ? "checkFailedHint" : setup.reason === "plan_required" ? "planHint"
    : setup.reason === "voice_disabled" ? "disabledHint" : setup.reason === "stt_disabled" ? "listeningDisabledHint" : "setupHint";
  return (
    <MediaSettingsSurface>
      <Dialog hidden={!open} onDismiss={onDismiss}
        dialogContentProps={{ type: DialogType.normal, title: tx(title), closeButtonAriaLabel: tx("close") }}
        modalProps={{ isBlocking: false }} minWidth={320} maxWidth={500}>
        <motion.div initial={reduced ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}>
          <Stack tokens={{ childrenGap: 14 }}>
            <Text>{tx(hint)}</Text>
            {!failed && setup.reason !== "plan_required" ? <>
              <Text><strong>{tx("listening")}</strong>: {tx(setup.stt.configured && setup.stt.enabled ? "configured" : "needed")}</Text>
              <Text><strong>{tx("speaking")}</strong>: {tx(setup.tts.configured ? "configured" : "needed")}</Text>
            </> : null}
            {busy ? <Spinner label={tx("checking")} /> : null}
          </Stack>
        </motion.div>
        <DialogFooter>
          <PrimaryButton text={tx(failed ? "retry" : "configure")} disabled={busy} onClick={failed ? onRetry : onConfigure} />
          {!failed ? <DefaultButton text={tx("alreadyConfigured")} disabled={busy} onClick={onRetry} /> : null}
          <DefaultButton text={tx("close")} onClick={onDismiss} />
        </DialogFooter>
      </Dialog>
    </MediaSettingsSurface>
  );
}
