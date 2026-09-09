import { DefaultButton, DetailsList, DetailsListLayoutMode, MessageBar, MessageBarType, SelectionMode, Stack, Text } from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import type { AlertReceipt, TenderDesk } from "@/lib/tenders-api";
import { BUTTON_STYLES } from "@/components/studio/tenders/tenders-ui";

const CHANNEL_NAMES: Record<string, string> = {
  telegram: "Telegram", whatsapp: "WhatsApp", email: "Email", teams: "Microsoft Teams", slack: "Slack",
};

export function TenderAlertDeliveries({ desk, busy, onRetry, onConfigure }: {
  desk: TenderDesk;
  busy: boolean;
  onRetry: () => void;
  onConfigure: () => void;
}) {
  const { i18n } = useTranslation();
  const fr = i18n.language.startsWith("fr");
  const reduced = useReducedMotion();
  const copy = (french: string, english: string) => fr ? french : english;
  const delivery = desk.alert_deliveries;
  const statuses: Record<AlertReceipt["status"], string> = {
    new: copy("À préparer", "To prepare"),
    pending: copy("En attente", "Pending"),
    sending: copy("Transmission", "Sending"),
    accepted: copy("Acceptée par le transport", "Accepted by transport"),
    failed: copy("Échec", "Failed"),
    uncertain: copy("Confirmation inconnue", "Confirmation unknown"),
    cancelled: copy("Annulée", "Cancelled"),
  };
  const errors: Record<string, string> = {
    gateway_not_running: copy("Navin doit être lancé.", "Navin must be running."),
    channel_not_connected: copy("Connectez ce canal dans Réglages.", "Connect this channel in Settings."),
    channel_not_configured: copy("Vérifiez la configuration et le consentement du canal.", "Check channel configuration and consent."),
    destination_missing: copy("Renseignez le destinataire.", "Enter the recipient."),
    invalid_recipient: copy("Le destinataire est invalide.", "The recipient is invalid."),
    teams_conversation_unknown: copy("Ouvrez d'abord une conversation avec le bot Teams.", "First open a conversation with the Teams bot."),
    teams_authentication_failed: copy("Vérifiez les identifiants Teams.", "Check the Teams credentials."),
    teams_service_url_untrusted: copy("La conversation Teams ne possède pas une adresse de service autorisée.", "The Teams conversation has an untrusted service URL."),
    outbound_queue_full: copy("La file est pleine. Réessayez plus tard.", "The queue is full. Retry later."),
    channel_opt_in_removed: copy("Le canal ou le destinataire a été désactivé.", "The channel or recipient was disabled."),
    event_no_longer_pending: copy("L'événement ne nécessite plus d'alerte.", "The event no longer needs an alert."),
  };
  const rows = (delivery?.events || []).slice(0, 8).flatMap(event =>
    Object.values(event.receipts || {}).map(receipt => ({
      key: `${event.event_id}:${receipt.channel}`,
      title: event.title,
      channel: CHANNEL_NAMES[receipt.channel] || receipt.channel,
      receipt,
    })),
  );
  return (
    <motion.section data-testid="tenders-alert-deliveries" aria-label={copy("Suivi des alertes", "Alert delivery tracking")}
      initial={false} animate={{ opacity: 1 }} transition={reduced ? { duration: 0 } : { type: "spring", duration: 0.3, bounce: 0 }}>
      <Stack tokens={{ childrenGap: 12 }}>
        <Stack horizontal wrap horizontalAlign="space-between" verticalAlign="center" tokens={{ childrenGap: 12 }}>
          <div>
            <Text as="h2" variant="large" block>{copy("Suivi des alertes", "Alert delivery tracking")}</Text>
            <Text block styles={{ root: { fontVariantNumeric: "tabular-nums" } }}>
              {copy(`${delivery?.accepted || 0} confirmations · ${delivery?.pending || 0} en attente · ${delivery?.failed || 0} échecs`,
                `${delivery?.accepted || 0} confirmations · ${delivery?.pending || 0} pending · ${delivery?.failed || 0} failed`)}
            </Text>
          </div>
          <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
            <DefaultButton text={copy("Configurer les canaux", "Configure channels")} onClick={onConfigure} disabled={busy} styles={BUTTON_STYLES} />
            <DefaultButton text={copy("Relancer les échecs", "Retry failed alerts")} onClick={onRetry}
              disabled={busy || !delivery?.failed} styles={BUTTON_STYLES} data-testid="tenders-retry-alerts" />
          </Stack>
        </Stack>
        {delivery?.error ? <MessageBar messageBarType={MessageBarType.error}>
          {copy("Le registre des confirmations est illisible. Les alertes ne sont pas déclarées envoyées.", "The receipt ledger is unreadable. Alerts are not marked as sent.")}
        </MessageBar> : null}
        {delivery?.uncertain ? <MessageBar messageBarType={MessageBarType.warning}>
          {copy("Une confirmation manque après transmission. Vérifiez le canal avant un nouvel envoi.", "A confirmation is missing after transmission. Check the channel before sending again.")}
        </MessageBar> : null}
        {rows.length ? <div style={{ overflowX: "auto" }}>
          <DetailsList items={rows} selectionMode={SelectionMode.none} layoutMode={DetailsListLayoutMode.justified}
            columns={[
              { key: "title", name: copy("Alerte", "Alert"), fieldName: "title", minWidth: 160, maxWidth: 260, isMultiline: true },
              { key: "channel", name: copy("Canal", "Channel"), fieldName: "channel", minWidth: 100, maxWidth: 130 },
              { key: "status", name: copy("État et reçu", "Status and receipt"), minWidth: 220, isMultiline: true,
                onRender: (item: typeof rows[number]) => <Stack tokens={{ childrenGap: 4 }}>
                  <Text>{statuses[item.receipt.status]}</Text>
                  {item.receipt.message_id ? <Text variant="small" styles={{ root: { overflowWrap: "anywhere" } }}>{item.receipt.message_id}</Text> : null}
                  {item.receipt.error ? <Text variant="small">{errors[item.receipt.error] || (item.receipt.status === "uncertain"
                    ? copy("Aucun nouvel envoi automatique.", "No automatic resend.")
                    : copy("Le transport n'a pas confirmé l'envoi. Vérifiez sa connexion et le destinataire.", "The transport did not confirm the send. Check its connection and recipient."))}</Text> : null}
                </Stack> },
            ]} />
        </div> : <Text block>{copy("Aucune livraison d'alerte enregistrée. Choisissez vos canaux dans la configuration.", "No alert delivery recorded. Choose your channels in configuration.")}</Text>}
        <Text variant="small" block>{copy("Un reçu confirme l'acceptation par le service d'envoi. Il ne confirme pas la lecture par le destinataire.",
          "A receipt confirms acceptance by the sending service. It does not confirm the recipient read the message.")}</Text>
      </Stack>
    </motion.section>
  );
}
