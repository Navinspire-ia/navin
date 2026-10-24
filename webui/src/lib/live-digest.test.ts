import { describe, expect, it } from "vitest";

import { lastAssistantText, liveDigest, offerAnswer } from "./live-digest";

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
