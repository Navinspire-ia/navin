import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { fetchBenchReport, type BenchReport } from "@/lib/bench-api";

export function BenchResults({ token }: { token: string }) {
  const { t } = useTranslation();
  const [report, setReport] = useState<BenchReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    fetchBenchReport(token)
      .then((r) => {
        if (!live) return;
        setReport(r.report);
        setFailed(false);
      })
      .catch(() => {
        if (live) setFailed(true);
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [token, nonce]);

  const header = (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="text-base font-semibold">{t("bench.title")}</h2>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={refresh}
        disabled={loading}
        aria-label={t("bench.refresh")}
      >
        <RefreshCw className={loading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} aria-hidden />
        <span className="ml-1.5">{t("bench.refresh")}</span>
      </Button>
    </div>
  );

  let body;
  if (loading && !report) {
    body = (
      <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        {t("bench.loading")}
      </p>
    );
  } else if (failed) {
    body = (
      <p className="text-sm text-destructive" role="alert">
        {t("bench.error")}
      </p>
    );
  } else if (!report || report.matrix.length === 0) {
    body = <p className="text-sm text-muted-foreground">{t("bench.empty")}</p>;
  } else {
    body = (
      <>
        <p className="mb-2 text-xs text-muted-foreground">
          {t("bench.generated", { date: report.generated_at })}
          {report.dry_run ? ` (${t("bench.dryRun")})` : ""}
        </p>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left">
              <th>{t("bench.model")}</th>
              <th>{t("bench.strategy")}</th>
              <th>{t("bench.success")}</th>
              <th>{t("bench.usd")}</th>
              <th>{t("bench.usdPerSuccess")}</th>
              <th>{t("bench.brokenTests")}</th>
              <th>{t("bench.lintDelta")}</th>
            </tr>
          </thead>
          <tbody>
            {report.matrix.map((c) => (
              <tr key={`${c.model}/${c.strategy}`}>
                <td>{c.model}</td>
                <td>{c.strategy}</td>
                <td>
                  {c.successes}/{c.missions}
                </td>
                <td>{c.usd.toFixed(4)}</td>
                <td>{c.usd_per_success === null ? "n/a" : c.usd_per_success.toFixed(4)}</td>
                <td>{c.tests_failed}</td>
                <td>{c.lint_delta}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </>
    );
  }
  return (
    <div className="p-3">
      {header}
      {body}
    </div>
  );
}
