// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useRef } from "react";

import { useClient } from "@/providers/ClientProvider";

/**
 * The API token as a ref that always holds the newest value.
 *
 * The gateway rotates the token every few minutes (300 s TTL, renewed 30 s
 * early). A loader that lists ``token`` in its dependencies reloads on every
 * rotation: the desktop log shows the rail counters, the board panels and
 * every file chip probe re-sent every 270 s, all day, each one a round trip
 * through the WSL bridge. Reading the token from this ref at call time keeps
 * the request authorized without making the rotation a reload trigger.
 */
export function useLatestTokenRef(): { readonly current: string } {
  const { token } = useClient();
  const ref = useRef(token);
  ref.current = token;
  return ref;
}
