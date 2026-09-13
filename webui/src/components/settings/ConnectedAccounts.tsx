import { useCallback, useEffect, useState } from "react";
import { Checkbox, DefaultButton, MessageBar, MessageBarType, PrimaryButton, ProgressIndicator, Stack } from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";
import { useClient } from "@/providers/ClientProvider";
import { openInOsBrowser } from "@/lib/api";
import { accountAction, DEFAULT_ACCOUNT_PERMISSIONS, type AccountPermission, type AccountPermissions, type AccountsSnapshot, type ConnectedAccount } from "@/lib/accounts-api";

const BUTTON = { root: { minHeight: 40 } };
export function ConnectedAccounts({ selectedId, onSelect }: { selectedId?: string; onSelect?: (account: ConnectedAccount) => void }) {
  const { token } = useClient();
  const { i18n } = useTranslation();
  const c = (fr: string, en: string) => i18n.language.startsWith("fr") ? fr : en;
  const reduced = useReducedMotion();
  const [state, setState] = useState<AccountsSnapshot | null>(null);
  const [permissions, setPermissions] = useState(DEFAULT_ACCOUNT_PERMISSIONS);
  const [flow, setFlow] = useState("");
  const [flowUrl, setFlowUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const labels: Record<AccountPermission, string> = { read_mail: c("Lire et rechercher les emails", "Read and search emails"), draft_mail: c("Créer des brouillons", "Create drafts"),
    send_mail: c("Envoyer sans approbation", "Send without approval"), archive_mail: c("Archiver les emails", "Archive emails"), delete_mail: c("Mettre les emails à la corbeille", "Move emails to trash"),
    read_calendar: c("Lire le calendrier", "Read calendar"), write_calendar: c("Créer et modifier les événements", "Create and edit events"), cancel_events: c("Annuler les événements", "Cancel events"), read_contacts: c("Lire les contacts", "Read contacts") };
  const refresh = useCallback(async () => { const result = await accountAction(token, "snapshot"); setState(result); return result; }, [token]);
  useEffect(() => { const abort = new AbortController(); void accountAction(token, "snapshot", {}, abort.signal).then(setState).catch(e => { if (!abort.signal.aborted) setError(String(e.message || e)); }); return () => abort.abort(); }, [token]);
  const action = async (kind: string, body: Record<string, unknown>) => {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await accountAction<AccountsSnapshot & { result?: { status: string; error?: string } }>(token, kind, body);
      setState(result);
      if (result.result) setNotice(result.result.error || result.result.status);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    if (!flow) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await accountAction<{ status: string; account_id?: string; error?: string }>(token, "poll", { flow_id: flow });
        if (!active) return;
        if (result.status === "connected") {
          const snapshot = await refresh();
          if (!active) return;
          const account = snapshot.accounts.find(a => a.id === result.account_id);
          if (account) onSelect?.(account);
          setFlow(""); setNotice(c("Compte connecté.", "Account connected."));
          if ("__TAURI_INTERNALS__" in window) {
            const { getCurrentWindow } = await import("@tauri-apps/api/window");
            await getCurrentWindow().unminimize().catch(() => undefined);
            await getCurrentWindow().setFocus().catch(() => undefined);
          }
        } else if (["failed", "expired", "cancelled"].includes(result.status)) {
          setFlow(""); if (result.error) setError(result.error);
        } else timer = setTimeout(poll, 1500);
      } catch (e) { if (active) { setError(e instanceof Error ? e.message : String(e)); setFlow(""); } }
    };
    timer = setTimeout(poll, 1000);
    return () => { active = false; clearTimeout(timer); };
  // A selection callback may change as its parent form rerenders; the flow remains tied to its ID.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow, token, refresh]);
  const connect = async (provider: string, grants = permissions) => {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await accountAction<{ flow_id: string; url: string }>(token, "connect", { provider, permissions: grants, locale: i18n.language });
      setFlowUrl(result.url);
      setFlow(result.flow_id);
      const opened = await openInOsBrowser(token, result.url);
      if (!opened.opened) setNotice(c("Le navigateur ne s'est pas ouvert. Utilisez le bouton ci-dessous pour poursuivre la connexion.", "The browser did not open. Use the button below to continue signing in."));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const choices = (values: AccountPermissions, change: (key: AccountPermission, value: boolean) => void, capabilities?: AccountPermissions) =>
    (Object.keys(labels) as AccountPermission[]).map(key => <Checkbox key={key} label={labels[key]} checked={values[key]} disabled={busy || (!!capabilities && !capabilities[key])}
      onChange={(_, value) => change(key, !!value)} />);
  return <Stack tokens={{ childrenGap: 16 }} data-testid="connected-accounts">
    <div><h3 className="text-lg font-semibold">{c("Connecter un compte", "Connect accounts")}</h3><p className="mt-1 text-sm text-muted-foreground">{c("Google ou Microsoft, dans votre navigateur. Choisissez ensuite ce que l'agent peut faire.", "Google or Microsoft, in your browser. Then choose what your agent can do.")}</p></div>
    {error && <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar>}
    {notice && <MessageBar>{notice}</MessageBar>}
    {(!state || busy || flow) && <ProgressIndicator label={flow ? c("Autorisez Navin dans le navigateur...", "Authorize Navin in your browser...") : c("Chargement...", "Loading...")} />}
    <Stack horizontal wrap tokens={{ childrenGap: 8 }}>{(state?.providers || []).map(provider => <DefaultButton key={provider.id} text={`${c("Connecter", "Connect")} ${provider.name}`}
      styles={BUTTON} disabled={busy || !!flow || !provider.ready || !state?.vault_ready} onClick={() => void connect(provider.id)} data-testid={`connect-${provider.id}`} />)}</Stack>
    {state?.providers.some(p => !p.ready) && <MessageBar>{c(
      "La connexion Google/Microsoft n'est pas encore activée dans cette distribution de Navin. L'inscription de Navin auprès du fournisseur est réalisée une seule fois par son éditeur, pour tous les utilisateurs.",
      "Google/Microsoft sign-in is not yet enabled in this Navin distribution. Navin's publisher registers the application with each provider once for all users.")}</MessageBar>}
    {state && !state.vault_ready && <MessageBar>{c(
      "Le coffre de mots de passe du système est indisponible. Activez ou déverrouillez votre trousseau pour conserver les autorisations Google/Microsoft sur cet ordinateur.",
      "The system credential vault is unavailable. Enable or unlock your keychain to store Google/Microsoft authorizations on this computer.")}</MessageBar>}
    {(!state || !state.vault_ready || state.providers.some(p => !p.ready)) && <DefaultButton styles={BUTTON}
      text={c("Revérifier la disponibilité", "Recheck availability")} disabled={busy || !!flow}
      onClick={() => void action("snapshot", {})} />}
    {flow && <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
      <DefaultButton text={c("Ouvrir le navigateur", "Open browser")} styles={BUTTON} disabled={busy || !flowUrl} onClick={async () => {
        try {
          const opened = await openInOsBrowser(token, flowUrl);
          setError(opened.opened ? "" : c("Autorisez l'ouverture d'une nouvelle fenêtre dans votre navigateur, puis réessayez.", "Allow your browser to open a new window, then try again."));
        } catch { setError(c("Impossible d'ouvrir le navigateur. Réessayez.", "Could not open the browser. Try again.")); }
      }} />
      <DefaultButton text={c("Annuler la connexion", "Cancel connection")} styles={BUTTON} disabled={busy} onClick={async () => {
        setBusy(true);
        try { await accountAction(token, "cancel", { flow_id: flow }); setFlow(""); setFlowUrl(""); setNotice(""); }
        catch { setError(c("Impossible d'annuler la connexion. Réessayez.", "Could not cancel the connection. Try again.")); }
        finally { setBusy(false); }
      }} />
    </Stack>}
    <details open><summary className="cursor-pointer text-sm">{c("Permissions pour la prochaine connexion", "Permissions for the next connection")}</summary>
      <Stack tokens={{ childrenGap: 12 }} styles={{ root: { paddingTop: 16 } }}>{choices(permissions, (key, value) => setPermissions(old => ({ ...old, [key]: value })))}</Stack></details>
    {state?.accounts.map(account => <motion.div key={account.id} layout={!reduced} className="rounded-xl border border-border p-4">
      <Stack tokens={{ childrenGap: 12 }}><strong>{account.provider === "google" ? "Google" : "Microsoft"} · {account.email}</strong>
        <p className="text-xs text-muted-foreground">{c("Jetons dans le coffre local · Relais cloud désactivé", "Tokens in local OS vault · Cloud relay disabled")}</p>
        {onSelect && <PrimaryButton text={selectedId === account.id ? c("Compte utilisé par Carrière", "Used by Career") : c("Utiliser dans Carrière", "Use in Career")}
          disabled={busy || selectedId === account.id} styles={BUTTON} onClick={() => onSelect(account)} />}
        <details><summary className="cursor-pointer">{c("Actions autonomes", "Autonomous actions")}</summary><Stack tokens={{ childrenGap: 12 }} styles={{ root: { paddingTop: 16 } }}>
          {choices(account.permissions, (key, value) => void action("permissions", { account_id: account.id, permissions: { ...account.permissions, [key]: value } }), account.capabilities)}
          <p className="text-xs text-muted-foreground">{c("Les actions non autorisées restent à approuver. Pour élargir l'accès, choisissez les permissions ci-dessus puis reconnectez le compte.", "Unauthorized actions require approval. To expand access, select permissions above and reconnect the account.")}</p>
        </Stack></details>
        <Stack horizontal wrap tokens={{ childrenGap: 8 }}><DefaultButton text={c("Reconnecter", "Reconnect")} styles={BUTTON} disabled={busy || !!flow} onClick={() => void connect(account.provider)} />
          <DefaultButton text={c("Déconnecter", "Disconnect")} styles={BUTTON} disabled={busy} onClick={() => void action("disconnect", { account_id: account.id })} /></Stack>
      </Stack>
    </motion.div>)}
    {!!state?.pending.length && <h4 className="font-semibold">{c("À approuver", "Pending approval")}</h4>}
    {state?.pending.map(entry => <div key={entry.id} className="rounded-xl border border-border p-4"><strong>{entry.action}</strong>
      <p className="text-xs">{state.accounts.find(a => a.id === entry.account_id)?.email}</p>
      <dl className="my-3 space-y-2 text-sm">{Object.entries(entry.body).filter(([key]) => key !== "request_id").map(([key, value]) => <div key={key}><dt className="font-medium">{key}</dt><dd className="whitespace-pre-wrap break-words">{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>)}</dl>
      <Stack horizontal wrap tokens={{ childrenGap: 8 }}><PrimaryButton text={c("Approuver cette action", "Approve this action")} styles={BUTTON} disabled={busy} onClick={() => void action("approve", { approval_id: entry.id })} />
        <DefaultButton text={c("Refuser", "Reject")} styles={BUTTON} disabled={busy} onClick={() => void action("reject", { approval_id: entry.id })} /></Stack>
    </div>)}
  </Stack>;
}
