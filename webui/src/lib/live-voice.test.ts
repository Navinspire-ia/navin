import { describe, expect, it } from "vitest";

import { OTHER_CHOICE_ID, type PendingChoice } from "./choices";
import {
  answerExpected,
  choiceAnswerFromMatch,
  chunkSpeechText,
  completionCueWanted,
  liveVoiceStateFrom,
  matchChoiceFromSpeech,
  speechForChoice,
  speechTextFromMarkdown,
  speechDedupeKey,
  spokenProgressFromEvent,
  spokenTechnicalText,
  isSpokenStop,
  transcriptIsMeaningful,
} from "./live-voice";

const STRINGS = {
  codeOmitted: "code block, see the chat",
  restInChat: "The rest is in the chat.",
  link: "link",
};

function choice(overrides: Partial<PendingChoice> = {}): PendingChoice {
  return {
    requestId: "q1",
    question: "Which database do we use?",
    options: [
      { id: "pg", label: "PostgreSQL", detail: "managed", recommended: true },
      { id: "sqlite", label: "SQLite", detail: "", recommended: false },
      { id: "mongo", label: "MongoDB Atlas", detail: "", recommended: false },
    ],
    allowSkip: true,
    recommendedId: "pg",
    expiresAt: null,
    receivedAt: 0,
    ...overrides,
  };
}

describe("natural spoken text", () => {
  it("connects technology names in the conversation language", () => {
    expect(spokenTechnicalText("React/SQL. HTML/CSS et STT/TTS.", "fr-FR"))
      .toBe("React et S Q L. H T M L et C S S et S T T et T T S.");
    expect(spokenTechnicalText("React/SQL and React/TypeScript.", "en"))
      .toBe("React and S Q L and React and TypeScript.");
  });

  it("keeps paths, identifiers and unknown languages intact", () => {
    const exact = "/React/SQL /src/api.ts https://example.test/api sql_table React/Unknown";
    expect(spokenTechnicalText(exact, "fr")).toBe(exact);
    expect(spokenTechnicalText("React/SQL", "ja")).toBe("React/SQL");
  });

  it("speaks an explanation while keeping reasoning and tool traces silent", () => {
    const frame = { event: "message" as const, chat_id: "chat", turn_id: "turn", text: "Je vérifie le résultat." };
    expect(spokenProgressFromEvent({ ...frame, kind: "progress" })).toEqual({ text: frame.text, turnId: "turn" });
    expect(spokenProgressFromEvent({ ...frame, kind: "reasoning" })).toBeNull();
    expect(spokenProgressFromEvent({ ...frame, kind: "tool_hint" })).toBeNull();
    expect(spokenProgressFromEvent({ ...frame, kind: "progress", agent_ui: { kind: "computer", data: { action: "snapshot" } } })).toBeNull();
  });

  it("recognizes a direct stop without cancelling an ordinary instruction", () => {
    for (const stop of ["Stop.", "Arrête !", "annule la tâche", "Stop, please.", "Arrête s'il te plaît."])
      expect(isSpokenStop(stop)).toBe(true);
    for (const message of ["Stop after the tests", "Explique le bouton stop", "Ne t'arrête pas"])
      expect(isSpokenStop(message)).toBe(false);
  });
});

describe("speechTextFromMarkdown", () => {
  it("drops code blocks, links and markup while keeping the prose", () => {
    const markdown = [
      "## Plan",
      "I will **update** the `config.ts` file, then run the tests.",
      "",
      "```ts",
      "export const x = 1;",
      "```",
      "",
      "- Step one",
      "- Step two [docs](https://example.com/docs)",
      "",
      "See https://example.com for more.",
    ].join("\n");
    const spoken = speechTextFromMarkdown(markdown, STRINGS);
    expect(spoken).toBe(
      "Plan. I will update the config.ts file, then run the tests. (code block, see the chat). Step one. Step two docs. See link for more.",
    );
    expect(spoken).not.toContain("```");
    expect(spoken).not.toContain("**");
    expect(spoken).not.toContain("https://");
  });

  it("mentions code only once and reads tables as lists", () => {
    const markdown = [
      "| Name | Status |",
      "| --- | --- |",
      "| api | ok |",
      "```sh",
      "ls",
      "```",
      "```sh",
      "pwd",
      "```",
    ].join("\n");
    const spoken = speechTextFromMarkdown(markdown, STRINGS);
    expect(spoken).toBe("Name, Status. A P I, ok. (code block, see the chat).");
  });

  it("cuts long replies at a sentence and points to the chat", () => {
    const sentence = "This is one sentence of the long answer that keeps going. ";
    const spoken = speechTextFromMarkdown(sentence.repeat(60), STRINGS, { maxChars: 300 });
    expect(spoken.length).toBeLessThan(300 + STRINGS.restInChat.length + 2);
    expect(spoken.endsWith(STRINGS.restInChat)).toBe(true);
    expect(spoken).toMatch(/going\. The rest is in the chat\.$/);
  });

  it("returns an empty string for blank input", () => {
    expect(speechTextFromMarkdown("   \n", STRINGS)).toBe("");
  });
});

describe("chunkSpeechText", () => {
  it("keeps short text in one chunk", () => {
    expect(chunkSpeechText("Hello there.", 100)).toEqual(["Hello there."]);
  });

  it("splits between sentences under the limit", () => {
    const text = "First sentence here. Second sentence here. Third one is here too.";
    const chunks = chunkSpeechText(text, 45);
    expect(chunks).toEqual([
      "First sentence here. Second sentence here.",
      "Third one is here too.",
    ]);
    for (const chunk of chunks) expect(chunk.length).toBeLessThanOrEqual(45);
  });

  it("opens with a short chunk so the voice starts fast", () => {
    const long =
      "Ce projet est une application web de gestion des tickets resto : un backend FastAPI avec SQLite " +
      "et un frontend React avec MUI, avec authentification, création et suivi des tickets, et un " +
      "dashboard avec des statistiques détaillées pour les administrateurs.";
    const chunks = chunkSpeechText(long);
    expect(chunks.length).toBe(3);
    expect(chunks[0]).toBe("Ce projet est une application web de gestion des tickets resto :");
    // Growing chunks, each cut on a clause boundary, never mid-word.
    expect(chunks[1].length).toBeLessThanOrEqual(100);
    expect(chunks[1].endsWith("authentification,")).toBe(true);
    expect(chunks.join(" ")).toBe(long);

    const twoSentences = "Compris, je lance les tests. Ensuite je te fais un compte rendu détaillé de ce qui casse.";
    expect(chunkSpeechText(twoSentences, 420, [60])).toEqual([
      "Compris, je lance les tests.",
      "Ensuite je te fais un compte rendu détaillé de ce qui casse.",
    ]);
  });

  it("extends past the soft limit rather than cutting a clause mid-word", () => {
    const text =
      "je veux que tu regardes le fichier de configuration du serveur avant de lancer les tests, " +
      "puis que tu me fasses un retour.";
    expect(chunkSpeechText(text)).toEqual([
      "je veux que tu regardes le fichier de configuration du serveur avant de lancer les tests,",
      "puis que tu me fasses un retour.",
    ]);
  });

  it("hard-splits a single oversized sentence", () => {
    const text = "word ".repeat(60).trim();
    const chunks = chunkSpeechText(text, 50);
    expect(chunks.length).toBeGreaterThan(1);
    for (const chunk of chunks) expect(chunk.length).toBeLessThanOrEqual(50);
    expect(chunks.join(" ")).toBe(text);
  });
});

describe("speechForChoice", () => {
  it("reads the question, lettered options and the free answer hint", () => {
    const spoken = speechForChoice(choice(), {
      question: "Question",
      option: (letter) => `Option ${letter}`,
      recommended: "recommended",
      other: "You can also answer freely",
    });
    expect(spoken).toBe(
      "Question: Which database do we use? Option A: PostgreSQL, managed (recommended). Option B: SQLite. Option C: MongoDB Atlas. You can also answer freely.",
    );
  });
});

describe("matchChoiceFromSpeech", () => {
  it("understands letters, with or without a frame word", () => {
    expect(matchChoiceFromSpeech("B", choice())).toEqual({ kind: "option", optionId: "sqlite" });
    expect(matchChoiceFromSpeech("Option C.", choice())).toEqual({ kind: "option", optionId: "mongo" });
    expect(matchChoiceFromSpeech("la réponse A, s'il te plaît", choice())).toEqual({
      kind: "option",
      optionId: "pg",
    });
  });

  it("understands ordinals in short answers only", () => {
    expect(matchChoiceFromSpeech("la deuxième", choice())).toEqual({ kind: "option", optionId: "sqlite" });
    expect(matchChoiceFromSpeech("the last one", choice())).toEqual({ kind: "option", optionId: "mongo" });
    expect(matchChoiceFromSpeech("numéro un", choice())).toEqual({ kind: "option", optionId: "pg" });
    const long = matchChoiceFromSpeech(
      "je pense qu'il faut deux instances en plus pour tenir la charge",
      choice(),
    );
    expect(long).toEqual({ kind: "other", text: "je pense qu'il faut deux instances en plus pour tenir la charge" });
  });

  it("matches option labels said in a sentence", () => {
    expect(matchChoiceFromSpeech("on part sur postgresql", choice())).toEqual({
      kind: "option",
      optionId: "pg",
    });
    expect(matchChoiceFromSpeech("MongoDB Atlas please", choice())).toEqual({
      kind: "option",
      optionId: "mongo",
    });
  });

  it("recognises skip phrases and falls back to free text", () => {
    expect(matchChoiceFromSpeech("comme tu veux", choice())).toEqual({ kind: "skip" });
    expect(matchChoiceFromSpeech("up to you", choice())).toEqual({ kind: "skip" });
    expect(matchChoiceFromSpeech("utilise redis à la place", choice())).toEqual({
      kind: "other",
      text: "utilise redis à la place",
    });
    expect(matchChoiceFromSpeech("   ", choice())).toBeNull();
  });
});

describe("choiceAnswerFromMatch", () => {
  it("builds the same payload the card would send", () => {
    expect(choiceAnswerFromMatch({ kind: "option", optionId: "sqlite" }, choice())).toEqual({
      optionId: "sqlite",
      skipped: false,
      customText: "",
    });
    expect(choiceAnswerFromMatch({ kind: "skip" }, choice())).toEqual({
      optionId: "pg",
      skipped: true,
      customText: "",
    });
    expect(choiceAnswerFromMatch({ kind: "other", text: "redis" }, choice())).toEqual({
      optionId: OTHER_CHOICE_ID,
      skipped: false,
      customText: "redis",
    });
  });
});

describe("speechDedupeKey", () => {
  it("ignores case and whitespace so a re-keyed message is spoken once", () => {
    expect(speechDedupeKey("  C'est   fait.\nTu peux verifier. ")).toBe(
      speechDedupeKey("c'est fait. tu peux verifier."),
    );
    expect(speechDedupeKey("C'est fait.")).not.toBe(speechDedupeKey("C'est presque fait."));
  });
});

describe("transcriptIsMeaningful", () => {
  it("drops known STT hallucinations and stray syllables", () => {
    expect(transcriptIsMeaningful("Sous-titres réalisés par la communauté d'Amara.org")).toBe(false);
    expect(transcriptIsMeaningful("Thank you.")).toBe(false);
    expect(transcriptIsMeaningful("...")).toBe(false);
    expect(transcriptIsMeaningful("a")).toBe(false);
    expect(transcriptIsMeaningful("go ahead and deploy it")).toBe(true);
  });

  it("keeps back-channel words only when the assistant asked a question", () => {
    for (const noise of ["Yeah.", "Hmm.", "OK", "Euh...", "Oui, d'accord."]) {
      expect(transcriptIsMeaningful(noise)).toBe(false);
      expect(transcriptIsMeaningful(noise, { answerExpected: true })).toBe(true);
    }
    expect(transcriptIsMeaningful("oui, deploie-le", { answerExpected: false })).toBe(true);
    expect(answerExpected("Je peux lancer le deploiement maintenant ?")).toBe(true);
    expect(answerExpected("C'est fait, tout est vert.")).toBe(false);
    expect(answerExpected(undefined)).toBe(false);
  });
});

describe("completionCueWanted / liveVoiceStateFrom", () => {
  it("only chimes after a long run", () => {
    expect(completionCueWanted(null)).toBe(false);
    expect(completionCueWanted(3_000)).toBe(false);
    expect(completionCueWanted(45_000)).toBe(true);
  });

  it("derives the bar state in priority order", () => {
    const base = {
      enabled: true,
      sessionState: "listening" as const,
      muted: false,
      ttsPlaying: false,
      agentWorking: false,
      error: null,
    };
    expect(liveVoiceStateFrom({ ...base, enabled: false })).toBe("off");
    expect(liveVoiceStateFrom({ ...base, error: "permission" })).toBe("error");
    expect(liveVoiceStateFrom({ ...base, sessionState: "starting" })).toBe("starting");
    expect(liveVoiceStateFrom({ ...base, ttsPlaying: true, agentWorking: true })).toBe("speaking");
    expect(liveVoiceStateFrom({ ...base, agentWorking: true })).toBe("working");
    expect(liveVoiceStateFrom({ ...base, muted: true })).toBe("muted");
    expect(liveVoiceStateFrom(base)).toBe("listening");
  });
});
