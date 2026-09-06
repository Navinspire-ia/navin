/**
 * Decides when a terminal tab should ask the gateway for its shell back.
 *
 * The gateway keys shells by websocket connection and kills them when it drops,
 * but the tab in the UI outlives the socket. Left alone the tab turns into a
 * dead box that answers every keystroke and every resize with "terminal not
 * found". Re-opening fixes it, so the only real questions are when to do it and
 * when to stop trying - which is what this tracks.
 *
 * A shell that ended is not a shell that was lost, and the difference matters:
 * re-opening a lost shell restores what the user had, while re-opening an ended
 * one hands back a shell nobody asked for. On a host that cannot keep a shell
 * alive at all, doing the second forever is what fills the tab with alternating
 * "process exited" and "new shell started".
 */

/** Errors arriving this soon after a re-open are the same loss, reported twice. */
const DEFAULT_QUIET_MS = 4000;
/** Enough to ride out a gateway restart; past it, re-opening is not the cure. */
const DEFAULT_MAX_ATTEMPTS = 3;
/**
 * How long a shell has to hold before its arrival counts as a recovery. Longer
 * than the quiet window on purpose: a shell that only ever survives from one
 * re-open to the next is failing, however many times it answers.
 */
const DEFAULT_SETTLED_MS = 15000;

export type RecoveryDecision =
  /** Send terminal_open again. */
  | "reopen"
  /** A re-open is already in flight, or the shell ended and should stay ended. */
  | "wait"
  /** Re-opening keeps failing - tell the user instead of looping. */
  | "give-up";

export interface TerminalRecovery {
  /** True once the gateway has confirmed a session for this tab. */
  readonly attached: boolean;
  /** Record a terminal_opened: the tab has a live shell again. */
  markAttached(): void;
  /** Record a terminal_exit: the shell ended, and nothing should replace it. */
  markExited(): void;
  /** Decide what to do about a shell that the gateway says it does not have. */
  decide(): RecoveryDecision;
}

export function createTerminalRecovery(
  options: {
    quietMs?: number;
    maxAttempts?: number;
    settledMs?: number;
    now?: () => number;
  } = {},
): TerminalRecovery {
  const quietMs = options.quietMs ?? DEFAULT_QUIET_MS;
  const maxAttempts = options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
  const settledMs = options.settledMs ?? DEFAULT_SETTLED_MS;
  const now = options.now ?? (() => Date.now());

  let attached = false;
  let exited = false;
  let attempts = 0;
  let quietUntil = 0;
  let attachedAt = now();

  return {
    get attached() {
      return attached;
    },
    markAttached() {
      attached = true;
      exited = false;
      attachedAt = now();
    },
    markExited() {
      exited = true;
    },
    decide() {
      // The shell ended rather than went missing. Whatever asks after it - a
      // late resize, a reconnect - is asking about something already finished.
      if (exited) return "wait";
      const at = now();
      if (at < quietUntil) return "wait";
      quietUntil = at + quietMs;
      // How long the shell being replaced managed to hold is only knowable
      // here, at the failure, which is why the budget is credited here rather
      // than on arrival. A shell that held is a recovery and earns a fresh
      // budget; one that only survived from re-open to re-open earns nothing,
      // however many times it answered.
      if (attached && at - attachedAt >= settledMs) attempts = 0;
      if (attempts >= maxAttempts) return "give-up";
      attempts += 1;
      return "reopen";
    },
  };
}
