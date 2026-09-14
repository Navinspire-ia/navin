// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import { DefaultButton, Dropdown, PrimaryButton, TextField, Stack, MessageBar, MessageBarType, ProgressIndicator } from "@fluentui/react";
import { useTranslation } from "react-i18next";
import { extensionPackageBrowser, extensionRequest, saveExtensionPackage, type ExtensionState } from "@/lib/browser-extension";
import { CareerScene } from "./CareerScene";
import { extensionPairingAddress } from "@/lib/browser-extension-address";
import { BUTTON_STYLES } from "./career-ui";

export function BrowserExtensionPanel({ token }: { token: string }) {
  const { i18n } = useTranslation();
  const fr = i18n.language.startsWith("fr");
  const c = (f: string, e: string) => fr ? f : e;
  let address = "";
  try { address = extensionPairingAddress(window.location.href); } catch { /* Not a browser-reachable origin. */ }
  const [state, setState] = useState<ExtensionState>({});
  const [browser, setBrowser] = useState("chrome");
  const browserName = browser === "firefox" ? "Firefox" : browser === "safari" ? "Safari" : browser === "edge" ? "Microsoft Edge" : "Chrome";
  const chromium = browser === "chrome" || browser === "edge";
  const installAddress = browser === "edge" ? "edge://extensions" : browser === "chrome" ? "chrome://extensions" : "about:debugging#/runtime/this-firefox";
  const packageBrowser = extensionPackageBrowser(browser, state.downloads);
  const [pair, setPair] = useState<{ code: string; expires: number } | null>(null);
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const refresh = useCallback(async () => setState(await extensionRequest(token, "devices")), [token]);
  useEffect(() => {
    void refresh().catch(err => setError(String(err.message || err)));
    const timer = setInterval(() => { setNow(Date.now()); void refresh().catch(() => undefined); }, 5000);
    return () => clearInterval(timer);
  }, [refresh]);
  const run = async (action: string, body = {}) => {
    setBusy(true); setError(""); setNotice("");
    try { return await extensionRequest(token, action, body); }
    catch (err) { setError(String((err as Error).message || err)); return null; }
    finally { setBusy(false); }
  };
  const copy = async (value: string) => {
    try { await navigator.clipboard.writeText(value); setNotice(c("Copié", "Copied")); }
    catch { setError(c("Sélectionnez le texte et copiez-le avec votre clavier.", "Select the text and copy it with your keyboard.")); }
  };
  return <Stack tokens={{ childrenGap: 16 }} data-testid="browser-extension-panel">
    <Stack horizontal tokens={{ childrenGap: 16 }} verticalAlign="center">
      <div style={{ flex: 1 }}><h2 style={{ fontSize: 24, fontWeight: 600 }}>{c("Votre navigateur, relié à Navin", "Your browser, connected to Navin")}</h2>
        <p>{c("Connectez-vous aux sites dans Chrome, Edge ou Firefox. L'extension prépare les fiches que vous choisissez et les envoie après votre vérification.", "Sign in to websites in Chrome, Edge or Firefox. The extension prepares the records you choose and sends them after your review.")}</p></div>
      <div style={{ width: 155 }}><CareerScene matchRatio={state.devices?.length ? 1 : 0} label={c("Fiches transmises à Navin", "Records sent to Navin")} /></div>
    </Stack>
    <MessageBar>{c("Missions et emplois : Carrière. Profils : vivier de candidats. Leads : Leads. Appels d'offres : Tender, ou Carrière pour les consultations de missions. Les mots de passe et cookies restent dans votre navigateur.", "Missions and jobs: Career. Profiles: candidate pool. Prospects: Leads. Tenders: Tender, or Career for mission RFPs. Passwords and cookies stay in your browser.")}</MessageBar>
    {error && <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar>}
    {notice && <MessageBar messageBarType={MessageBarType.success}>{notice}</MessageBar>}
    {busy && <ProgressIndicator label={c("Opération en cours", "Working")} />}
    <Dropdown label={c("Navigateur", "Browser")} selectedKey={browser} disabled={busy}
      options={[{ key: "chrome", text: "Google Chrome" }, { key: "edge", text: "Microsoft Edge" }, { key: "firefox", text: "Mozilla Firefox" }, { key: "safari", text: "Apple Safari (macOS)" }]}
      onChange={(_, option) => { if (option) { setBrowser(String(option.key)); setPair(null); setError(""); setNotice(""); } }} />
    {browser === "safari" ? <Stack tokens={{ childrenGap: 12 }}>
      <h3>{c("Safari sur Mac", "Safari on Mac")}</h3>
      <MessageBar>{c("L'extension Navin pour Safari n'est pas encore distribuée. Safari nécessite un paquet Apple signé ; les ZIP Chrome, Edge et Firefox ne s'y installent pas. L'application Navin pour macOS peut déjà recevoir les imports de Chrome, Edge ou Firefox sur votre Mac.", "The Navin extension for Safari is not distributed yet. Safari requires a signed Apple package; Chrome, Edge and Firefox ZIP files cannot be installed in it. The Navin macOS app can already receive imports from Chrome, Edge or Firefox on your Mac.")}</MessageBar>
      <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
        <DefaultButton text={c("Choisir Chrome", "Choose Chrome")} styles={BUTTON_STYLES} onClick={() => setBrowser("chrome")} />
        <DefaultButton text={c("Choisir Firefox", "Choose Firefox")} styles={BUTTON_STYLES} onClick={() => setBrowser("firefox")} />
      </Stack>
    </Stack> : <>
    <h3>{c("1. Installer l'extension", "1. Install the extension")}</h3>
    <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
      <DefaultButton text={`${c("Télécharger pour", "Download for")} ${browserName}`}
        styles={BUTTON_STYLES} disabled={busy || !packageBrowser} onClick={async () => {
          if (!packageBrowser) return;
          setBusy(true); setError("");
          try {
            const result = await extensionRequest(token, "package", { browser: packageBrowser });
            if (!result.package) throw new Error(c("Paquet indisponible.", "Package unavailable."));
            await saveExtensionPackage({ ...result.package, name: `navin-import-${browser}.zip` });
          } catch (err) { setError(String((err as Error).message || err)); }
          finally { setBusy(false); }
        }} /></Stack>
    {state.downloads && !packageBrowser && <MessageBar messageBarType={MessageBarType.warning}>
      {c("Les extensions ne sont pas incluses dans cette installation de Navin. Installez une version qui contient les paquets navigateur.", "Browser extensions are missing from this Navin installation. Install a version that includes the browser packages.")}
    </MessageBar>}
    <TextField label={c("Adresse à coller dans le navigateur", "Address to paste into your browser")} readOnly
      value={installAddress} />
    <DefaultButton text={c("Copier l'adresse d'installation", "Copy installation address")} styles={BUTTON_STYLES}
      onClick={() => void copy(installAddress)} />
    {chromium && <MessageBar messageBarType={MessageBarType.info}>
      <strong>{c("Le dossier paraît vide ? Cliquez quand même sur Sélectionner le dossier.", "Does the folder look empty? Click Select Folder anyway.")}</strong>{" "}
      {c("Cette fenêtre sélectionne un dossier et masque les fichiers. Si vous avez vu manifest.json dans ce même dossier avec votre explorateur, vous êtes au bon endroit.", "This window selects a folder and hides files. If you saw manifest.json in this same folder in your file manager, you are in the right place.")}
    </MessageBar>}
    <ol style={{ paddingLeft: 24, lineHeight: 1.7, listStyleType: "decimal" }}>
      <li>{chromium ? c(`Dans votre explorateur de fichiers, extrayez navin-import-${browser}.zip. Ouvrez le dossier obtenu et vérifiez qu'il contient manifest.json, app.js et index.html.`, `In your file manager, extract navin-import-${browser}.zip. Open the extracted folder and check that it contains manifest.json, app.js and index.html.`) : c("Conservez le fichier navin-import-firefox.zip téléchargé. Il peut être chargé directement, sans décompression.", "Keep the downloaded navin-import-firefox.zip file. It can be loaded directly, without extraction.")}</li>
      <li>{chromium ? c(`Collez ${installAddress} dans la barre d'adresse de ${browserName}, puis activez Mode développeur.`, `Paste ${installAddress} into ${browserName}'s address bar, then enable Developer mode.`) : c("Collez about:debugging#/runtime/this-firefox dans la barre d'adresse de Firefox. Utilisez cette page de débogage pour l'installation temporaire.", "Paste about:debugging#/runtime/this-firefox into Firefox's address bar. Use this debugging page for temporary installation.")}</li>
      <li>{chromium ? c("Cliquez sur Charger l'extension non empaquetée. Sélectionnez le dossier qui contient manifest.json, puis validez Sélectionner le dossier, même si aucun fichier n'est affiché.", "Click Load unpacked. Select the folder containing manifest.json, then confirm Select Folder, even if no files are displayed.") : c("Cliquez sur Charger un module complémentaire temporaire, puis sélectionnez navin-import-firefox.zip. Si vous l'avez déjà extrait, sélectionnez le fichier manifest.json à l'intérieur du dossier.", "Click Load Temporary Add-on, then select navin-import-firefox.zip. If you already extracted it, select manifest.json inside the folder.")}</li>
      <li>{c("Vérifiez que Navin Import apparaît dans la liste des extensions, puis générez le code d'appairage ci-dessous.", "Check that Navin Import appears in the extension list, then generate the pairing code below.")}</li>
    </ol>
    {browser === "firefox" && <MessageBar>{c("N'utilisez pas Installer un module depuis un fichier dans about:addons : cette installation permanente exige une signature Mozilla. Le ZIP fourni se charge depuis about:debugging, avec Firefox 140 ou plus récent.", "Do not use Install Add-on From File in about:addons: permanent installation requires a Mozilla signature. Load this ZIP from about:debugging, using Firefox 140 or newer.")}</MessageBar>}
    {browser === "firefox" && <MessageBar>{c("L'installation Firefox est temporaire : rechargez l'extension après chaque redémarrage du navigateur. Le paquet fourni n'est pas signé par Mozilla.", "Firefox installation is temporary: reload the extension after each browser restart. The supplied package is not signed by Mozilla.")}</MessageBar>}
    <h3>{c("2. Relier le navigateur", "2. Pair the browser")}</h3>
    <PrimaryButton text={c("Générer un code d'appairage", "Generate pairing code")} disabled={busy} styles={BUTTON_STYLES}
      onClick={async () => { const result = await run("create", { label: browserName }); if (result?.code && result.expires_at) setPair({ code: result.code, expires: result.expires_at }); }} />
    {pair && <Stack tokens={{ childrenGap: 8 }}>
      <TextField label={c("Code à saisir dans l'extension", "Code to enter in the extension")} readOnly value={pair.code.match(/.{1,4}/g)?.join(" ") || pair.code} />
      <p>{pair.expires * 1000 > now ? c("Usage unique, valable 5 minutes.", "Single use, valid for 5 minutes.") : c("Code expiré. Générez-en un nouveau.", "Code expired. Generate a new one.")}</p>
      <DefaultButton text={c("Copier le code", "Copy code")} disabled={pair.expires * 1000 <= now} onClick={() => void copy(pair.code)} styles={BUTTON_STYLES} />
      {address ? <>
        <TextField label={c("Adresse de Navin", "Navin address")} readOnly value={address} />
        <DefaultButton text={c("Copier l'adresse et le code", "Copy address and code")} disabled={pair.expires * 1000 <= now}
          onClick={() => void copy(JSON.stringify({ type: "navin-browser-pairing", navin_url: address, code: pair.code }))} styles={BUTTON_STYLES} />
        <p>{c("Collez ces informations dans le champ Code d'appairage de l'extension. L'adresse est remplie avec celle de cette instance Navin. Pour une adresse locale, utilisez le navigateur sur le même ordinateur que Navin.", "Paste this into the extension's pairing code field. It fills in this Navin instance's address. For a local address, use the browser on the same computer as Navin.")}</p>
      </> : <MessageBar messageBarType={MessageBarType.warning}>{c("L'adresse actuelle ne peut pas être utilisée par le navigateur. Ouvrez Navin depuis son adresse HTTP locale ou HTTPS publique avant l'appairage.", "The browser cannot use this address. Open Navin at its local HTTP or public HTTPS address before pairing.")}</MessageBar>}
    </Stack>}
    <h3>{c("3. Importer une fiche", "3. Import a record")}</h3>
    <p>{c("Ouvrez une fiche puis cliquez sur la bulle Navin. Pour les prospects, choisissez Prospection commerciale (Leads), renseignez l'entreprise et vérifiez le contact. Pour les missions et candidats, analysez avec vos critères Navin. Sélectionnez les fiches puis confirmez leur enregistrement. Sur LinkedIn, sélectionnez d'abord le texte à reprendre.", "Open a record and click the Navin bubble. For prospects, choose Commercial prospecting (Leads), enter the company and review the contact. For missions and candidates, analyze with your Navin criteria. Select the records and confirm saving. On LinkedIn, select the text to capture first.")}</p>
    </>}
    <h3>{c("Navigateurs appairés", "Paired browsers")}</h3>
    {!state.devices?.length && <p>{c("Aucun navigateur appairé pour le moment.", "No paired browsers yet.")}</p>}
    {state.devices?.map(device => <Stack key={device.id} horizontal wrap horizontalAlign="space-between" verticalAlign="center" tokens={{ childrenGap: 12 }}>
      <div><strong>{device.label}</strong><p>{c("Dernier contact", "Last seen")} : {new Date(device.last_seen * 1000).toLocaleString(i18n.language)}</p></div>
      <DefaultButton text={c("Révoquer l'accès", "Revoke access")} disabled={busy} styles={BUTTON_STYLES} onClick={async () => {
        const result = await run("revoke", { id: device.id }); if (result) setState(result);
      }} />
    </Stack>)}
    <h3>{c("Derniers imports", "Recent imports")}</h3>
    {!state.receipts?.length && <p>{c("Les résultats apparaîtront après votre premier envoi.", "Results will appear after your first import.")}</p>}
    {state.receipts?.slice(-10).reverse().map(receipt => <p key={`${receipt.device}-${receipt.request_id}`}>
      {new Date(receipt.at * 1000).toLocaleString(i18n.language)} · {receipt.kind} · {receipt.status === "excluded" ? c("Écarté par les critères", "Excluded by criteria") : receipt.status === "updated" ? c("Fiche mise à jour", "Record updated") : c("Import réussi", "Imported")}
      {receipt.reason ? ` (${receipt.reason})` : ""}
    </p>)}
    <DefaultButton text={c("Actualiser", "Refresh")} disabled={busy} styles={BUTTON_STYLES} onClick={() => void refresh().catch(err => setError(String(err.message || err)))} />
  </Stack>;
}
