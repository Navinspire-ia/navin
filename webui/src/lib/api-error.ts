// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Lives apart from the API client so that modules the client itself depends on
 * can recognise its errors without importing it back.
 */
export class ApiError extends Error {
  status: number;
  /** Machine-readable `code` from a JSON error body, when the gateway sent one. */
  code?: string;
  /** The parsed JSON error body, when there was one. */
  body?: Record<string, unknown>;
  constructor(status: number, message: string, body?: Record<string, unknown> | null) {
    super(message);
    this.status = status;
    this.name = "ApiError";
    if (body) {
      this.body = body;
      if (typeof body.code === "string") this.code = body.code;
    }
  }
}

/** Parse a JSON error body; anything else (HTML, plain text) gives null. */
export function parseApiErrorBody(text: string): Record<string, unknown> | null {
  const raw = (text || "").trim();
  if (!raw.startsWith("{")) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}
