import { describe, expect, it } from "vitest";

import {
  BRIEFING_MAX_CHARS,
  OPENING_MAX_CHARS,
  answerOpening,
  lastAssistantText,
  liveBriefing,
  liveDigest,
  offerAnswer,
} from "./live-digest";

const plan = [
  "## Plan de migration",
  "Voici le plan pour passer à Postgres.",
  "1. Ajouter le driver",
  "2. Écrire les migrations",
  "3. Copier les données",
  "4. Basculer la config",
  "5. Supprimer SQLite",
  "6. Tester en recette",
].join("\n");

describe("liveDigest", () => {
  it("names a plan and counts its steps", () => {
    expect(liveDigest(plan)).toEqual({ kind: "plan", count: 6 });
  });

  it("recognizes security reports and reviews", () => {
    const security = "# Rapport de sécurité\n- XSS dans le formulaire\n- Secret dans le dépôt\n- CSRF absent";
    expect(liveDigest(security)).toEqual({ kind: "security", count: 3 });
    const review = "## Code review\n1. Nommage\n2. Tests manquants\n3. Fuite mémoire";
    expect(liveDigest(review)).toEqual({ kind: "review", count: 3 });
  });

  it("leaves short answers alone and treats long prose as long", () => {
    expect(liveDigest("C'est fait, les tests passent.")).toBeNull();
    expect(liveDigest("Explication. ".repeat(80))).toEqual({ kind: "long", count: 0 });
  });

  it("ignores lists inside code blocks", () => {
    expect(liveDigest("```\n1. a\n2. b\n3. c\n```\nVoilà le plan.")).toBeNull();
  });
});

describe("liveBriefing", () => {
  it("hands the voice the answer to brief at once, code left out and bounded", () => {
    const report = `${plan}\n\n\n\`\`\`sql\nDROP TABLE legacy;\n\`\`\`\nLes tests passent.`;
    const briefing = liveBriefing({ kind: "plan", count: 6 }, report);
    expect(briefing).toContain("The agent finished a plan in 6 steps");
    expect(briefing).toContain("Tell the user its gist now");
    expect(briefing).toContain("do not offer to sum it up");
    expect(briefing).toContain("3. Copier les données");
    expect(briefing).toContain("[code in the chat]");
    expect(briefing).not.toContain("DROP TABLE");
    expect(briefing).not.toContain("\n\n\n");

    const long = liveBriefing({ kind: "long", count: 0 }, "Explication. ".repeat(1_000));
    expect(long).toContain("The agent finished a long answer");
    expect(long).toContain("[the rest is in the chat]");
    expect(long.length).toBeLessThan(BRIEFING_MAX_CHARS + 1_000);
  });
});

describe("answerOpening", () => {
  it("is the answer's own first prose, two sentences at most, never a heading, a list or code", () => {
    expect(answerOpening(plan)).toBe("Voici le plan pour passer à Postgres.");
    const report = "J'ai trouvé la cause du bug. Le cache gardait l'ancien jeton. J'ai corrigé le service.\n\nDétails...";
    expect(answerOpening(report)).toBe("J'ai trouvé la cause du bug. Le cache gardait l'ancien jeton.");
    expect(answerOpening("La version 2.4.7 est publiée. Tout est vert. Rien à faire.")).toBe(
      "La version 2.4.7 est publiée. Tout est vert.",
    );
    expect(answerOpening("```\ncode\n```\n- a\n- b")).toBeNull();
    expect(answerOpening("Mot ".repeat(200))!.length).toBeLessThanOrEqual(OPENING_MAX_CHARS + 3);
  });
});

describe("lastAssistantText", () => {
  it("is the last finished agent message, none after a user message", () => {
    const messages = [
      { role: "assistant", content: "ancien" },
      { role: "assistant", content: "trace", kind: "trace" },
      { role: "assistant", content: "dernier" },
    ];
    expect(lastAssistantText(messages)).toBe("dernier");
    expect(lastAssistantText([...messages, { role: "user", content: "?" }])).toBeNull();
  });
});

describe("offerAnswer", () => {
  it("hears yes and no in French, English and Arabic", () => {
    for (const yes of ["oui", "Oui vas-y", "résume-moi", "yes please", "نعم"]) expect(offerAnswer(yes)).toBe("yes");
    for (const no of ["non", "pas maintenant", "no thanks", "لا"]) expect(offerAnswer(no)).toBe("no");
    expect(offerAnswer("lance les tests")).toBeNull();
  });
});
