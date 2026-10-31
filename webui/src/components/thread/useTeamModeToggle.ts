// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useRef, useState } from "react";

/**
 * The Team mode switch of the chat menu.
 *
 * The switch used to wait for the save and then for the team feed, which the
 * engine answers through the agent loop (seconds while a turn runs, a timeout
 * when it is busy). When that answer failed or came late, the chat kept the
 * value it had read when it opened: the switch did not move and only a
 * restart showed the real state.
 *
 * Now the switch moves at once (``pending``), the saved value the settings
 * route returns is the truth (``onSaved``), saves reach the engine in the
 * order they were made, and a save that fails puts the switch back and says
 * so (``failed``).
 */
export function useTeamModeToggle(
  save: (enabled: boolean) => Promise<boolean>,
  onSaved: (enabled: boolean) => void,
): {
  /** The value just asked for, until its save answers; null when none is in flight. */
  pending: boolean | null;
  /** The last switch could not be saved. */
  failed: boolean;
  toggle: (enabled: boolean) => void;
} {
  const [pending, setPending] = useState<boolean | null>(null);
  const [failed, setFailed] = useState(false);
  const seqRef = useRef(0);
  const queueRef = useRef<Promise<unknown>>(Promise.resolve());
  const saveRef = useRef(save);
  saveRef.current = save;
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;

  const toggle = useCallback((enabled: boolean) => {
    const seq = ++seqRef.current;
    setPending(enabled);
    setFailed(false);
    // One save at a time: on, off, on in a row reach the engine in that order.
    const run = queueRef.current.then(() => saveRef.current(enabled));
    queueRef.current = run.catch(() => undefined);
    run.then(
      (saved) => {
        // A newer switch is on its way; its own answer settles the state.
        if (seq !== seqRef.current) return;
        onSavedRef.current(saved);
        setPending(null);
      },
      (error: unknown) => {
        if (seq !== seqRef.current) return;
        console.error("Could not switch Team mode", error);
        setPending(null);
        setFailed(true);
      },
    );
  }, []);

  return { pending, failed, toggle };
}
