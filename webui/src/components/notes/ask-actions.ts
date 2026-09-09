// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Quick "Ask Navin" actions from a note toolbar.
 *
 * Each action seeds (or auto-sends) a chat prompt that identifies the note
 * (id + markdown path) and tells the agent how to write back into that same
 * note. With an id the agent is steered to the `notes` tool (action=read /
 * action=update): the store then keeps the metadata, the history snapshot and
 * the search index right, and the editor gets a fresh `updated` token so an
 * autosave cannot overwrite the agent's edit. The path stays as a fallback for
 * plain file tools.
 */

export type NoteAskAction = "summarize" | "translate" | "correct";
export type NoteAskLocale = "fr" | "en";

export interface NoteAskRequest {
  noteTitle: string;
  /** Path shown to the agent, e.g. ~/.navin/notes/sans-titre-3.md */
  notePath: string;
  /** Notes store id; lets the agent use the `notes` tool instead of raw files. */
  noteId?: string;
  /** Localized lead line, e.g. "Résume cette note" */
  lead: string;
  /** Language of the instructions body (default French, the historical one). */
  locale?: NoteAskLocale;
}

function noteWhere(request: NoteAskRequest): string {
  const parts: string[] = [];
  if (request.noteId) parts.push(`id ${request.noteId}`);
  if (request.notePath) {
    parts.push(
      `${request.locale === "en" ? "file" : "fichier"} ${request.notePath}`,
    );
  }
  return parts.length
    ? `« ${request.noteTitle} » (${parts.join(", ")})`
    : `« ${request.noteTitle} »`;
}

function toolHint(request: NoteAskRequest): string[] {
  if (!request.noteId) return [];
  return request.locale === "en"
    ? [
        "",
        `Use the notes tool: action=read id=${request.noteId} to get the current`,
        `markdown, then action=update id=${request.noteId} with the full new`,
        "markdown (the store keeps the history). Do not edit the file directly.",
      ]
    : [
        "",
        `Utilise l'outil notes : action=read id=${request.noteId} pour lire le`,
        `markdown actuel, puis action=update id=${request.noteId} avec le nouveau`,
        "markdown complet (l'historique est conservé). N'édite pas le fichier",
        "directement.",
      ];
}

/** Generic ask seed: note context only, user finishes the question. */
export function buildNoteAskSeed(request: NoteAskRequest): string {
  return `${request.lead} : ${noteWhere(request)}`;
}

/**
 * Action prompt: lead + where + instructions for what to write and where.
 */
export function buildNoteAskActionPrompt(
  action: NoteAskAction,
  request: NoteAskRequest,
): string {
  const head = `${request.lead} : ${noteWhere(request)}`;
  const en = request.locale === "en";
  let body: string[];
  if (action === "summarize") {
    body = en
      ? [
          "Read this note's markdown, then write the summary at the top of the",
          "same note (right below the YAML frontmatter if any, before the",
          "existing content), under a « ## Summary » section.",
          "Do not create another note. Keep the rest of the note intact.",
        ]
      : [
          "Lis le markdown de cette note, puis écris le résumé en haut",
          "de la même note (juste sous le frontmatter YAML s'il y en a un, avant",
          "le contenu existant), sous une section « ## Résumé ».",
          "Ne crée pas une autre note. Garde le reste de la note intact.",
        ];
  } else if (action === "translate") {
    body = en
      ? [
          "Read this note's markdown. Translate the content to French if it is",
          "mostly in English, to English otherwise.",
          "Write the translation below the existing text in the same note,",
          "under a « ## Translation » section. Do not create another note.",
          "Keep the original text intact.",
        ]
      : [
          "Lis le markdown de cette note. Traduis le contenu vers",
          "l'anglais s'il est surtout en français, vers le français sinon.",
          "Écris la traduction en dessous du texte existant dans la même note,",
          "sous une section « ## Traduction ». Ne crée pas une autre note.",
          "Garde le texte original intact.",
        ];
  } else {
    body = en
      ? [
          "Read this note's markdown, then correct the text in place in the same",
          "note (spelling, grammar, clarity) without changing the meaning.",
          "Replace the content with the corrected version.",
          "Do not create another note. Do not duplicate the text.",
        ]
      : [
          "Lis le markdown de cette note, puis corrige directement le",
          "texte dans la même note (orthographe, grammaire, clarté) sans",
          "changer le sens. Remplace le contenu corrigé en place.",
          "Ne crée pas une autre note. Ne duplique pas le texte.",
        ];
  }
  return [head, "", ...body, ...toolHint(request)].join("\n");
}
