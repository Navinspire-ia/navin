import { useEffect, useLayoutEffect } from "react";

/**
 * useLayoutEffect in the browser, useEffect during server rendering
 * (renderToString in tests/SSR), which silences React's SSR warning.
 */
export const useIsomorphicLayoutEffect =
  typeof window !== "undefined" && typeof window.document !== "undefined"
    ? useLayoutEffect
    : useEffect;
