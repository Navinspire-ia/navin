/**
 * Slash-menu catalog for the Notes editor.
 *
 * Pure data + filtering, kept out of the React component so the matching
 * logic is unit-testable and the menu stays in sync with what the editor
 * can actually insert.
 */

export type SlashCommandId =
  | "title"
  | "h1"
  | "h2"
  | "h3"
  | "text"
  | "todo"
  | "bullet"
  | "numbered"
  | "quote"
  | "code"
  | "table"
  | "divider"
  | "link"
  | "image"
  | "file"
  | "agent";

export interface SlashCommandItem {
  id: SlashCommandId;
  /** i18n key under notes.slash.*; defaultLabel is the fallback copy. */
  labelKey: string;
  defaultLabel: string;
  defaultHint: string;
  /** Matched against the text typed after "/" (any language + synonyms). */
  keywords: string[];
}

export const SLASH_COMMANDS: SlashCommandItem[] = [
  {
    id: "title",
    labelKey: "notes.slash.title",
    defaultLabel: "Title",
    defaultHint: "Big section heading",
    keywords: ["title", "titre", "heading", "h1"],
  },
  {
    id: "h2",
    labelKey: "notes.slash.h2",
    defaultLabel: "Heading 2",
    defaultHint: "Medium heading",
    keywords: ["h2", "heading 2", "sous-titre", "subtitle"],
  },
  {
    id: "h3",
    labelKey: "notes.slash.h3",
    defaultLabel: "Heading 3",
    defaultHint: "Small heading",
    keywords: ["h3", "heading 3", "sous-section"],
  },
  {
    id: "text",
    labelKey: "notes.slash.text",
    defaultLabel: "Text",
    defaultHint: "Plain paragraph",
    keywords: ["text", "texte", "paragraph", "paragraphe", "p"],
  },
  {
    id: "todo",
    labelKey: "notes.slash.todo",
    defaultLabel: "To-do",
    defaultHint: "Checkbox task list",
    keywords: ["todo", "task", "tache", "checkbox", "to-do"],
  },
  {
    id: "bullet",
    labelKey: "notes.slash.bullet",
    defaultLabel: "Bullet list",
    defaultHint: "Simple list",
    keywords: ["bullet", "list", "liste", "puces", "ul"],
  },
  {
    id: "numbered",
    labelKey: "notes.slash.numbered",
    defaultLabel: "Numbered list",
    defaultHint: "Ordered list",
    keywords: ["numbered", "ordered", "numerote", "ol", "123"],
  },
  {
    id: "quote",
    labelKey: "notes.slash.quote",
    defaultLabel: "Quote",
    defaultHint: "Callout quote block",
    keywords: ["quote", "citation", "blockquote"],
  },
  {
    id: "code",
    labelKey: "notes.slash.code",
    defaultLabel: "Code",
    defaultHint: "Code block with highlighting",
    keywords: ["code", "snippet", "bloc de code", "pre"],
  },
  {
    id: "table",
    labelKey: "notes.slash.table",
    defaultLabel: "Table",
    defaultHint: "3 x 3 table",
    keywords: ["table", "tableau", "grid"],
  },
  {
    id: "divider",
    labelKey: "notes.slash.divider",
    defaultLabel: "Divider",
    defaultHint: "Horizontal rule",
    keywords: ["divider", "separateur", "hr", "rule", "ligne"],
  },
  {
    id: "link",
    labelKey: "notes.slash.link",
    defaultLabel: "Link",
    defaultHint: "Insert a hyperlink",
    keywords: ["link", "lien", "url", "hyperlink", "href", "web"],
  },
  {
    id: "image",
    labelKey: "notes.slash.image",
    defaultLabel: "Image",
    defaultHint: "Upload and embed a picture",
    keywords: ["image", "photo", "picture", "img"],
  },
  {
    id: "file",
    labelKey: "notes.slash.file",
    defaultLabel: "File",
    defaultHint: "Attach a file",
    keywords: ["file", "fichier", "attachment", "piece jointe", "pdf"],
  },
  {
    id: "agent",
    labelKey: "notes.slash.agent",
    defaultLabel: "Agent block",
    defaultHint: "Describe a task, run it from the note",
    keywords: ["agent", "run", "lancer", "tache", "task", "bot", "ia", "ai"],
  },
];

/** Filter the catalog with the text typed after "/" (case/accent tolerant). */
export function filterSlashCommands(query: string): SlashCommandItem[] {
  const needle = normalize(query);
  if (!needle) return SLASH_COMMANDS;
  return SLASH_COMMANDS.filter(
    (item) =>
      normalize(item.id).includes(needle) ||
      normalize(item.defaultLabel).includes(needle) ||
      item.keywords.some((keyword) => normalize(keyword).includes(needle)),
  );
}

/**
 * Parse the current text block: a slash menu is open when the block starts
 * with "/" and the text after it is still a plausible command query.
 * Returns the query (text after "/") or null when the menu must stay closed.
 */
export function slashQueryFromBlockText(text: string): string | null {
  if (!text.startsWith("/")) return null;
  const query = text.slice(1);
  if (query.length > 24) return null;
  if (query.includes("/") || query.includes("\n")) return null;
  return query;
}

function normalize(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}
