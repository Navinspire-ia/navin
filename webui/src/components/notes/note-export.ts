/**
 * Per-note export helpers.
 *
 * - Markdown keeps TipTap's on-disk format (including colour/highlight HTML).
 * - Text strips markup to readable plain text.
 * - PDF prints the editor HTML so colours, highlights, tables, tasks, etc. show.
 */

import { printHtmlDocument } from "@/lib/printHtml";
import { saveTextFile } from "@/lib/save-blob";

export type NoteExportFormat = "md" | "txt" | "pdf";

export interface NoteExportContent {
  markdown: string;
  /** TipTap getHTML() when the editor is mounted - preferred for PDF. */
  html?: string | null;
}

export function noteExportSlug(title: string): string {
  const base = (title || "note")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/gu, "")
    .replace(/[^a-zA-Z0-9._-]+/gu, "-")
    .replace(/^-+|-+$/gu, "")
    .toLowerCase();
  return base || "note";
}

function decodeBasicEntities(value: string): string {
  return value
    .replace(/&nbsp;/giu, " ")
    .replace(/&amp;/giu, "&")
    .replace(/&lt;/giu, "<")
    .replace(/&gt;/giu, ">")
    .replace(/&quot;/giu, '"')
    .replace(/&#39;/giu, "'")
    .replace(/&#(\d+);/gu, (_, code) =>
      String.fromCodePoint(Number.parseInt(code, 10)),
    );
}

/**
 * Unwrap HTML for plain-text export. Fenced code stays untouched.
 * Tolerates truncated TipTap tags (`</span` without `>`).
 */
export function stripNoteHtmlMarkup(markdown: string): string {
  const fences: string[] = [];
  const protectedMd = (markdown || "").replace(/```[\s\S]*?```/gu, (block) => {
    const index = fences.length;
    fences.push(block);
    return `\u0000FENCE${index}\u0000`;
  });

  const stripped = protectedMd
    .replace(/<br\s*\/?>/giu, "\n")
    .replace(/<\/(p|div|tr|li|h[1-6])>/giu, "\n")
    .replace(/<\/?[a-zA-Z][^>\n]*(?:>|$)/gu, "")
    // eslint-disable-next-line no-control-regex -- NUL bytes are the fence markers set above; they cannot appear in user text.
    .replace(/\u0000FENCE(\d+)\u0000/gu, (_, index) => fences[Number(index)] ?? "")
    .replace(/[ \t]+\n/gu, "\n")
    .replace(/\n{3,}/gu, "\n\n");

  return decodeBasicEntities(stripped);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/gu, "&amp;")
    .replace(/</gu, "&lt;")
    .replace(/>/gu, "&gt;")
    .replace(/"/gu, "&quot;");
}

/** Drop scripts / handlers before printing untrusted-ish editor HTML. */
export function sanitizeNoteExportHtml(html: string): string {
  return (html || "")
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/giu, "")
    .replace(/\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/giu, "")
    .replace(/javascript:/giu, "");
}

/**
 * When the editor is unavailable, turn TipTap markdown (with inline HTML) into
 * a printable body without escaping the colour spans.
 */
export function markdownWithHtmlToExportBody(markdown: string): string {
  const raw = (markdown || "").replace(/^\uFEFF/, "");
  const blocks: string[] = [];
  const parts = raw.split(/(```[\s\S]*?```)/gu);

  for (const part of parts) {
    if (!part) continue;
    if (part.startsWith("```")) {
      const match = part.match(/^```([\w-]*)\n?([\s\S]*?)```$/u);
      const lang = match?.[1] ?? "";
      const code = escapeHtml(match?.[2] ?? part.slice(3));
      blocks.push(
        `<pre><code class="language-${escapeHtml(lang)}">${code}</code></pre>`,
      );
      continue;
    }

    const lines = part.replace(/\r\n/gu, "\n").split("\n");
    let paragraph: string[] = [];
    let list: "ul" | "ol" | null = null;

    const flushParagraph = () => {
      if (!paragraph.length) return;
      blocks.push(`<p>${paragraph.join("<br>")}</p>`);
      paragraph = [];
    };
    const flushList = () => {
      if (!list) return;
      blocks.push(`</${list}>`);
      list = null;
    };

    for (const line of lines) {
      const trimmed = line.trimEnd();
      if (!trimmed.trim()) {
        flushParagraph();
        flushList();
        continue;
      }
      const heading = trimmed.match(/^(#{1,6})\s+(.*)$/u);
      if (heading) {
        flushParagraph();
        flushList();
        const level = heading[1].length;
        blocks.push(`<h${level}>${heading[2]}</h${level}>`);
        continue;
      }
      const bullet = trimmed.match(/^\s*[-*+]\s+(.*)$/u);
      if (bullet) {
        flushParagraph();
        if (list !== "ul") {
          flushList();
          blocks.push("<ul>");
          list = "ul";
        }
        blocks.push(`<li>${bullet[1]}</li>`);
        continue;
      }
      const numbered = trimmed.match(/^\s*\d+[.)]\s+(.*)$/u);
      if (numbered) {
        flushParagraph();
        if (list !== "ol") {
          flushList();
          blocks.push("<ol>");
          list = "ol";
        }
        blocks.push(`<li>${numbered[1]}</li>`);
        continue;
      }
      flushList();
      // Keep TipTap inline HTML (span/mark/strong…) as-is; only light md marks.
      const rich = trimmed
        .replace(/\*\*([^*]+)\*\*/gu, "<strong>$1</strong>")
        .replace(/__([^_]+)__/gu, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*]+)\*/gu, "$1<em>$2</em>")
        .replace(/`([^`]+)`/gu, "<code>$1</code>");
      paragraph.push(rich);
    }
    flushParagraph();
    flushList();
  }

  return sanitizeNoteExportHtml(blocks.join("\n"));
}

/** Full markdown file: keeps colour HTML so re-import keeps formatting. */
export function buildNoteMarkdownExport(title: string, markdown: string): string {
  const body = (markdown || "").replace(/^\uFEFF/, "").trimEnd();
  const trimmedTitle = (title || "").trim() || "Untitled";
  if (/^#\s+\S/u.test(body)) {
    return `${body}\n`;
  }
  if (!body) {
    return `# ${trimmedTitle}\n`;
  }
  return `# ${trimmedTitle}\n\n${body}\n`;
}

/** Plain text: no HTML, light markdown stripped. */
export function buildNotePlainTextExport(title: string, markdown: string): string {
  const md = buildNoteMarkdownExport(title, stripNoteHtmlMarkup(markdown));
  const text = md
    .replace(/^```[\w-]*\n([\s\S]*?)```$/gmu, "$1")
    .replace(/^#{1,6}\s+/gmu, "")
    .replace(/!\[[^\]]*\]\([^)]+\)/gu, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/gu, "$1")
    .replace(/`([^`]+)`/gu, "$1")
    .replace(/\*\*([^*]+)\*\*/gu, "$1")
    .replace(/__([^_]+)__/gu, "$1")
    .replace(/(^|[^*])\*([^*]+)\*/gu, "$1$2")
    .replace(/(^|[^_])_([^_]+)_/gu, "$1$2")
    .replace(/^>\s?/gmu, "")
    .replace(/^[-*+]\s+/gmu, "• ")
    .replace(/^\d+[.)]\s+/gmu, "")
    .replace(/\n{3,}/gu, "\n\n")
    .trim();
  return `${text}\n`;
}

const PDF_STYLES = `
  @page { margin: 18mm 16mm; }
  body {
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.65;
    color: #111;
    max-width: 46rem;
    margin: 0 auto;
  }
  h1.doc-title { font-size: 1.75rem; margin: 0 0 1.1rem; line-height: 1.25; }
  h1 { font-size: 1.55rem; margin: 1.2rem 0 0.55rem; }
  h2 { font-size: 1.3rem; margin: 1.15rem 0 0.5rem; }
  h3, h4, h5 { font-size: 1.08rem; margin: 1rem 0 0.4rem; }
  p { margin: 0.45rem 0; }
  ul, ol { margin: 0.4rem 0 0.4rem 1.25rem; padding: 0; }
  li { margin: 0.2rem 0; }
  ul[data-type="taskList"] { list-style: none; margin-left: 0; padding-left: 0; }
  ul[data-type="taskList"] li {
    display: flex;
    gap: 0.55em;
    align-items: flex-start;
  }
  ul[data-type="taskList"] li > label { margin-top: 0.2em; }
  ul[data-type="taskList"] li[data-checked="true"] > div {
    color: #71717a;
    text-decoration: line-through;
  }
  blockquote {
    margin: 0.7rem 0;
    padding: 0.15rem 0 0.15rem 0.9rem;
    border-left: 3px solid #d4d4d8;
    color: #3f3f46;
  }
  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.92em;
    background: #f4f4f5;
    padding: 0.1em 0.35em;
    border-radius: 3px;
  }
  pre {
    background: #f4f4f5;
    padding: 0.75rem 0.9rem;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 0.9em;
  }
  pre code { background: none; padding: 0; }
  table { border-collapse: collapse; width: 100%; margin: 0.8rem 0; }
  th, td { border: 1px solid #d4d4d8; padding: 0.35rem 0.55rem; text-align: left; }
  th { background: #f4f4f5; }
  a { color: #1d4ed8; }
  img { max-width: 100%; height: auto; }
  mark { padding: 0.05em 0.15em; border-radius: 2px; }
  u { text-decoration: underline; }
  s { text-decoration: line-through; }
  strong { font-weight: 700; }
  em { font-style: italic; }
`;

/** Standalone HTML document for the print / Save as PDF dialog. */
export function buildNotePdfHtml(
  title: string,
  content: NoteExportContent,
): string {
  const safeTitle = escapeHtml((title || "").trim() || "Untitled");
  const fromEditor = (content.html || "").trim();
  const bodyHtml = sanitizeNoteExportHtml(
    fromEditor || markdownWithHtmlToExportBody(content.markdown || ""),
  );
  return `<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8" />
<title>${safeTitle}</title>
<style>${PDF_STYLES}</style>
</head>
<body>
<h1 class="doc-title">${safeTitle}</h1>
${bodyHtml}
</body>
</html>`;
}

export function downloadTextFile(
  filename: string,
  content: string,
  mime: string,
): void {
  saveTextFile(filename, content, mime);
}

export function exportNote(
  format: NoteExportFormat,
  title: string,
  content: NoteExportContent,
): void {
  const slug = noteExportSlug(title);
  if (format === "md") {
    downloadTextFile(
      `${slug}.md`,
      buildNoteMarkdownExport(title, content.markdown),
      "text/markdown;charset=utf-8",
    );
    return;
  }
  if (format === "txt") {
    downloadTextFile(
      `${slug}.txt`,
      buildNotePlainTextExport(title, content.markdown),
      "text/plain;charset=utf-8",
    );
    return;
  }
  printHtmlDocument(buildNotePdfHtml(title, content));
}
