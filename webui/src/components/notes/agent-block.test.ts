// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  AGENT_BLOCK_LANGUAGE,
  agentBlockTemplate,
  buildAgentRunPrompt,
} from "./agent-block";
import { filterSlashCommands } from "./slash-commands";

describe("agent block", () => {
  it("uses the agent fence language", () => {
    expect(AGENT_BLOCK_LANGUAGE).toBe("agent");
  });

  it("template carries the labels", () => {
    const text = agentBlockTemplate("faire X", "cette note");
    expect(text).toContain("objectif: faire X");
    expect(text).toContain("sortie: cette note");
  });

  it("run prompt includes spec, note title and path", () => {
    const prompt = buildAgentRunPrompt({
      noteTitle: "Étude marché",
      notePath: "~/.navin/notes/etude.md",
      spec: "objectif: trouver 50 leads\nsortie: cette note",
    });
    expect(prompt).toContain("Étude marché");
    expect(prompt).toContain("~/.navin/notes/etude.md");
    expect(prompt).toContain("objectif: trouver 50 leads");
    expect(prompt).toContain("## Résultat");
  });

  it("run prompt survives a note without path", () => {
    const prompt = buildAgentRunPrompt({
      noteTitle: "Sans titre",
      notePath: "",
      spec: "objectif: x",
    });
    expect(prompt).toContain("« Sans titre »");
    expect(prompt).not.toContain("(fichier");
  });

  it("the /agent slash command is discoverable", () => {
    expect(filterSlashCommands("agent").some((c) => c.id === "agent")).toBe(true);
    expect(filterSlashCommands("lancer").some((c) => c.id === "agent")).toBe(true);
    expect(filterSlashCommands("bot").some((c) => c.id === "agent")).toBe(true);
  });

  it("the /link slash command is discoverable", () => {
    expect(filterSlashCommands("link").some((c) => c.id === "link")).toBe(true);
    expect(filterSlashCommands("lien").some((c) => c.id === "link")).toBe(true);
    expect(filterSlashCommands("url").some((c) => c.id === "link")).toBe(true);
  });
});
