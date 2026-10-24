// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from "react";
import { ArrowLeft, Copy, LayoutTemplate, Loader2, Mail, MessageCircle, Plus, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { deleteTemplate, saveTemplate } from "@/lib/campaigns-api";
import type { CampaignChannel, Template } from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import { EmailEditor } from "./EmailEditor";
import { EmptyState, ErrorBanner, errorText, Field, formatDateTime, SELECT_CLASS, type Tx } from "./campaigns-ui";

/** Wrap a template's HTML so a thumbnail renders like an email. */
export function emailDocument(html: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;color:#0f172a;margin:24px;}img{max-width:100%;}table{border-collapse:collapse;width:100%;}td,th{border:1px solid #e2e8f0;padding:6px;}</style></head><body>${html}</body></html>`;
}

export function TemplatesView({
  token,
  tx,
  templates,
  attrKeys,
  onChanged,
}: {
  token: string;
  tx: Tx;
  templates: Template[];
  attrKeys: string[];
  onChanged: () => Promise<void> | void;
}) {
  const [editing, setEditing] = useState<Partial<Template> | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Template | null>(null);
  const [error, setError] = useState("");

  if (editing) {
    return (
      <TemplateEditor
        token={token}
        tx={tx}
        template={editing}
        attrKeys={attrKeys}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await onChanged();
        }}
      />
    );
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex-1">
          <h2 className="text-[18px] font-semibold text-foreground">{tx("templates.title", "Templates")}</h2>
          <p className="text-[12.5px] text-muted-foreground">{tx("templates.subtitle", "Reusable emails and WhatsApp messages, loaded into any campaign.")}</p>
        </div>
        <Button size="sm" variant="outline" className="rounded-full" onClick={() => setEditing({ channel: "whatsapp", name: "", text: "", wa_params: [] })}>
          <MessageCircle className="me-1.5 h-4 w-4" aria-hidden />
          {tx("templates.newWa", "WhatsApp template")}
        </Button>
        <Button size="sm" className="rounded-full" onClick={() => setEditing({ channel: "email", name: "", subject: "", preheader: "", html: "" })}>
          <Plus className="me-1.5 h-4 w-4" aria-hidden />
          {tx("templates.new", "New email template")}
        </Button>
      </div>
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      {templates.length === 0 ? (
        <EmptyState
          icon={<LayoutTemplate className="h-5 w-5" aria-hidden />}
          title={tx("templates.empty", "No template yet")}
          body={tx("templates.emptyBody", "Design an email once, then start campaigns from it. You can also save any campaign as a template.")}
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {templates.map((template) => (
            <div key={template.id} className="group overflow-hidden rounded-2xl border border-border/60 bg-card/80 shadow-sm">
              <button type="button" onClick={() => setEditing(template)} className="block w-full text-start">
                <div className="relative h-44 overflow-hidden border-b border-border/50 bg-white">
                  {template.channel === "email" ? (
                    <iframe
                      title={template.name}
                      sandbox=""
                      srcDoc={emailDocument(template.html)}
                      tabIndex={-1}
                      className="pointer-events-none absolute left-0 top-0 h-[440px] w-[250%] origin-top-left scale-[0.4]"
                    />
                  ) : (
                    <div className="flex h-full items-start bg-[#efeae2] p-4">
                      <p className="line-clamp-6 max-w-[90%] rounded-xl bg-[#d9fdd3] px-3 py-2 text-[12px] text-[#111b21] shadow-sm">
                        {template.text || template.wa_template}
                      </p>
                    </div>
                  )}
                </div>
                <div className="px-3 py-2.5">
                  <p className="flex items-center gap-1.5 truncate text-[13px] font-semibold text-foreground">
                    {template.channel === "whatsapp" ? <MessageCircle className="h-3.5 w-3.5 text-[#1f9d74]" aria-hidden /> : <Mail className="h-3.5 w-3.5 text-[#2f7de1]" aria-hidden />}
                    {template.name}
                  </p>
                  <p className="truncate text-[11.5px] text-muted-foreground">{template.subject || template.wa_template || tx("templates.noSubject", "No subject")}</p>
                  <p className="text-[11px] text-muted-foreground">{tx("templates.updated", "Updated {{when}}", { when: formatDateTime(template.updated_at) })}</p>
                </div>
              </button>
              <div className="flex justify-end gap-1 px-2 pb-2">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 rounded-full px-2 text-[12px]"
                  onClick={() =>
                    void saveTemplate(token, { ...template, id: undefined, name: tx("templates.copyName", "{{name}} (copy)", { name: template.name }) })
                      .then(() => onChanged())
                      .catch((err) => setError(errorText(err)))
                  }
                >
                  <Copy className="me-1 h-3.5 w-3.5" aria-hidden />
                  {tx("templates.duplicate", "Duplicate")}
                </Button>
                <Button size="sm" variant="ghost" className="h-7 rounded-full px-2 text-[12px] text-[#c2410c]" onClick={() => setPendingDelete(template)}>
                  <Trash2 className="me-1 h-3.5 w-3.5" aria-hidden />
                  {tx("templates.delete", "Delete")}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title={tx("templates.deleteTitle", "Delete {{name}}?", { name: pendingDelete?.name ?? "" })}
        description={tx("templates.deleteHelp", "Campaigns already made from it keep their content.")}
        confirmLabel={tx("templates.delete", "Delete")}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          const target = pendingDelete;
          setPendingDelete(null);
          if (target) void deleteTemplate(token, target.id).then(() => onChanged()).catch((err) => setError(errorText(err)));
        }}
      />
    </div>
  );
}

function TemplateEditor({
  token,
  tx,
  template,
  attrKeys,
  onClose,
  onSaved,
}: {
  token: string;
  tx: Tx;
  template: Partial<Template>;
  attrKeys: string[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<Partial<Template>>(template);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (patch: Partial<Template>) => setDraft((prev) => ({ ...prev, ...patch }));
  const channel: CampaignChannel = draft.channel ?? "email";
  const params = draft.wa_params ?? [];
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
        <button type="button" onClick={onClose} className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={tx("templates.back", "Back to templates")}>
          <ArrowLeft className="h-4 w-4 rtl:-scale-x-100" aria-hidden />
        </button>
        <p className="flex-1 truncate text-[14px] font-semibold">{draft.name || tx("templates.untitled", "Untitled template")}</p>
        <Button
          size="sm"
          className="rounded-full"
          disabled={busy || !(draft.name ?? "").trim()}
          onClick={() => {
            setBusy(true);
            setError("");
            void saveTemplate(token, draft)
              .then(() => onSaved())
              .catch((err) => setError(errorText(err)))
              .finally(() => setBusy(false));
          }}
        >
          {busy ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
          {tx("save", "Save")}
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl space-y-4 p-4 md:p-6">
          <ErrorBanner message={error} onDismiss={() => setError("")} />
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={tx("templates.name", "Template name")}>
              <Input value={draft.name ?? ""} maxLength={80} onChange={(event) => set({ name: event.target.value })} autoFocus />
            </Field>
            <Field label={tx("wizard.channel", "Channel")}>
              <select className={SELECT_CLASS} value={channel} disabled={Boolean(draft.id)} onChange={(event) => set({ channel: event.target.value as CampaignChannel })}>
                <option value="email">{tx("channel.email", "Email")}</option>
                <option value="whatsapp">WhatsApp</option>
              </select>
            </Field>
          </div>
          {channel === "email" ? (
            <>
              <Field label={tx("content.subject", "Subject")}>
                <Input value={draft.subject ?? ""} maxLength={200} onChange={(event) => set({ subject: event.target.value })} />
              </Field>
              <Field label={tx("content.preheader", "Preview text")}>
                <Input value={draft.preheader ?? ""} maxLength={200} onChange={(event) => set({ preheader: event.target.value })} />
              </Field>
              <EmailEditor value={draft.html ?? ""} onChange={(html) => set({ html })} tx={tx} extraTags={attrKeys} placeholder={tx("content.bodyPlaceholder", "Write your email...")} />
            </>
          ) : (
            <div className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={tx("templates.waName", "Approved template name (Meta)")} hint={tx("templates.waNameHint", "Leave empty for a free-text message.")}>
                  <Input value={draft.wa_template ?? ""} onChange={(event) => set({ wa_template: event.target.value })} placeholder="spring_offer" />
                </Field>
                <Field label={tx("templates.waLanguage", "Language code")}>
                  <Input value={draft.wa_language ?? ""} onChange={(event) => set({ wa_language: event.target.value })} placeholder="fr" />
                </Field>
              </div>
              {draft.wa_template ? (
                <div className="space-y-2">
                  <p className="text-[12px] font-medium text-foreground/85">{tx("templates.waParams", "Body parameters, in order")}</p>
                  {params.map((param, index) => (
                    <div key={index} className="flex gap-1.5">
                      <Input value={param} onChange={(event) => set({ wa_params: params.map((value, i) => (i === index ? event.target.value : value)) })} placeholder={`{{${index + 1}}} = {{first_name}}`} />
                      <Button variant="ghost" size="icon" onClick={() => set({ wa_params: params.filter((_, i) => i !== index) })} aria-label={tx("audience.remove", "Remove")}>
                        <Trash2 className="h-4 w-4" aria-hidden />
                      </Button>
                    </div>
                  ))}
                  <Button size="sm" variant="outline" className={cn("rounded-full")} onClick={() => set({ wa_params: [...params, ""] })}>
                    <Plus className="me-1 h-3.5 w-3.5" aria-hidden />
                    {tx("templates.addParam", "Add parameter")}
                  </Button>
                </div>
              ) : (
                <Field label={tx("wa.message", "Message")}>
                  <Textarea rows={6} value={draft.text ?? ""} maxLength={4096} onChange={(event) => set({ text: event.target.value })} />
                </Field>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
