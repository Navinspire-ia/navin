/**
 * Agent blocks: a note describes work, the block launches it.
 *
 * The block is stored as a fenced code block with the "agent" language, so
 * the note stays plain portable markdown (```agent ... ```), survives the
 * Tiptap round-trip unchanged, and is readable by the agent through the
 * ordinary file tools. Pure helpers live here so they are unit-testable.
 */

export const AGENT_BLOCK_LANGUAGE = "agent";

/** Default body inserted by the /agent slash command. */
export function agentBlockTemplate(
  objectiveLabel: string,
  outputLabel: string,
): string {
  return `objectif: ${objectiveLabel}\nsortie: ${outputLabel}`;
}

export interface AgentRunRequest {
  /** Title of the note holding the block. */
  noteTitle: string;
  /** Path of the note file, as shown to the agent (e.g. ~/.navin/notes/x.md). */
  notePath: string;
  /** Raw text content of the agent block. */
  spec: string;
  /** Notes store id; steers the agent to `notes action=append`. */
  noteId?: string;
}

/**
 * Chat prompt for one Run click.
 *
 * The agent gets the block spec plus where the note lives, and is told to do
 * the work now and append its result to the note. With an id the append goes
 * through the `notes` tool (history, index and the editor's conflict token
 * stay right); the file path remains as a fallback.
 */
export function buildAgentRunPrompt(request: AgentRunRequest): string {
  const spec = request.spec.trim();
  const parts: string[] = [];
  if (request.noteId) parts.push(`id ${request.noteId}`);
  if (request.notePath) parts.push(`fichier ${request.notePath}`);
  const where = parts.length
    ? `« ${request.noteTitle} » (${parts.join(", ")})`
    : `« ${request.noteTitle} »`;
  const howToWrite = request.noteId
    ? [
        `Fais le travail demandé maintenant. Quand c'est fini, ajoute le résultat`,
        `à la fin de la note avec l'outil notes (action=append id=${request.noteId}),`,
        "sous une section « ## Résultat » (garde le reste de la note intact).",
      ]
    : [
        "Fais le travail demandé maintenant. Quand c'est fini, ajoute le résultat",
        "à la fin du fichier de la note, sous une section « ## Résultat » (garde",
        "le reste de la note intact).",
      ];
  return [
    `Exécute ce bloc agent de ma note ${where} :`,
    "",
    "```",
    spec,
    "```",
    "",
    ...howToWrite,
  ].join("\n");
}
