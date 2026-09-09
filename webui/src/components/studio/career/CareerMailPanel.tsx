// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  DefaultButton, Dropdown, MessageBar, MessageBarType, Panel, PanelType,
  PrimaryButton, ProgressIndicator, TextField, Toggle,
} from "@fluentui/react";
import { DocumentGenerationNotice } from "@/components/studio/DocumentGenerationNotice";
import type {
  CareerDesk, CareerMailDraft, CareerMailbox, CareerMailboxStatus, CareerMailReceipt, CareerProfile,
} from "@/lib/career-api";

const BUTTON = { root: { minHeight: 40 } };
type MailAction = (action: string, body?: Record<string, unknown>) => Promise<CareerDesk | null>;
const DEFAULT_MAILBOX: CareerMailbox = {
  enabled: false, sender_name: "", sender_email: "", smtp_host: "", smtp_port: 587,
  smtp_security: "starttls", smtp_username: "", imap_host: "", imap_port: 993,
  imap_security: "ssl", imap_username: "", imap_folder: "INBOX", read_replies: false,
  auto_send: false, min_match_score: 85, max_per_day: 3, poll_interval_minutes: 5,
  allowed_recipient_domains: [],
};

const ERRORS: Record<string, [string, string]> = {
  mail_recipient_invalid: ["Indiquez une seule adresse email valide.", "Enter one valid email address."],
  mail_account_disabled: ["Activez le compte de courrier professionnel dans les réglages.", "Enable the professional mail account in settings."],
  mail_config_required: ["Renseignez les paramètres du compte.", "Enter your account settings."],
  mail_tls_required: ["Choisissez TLS ou STARTTLS pour ce compte.", "Choose TLS or STARTTLS for this account."],
  mail_header_invalid: ["Le nom et le dossier doivent tenir sur une seule ligne.", "The name and folder must each fit on one line."],
  mail_smtp_host_required: ["Indiquez le serveur SMTP.", "Enter the SMTP server."],
  mail_smtp_username_required: ["Indiquez l'identifiant SMTP.", "Enter the SMTP username."],
  mail_imap_host_required: ["Indiquez le serveur IMAP.", "Enter the IMAP server."],
  mail_imap_username_required: ["Indiquez l'identifiant IMAP.", "Enter the IMAP username."],
  mail_smtp_password_required: ["Enregistrez le mot de passe SMTP.", "Save the SMTP password."],
  mail_imap_password_required: ["Enregistrez le mot de passe IMAP.", "Save the IMAP password."],
  mail_authentication_failed: ["Authentification refusée. Vérifiez l'identifiant et le mot de passe d'application.", "Authentication was refused. Check the username and app password."],
  mail_connection_failed: ["Connexion impossible au serveur de courrier. Vérifiez l'adresse et le port.", "Could not connect to the mail server. Check the host and port."],
  mail_tls_failed: ["La connexion TLS n'a pas pu être vérifiée.", "The TLS connection could not be verified."],
  mail_tls_or_auth_unsupported: ["Le serveur ne propose pas le protocole de connexion choisi.", "The server does not support the selected connection method."],
  mail_prepare_documents_first: ["Préparez le CV et la lettre pour cette offre avant de composer le courrier.", "Prepare the CV and cover letter for this offer before composing the email."],
  mail_document_error: ["Les documents n'ont pas pu être préparés. Vérifiez le profil et relancez la préparation.", "The documents could not be prepared. Check the profile and prepare them again."],
  mail_attachment_invalid: ["Une pièce jointe Word est invalide. Préparez à nouveau les documents.", "A Word attachment is invalid. Prepare the documents again."],
  mail_attachments_too_large: ["Les pièces jointes dépassent la limite de 15 Mo.", "The attachments exceed the 15 MB limit."],
  mail_draft_changed: ["Le contenu a changé. Actualisez l'aperçu avant l'envoi.", "The content changed. Refresh the preview before sending."],
  mail_documents_need_review: ["Relisez le CV et la lettre, puis confirmez leur relecture.", "Review the CV and cover letter, then confirm your review."],
  mail_sender_refused: ["Le serveur refuse cette adresse d'expédition.", "The server refused this sender address."],
  mail_recipient_refused: ["Le serveur a refusé le destinataire. Aucun envoi confirmé.", "The server refused the recipient. No send was confirmed."],
  mail_message_refused: ["Le serveur a refusé le message. Aucun envoi confirmé.", "The server refused the message. No send was confirmed."],
  mail_acceptance_unknown: ["La connexion a coupé avant la confirmation. Vérifiez votre messagerie et le serveur avant toute nouvelle candidature à cette offre.", "The connection ended before confirmation. Check your mailbox and server before applying to this offer again."],
  mail_busy: ["Une opération de courrier est déjà en cours.", "A mail operation is already running."],
  mail_configuration_changed: ["Les réglages ont changé pendant l'opération. Actualisez l'aperçu.", "Settings changed during the operation. Refresh the preview."],
  mail_imap_folder_unavailable: ["Le dossier IMAP est introuvable ou inaccessible.", "The IMAP folder could not be opened."],
  mail_imap_search_failed: ["La recherche des réponses a échoué. Vous pouvez relancer la relève.", "Searching for replies failed. You can sync again."],
  mail_imap_fetch_failed: ["Une réponse n'a pas pu être lue. La prochaine relève reprendra au même point.", "A reply could not be read. The next sync will resume at the same point."],
  mail_imap_command_failed: ["Le serveur IMAP a refusé une opération. Vérifiez le compte et le dossier.", "The IMAP server refused an operation. Check the account and folder."],
  mail_imap_uidvalidity_missing: ["Le serveur n'a pas fourni l'identifiant stable du dossier IMAP.", "The server did not provide the IMAP folder's stable identifier."],
  mail_loop_paused: ["La boucle est en pause. Aucun envoi automatique n'a été lancé.", "The loop is paused. No automatic send was started."],
  mail_daily_limit_reached: ["Le plafond quotidien de candidatures est atteint.", "The daily application limit has been reached."],
  mail_auto_send_disabled: ["L'envoi automatique est désactivé.", "Automatic sending is disabled."],
  mail_reply_sync_disabled: ["Activez la relève des réponses dans les réglages du compte.", "Enable reply sync in the account settings."],
  mail_notification_failed: ["La notification reste en attente de confirmation.", "The notification is still awaiting confirmation."],
  mail_retry_paused: ["Les nouvelles tentatives sont suspendues. Vérifiez le compte et le dernier échec.", "Retries are paused. Check the account and the last failure."],
};

export function careerMailError(error: string, french: boolean): string {
  const code = error.match(/\bmail_[a-z_]+\b/)?.[0];
  if (!code) return error;
  return ERRORS[code]?.[french ? 0 : 1] || (french ? `Opération de courrier interrompue (${code}).` : `Mail operation stopped (${code}).`);
}

export function CareerMailReceiptView({ receipt, french }: { receipt: CareerMailReceipt; french: boolean }) {
  const accepted = receipt.status === "accepted";
  return (
    <div className="grid gap-2 text-sm" data-testid="career-mail-receipt" data-status={receipt.status}>
      <MessageBar messageBarType={accepted ? MessageBarType.success : receipt.status === "failed" ? MessageBarType.error : MessageBarType.warning}>
        {accepted
          ? (french ? "Candidature acceptée par le serveur de courrier." : "Application accepted by the mail server.")
          : careerMailError(receipt.error || "mail_acceptance_unknown", french)}
      </MessageBar>
      <p>{receipt.recipient}{receipt.accepted_at ? ` · ${new Date(receipt.accepted_at * 1000).toLocaleString(french ? "fr-FR" : "en-GB")}` : ""}</p>
      <p className="break-all font-mono text-xs">Message-ID: {receipt.message_id}</p>
      {accepted ? <p className="text-muted-foreground">{french ? "La livraison et la réponse du recruteur seront suivies à partir des messages reçus." : "Delivery reports and recruiter replies will be tracked from incoming messages."}</p> : null}
      {receipt.delivery_status === "delivery_report" ? <MessageBar messageBarType={MessageBarType.warning}>{french ? "Un rapport de livraison est disponible dans la boîte de réception." : "A delivery report is available in your inbox."}</MessageBar> : null}
    </div>
  );
}

export function CareerMailAccountSummary({ profile, status, french, busy, onSettings, onSync }: {
  profile: CareerProfile; status?: CareerMailboxStatus; french: boolean; busy: boolean;
  onSettings: () => void; onSync: () => void;
}) {
  const config = profile.mailbox;
  const sync = status?.sync;
  return (
    <section className="mb-6 grid gap-4 rounded-2xl p-6 outline outline-1 outline-black/10 dark:outline-white/10" data-testid="career-mail-account">
      <div>
        <h2 className="text-lg font-semibold">{french ? "Courrier professionnel" : "Professional mail"}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{config?.enabled ? config.sender_email : (french ? "Connectez le compte qui signe vos candidatures et reçoit les réponses." : "Connect the account that sends your applications and receives replies.")}</p>
      </div>
      {config?.enabled ? (
        <dl className="grid gap-3 text-sm sm:grid-cols-3">
          <div><dt className="text-muted-foreground">{french ? "Envoi automatique" : "Automatic sending"}</dt><dd>{config.auto_send ? `${config.max_per_day} / ${french ? "jour" : "day"}, score ≥ ${config.min_match_score}` : (french ? "Désactivé" : "Disabled")}</dd></div>
          <div><dt className="text-muted-foreground">{french ? "Relève des réponses" : "Reply sync"}</dt><dd>{config.read_replies ? `${config.poll_interval_minutes} min` : (french ? "Désactivée" : "Disabled")}</dd></div>
          <div><dt className="text-muted-foreground">{french ? "Acceptées par SMTP" : "Accepted by SMTP"}</dt><dd>{status?.accepted || 0}</dd></div>
        </dl>
      ) : null}
      {sync?.checked_at ? <p className="text-xs text-muted-foreground">{french ? "Dernière relève" : "Last sync"}: {new Date(sync.checked_at * 1000).toLocaleString(french ? "fr-FR" : "en-GB")} · {sync.received || 0} {french ? "nouvelle(s) réponse(s)" : "new replies"}</p> : null}
      {sync?.error ? <MessageBar messageBarType={MessageBarType.error}>{careerMailError(sync.error, french)}</MessageBar> : null}
      {(sync?.skipped_large || 0) > 0 ? <MessageBar messageBarType={MessageBarType.warning}>{french ? "Un message volumineux reste à consulter dans votre messagerie." : "A large message remains available in your mail app."}</MessageBar> : null}
      {(status?.pending_notifications || 0) > 0 ? <p className="text-sm text-muted-foreground">{status?.pending_notifications} {french ? "notification(s) en attente de confirmation." : "notification(s) awaiting confirmation."}</p> : null}
      <div className="flex flex-wrap gap-2">
        <DefaultButton styles={BUTTON} text={french ? "Configurer le compte" : "Account settings"} iconProps={{ iconName: "Mail" }} onClick={onSettings} data-testid="career-mail-settings-open" />
        <DefaultButton styles={BUTTON} text={french ? "Relever les réponses" : "Sync replies"} iconProps={{ iconName: "Sync" }} disabled={busy || !config?.enabled || !config.read_replies} onClick={onSync} data-testid="career-mail-sync" />
      </div>
    </section>
  );
}

export function CareerMailSettings({ open, profile, status, french, busy, error, loopEnabled, onDismiss, onSchedule, run }: {
  open: boolean; profile: CareerProfile; status?: CareerMailboxStatus; french: boolean; busy: boolean;
  error: string; loopEnabled: boolean; onDismiss: () => void; onSchedule: () => void; run: MailAction;
}) {
  const [form, setForm] = useState<CareerMailbox>({ ...DEFAULT_MAILBOX, sender_name: profile.display_name || "", sender_email: profile.email || "", ...profile.mailbox });
  const [smtpPassword, setSmtpPassword] = useState("");
  const [imapPassword, setImapPassword] = useState("");
  const [saved, setSaved] = useState(false);
  const set = <K extends keyof CareerMailbox>(key: K, value: CareerMailbox[K]) => { setSaved(false); setForm((current) => ({ ...current, [key]: value })); };
  const save = async () => {
    const result = await run("mail_config", { mailbox: form, smtp_password: smtpPassword, imap_password: imapPassword });
    if (result) { setSmtpPassword(""); setImapPassword(""); setSaved(true); }
    return result;
  };
  const test = async () => { if (await save()) await run("mail_test"); };
  const text = (fr: string, en: string) => french ? fr : en;
  const reduced = useReducedMotion();
  return (
    <Panel isOpen={open} onDismiss={onDismiss} isLightDismiss={!busy} type={PanelType.medium} headerText={text("Courrier professionnel", "Professional mail")} closeButtonAriaLabel={text("Fermer", "Close")}>
      <div className="grid gap-5 pb-8" data-testid="career-mail-settings">
        <p className="text-sm text-muted-foreground">{text("Utilisez les paramètres SMTP/IMAP de votre messagerie et, si requis, son mot de passe d'application.", "Use your mail provider's SMTP/IMAP settings and its app password, if required.")}</p>
        <Toggle label={text("Activer ce compte pour les candidatures", "Enable this account for applications")} checked={form.enabled} onChange={(_, checked) => set("enabled", Boolean(checked))} disabled={busy} />
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label={text("Nom de l'expéditeur", "Sender name")} value={form.sender_name} onChange={(_, value) => set("sender_name", value || "")} disabled={busy} />
          <TextField label={text("Adresse professionnelle", "Professional email")} type="email" value={form.sender_email} onChange={(_, value) => set("sender_email", value || "")} disabled={busy} />
        </div>
        <fieldset className="grid gap-3 border-0 p-0">
          <legend className="mb-2 font-semibold">{text("Envoi SMTP", "SMTP sending")}</legend>
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField label={text("Serveur SMTP", "SMTP server")} value={form.smtp_host} onChange={(_, value) => set("smtp_host", value || "")} disabled={busy} placeholder="smtp.example.com" />
            <TextField label={text("Port SMTP", "SMTP port")} type="number" min={1} max={65535} value={String(form.smtp_port)} onChange={(_, value) => set("smtp_port", Number(value || 587))} disabled={busy} />
            <Dropdown label={text("Sécurité SMTP", "SMTP security")} selectedKey={form.smtp_security} options={[{ key: "starttls", text: "STARTTLS" }, { key: "ssl", text: "TLS" }]} onChange={(_, option) => option && set("smtp_security", option.key as "ssl" | "starttls")} disabled={busy} />
            <TextField label={text("Identifiant SMTP", "SMTP username")} value={form.smtp_username} onChange={(_, value) => set("smtp_username", value || "")} disabled={busy} autoComplete="username" />
          </div>
          <TextField label={text("Mot de passe SMTP", "SMTP password")} type="password" autoComplete="new-password" value={smtpPassword} onChange={(_, value) => { setSaved(false); setSmtpPassword(value || ""); }} disabled={busy} placeholder={status?.smtp_password_set ? text("Enregistré. Laisser vide pour le conserver.", "Saved. Leave blank to keep it.") : ""} />
        </fieldset>
        <fieldset className="grid gap-3 border-0 p-0">
          <legend className="mb-2 font-semibold">{text("Réception IMAP", "IMAP replies")}</legend>
          <Toggle label={text("Relever les réponses à mes candidatures", "Sync replies to my applications")} checked={form.read_replies} onChange={(_, checked) => set("read_replies", Boolean(checked))} disabled={busy} />
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField label={text("Serveur IMAP", "IMAP server")} value={form.imap_host} onChange={(_, value) => set("imap_host", value || "")} disabled={busy} placeholder="imap.example.com" />
            <TextField label={text("Port IMAP", "IMAP port")} type="number" min={1} max={65535} value={String(form.imap_port)} onChange={(_, value) => set("imap_port", Number(value || 993))} disabled={busy} />
            <Dropdown label={text("Sécurité IMAP", "IMAP security")} selectedKey={form.imap_security} options={[{ key: "ssl", text: "TLS" }, { key: "starttls", text: "STARTTLS" }]} onChange={(_, option) => option && set("imap_security", option.key as "ssl" | "starttls")} disabled={busy} />
            <TextField label={text("Identifiant IMAP", "IMAP username")} value={form.imap_username} onChange={(_, value) => set("imap_username", value || "")} disabled={busy} />
            <TextField label={text("Dossier", "Folder")} value={form.imap_folder} onChange={(_, value) => set("imap_folder", value || "")} disabled={busy} />
            <TextField label={text("Relève toutes les (minutes)", "Sync every (minutes)")} type="number" min={1} max={60} value={String(form.poll_interval_minutes)} onChange={(_, value) => set("poll_interval_minutes", Number(value || 5))} disabled={busy} />
          </div>
          <TextField label={text("Mot de passe IMAP", "IMAP password")} type="password" autoComplete="new-password" value={imapPassword} onChange={(_, value) => { setSaved(false); setImapPassword(value || ""); }} disabled={busy} placeholder={status?.imap_password_set ? text("Enregistré. Laisser vide pour le conserver.", "Saved. Leave blank to keep it.") : ""} />
        </fieldset>
        <motion.div layout={!reduced} transition={reduced ? { duration: 0 } : { type: "spring", duration: 0.3, bounce: 0 }} className="grid gap-3 rounded-xl bg-indigo-500/5 p-4">
          <Toggle label={text("Autoriser l'envoi automatique après la chasse", "Allow automatic sending after each hunt")} checked={form.auto_send} onChange={(_, checked) => set("auto_send", Boolean(checked))} disabled={busy} data-testid="career-mail-auto-send" />
          <p className="text-sm text-muted-foreground">{text("Chaque candidature utilise un CV adapté à l'offre, sa lettre et une adresse de candidature publiée. Les documents incomplets restent à relire.", "Each application uses a CV tailored to the offer, its cover letter and a published application address. Incomplete documents remain available for review.")}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField label={text("Score minimum sur 100", "Minimum match score out of 100")} type="number" min={0} max={100} value={String(form.min_match_score)} onChange={(_, value) => set("min_match_score", Number(value || 0))} disabled={busy} />
            <TextField label={text("Candidatures maximum par jour", "Maximum applications per day")} type="number" min={1} max={25} value={String(form.max_per_day)} onChange={(_, value) => set("max_per_day", Number(value || 1))} disabled={busy} />
          </div>
          <TextField label={text("Domaines destinataires autorisés, facultatif", "Allowed recipient domains, optional")} description={text("Séparés par des virgules. Vide : toute adresse publiée dans une offre éligible.", "Comma-separated. Leave empty to allow published addresses on eligible offers.")} value={form.allowed_recipient_domains.join(", ")} onChange={(_, value) => set("allowed_recipient_domains", (value || "").split(",").map((item) => item.trim()))} disabled={busy} />
          {form.auto_send && !loopEnabled ? <DefaultButton styles={BUTTON} disabled={busy} text={text("Enregistrer et programmer la boucle", "Save and schedule the loop")} onClick={() => void save().then((result) => { if (result) onSchedule(); })} /> : null}
        </motion.div>
        {busy ? <ProgressIndicator label={text("Opération en cours...", "Working...")} /> : null}
        {error ? <MessageBar messageBarType={MessageBarType.error}>{careerMailError(error, french)}</MessageBar> : null}
        {saved && !error ? <MessageBar messageBarType={MessageBarType.success}>{text("Paramètres enregistrés.", "Settings saved.")}</MessageBar> : null}
        {Object.entries(status?.checks || {}).map(([protocol, check]) => check ? <MessageBar key={protocol} messageBarType={check.status === "connected" ? MessageBarType.success : MessageBarType.error}>{protocol.toUpperCase()}: {check.status === "connected" ? text("authentification vérifiée", "authentication verified") : careerMailError(check.error || "mail_transport_failed", french)}</MessageBar> : null)}
        <div className="flex flex-wrap gap-2">
          <PrimaryButton styles={BUTTON} text={text("Enregistrer", "Save")} onClick={() => void save()} disabled={busy} data-testid="career-mail-save" />
          <DefaultButton styles={BUTTON} text={text("Enregistrer et tester la connexion", "Save and test connection")} onClick={() => void test()} disabled={busy} data-testid="career-mail-test" />
        </div>
      </div>
    </Panel>
  );
}

export function CareerMailCompose({ open, draft, receipt, french, busy, error, accountEnabled, onDismiss, onSettings, onRefresh, onSend, onDownload }: {
  open: boolean; draft: CareerMailDraft | null; receipt?: CareerMailReceipt; french: boolean; busy: boolean;
  error: string; accountEnabled: boolean; onDismiss: () => void; onSettings: () => void;
  onRefresh: (recipient: string) => void; onSend: (reviewed: boolean, retry: boolean) => void;
  onDownload: (kind: "cv_docx" | "cover_docx") => void;
}) {
  const [recipient, setRecipient] = useState(draft?.recipient || "");
  const [reviewed, setReviewed] = useState(false);
  useEffect(() => { setRecipient(draft?.recipient || ""); setReviewed(false); }, [draft]);
  const locked = receipt?.status === "accepted" || receipt?.status === "unknown" || receipt?.status === "sending";
  const fresh = Boolean(draft && recipient.trim() && recipient.trim() === draft.recipient);
  const text = (fr: string, en: string) => french ? fr : en;
  return (
    <Panel isOpen={open} onDismiss={onDismiss} isLightDismiss={!busy} type={PanelType.medium} headerText={text("Candidature par email", "Application by email")} closeButtonAriaLabel={text("Fermer", "Close")}>
      <div className="grid gap-5 pb-8" data-testid="career-mail-compose">
        {busy ? <ProgressIndicator label={text("Opération en cours...", "Working...")} /> : null}
        {error ? <MessageBar messageBarType={MessageBarType.error}>{careerMailError(error, french)}</MessageBar> : null}
        {receipt ? <CareerMailReceiptView receipt={receipt} french={french} /> : null}
        {!accountEnabled ? <div className="grid gap-2"><p className="text-sm">{text("Connectez votre courrier professionnel pour envoyer cette candidature.", "Connect your professional mail account to send this application.")}</p><DefaultButton styles={BUTTON} text={text("Configurer le compte", "Account settings")} onClick={onSettings} /></div> : null}
        {draft ? <>
          <TextField label={text("Expéditeur", "From")} value={draft.sender} readOnly />
          <TextField label={text("Destinataire", "To")} type="email" value={recipient} onChange={(_, value) => setRecipient(value || "")} disabled={busy || locked} description={text("Adresse publiée dans l'offre ou communiquée par le recruteur.", "Address published in the offer or provided by the recruiter.")} data-testid="career-mail-recipient" />
          <TextField label={text("Objet", "Subject")} value={draft.subject} readOnly />
          <TextField label={text("Message", "Message")} value={draft.body} multiline rows={10} readOnly />
          <div className="grid gap-2">
            <h3 className="font-semibold">{text("Pièces jointes", "Attachments")}</h3>
            {draft.attachments.map((attachment) => <DefaultButton key={attachment.kind} styles={BUTTON} iconProps={{ iconName: "WordDocument" }} text={`${attachment.kind === "cv_docx" ? "CV" : text("Lettre", "Cover letter")} · ${attachment.name} · ${Math.ceil(attachment.size / 1024)} Ko`} onClick={() => onDownload(attachment.kind)} disabled={busy} data-testid={`career-mail-attachment-${attachment.kind}`} />)}
          </div>
          {!locked ? <DocumentGenerationNotice generation={draft.generation} /> : null}
          {draft.requires_review && !locked ? <Toggle label={text("J'ai relu le CV et la lettre", "I have reviewed the CV and cover letter")} checked={reviewed} onChange={(_, checked) => setReviewed(Boolean(checked))} disabled={busy} /> : null}
          {!locked ? <div className="flex flex-wrap gap-2">
            <PrimaryButton styles={BUTTON} text={receipt?.status === "failed" ? text("Réessayer l'envoi", "Retry sending") : text("Envoyer avec les deux pièces jointes", "Send with both attachments")} iconProps={{ iconName: "Send" }} disabled={busy || !accountEnabled || !fresh || (draft.requires_review && !reviewed)} onClick={() => onSend(reviewed, receipt?.status === "failed")} data-testid="career-mail-send" />
            <DefaultButton styles={BUTTON} text={text("Actualiser l'aperçu", "Refresh preview")} disabled={busy || !recipient.trim()} onClick={() => onRefresh(recipient)} />
          </div> : null}
        </> : null}
      </div>
    </Panel>
  );
}
