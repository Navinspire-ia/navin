// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { stripHtml } from "@/lib/plain-text";
import {
  detectNoticeLanguage,
  detectWorkMode,
  factDisplay,
  parseTenderFacts,
  pickDuration,
} from "@/lib/tender-facts";
import type { TenderNotice } from "@/lib/tenders-api";

const tx = (key: string, fallback: string) => (key === "unknown" ? "non renseigne" : fallback);

function notice(partial: Partial<TenderNotice> = {}): TenderNotice {
  return {
    id: "tn-1",
    source_id: "ted",
    country: "FR",
    title: "Modernisation de la plateforme decisionnelle",
    stage: "matched",
    ...partial,
  };
}

describe("tender facts parser", () => {
  it("strips leftover HTML tags from the description", () => {
    const facts = parseTenderFacts(
      notice({
        description: "<p>Are you a talented team looking for a remote...</p>",
      }),
    );
    expect(facts.description).toContain("Are you a talented team looking for a remote");
    expect(facts.description).not.toContain("<p>");
    expect(facts.description).not.toContain("</p>");
  });

  it("never invents budget, deadline or city", () => {
    const facts = parseTenderFacts(notice({ description: "Prestataire pour une refonte." }));
    const byKey = Object.fromEntries(facts.card.map((row) => [row.key, row]));
    expect(byKey.budget.value).toBe("");
    expect(byKey.deadline.value).toBe("");
    expect(factDisplay(byKey.budget, tx)).toBe("non renseigne");
    expect(factDisplay(byKey.deadline, tx)).toBe("non renseigne");
    expect(byKey.place.value).toBe("FR");
  });

  it("reads structured fields and labeled duration from the notice text", () => {
    const facts = parseTenderFacts(
      notice({
        status: "open",
        buyer: "DINUM",
        budget: 120000,
        currency: "EUR",
        deadline: "2026-09-15",
        city: "Paris",
        description: "Duree de mission : 12 mois. Depot sur PLACE.",
      }),
      { locale: "en-GB", sourceNames: { ted: "TED" } },
    );
    const byKey = Object.fromEntries(facts.card.map((row) => [row.key, row]));
    expect(byKey.status.valueKey).toBe("noticeStatus.open");
    expect(byKey.buyer.value).toBe("DINUM");
    expect(byKey.budget.value).toContain("EUR");
    expect(byKey.deadline.value).toMatch(/2026/);
    expect(byKey.place.value).toBe("Paris, FR");
    expect(byKey.duration.value.toLowerCase()).toContain("12 mois");
    expect(byKey.source.value).toBe("TED");
    expect(byKey.language.valueKey).toBe("langFr");
  });

  it("detects remote only when the notice states it", () => {
    expect(detectWorkMode("Mission en teletravail complet")).toBe("remote");
    expect(detectWorkMode("Fourniture de serveurs pour l'acheteur")).toBe("");
    const facts = parseTenderFacts(notice({ description: "Prestation sur site a Lyon." }));
    expect(facts.extra.some((row) => row.key === "remote" && row.value === "onsite")).toBe(true);
  });

  it("does not guess English or French from a thin blob", () => {
    expect(detectNoticeLanguage(notice({ title: "Lot 3", description: "x" }))).toBe("");
  });

  it("picks duration from English notices without inventing months", () => {
    expect(pickDuration("The contract duration is 18 months for this rebuild.")).toMatch(/18 months/i);
    expect(pickDuration("Please submit the tender on the portal.")).toBe("");
  });

  it("strips HTML entities and em dash leftovers", () => {
    expect(stripHtml("A &amp; B <strong>win</strong>")).toBe("A & B win");
    expect(stripHtml("from 12\u201318 months")).toBe("from 12-18 months");
  });
});
