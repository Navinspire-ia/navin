// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { DESK_PANEL_STYLES } from "../desk-panel";
import { useState } from "react";
import { DefaultButton, PrimaryButton, Panel, PanelType, Pivot, PivotItem, TextField, Dropdown, Dialog, DialogType, DialogFooter,
  MessageBar, MessageBarType, ProgressIndicator, Stack, Link } from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";
import type { CareerDesk } from "@/lib/career-api";
import { emptyProspecting, downloadCandidate, sourcingStatusText, type CandidateMatch } from "@/lib/career-prospecting";
import { localizeCareerValue } from "@/lib/career-role-matrix";
import { CareerCompanySetup } from "./CareerCompanySetup";
import { saveArchive } from "@/lib/desk-archive";
import { CareerScene } from "./CareerScene";
import { BUTTON_STYLES, openOfficialCareerUrl } from "./career-ui";

export type ProspectTab = "dashboard" | "criteria" | "profiles" | "matches" | "platforms";
type Run = (action: string, body?: Record<string, unknown>) => Promise<CareerDesk | null>;
export function CareerProspecting({ desk, initialTab, offerId, busy, error, token, onMail, onSchedule, onRun, onDismiss }: {
  desk: CareerDesk; initialTab: ProspectTab; offerId: string; busy: boolean; error: string;
  token: string; onMail: () => void; onSchedule: () => void; onRun: Run; onDismiss: () => void;
}) {
  const { i18n } = useTranslation();
  const fr = i18n.language.startsWith("fr");
  const c = (f: string, e: string) => fr ? f : e;
  const reduced = useReducedMotion();
  const state = desk.prospecting || emptyProspecting;
  const [tab, setTab] = useState<ProspectTab>(initialTab);
  const [filter, setFilter] = useState("");
  const [selectedOffer, setSelectedOffer] = useState(offerId);
  const [editing, setEditing] = useState<CandidateMatch | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [clientEmail, setClientEmail] = useState("");
  const search = async () => {
    const result = await onRun("prospecting_search");
    if (result) {
      setTab("dashboard");
      if (result.prospecting?.criteria.auto_contact || result.prospecting?.criteria.auto_present) await onRun("prospecting_automate");
    }
  };
  const matches = state.matches[selectedOffer];
  const candidateSearchFailed = state.last_run.offer_id === selectedOffer && !!state.last_run.sources?.length
    && state.last_run.sources.every(source => source.status !== "ok");
  const profiles = state.candidates.filter(p => [p.name, p.headline, p.snippet, p.source, ...p.skills].join(" ").toLocaleLowerCase().includes(filter.toLocaleLowerCase()));
  const activeOffers = desk.opportunities.filter(o => !o.archived && o.search_scope?.eligible !== false);
  const selectedMission = activeOffers.find(o => o.id === selectedOffer);
  const dossiers = Object.entries(state.matches).flatMap(([id, group]) => group.results
    .filter(result => activeOffers.some(o => o.id === id) || result.stage !== "discovered")
    .map(result => ({ id, ...result })));
  const stageLabel = (stage: string) => ({ discovered: c("Identifié", "Discovered"), contacted: c("Contacté", "Contacted"),
    qualified: c("Qualifié", "Qualified"), submitted: c("Présenté au client", "Submitted"), interview: c("Entretien", "Interview"),
    placed: c("Placé", "Placed"), contracted: c("Contrat signé", "Contract signed"), rejected: c("Écarté", "Rejected") }[stage] || stage);
  const confirmationLabel = (status: string) => status === "confirmed" ? c("Confirmé", "Confirmed") : status === "declined" ? c("Non", "No") : c("À confirmer", "Unconfirmed");
  const matchLabel = (label: string) => ({ Skills: c("Compétences", "Skills"), Role: c("Métier", "Role"), Country: c("Pays", "Country"), City: c("Ville", "City") } as Record<string, string>)[label] || label;
  const matchDetail = (detail: string) => ({ "Not documented": c("Non documenté", "Not documented"), "To be confirmed": c("À confirmer", "To be confirmed"), "Not requested": c("Non demandé", "Not requested") } as Record<string, string>)[detail] || detail;
  const links = (url: string, label: string) => <Link onClick={() => openOfficialCareerUrl(token, url)}>{label}</Link>;
  const sourceLabel = (source: string) => {
    const [kind, id, country] = source.split(":");
    const name = state.mission_catalog?.find(s => s.id === id)?.name || state.platform_catalog.find(s => s.id === id)?.name || ({ serpapi: "Google", brave: "Brave", public_web: c("Recherche web", "Web search") }[id]) || id;
    return [kind === "profiles" ? c("Profils", "Candidates") : c("Missions", "Missions"), name, country].filter(Boolean).join(" · ");
  };

  return <Panel styles={DESK_PANEL_STYLES} isOpen type={PanelType.large} headerText={c("Carrière · Espace société", "Career · Company workspace")}
    onDismiss={onDismiss} closeButtonAriaLabel={c("Fermer", "Close")} isLightDismiss={false} onOuterClick={() => undefined}>
    <Stack tokens={{ childrenGap: 18 }} styles={{ root: { paddingTop: 12, paddingBottom: 32 } }}>
      <p>{c("Missions et profils, avec ou sans vivier interne. Tous métiers et tous marchés.", "Missions and candidates, with or without an internal talent pool. All professions and markets.")}</p>
      {busy && <ProgressIndicator label={c("Traitement en cours. Chaque source est interrogée séparément.", "Working. Each source is queried independently.")} />}
      {error && <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar>}
      <Pivot overflowBehavior="menu" selectedKey={tab} onLinkClick={item => { setTab(item?.props.itemKey as ProspectTab); }}>
        <PivotItem itemKey="dashboard" headerText={c("Suivi", "Overview")} />
        <PivotItem itemKey="criteria" headerText={c("Configuration", "Setup")} />
        <PivotItem itemKey="profiles" headerText={c("Profils", "Profiles")} itemCount={state.candidates.length} />
        <PivotItem itemKey="matches" headerText={c("Matching et suivi", "Matches and tracking")} />
        <PivotItem itemKey="platforms" headerText={c("Plateformes et API", "Platforms and APIs")} />
      </Pivot>
      <motion.div key={tab} initial={reduced ? false : { opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
        {tab === "dashboard" && <Stack tokens={{ childrenGap: 18 }}>
          <div className="grid gap-4 sm:grid-cols-[1fr_180px]"><div><h2 className="text-2xl font-semibold">{desk.profile.company?.name || c("Votre activité", "Your activity")}</h2>
            <p className="mt-2 text-sm text-muted-foreground">{c("De la recherche au placement, chaque dossier reste au même endroit.", "From search to placement, every dossier stays in one place.")}</p></div>
            <CareerScene matchRatio={dossiers.length ? dossiers.filter(d => d.interest === "confirmed").length / dossiers.length : 0} label={c("Dossiers de placement", "Placement dossiers")} /></div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[
            [c("Missions", "Missions"), activeOffers.length], [c("Profils sauvegardés", "Saved profiles"), state.candidates.length],
            [c("CV reçus", "CVs received"), state.candidates.filter(p => p.cv).length],
            [c("Placements", "Placements"), dossiers.filter(d => ["placed", "contracted"].includes(d.stage)).length],
          ].map(([label, value]) => <div key={label} className="rounded-xl border border-border p-4"><p className="text-2xl font-semibold tabular-nums">{value}</p><p className="text-sm text-muted-foreground">{label}</p></div>)}</div>
          <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
            <PrimaryButton text={c("Lancer la prospection", "Start prospecting")} disabled={busy} onClick={() => void search()} styles={BUTTON_STYLES} />
            <DefaultButton text={c("Synchroniser et traiter les emails", "Sync and process emails")} disabled={busy} onClick={() => void onRun("prospecting_automate")} styles={BUTTON_STYLES} />
            <DefaultButton text={c("Horaires automatiques", "Automatic schedule")} onClick={onSchedule} styles={BUTTON_STYLES} />
          </Stack>
          {!dossiers.length && <MessageBar>{c("Aucun dossier pour le moment. Configurez la société puis lancez la prospection, ou matchez une offre avec votre vivier.", "No dossiers yet. Set up your company and start prospecting, or match an offer against your talent pool.")}</MessageBar>}
          <div data-testid="career-daily-summary" className="rounded-xl border border-border p-4">
            <p className="font-semibold">{c("Recherche quotidienne", "Daily search")} · {desk.loop?.enabled ? c("Active", "Active") : c("En pause", "Paused")}</p>
            {!!desk.loop?.next_due && desk.loop.enabled && <p className="mt-1 text-sm">{c("Prochaine recherche", "Next search")} : {new Date(desk.loop.next_due * 1000).toLocaleString(i18n.language)}</p>}
            {(state.runs || []).length ? <ul className="mt-3 space-y-2 text-sm">{state.runs!.slice(-7).reverse().map((run, index) => <li key={`${run.at}-${index}`}>
              <time>{new Date(run.at * 1000).toLocaleString(i18n.language)}</time>{" · "}
              {run.offers} {c("nouvelles missions", "new missions")}, {run.new_profiles} {c("nouveaux profils", "new profiles")}, {run.matched_offers} {c("missions avec matching", "matched missions")}
              {(run.source_errors > 0 || run.deferred > 0) && <span className="block text-muted-foreground">{c("Recherche partielle : les sources disponibles ont été traitées. Les autres seront retentées.", "Partial search: available sources were processed. Others will be retried.")}</span>}
            </li>)}</ul> : <p className="mt-2 text-sm">{c("Le bilan apparaîtra après la première recherche.", "The summary will appear after the first search.")}</p>}
          </div>
          <div style={{ overflowX: "auto" }}><table className="w-full text-left text-sm"><thead><tr>{[c("Mission", "Mission"), c("Profil", "Candidate"), c("Étape", "Stage"), c("Prochaine action", "Next action")].map(h => <th key={h} className="p-3">{h}</th>)}</tr></thead>
            <tbody>{dossiers.map(d => <tr key={`${d.id}-${d.candidate.id}`} className="border-t border-border">
              <td className="p-3"><Link onClick={() => { setSelectedOffer(d.id); setTab("matches"); }}>{desk.opportunities.find(o => o.id === d.id)?.title || d.id}</Link></td>
              <td className="p-3">{d.candidate.name}<p className="text-xs tabular-nums">{d.score}%</p></td><td className="p-3">{stageLabel(d.stage)}</td>
              <td className="p-3">{d.next_action || c("Qualifier le profil", "Qualify candidate")}</td>
            </tr>)}</tbody></table></div>
        </Stack>}
        {(tab === "criteria" || tab === "platforms") && <CareerCompanySetup key={tab} desk={desk} initialStep={tab === "platforms" ? 3 : 0}
          busy={busy} token={token} run={onRun} onMail={onMail} onSchedule={onSchedule} onFinish={() => setTab("dashboard")} />}
        {tab === "profiles" && <Stack tokens={{ childrenGap: 16 }}>
          <h3 className="text-lg font-semibold">{c("Vivier de candidats", "Candidate pool")} · {state.candidates.length}</h3>
          <p>{c("Profils sauvegardés et réutilisables. Les observations publiques ne constituent ni un CV complet ni une confirmation de disponibilité.", "Saved, reusable profiles. Public observations are neither a complete resume nor confirmed availability.")}</p>
          <TextField label={c("Rechercher dans le vivier", "Search saved candidates")} value={filter} onChange={(_, v) => setFilter(v || "")} />
          <PrimaryButton text={c("Rechercher sur mes plateformes", "Search my platforms")} disabled={busy}
            onClick={() => void onRun("search_candidates", { query: filter })} styles={BUTTON_STYLES} />
          {!profiles.length && <MessageBar>{c("Aucun profil pour cette recherche. Lancez une prospection ou modifiez le filtre.", "No profiles for this search. Start prospecting or change the filter.")}</MessageBar>}
          <div style={{ overflowX: "auto" }}><table className="w-full text-left text-sm" data-testid="career-candidate-list">
            <thead><tr>{[c("Profil", "Profile"), c("Source / fraîcheur", "Source / freshness"), c("Signal", "Signal"), "Actions"].map(h => <th key={h} className="p-3">{h}</th>)}</tr></thead>
            <tbody>{profiles.map(p => <tr key={p.id} className="border-t border-border">
              <td className="p-3"><strong>{p.name}</strong><p className="mt-1 max-w-lg">{p.snippet}</p>{p.skills.join(", ")}
                {p.email && <p><a href={`mailto:${p.email}`}>{p.email}</a></p>}{p.phone && <p><a href={`tel:${p.phone}`}>{p.phone}</a></p>}</td>
              <td className="p-3">{p.source}<p>{new Date(p.observed_at * 1000).toLocaleDateString(i18n.language)}</p></td>
              <td className="p-3">{p.signal === "declared" ? c("Disponibilité évoquée, à confirmer", "Availability mentioned, verify") : c("À confirmer", "Unconfirmed")}</td>
              <td className="p-3"><Stack tokens={{ childrenGap: 8 }}>
                {p.url && links(p.url, c("Voir le profil", "View profile"))}
                <DefaultButton text={c("Télécharger la fiche", "Download profile sheet")} onClick={() => downloadCandidate(p)} styles={BUTTON_STYLES} />
                {p.cv && <DefaultButton text={c("Télécharger le CV reçu", "Download received CV")} disabled={busy} styles={BUTTON_STYLES}
                  onClick={async () => { const result = await onRun("candidate_cv", { id: p.id }); if (result?.candidate_file) saveArchive(result.candidate_file); }} />}
                {p.dossier && <DefaultButton text={c("Dossier de compétences", "Skills dossier")} disabled={busy} styles={BUTTON_STYLES}
                  onClick={async () => { const result = await onRun("candidate_cv", { id: p.id, document: "dossier" }); if (result?.candidate_file) saveArchive(result.candidate_file); }} />}
                <DefaultButton text={c("Supprimer le profil", "Delete profile")} iconProps={{ iconName: "Delete" }} disabled={busy}
                  onClick={() => setDeleteTarget({ id: p.id, name: p.name })} styles={BUTTON_STYLES} />
              </Stack></td>
            </tr>)}</tbody></table></div>
        </Stack>}
        {tab === "matches" && <Stack tokens={{ childrenGap: 16 }}>
          <Dropdown label={c("Mission / projet client", "Mission / client project")} selectedKey={selectedOffer} options={activeOffers.map(o => ({ key: o.id, text: `${o.title}${o.company ? ` · ${o.company}` : ""}` }))}
            onChange={(_, o) => { setSelectedOffer(String(o?.key || "")); setEditing(null); }} placeholder={c("Choisir une offre", "Select an offer")} />
          <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
            <PrimaryButton text={c("Rechercher des profils", "Search candidates")} disabled={busy || !selectedOffer} onClick={() => void onRun("prospecting_match", { id: selectedOffer })} styles={BUTTON_STYLES} />
            <DefaultButton text={c("Matcher mon vivier", "Match saved candidates")} disabled={busy || !selectedOffer} onClick={() => void onRun("prospecting_reuse", { id: selectedOffer })} styles={BUTTON_STYLES} />
          </Stack>
          {selectedMission?.url && <div className="rounded-xl border border-border p-3">
            {links(selectedMission.url, c("Ouvrir l'annonce d'origine", "Open original listing"))}
            <p className="mt-1 break-all text-xs text-muted-foreground">{selectedMission.url}</p>
          </div>}
          {(activeOffers.find(o => o.id === selectedOffer)?.source_links?.length || 0) > 1 && <Stack horizontal wrap tokens={{ childrenGap: 12 }}>
            <span>{c("Cette mission apparaît sur", "This mission also appears on")} :</span>
            {activeOffers.find(o => o.id === selectedOffer)?.source_links?.map(link => <span key={link.url}>{links(link.url, state.mission_catalog?.find(s => s.id === link.source)?.name || link.source || c("Source", "Source"))}</span>)}
          </Stack>}
          {!selectedOffer && <MessageBar>{c("Choisissez une offre existante ou lancez la prospection. Les offres sont conservées dans Carrière.", "Choose an existing offer or start prospecting. Offers are stored in Career.")}</MessageBar>}
          {selectedOffer && !matches?.results.length && <MessageBar messageBarType={candidateSearchFailed ? MessageBarType.warning : MessageBarType.info}>{candidateSearchFailed
            ? c("La recherche de profils n'a pas pu aboutir. Consultez les erreurs des sources ci-dessous avant de conclure à une absence de candidats.", "Candidate search could not complete. Check source errors below before concluding that no candidates exist.")
            : matches
            ? c("Aucun profil correspondant dans les résultats disponibles. Consultez l'état des sources ci-dessous.", "No matching profiles in the available results. Check source status below.")
            : c("Aucun profil correspondant enregistré pour cette offre. Recherchez sur les plateformes ou dans le vivier.", "No saved matches for this offer. Search platforms or your candidate pool.")}</MessageBar>}
          {matches && <p className="text-sm text-muted-foreground">{c("Dernier matching", "Last matched")} : {new Date(matches.searched_at * 1000).toLocaleString(i18n.language)}. {c("Score de correspondance documentaire, pas une probabilité de placement.", "Evidence match score, not a placement probability.")}</p>}
          {matches?.results.map(match => <Stack key={match.candidate.id} tokens={{ childrenGap: 10 }} styles={{ root: { padding: 18, borderRadius: 12, border: "1px solid rgba(128,128,128,.25)" } }}>
            <div className="flex items-start justify-between gap-4"><strong>{match.candidate.name}</strong><strong className="tabular-nums">{match.score}%</strong></div>
            <p>{match.candidate.source} · {c("Observé le", "Observed")} {new Date(match.candidate.observed_at * 1000).toLocaleDateString(i18n.language)}</p>
            <p>{match.candidate.snippet}</p>
            <div className="grid gap-2 sm:grid-cols-2">{match.reasons.map(r => <p key={r.label} className="text-sm"><strong>{matchLabel(r.label)} : {r.points}/{r.max}</strong><br />{matchDetail(r.detail)}</p>)}</div>
            {match.missing_skills.length > 0 && <p>{c("Compétences à vérifier", "Skills to verify")} : {match.missing_skills.join(", ")}</p>}
            <p>{stageLabel(match.stage)} · {c("Intérêt", "Interest")} : {confirmationLabel(match.interest)} · {c("Disponibilité", "Availability")} : {confirmationLabel(match.availability)}</p>
            {match.next_action && <MessageBar>{match.next_action}</MessageBar>}
            {match.availability_detail && <p>{match.availability_detail}</p>}
            {match.interview?.slot && <p><strong>{c("Entretien", "Interview")} : </strong>{new Date(match.interview.slot).toLocaleString(i18n.language)} ({Intl.DateTimeFormat().resolvedOptions().timeZone})</p>}
            {match.preparation && <details><summary className="cursor-pointer">{c("Préparation de l'entretien", "Interview preparation")}</summary><p className="whitespace-pre-wrap text-sm">{match.preparation}</p></details>}
            {match.last_reply && <details><summary className="cursor-pointer">{c("Dernière réponse", "Latest reply")}</summary><p className="whitespace-pre-wrap text-sm">{match.last_reply.text}</p></details>}
            {(match.emails || [match.candidate_mail, match.client_mail].filter(Boolean)).map((mail, index) => <details key={index}><summary className="cursor-pointer">Email : {mail?.recipient} · {mail?.status}</summary><p className="whitespace-pre-wrap text-sm">{mail?.body}</p></details>)}
            <Stack horizontal wrap tokens={{ childrenGap: 10 }}>
              {match.candidate.url && links(match.candidate.url, c("Voir / contacter sur la plateforme", "View / contact on platform"))}
              <DefaultButton text={c("Mettre à jour le dossier", "Update dossier")} disabled={busy} onClick={() => { setEditing({ ...match }); setClientEmail(desk.opportunities.find(o => o.id === selectedOffer)?.application_email || ""); }} styles={BUTTON_STYLES} />
              <DefaultButton text={c("Télécharger la fiche", "Download sheet")} onClick={() => downloadCandidate(match.candidate)} styles={BUTTON_STYLES} />
            </Stack>
            {editing?.candidate.id === match.candidate.id && <Stack tokens={{ childrenGap: 12 }}>
              <MessageBar>{c("Renseignez les confirmations obtenues lors de votre échange. Aucun message n'est envoyé par ce formulaire.", "Record confirmations obtained during your conversation. This form sends no messages.")}</MessageBar>
              {(["interest", "availability"] as const).map(key => <Dropdown key={key} label={key === "interest" ? c("Intérêt pour cette mission", "Interest in this mission") : c("Disponibilité pour cette mission", "Availability for this mission")}
                selectedKey={editing[key]} onChange={(_, o) => setEditing({ ...editing, [key]: String(o?.key) })} options={[
                  { key: "unknown", text: c("À confirmer", "Unconfirmed") }, { key: "confirmed", text: c("Confirmé par le candidat", "Confirmed by candidate") }, { key: "declined", text: c("Non", "No") }]} />)}
              <Dropdown label={c("Étape", "Stage")} selectedKey={editing.stage} onChange={(_, o) => setEditing({ ...editing, stage: String(o?.key) })}
                options={[["discovered", "Identifié", "Discovered"], ["contacted", "Contacté", "Contacted"], ["qualified", "Qualifié", "Qualified"], ["submitted", "Présenté au client", "Submitted to client"], ["interview", "Entretien", "Interview"], ["placed", "Placé", "Placed"], ["contracted", "Contrat signé", "Contract signed"], ["rejected", "Écarté", "Rejected"]].map(([key, f, e]) => ({ key, text: c(f, e) }))} />
              <TextField label={c("Notes de l'échange", "Conversation notes")} multiline value={editing.note} onChange={(_, v) => setEditing({ ...editing, note: v || "" })} />
              <TextField label={c("Email du candidat", "Candidate email")} type="email" value={editing.candidate.email || ""} onChange={(_, v) => setEditing({ ...editing, candidate: { ...editing.candidate, email: v || "" } })} />
              <TextField label={c("Email du client", "Client email")} type="email" value={clientEmail} onChange={(_, v) => setClientEmail(v || "")} />
              <TextField label={c("TJM d'achat confirmé", "Confirmed daily buying rate")} type="number" min={0} value={editing.purchase_rate ? String(editing.purchase_rate) : ""} onChange={(_, v) => setEditing({ ...editing, purchase_rate: Number(v || 0) })} />
              <TextField label={c("Référence du contrat signé", "Signed contract reference")} value={editing.contract} onChange={(_, v) => setEditing({ ...editing, contract: v || "" })} />
              <PrimaryButton text={c("Enregistrer le suivi", "Save tracking")} disabled={busy} onClick={async () => {
                const result = await onRun("prospecting_dossier", { id: selectedOffer, candidate_id: editing.candidate.id,
                  candidate_email: editing.candidate.email, client_email: clientEmail, purchase_rate: editing.purchase_rate || 0,
                  stage: editing.stage, interest: editing.interest, availability: editing.availability, note: editing.note, contract: editing.contract });
                if (result) setEditing(null);
              }} styles={BUTTON_STYLES} />
            </Stack>}
          </Stack>)}
        </Stack>}
      </motion.div>
      {state.last_run.at && <details open={candidateSearchFailed || undefined}><summary className="cursor-pointer">{c("Dernière recherche : voir l'état des sources", "Last search: view source status")}</summary><Stack tokens={{ childrenGap: 6 }}>
        {!!state.last_run.deferred && <MessageBar>{state.last_run.deferred} {c("recherches de sources/pays restent à parcourir au prochain lancement.", "source/country searches remain for the next run.")}</MessageBar>}
        {state.last_run.sources?.map(s => <MessageBar key={s.source} messageBarType={s.status === "ok" ? MessageBarType.info : MessageBarType.warning}>
          {[sourceLabel(s.source), state.platform_catalog.find(p => p.id === s.platform)?.name,
            s.role && localizeCareerValue(s.role, i18n.language)].filter(Boolean).join(" · ")} : {sourcingStatusText(s, i18n.language)}
          {s.status === "not_configured" && <> <Link onClick={() => setTab("platforms")}>{c("Configurer les sources", "Configure sources")}</Link></>}
          {Object.entries(s.rejected || {}).filter(([, count]) => count > 0).map(([reason, count]) => <p key={reason}>
            {count} {c("écarté(s)", "excluded")} : {({ country: c("pays hors périmètre ou inconnu", "country outside scope or unknown"), role: c("métier différent", "different role"), track: c("type de contrat différent", "different engagement"),
              rate: c("TJM inférieur au minimum", "day rate below minimum"), rate_unknown: c("TJM non vérifié", "unverified day rate"), work_mode: c("mode de travail différent", "different work mode"),
              work_mode_unknown: c("mode de travail non précisé", "unspecified work mode"), date: c("date hors de la période autorisée", "date outside allowed period"), date_unknown: c("date de publication inconnue", "unknown publication date"),
              platform: c("page hors des profils de la plateforme sélectionnée", "page outside the selected platform's profiles"),
              availability_unknown: c("aucun signal public de disponibilité", "no public availability signal"),
              unavailable: c("indisponibilité déclarée", "declared unavailable"), evidence: c("extrait de recherche ambigu", "ambiguous search excerpt") } as Record<string, string>)[reason] || reason}
          </p>)}
          {s.retry_at ? ` ${c("Nouvel essai possible à", "Retry after")} ${new Date(s.retry_at * 1000).toLocaleTimeString(i18n.language)}` : ""}
        </MessageBar>)}
      </Stack></details>}
    </Stack>
    <Dialog hidden={!deleteTarget} onDismiss={() => { if (!busy) setDeleteTarget(null); }}
      modalProps={{ isBlocking: true }} dialogContentProps={{ type: DialogType.normal,
        closeButtonAriaLabel: c("Fermer", "Close"),
        title: c("Supprimer ce profil ?", "Delete this profile?"),
        subText: c(`Le profil de ${deleteTarget?.name || ""}, ses documents enregistrés et ses correspondances avec les missions seront supprimés. Cette action est définitive.`,
          `${deleteTarget?.name || ""}'s profile, saved documents and mission matches will be deleted. This cannot be undone.`) }}>
      {error && <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar>}
      <DialogFooter>
        <DefaultButton text={c("Annuler", "Cancel")} disabled={busy} onClick={() => setDeleteTarget(null)} />
        <PrimaryButton text={c("Supprimer", "Delete")} disabled={busy || !deleteTarget} onClick={async () => {
          if (!deleteTarget) return;
          const result = await onRun("prospecting_delete_candidate", { id: deleteTarget.id, confirmed: true });
          if (result) { if (editing?.candidate.id === deleteTarget.id) setEditing(null); setDeleteTarget(null); }
        }} />
      </DialogFooter>
    </Dialog>
  </Panel>;
}
