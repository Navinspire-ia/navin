import { Button } from "@/components/ui/button";
import { pageRange, visiblePages } from "@/lib/crm-list";
import { cn } from "@/lib/utils";

type Props = {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  tx: (key: string, fallback: string) => string;
  className?: string;
};

export function CrmPagination({ page, pageSize, total, onPage, tx, className }: Props) {
  if (total === 0) return null;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const { from, to } = pageRange(page, pageSize, total);
  const pages = visiblePages(page, totalPages);
  const showButtons = total > pageSize;

  return (
    <div className={cn("flex shrink-0 flex-wrap items-center justify-between gap-2 pt-2", className)}>
      <p className="text-[11px] tabular-nums text-muted-foreground">
        {tx("crm.pageRange", "{{from}}-{{to}} sur {{total}} · {{size}} par page")
          .replace("{{from}}", String(from))
          .replace("{{to}}", String(to))
          .replace("{{total}}", String(total))
          .replace("{{size}}", String(pageSize))}
      </p>
      {showButtons ? (
      <div className="flex flex-wrap items-center gap-1">
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 cursor-pointer text-[11px]"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          {tx("crm.prev", "Precedent")}
        </Button>
        {pages.map((item, index) =>
          item === "ellipsis" ? (
            <span key={`e-${index}`} className="px-1 text-[11px] text-muted-foreground">
              ...
            </span>
          ) : (
            <Button
              key={item}
              type="button"
              size="sm"
              variant={item === page ? "default" : "outline"}
              className="h-8 min-w-8 cursor-pointer px-2 text-[11px]"
              onClick={() => onPage(item)}
            >
              {item}
            </Button>
          ),
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 cursor-pointer text-[11px]"
          disabled={page >= totalPages}
          onClick={() => onPage(page + 1)}
        >
          {tx("crm.next", "Suivant")}
        </Button>
      </div>
      ) : null}
    </div>
  );
}
