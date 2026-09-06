/** One number the desk can put in the hero: money first, then count. */

export function formatTenderMoney(value: number, currency = "EUR"): string {
  const code = currency.trim() || "EUR";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} M ${code}`;
  if (value >= 1_000) return `${Math.round(value / 1_000)} k ${code}`;
  return `${Math.round(value).toLocaleString("fr-FR")} ${code}`;
}

export type MoneyHeroKind = "empty" | "screened" | "play-money" | "play-count";

export function moneyHeroKind(input: {
  collected: number;
  inPlay: number;
  weighted: number;
}): MoneyHeroKind {
  if (input.collected <= 0) return "empty";
  if (input.inPlay <= 0) return "screened";
  if (input.weighted > 0) return "play-money";
  return "play-count";
}
