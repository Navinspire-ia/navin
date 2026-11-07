/**
 * Which chat a runtime frame or a queued send belongs to.
 *
 * Several chats are open at once (side chat, Code, background turns) and some
 * frames reach every window. These checks keep one chat's events out of the
 * others.
 */

export interface RuntimeModelFrameMeta {
  chatId?: string | null;
  reason?: string | null;
}

/**
 * Whether a `runtime_model_updated` frame concerns the chat on screen.
 *
 * A frame for another chat never applies. The model of one chat's turn (task
 * routing: a `task:` or `route:` reason) applies only to the chat named in the
 * frame, never through a broadcast: taken from one, the bar above a chat
 * showed another chat's model, and its effort control changed that preset.
 */
export function runtimeModelFrameApplies(
  meta: RuntimeModelFrameMeta | null | undefined,
  chatId: string | null,
): boolean {
  const target = meta?.chatId || null;
  if (target && target !== "*" && chatId && target !== chatId) return false;
  const reason = meta?.reason || "";
  if (reason.startsWith("task:") || reason.startsWith("route:")) {
    return Boolean(chatId) && target === chatId;
  }
  return true;
}

/**
 * Whether a queued automatic send may go out in the chat whose history key is
 * `historyKey`. A request bound to a chat waits for that chat: unbound, a plan
 * relaunch went into whichever chat was open when the agent became idle.
 */
export function autoSendTargetsChat(
  targetKey: string | null | undefined,
  historyKey: string | null,
): boolean {
  return !targetKey || targetKey === historyKey;
}
