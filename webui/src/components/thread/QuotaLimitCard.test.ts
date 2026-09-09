// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  isProviderCreditMessage,
  isQuotaLimitMessage,
  parseProviderCreditMessage,
  quotaBubbleKind,
  quotaCardVariant,
  refusalOutdatedForSelection,
} from "./QuotaLimitCard";
import type { AccountPayload } from "@/lib/api";

type AccountLike = Pick<AccountPayload, "usage">;

const withBudget = (usedPercent: number): AccountLike => ({
  usage: {
    budget_micro_usd: 17_000_000,
    spent_micro_usd: Math.round(17_000_000 * (usedPercent / 100)),
    used_percent: usedPercent,
    remaining_equivalent_tokens: 1_000,
    mode: "normal",
  },
});

describe("quotaCardVariant", () => {
  it("waits while the account is still loading", () => {
    expect(quotaCardVariant(null, true)).toBe("checking");
    expect(quotaCardVariant(withBudget(10), true)).toBe("checking");
  });

  it("treats leftover budget as a key desync, not a healthy leftover bubble", () => {
    expect(quotaCardVariant(withBudget(19), false)).toBe("desync");
    expect(quotaCardVariant(withBudget(84), false)).toBe("desync");
  });

  it("reports the limit once the budget is nearly spent", () => {
    expect(quotaCardVariant(withBudget(85), false)).toBe("limit");
    expect(quotaCardVariant(withBudget(100), false)).toBe("limit");
  });

  it("never claims a healthy budget on a plan that includes none", () => {
    // Free plans ship no usage payload, so the old "0 % used" reading told
    // the user their budget was fine right after a call had been refused.
    expect(quotaCardVariant({ usage: null }, false)).toBe("no-budget");
    expect(quotaCardVariant({}, false)).toBe("no-budget");
    expect(
      quotaCardVariant(
        {
          usage: {
            budget_micro_usd: 0,
            spent_micro_usd: 0,
            used_percent: 0,
            remaining_equivalent_tokens: 0,
            mode: "normal",
          },
        },
        false,
      ),
    ).toBe("no-budget");
  });
});

describe("quotaBubbleKind", () => {
  it("keeps a managed plan sentinel as a plan limit", () => {
    expect(
      quotaBubbleKind("__NAVIN_QUOTA_LIMIT__\nYour plan's monthly model quota is used up."),
    ).toBe("plan");
    expect(isQuotaLimitMessage("__NAVIN_QUOTA_LIMIT__\nmonthly model quota is used up")).toBe(
      true,
    );
  });

  it("classifies BYOK credit as provider, even on an old mixed bubble", () => {
    expect(
      quotaBubbleKind(
        "__NAVIN_QUOTA_LIMIT__\nThe AI provider rejected the request because the API key is out of quota or the account is in arrears.",
      ),
    ).toBe("provider");
    expect(
      isProviderCreditMessage(
        "__NAVIN_PROVIDER_CREDIT__\nYour own API key is out of credit. Top up at the provider, or switch to a Navin plan model.",
      ),
    ).toBe(true);
    expect(isQuotaLimitMessage("Error: the API key is out of quota")).toBe(false);
  });

  it("leaves an unrelated error alone", () => {
    expect(quotaBubbleKind("Could not reach the model provider.")).toBeNull();
    expect(quotaBubbleKind("   ")).toBeNull();
  });

  it("still recognises the new provider bubble shape", () => {
    expect(
      quotaBubbleKind(
        "__NAVIN_PROVIDER_CREDIT__\nZ.AI refused the call for glm-4.5: the key is out of credit.\nProvider message: Insufficient balance.\nThis model runs on your own API key. Top up at the provider, or pick another model.",
      ),
    ).toBe("provider");
  });
});

describe("parseProviderCreditMessage", () => {
  it("reads provider, model and the provider's own words", () => {
    expect(
      parseProviderCreditMessage(
        "__NAVIN_PROVIDER_CREDIT__\n"
          + "Z.AI refused the call for glm-4.5: the key is out of credit.\n"
          + "Provider message: Insufficient balance or no resource package. Please recharge.\n"
          + "This model runs on your own API key. Top up at the provider, or pick another model.",
      ),
    ).toEqual({
      provider: "Z.AI",
      model: "glm-4.5",
      detail: "Insufficient balance or no resource package. Please recharge.",
    });
  });

  it("keeps an unnamed provider and a missing model as null", () => {
    expect(
      parseProviderCreditMessage(
        "__NAVIN_PROVIDER_CREDIT__\n"
          + "The provider refused the call: the key is out of credit.\n"
          + "This model runs on your own API key. Top up at the provider, or pick another model.",
      ),
    ).toEqual({ provider: null, model: null, detail: null });
  });

  it("yields nothing for a legacy bubble, so the generic copy shows", () => {
    expect(
      parseProviderCreditMessage(
        "__NAVIN_PROVIDER_CREDIT__\nYour own API key is out of credit. Top up at the provider, or switch to a Navin plan model.",
      ),
    ).toEqual({ provider: null, model: null, detail: null });
    expect(parseProviderCreditMessage("")).toEqual({ provider: null, model: null, detail: null });
  });
});

describe("refusalOutdatedForSelection", () => {
  const PLAN = "__NAVIN_QUOTA_LIMIT__\nYour plan's monthly model quota is used up.";
  const ZAI =
    "__NAVIN_PROVIDER_CREDIT__\n"
    + "Z.AI refused the call for glm-4.5: the key is out of credit.\n"
    + "Provider message: Insufficient balance.\n"
    + "This model runs on your own API key. Top up at the provider, or pick another model.";
  const LEGACY =
    "__NAVIN_PROVIDER_CREDIT__\nYour own API key is out of credit. Top up at the provider, or switch to a Navin plan model.";

  it("keeps the plan card while a Navin model is still selected", () => {
    expect(
      refusalOutdatedForSelection(PLAN, { provider: "navin", providerLabel: "Navin" }),
    ).toBe(false);
    expect(refusalOutdatedForSelection(PLAN, { provider: null, providerLabel: null })).toBe(false);
    expect(refusalOutdatedForSelection(PLAN, null)).toBe(false);
  });

  it("retires the plan card once a BYOK or local model is picked", () => {
    expect(
      refusalOutdatedForSelection(PLAN, { provider: "zai", providerLabel: "Z.AI" }),
    ).toBe(true);
    expect(
      refusalOutdatedForSelection(PLAN, { provider: "ollama", providerLabel: "Ollama" }),
    ).toBe(true);
  });

  it("retires the provider card only when another provider is selected", () => {
    expect(
      refusalOutdatedForSelection(ZAI, { provider: "zai", providerLabel: "Z.AI" }),
    ).toBe(false);
    expect(
      refusalOutdatedForSelection(ZAI, { provider: "zai", providerLabel: "z.ai" }),
    ).toBe(false);
    expect(
      refusalOutdatedForSelection(ZAI, { provider: "navin", providerLabel: "Navin" }),
    ).toBe(true);
    expect(
      refusalOutdatedForSelection(ZAI, { provider: "anthropic", providerLabel: "Anthropic" }),
    ).toBe(true);
  });

  it("never guesses for a bubble that does not name its provider", () => {
    expect(
      refusalOutdatedForSelection(LEGACY, { provider: "navin", providerLabel: "Navin" }),
    ).toBe(false);
    expect(
      refusalOutdatedForSelection(ZAI, { provider: "navin", providerLabel: null }),
    ).toBe(false);
  });

  it("ignores ordinary assistant replies", () => {
    expect(
      refusalOutdatedForSelection("Here is the summary.", { provider: "zai", providerLabel: "Z.AI" }),
    ).toBe(false);
  });
});
