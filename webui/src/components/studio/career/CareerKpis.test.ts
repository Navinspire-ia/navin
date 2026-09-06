import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CareerMoneyBar } from "@/components/studio/career/CareerKpis";

const FACTS = [
  { id: "above", label: "Offers at your rate", value: "12/30" },
  { id: "strong", label: "Strong matches", value: "8" },
  { id: "inflight", label: "In progress", value: "3" },
  { id: "replies", label: "Replies", value: "1" },
];

describe("career money bar", () => {
  it("leads with the monthly amount and its basis", () => {
    const html = renderToStaticMarkup(
      createElement(CareerMoneyBar, {
        amount: "11 700 EUR",
        basis: "650 EUR / day x 18 days",
        facts: FACTS,
      }),
    );
    expect(html).toContain('data-testid="career-money-bar"');
    expect(html).toContain("11 700 EUR");
    expect(html).toContain("650 EUR / day x 18 days");
    expect(html).toContain('data-testid="career-money-above"');
    expect(html).toContain("12/30");
    expect(html).not.toContain("Your rate, per month");
    expect(html).not.toContain("Your salary, per month");
  });

  it("does not invent a monthly number or a salary heading when no rate is set", () => {
    const html = renderToStaticMarkup(
      createElement(CareerMoneyBar, {
        amount: "",
        basis: "650 EUR / day",
        facts: FACTS,
      }),
    );
    expect(html).not.toContain("Your salary, per month");
    expect(html).not.toContain("Your rate, per month");
    expect(html).not.toContain("Set your rate in the profile");
    expect(html).not.toContain('data-testid="career-money-amount"');
    expect(html).not.toContain("650 EUR / day");
  });

  it("lets the user type a rate when none is stored", () => {
    const html = renderToStaticMarkup(
      createElement(CareerMoneyBar, {
        amount: "",
        facts: FACTS,
        goalField: {
          label: "Your daily rate",
          value: "650",
          suffix: "EUR / day",
          saveLabel: "Save rate",
          onChange: () => undefined,
          onSave: () => undefined,
        },
      }),
    );
    expect(html).toContain('data-testid="career-money-goal"');
    expect(html).toContain("Save rate");
    expect(html).toContain("650");
    expect(html).not.toContain("Your salary, per month");
    expect(html).not.toContain("Set your rate in the profile");
  });
});
