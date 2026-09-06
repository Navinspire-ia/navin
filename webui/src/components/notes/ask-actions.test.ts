import { describe, expect, it } from "vitest";

import {
  buildNoteAskActionPrompt,
  buildNoteAskSeed,
} from "./ask-actions";

const base = {
  noteTitle: "Sans titre",
  notePath: "~/.navin/notes/sans-titre-3.md",
};

describe("note ask actions", () => {
  it("seed matches the Demander à Navin format", () => {
    const prompt = buildNoteAskSeed({
      ...base,
      lead: "À propos de cette note",
    });
    expect(prompt).toBe(
      "À propos de cette note : « Sans titre » (fichier ~/.navin/notes/sans-titre-3.md)",
    );
  });

  it("summarize writes at the top of the same file", () => {
    const prompt = buildNoteAskActionPrompt("summarize", {
      ...base,
      lead: "Résume cette note",
    });
    expect(prompt).toContain(
      "Résume cette note : « Sans titre » (fichier ~/.navin/notes/sans-titre-3.md)",
    );
    expect(prompt).toContain("## Résumé");
    expect(prompt).toContain("en haut");
    expect(prompt).toContain("Ne crée pas une autre note");
  });

  it("translate appends below the existing text", () => {
    const prompt = buildNoteAskActionPrompt("translate", {
      ...base,
      lead: "Traduis cette note",
    });
    expect(prompt).toContain("Traduis cette note");
    expect(prompt).toContain("## Traduction");
    expect(prompt).toContain("en dessous");
  });

  it("correct edits in place", () => {
    const prompt = buildNoteAskActionPrompt("correct", {
      ...base,
      lead: "Corrige cette note",
    });
    expect(prompt).toContain("Corrige cette note");
    expect(prompt).toContain("en place");
    expect(prompt).toContain("Ne duplique pas");
  });

  it("works without a path", () => {
    const prompt = buildNoteAskActionPrompt("summarize", {
      noteTitle: "Idées",
      notePath: "",
      lead: "Résume cette note",
    });
    expect(prompt).toContain("« Idées »");
    expect(prompt).not.toContain("(fichier");
  });

  it("with an id, steers the agent to the notes tool instead of raw files", () => {
    const prompt = buildNoteAskActionPrompt("correct", {
      ...base,
      noteId: "n42",
      lead: "Corrige cette note",
    });
    expect(prompt).toContain("(id n42, fichier ~/.navin/notes/sans-titre-3.md)");
    expect(prompt).toContain("action=read id=n42");
    expect(prompt).toContain("action=update id=n42");
    expect(prompt).toContain("N'édite pas le fichier");
  });

  it("speaks English when the UI does", () => {
    const prompt = buildNoteAskActionPrompt("summarize", {
      ...base,
      noteId: "n42",
      lead: "Summarize this note",
      locale: "en",
    });
    expect(prompt).toContain("(id n42, file ~/.navin/notes/sans-titre-3.md)");
    expect(prompt).toContain("## Summary");
    expect(prompt).toContain("Use the notes tool");
    expect(prompt).not.toContain("Résumé");
  });
});
