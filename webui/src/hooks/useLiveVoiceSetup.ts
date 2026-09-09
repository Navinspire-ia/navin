// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchSettings } from "@/lib/api";
import { liveVoiceSetup } from "@/lib/live-voice-setup";
import type { SettingsPayload } from "@/lib/types";

/** Check the saved setup at entry, including after returning from Settings. */
export function useLiveVoiceSetup(
  token: string,
  scope: string,
  onSettings: (settings: SettingsPayload) => void,
) {
  const [checking, setChecking] = useState(false);
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const generation = useRef(0);
  const checkingRef = useRef(false);
  const onSettingsRef = useRef(onSettings);
  onSettingsRef.current = onSettings;

  const dismiss = useCallback(() => {
    generation.current += 1;
    checkingRef.current = false;
    setChecking(false);
    setOpen(false);
  }, []);

  useEffect(() => {
    dismiss();
    return () => { generation.current += 1; checkingRef.current = false; };
  }, [dismiss, scope, token]);

  const check = useCallback(async () => {
    if (checkingRef.current) return false;
    checkingRef.current = true;
    const request = ++generation.current;
    setChecking(true);
    setFailed(false);
    try {
      const next = await fetchSettings(token);
      if (request !== generation.current) return false;
      setSettings(next);
      onSettingsRef.current(next);
      const ready = liveVoiceSetup(next).ready;
      setOpen(!ready);
      return ready;
    } catch {
      if (request === generation.current) {
        setFailed(true);
        setOpen(true);
      }
      return false;
    } finally {
      if (request === generation.current) {
        checkingRef.current = false;
        setChecking(false);
      }
    }
  }, [token]);

  return { checking, open, failed, settings, check, dismiss };
}
