// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { TalkingHead } from "@met4citizen/talkinghead";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ChevronDown, Mic, MicOff, PhoneOff, Settings, SkipForward, UserRound, Users } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";

import type { LiveVoiceControl } from "@/hooks/useLiveVoice";
import { LIVE_AVATAR_URLS, avatarBodyForVoice, type AvatarBody } from "@/lib/live-avatar";
import {
  publishLiveAvatar,
  useLiveAvatarDock,
  useLiveAvatarDocked,
  useLiveAvatarState,
  type LiveAvatarActions,
  type LiveAvatarState,
} from "@/lib/live-avatar-store";

import { livePresenceCaption } from "./LivePresence";

type AvatarPlacement = "floating" | "rail" | "rail-icons";

/**
 * The chat's side of the Live avatar: it publishes the call for the dock at
 * the bottom of the Code rail (next to the chat, with the Live controls under
 * the avatar) and, when no dock is on screen, shows the avatar itself at the
 * bottom right of the window, outside the chat column so it never covers the
 * journal.
 */
export function LiveAvatar({
  control,
  voice,
  onOpenSettings,
  onSwitchAvatar,
}: {
  control: LiveVoiceControl;
  voice: string | null | undefined;
  onOpenSettings?: () => void;
  /** Switch man / woman: a voice of the other body, the avatar follows. */
  onSwitchAvatar?: () => void;
}) {
  const active = control.state !== "off" && control.state !== "error";
  const docked = useLiveAvatarDocked();
  // Stable actions over the latest control: the dock never re-renders for them.
  const latest = useRef({ control, onOpenSettings, onSwitchAvatar });
  latest.current = { control, onOpenSettings, onSwitchAvatar };
  const actions = useMemo<LiveAvatarActions>(() => ({
    setMuted: (muted) => latest.current.control.setMuted(muted),
    skipSpeech: () => latest.current.control.skipSpeech(),
    stop: () => latest.current.control.stop(),
    openSettings: () => latest.current.onOpenSettings?.(),
    switchAvatar: () => latest.current.onSwitchAvatar?.(),
  }), []);
  useEffect(() => {
    publishLiveAvatar({
      active,
      voice: voice ?? null,
      speechLevel: control.speechLevel,
      state: control.state,
      muted: control.muted,
      actions,
    });
  }, [active, voice, control.speechLevel, control.state, control.muted, actions]);
  useEffect(() => () => publishLiveAvatar({
    active: false, voice: null, speechLevel: () => 0, state: "off", muted: false, actions: null,
  }), []);
  if (docked) return null;
  return (
    <div className="pointer-events-none fixed bottom-4 end-4 z-30 flex flex-col items-end">
      <LiveAvatarCard active={active} voice={voice ?? null} speechLevel={control.speechLevel} placement="floating"
        onSwitch={onSwitchAvatar} />
    </div>
  );
}

/**
 * Docked at the bottom of the Code rail, level with the chat: the avatar,
 * and the Live controls under it (status, microphone, skip, settings, hang up).
 */
export function LiveAvatarDock({ iconsOnly }: { iconsOnly: boolean }) {
  useLiveAvatarDock();
  const live = useLiveAvatarState();
  return (
    <div className={iconsOnly ? "flex w-full shrink-0 flex-col items-center pb-3 pt-2" : "w-full shrink-0 px-2 pb-3 pt-2"}>
      <LiveAvatarCard
        active={live.active}
        voice={live.voice}
        speechLevel={live.speechLevel}
        placement={iconsOnly ? "rail-icons" : "rail"}
      />
      {live.active && live.actions ? (
        <LiveDockControls live={live} actions={live.actions} iconsOnly={iconsOnly} />
      ) : null}
    </div>
  );
}

function LiveDockControls({
  live,
  actions,
  iconsOnly,
}: {
  live: LiveAvatarState;
  actions: LiveAvatarActions;
  iconsOnly: boolean;
}) {
  const { t } = useTranslation();
  const mode = live.state === "speaking" ? "speaking" : live.state === "working" ? "thinking" : live.state === "starting" ? "connecting" : live.muted ? "muted" : "listening";
  const button = "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/65 hover:text-foreground";
  const muteLabel = live.muted
    ? t("thread.composer.liveVoice.unmute", { defaultValue: "Unmute microphone" })
    : t("thread.composer.liveVoice.mute", { defaultValue: "Mute microphone" });
  return (
    <div
      className={iconsOnly ? "mt-2 flex flex-col items-center gap-1" : "mt-2 flex items-center gap-1"}
      data-testid="live-dock-controls"
    >
      {iconsOnly ? null : (
        <span className="me-auto min-w-0 truncate text-xs text-muted-foreground" role="status" aria-live="polite">
          {livePresenceCaption(mode, t)}
        </span>
      )}
      <button
        type="button"
        className={`${button} ${live.muted ? "bg-muted/70 text-foreground" : ""}`}
        aria-pressed={live.muted}
        aria-label={muteLabel}
        title={muteLabel}
        onClick={() => actions.setMuted(!live.muted)}
      >
        {live.muted ? <MicOff className="h-4 w-4" aria-hidden /> : <Mic className="h-4 w-4" aria-hidden />}
      </button>
      {live.state === "speaking" ? (
        <button
          type="button"
          className={button}
          aria-label={t("thread.composer.liveVoice.skip", { defaultValue: "Skip what is being said" })}
          title={t("thread.composer.liveVoice.skip", { defaultValue: "Skip what is being said" })}
          onClick={actions.skipSpeech}
        >
          <SkipForward className="h-4 w-4" aria-hidden />
        </button>
      ) : null}
      {actions.switchAvatar ? (
        <button
          type="button"
          className={button}
          aria-label={t("thread.composer.liveVoice.avatar.switch", { defaultValue: "Switch avatar and voice" })}
          title={t("thread.composer.liveVoice.avatar.switch", { defaultValue: "Switch avatar and voice" })}
          onClick={actions.switchAvatar}
          data-testid="live-avatar-switch"
        >
          <Users className="h-4 w-4" aria-hidden />
        </button>
      ) : null}
      {actions.openSettings && !iconsOnly ? (
        <button
          type="button"
          className={button}
          aria-label={t("settings.live.configure")}
          title={t("settings.live.configure")}
          onClick={actions.openSettings}
        >
          <Settings className="h-4 w-4" aria-hidden />
        </button>
      ) : null}
      <button
        type="button"
        className={`${button} hover:bg-red-500/10 hover:text-red-600 dark:hover:text-red-400`}
        aria-label={t("thread.composer.liveVoice.end", { defaultValue: "End voice conversation" })}
        title={t("thread.composer.liveVoice.end", { defaultValue: "End voice conversation" })}
        onClick={actions.stop}
      >
        <PhoneOff className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}

/**
 * The agent's face during a Live call: a TalkingHead avatar that pops out and
 * speaks with the voice. Its body follows the voice (male / female); its mouth
 * follows the loudness of the audio being played, so any engine, voice and
 * language moves it.
 */
function LiveAvatarCard({
  active,
  voice,
  speechLevel,
  placement,
  onSwitch,
}: {
  active: boolean;
  voice: string | null;
  speechLevel: () => number;
  placement: AvatarPlacement;
  onSwitch?: () => void;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const [collapsed, setCollapsed] = useState(false);
  const body = avatarBodyForVoice(voice);
  const levelRef = useRef(speechLevel);
  levelRef.current = speechLevel;
  const icons = placement === "rail-icons";
  const frame = {
    floating: "h-[260px] w-48",
    // Short windows keep the modules and Collapse above in view.
    rail: "h-[min(260px,32vh)] w-full",
    "rail-icons": "h-10 w-10",
  }[placement];

  return (
    <AnimatePresence initial={false}>
      {active ? (
        <motion.div
          key="live-avatar"
          className="pointer-events-auto w-full"
          initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.92 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.94 }}
          transition={{ type: "spring", stiffness: 320, damping: 28 }}
          data-testid="live-avatar"
          data-placement={placement}
        >
          {collapsed && !icons ? (
            <button
              type="button"
              className="inline-flex h-8 w-full items-center gap-2 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              onClick={() => setCollapsed(false)}
              aria-label={t("thread.composer.liveVoice.avatar.expand", { defaultValue: "Show the avatar" })}
            >
              <UserRound className="h-4 w-4 shrink-0" aria-hidden />
              <span className="truncate">{t("thread.composer.liveVoice.avatar.label", { defaultValue: "Live avatar" })}</span>
            </button>
          ) : (
            // No card: the avatar stands on the interface (the 3D canvas is transparent).
            <div className={`group relative ${frame}`}>
              <AvatarStage body={body} levelRef={levelRef} view={icons ? "head" : "mid"} />
              {onSwitch && !icons ? (
                <button
                  type="button"
                  className="absolute start-1 top-1 inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground opacity-0 transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
                  onClick={onSwitch}
                  aria-label={t("thread.composer.liveVoice.avatar.switch", { defaultValue: "Switch avatar and voice" })}
                  title={t("thread.composer.liveVoice.avatar.switch", { defaultValue: "Switch avatar and voice" })}
                >
                  <Users className="h-4 w-4" aria-hidden />
                </button>
              ) : null}
              {icons ? null : (
                <button
                  type="button"
                  className="absolute end-1 top-1 inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground opacity-0 transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
                  onClick={() => setCollapsed(true)}
                  aria-label={t("thread.composer.liveVoice.avatar.collapse", { defaultValue: "Hide the avatar" })}
                >
                  <ChevronDown className="h-4 w-4" aria-hidden />
                </button>
              )}
            </div>
          )}
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

const FADE_MASK =
  "linear-gradient(to bottom, #000 82%, transparent 100%), "
  + "linear-gradient(to right, transparent 0, #000 4%, #000 96%, transparent 100%)";
const FADE_EDGES: CSSProperties = {
  maskImage: FADE_MASK,
  WebkitMaskImage: FADE_MASK,
  maskComposite: "intersect",
  WebkitMaskComposite: "source-in",
};

/** Drive one mouth shape through TalkingHead's realtime channel. */
function setMouth(head: TalkingHead, shape: string, value: number | null) {
  const target = head.mtAvatar?.[shape];
  if (!target || target.realtime === value) return;
  target.realtime = value;
  target.needsUpdate = true;
}

function AvatarStage({
  body,
  levelRef,
  view,
}: {
  body: AvatarBody;
  levelRef: { current: () => number };
  view: "mid" | "head";
}) {
  const { t } = useTranslation();
  const nodeRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const node = nodeRef.current;
    if (!node) return;
    let head: TalkingHead | null = null;
    let frame = 0;
    let disposed = false;
    let mouth = 0;
    setReady(false);
    setFailed(false);

    const drive = (now: number) => {
      if (!head) return;
      // Smooth the loudness so the jaw does not flicker, then split it over
      // an open and a rounded mouth for a less mechanical motion. TalkingHead's
      // realtime channel applies at once; null hands the mouth back to its
      // idle animation between sentences.
      mouth = mouth * 0.55 + levelRef.current() * 0.45;
      const seconds = now / 1000;
      const open = mouth > 0.02;
      setMouth(head, "jawOpen", open ? mouth * 0.28 : null);
      setMouth(head, "viseme_aa", open ? mouth * (0.32 + 0.16 * Math.sin(seconds * 9)) : null);
      setMouth(head, "viseme_O", open ? mouth * (0.12 + 0.12 * Math.cos(seconds * 6)) : null);
      frame = requestAnimationFrame(drive);
    };

    void (async () => {
      try {
        // Loaded with the first Live call only: three.js scene, avatar, GLB.
        const { TalkingHead } = await import("@met4citizen/talkinghead");
        if (disposed) return;
        head = new TalkingHead(node, {
          ttsEndpoint: null,
          lipsyncModules: [],
          cameraView: view,
          // Down to the hips, hands included, the whole avatar in the column.
          // The idle pose rests on one hip and leans right: cameraX
          // brings it back to the center.
          ...(view === "mid" ? { cameraDistance: 0.5, cameraY: 0, cameraX: -0.1 } : {}),
          cameraRotateEnable: false,
          cameraPanEnable: false,
          cameraZoomEnable: false,
          avatarMood: "happy",
          modelFPS: 30,
          modelPixelRatio: Math.min(window.devicePixelRatio || 1, 1.5),
          avatarIdleEyeContact: 0.6,
          avatarSpeakingEyeContact: 0.8,
        });
        try {
          await head.showAvatar({ url: LIVE_AVATAR_URLS[body], body, avatarMood: "happy" });
        } catch (error) {
          // No male.glb yet: every voice gets the female avatar.
          if (body === "F") throw error;
          await head.showAvatar({ url: LIVE_AVATAR_URLS.F, body: "F", avatarMood: "happy" });
        }
        if (disposed) return;
        setReady(true);
        frame = requestAnimationFrame(drive);
      } catch (error) {
        console.warn("live avatar unavailable", error);
        if (!disposed) setFailed(true);
      }
    })();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      try {
        head?.dispose();
      } catch {
        // already torn down
      }
      head = null;
      node.replaceChildren();
    };
  }, [body, levelRef, view]);

  if (failed) {
    return (
      <div className="flex h-full w-full items-center justify-center text-muted-foreground">
        <UserRound className="h-10 w-10" aria-hidden />
      </div>
    );
  }
  return (
    <>
      {/* No hard edge: the body fades out at the bottom and on the sides. */}
      <div ref={nodeRef} className="h-full w-full" style={FADE_EDGES} aria-label={t("thread.composer.liveVoice.avatar.label", { defaultValue: "Live avatar" })} role="img" />
      {!ready ? (
        <div
          className="absolute inset-0 flex items-center justify-center"
          aria-label={t("thread.composer.liveVoice.avatar.loading", { defaultValue: "Avatar loading" })}
        >
          <span className="h-12 w-12 animate-pulse rounded-full bg-muted/50" />
        </div>
      ) : null}
    </>
  );
}
