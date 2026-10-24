// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import MarkdownTextRenderer from "@/components/MarkdownTextRenderer";

const globalsCss = readFileSync(resolve(__dirname, "../globals.css"), "utf8");
const bubbleSource = readFileSync(resolve(__dirname, "MessageBubble.tsx"), "utf8");

const SAMPLE = [
  "Intro paragraph with `inline code` and **bold**.",
  "",
  "## Résultat",
  "",
  "- first `a.ts` item",
  "- second item",
  "",
  "> A note",
  "",
  "| A | B |",
  "| --- | --- |",
  "| 1 | 2 |",
].join("\n");

describe("assistant markdown typography", () => {
  const html = renderToStaticMarkup(createElement(MarkdownTextRenderer, { highlightCode: false, children: SAMPLE }));

  it("renders through one prose container driven by the shared chat scale", () => {
    expect(html).toContain('class="markdown-content prose max-w-none dark:prose-invert');
    // No per-instance size: the scale comes from --chat-font-size (Density).
    expect(html).not.toContain("text-[14px]");
    expect(html).not.toContain("line-height:var(--cjk-line-height)");
  });

  it("keeps headings, lists, quotes and tables as plain semantic elements styled by globals.css", () => {
    expect(html).toContain("<h2>Résultat</h2>");
    expect(html).toContain("<blockquote>");
    expect(html).toContain("<table");
    expect(html).toMatch(/<li>first /);
  });

  it("renders inline code as a chip without per-element size utilities", () => {
    expect(html).toContain('<code class="font-mono">inline code</code>');
    expect(html).not.toContain("text-[0.9em]");
    // react-markdown's `node` prop must not leak into the DOM.
    expect(html).not.toContain('node="[object Object]"');
  });

  it("does not leak the hast node onto links and tables either", () => {
    const linked = renderToStaticMarkup(
      createElement(MarkdownTextRenderer, {
        highlightCode: false,
        children: "See [docs](https://example.com/x).\n\n| A |\n| --- |\n| 1 |",
      }),
    );
    expect(linked).toContain('href="https://example.com/x"');
    expect(linked).not.toContain('node="[object Object]"');
  });

  it("wraps tables in their own scroll box and lets cells wrap instead of being cut", () => {
    expect(html).toContain('<div class="markdown-table-scroll" data-testid="markdown-table-scroll"><table class="w-full">');
    // min-w-max kept every cell on one line, so wide tables overflowed the column.
    expect(html).not.toContain("min-w-max");
    expect(globalsCss).toMatch(/\.markdown-content \.markdown-table-scroll \{[^}]*max-width: 100%;[^}]*overflow-x: auto;/);
    expect(globalsCss).toMatch(/\.markdown-content :where\(th, td\) \{[^}]*overflow-wrap: break-word;/);
    expect(globalsCss).toContain("@container thread-viewport (max-width: 40rem)");
  });
});

describe("chat reading scale (Density preference)", () => {
  it("defines the scale on the root and a compact variant", () => {
    expect(globalsCss).toContain('[data-chat-density="comfortable"]');
    expect(globalsCss).toContain('[data-chat-density="compact"]');
    expect(globalsCss).toMatch(/--chat-font-size:\s*14\.5px/);
    expect(globalsCss).toMatch(/--chat-font-size:\s*13\.5px/);
    expect(globalsCss).toMatch(/--cjk-line-height:\s*var\(--chat-line-height\)/);
  });

  it("gives the markdown body one typographic system in globals.css", () => {
    expect(globalsCss).toContain(".markdown-content :where(h1, h2, h3, h4, h5, h6)");
    expect(globalsCss).toMatch(/\.markdown-content :where\(h1\) \{\s*font-size: 1\.36em/);
    expect(globalsCss).toMatch(/\.markdown-content :where\(h2\) \{\s*font-size: 1\.22em/);
    expect(globalsCss).toContain(".markdown-content :where(:not(pre) > code)");
    expect(globalsCss).toContain(".markdown-content :where(blockquote p:first-of-type)::before");
    expect(globalsCss).toContain(".markdown-content :where(table)");
    // Body color comes from our tokens on both variable sets Typography uses.
    expect(globalsCss).toContain("--tw-prose-body: var(--md-body)");
    expect(globalsCss).toContain("--tw-prose-invert-body: var(--md-body)");
    expect(globalsCss).toMatch(/--md-body:\s*hsl\(var\(--foreground\) \/ 0\.92\)/);
  });

  it("scales the user pill and the assistant column with the same variables", () => {
    expect(bubbleSource).toContain("text-[length:var(--chat-font-size)] leading-[var(--chat-line-height)]");
    expect(bubbleSource).not.toContain("text-[14px]/[1.6]");
  });

  it("names a bold-only paragraph so the answer under a journal can set it as a section title", () => {
    const lead = renderToStaticMarkup(createElement(MarkdownTextRenderer, { highlightCode: false, children: "**What was done**\n\n**Lead:** then text." }));
    expect(lead).toContain('<p class="md-lead"><strong>What was done</strong></p>');
    expect(lead).toContain("<p><strong>Lead:</strong> then text.</p>");
    expect(globalsCss).toContain('[data-agent-result="true"] .markdown-content :where(h1, h2, h3, h4, h5, h6, p.md-lead)');
  });
});
