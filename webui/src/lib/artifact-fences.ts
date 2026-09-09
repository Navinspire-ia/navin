// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Pure helper: extract ```html / ```mermaid fenced blocks from markdown text.
 * Mirrors ``navin.artifacts.detect.extract_fenced_artifacts`` for client tests
 * and optional local preview without waiting on the gateway.
 */

export type DetectedFence = {
  type: "html" | "mermaid";
  title: string;
  content: string;
  id: string;
};

const FENCE_RE = /```(html|mermaid)[^\n]*\n([\s\S]*?)(?:\n```|$)/gi;

function stableId(type: string, content: string): string {
  // Lightweight FNV-1a so the helper stays dependency-free in vitest.
  let hash = 0x811c9dc5;
  const input = `${type}\n${content}`;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return `auto-${type}-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

export function extractFencedArtifacts(text: string): DetectedFence[] {
  if (!text || !text.includes("```")) return [];
  const found: DetectedFence[] = [];
  const seen = new Set<string>();
  const re = new RegExp(FENCE_RE.source, FENCE_RE.flags);
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const lang = (match[1] || "").toLowerCase() as "html" | "mermaid";
    const body = (match[2] || "").replace(/^\n/, "").replace(/\n$/, "");
    if (!body.trim()) continue;
    const id = stableId(lang, body);
    if (seen.has(id)) continue;
    seen.add(id);
    found.push({
      type: lang,
      title: lang === "html" ? "HTML preview" : "Mermaid diagram",
      content: body,
      id,
    });
  }
  return found;
}
