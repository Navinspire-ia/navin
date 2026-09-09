// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  lazy,
  useState,
  type ComponentProps,
  type ComponentType,
  type LazyExoticComponent,
} from "react";

/**
 * `React.lazy` with retries and recovery: a chunk fetch can fail transiently
 * (HMR churn in dev, a stale hash right after a redeploy, a flaky network).
 * Without this a single failed import throws during render and, with no error
 * boundary above, unmounts the whole app - the "black screen" after clicking
 * a file.
 *
 * Two layers:
 * - the import itself is retried a couple of times with a short backoff;
 * - `React.lazy` caches a rejected import forever, so after a final failure a
 *   fresh lazy component is created for the next mount. An error boundary
 *   that re-mounts the subtree therefore gets a real second chance instead of
 *   replaying the cached rejection.
 */
// `any` is the only constraint that lets ComponentProps<T> infer the real prop
// types of the wrapped component; unknown or a record widens them away.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyWithRetry<T extends ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
  retries = 2,
  delayMs = 700,
): ComponentType<ComponentProps<T>> {
  let current: LazyExoticComponent<T> | null = null;
  let failed = false;

  const load = async (): Promise<{ default: T }> => {
    let lastError: unknown;
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      try {
        return await factory();
      } catch (error) {
        lastError = error;
        if (attempt < retries) {
          await new Promise((resolve) => setTimeout(resolve, delayMs * (attempt + 1)));
        }
      }
    }
    // The rejection has to stay cached on this lazy component so React can
    // throw it and the boundary above can offer a retry. Dropping `current`
    // here instead would hand the next render attempt a fresh lazy, which
    // suspends again: the panel would then stay on its loading fallback for
    // ever and the failure would never be reported.
    failed = true;
    throw lastError;
  };

  /** The lazy component a new mount should use: fresh after a failure. */
  function instanceForMount(): LazyExoticComponent<T> {
    if (!current || failed) {
      failed = false;
      current = lazy(load);
    }
    return current;
  }

  function Retryable(props: ComponentProps<T>) {
    // Held in state so re-renders keep replaying the same (possibly rejected)
    // import, while a remount - an error-boundary retry, or reopening the
    // panel - is a deliberate new attempt.
    const [Comp] = useState(instanceForMount);
    return <Comp {...props} />;
  }
  return Retryable;
}
