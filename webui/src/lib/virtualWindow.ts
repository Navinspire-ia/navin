/**
 * Measured-height windowing for long, variable-height lists.
 *
 * The chat thread used to mount every display unit. A conversation with
 * hundreds of tool clusters froze the tab; this keeps only the viewport plus
 * an overscan strip in the DOM and replaces the rest with spacers.
 */
import {
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";

export type VirtualWindow = {
  start: number;
  end: number;
  paddingTop: number;
  paddingBottom: number;
};

export const VIRTUALIZE_AFTER = 24;
export const DEFAULT_ESTIMATED_HEIGHT = 88;
export const DEFAULT_OVERSCAN = 8;

export function prefixHeights(
  count: number,
  heights: Array<number | undefined>,
  estimatedHeight: number,
): number[] {
  const prefix = new Array<number>(count + 1);
  prefix[0] = 0;
  const fallback = estimatedHeight > 0 ? estimatedHeight : DEFAULT_ESTIMATED_HEIGHT;
  for (let i = 0; i < count; i += 1) {
    const measured = heights[i];
    prefix[i + 1] = prefix[i] + (measured && measured > 0 ? measured : fallback);
  }
  return prefix;
}

export function computeVirtualWindow(opts: {
  count: number;
  scrollTop: number;
  viewportHeight: number;
  heights: Array<number | undefined>;
  estimatedHeight?: number;
  overscan?: number;
  pinIndexes?: number[];
}): VirtualWindow {
  const estimatedHeight = opts.estimatedHeight ?? DEFAULT_ESTIMATED_HEIGHT;
  const overscan = opts.overscan ?? DEFAULT_OVERSCAN;
  const { count, heights } = opts;
  if (count <= 0) {
    return { start: 0, end: 0, paddingTop: 0, paddingBottom: 0 };
  }

  const prefix = prefixHeights(count, heights, estimatedHeight);
  const total = prefix[count] ?? 0;
  const top = Math.max(0, opts.scrollTop);
  const bottom = top + Math.max(1, opts.viewportHeight);

  let start = 0;
  while (start < count && (prefix[start + 1] ?? 0) <= top) start += 1;
  let end = start;
  while (end < count && (prefix[end] ?? 0) < bottom) end += 1;

  start = Math.max(0, start - overscan);
  end = Math.min(count, end + overscan);

  for (const pin of opts.pinIndexes ?? []) {
    if (!Number.isFinite(pin)) continue;
    const index = Math.max(0, Math.min(count - 1, Math.floor(pin)));
    start = Math.min(start, index);
    end = Math.max(end, index + 1);
  }

  return {
    start,
    end,
    paddingTop: prefix[start] ?? 0,
    paddingBottom: Math.max(0, total - (prefix[end] ?? total)),
  };
}

export function useVirtualWindow(opts: {
  count: number;
  scrollParentRef?: RefObject<HTMLElement | null>;
  estimatedHeight?: number;
  overscan?: number;
  enabled?: boolean;
  pinIndexes?: number[];
}): {
  window: VirtualWindow | null;
  listRef: RefObject<HTMLDivElement>;
} {
  const {
    count,
    scrollParentRef,
    estimatedHeight = DEFAULT_ESTIMATED_HEIGHT,
    overscan = DEFAULT_OVERSCAN,
    enabled = true,
    pinIndexes,
  } = opts;
  const listRef = useRef<HTMLDivElement>(null);
  const heightsRef = useRef<Array<number | undefined>>([]);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(800);
  const [measureVersion, setMeasureVersion] = useState(0);

  useLayoutEffect(() => {
    if (heightsRef.current.length > count) {
      heightsRef.current.length = count;
    }
  }, [count]);

  useLayoutEffect(() => {
    const el = scrollParentRef?.current;
    if (!el || !enabled) return undefined;
    let frame = 0;
    const sync = () => {
      frame = 0;
      setScrollTop(el.scrollTop);
      setViewportHeight(el.clientHeight || 800);
    };
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(sync);
    };
    sync();
    el.addEventListener("scroll", onScroll, { passive: true });
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(onScroll);
    observer?.observe(el);
    return () => {
      el.removeEventListener("scroll", onScroll);
      observer?.disconnect();
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [count, enabled, scrollParentRef]);

  const windowRange = useMemo(() => {
    if (!enabled || count < VIRTUALIZE_AFTER) return null;
    return computeVirtualWindow({
      count,
      scrollTop,
      viewportHeight,
      heights: heightsRef.current,
      estimatedHeight,
      overscan,
      pinIndexes,
    });
  }, [
    count,
    enabled,
    estimatedHeight,
    measureVersion,
    overscan,
    pinIndexes,
    scrollTop,
    viewportHeight,
  ]);

  useLayoutEffect(() => {
    if (!enabled || !windowRange) return;
    const root = listRef.current;
    if (!root) return;
    const nodes = root.querySelectorAll<HTMLElement>("[data-virtual-index]");
    let changed = false;
    nodes.forEach((node) => {
      const index = Number(node.dataset.virtualIndex);
      if (!Number.isFinite(index) || index < 0 || index >= count) return;
      const height = node.offsetHeight;
      if (height <= 0) return;
      const previous = heightsRef.current[index];
      // Ignore sub-pixel / 1px jitter from font rounding. A 1px rewrite of
      // the spacer heights remounts the window edge and the thread blinks.
      if (previous !== undefined && Math.abs(previous - height) < 2) {
        return;
      }
      if (previous !== height) {
        heightsRef.current[index] = height;
        changed = true;
      }
    });
    if (changed) setMeasureVersion((version) => version + 1);
  }, [count, enabled, windowRange]);

  return { window: windowRange, listRef };
}
