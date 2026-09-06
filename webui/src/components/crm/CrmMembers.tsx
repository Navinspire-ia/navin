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
import type { CrmInvite, CrmMember } from "@/lib/api";

const ROLES = ["owner", "admin", "member", "viewer"] as const;

type Props = {
  open: boolean;
  members: CrmMember[];
  invites: CrmInvite[];
  actor: string;
  myRole: string | null;
  busy?: boolean;
  tx: (key: string, fallback: string) => string;
  onClose: () => void;
  onInvite: (identity: string, role: string) => Promise<void>;
  onAccept: (inviteId: string, accept: boolean) => Promise<void>;
  onRole: (id: string, role: string) => Promise<void>;
  onKick: (id: string) => Promise<void>;
};

export function CrmMembers({
  open,
  members,
  invites,
  actor,
  myRole,
  busy,
  tx,
  onClose,
  onInvite,
  onAccept,
  onRole,
  onKick,
}: Props) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const owner = myRole === "owner";
  const selfKey = actor.trim().toLowerCase();
  const mine = invites.filter(
    (row) =>
      row.status === "pending" &&
      (row.identity === selfKey || row.email.toLowerCase() === selfKey),
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent
        showCloseButton
        className="max-h-[90vh] max-w-md overflow-y-auto rounded-[22px] border-border/70 bg-popover p-5 shadow-2xl"
      >
        <DialogHeader className="text-left">
          <DialogTitle style={{ textWrap: "balance" } as never}>
            {tx("crm.membersTitle", "Membres CRM")}
          </DialogTitle>
          <DialogDescription>
            {tx("crm.membersHint", "Invite par email, identifiant ou handle - comme Team.")}
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            if (!email.trim()) return;
            void onInvite(email.trim(), role).then(() => setEmail(""));
          }}
        >
          <input
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder={tx("crm.inviteEmail", "collegue@entreprise.com")}
            className="h-8 w-full cursor-text rounded-md border border-border/70 bg-background px-2 text-[12px]"
            disabled={!owner}
          />
          <select
            value={role}
            onChange={(event) => setRole(event.target.value)}
            className="h-8 w-full cursor-pointer rounded-md border border-border/70 bg-background px-2 text-[12px]"
            disabled={!owner}
          >
            {ROLES.filter((item) => item !== "owner").map((item) => (
              <option key={item} value={item}>
                {tx(`crm.roles.${item}`, item)}
              </option>
            ))}
          </select>
          <Button type="submit" size="sm" className="h-8 w-full cursor-pointer text-[11px]" disabled={!owner || busy || !email.trim()}>
            {tx("crm.inviteSend", "Inviter")}
          </Button>
          {!owner ? (
            <p className="text-[11px] text-muted-foreground">
              {tx("crm.inviteOwnerOnly", "Seul le owner peut inviter ou changer les roles.")}
            </p>
          ) : null}
        </form>

        {mine.length > 0 ? (
          <div className="space-y-1.5">
            <p className="text-[11px] font-semibold">{tx("crm.yourInvites", "Tes invitations")}</p>
            {mine.map((row) => (
              <div key={row.id} className="rounded-lg border border-border/60 p-2 text-[12px]">
                <p>{row.email || row.identity} - {row.role}</p>
                <div className="mt-1 flex gap-1">
                  <Button type="button" size="sm" className="h-8 cursor-pointer text-[11px]" onClick={() => void onAccept(row.id, true)}>
                    {tx("crm.accept", "Accepter")}
                  </Button>
                  <Button type="button" size="sm" variant="outline" className="h-8 cursor-pointer text-[11px]" onClick={() => void onAccept(row.id, false)}>
                    {tx("crm.decline", "Refuser")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : null}

        <ul className="max-h-64 space-y-1.5 overflow-y-auto">
          {members.map((row) => (
            <li key={row.id} className="rounded-lg border border-border/60 px-2 py-1.5 text-[12px]">
              <div className="font-medium">{row.displayName || row.email || row.identity}</div>
              <div className="text-[11px] text-muted-foreground">{row.email || row.handle}</div>
              {owner && row.role !== "owner" ? (
                <div className="mt-1 flex items-center gap-1">
                  <select
                    value={row.role}
                    onChange={(event) => void onRole(row.id, event.target.value)}
                    className="h-8 flex-1 cursor-pointer rounded-md border border-border/70 bg-background px-1 text-[11px]"
                  >
                    {ROLES.filter((item) => item !== "owner").map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                  <Button type="button" size="sm" variant="ghost" className="h-8 cursor-pointer text-[11px]" onClick={() => void onKick(row.id)}>
                    {tx("crm.kick", "Retirer")}
                  </Button>
                </div>
              ) : (
                <span className="text-[11px] uppercase text-muted-foreground">{row.role}</span>
              )}
            </li>
          ))}
        </ul>
        {invites.filter((row) => row.status === "pending").length > 0 ? (
          <div className="border-t border-border/50 pt-2">
            <p className="text-[11px] font-semibold">{tx("crm.pending", "En attente")}</p>
            {invites
              .filter((row) => row.status === "pending")
              .map((row) => (
                <p key={row.id} className="text-[11px] text-muted-foreground">
                  {row.email || row.identity} - {tx("crm.invitePending", "en attente")}
                </p>
              ))}
          </div>
        ) : null}

        <DialogFooter className="gap-2 sm:space-x-0">
          <Button type="button" variant="outline" className="cursor-pointer" onClick={onClose}>
            {tx("crm.close", "Fermer")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
