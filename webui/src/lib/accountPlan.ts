type Translate = (key: string, options?: Record<string, unknown>) => string;

export function accountPlanSlug(
  account?: { plan?: string; plan_label?: string } | null,
): string {
  return (account?.plan || "").trim().toLowerCase();
}

export function localizedPlanName(
  t: Translate,
  account?: { plan?: string; plan_label?: string } | null,
): string {
  const slug = accountPlanSlug(account);
  const fallback = account?.plan_label || account?.plan || "";
  if (!slug) return fallback;
  return t(`sidebar.account.plans.${slug}`, { defaultValue: fallback || slug });
}

export function localizedPlanLine(
  t: Translate,
  account?: { plan?: string; plan_label?: string } | null,
): string {
  const slug = accountPlanSlug(account);
  const name = localizedPlanName(t, account);
  if (!name) return "";
  if (slug === "free") {
    return t("sidebar.account.planFree", { defaultValue: "Free plan" });
  }
  return t("sidebar.account.plan", { plan: name, defaultValue: "{{plan}} plan" });
}

/** Compact chip: Plus  97%  $20/month. Never spent / budget dollars. */
export function compactAccountLine(
  t: Translate,
  account?: {
    plan?: string;
    plan_label?: string;
    plan_price_usd?: number | null;
    usage?: { used_percent?: number | null } | null;
  } | null,
): string {
  const name = localizedPlanName(t, account);
  if (!name) return "";
  const bits = [name];
  const percent = account?.usage?.used_percent;
  if (percent != null && Number.isFinite(percent)) {
    bits.push(`${Math.min(100, Math.max(0, Math.round(percent)))}%`);
  }
  const price = account?.plan_price_usd;
  if (typeof price === "number" && price > 0) {
    bits.push(`$${Number.isInteger(price) ? price : price.toFixed(2)}/month`);
  }
  return bits.join("  ");
}
