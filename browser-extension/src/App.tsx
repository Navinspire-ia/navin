// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useRef, useState } from "react";
import { Checkbox, DefaultButton, Dropdown, Label, Link, MessageBar, MessageBarType, PrimaryButton, ProgressIndicator, Stack, TextField, ThemeProvider, createTheme } from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { parseExtensionPairing } from "../../webui/src/lib/browser-extension-address";
import { api, autoPair, blankRecord, discoverLocalNavin, navinBase, request, routeRecords, type Analysis, type AnalysisResult, type Capture, type Connection, type ImportRecord, type RecordKind } from "./client";

const kinds = [{ key: "mission", text: "Mission / offre freelance" }, { key: "job", text: "Emploi salarié" },
  { key: "candidate", text: "Profil candidat" }, { key: "lead", text: "Lead / prospect" }, { key: "tender", text: "Appel d'offres" }];
const reasons: Record<string, string> = { country: "Pays hors périmètre ou inconnu", city: "Ville différente ou inconnue", role: "Métier différent",
  skills: "Compétences requises non renseignées", rate: "TJM inférieur au minimum du mode de travail", rate_unknown: "TJM ou devise non vérifié",
  budget: "Budget projet inférieur au minimum", budget_unknown: "Budget projet non renseigné", work_mode: "Mode de travail différent",
  work_mode_unknown: "Mode de travail inconnu", track: "Type de contrat différent de vos critères", date: "Publication hors période",
  date_unknown: "Date de publication manquante", expired: "Date limite dépassée" };
const theme = createTheme({ palette: { themePrimary: "#155bba", neutralLighterAlt: "#f6f8fb" }, defaultFontStyle: { fontFamily: "Segoe UI, system-ui, sans-serif" } });
const captureKey = "capture:" + (new URLSearchParams(location.search).get("capture") || "manual");
const analysisLabels = { recommended: "Correspond à vos critères", review: "À vérifier", excluded: "Hors critères", duplicate: "Déjà présent", invalid: "À compléter" };
const reasonLabels: Record<string, string> = { role: "Métier", skills: "Compétences", country: "Pays", city: "Ville", domain: "Domaine", buy_rate: "TJM d'achat", salary: "Salaire", availability: "Disponibilité", mode: "Recherche active", configuration: "Configuration", duplicate: "Doublon", criteria: "Critères", manual: "Destination" };

export function App() {
  const reduced = useReducedMotion();
  const [connection, setConnection] = useState<Connection | null>(null);
  const [verified, setVerified] = useState(false);
  const [analysisSupported, setAnalysisSupported] = useState(false);
  const [criteria, setCriteria] = useState<Analysis | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [base, setBase] = useState("");
  const [code, setCode] = useState("");
  const [consent, setConsent] = useState(false);
  const [autoTried, setAutoTried] = useState(false);
  const [capture, setCapture] = useState<Capture>({});
  const [destination, setDestination] = useState("detected");
  const [records, setRecords] = useState<ImportRecord[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [active, setActive] = useState(0);
  const [editing, setEditing] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [results, setResults] = useState<Array<{ request_id: string; status: string; reason?: string; error?: string }>>([]);
  const activate = async (next: Connection) => {
    setConnection(next); setBase(next.base);
    const status = await request(next.base, next.token, "status");
    setVerified(true);
    setAnalysisSupported(status.analysis_version === 1);
    if (status.analysis_version === 1) setCriteria(await request(next.base, next.token, "criteria") as unknown as Analysis);
  };
  useEffect(() => {
    void (async () => {
      try {
        const [saved, draft] = await Promise.all([api.storage.local.get(["connection", "destination"]), api.storage.session.get(captureKey)]);
        const data = draft[captureKey] || {};
        const target = saved.destination === "leads" ? "leads" : "detected";
        setDestination(target);
        setCapture(data); setRecords(routeRecords(data.records || [], target));
        if (data.records?.length === 1) setSelected([0]);
        if (saved.connection) { await activate(saved.connection); return; }
        // Auto-connection: find a local Navin and pair without a code.
        const found = await discoverLocalNavin();
        if (found) {
          try {
            const next = await autoPair(found, "Mon navigateur");
            await api.storage.local.set({ connection: next });
            await activate(next);
            setSuccess("Navin détecté sur cet ordinateur : extension reliée automatiquement.");
            return;
          } catch { /* Auto-pair refused: fall back to the manual form. */ }
        }
        setAutoTried(true);
      } catch (err) { setError(String((err as Error).message || err)); }
      finally { setBusy(false); }
    })();
  }, []);
  const persist = (rows: ImportRecord[]) => { void api.storage.session.set({ [captureKey]: { ...capture, records: rows, at: Date.now() } }); };
  const edit = (key: keyof ImportRecord, value: unknown) => {
    if (key === "kind" && destination === "leads" && value !== "lead") {
      setDestination("detected"); void api.storage.local.set({ destination: "detected" });
    }
    const next = records.map((row, index) => index === active ? { ...row, [key]: value, request_id: crypto.randomUUID() } : row);
    setRecords(next); persist(next); setReviewed(false); setAnalysis(null); setSelected([]);
  };
  const connect = async () => {
    setBusy(true); setError(""); setSuccess(""); setAnalysis(null); setCriteria(null); setReviewed(false);
    try {
      const address = navinBase(base);
      const url = new URL(address);
      const permitted = await api.permissions.request({ origins: [`${url.protocol}//${url.hostname}/*`] });
      if (!permitted) throw new Error("Autorisez l'accès à votre instance Navin pour relier l'extension.");
      const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
      let next: Connection;
      if (code.trim()) {
        const result = await request(address, "", "pair", { code });
        next = { base: address, token: String(result.token), label: (result.device as { label: string }).label };
      } else if (local) {
        next = await autoPair(address, "Mon navigateur");
      } else {
        throw new Error("Pour Navin en ligne, générez et collez un code d'appairage.");
      }
      await api.storage.local.set({ connection: next }); setConnection(next); setVerified(true); setCode(""); setSuccess("Navigateur relié à Navin.");
      const status = await request(address, next.token, "status");
      setAnalysisSupported(status.analysis_version === 1);
      if (status.analysis_version === 1) setCriteria(await request(address, next.token, "criteria") as unknown as Analysis);
    } catch (err) { setError(String((err as Error).message || err)); }
    finally { setBusy(false); }
  };
  const retryAuto = async () => {
    setBusy(true); setError("");
    try {
      const found = await discoverLocalNavin();
      if (!found) throw new Error("Navin n'a pas été trouvé sur cet ordinateur. Démarrez Navin puis réessayez.");
      const next = await autoPair(found, "Mon navigateur");
      await api.storage.local.set({ connection: next });
      await activate(next);
      setSuccess("Navin détecté sur cet ordinateur : extension reliée automatiquement.");
    } catch (err) { setError(String((err as Error).message || err)); }
    finally { setBusy(false); }
  };
  const analyze = async () => {
    if (!connection) return;
    setBusy(true); setError(""); setSuccess(""); setReviewed(false); setSelected([]); setAnalysis(null); setResults([]);
    try {
      const result = await request(connection.base, connection.token, "analyze", { records }) as unknown as Analysis;
      const byId = new Map(result.results?.map(item => [item.request_id, item]));
      const next = records.map(row => byId.get(row.request_id)?.record || row);
      setRecords(next); persist(next); setCriteria(result); setAnalysis(result); setEditing(false);
      setSelected(next.flatMap((row, index) => byId.get(row.request_id)?.status === "recommended" ? [index] : []));
      setSuccess("Analyse terminée. Vérifiez les fiches retenues avant leur enregistrement.");
    } catch (err) { setError(String((err as Error).message || err)); }
    finally { setBusy(false); }
  };
  const send = async () => {
    if (!connection || !reviewed || !selected.length) return;
    setBusy(true); setError(""); setSuccess("");
    try {
      const result = await request(connection.base, connection.token, "import", { records: records.filter((_, index) => selected.includes(index)).map(row => ({ ...row, reviewed: true })), ...(analysis ? { criteria_revision: analysis.revision } : {}) });
      const receipts = result.results as typeof results;
      setResults(receipts);
      const done = new Set(receipts.filter(row => ["imported", "updated"].includes(row.status)).map(row => row.request_id));
      if (done.size) setSuccess(`${done.size} fiche(s) enregistrée(s) dans Navin.`);
      const remaining = records.filter(row => !done.has(row.request_id));
      setRecords(remaining); persist(remaining); setSelected([]); setActive(0); setReviewed(false);
    } catch (err) { setError(String((err as Error).message || err)); }
    finally { setBusy(false); }
  };
  const row = records[active];
  const autoAnalyzed = useRef(false);
  useEffect(() => {
    if (verified && analysisSupported && destination !== "leads" && records.length && !analysis && !busy && !autoAnalyzed.current) {
      autoAnalyzed.current = true;
      void analyze();
    }
  }, [verified, analysisSupported, destination, records.length, analysis, busy]);
  const match = (item: ImportRecord): AnalysisResult | undefined => analysis?.results?.find(result => result.request_id === item.request_id);
  const activeMatch = row ? match(row) : undefined;
  const needsAnalysis = analysisSupported && records.some(item => ["candidate", "mission", "job"].includes(item.kind)) && !analysis;
  const field = (key: keyof ImportRecord, label: string, options: { multiline?: boolean; type?: string; maxLength?: number; required?: boolean } = {}) =>
    <TextField key={key} label={label} value={String(row?.[key] ?? "")} onChange={(_, value) => edit(key, value || "")} {...options} disabled={busy} />;

  return <ThemeProvider theme={theme}><main style={{ maxWidth: 1120, margin: "0 auto", padding: "32px 24px 64px" }}>
    <motion.div initial={reduced ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
      <Stack tokens={{ childrenGap: 18 }}>
        <header><Label styles={{ root: { color: theme.palette.themePrimary, letterSpacing: "1.3px", fontWeight: 700 } }}>NAVIN IMPORT</Label>
          <h1 style={{ margin: "8px 0", fontSize: 30 }}>{destination === "leads" ? "Conserver ces contacts dans Leads" : "Cette page correspond-elle à vos critères ?"}</h1>
          <p>{destination === "leads" ? "Vérifiez les coordonnées et informations publiées avant d'enregistrer vos prospects dans Navin." : "Analysez les profils et missions de cette page avec vos critères Navin, puis validez les fiches à conserver."}</p></header>
        <Dropdown label="Utilisation de cette capture" selectedKey={destination} disabled={busy}
          options={[{ key: "detected", text: "Selon le type de fiche détecté" }, { key: "leads", text: "Prospection commerciale (Leads)" }]}
          onChange={(_, option) => {
            const target = String(option?.key || "detected");
            setDestination(target); void api.storage.local.set({ destination: target });
            const next = routeRecords(records, target).map(row => ({ ...row, request_id: crypto.randomUUID() }));
            setRecords(next); persist(next); setAnalysis(null); setReviewed(false); setResults([]); setSuccess("");
            setSelected(target === "leads" ? next.map((_, index) => index) : []);
          }} />
        {destination === "leads" && <MessageBar>Ces fiches seront enregistrées dans Leads comme prospects, après votre vérification. Les critères Carrière ne s'appliquent pas à cet import.</MessageBar>}
        {busy && <ProgressIndicator label="Opération en cours" />}
        {error && <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar>}
        {success && <MessageBar messageBarType={MessageBarType.success}>{success}</MessageBar>}
        {!verified && autoTried && <Stack tokens={{ childrenGap: 12 }} styles={{ root: { padding: 24, background: "white", borderRadius: 16, boxShadow: "0 3px 18px #10204012" } }}>
          <h2>Relier ce navigateur à Navin</h2>
          <p>Navin n'a pas été trouvé sur cet ordinateur. Si vous utilisez Navin en ligne, entrez son adresse ci-dessous ; sinon, démarrez Navin puis cliquez sur Réessayer.</p>
          <TextField label="Adresse de Navin" placeholder="https://votre-navin.exemple.com ou http://127.0.0.1:8765" value={base} onChange={(_, value) => setBase(value || "")} disabled={busy} />
          <TextField label="Code d'appairage (Navin en ligne uniquement)" description="Pour une instance en ligne, générez un code dans Navin > Extensions. En local, la connexion est automatique."
            value={code} autoComplete="off" onChange={(_, value) => {
              const input = value || "";
              if (input.trim().startsWith("{")) {
                try { const pairing = parseExtensionPairing(input); setBase(pairing.address); setCode(pairing.code); setError(""); }
                catch (err) { setCode(""); setError(String((err as Error).message)); }
              } else setCode(input);
            }} disabled={busy} />
          <p>Navin en ligne : utilisez son adresse HTTPS et un code d'appairage. Navin desktop (Windows, Linux, macOS) : la détection et la connexion sont automatiques.</p>
          <Checkbox label="J'autorise l'analyse des fiches à ma demande par cette instance Navin et leur enregistrement après ma validation. Aucun mot de passe ou cookie des plateformes n'est transmis." checked={consent} onChange={(_, value) => setConsent(!!value)} disabled={busy} />
          <Stack horizontal tokens={{ childrenGap: 8 }}>
            <PrimaryButton text="Relier à Navin" disabled={busy || !consent || !base.trim()} onClick={() => void connect()} />
            <DefaultButton text="Réessayer la détection automatique" disabled={busy} onClick={() => void retryAuto()} />
          </Stack>
        </Stack>}
        {verified && !analysisSupported && <MessageBar messageBarType={MessageBarType.warning}>Votre serveur Navin ne propose pas encore l'analyse par critères. Redémarrez Navin après sa mise à jour, puis rouvrez l'extension. L'import manuel reste disponible.</MessageBar>}
        {verified && analysisSupported && destination !== "leads" && <Stack tokens={{ childrenGap: 12 }} styles={{ root: { padding: 20, background: "#eaf2fc", borderRadius: 12 } }}>
          <h2 style={{ margin: 0 }}>Vos critères dans Navin</h2>
          {criteria && <>
            <span>Recherche : {criteria.criteria.mode === "profiles" ? "Profils candidats" : criteria.criteria.mode === "missions" ? "Missions et emplois" : "Missions et profils"}</span>
            {[["Profils", criteria.profiles], ["Missions", criteria.criteria]].map(([label, value]) => {
              const scope = value as Analysis["criteria"];
              return <div key={String(label)}><strong>{String(label)} : </strong>{[scope.roles?.join(", "), scope.skills?.join(", "), scope.countries?.join(", "), scope.city].filter(Boolean).join(" · ") || "Critères à définir dans Navin"}</div>;
            })}
            <span style={{ fontSize: 12 }}>{[
              criteria.profiles.buy_rate_max ? `TJM d'achat maximum : ${criteria.profiles.buy_rate_max} ${criteria.profiles.currency}` : "",
              criteria.profiles.salary_max ? `Salaire maximum : ${criteria.profiles.salary_max} ${criteria.profiles.currency}/an` : "",
              criteria.profiles.min_score ? `Score minimum : ${criteria.profiles.min_score}%` : "",
              criteria.criteria.max_age_days ? `Ancienneté des offres : ${criteria.criteria.max_age_days} jours` : "",
            ].filter(Boolean).join(" · ")}</span>
            {!criteria.configured && <MessageBar>Définissez vos métiers ou compétences dans Carrière pour obtenir des correspondances pertinentes.</MessageBar>}
          </>}
          <PrimaryButton text={analysis ? "Actualiser les critères et relancer l'analyse" : "Analyser cette page avec mes critères Navin"}
            disabled={busy || !records.length} onClick={() => void analyze()} />
          <span style={{ fontSize: 12 }}>L'analyse transmet les fiches capturées à votre Navin sans les enregistrer. Les informations absentes restent à vérifier.</span>
        </Stack>}
        {connection && verified && <Stack horizontal wrap horizontalAlign="space-between" verticalAlign="center" tokens={{ childrenGap: 12 }}>
          <span><strong>{connection.label}</strong> · {connection.base}</span>
          <Stack horizontal tokens={{ childrenGap: 8 }}>
            <Link href={`${connection.base}/#/${destination === "leads" ? "leads" : "career"}`} target="_blank" rel="noreferrer">Ouvrir Navin</Link>
            <DefaultButton text="Déconnecter l'extension" disabled={busy} onClick={async () => {
              setBusy(true); setError("");
              try { await request(connection.base, connection.token, "disconnect"); await api.storage.local.remove("connection"); setConnection(null); setVerified(false); setAnalysis(null); setCriteria(null); setReviewed(false); }
              catch (err) { setError(String((err as Error).message || err)); }
              finally { setBusy(false); }
            }} />
          </Stack>
        </Stack>}
        {capture.linkedin && <MessageBar messageBarType={MessageBarType.warning}>LinkedIn : seul le texte que vous avez sélectionné est repris. L'extension ne visite pas d'autres profils et ne transmet pas la session. Cela ne garantit pas l'absence de restriction du compte.</MessageBar>}
        {capture.error && <MessageBar>{capture.error}</MessageBar>}
        {capture.profile_list && <MessageBar>{records.length} profil(s) repéré(s) sur la page ouverte. Aucune autre page n'a été visitée.</MessageBar>}
        {capture.unstructured && <MessageBar>Cette page n'a pas de fiche structurée exploitable. Retirez les éléments sans rapport et complétez uniquement les informations publiées.</MessageBar>}
        {capture.truncated && <MessageBar>100 fiches maximum par envoi. Les autres fiches n'ont pas été ajoutées.</MessageBar>}
        {results.filter(result => !["imported", "updated"].includes(result.status)).map(result => <MessageBar key={result.request_id} messageBarType={MessageBarType.warning}>
          {result.error || `Écarté par vos critères : ${reasons[result.reason || ""] || result.reason}. Corrigez la fiche ou vos critères dans Navin.`}
        </MessageBar>)}
        {!!records.length && <div style={{ display: "grid", gridTemplateColumns: "minmax(190px, 1fr) minmax(0, 3fr)", gap: 24 }}>
          <Stack tokens={{ childrenGap: 12 }}>
            <h2 style={{ fontSize: 18 }}>Fiches à envoyer ({records.length})</h2>
            {records.map((item, index) => <Stack key={index} tokens={{ childrenGap: 6 }} styles={{ root: { padding: 12, borderRadius: 12,
              background: active === index ? "#eaf2fc" : "white", border: active === index ? "1px solid #155bba" : "1px solid #dce3eb" } }}>
              <Checkbox label={item.name || item.title || `Fiche ${index + 1}`} checked={selected.includes(index)} disabled={busy || needsAnalysis || ["invalid", "excluded"].includes(match(item)?.status || "")}
                onChange={(_, value) => { setSelected(values => value ? [...values, index] : values.filter(i => i !== index)); setReviewed(false); }} />
              {match(item) && <span>{analysisLabels[match(item)!.status]}{match(item)?.score != null ? ` · ${match(item)!.score}%` : ""}</span>}
              <DefaultButton text="Vérifier cette fiche" disabled={busy} onClick={() => { setActive(index); setEditing(false); }} />
            </Stack>)}
          </Stack>
          {row && <Stack tokens={{ childrenGap: 12 }} styles={{ root: { padding: 24, background: "white", borderRadius: 16, boxShadow: "0 3px 18px #10204012" } }}>
            {activeMatch && <Stack tokens={{ childrenGap: 8 }}>
              <h3 style={{ margin: 0 }}>{analysisLabels[activeMatch.status]}{activeMatch.score != null ? ` (${activeMatch.score}%)` : ""}</h3>
              {activeMatch.error && <MessageBar messageBarType={MessageBarType.warning}>{activeMatch.error}</MessageBar>}
              {activeMatch.reasons?.map((reason, index) => <div key={`${reason.key}-${index}`}>
                <strong>{reasonLabels[reason.key] || reasons[reason.key] || reason.key} : </strong>
                {reason.status === "match" ? "Conforme" : reason.status === "mismatch" ? "Hors critères" : "À vérifier"} · {reasons[reason.detail] || reason.detail}
              </div>)}
            </Stack>}
            {analysis && !editing && activeMatch?.record ? <>
              <h2 style={{ margin: 0 }}>{row.name || row.title}</h2>
              {row.headline && <strong>{row.headline}</strong>}
              <span>Destination : {row.kind === "candidate" ? "Carrière > Vivier de candidats" : ["mission", "job"].includes(row.kind) ? "Carrière > Opportunités" : row.kind === "lead" ? "Leads" : "Tender"}</span>
              <Link href={row.url} target="_blank" rel="noreferrer">Consulter la fiche source</Link>
              <span>{[row.country, row.location, row.skills.join(", "), row.daily_rate_min ? `${row.daily_rate_min} ${row.currency}/jour` : ""].filter(Boolean).join(" · ")}</span>
              <div style={{ whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto", lineHeight: 1.5 }}>{row.description}</div>
              <DefaultButton text="Modifier les champs" disabled={busy} onClick={() => setEditing(true)} />
            </> : <>
            <Dropdown label="Type de fiche" selectedKey={row.kind} options={kinds} disabled={busy} onChange={(_, option) => edit("kind", option?.key as RecordKind)} />
            <p style={{ fontSize: 12, margin: 0 }}>Destination : {row.kind === "lead" ? "Leads" : row.kind === "tender" ? "Tender" : row.kind === "candidate" ? "Carrière > Vivier de candidats" : "Carrière"}</p>
            {field("url", "Adresse source")}
            {["candidate", "lead"].includes(row.kind) ? <>{field("name", "Nom du profil / contact")}{field("headline", "Métier / fonction")}</> : field("title", "Titre de la mission / offre")}
            {field("company", "Entreprise / client", { required: row.kind === "lead" })}
            {field("description", "Description et informations à conserver", { multiline: true })}
            <Stack horizontal wrap tokens={{ childrenGap: 12 }}>{field("country", "Pays (code ISO)", { maxLength: 2 })}{field("location", "Ville / localisation")}</Stack>
            <TextField label="Compétences (séparées par des virgules)" value={row.skills.join(", ")} disabled={busy} onChange={(_, value) => edit("skills", (value || "").split(",").map(skill => skill.trim()).filter(Boolean))} />
            {["candidate", "lead"].includes(row.kind) && <>{field("email", "Email publié", { type: "email" })}{field("phone", "Téléphone publié")}{field("website", "Site de l'entreprise", { type: "url" })}{field("availability", "Disponibilité indiquée (à confirmer)")}</>}
            {row.kind !== "lead" && <>
              <Stack horizontal wrap tokens={{ childrenGap: 12 }}>{field("currency", "Devise (ex. EUR)", { maxLength: 3 })}
                <Dropdown label="Mode de travail" selectedKey={row.remote || ""} disabled={busy} onChange={(_, option) => edit("remote", option?.key)} options={[{ key: "", text: "Non précisé" }, { key: "remote", text: "Full remote" }, { key: "hybrid", text: "Hybride" }, { key: "onsite", text: "Présentiel" }]} /></Stack>
              <Stack horizontal wrap tokens={{ childrenGap: 12 }}>{(row.kind === "job" ? [["salary_min", "Salaire annuel minimum"], ["salary_max", "Salaire annuel maximum"]]
                : row.kind === "tender" ? [["budget_min", "Budget minimum"], ["budget_max", "Budget maximum"]]
                  : [["daily_rate_min", "TJM minimum publié"], ["daily_rate_max", "TJM maximum publié"], ...(row.kind === "mission" ? [["budget_min", "Budget projet minimum"], ["budget_max", "Budget projet maximum"]] : [])]).map(([key, label]) =>
                  <TextField key={key} label={label} type="number" min={0} disabled={busy} value={row[key as keyof ImportRecord] == null ? "" : String(row[key as keyof ImportRecord])}
                    onChange={(_, value) => edit(key as keyof ImportRecord, value ? Number(value) : null)} />)}</Stack>
            </>}
            {["mission", "job", "tender"].includes(row.kind) && <Stack horizontal wrap tokens={{ childrenGap: 12 }}>{field("posted_at", "Date de publication", { type: "date" })}{field("deadline", "Date limite", { type: "date" })}</Stack>}
            {["mission", "tender"].includes(row.kind) && <Dropdown label="Type de besoin" selectedKey={row.need_type || ""} disabled={busy} onChange={(_, option) => edit("need_type", option?.key)}
              options={[{ key: "", text: "Mission / projet" }, ...["rfp", "rfq", "rfi", "eoi", "sow"].map(key => ({ key, text: key.toUpperCase() }))]} />}
            </>}
            <DefaultButton text="Retirer cette fiche" disabled={busy} onClick={() => { const next = records.filter((_, index) => index !== active); setRecords(next); persist(next); setSelected([]); setActive(0); setReviewed(false); setAnalysis(null); }} />
          </Stack>}
        </div>}
        <DefaultButton text="Ajouter une fiche manuellement" disabled={busy || records.length >= 100} onClick={() => {
          const next = [...records, ...routeRecords([blankRecord()], destination)]; setRecords(next); persist(next); setActive(next.length - 1); setReviewed(false); setAnalysis(null); setSelected([]);
        }} />
        {!!records.length && <Stack tokens={{ childrenGap: 12 }} styles={{ root: { padding: 20, background: "#eaf2fc", borderRadius: 12 } }}>
          <Checkbox label={`J'ai vérifié les ${selected.length} fiche(s) sélectionnée(s) et j'autorise leur envoi à Navin.`} checked={reviewed} disabled={busy || needsAnalysis || !selected.length}
            onChange={(_, value) => setReviewed(!!value)} />
          <PrimaryButton text={`Envoyer ${selected.length} fiche(s) vers Navin`} disabled={busy || needsAnalysis || !verified || !reviewed || !selected.length} onClick={() => void send()} />
          <span style={{ fontSize: 12 }}>Les missions et emplois passent les filtres configurés dans Navin. Un email visible reste non vérifié. L'import ne contacte personne.</span>
        </Stack>}
        <footer style={{ fontSize: 12, color: "#526174" }}>Aucune lecture de cookies, aucun mot de passe de plateforme transmis, aucune navigation automatique. Vos captures restent dans cette extension jusqu'à l'envoi et sont effacées à la fermeture du navigateur. <a href="privacy.html" target="_blank" rel="noreferrer">Confidentialité</a></footer>
      </Stack>
    </motion.div>
  </main></ThemeProvider>;
}
