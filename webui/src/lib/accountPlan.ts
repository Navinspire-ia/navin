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
