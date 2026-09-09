// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Icon } from "@fluentui/react";
import {
  AlertTriangle,
  Check,
  Mic,
  MicOff,
  PhoneOff,
  RotateCw,
  SkipForward,
  SlidersHorizontal,
  Volume2,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { LiveVoiceControl } from "@/hooks/useLiveVoice";
import type { LiveVoiceState } from "@/lib/live-voice";
import { cn } from "@/lib/utils";
import type { VoiceDevice } from "@/lib/voice-devices";

interface LiveVoiceBarProps {
  control: LiveVoiceControl;
  className?: string;
  onOpenSettings?: () => void;
  voiceLabel?: string;
}

const spring = { type: "spring" as const, duration: 0.32, bounce: 0 };

const STATE_TONE: Record<LiveVoiceState, { dot: string; ring: string; text: string }> = {
  off: { dot: "bg-muted-foreground", ring: "bg-muted-foreground/25", text: "text-muted-foreground" },
  starting: { dot: "bg-sky-500", ring: "bg-sky-500/25", text: "text-sky-600 dark:text-sky-300" },
  listening: {
    dot: "bg-emerald-500",
    ring: "bg-emerald-500/25",
    text: "text-emerald-600 dark:text-emerald-300",
  },
  muted: { dot: "bg-muted-foreground", ring: "bg-muted-foreground/20", text: "text-muted-foreground" },
  speaking: { dot: "bg-primary", ring: "bg-primary/25", text: "text-primary" },
  working: { dot: "bg-amber-500", ring: "bg-amber-500/25", text: "text-amber-600 dark:text-amber-300" },
  error: { dot: "bg-amber-500", ring: "bg-amber-500/25", text: "text-amber-700 dark:text-amber-300" },
};

/**
 * The compact strip above the composer while the voice conversation is on:
 * who is talking right now, what the microphone heard and what was sent, the
 * live input level, the device picker, and the three controls a call needs
 * (mute, skip what is being said, hang up).
 */
export function LiveVoiceBar({ control, className, onOpenSettings, voiceLabel }: LiveVoiceBarProps) {
  const visible = control.state !== "off" || control.error !== null;
  return (
    <AnimatePresence initial={false}>
      {visible ? <LiveVoiceBarBody key="live-voice-bar" control={control} className={className}
        onOpenSettings={onOpenSettings} voiceLabel={voiceLabel} /> : null}
    </AnimatePresence>
  );
}

function LiveVoiceBarBody({ control, className, onOpenSettings, voiceLabel }: LiveVoiceBarProps) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const { state } = control;
  const tone = STATE_TONE[state];
  const errorState = state === "error";
  const pulsing = !reduceMotion && (state === "listening" || state === "speaking" || state === "working");

  const statusLabel = errorState
    ? t(`thread.composer.liveVoice.errors.${control.error ?? "failed"}`, {
        defaultValue: t("thread.composer.liveVoice.errors.failed", {
          defaultValue: "The voice conversation stopped.",
        }),
      })
    : control.userSpeaking
      ? t("thread.composer.liveVoice.hearingYou", { defaultValue: "I hear you..." })
      : control.awaitingPermission
        ? t("thread.composer.liveVoice.permissionPrompt", { defaultValue: "Allow the microphone" })
        : t(`thread.composer.liveVoice.status.${state}`, { defaultValue: state });

  const hint = errorState
    ? errorHint(control, t)
    : control.awaitingPermission
      ? t("thread.composer.liveVoice.permissionHint", {
          defaultValue: "Your browser or system is asking whether Navin may use the microphone. Choose Allow.",
        })
      : control.isTranscribing
        ? t("thread.composer.liveVoice.transcribing", { defaultValue: "Understanding what you said..." })
        : control.isRewriting && control.lastHeard
          ? t("thread.composer.liveVoice.rewriting", {
              text: control.lastHeard,
              defaultValue: `Heard: "${control.lastHeard}". Preparing the message...`,
            })
          : control.lastPrompt
            ? t("thread.composer.liveVoice.sent", {
                text: control.lastPrompt,
                defaultValue: `Sent: ${control.lastPrompt}`,
              })
            : control.lastHeard
              ? t("thread.composer.liveVoice.heard", {
                  text: control.lastHeard,
                  defaultValue: `You: ${control.lastHeard}`,
                })
              : state === "listening" && control.activeInputLabel
                ? t("thread.composer.liveVoice.hints.listeningOn", {
                    device: control.activeInputLabel,
                    defaultValue: `Listening on ${control.activeInputLabel}. Say what you need; the agent answers out loud and tells you when it is done.`,
                  })
                : t(`thread.composer.liveVoice.hints.${state}`, {
                    defaultValue: t("thread.composer.liveVoice.hints.listening", {
                      defaultValue: "Say what you need. The agent answers out loud and tells you when it is done.",
                    }),
                  });

  const hintTitle = errorState
    ? control.errorDetail ?? undefined
    : control.lastHeard
      ? `${control.lastHeard}${control.lastPrompt && control.lastPrompt !== control.lastHeard ? `\n-> ${control.lastPrompt}` : ""}`
      : undefined;

  return (
    <motion.div
      role="status"
      aria-live="polite"
      data-live-voice-state={state}
      data-live-voice-hearing={control.hearing ? "true" : "false"}
      initial={reduceMotion ? false : { opacity: 0, y: 8, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={reduceMotion ? undefined : { opacity: 0, y: 6, scale: 0.985 }}
      transition={spring}
      className={cn(
        "mb-2 flex w-full min-w-0 items-center gap-3 rounded-xl border px-3 py-2 text-[12px]",
        errorState
          ? "border-amber-500/40 bg-amber-500/8"
          : "border-border/70 bg-card/95 shadow-[0_6px_18px_rgba(15,23,42,0.06)]",
        className,
      )}
    >
      <span className="relative flex h-7 w-7 shrink-0 items-center justify-center" aria-hidden>
        {pulsing ? (
          <motion.span
            className={cn("absolute inset-0 rounded-full", tone.ring)}
            animate={{ scale: [1, 1.45, 1], opacity: [0.7, 0, 0.7] }}
            transition={{ duration: state === "speaking" ? 1.1 : 1.8, repeat: Infinity, ease: "easeInOut" }}
          />
        ) : (
          <span className={cn("absolute inset-0 rounded-full", tone.ring)} />
        )}
        <motion.span
          className={cn("relative h-2.5 w-2.5 rounded-full", tone.dot)}
          animate={
            !reduceMotion && control.userSpeaking
              ? { scale: [1, 1.5, 1] }
              : { scale: 1 + Math.min(0.6, control.inputLevel * 0.8) }
          }
          transition={
            control.userSpeaking ? { duration: 0.35, repeat: Infinity } : { duration: 0.08, ease: "linear" }
          }
        />
      </span>

      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn("shrink-0 font-medium", errorState ? tone.text : "text-foreground")}>
            {errorState ? (
              <span className="inline-flex items-center gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                {statusLabel}
              </span>
            ) : (
              statusLabel
            )}
          </span>
          {!errorState && state !== "starting" ? (
            <VoiceLevels
              levels={control.levels}
              state={state}
              hearing={control.hearing || control.userSpeaking}
              className={cn("min-w-0 flex-1", tone.text)}
              reduceMotion={Boolean(reduceMotion)}
            />
          ) : null}
        </div>
        <p
          className={cn(
            "min-w-0 truncate text-muted-foreground",
            control.lastHeard && !control.isTranscribing && !control.isRewriting && "italic",
          )}
          title={hintTitle}
          data-live-voice-heard={control.lastHeard ?? undefined}
          data-live-voice-sent={control.lastPrompt ?? undefined}
        >
          {hint}
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-0.5">
        {onOpenSettings ? (
          <Button type="button" size="icon" variant="ghost" className="h-10 w-10"
            aria-label={t("settings.live.configure")}
            title={`${t("settings.live.configure")}${voiceLabel ? `: ${voiceLabel}` : ""}`}
            onClick={onOpenSettings}>
            <Icon iconName="Settings" aria-hidden />
          </Button>
        ) : null}
        {errorState ? (
          <>
            <VoiceDevicePicker control={control} />
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 gap-1.5 px-2 text-[12px]"
              onClick={control.toggle}
            >
              <RotateCw className="h-3.5 w-3.5" aria-hidden />
              {t("thread.composer.liveVoice.retry", { defaultValue: "Retry" })}
            </Button>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="h-7 w-7 rounded-md text-muted-foreground hover:bg-muted/65 hover:text-foreground"
              aria-label={t("thread.composer.liveVoice.dismiss", { defaultValue: "Dismiss" })}
              onClick={control.dismissError}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </>
        ) : (
          <>
            <VoiceDevicePicker control={control} />
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-pressed={control.muted}
              aria-label={
                control.muted
                  ? t("thread.composer.liveVoice.unmute", { defaultValue: "Unmute microphone" })
                  : t("thread.composer.liveVoice.mute", { defaultValue: "Mute microphone" })
              }
              title={
                control.muted
                  ? t("thread.composer.liveVoice.unmute", { defaultValue: "Unmute microphone" })
                  : t("thread.composer.liveVoice.mute", { defaultValue: "Mute microphone" })
              }
              onClick={() => control.setMuted(!control.muted)}
              className={cn(
                "h-7 w-7 rounded-md text-muted-foreground hover:bg-muted/65 hover:text-foreground",
                control.muted && "bg-muted/70 text-foreground",
              )}
            >
              {control.muted ? <MicOff className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
            </Button>
            <AnimatePresence initial={false}>
              {state === "speaking" ? (
                <motion.div
                  key="skip"
                  initial={reduceMotion ? false : { opacity: 0, width: 0 }}
                  animate={{ opacity: 1, width: "auto" }}
                  exit={reduceMotion ? undefined : { opacity: 0, width: 0 }}
                  transition={spring}
                  className="overflow-hidden"
                >
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    aria-label={t("thread.composer.liveVoice.skip", { defaultValue: "Skip what is being said" })}
                    title={t("thread.composer.liveVoice.skip", { defaultValue: "Skip what is being said" })}
                    onClick={control.skipSpeech}
                    className="h-7 w-7 rounded-md text-muted-foreground hover:bg-muted/65 hover:text-foreground"
                  >
                    <SkipForward className="h-3.5 w-3.5" />
                  </Button>
                </motion.div>
              ) : null}
            </AnimatePresence>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label={t("thread.composer.liveVoice.end", { defaultValue: "End voice conversation" })}
              title={t("thread.composer.liveVoice.end", { defaultValue: "End voice conversation" })}
              onClick={control.stop}
              className="h-7 w-7 rounded-md text-muted-foreground hover:bg-red-500/10 hover:text-red-600 dark:hover:text-red-400"
            >
              <PhoneOff className="h-3.5 w-3.5" />
            </Button>
          </>
        )}
      </div>
    </motion.div>
  );
}

function errorHint(control: LiveVoiceControl, t: ReturnType<typeof useTranslation>["t"]): string {
  const key = control.error ?? "failed";
  const base = t(`thread.composer.liveVoice.errorHints.${key}`, {
    defaultValue: t("thread.composer.liveVoice.errorHints.failed", {
      defaultValue: "Check the microphone and try again.",
    }),
  });
  return control.errorDetail && key === "failed" ? `${base} (${control.errorDetail})` : base;
}

/**
 * Microphone / speaker picker. Lists what the OS exposes, remembers the
 * choice, and shows the live input level so the user can see the chosen
 * microphone actually hears them before they talk to the agent.
 */
function VoiceDevicePicker({ control }: { control: LiveVoiceControl }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const { devices, refreshDevices } = control;

  useEffect(() => {
    if (open) void refreshDevices();
  }, [refreshDevices, open]);

  const label = t("thread.composer.liveVoice.devices.title", { defaultValue: "Microphone and speakers" });
  const inputs = devices.inputs;
  const outputs = devices.outputSelectable ? devices.outputs : [];
  const selectedInput = control.inputDeviceId;
  const selectedOutput = control.outputDeviceId;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label={label}
          title={label}
          data-live-voice-devices
          className={cn(
            "h-7 w-7 rounded-md text-muted-foreground hover:bg-muted/65 hover:text-foreground",
            open && "bg-muted/70 text-foreground",
          )}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" side="top" className="w-[21rem] p-3 text-[12px]">
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="font-medium text-foreground">{label}</span>
          <span className="text-[11px] text-muted-foreground">
            {control.state === "listening" || control.state === "muted" || control.state === "working" || control.state === "speaking"
              ? t("thread.composer.liveVoice.devices.levelLabel", { defaultValue: "Input level" })
              : null}
          </span>
        </div>
        <InputLevelMeter level={control.inputLevel} hearing={control.hearing} muted={control.muted} />
        {control.noiseFloorDb !== null ? (
          <p className="mt-1 text-[11px] text-muted-foreground">
            {t("thread.composer.liveVoice.devices.noiseFloor", {
              db: control.noiseFloorDb,
              defaultValue: `Room noise: ${control.noiseFloorDb} dB. Speech shows in green.`,
            })}
          </p>
        ) : null}

        <DeviceGroup
          icon={<Mic className="h-3.5 w-3.5" aria-hidden />}
          title={t("thread.composer.liveVoice.devices.inputs", { defaultValue: "Microphone" })}
          devices={inputs}
          selectedId={selectedInput}
          defaultLabel={t("thread.composer.liveVoice.devices.systemDefault", { defaultValue: "System default" })}
          emptyLabel={t("thread.composer.liveVoice.devices.noInputs", {
            defaultValue: "No microphone detected. Plug one in or check the system sound settings.",
          })}
          onSelect={(id) => void control.selectInputDevice(id)}
        />
        {outputs.length > 0 ? (
          <DeviceGroup
            icon={<Volume2 className="h-3.5 w-3.5" aria-hidden />}
            title={t("thread.composer.liveVoice.devices.outputs", { defaultValue: "Speakers" })}
            devices={outputs}
            selectedId={selectedOutput}
            defaultLabel={t("thread.composer.liveVoice.devices.systemDefault", { defaultValue: "System default" })}
            emptyLabel=""
            onSelect={(id) => control.selectOutputDevice(id)}
          />
        ) : (
          <p className="mt-3 text-[11px] text-muted-foreground">
            {t("thread.composer.liveVoice.devices.outputSystem", {
              defaultValue: "The assistant voice plays on the system output. Change it in the system sound settings.",
            })}
          </p>
        )}
        {control.permission === "denied" ? (
          <p className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-300">
            {t("thread.composer.liveVoice.errorHints.permission", {
              defaultValue:
                "Microphone access is blocked. Allow it in the site or app permissions, then retry.",
            })}
          </p>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function DeviceGroup({
  icon,
  title,
  devices,
  selectedId,
  defaultLabel,
  emptyLabel,
  onSelect,
}: {
  icon: ReactNode;
  title: string;
  devices: VoiceDevice[];
  selectedId: string | null;
  defaultLabel: string;
  emptyLabel: string;
  onSelect: (deviceId: string | null) => void;
}) {
  const items: Array<{ id: string | null; label: string }> = [
    { id: null, label: defaultLabel },
    ...devices
      .filter((device) => device.deviceId !== "default")
      .map((device) => ({ id: device.deviceId, label: device.label })),
  ];
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {icon}
        {title}
      </div>
      {devices.length === 0 && emptyLabel ? (
        <p className="text-[11px] text-muted-foreground">{emptyLabel}</p>
      ) : (
        <ul className="flex max-h-40 flex-col gap-0.5 overflow-y-auto" role="listbox" aria-label={title}>
          {items.map((item) => {
            const active = (item.id ?? null) === (selectedId ?? null);
            return (
              <li key={item.id ?? "__default"}>
                <button
                  type="button"
                  role="option"
                  aria-selected={active}
                  onClick={() => onSelect(item.id)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] transition-colors hover:bg-muted/65",
                    active ? "bg-primary/10 text-foreground" : "text-foreground/85",
                  )}
                >
                  <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center text-primary">
                    {active ? <Check className="h-3.5 w-3.5" aria-hidden /> : null}
                  </span>
                  <span className="min-w-0 flex-1 truncate" title={item.label}>
                    {item.label}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function InputLevelMeter({ level, hearing, muted }: { level: number; hearing: boolean; muted: boolean }) {
  const segments = 24;
  const lit = muted ? 0 : Math.round(level * segments);
  return (
    <div
      className="flex h-2.5 items-center gap-[2px]"
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(level * 100)}
    >
      {Array.from({ length: segments }, (_, index) => (
        <span
          key={index}
          className={cn(
            "h-full flex-1 rounded-[2px] transition-colors duration-75",
            index < lit
              ? hearing
                ? "bg-emerald-500"
                : "bg-primary/70"
              : "bg-muted-foreground/15",
          )}
        />
      ))}
    </div>
  );
}

const SPEAKING_BARS = [0.55, 0.9, 0.7, 1, 0.6, 0.85, 0.5];

function VoiceLevels({
  levels,
  state,
  hearing,
  className,
  reduceMotion,
}: {
  levels: number[];
  state: LiveVoiceState;
  hearing: boolean;
  className?: string;
  reduceMotion: boolean;
}) {
  if (state === "speaking") {
    return (
      <span className={cn("flex h-4 items-center gap-[3px]", className)} aria-hidden>
        {SPEAKING_BARS.map((peak, index) => (
          <motion.span
            key={index}
            className="w-[2.5px] rounded-full bg-current"
            style={{ height: 16 }}
            animate={reduceMotion ? { scaleY: peak } : { scaleY: [0.25, peak, 0.25] }}
            transition={{
              duration: 0.9,
              repeat: Infinity,
              ease: "easeInOut",
              delay: index * 0.08,
            }}
          />
        ))}
      </span>
    );
  }
  if (state === "working") {
    return (
      <span className={cn("flex h-4 items-center gap-[3px]", className)} aria-hidden>
        {[0, 1, 2].map((index) => (
          <motion.span
            key={index}
            className="h-1.5 w-1.5 rounded-full bg-current"
            animate={reduceMotion ? { opacity: 0.7 } : { opacity: [0.25, 1, 0.25] }}
            transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut", delay: index * 0.2 }}
          />
        ))}
      </span>
    );
  }
  const muted = state === "muted";
  return (
    <span
      className={cn(
        "flex h-4 min-w-0 flex-1 items-center justify-between overflow-hidden",
        hearing ? "text-emerald-500" : className,
      )}
      aria-hidden
    >
      {levels.map((level, index) => (
        <span
          key={index}
          className={cn(
            "w-[2px] rounded-full bg-current transition-[height] duration-75 ease-linear motion-reduce:transition-none",
            muted ? "opacity-30" : "opacity-85",
          )}
          style={{ height: muted ? 2 : Math.max(2, Math.round(level * 16)) }}
        />
      ))}
    </span>
  );
}
