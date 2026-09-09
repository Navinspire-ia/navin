import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  Customizer, DefaultButton, Dropdown, IconButton, MessageBar, MessageBarType,
  Spinner, Stack, Text, createTheme,
} from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { useThemeValue } from "@/hooks/useTheme";
import { liveFramePoint, liveKeyInput, type AgentLiveSession } from "@/lib/agent-live";
import type { AgentBrowserInputAction, AgentBrowserInputPayload, NavinClient } from "@/lib/navin-client";
import "./agent-live-view.css";
import "@/lib/fluent-icons";

const AgentDesktopScene = lazy(() => import("./AgentDesktopScene"));

export function AgentLiveView({
  client, session, sessions, onSelect, onClosed, onMinimize,
}: {
  client: NavinClient;
  session: AgentLiveSession;
  sessions: AgentLiveSession[];
  onSelect: (id: string) => void;
  onClosed: (id: string) => void;
  onMinimize: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const dark = useThemeValue() === "dark";
  const reduced = useReducedMotion();
  const theme = useMemo(() => createTheme({
    isInverted: dark,
    palette: dark ? {
      themePrimary: "#62abf5", white: "#17191d", black: "#f5f5f5",
      neutralLighterAlt: "#1b1e23", neutralLighter: "#22262c", neutralLight: "#303640",
      neutralQuaternaryAlt: "#404650", neutralQuaternary: "#505864", neutralTertiaryAlt: "#67717f",
      neutralTertiary: "#a1aab5", neutralSecondary: "#c2c8d0", neutralPrimaryAlt: "#e2e6ec",
      neutralPrimary: "#f5f5f5", neutralDark: "#ffffff",
    } : { themePrimary: "#0078d4" },
    defaultFontStyle: { fontFamily: "inherit" },
  }), [dark]);
  const [control, setControl] = useState(session.userControl);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [spatial, setSpatial] = useState(false);
  const image = useRef<HTMLImageElement>(null);
  const activeId = useRef(session.id);
  activeId.current = session.id;
  const desktop = session.id.startsWith("desktop-");
  const label = desktop ? tx("dev.agentDesktopTab", "Agent desktop") : tx("dev.agentBrowserTab", "Agent browser");
  const source = session.frame ? `data:image/jpeg;base64,${session.frame}` : "";

  useEffect(() => { setControl(session.userControl); }, [session.id, session.userControl]);
  useEffect(() => { setError(null); setPending(false); setSpatial(false); }, [session.id]);

  async function send(action: AgentBrowserInputAction, payload: AgentBrowserInputPayload = {}) {
    const id = session.id;
    try {
      const result = await client.agentBrowserInput(session.chatId, action, payload, id);
      if (activeId.current === id) {
        setControl(result.userControl);
        setError(null);
      }
      return true;
    } catch (reason) {
      if (activeId.current === id) setError(reason instanceof Error ? reason.message : String(reason));
      return false;
    }
  }

  async function toggleControl() {
    setPending(true);
    setSpatial(false);
    const id = session.id;
    const ok = await send(control ? "release" : "takeover");
    if (activeId.current === id) {
      setPending(false);
      if (ok && !control) requestAnimationFrame(() => image.current?.focus());
    }
  }

  function point(event: { currentTarget: HTMLImageElement; clientX: number; clientY: number }) {
    const target = event.currentTarget;
    return liveFramePoint(target.getBoundingClientRect(), target.naturalWidth, target.naturalHeight, event.clientX, event.clientY);
  }

  async function close() {
    if (!session.live) { onClosed(session.id); return; }
    setPending(true);
    try {
      await client.agentBrowserClose(session.chatId, session.id);
      onClosed(session.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPending(false);
    }
  }

  return (
    <Customizer settings={{ theme }}>
      <div className="navin-agent-live" data-testid="agent-live-view">
        <Stack horizontal wrap verticalAlign="center" tokens={{ childrenGap: 8 }} className="navin-agent-live-toolbar">
          <span className={`navin-agent-live-status ${session.live ? "is-live" : ""}`} aria-hidden />
          <Text variant="small" aria-live="polite">
            {!session.live ? tx("dev.agentBrowserEnded", "Session closed") : control
              ? tx("dev.agentBrowserTakeoverOn", "You have control")
              : tx("dev.agentLiveDriving", "Agent in control")}
          </Text>
          {sessions.length > 1 ? (
            <Dropdown
              ariaLabel={tx("dev.agentLiveSession", "Live session")}
              selectedKey={session.id}
              options={sessions.map((item) => ({ key: item.id, text: `${item.id.startsWith("desktop-") ? tx("dev.agentDesktopTab", "Agent desktop") : tx("dev.agentBrowserTab", "Agent browser")} ${item.id.slice(-4)}` }))}
              onChange={(_, option) => { if (option) onSelect(String(option.key)); }}
              styles={{ root: { minWidth: 145 }, dropdown: { minHeight: 40 } }}
            />
          ) : <Text className="navin-agent-live-title">{label}</Text>}
          <span className="navin-agent-live-spacer" />
          <motion.div whileTap={reduced ? undefined : { scale: 0.96 }}>
            <DefaultButton
              text={pending ? tx("dev.agentLiveApplying", "Applying...") : control
                ? tx("dev.agentLiveRelease", "Return control to agent")
                : tx("dev.agentBrowserTakeover", "Take control")}
              iconProps={{ iconName: control ? "Play" : "TouchPointer" }}
              checked={control}
              aria-pressed={control}
              disabled={!session.live || pending}
              onClick={() => void toggleControl()}
              styles={{ root: { minHeight: 40 } }}
            />
          </motion.div>
          <IconButton
            iconProps={{ iconName: spatial ? "TVMonitor" : "CubeShape" }}
            title={spatial ? tx("dev.agentLiveFlat", "Screen view") : tx("dev.agentLiveSpatial", "3D screen view")}
            ariaLabel={spatial ? tx("dev.agentLiveFlat", "Screen view") : tx("dev.agentLiveSpatial", "3D screen view")}
            checked={spatial}
            disabled={control || !source || !!reduced}
            onClick={() => setSpatial((value) => !value)}
            styles={{ root: { width: 40, height: 40 } }}
          />
          <IconButton iconProps={{ iconName: "ChromeMinimize" }} ariaLabel={tx("dev.agentBrowserMinimize", "Minimize")} title={tx("dev.agentBrowserMinimize", "Minimize")} onClick={onMinimize} styles={{ root: { width: 40, height: 40 } }} />
          <IconButton iconProps={{ iconName: "ChromeClose" }} ariaLabel={tx("dev.agentLiveClose", "Close this session")} title={tx("dev.agentLiveClose", "Close this session")} disabled={pending} onClick={() => void close()} styles={{ root: { width: 40, height: 40 } }} />
        </Stack>
        {error ? <MessageBar messageBarType={MessageBarType.error} onDismiss={() => setError(null)}>{error}</MessageBar> : null}
        <div className="navin-agent-live-screen">
          {spatial && source ? (
            <Suspense fallback={<Spinner label={tx("dev.agentLiveApplying", "Applying...")} />}>
              <AgentDesktopScene source={source} label={label} />
            </Suspense>
          ) : source ? (
            <img
              ref={image}
              src={source}
              alt={label}
              draggable={false}
              tabIndex={control && session.live ? 0 : -1}
              aria-label={control ? tx("dev.agentLiveKeyboardHint", "Remote screen. Ctrl+Alt+Escape returns control to the agent.") : label}
              className={control ? "is-controllable" : ""}
              onClick={control && session.live ? (event) => {
                const coordinates = point(event);
                if (coordinates) { image.current?.focus(); void send("click", { ...coordinates, count: 1 }); }
              } : undefined}
              onContextMenu={control && session.live ? (event) => {
                event.preventDefault();
                const coordinates = point(event);
                if (coordinates) void send("click", { ...coordinates, button: "right" });
              } : undefined}
              onWheel={control && session.live ? (event) => {
                const coordinates = point(event);
                if (coordinates) {
                  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? event.currentTarget.clientHeight : 1;
                  void send("scroll", { ...coordinates, dx: event.deltaX * unit, dy: event.deltaY * unit });
                }
              } : undefined}
              onKeyDown={control && session.live ? (event) => {
                if (event.ctrlKey && event.altKey && event.key === "Escape") {
                  event.preventDefault(); void toggleControl(); return;
                }
                const input = liveKeyInput({ ...event, isComposing: event.nativeEvent.isComposing });
                if (input) { event.preventDefault(); void send(input.action, input.payload); }
              } : undefined}
              onPaste={control && session.live ? (event) => {
                event.preventDefault();
                const text = event.clipboardData.getData("text/plain");
                if (text.length > 2000) { setError(tx("dev.agentLivePasteLimit", "Paste up to 2,000 characters at a time.")); return; }
                if (text) void send("text", { text });
              } : undefined}
              onCompositionEnd={control && session.live ? (event) => { if (event.data) void send("text", { text: event.data }); } : undefined}
            />
          ) : <Spinner label={tx("dev.agentDesktopWaiting", "Waiting for the first screen capture...")} />}
        </div>
        <Stack horizontal wrap tokens={{ childrenGap: 12 }} className="navin-agent-live-footer">
          <Text variant="small" className="navin-agent-live-url" title={session.url}>{desktop ? label : session.url}</Text>
          {session.frameWidth && session.frameHeight ? <Text variant="small">{session.frameWidth} x {session.frameHeight}</Text> : null}
          {control ? <Text variant="small">{tx("dev.agentLiveReleaseShortcut", "Ctrl+Alt+Esc to return control")}</Text> : null}
        </Stack>
        {session.actions.length > 0 ? (
          <div className="navin-agent-live-actions" aria-label={tx("dev.agentLiveActivity", "Recent activity")}>
            {session.actions.slice(-6).map((line, index) => <div key={`${index}-${line}`} title={line}>{line}</div>)}
          </div>
        ) : null}
      </div>
    </Customizer>
  );
}
