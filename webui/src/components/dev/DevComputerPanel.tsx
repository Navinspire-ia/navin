import { useCallback, useEffect, useState } from "react";
import { DefaultButton, MessageBar, MessageBarType, Spinner } from "@fluentui/react";
import { useTranslation } from "react-i18next";

import { ComputerSettings } from "@/components/settings/ComputerSettings";
import { fetchSettings } from "@/lib/api";
import { getRuntimeHost } from "@/lib/runtime";
import type { SettingsPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

export default function DevComputerPanel() {
  const { token } = useClient();
  const { t } = useTranslation();
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [isRestarting, setRestarting] = useState(false);
  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true); setError(null);
    try { setSettings(await fetchSettings(token)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoading(false); }
  }, [token]);
  useEffect(() => { void load(); }, [load]);

  async function restart() {
    const host = getRuntimeHost();
    if (!host.restartEngine) {
      window.location.hash = "#/settings?section=computer";
      return;
    }
    setRestarting(true);
    try { await host.restartEngine(); await load(); }
    finally { setRestarting(false); }
  }

  return (
    <div className="computer-workbench-panel" data-testid="dev-computer-panel">
      {error ? <MessageBar messageBarType={MessageBarType.error} actions={
        <DefaultButton text={t("settings.computer.retry")} disabled={loading} onClick={() => void load()} />
      }>{error}</MessageBar> : null}
      {settings ? <ComputerSettings settings={settings} onUpdated={setSettings}
        onRestart={restart} isRestarting={isRestarting} />
        : loading ? <Spinner label={t("settings.computer.loading")} /> : null}
    </div>
  );
}
