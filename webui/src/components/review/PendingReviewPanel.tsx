// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Callout, DirectionalHint } from "@fluentui/react";
import { Check, ChevronRight, CircleCheck, Eye, EyeOff, FileDiff, FlaskConical, Loader2, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { FileTypeIcon } from "@/components/dev/FileTypeIcon";
import { reviewLineStats } from "@/components/dev/devWorkbenchUtils";
import { DiffPair } from "@/components/thread/activity/DiffPair";
import type { ProveChange } from "@/hooks/useProveChange";
import type { ReviewChangeEntry } from "@/lib/types";
import { SHOW_PENDING_REVIEW_EVENT } from "@/lib/workbench-events";
import { cn } from "@/lib/utils";

export type ReviewAction = (action: "accept" | "reject", path?: string) => void;

function baseName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** Cursor-style path: `file.ts` or `.../folder/file.ts`. */
function listPath(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  if (parts.length <= 1) return path;
  if (parts.length === 2) return parts.join("/");
  return `.../${parts.slice(-2).join("/")}`;
}

/**
 * Pending edits and decisions, with a header mode beside the session plan.
 * The header opens files in a callout without pushing the chat down.
 */
const ACTION_CLASS =
  "shrink-0 px-1 py-0.5 text-[11.5px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50";
/** In the thread header the actions read at the size of the journal sections. */
const HEADER_ACTION_CLASS = "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[13px] hover:bg-foreground/[0.04]";

export function PendingReviewPanel({
  changes,
  busy = false,
  error = null,
  activePath = null,
  defaultExpanded = false,
  onAction,
  onOpenFile,
  className,
  compact = false,
  tucked = false,
  header = false,
  prove,
  proveHref = "#/code?panel=evolve",
}: {
  changes: ReviewChangeEntry[];
  busy?: boolean;
  error?: string | null;
  activePath?: string | null;
  defaultExpanded?: boolean;
  onAction: ReviewAction;
  onOpenFile?: (path: string) => void;
  className?: string;
  compact?: boolean;
  /** Sit behind the composer: the chat box covers the bottom edge. */
  tucked?: boolean;
  header?: boolean;
  /** Evolve "Prove this change": prove the pending edits under load. */
  prove?: ProveChange;
  /** Code Evolve tab for this chat. Must keep `?chat=` or the session is dropped. */
  proveHref?: string;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [hidden, setHidden] = useState(false);
  const target = useRef<HTMLButtonElement>(null);
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const lineStats = useMemo(() => reviewLineStats(changes), [changes]);

  useEffect(() => {
    if (changes.length === 0) setHidden(false);
  }, [changes.length]);

  useEffect(() => {
    const onShow = () => {
      setHidden(false);
      setExpanded(true);
    };
    globalThis.addEventListener(SHOW_PENDING_REVIEW_EVENT, onShow);
    return () => globalThis.removeEventListener(SHOW_PENDING_REVIEW_EVENT, onShow);
  }, []);

  if (!changes.length) return null;

  const acceptAllLabel = tx("dev.review.acceptAll", "Accept all");
  const rejectAllLabel = tx("dev.review.rejectAll", "Reject all");
  const hideLabel = tx("dev.review.hide", "Hide");
  const showLabel = tx("dev.review.show", "Show files");
  const filesLabel = t("dev.review.pending", {
    defaultValue: "{{count}} files",
    count: changes.length,
  });
  const shellClass = cn(
    header ? "pending-review-header min-w-0 flex-1" : "w-full",
    header ? "" : tucked
      ? "relative z-0 -mb-3 rounded-t-[22px] border border-b-0 border-white/25 bg-transparent pb-3 dark:border-white/20"
      : "rounded-lg border border-black/[0.08] dark:border-white/[0.12]",
    className,
  );
  const fileList = (
    <ul className="max-h-[18rem] overflow-y-auto p-2">
      {changes.map((change) => (
        <ReviewRow
          key={change.path}
          change={change}
          active={activePath === change.path}
          busy={busy}
          onAction={onAction}
          onOpenFile={onOpenFile ? (path) => { onOpenFile(path); if (header) setExpanded(false); } : undefined}
          acceptLabel={tx("dev.review.acceptFile", "Accept this file")}
          rejectLabel={tx("dev.review.rejectFile", "Reject this file")}
        />
      ))}
    </ul>
  );

  if (hidden) {
    return (
      <div
        data-testid="pending-review-panel"
        data-hidden="true"
        className={shellClass}
      >
        <div className={cn("flex items-center", header ? "h-10" : "h-7 px-3")}>
          <button
            type="button"
            onClick={() => setHidden(false)}
            data-testid="pending-review-show"
            title={showLabel}
            aria-label={showLabel}
            className="flex min-w-0 items-center gap-1.5 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
          >
            <Eye className="h-3 w-3 shrink-0" aria-hidden />
            <span className="truncate">{filesLabel}</span>
            {lineStats.added + lineStats.deleted > 0 ? (
              <DiffPair
                added={lineStats.added}
                deleted={lineStats.deleted}
                hideZero
                className="text-[12px] font-medium"
              />
            ) : null}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid="pending-review-panel"
      className={shellClass}
    >
      <div className={cn("flex items-center", header ? "pending-review-toolbar h-10 gap-1" : "h-7 gap-2 px-3")}>
        <button
          ref={target}
          type="button"
          onClick={() => setExpanded((open) => !open)}
          aria-expanded={expanded}
          title={filesLabel}
          data-testid="pending-review-toggle"
          className={cn(
            "flex min-w-0 flex-1 items-center text-start",
            header
              ? "min-w-[2.75rem] gap-2 overflow-hidden rounded-md px-1.5 py-1 text-[14px] font-semibold transition-colors hover:bg-foreground/[0.04]"
              : "gap-1.5 text-[12px] font-medium",
          )}
        >
          {header ? (
            <FileDiff aria-hidden className="h-[18px] w-[18px] shrink-0 text-muted-foreground" strokeWidth={2} />
          ) : (
            <ChevronRight
              aria-hidden
              className={cn(
                "rtl:[&:not(.rotate-90)]:rotate-180 h-3 w-3 shrink-0 text-muted-foreground/65 transition-transform duration-150",
                expanded && "rotate-90",
              )}
            />
          )}
          <span className="min-w-0 truncate text-foreground/90">
            {filesLabel}
          </span>
          {lineStats.added + lineStats.deleted > 0 ? (
            <DiffPair
              added={lineStats.added}
              deleted={lineStats.deleted}
              hideZero
              className={header ? "pending-review-diff shrink-0 text-[13.5px] font-normal tabular-nums" : "text-[12px] font-medium"}
            />
          ) : null}
          {header ? (
            <ChevronRight
              aria-hidden
              className={cn(
                "rtl:[&:not(.rotate-90)]:rotate-180 h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200",
                expanded && "rotate-90",
              )}
            />
          ) : null}
        </button>
        {error ? (
          <span className="max-w-[10rem] shrink-0 truncate text-[11px] text-destructive">
            {error}
          </span>
        ) : null}
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" aria-hidden />
        ) : null}
        {prove?.available ? (
          <ProveButton prove={prove} compact={compact} header={header} tx={tx} href={proveHref} />
        ) : null}
        <button
          type="button"
          disabled={busy}
          onClick={() => onAction("reject")}
          data-testid="pending-review-reject-all"
          aria-label={rejectAllLabel}
          title={rejectAllLabel}
          className={cn(ACTION_CLASS, header && HEADER_ACTION_CLASS)}
        >
          {compact || header ? <X className={cn(header ? "h-3.5 w-3.5" : "h-3 w-3", !compact && "pending-review-icon")} aria-hidden /> : null}
          {!compact ? <span className="pending-review-label">{rejectAllLabel}</span> : null}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onAction("accept")}
          data-testid="pending-review-accept-all"
          aria-label={acceptAllLabel}
          title={acceptAllLabel}
          className={cn(ACTION_CLASS, header && HEADER_ACTION_CLASS)}
        >
          {compact || header ? <Check className={cn(header ? "h-3.5 w-3.5" : "h-3 w-3", !compact && "pending-review-icon")} aria-hidden /> : null}
          {!compact ? <span className="pending-review-label">{acceptAllLabel}</span> : null}
        </button>
        <button
          type="button"
          onClick={() => setHidden(true)}
          data-testid="pending-review-hide"
          title={hideLabel}
          aria-label={hideLabel}
          className={cn(ACTION_CLASS, header && HEADER_ACTION_CLASS)}
        >
          {compact || header ? <EyeOff className={cn(header ? "h-3.5 w-3.5" : "h-3 w-3", !compact && "pending-review-icon")} aria-hidden /> : null}
          {!compact ? <span className="pending-review-label">{hideLabel}</span> : null}
        </button>
      </div>

      {header && expanded ? (
        <Callout target={target} onDismiss={() => setExpanded(false)}
          directionalHint={DirectionalHint.bottomRightEdge} gapSpace={6}
          isBeakVisible={false} setInitialFocus role="dialog" ariaLabel={filesLabel}
          styles={{ calloutMain: { width: "min(440px, calc(100vw - 24px))", borderRadius: 12,
            background: "hsl(var(--popover))", color: "hsl(var(--foreground))" } }}>
          <motion.div initial={false} animate={{ opacity: 1 }} transition={{ duration: reduceMotion ? 0 : 0.15 }}>
            {fileList}
          </motion.div>
        </Callout>
      ) : null}
      {!header ? <AnimatePresence initial={false}>
        {expanded ? (
          <motion.div
            key="list"
            initial={reduceMotion ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ type: "spring", duration: 0.28, bounce: 0 }}
            className="overflow-hidden"
          >
            {fileList}
          </motion.div>
        ) : null}
      </AnimatePresence> : null}
    </div>
  );
}

/**
 * Launch an Evolve robustness proof of the pending changes, then hand the
 * user over to the Evolve tab where the run streams. Errors reset on click.
 */
function ProveButton({
  prove,
  compact,
  header = false,
  tx,
  href,
}: {
  prove: ProveChange;
  compact: boolean;
  header?: boolean;
  tx: (key: string, fallback: string) => string;
  href: string;
}) {
  const { t } = useTranslation();
  const label = tx("dev.review.prove", "Prove this change");

  if (prove.state.phase === "done") {
    const { proof, detail } = prove.state;
    const score = proof ? Math.round(proof.robustness_score) : null;
    const label = !proof
      ? detail || tx("dev.review.proveFinished", "Proof finished")
      : proof.verdict === "pass"
        ? t("dev.review.provePassed", { defaultValue: "Proof passed · {{score}}/100", score })
        : proof.verdict === "weak"
          ? t("dev.review.proveWeak", { defaultValue: "Proof weak · {{score}}/100", score })
          : t("dev.review.proveProblems", { defaultValue: "Proof found problems · {{score}}/100", score });
    const tone = !proof
      ? "text-muted-foreground hover:text-foreground"
      : proof.verdict === "pass"
        ? "text-emerald-600 hover:text-emerald-500 dark:text-emerald-400"
        : proof.verdict === "weak"
          ? "text-amber-600 hover:text-amber-500 dark:text-amber-400"
          : "text-[hsl(var(--tone-error))] hover:opacity-80";
    return (
      <a
        href={href}
        onClick={() => prove.reset()}
        data-testid="pending-review-prove-result"
        data-verdict={proof?.verdict ?? "none"}
        className={cn(
          "flex shrink-0 items-center gap-1 font-medium transition-colors",
          tone,
          header ? "rounded-md px-1.5 py-1 text-[13px] hover:bg-foreground/[0.04]" : "px-1 py-0.5 text-[11.5px]",
        )}
        title={tx("dev.review.proveOpenResults", "Open the results in Evolve")}
        aria-label={label}
      >
        {proof?.verdict === "pass"
          ? <CircleCheck className={header ? "h-3.5 w-3.5" : "h-3 w-3"} aria-hidden />
          : <FlaskConical className={header ? "h-3.5 w-3.5" : "h-3 w-3"} aria-hidden />}
        <span className="pending-review-label">{label}</span>
      </a>
    );
  }

  if (prove.state.phase === "started") {
    return (
      <a
        href={href}
        data-testid="pending-review-prove-open"
        className={cn(
          "flex shrink-0 items-center gap-1 text-emerald-600 transition-colors hover:text-emerald-500 dark:text-emerald-400",
          header ? "rounded-md px-1.5 py-1 text-[13px] hover:bg-foreground/[0.04]" : "px-1 py-0.5 text-[11.5px]",
        )}
        title={tx("dev.review.proveOpen", "Open Evolve")}
        aria-label={tx("dev.review.proveOpen", "Open Evolve")}
      >
        <FlaskConical className={header ? "h-3.5 w-3.5" : "h-3 w-3"} aria-hidden />
        <span className="pending-review-label">{prove.state.job !== null
          ? t("dev.review.proveStarted", {
              defaultValue: "Proof #{{job}} running",
              job: prove.state.job,
            })
          : tx("dev.review.proveStartedNoId", "Proof running")}</span>
      </a>
    );
  }

  const starting = prove.state.phase === "starting";
  const failed = prove.state.phase === "error";
  return (
    <button
      type="button"
      disabled={starting}
      onClick={() => {
        if (failed) prove.reset();
        prove.start();
      }}
      data-testid="pending-review-prove"
      aria-label={failed ? tx("dev.review.proveRetry", "Proof failed - retry") : label}
      title={
        failed && prove.state.phase === "error"
          ? prove.state.message
          : tx(
              "dev.review.proveHint",
              "Evolve proves the pending change under load in an isolated shadow before you accept it.",
            )
      }
      className={cn(
        "flex shrink-0 items-center gap-1 transition-colors disabled:opacity-60",
        header ? "rounded-md px-1.5 py-1 text-[13px] hover:bg-foreground/[0.04]" : "px-1 py-0.5 text-[11.5px]",
        failed
          ? "text-destructive hover:text-destructive/80"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {starting ? (
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
      ) : (
        <FlaskConical className={header ? "h-3.5 w-3.5" : "h-3 w-3"} aria-hidden />
      )}
      {compact ? null : <span className="pending-review-label">{failed ? tx("dev.review.proveRetry", "Proof failed - retry") : label}</span>}
    </button>
  );
}

function ReviewRow({
  change,
  active,
  busy,
  onAction,
  onOpenFile,
  acceptLabel,
  rejectLabel,
}: {
  change: ReviewChangeEntry;
  active: boolean;
  busy: boolean;
  onAction: ReviewAction;
  onOpenFile?: (path: string) => void;
  acceptLabel: string;
  rejectLabel: string;
}) {
  const name = baseName(change.display_path);
  const pathLabel = listPath(change.display_path);
  const openable = Boolean(onOpenFile) && change.status !== "deleted";

  return (
    <li
      className={cn(
        "group flex items-center gap-1.5 rounded-md px-1 py-0.5",
        active && "bg-muted/50",
        "hover:bg-muted/40",
      )}
    >
      <button
        type="button"
        disabled={!openable}
        onClick={() => onOpenFile?.(change.path)}
        title={change.display_path}
        className={cn(
          "flex min-w-0 flex-1 items-center gap-2 text-start",
          openable && "cursor-pointer",
        )}
      >
        <FileTypeIcon name={name} className="h-3 w-3" />
        <span className="min-w-0 truncate text-[11.5px] text-foreground/85">{pathLabel}</span>
      </button>
      {change.added || change.deleted ? (
        <span className="shrink-0 text-[11px] leading-none">
          <DiffPair added={change.added ?? 0} deleted={change.deleted ?? 0} hideZero />
        </span>
      ) : null}
      <div className="flex w-12 shrink-0 items-center justify-end gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <button
          type="button"
          disabled={busy}
          onClick={() => onAction("reject", change.path)}
          title={rejectLabel}
          aria-label={rejectLabel}
          className="grid h-5 w-5 place-items-center rounded text-muted-foreground/70 hover:text-rose-500 disabled:opacity-50"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onAction("accept", change.path)}
          title={acceptLabel}
          aria-label={acceptLabel}
          className="grid h-5 w-5 place-items-center rounded text-muted-foreground/70 hover:text-emerald-500 disabled:opacity-50"
        >
          <Check className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
    </li>
  );
}
