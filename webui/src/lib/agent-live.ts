import type { AgentBrowserInputPayload, AgentBrowserUpdate } from "./navin-client";

export interface AgentLiveSession {
  id: string;
  chatId: string;
  live: boolean;
  userControl: boolean;
  url: string;
  frame: string | null;
  frameWidth: number | null;
  frameHeight: number | null;
  actions: string[];
}

export function updateLiveSession(
  previous: AgentLiveSession | undefined,
  update: AgentBrowserUpdate,
): AgentLiveSession {
  const base = previous ?? {
    id: update.id,
    chatId: update.chatId,
    live: true,
    userControl: false,
    url: "",
    frame: null,
    frameWidth: null,
    frameHeight: null,
    actions: [],
  };
  if (!base.live && update.phase !== "start") return base;
  return {
    ...base,
    live: update.phase !== "exit",
    userControl: update.phase === "exit" ? false : (update.userControl ?? base.userControl),
    url: update.url ?? base.url,
    frame: update.phase === "frame" && update.data ? update.data : base.frame,
    frameWidth: update.width ?? base.frameWidth,
    frameHeight: update.height ?? base.frameHeight,
    actions: update.phase === "action" && update.action
      ? [...base.actions.slice(-49), update.action]
      : base.actions,
  };
}

/** Letterbox bars are not part of the remote screen. */
export function liveFramePoint(
  rect: { left: number; top: number; width: number; height: number },
  width: number,
  height: number,
  clientX: number,
  clientY: number,
): AgentBrowserInputPayload | null {
  if (![width, height, rect.width, rect.height, clientX, clientY].every(Number.isFinite)) return null;
  if (width <= 0 || height <= 0 || rect.width <= 0 || rect.height <= 0) return null;
  const scale = Math.min(rect.width / width, rect.height / height);
  const x = (clientX - rect.left - (rect.width - width * scale) / 2) / scale;
  const y = (clientY - rect.top - (rect.height - height * scale) / 2) / scale;
  if (x < 0 || y < 0 || x >= width || y >= height) return null;
  return { x, y, width, height };
}

export function liveKeyInput(event: {
  key: string;
  ctrlKey: boolean;
  altKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  isComposing?: boolean;
}): { action: "text" | "key"; payload: AgentBrowserInputPayload } | null {
  const { key } = event;
  if (event.isComposing || ["Shift", "Control", "Alt", "Meta", "Dead", "Process", "Unidentified"].includes(key)) return null;
  if (Array.from(key).length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) {
    return { action: "text", payload: { text: key } };
  }
  const combo = [
    event.ctrlKey ? "Control" : null,
    event.altKey ? "Alt" : null,
    event.metaKey ? "Meta" : null,
    event.shiftKey ? "Shift" : null,
    key,
  ].filter(Boolean).join("+");
  return { action: "key", payload: { key: combo } };
}
