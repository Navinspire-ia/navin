import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { CrmChannelStatus, CrmRecord } from "@/lib/api";
import { displayText } from "@/lib/crm-format";

type Props = {
  token: string;
  sessionKey: string;
  actor: string;
  contact?: CrmRecord;
  company?: CrmRecord;
  opportunity?: CrmRecord;
  channels: { email: CrmChannelStatus; whatsapp: CrmChannelStatus; teams: CrmChannelStatus } | null;
  tx: (key: string, fallback: string) => string;
  onOutreach: (body: Record<string, unknown>) => Promise<void>;
};

export function CrmOutreach({ contact, opportunity, channels, tx, onOutreach }: Props) {
  const [open, setOpen] = useState(false);
  const [channel, setChannel] = useState<"email" | "whatsapp" | "teams">("email");
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const defaultTo =
    channel === "email"
      ? displayText(contact?.email)
      : channel === "whatsapp"
        ? displayText(contact?.whatsapp) || displayText(contact?.phone)
        : displayText(contact?.email);

  const status = channels?.[channel];

  const submit = async (send: boolean, logAnyway = false) => {
    setBusy(true);
    setNotice(null);
    try {
      await onOutreach({
        channel,
        to: (to || defaultTo).trim(),
        subject,
        body,
        send,
        logAnyway,
        contactId: contact?.id || "",
        companyId: displayText(contact?.companyId) || displayText(opportunity?.companyId),
        opportunityId: opportunity?.id || "",
      });
      setNotice(send ? tx("crm.outreachSent", "Message traite et activite journalisee.") : tx("crm.outreachDraft", "Brouillon journalise."));
      setBody("");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-8 cursor-pointer text-[11px]"
        onClick={() => {
          setNotice(null);
          setOpen(true);
        }}
      >
        {tx("crm.outreach", "Ecrire")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          showCloseButton
          className="max-h-[90vh] max-w-md overflow-y-auto rounded-[22px] border-border/70 bg-popover p-5 shadow-2xl"
        >
          <DialogHeader className="text-left">
            <DialogTitle>{tx("crm.outreach", "Ecrire")}</DialogTitle>
            <DialogDescription>
              {tx("crm.outreachHelp", "Compose un email, WhatsApp ou Teams. Le message s'ouvre dans cette fenetre.")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <div className="flex flex-wrap gap-1">
              {(["email", "whatsapp", "teams"] as const).map((item) => (
                <Button
                  key={item}
                  type="button"
                  size="sm"
                  variant={channel === item ? "default" : "outline"}
                  className="h-8 cursor-pointer text-[11px]"
                  onClick={() => setChannel(item)}
                >
                  {tx(`crm.kinds.${item === "teams" ? "teams" : item}`, item)}
                </Button>
              ))}
            </div>
            {status && !status.ready ? (
              <p className="text-[11px] text-amber-600">
                {status.hint || tx("crm.channelOff", "Canal non configure.")}
              </p>
            ) : null}
            <input
              value={to}
              onChange={(event) => setTo(event.target.value)}
              placeholder={defaultTo || tx("crm.recipient", "Destinataire")}
              className="h-8 w-full cursor-text rounded-md border border-border/70 bg-background px-2 text-[12px]"
            />
            {channel === "email" ? (
              <input
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
                placeholder={tx("crm.subject", "Sujet")}
                className="h-8 w-full cursor-text rounded-md border border-border/70 bg-background px-2 text-[12px]"
              />
            ) : null}
            <textarea
              value={body}
              onChange={(event) => setBody(event.target.value)}
              placeholder={tx("crm.message", "Message")}
              rows={6}
              className="w-full cursor-text rounded-md border border-border/70 bg-background px-2 py-1.5 text-[12px]"
            />
            {notice ? <p className="text-[11px] text-muted-foreground">{notice}</p> : null}
          </div>
          <DialogFooter className="gap-2 sm:flex-wrap sm:space-x-0">
            <Button
              type="button"
              size="sm"
              className="h-8 cursor-pointer text-[11px]"
              disabled={busy || !(to || defaultTo) || !body.trim()}
              onClick={() => void submit(true)}
            >
              {tx("crm.send", "Envoyer")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 cursor-pointer text-[11px]"
              disabled={busy || !body.trim()}
              onClick={() => void submit(false)}
            >
              {tx("crm.prepare", "Preparer")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-8 cursor-pointer text-[11px]"
              disabled={busy || !body.trim()}
              onClick={() => void submit(true, true)}
            >
              {tx("crm.logAnyway", "Journaliser quand meme")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
