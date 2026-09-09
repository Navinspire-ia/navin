// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shortening helpers for values shown inside a single activity row.
 *
 * A tool argument is often a path several folders deep. Printed whole it wraps
 * over three lines and buries the only part that identifies it, so these keep
 * the tail and drop what does not fit.
 */

/** Roughly the width of one activity row at its current font size. */
const DEFAULT_BUDGET = 44;

/** Collapse `value` around a middle ellipsis, keeping more of the head. */
export function truncateMiddle(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  const head = Math.ceil((maxLength - 1) * 0.62);
  const tail = Math.floor((maxLength - 1) * 0.38);
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

/**
 * Keep the file name plus as many parent folders as `budget` allows.
 *
 * Grown from the right rather than cut to a fixed depth: `slide_01.html` is
 * what the reader is looking for, and the folders above it are worth showing
 * only while there is room. Returns `value` untouched when it already fits.
 */
export function shortenPath(value: string, budget: number = DEFAULT_BUDGET): string {
  const trimmed = value.trim();
  if (trimmed.length <= budget) return trimmed;

  const separator = trimmed.includes("\\") && !trimmed.includes("/") ? "\\" : "/";
  const segments = trimmed.split(/[\\/]/).filter(Boolean);
  if (segments.length <= 1) return truncateMiddle(trimmed, budget);

  const prefix = `…${separator}`;
  const leaf = segments[segments.length - 1];
  // The file name is the point of the path, so it is spent first and cut only
  // when it does not fit even alone. Truncating the whole tail instead would
  // eat the extension, which is the one part nobody can guess.
  if (prefix.length + leaf.length >= budget) {
    return `${prefix}${truncateMiddle(leaf, budget - prefix.length)}`;
  }

  const kept = [leaf];
  for (let index = segments.length - 2; index >= 0; index -= 1) {
    const candidate = [segments[index], ...kept].join(separator);
    if (prefix.length + candidate.length > budget) break;
    kept.unshift(segments[index]);
  }

  // Nothing was dropped, so the length came from separators or a leading slash
  // rather than from depth; an ellipsised prefix would be a lie.
  if (kept.length === segments.length) return truncateMiddle(trimmed, budget);
  return `${prefix}${kept.join(separator)}`;
}

/** True for values worth treating as a path rather than as prose. */
export function looksLikePath(value: string): boolean {
  return /[\\/]/.test(value) && !/\s{2,}/.test(value);
}

/** Shorten as a path when it is one, otherwise just cap the length. */
export function shortenArgValue(value: string, budget: number = DEFAULT_BUDGET): string {
  return looksLikePath(value) ? shortenPath(value, budget) : truncateMiddle(value, budget);
}
