// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { DESK_PANEL_STYLES } from "./desk-panel";
import { useState } from "react";
import { Checkbox, DefaultButton, Panel, PanelType, PrimaryButton, MessageBar, MessageBarType, Stack } from "@fluentui/react";
import { useTranslation } from "react-i18next";
import { saveArchive, type ArchiveFile, type DeskArchive } from "@/lib/desk-archive";

export function DeskReset({ open, module, archives, busy, error, onDismiss, onReset, onDownload }: {
  open: boolean; module: "career" | "tenders"; archives: DeskArchive[]; busy: boolean; error: string;
  onDismiss: () => void; onReset: () => Promise<boolean>; onDownload: (id: string) => Promise<ArchiveFile | undefined>;
}) {
  const { i18n } = useTranslation();
  const fr = i18n.language.startsWith("fr");
  const c = (f: string, e: string) => fr ? f : e;
  const career = module === "career";
  const [confirmed, setConfirmed] = useState(false);
  return <Panel styles={DESK_PANEL_STYLES} isOpen={open} type={PanelType.medium} onDismiss={() => { setConfirmed(false); onDismiss(); }}
    headerText={career ? c("Vider les offres", "Clear offers") : c("Recommencer à zéro", "Start over")} closeButtonAriaLabel={c("Fermer", "Close")} isLightDismiss={!busy}>
    <Stack tokens={{ childrenGap: 20 }} styles={{ root: { paddingTop: 20 } }}>
      <MessageBar messageBarType={MessageBarType.warning}>{career ? c(
        "Les offres, candidatures et historiques de recherche seront déplacés dans une archive. Les recherches programmées seront mises en pause.",
        "Offers, applications and search history will move into an archive. Scheduled searches will be paused.") : c(
        "Les offres, dossiers, documents, réglages et historiques actuels seront déplacés dans une archive. Les recherches programmées seront arrêtées. L'espace redémarrera à la configuration initiale.",
        "Current offers, dossiers, documents, settings and history will move into an archive. Scheduled searches will stop. The workspace will return to initial setup.")}</MessageBar>
      <p>{career ? c(
        "La configuration de votre société, votre profil personnel, votre CV, vos critères de recherche, vos plateformes et vos clés API sont conservés. Le vivier reste disponible ; chaque profil peut être supprimé séparément.",
        "Your company settings, personal profile, CV, search criteria, platforms and API keys are kept. Saved candidates remain available; each candidate can be deleted separately.") : c("Les clés API restent enregistrées.", "API keys stay saved.")}</p>
      {error && <MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar>}
      <Checkbox checked={confirmed} disabled={busy} onChange={(_, v) => setConfirmed(!!v)} label={career ? c("Vider les offres en conservant ma configuration", "Clear offers and keep my configuration") : c("Archiver cet espace et recommencer à zéro", "Archive this workspace and start over")} />
      <PrimaryButton disabled={busy || !confirmed} text={career ? c("Archiver et vider les offres", "Archive and clear offers") : c("Archiver et recommencer", "Archive and restart")} onClick={async () => { if (await onReset()) setConfirmed(false); }} />
      <h3 className="text-lg font-semibold">{c("Archives conservées", "Saved archives")} ({archives.length})</h3>
      {archives.map(archive => <Stack key={archive.id} tokens={{ childrenGap: 8 }}>
        <strong>{new Date(archive.at * 1000).toLocaleString(i18n.language)}</strong>
        <p className="break-all text-xs text-muted-foreground">{archive.path}</p>
        <DefaultButton text={c("Télécharger l'archive", "Download archive")} disabled={busy} onClick={async () => { const file = await onDownload(archive.id); if (file) saveArchive(file); }} />
      </Stack>)}
    </Stack>
  </Panel>;
}
