/** Turn HTML (and leftover tags like `<p>`) into readable plain text. Never invents content. */

function decodeEntities(text: string): string {
  return text
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCharCode(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)));
}

export function stripHtml(html: string): string {
  if (!html) return "";
  let text = decodeEntities(html);
  for (let pass = 0; pass < 2; pass += 1) {
    if (!/<[a-z/]/i.test(text)) break;
    text = text
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/(?:p|div|h[1-6]|li|tr|blockquote|ul|ol)>/gi, "\n")
      .replace(/<li[^>]*>/gi, "- ")
      .replace(/<[^>]+>/g, " ");
    text = decodeEntities(text);
  }
  return text
    .replace(/[\u2013\u2014]/g, "-")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function firstNonEmpty(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string") {
      const text = stripHtml(value).trim();
      if (text) return text;
    }
  }
  return "";
}
