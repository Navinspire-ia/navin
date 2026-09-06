import { DefaultButton, Dialog, DialogFooter, DialogType, PrimaryButton } from "@fluentui/react";

import { NoticeGoButtons } from "@/components/studio/tenders/NoticeGoButtons";
import { TenderFactsRow } from "@/components/studio/tenders/TenderFactsRow";
import { BUTTON_STYLES, OfficialLink, scoreTone, type Tx } from "@/components/studio/tenders/tenders-ui";
import { parseTenderFacts } from "@/lib/tender-facts";
import type { TenderNotice } from "@/lib/tenders-api";
import { officialTenderHref } from "@/lib/tenders-api";
import { cn } from "@/lib/utils";

export function TenderNoticeDetail({
  notice,
  tx,
  token,
  locale,
  sourceNames,
}: {
  notice: TenderNotice;
  tx: Tx;
  token: string;
  locale?: string;
  sourceNames?: Record<string, string>;
}) {
  const facts = parseTenderFacts(notice, { locale, sourceNames });
  const official = officialTenderHref(notice.source_url || "");
  const decision =
    notice.go == null
      ? tx("notScoredYet", "Not scored yet")
      : notice.go
        ? tx("goYes", "GO")
        : tx("goNo", "NO-GO");
  return (
    <div className="grid min-w-0 gap-5" data-testid="tenders-notice-detail">
      <div className="flex min-w-0 flex-nowrap items-center gap-3">
        <h3 className="min-w-0 flex-1 truncate text-lg font-semibold" title={facts.title}>
          {facts.title || tx("unknown", "non renseigne")}
        </h3>
        <span className={cn("shrink-0 tabular-nums text-xl font-semibold", scoreTone(notice.score))}>
          {notice.score != null ? Math.round(notice.score) : "-"}
        </span>
      </div>
      <TenderFactsRow facts={facts.card} tx={tx} />
      <TenderFactsRow facts={facts.extra} tx={tx} testId="tenders-facts-extra" className="sm:grid-cols-3" />
      {notice.go != null ? (
        <div className="grid gap-1">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {tx("filterGoPick", "GO / NO-GO")}
          </p>
          <p className="text-sm font-semibold">
            {decision}{" "}
            <span className={cn("tabular-nums", scoreTone(notice.go_pct ?? notice.score))}>
              {notice.go_pct ?? notice.score ?? "-"}%
            </span>
          </p>
          {facts.goReason ? <p className="text-pretty text-sm text-muted-foreground">{facts.goReason}</p> : null}
          {facts.goNote ? <p className="text-pretty text-sm">{facts.goNote}</p> : null}
        </div>
      ) : null}
      <div>
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          {tx("detailDescription", "Description")}
        </p>
        <p className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap text-pretty text-sm leading-relaxed">
          {facts.description || tx("noDescription", "No published description in the store yet.")}
        </p>
      </div>
      {facts.need ? (
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {tx("factNeed", "Need")}
          </p>
          <p className="mt-1 text-pretty text-sm">{facts.need}</p>
        </div>
      ) : null}
      {facts.eligibility ? (
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {tx("factEligibility", "Eligibility")}
          </p>
          <p className="mt-1 text-pretty text-sm">{facts.eligibility}</p>
        </div>
      ) : null}
      {official && token ? (
        <OfficialLink
          href={official}
          token={token}
          className="text-sm text-emerald-700 underline underline-offset-2 dark:text-emerald-300"
        >
          {tx("rowOfficial", "Official notice")}
        </OfficialLink>
      ) : null}
    </div>
  );
}

export function TenderNoticeDialog({
  notice,
  tx,
  token,
  busy,
  locale,
  sourceNames,
  onDismiss,
  onOpenDossier,
  onQualify,
  onWrite,
  onGo,
  onNogo,
}: {
  notice: TenderNotice | null;
  tx: Tx;
  token: string;
  busy?: boolean;
  locale?: string;
  sourceNames?: Record<string, string>;
  onDismiss: () => void;
  onOpenDossier: () => void;
  onQualify: () => void;
  onWrite: () => void;
  onGo: () => void;
  onNogo: () => void;
}) {
  if (!notice) return null;
  return (
    <Dialog
      hidden={false}
      onDismiss={onDismiss}
      minWidth={640}
      maxWidth={920}
      modalProps={{ isBlocking: true, dragOptions: undefined }}
      dialogContentProps={{
        type: DialogType.largeHeader,
        title: tx("readStep", "1. This notice"),
        showCloseButton: true,
      }}
    >
      <div data-testid="tenders-notice-dialog">
        <TenderNoticeDetail
          notice={notice}
          tx={tx}
          token={token}
          locale={locale}
          sourceNames={sourceNames}
        />
      </div>
      <DialogFooter>
        <PrimaryButton
          text={tx("qualify", "Score this notice")}
          disabled={Boolean(busy)}
          onClick={onQualify}
          styles={BUTTON_STYLES}
        />
        <NoticeGoButtons
          row={notice}
          tx={tx}
          busy={Boolean(busy)}
          size="regular"
          onGo={onGo}
          onNogo={onNogo}
        />
        <DefaultButton
          text={tx("write", "Write the reply")}
          disabled={Boolean(busy)}
          onClick={onWrite}
          styles={BUTTON_STYLES}
        />
        <DefaultButton
          text={tx("openDossier", "Open dossier")}
          onClick={onOpenDossier}
          styles={BUTTON_STYLES}
        />
        <DefaultButton text={tx("closeDetail", "Close")} onClick={onDismiss} styles={BUTTON_STYLES} />
      </DialogFooter>
    </Dialog>
  );
}
