import { describe, expect, it } from "vitest";

import {
  applyComposerTurnMode,
  composerModeAccent,
  isComposerTurnMode,
  isModeRoutingSlash,
  isSimpleStartNowPrompt,
  modeFromOutgoingContent,
  stripModeRoutingSlash,
} from "./ComposerModeMenu";

describe("stripModeRoutingSlash", () => {
  it("hides the routing slash and keeps the prompt text", () => {
    // Regression: "/ask cc" rendered verbatim in the user bubble because the
    // old path depended on the server slash-command list accepting args.
    expect(stripModeRoutingSlash("/ask cc")).toBe("cc");
    expect(stripModeRoutingSlash("/forge Add a login page")).toBe(
      "Add a login page",
    );
    expect(stripModeRoutingSlash("  /blueprint   plan the CRM  ")).toBe(
      "plan the CRM",
    );
  });

  it("keeps a bare routing command (pill rendering path)", () => {
    expect(stripModeRoutingSlash("/ask")).toBeNull();
    expect(stripModeRoutingSlash("/forge  ")).toBeNull();
  });

  it("leaves non-routing content untouched", () => {
    expect(stripModeRoutingSlash("/help me")).toBeNull();
    expect(stripModeRoutingSlash("plain text")).toBeNull();
  });
});

describe("applyComposerTurnMode", () => {
  it("prefixes agent-mode free text with /forge so build skills preload", () => {
    expect(applyComposerTurnMode("Add a login page", "agent")).toBe(
      "/forge Add a login page",
    );
  });

  it("prefixes ask-mode free text with /ask", () => {
    expect(applyComposerTurnMode("Explain AuthService", "ask")).toBe(
      "/ask Explain AuthService",
    );
  });

  it("prefixes free text with the workflow slash for each investigate mode", () => {
    expect(applyComposerTurnMode("Add a login page", "plan")).toBe(
      "/blueprint Add a login page",
    );
    expect(applyComposerTurnMode("Review auth", "review")).toBe(
      "/inspect Review auth",
    );
    expect(applyComposerTurnMode("Audit API", "security")).toBe(
      "/fortify Audit API",
    );
    expect(applyComposerTurnMode("Fix the crash", "debug")).toBe(
      "/debug Fix the crash",
    );
    expect(applyComposerTurnMode("Push my product", "montage")).toBe(
      "/montage Push my product",
    );
  });

  it("does not double-prefix an explicit slash command", () => {
    expect(applyComposerTurnMode("/forge ship it", "plan")).toBe(
      "/forge ship it",
    );
    expect(applyComposerTurnMode("/ask already", "agent")).toBe("/ask already");
    expect(applyComposerTurnMode("/blueprint already", "review")).toBe(
      "/blueprint already",
    );
    expect(applyComposerTurnMode("/inspect scoped", "security")).toBe(
      "/inspect scoped",
    );
  });

  it("trims whitespace", () => {
    expect(applyComposerTurnMode("  hello  ", "debug")).toBe("/debug hello");
  });

  it("starts a simple one-shot immediately instead of waiting for Build", () => {
    expect(
      applyComposerTurnMode("Pitch Navin en 10 slides", "plan"),
    ).toBe("/forge Pitch Navin en 10 slides");
    expect(
      applyComposerTurnMode("Génère un mémo d'une page", "plan"),
    ).toBe("/forge Génère un mémo d'une page");
    expect(
      applyComposerTurnMode("Écris un script qui liste les PDF", "plan"),
    ).toBe("/forge Écris un script qui liste les PDF");
    expect(
      applyComposerTurnMode("Corrige ça: typo dans le titre", "plan"),
    ).toBe("/forge Corrige ça: typo dans le titre");
    expect(
      applyComposerTurnMode("Pitch Navin en 10 slides", "agent", {
        startNow: true,
      }),
    ).toBe("/forge Pitch Navin en 10 slides");
  });

  it("keeps Plan plus Build for real projects and explicit plan asks", () => {
    expect(applyComposerTurnMode("Add a login page", "plan")).toBe(
      "/blueprint Add a login page",
    );
    expect(
      applyComposerTurnMode("Fais un plan pour un pitch de 10 slides", "plan"),
    ).toBe("/blueprint Fais un plan pour un pitch de 10 slides");
    expect(
      applyComposerTurnMode("Refactor the auth module", "plan"),
    ).toBe("/blueprint Refactor the auth module");
    expect(
      applyComposerTurnMode("/blueprint Pitch Navin en 10 slides", "plan"),
    ).toBe("/blueprint Pitch Navin en 10 slides");
  });
});

describe("isSimpleStartNowPrompt", () => {
  it("flags one-shot deliverables and tiny edits", () => {
    expect(isSimpleStartNowPrompt("Pitch Navin en 10 slides")).toBe(true);
    expect(isSimpleStartNowPrompt("Génère un PDF du rapport")).toBe(true);
    expect(isSimpleStartNowPrompt("Rename the helper file")).toBe(true);
    expect(isSimpleStartNowPrompt("Add a login page")).toBe(false);
    expect(isSimpleStartNowPrompt("Fais un plan pour le CRM")).toBe(false);
    expect(isSimpleStartNowPrompt("Migrate the billing stack")).toBe(false);
    expect(isSimpleStartNowPrompt("/blueprint déjà préfixé")).toBe(false);
  });
});

describe("modeFromOutgoingContent", () => {
  it("maps workflow slashes to composer modes", () => {
    expect(modeFromOutgoingContent("/ask Explain AuthService")).toBe("ask");
    expect(modeFromOutgoingContent("/forge Implement the plan")).toBe("agent");
    expect(modeFromOutgoingContent("/blueprint Design auth")).toBe("plan");
    expect(modeFromOutgoingContent("/inspect Review auth")).toBe("review");
    expect(modeFromOutgoingContent("/fortify Audit API")).toBe("security");
    expect(modeFromOutgoingContent("/debug Fix crash")).toBe("debug");
    expect(modeFromOutgoingContent("/probe OWASP")).toBe("security");
    expect(modeFromOutgoingContent("/xray sinks")).toBe("security");
    expect(modeFromOutgoingContent("/turbo perf")).toBe("review");
    expect(modeFromOutgoingContent("/montage Analyze project")).toBe("montage");
  });

  it("ignores free text and other commands", () => {
    expect(modeFromOutgoingContent("just chat")).toBeNull();
    expect(modeFromOutgoingContent("/help")).toBeNull();
  });
});

describe("isModeRoutingSlash", () => {
  it("recognizes composer-injected workflow slashes", () => {
    expect(isModeRoutingSlash("/ask")).toBe(true);
    expect(isModeRoutingSlash("/forge")).toBe(true);
    expect(isModeRoutingSlash("/blueprint")).toBe(true);
    expect(isModeRoutingSlash("/debug")).toBe(true);
    expect(isModeRoutingSlash("/FORGE")).toBe(true);
  });

  it("leaves ordinary commands alone so their pill still shows", () => {
    expect(isModeRoutingSlash("/help")).toBe(false);
    expect(isModeRoutingSlash("/model")).toBe(false);
  });
});

describe("composerModeAccent", () => {
  it("returns accents for ask and investigate modes", () => {
    expect(composerModeAccent("ask")).toBe("ask");
    expect(composerModeAccent("agent")).toBeNull();
    expect(composerModeAccent("plan")).toBe("plan");
    expect(composerModeAccent("review")).toBe("review");
    expect(composerModeAccent("security")).toBe("security");
    expect(composerModeAccent("debug")).toBe("debug");
    expect(composerModeAccent("montage")).toBe("montage");
  });
});

describe("isComposerTurnMode", () => {
  it("accepts known modes only", () => {
    expect(isComposerTurnMode("ask")).toBe(true);
    expect(isComposerTurnMode("review")).toBe(true);
    expect(isComposerTurnMode("agent")).toBe(true);
    expect(isComposerTurnMode("montage")).toBe(true);
    expect(isComposerTurnMode("explore")).toBe(false);
    expect(isComposerTurnMode("")).toBe(false);
    expect(isComposerTurnMode(null)).toBe(false);
  });
});
