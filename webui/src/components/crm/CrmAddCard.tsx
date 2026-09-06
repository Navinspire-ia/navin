import { Plus } from "lucide-react";

import { cn } from "@/lib/utils";

type Props = {
  label: string;
  onClick: () => void;
  compact?: boolean;
};

export function CrmAddCard({ label, onClick, compact }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-border/80 bg-muted/10 px-4 text-center text-[13px] font-medium text-muted-foreground transition-[background-color,border-color,color,transform] duration-150 hover:border-violet-400/70 hover:bg-muted/30 hover:text-foreground active:scale-[0.96]",
        compact ? "min-h-[88px] py-4" : "min-h-[132px] py-6",
      )}
    >
      <span className="grid h-10 w-10 place-items-center rounded-full border border-dashed border-border/80">
        <Plus className="h-5 w-5" aria-hidden />
      </span>
      {label}
    </button>
  );
}
