import { describe, expect, it } from "vitest";

import {
  buildNoteMarkdownExport,
  buildNotePdfHtml,
  buildNotePlainTextExport,
  markdownWithHtmlToExportBody,
  noteExportSlug,
  stripNoteHtmlMarkup,
} from "./note-export";

describe("note export", () => {
  it("slugs titles for filenames", () => {
    expect(noteExportSlug("Sans titre")).toBe("sans-titre");
    expect(noteExportSlug("Réunion Q1!")).toBe("reunion-q1");
    expect(noteExportSlug("   ")).toBe("note");
  });

  it("adds a title heading when the body has none", () => {
    expect(buildNoteMarkdownExport("Idées", "ligne un\nligne deux")).toBe(
      "# Idées\n\nligne un\nligne deux\n",
    );
  });

  it("keeps TipTap color spans in markdown export", () => {
    const md = buildNoteMarkdownExport(
      "Sans titre",
      '<span style="color: rgb(249, 115, 22);">hhhhjmcdAvv</span>',
    );
    expect(md).toContain('style="color: rgb(249, 115, 22);"');
    expect(md).toContain("hhhhjmcdAvv");
  });

  it("strips TipTap color spans for plain text only", () => {
    const text = buildNotePlainTextExport(
      "Sans titre",
      '<span style="color: rgb(249, 115, 22);">hhhhjmcdAvv</span>',
    );
    expect(text).toBe("Sans titre\n\nhhhhjmcdAvv\n");
    expect(text).not.toContain("<span");
  });

  it("strips truncated TipTap closing tags for txt", () => {
    expect(
      stripNoteHtmlMarkup(
        '<span style="color: rgb(249, 115, 22);">hhhhjmcdAvv</span',
      ),
    ).toBe("hhhhjmcdAvv");
  });

  it("preserves colours in pdf from editor html", () => {
    const html = buildNotePdfHtml("Sans titre", {
      markdown: "fallback",
      html: '<p><span style="color: rgb(249, 115, 22);">hhhhjmcdAvv</span></p>',
    });
    expect(html).toContain('style="color: rgb(249, 115, 22);"');
    expect(html).toContain("hhhhjmcdAvv");
    expect(html).toContain("<h1 class=\"doc-title\">Sans titre</h1>");
  });

  it("falls back to markdown+html body when editor html is missing", () => {
    const body = markdownWithHtmlToExportBody(
      'Hello <span style="color: #ef4444;">red</span> **bold**',
    );
    expect(body).toContain('style="color: #ef4444;"');
    expect(body).toContain("<strong>bold</strong>");
    const html = buildNotePdfHtml("Note", {
      markdown: 'Hello <span style="color: #ef4444;">red</span>',
      html: null,
    });
    expect(html).toContain('style="color: #ef4444;"');
  });

  it("keeps fenced code when stripping html", () => {
    const raw = "avant\n\n```html\n<span>keep</span>\n```\n\napres";
    expect(stripNoteHtmlMarkup(raw)).toContain("```html\n<span>keep</span>\n```");
  });
});
