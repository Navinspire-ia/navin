import { Eye } from "lucide-react";
import { useTranslation } from "react-i18next";

/** Banner shown when the local org role is viewer (observe-only). */
export function ViewerReadOnlyBanner() {
  const { t } = useTranslation();
  return (
    <div
      className="flex items-center gap-2 border-b border-amber-500/25 bg-amber-500/10 px-3 py-1.5 text-[12px] text-amber-800 dark:text-amber-200"
      role="status"
    >
      <Eye className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span>
        {t("thread.presence.viewerBanner", {
          defaultValue:
            "You are viewing as a read-only teammate. Tool runs and approvals are disabled.",
        })}
      </span>
    </div>
  );
}
