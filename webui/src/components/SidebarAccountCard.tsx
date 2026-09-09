import { useTranslation } from "react-i18next";
import { CircleUserRound } from "lucide-react";

import { useAccount } from "@/hooks/useAccount";
import { compactAccountLine, localizedPlanLine, localizedPlanName } from "@/lib/accountPlan";

/**
 * Cursor-style account chip: initial, name, plan line. Settings and the
 * other footer icons sit on the same row, so this chip stays compact.
 */
export function SidebarAccountCard({
  collapsed,
  onClick,
}: {
  collapsed: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  const { account } = useAccount();

  const connected = account?.connected ?? false;
  const displayName = account?.name || account?.email || "";
  const initial = displayName.trim().charAt(0).toUpperCase();
  const planLine =
    compactAccountLine(t, account)
    || localizedPlanLine(t, account)
    || localizedPlanName(t, account);

  const label = connected
    ? displayName
    : t("sidebar.account.signIn", { defaultValue: "Sign in" });

  if (collapsed) {
    return (
      <button
        type="button"
        aria-label={label}
        title={label}
        onClick={onClick}
        className="mx-auto flex h-8 w-8 items-center justify-center rounded-md text-sidebar-foreground/80 transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
      >
        {connected && initial ? (
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/15 text-[11px] font-semibold text-primary">
            {initial}
          </span>
        ) : (
          <CircleUserRound className="h-4 w-4" strokeWidth={1.75} />
        )}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex h-9 w-full min-w-0 items-center gap-2 rounded-md px-1.5 text-left transition-colors hover:bg-sidebar-accent/60"
    >
      {connected && initial ? (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[11px] font-semibold text-primary">
          {initial}
        </span>
      ) : (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted/60 text-muted-foreground">
          <CircleUserRound className="h-4 w-4" strokeWidth={1.75} />
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] leading-4 text-sidebar-foreground/90">
          {label}
        </span>
        <span className="block truncate text-[11px] leading-4 text-muted-foreground/75">
          {connected
            ? planLine || t("sidebar.account.connected", { defaultValue: "Connected" })
            : t("sidebar.account.signInHint", {
                defaultValue: "Sync your navin.live plan",
              })}
        </span>
      </span>
    </button>
  );
}
