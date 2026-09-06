import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MarketingKpiGrid, buildMarketingKpis } from "@/components/studio/marketing/MarketingKpis";

describe("buildMarketingKpis", () => {
  it("renders the four growth counters", () => {
    const items = buildMarketingKpis({
      signups: 42,
      content: 12,
      winners: 2,
      campaigns: 1,
      labels: { signups: "Signups", content: "Content", winners: "Winners", campaigns: "Campaigns" },
    });
    expect(items.map((item) => item.id)).toEqual(["signups", "content", "winners", "campaigns"]);
    const html = renderToStaticMarkup(createElement(MarketingKpiGrid, { items }));
    expect(html).toContain("42");
    expect(html).toContain('data-testid="marketing-kpi-winners"');
  });

  it("adds the publishing row once the desk has posting history", () => {
    const items = buildMarketingKpis({
      signups: 1200,
      content: 12,
      winners: 2,
      campaigns: 1,
      published: 7,
      scheduled: 3,
      views: 15_400,
      clicks: 980,
      labels: {
        signups: "Signups",
        content: "Content",
        winners: "Winners",
        campaigns: "Campaigns",
        published: "Published",
        scheduled: "Scheduled",
        views: "Views",
        clicks: "Clicks",
      },
    });
    expect(items.map((item) => item.id)).toEqual(["signups", "content", "winners", "campaigns", "published", "scheduled", "views", "clicks"]);
    expect(items.find((item) => item.id === "signups")?.value).toBe("1.2k");
    expect(items.find((item) => item.id === "views")?.value).toBe("15k");
    expect(items.find((item) => item.id === "published")?.value).toBe("7");
    const html = renderToStaticMarkup(createElement(MarketingKpiGrid, { items }));
    expect(html).toContain('data-testid="marketing-kpi-published"');
  });
});
