// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  Braces,
  Briefcase,
  Check,
  FileUp,
  Loader2,
  Mail,
  MessageCircle,
  Monitor,
  Paperclip,
  Save,
  Search,
  Send,
  Smartphone,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchCampaignSettings,
  fileToBase64,
  listContacts,
  listWhatsAppTemplates,
  MAX_ATTACHMENT_BYTES,
  previewCampaign,
  saveCampaign,
  saveTemplate,
  scheduleCampaign,
  testCampaign,
  uploadAttachment,
} from "@/lib/campaigns-api";
import {
  CAMPAIGN_MERGE_TAGS,
  type Campaign,
  type CampaignChannel,
  type CampaignPreview,
  type Contact,
  type ContactList,
  type ImportResult,
  type Template,
  type WhatsAppTemplate,
} from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import { CrmAudiencePicker } from "./CrmAudiencePicker";
import { EmailEditor, insertAtCaret, mergeTag } from "./EmailEditor";
import { followUpSubject, SequenceEditor, sendWindowSummary, SubjectVariantsEditor } from "./SequenceEditor";
import {
  ErrorBanner,
  errorText,
  Field,
  formatBytes,
  formatNumber,
  isoToLocalInput,
  localInputToIso,
  localTimeZone,
  Notice,
  SELECT_CLASS,
  Switch,
  type Tx,
} from "./campaigns-ui";

export const WIZARD_STEPS = ["setup", "audience", "content", "preview", "schedule"] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];

export function emptyCampaign(channel: CampaignChannel = "email"): Partial<Campaign> {
  return {
    name: "",
    channel,
    subject: "",
    preheader: "",
    from_name: "",
    reply_to: "",
    html: "",
    text: "",
    wa_template: "",
    wa_language: "",
    wa_params: [],
    template_id: "",
    list_ids: [],
    contact_ids: [],
    attachments: [],
    schedule_at: "",
    throttle_per_minute: 30,
    track_opens: true,
    track_clicks: true,
    steps: [],
    subject_variants: [],
  };
}

/** What blocks moving past a step; empty means the step is complete. */
export function stepProblems(step: WizardStep, draft: Partial<Campaign>, tx: Tx): string[] {
  const problems: string[] = [];
  if (step === "setup" && !(draft.name ?? "").trim()) problems.push(tx("wizard.needName", "Give the campaign a name."));
  if (step === "audience" && !(draft.list_ids?.length || draft.contact_ids?.length)) {
    problems.push(tx("wizard.needAudience", "Pick at least one list or contact."));
  }
  if (step === "content") {
    if (draft.channel === "whatsapp") {
      if (!draft.wa_template && !(draft.text ?? "").trim()) problems.push(tx("wizard.needWa", "Pick an approved template or write a message."));
      if (draft.wa_template && (draft.wa_params ?? []).some((param) => !param.trim())) {
        problems.push(tx("wizard.needParams", "Fill every template parameter."));
      }
    } else {
      if (!(draft.subject ?? "").trim()) problems.push(tx("wizard.needSubject", "Write a subject."));
      if (!textOf(draft.html ?? "").trim()) problems.push(tx("wizard.needBody", "Write the email body."));
      if ((draft.subject_variants ?? []).some((variant) => !variant.trim())) {
        problems.push(tx("wizard.needVariant", "Fill or remove the empty A/B subject."));
      }
      const emptyStep = (draft.steps ?? []).findIndex((item) => !textOf(item.html).trim());
      if (emptyStep >= 0) problems.push(tx("wizard.needFollowUp", "Write follow-up {{n}} or delete it.", { n: emptyStep + 1 }));
    }
  }
  return problems;
}

function textOf(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ");
}

export function CampaignWizard({
  token,
  sessionKey,
  tx,
  initial,
  lists,
  templates,
  attrKeys,
  onClose,
  onSaved,
  onLaunched,
  onListsChanged,
  onTemplatesChanged,
}: {
  token: string;
  sessionKey: string | null;
  tx: Tx;
  initial: Partial<Campaign>;
  lists: ContactList[];
  templates: Template[];
  attrKeys: string[];
  onClose: () => void;
  onSaved: (campaign: Campaign) => void;
  onLaunched: (campaign: Campaign) => void;
  onListsChanged: () => Promise<void> | void;
  onTemplatesChanged: () => Promise<void> | void;
}) {
  const [step, setStep] = useState<WizardStep>("setup");
  const [draft, setDraft] = useState<Partial<Campaign>>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const index = WIZARD_STEPS.indexOf(step);
  const problems = stepProblems(step, draft, tx);
  const set = (patch: Partial<Campaign>) => setDraft((prev) => ({ ...prev, ...patch }));

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 3500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const persist = async (): Promise<Campaign> => {
    const saved = await saveCampaign(token, draft);
    setDraft(saved);
    onSaved(saved);
    return saved;
  };
  const saveDraft = async () => {
    setBusy(true);
    setError("");
    try {
      await persist();
      setNotice(tx("wizard.saved", "Draft saved."));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const next = async () => {
    if (problems.length) return;
    // Saved at every step: closing the wizard never loses work.
    setBusy(true);
    setError("");
    try {
      await persist();
      setStep(WIZARD_STEPS[Math.min(WIZARD_STEPS.length - 1, index + 1)]);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const stepLabels: Record<WizardStep, string> = {
    setup: tx("wizard.setup", "Setup"),
    audience: tx("wizard.audience", "Audience"),
    content: tx("wizard.content", "Content"),
    preview: tx("wizard.preview", "Preview and test"),
    schedule: tx("wizard.schedule", "Schedule"),
  };

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="campaign-wizard">
      <header className="flex flex-wrap items-center gap-3 border-b border-border/60 px-4 py-2.5">
        <button type="button" onClick={onClose} className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={tx("wizard.close", "Close")}>
          <X className="h-4 w-4" aria-hidden />
        </button>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[14px] font-semibold text-foreground">{draft.name || tx("campaigns.new", "New campaign")}</p>
          <p className="text-[11.5px] text-muted-foreground">{draft.channel === "whatsapp" ? "WhatsApp" : tx("channel.email", "Email")}</p>
        </div>
        <ol className="hidden items-center gap-1 lg:flex">
          {WIZARD_STEPS.map((item, itemIndex) => (
            <li key={item}>
              <button
                type="button"
                disabled={itemIndex > index && !draft.id}
                onClick={() => setStep(item)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] transition-colors",
                  item === step ? "bg-[#1d6af0] text-white" : itemIndex < index ? "text-foreground hover:bg-muted" : "text-muted-foreground hover:bg-muted disabled:opacity-50",
                )}
              >
                <span className={cn("grid h-4 w-4 place-items-center rounded-full text-[10px]", item === step ? "bg-white/25" : "bg-muted")}>
                  {itemIndex < index ? <Check className="h-2.5 w-2.5" aria-hidden /> : itemIndex + 1}
                </span>
                {stepLabels[item]}
              </button>
            </li>
          ))}
        </ol>
        <Button size="sm" variant="ghost" className="rounded-full" disabled={busy || !(draft.name ?? "").trim()} onClick={() => void saveDraft()}>
          <Save className="me-1.5 h-3.5 w-3.5" aria-hidden />
          {tx("wizard.saveDraft", "Save draft")}
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl space-y-4 p-4 md:p-6">
          <p className="text-[11.5px] font-medium uppercase tracking-wide text-muted-foreground lg:hidden">
            {tx("wizard.stepOf", "Step {{n}} of {{total}}: {{label}}", { n: index + 1, total: WIZARD_STEPS.length, label: stepLabels[step] })}
          </p>
          <ErrorBanner message={error} onDismiss={() => setError("")} />
          <Notice message={notice} />
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={step} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.15 }}>
              {step === "setup" ? <SetupStep draft={draft} set={set} tx={tx} /> : null}
              {step === "audience" ? (
                <AudienceStep token={token} sessionKey={sessionKey} draft={draft} set={set} lists={lists} tx={tx} onListsChanged={onListsChanged} />
              ) : null}
              {step === "content" ? (
                <ContentStep
                  token={token}
                  draft={draft}
                  set={set}
                  tx={tx}
                  templates={templates}
                  attrKeys={attrKeys}
                  onError={setError}
                  onNotice={setNotice}
                  onTemplatesChanged={onTemplatesChanged}
                />
              ) : null}
              {step === "preview" ? <PreviewStep token={token} draft={draft} tx={tx} /> : null}
              {step === "schedule" ? (
                <ScheduleStep
                  token={token}
                  draft={draft}
                  set={set}
                  tx={tx}
                  persist={persist}
                  onLaunched={onLaunched}
                />
              ) : null}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      <footer className="flex items-center justify-between gap-2 border-t border-border/60 px-4 py-2.5">
        <Button variant="ghost" className="rounded-full" disabled={index === 0 || busy} onClick={() => setStep(WIZARD_STEPS[index - 1])}>
          <ArrowLeft className="me-1.5 h-4 w-4 rtl:-scale-x-100" aria-hidden />
          {tx("wizard.back", "Back")}
        </Button>
        {problems.length ? <span className="min-w-0 flex-1 truncate text-center text-[12px] text-muted-foreground">{problems[0]}</span> : <span className="flex-1" />}
        {step !== "schedule" ? (
          <Button className="rounded-full" disabled={busy || problems.length > 0} onClick={() => void next()} data-testid="wizard-next">
            {busy ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
            {tx("wizard.next", "Continue")}
            <ArrowRight className="ms-1.5 h-4 w-4 rtl:-scale-x-100" aria-hidden />
          </Button>
        ) : <span />}
      </footer>
    </div>
  );
}

function SetupStep({ draft, set, tx }: { draft: Partial<Campaign>; set: (patch: Partial<Campaign>) => void; tx: Tx }) {
  const channels: Array<{ id: CampaignChannel; label: string; body: string; icon: typeof Mail }> = [
    { id: "email", label: tx("channel.email", "Email"), body: tx("channel.emailBody", "Rich email with images, buttons, attachments and tracking."), icon: Mail },
    { id: "whatsapp", label: "WhatsApp", body: tx("channel.whatsappBody", "Approved WhatsApp templates through the official Cloud API."), icon: MessageCircle },
  ];
  return (
    <div className="space-y-5">
      <Field label={tx("wizard.name", "Campaign name")} hint={tx("wizard.nameHint", "Only you see it.")}>
        <Input value={draft.name ?? ""} maxLength={120} autoFocus onChange={(event) => set({ name: event.target.value })} placeholder={tx("wizard.namePlaceholder", "e.g. Spring launch, wave 1")} />
      </Field>
      <div>
        <p className="mb-2 text-[12px] font-medium text-foreground/85">{tx("wizard.channel", "Channel")}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          {channels.map((channel) => (
            <button
              key={channel.id}
              type="button"
              aria-pressed={draft.channel === channel.id}
              disabled={Boolean(draft.id) && draft.channel !== channel.id}
              onClick={() => set({ channel: channel.id })}
              className={cn(
                "flex items-start gap-3 rounded-2xl border p-4 text-start transition-colors disabled:opacity-40",
                draft.channel === channel.id ? "border-[#1d6af0] bg-[#1d6af0]/[0.05]" : "border-border/70 hover:bg-muted/30",
              )}
            >
              <channel.icon className="mt-0.5 h-5 w-5 text-[#1d6af0]" aria-hidden />
              <span>
                <span className="block text-[13.5px] font-semibold text-foreground">{channel.label}</span>
                <span className="block text-[12px] text-muted-foreground">{channel.body}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function AudienceStep({
  token,
  sessionKey,
  draft,
  set,
  lists,
  tx,
  onListsChanged,
}: {
  token: string;
  sessionKey: string | null;
  draft: Partial<Campaign>;
  set: (patch: Partial<Campaign>) => void;
  lists: ContactList[];
  tx: Tx;
  onListsChanged: () => Promise<void> | void;
}) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Contact[]>([]);
  const [picked, setPicked] = useState<Contact[]>([]);
  const [crmOpen, setCrmOpen] = useState(false);
  const listIds = draft.list_ids ?? [];
  const contactIds = draft.contact_ids ?? [];
  const listTotal = lists.filter((list) => listIds.includes(list.id)).reduce((sum, list) => sum + list.count, 0);

  useEffect(() => {
    if (!q.trim()) {
      setFound([]);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      listContacts(token, { q: q.trim(), limit: 8 }).then((page) => { if (live) setFound(page.items); }).catch(() => undefined);
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [q, token]);

  const crmDone = async (result: ImportResult) => {
    setCrmOpen(false);
    await onListsChanged();
    if (result.list_id && !listIds.includes(result.list_id)) set({ list_ids: [...listIds, result.list_id] });
  };

  return (
    <div className="space-y-5">
      <div>
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="text-[12px] font-medium text-foreground/85">{tx("audience.lists", "Lists")}</p>
          <Button size="sm" variant="outline" className="h-8 rounded-full" onClick={() => setCrmOpen(true)}>
            <Briefcase className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("audience.fromCrm", "From CRM")}
          </Button>
        </div>
        {lists.length === 0 ? (
          <p className="rounded-xl border border-dashed border-border/80 p-4 text-[12.5px] text-muted-foreground">{tx("audience.noLists", "No list yet: import contacts in Contacts and lists, or take them from the CRM.")}</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {lists.map((list) => {
              const on = listIds.includes(list.id);
              return (
                <label key={list.id} className={cn("flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2.5", on ? "border-[#1d6af0] bg-[#1d6af0]/[0.05]" : "border-border/70 hover:bg-muted/30")}>
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => set({ list_ids: on ? listIds.filter((id) => id !== list.id) : [...listIds, list.id] })}
                    className="accent-[#1d6af0]"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium text-foreground">{list.name}</span>
                    {list.description ? <span className="block truncate text-[11.5px] text-muted-foreground">{list.description}</span> : null}
                  </span>
                  <span className="text-[12px] tabular-nums text-muted-foreground">{formatNumber(list.count)}</span>
                </label>
              );
            })}
          </div>
        )}
      </div>

      <div>
        <p className="mb-2 text-[12px] font-medium text-foreground/85">{tx("audience.people", "Individual contacts")}</p>
        <div className="relative">
          <Search className="pointer-events-none absolute start-2.5 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden />
          <Input value={q} onChange={(event) => setQ(event.target.value)} placeholder={tx("audience.search", "Search a contact to add")} className="ps-8" />
        </div>
        {found.length ? (
          <ul className="mt-1 divide-y divide-border/50 rounded-xl border border-border/60">
            {found.map((contact) => (
              <li key={contact.id}>
                <button
                  type="button"
                  disabled={contactIds.includes(contact.id)}
                  onClick={() => {
                    set({ contact_ids: [...contactIds, contact.id] });
                    setPicked((prev) => [...prev, contact]);
                    setQ("");
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-start text-[12.5px] hover:bg-muted/30 disabled:opacity-50"
                >
                  <span className="min-w-0 flex-1 truncate">{[contact.first_name, contact.last_name].filter(Boolean).join(" ") || contact.email}</span>
                  <span className="truncate text-muted-foreground">{contact.email || contact.phone}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {contactIds.length ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {contactIds.map((id) => {
              const contact = picked.find((row) => row.id === id);
              return (
                <span key={id} className="inline-flex items-center gap-1 rounded-full bg-muted px-2.5 py-0.5 text-[12px]">
                  {contact ? contact.email || contact.phone : id}
                  <button type="button" onClick={() => set({ contact_ids: contactIds.filter((value) => value !== id) })} aria-label={tx("audience.remove", "Remove")}>
                    <X className="h-3 w-3" aria-hidden />
                  </button>
                </span>
              );
            })}
          </div>
        ) : null}
      </div>

      <div className="rounded-xl bg-muted/30 px-4 py-3 text-[13px]" data-testid="audience-count">
        <span className="font-semibold text-foreground">
          {tx("audience.total", "Up to {{count}} recipients", { count: formatNumber(listTotal + contactIds.length) })}
        </span>
        <span className="block text-[11.5px] text-muted-foreground">
          {draft.channel === "whatsapp"
            ? tx("audience.totalWa", "Contacts without a phone number, unsubscribed or duplicated are skipped at send time.")
            : tx("audience.totalEmail", "Contacts without an email, unsubscribed, bounced or duplicated are skipped at send time.")}
        </span>
      </div>

      <Dialog open={crmOpen} onOpenChange={setCrmOpen}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{tx("audience.fromCrm", "From CRM")}</DialogTitle>
            <DialogDescription>{tx("audience.fromCrmHelp", "The filtered people become a list, selected for this campaign.")}</DialogDescription>
          </DialogHeader>
          <CrmAudiencePicker
            token={token}
            sessionKey={sessionKey}
            lists={lists}
            tx={tx}
            onImported={(result) => void crmDone(result)}
            submitLabel={tx("audience.useCrm", "Create the list and use it")}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ContentStep({
  token,
  draft,
  set,
  tx,
  templates,
  attrKeys,
  onError,
  onNotice,
  onTemplatesChanged,
}: {
  token: string;
  draft: Partial<Campaign>;
  set: (patch: Partial<Campaign>) => void;
  tx: Tx;
  templates: Template[];
  attrKeys: string[];
  onError: (message: string) => void;
  onNotice: (message: string) => void;
  onTemplatesChanged: () => Promise<void> | void;
}) {
  const subjectRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const channelTemplates = templates.filter((template) => template.channel === draft.channel);

  const loadTemplate = (id: string) => {
    const template = templates.find((row) => row.id === id);
    if (!template) return;
    set({
      template_id: template.id,
      subject: template.subject,
      preheader: template.preheader,
      html: template.html,
      text: template.text,
      wa_template: template.wa_template,
      wa_language: template.wa_language,
      wa_params: [...template.wa_params],
    });
    onNotice(tx("content.loaded", "Template loaded."));
  };
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    try {
      const added = [];
      for (const file of Array.from(files)) {
        if (file.size > MAX_ATTACHMENT_BYTES) throw new Error(tx("content.tooBig", "{{name}} is over 10 MB.", { name: file.name }));
        added.push(await uploadAttachment(token, file.name, await fileToBase64(file)));
      }
      set({ attachments: [...(draft.attachments ?? []), ...added] });
    } catch (err) {
      onError(errorText(err));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-2">
        <Field label={tx("content.fromTemplate", "Start from a template")} className="min-w-[14rem] flex-1">
          <select className={SELECT_CLASS} value="" onChange={(event) => loadTemplate(event.target.value)}>
            <option value="">{channelTemplates.length ? tx("content.pickTemplate", "Choose a template...") : tx("content.noTemplate", "No template yet")}</option>
            {channelTemplates.map((template) => (
              <option key={template.id} value={template.id}>
                {template.name}
              </option>
            ))}
          </select>
        </Field>
        <Button variant="outline" size="sm" className="h-9 rounded-full" onClick={() => setSaveOpen(true)}>
          <Save className="me-1.5 h-3.5 w-3.5" aria-hidden />
          {tx("content.saveTemplate", "Save as template")}
        </Button>
      </div>

      {draft.channel === "whatsapp" ? (
        <WhatsAppContent token={token} draft={draft} set={set} tx={tx} attrKeys={attrKeys} onError={onError} />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={tx("content.subject", "Subject")} className="sm:col-span-2">
              <div className="flex gap-1.5">
                <Input ref={subjectRef} value={draft.subject ?? ""} maxLength={200} onChange={(event) => set({ subject: event.target.value })} placeholder={tx("content.subjectPlaceholder", "e.g. {{tag}}, our spring offer", { tag: "{{first_name|Hello}}" })} />
                <TagMenu tx={tx} attrKeys={attrKeys} onPick={(tag) => set({ subject: insertAtCaret(subjectRef.current, draft.subject ?? "", tag) })} />
              </div>
            </Field>
            <SubjectVariantsEditor draft={draft} set={set} tx={tx} />
            <Field label={tx("content.preheader", "Preview text")} hint={tx("content.preheaderHint", "Shown after the subject in most inboxes.")} className="sm:col-span-2">
              <Input value={draft.preheader ?? ""} maxLength={200} onChange={(event) => set({ preheader: event.target.value })} />
            </Field>
            <Field label={tx("content.fromName", "Sender name")} hint={tx("content.fromNameHint", "Empty = the name in campaign settings.")}>
              <Input value={draft.from_name ?? ""} maxLength={80} onChange={(event) => set({ from_name: event.target.value })} />
            </Field>
            <Field label={tx("content.replyTo", "Reply-to")} hint={tx("content.replyToHint", "Where answers go. Empty = settings.")}>
              <Input type="email" value={draft.reply_to ?? ""} onChange={(event) => set({ reply_to: event.target.value })} />
            </Field>
          </div>
          <EmailEditor value={draft.html ?? ""} onChange={(html) => set({ html })} tx={tx} extraTags={attrKeys} placeholder={tx("content.bodyPlaceholder", "Write your email...")} />
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <p className="text-[12px] font-medium text-foreground/85">{tx("content.attachments", "Attachments")}</p>
              <Button size="sm" variant="outline" className="h-8 rounded-full" disabled={uploading} onClick={() => fileRef.current?.click()}>
                {uploading ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <FileUp className="me-1.5 h-3.5 w-3.5" aria-hidden />}
                {tx("content.attach", "Attach files")}
              </Button>
              <input ref={fileRef} type="file" multiple className="hidden" onChange={(event) => void upload(event.target.files)} />
            </div>
            {(draft.attachments ?? []).length ? (
              <ul className="space-y-1">
                {(draft.attachments ?? []).map((attachment) => (
                  <li key={attachment.id} className="flex items-center gap-2 rounded-lg bg-muted/30 px-3 py-1.5 text-[12.5px]">
                    <Paperclip className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 flex-1 truncate">{attachment.name}</span>
                    <span className="text-[11.5px] text-muted-foreground">{formatBytes(attachment.size)}</span>
                    <button type="button" onClick={() => set({ attachments: (draft.attachments ?? []).filter((row) => row.id !== attachment.id) })} aria-label={tx("content.removeAttachment", "Remove")}>
                      <X className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[12px] text-muted-foreground">{tx("content.noAttachment", "No attachment. Up to 10 MB per file; large files hurt deliverability.")}</p>
            )}
          </div>
          <div className="border-t border-border/60 pt-4">
            <SequenceEditor draft={draft} set={set} tx={tx} attrKeys={attrKeys} />
          </div>
        </>
      )}

      {saveOpen ? (
        <SaveTemplateDialog
          tx={tx}
          defaultName={draft.name ?? ""}
          onClose={() => setSaveOpen(false)}
          onSave={async (name) => {
            const saved = await saveTemplate(token, {
              name,
              channel: draft.channel ?? "email",
              subject: draft.subject ?? "",
              preheader: draft.preheader ?? "",
              html: draft.html ?? "",
              text: draft.text ?? "",
              wa_template: draft.wa_template ?? "",
              wa_language: draft.wa_language ?? "",
              wa_params: draft.wa_params ?? [],
            });
            set({ template_id: saved.id });
            setSaveOpen(false);
            await onTemplatesChanged();
            onNotice(tx("content.templateSaved", "Template saved."));
          }}
        />
      ) : null}
    </div>
  );
}

function TagMenu({ tx, attrKeys, onPick }: { tx: Tx; attrKeys: string[]; onPick: (tag: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <Button type="button" variant="outline" size="icon" className="h-10 w-10" onClick={() => setOpen((value) => !value)} aria-label={tx("editor.mergeTags", "Personalize")}>
        <Braces className="h-4 w-4" aria-hidden />
      </Button>
      {open ? (
        <div className="absolute end-0 top-11 z-20 max-h-60 w-52 overflow-y-auto rounded-xl border border-border/70 bg-popover p-1 shadow-lg">
          {[...CAMPAIGN_MERGE_TAGS, ...attrKeys.map((key) => `attrs.${key}`)].map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => {
                onPick(mergeTag(name, name === "first_name" ? tx("editor.fallbackThere", "there") : ""));
                setOpen(false);
              }}
              className="block w-full rounded-md px-2 py-1.5 text-start font-mono text-[12px] hover:bg-muted"
            >
              {`{{${name}}}`}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function WhatsAppContent({
  token,
  draft,
  set,
  tx,
  attrKeys,
  onError,
}: {
  token: string;
  draft: Partial<Campaign>;
  set: (patch: Partial<Campaign>) => void;
  tx: Tx;
  attrKeys: string[];
  onError: (message: string) => void;
}) {
  const [templates, setTemplates] = useState<WhatsAppTemplate[] | null>(null);
  const [mode, setMode] = useState<"template" | "text">(draft.text && !draft.wa_template ? "text" : "template");
  useEffect(() => {
    let live = true;
    listWhatsAppTemplates(token)
      .then((rows) => { if (live) setTemplates(rows); })
      .catch((err) => {
        if (!live) return;
        setTemplates([]);
        onError(errorText(err));
      });
    return () => { live = false; };
  }, [onError, token]);
  const selected = templates?.find((row) => row.name === draft.wa_template && row.language === draft.wa_language) ?? null;
  const approved = (templates ?? []).filter((row) => row.status.toUpperCase() === "APPROVED");
  const params = draft.wa_params ?? [];

  return (
    <div className="space-y-4">
      <div className="inline-flex rounded-full bg-muted p-0.5 text-[12.5px]">
        {(["template", "text"] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => {
              setMode(value);
              if (value === "text") set({ wa_template: "", wa_language: "", wa_params: [] });
              else set({ text: "" });
            }}
            className={cn("rounded-full px-3 py-1", mode === value ? "bg-background font-medium shadow-sm" : "text-muted-foreground")}
          >
            {value === "template" ? tx("wa.template", "Approved template") : tx("wa.text", "Free text")}
          </button>
        ))}
      </div>
      {mode === "template" ? (
        <>
          {templates === null ? (
            <p className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {tx("wa.loading", "Loading your approved templates from Meta...")}
            </p>
          ) : approved.length === 0 ? (
            <p className="rounded-xl border border-dashed border-border/80 p-4 text-[12.5px] text-muted-foreground">
              {tx("wa.noTemplates", "No approved template found. Create and get templates approved in Meta WhatsApp Manager, and check the WhatsApp settings of this module.")}
            </p>
          ) : (
            <Field label={tx("wa.pick", "Template")}>
              <select
                className={SELECT_CLASS}
                value={selected ? `${selected.name}|${selected.language}` : ""}
                onChange={(event) => {
                  const row = approved.find((item) => `${item.name}|${item.language}` === event.target.value);
                  if (!row) return;
                  set({ wa_template: row.name, wa_language: row.language, wa_params: Array.from({ length: row.params }, (_, i) => params[i] ?? "") });
                }}
              >
                <option value="">{tx("wa.choose", "Choose a template...")}</option>
                {approved.map((row) => (
                  <option key={`${row.name}|${row.language}`} value={`${row.name}|${row.language}`}>
                    {row.name} ({row.language}, {row.category})
                  </option>
                ))}
              </select>
            </Field>
          )}
          {selected ? (
            <div className="space-y-3">
              <div className="rounded-2xl bg-[#e7f7e1] p-3 text-[13px] leading-relaxed text-[#111b21] dark:bg-[#1f3a2c] dark:text-[#e9edef]">
                <p className="whitespace-pre-wrap">{selected.body}</p>
              </div>
              {params.map((param, paramIndex) => (
                <Field key={paramIndex} label={tx("wa.param", "Parameter {{n}}", { n: paramIndex + 1 })}>
                  <div className="flex gap-1.5">
                    <Input value={param} onChange={(event) => set({ wa_params: params.map((value, i) => (i === paramIndex ? event.target.value : value)) })} placeholder="{{first_name}}" />
                    <TagMenu tx={tx} attrKeys={attrKeys} onPick={(tag) => set({ wa_params: params.map((value, i) => (i === paramIndex ? `${value}${tag}` : value)) })} />
                  </div>
                </Field>
              ))}
            </div>
          ) : null}
        </>
      ) : (
        <>
          <ErrorBanner message={tx("wa.windowWarning", "WhatsApp only delivers free text to people who wrote to you in the last 24 hours. For everyone else, use an approved template.")} />
          <Field label={tx("wa.message", "Message")}>
            <Textarea rows={6} value={draft.text ?? ""} maxLength={4096} onChange={(event) => set({ text: event.target.value })} placeholder={tx("wa.messagePlaceholder", "Hello {{tag}}, ...", { tag: "{{first_name|there}}" })} />
          </Field>
        </>
      )}
    </div>
  );
}

function SaveTemplateDialog({ tx, defaultName, onClose, onSave }: { tx: Tx; defaultName: string; onClose: () => void; onSave: (name: string) => Promise<void> }) {
  const [name, setName] = useState(defaultName);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{tx("content.saveTemplate", "Save as template")}</DialogTitle>
        </DialogHeader>
        <ErrorBanner message={error} />
        <Field label={tx("templates.name", "Template name")}>
          <Input value={name} maxLength={80} autoFocus onChange={(event) => setName(event.target.value)} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" className="rounded-full" onClick={onClose}>
            {tx("cancel", "Cancel")}
          </Button>
          <Button
            className="rounded-full"
            disabled={busy || !name.trim()}
            onClick={() => {
              setBusy(true);
              void onSave(name.trim()).catch((err) => setError(errorText(err))).finally(() => setBusy(false));
            }}
          >
            {tx("save", "Save")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function PreviewStep({ token, draft, tx }: { token: string; draft: Partial<Campaign>; tx: Tx }) {
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");
  // 0 = the first email, n = follow-up n (sequence campaigns).
  const [message, setMessage] = useState(0);
  // Memoized: a fresh [] each render would re-run the preview fetch forever.
  const followUps = useMemo(() => (draft.channel === "whatsapp" ? [] : (draft.steps ?? [])), [draft.channel, draft.steps]);
  const shown = useMemo<Partial<Campaign>>(() => {
    const step = message > 0 ? followUps[message - 1] : undefined;
    if (!step) return draft;
    return { ...draft, subject: followUpSubject(step, draft.subject ?? ""), preheader: "", html: step.html };
  }, [draft, followUps, message]);
  const [contactQ, setContactQ] = useState("");
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [contactId, setContactId] = useState("");
  const [preview, setPreview] = useState<CampaignPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [to, setTo] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState("");

  useEffect(() => {
    let live = true;
    listContacts(token, { q: contactQ.trim(), list_id: draft.list_ids?.[0] ?? "", limit: 20 })
      .then((page) => { if (live) setContacts(page.items); })
      .catch(() => undefined);
    return () => { live = false; };
  }, [contactQ, draft.list_ids, token]);

  useEffect(() => {
    let live = true;
    setLoading(true);
    previewCampaign(token, shown, contactId)
      .then((next) => {
        if (!live) return;
        setPreview(next);
        setError("");
      })
      .catch((err) => { if (live) setError(errorText(err)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [contactId, shown, token]);

  const recipients = useMemo(() => to.split(/[,;\s]+/).map((item) => item.trim()).filter(Boolean), [to]);

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      <div className="flex flex-wrap items-end gap-3">
        <Field label={tx("preview.as", "Preview as")} className="min-w-[14rem] flex-1">
          <div className="flex gap-1.5">
            <Input value={contactQ} onChange={(event) => setContactQ(event.target.value)} placeholder={tx("preview.searchContact", "Search a contact")} className="max-w-[12rem]" />
            <select className={SELECT_CLASS} value={contactId} onChange={(event) => setContactId(event.target.value)}>
              <option value="">{tx("preview.sample", "Sample data")}</option>
              {contacts.map((contact) => (
                <option key={contact.id} value={contact.id}>
                  {[contact.first_name, contact.last_name].filter(Boolean).join(" ") || contact.email || contact.phone}
                </option>
              ))}
            </select>
          </div>
        </Field>
        {draft.channel !== "whatsapp" ? (
          <div className="inline-flex rounded-full bg-muted p-0.5">
            {(["desktop", "mobile"] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={device === value}
                onClick={() => setDevice(value)}
                className={cn("inline-flex items-center gap-1 rounded-full px-3 py-1 text-[12.5px]", device === value ? "bg-background font-medium shadow-sm" : "text-muted-foreground")}
              >
                {value === "desktop" ? <Monitor className="h-3.5 w-3.5" aria-hidden /> : <Smartphone className="h-3.5 w-3.5" aria-hidden />}
                {value === "desktop" ? tx("preview.desktop", "Desktop") : tx("preview.mobile", "Mobile")}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {followUps.length ? (
        <div role="tablist" aria-label={tx("preview.messages", "Messages of the sequence")} className="flex flex-wrap gap-1" data-testid="preview-messages">
          {[tx("preview.firstEmail", "First email"), ...followUps.map((_, i) => tx("sequence.followUp", "Follow-up {{n}}", { n: i + 1 }))].map((label, i) => (
            <button
              key={label}
              type="button"
              role="tab"
              aria-selected={message === i}
              onClick={() => setMessage(i)}
              className={cn("rounded-full px-3 py-1 text-[12px]", message === i ? "bg-[#1d6af0] text-white" : "bg-muted text-muted-foreground hover:text-foreground")}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}
      {preview?.warnings.length ? (
        <ul className="space-y-1 rounded-xl border border-[#d4892a]/30 bg-[#d4892a]/[0.06] px-3 py-2 text-[12.5px] text-[#a15c07] dark:text-[#f0b35a]" data-testid="preview-warnings">
          {preview.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}

      <div className="rounded-2xl border border-border/60 bg-muted/20 p-3">
        {loading && !preview ? (
          <p className="flex items-center justify-center gap-2 py-16 text-[12.5px] text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {tx("preview.loading", "Rendering the preview...")}
          </p>
        ) : preview ? (
          draft.channel === "whatsapp" ? (
            <div className="mx-auto max-w-sm rounded-2xl bg-[#efeae2] p-4 dark:bg-[#0b141a]">
              <div className="ms-auto max-w-[85%] rounded-xl rounded-tr-sm bg-[#d9fdd3] px-3 py-2 text-[13px] text-[#111b21] shadow-sm dark:bg-[#005c4b] dark:text-[#e9edef]">
                <p className="whitespace-pre-wrap">{preview.text}</p>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="rounded-xl bg-background px-3 py-2 text-[12.5px]">
                <p className="font-semibold text-foreground">{preview.subject || tx("preview.noSubject", "(no subject)")}</p>
                {preview.preheader ? <p className="truncate text-muted-foreground">{preview.preheader}</p> : null}
              </div>
              <div className={cn("mx-auto overflow-hidden rounded-xl border border-border/60 bg-white transition-[width]", device === "mobile" ? "w-[375px] max-w-full" : "w-full")}>
                <iframe title={tx("preview.frame", "Email preview")} sandbox="" srcDoc={preview.html} className="h-[560px] w-full" />
              </div>
            </div>
          )
        ) : null}
      </div>

      <div className="rounded-2xl border border-border/60 p-4">
        <p className="text-[13px] font-semibold text-foreground">{tx("preview.test", "Send a test")}</p>
        <p className="mb-2 text-[12px] text-muted-foreground">
          {draft.channel === "whatsapp"
            ? tx("preview.testWaHelp", "One or more phone numbers in international format (+33612345678).")
            : tx("preview.testHelp", "One or more addresses, separated by commas. The subject starts with [TEST].")}
        </p>
        <div className="flex flex-wrap gap-2">
          <Input value={to} onChange={(event) => setTo(event.target.value)} placeholder={draft.channel === "whatsapp" ? "+33612345678" : "you@company.com, colleague@company.com"} className="min-w-[16rem] flex-1" />
          <Button
            className="rounded-full"
            disabled={testing || recipients.length === 0}
            onClick={() => {
              setTesting(true);
              setTestResult("");
              setError("");
              testCampaign(token, draft, recipients)
                .then((result) => setTestResult(tx("preview.testSent", "Test sent to {{to}}.", { to: result.sent.join(", ") })))
                .catch((err) => setError(errorText(err)))
                .finally(() => setTesting(false));
            }}
          >
            {testing ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : <Send className="me-1.5 h-4 w-4" aria-hidden />}
            {tx("preview.sendTest", "Send test")}
          </Button>
        </div>
        {testResult ? <p className="mt-2 text-[12.5px] text-[#1f9d74]" role="status">{testResult}</p> : null}
      </div>
    </div>
  );
}

function ScheduleStep({
  token,
  draft,
  set,
  tx,
  persist,
  onLaunched,
}: {
  token: string;
  draft: Partial<Campaign>;
  set: (patch: Partial<Campaign>) => void;
  tx: Tx;
  persist: () => Promise<Campaign>;
  onLaunched: (campaign: Campaign) => void;
}) {
  const [when, setWhen] = useState<"now" | "later">(draft.schedule_at ? "later" : "now");
  const [local, setLocal] = useState(isoToLocalInput(draft.schedule_at ?? ""));
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const iso = when === "later" ? localInputToIso(local) : "";
  const inPast = when === "later" && iso !== "" && new Date(iso).getTime() < Date.now() - 60_000;
  const [windowText, setWindowText] = useState("");
  useEffect(() => {
    let live = true;
    fetchCampaignSettings(token)
      .then((settings) => { if (live) setWindowText(sendWindowSummary(settings.send_window)); })
      .catch(() => undefined);
    return () => { live = false; };
  }, [token]);

  const launch = async () => {
    setBusy(true);
    setError("");
    try {
      const saved = await persist();
      const launched = await scheduleCampaign(token, saved.id, iso);
      onLaunched(launched);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      <div className="grid gap-3 sm:grid-cols-2">
        {(["now", "later"] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={when === value}
            onClick={() => setWhen(value)}
            className={cn("rounded-2xl border p-4 text-start", when === value ? "border-[#1d6af0] bg-[#1d6af0]/[0.05]" : "border-border/70 hover:bg-muted/30")}
          >
            <span className="block text-[13.5px] font-semibold text-foreground">{value === "now" ? tx("schedule.now", "Send now") : tx("schedule.later", "Schedule")}</span>
            <span className="block text-[12px] text-muted-foreground">
              {value === "now" ? tx("schedule.nowBody", "Starts within a minute, at the pace below.") : tx("schedule.laterBody", "Pick a date and time; the loop starts it on time.")}
            </span>
          </button>
        ))}
      </div>
      {when === "later" ? (
        <Field label={tx("schedule.at", "Date and time")} hint={tx("schedule.tz", "Your time zone: {{tz}}", { tz: localTimeZone() })}>
          <Input type="datetime-local" value={local} onChange={(event) => setLocal(event.target.value)} className="max-w-xs" />
        </Field>
      ) : null}
      {inPast ? <ErrorBanner message={tx("schedule.past", "This time is in the past.")} /> : null}
      {windowText ? (
        <p className="rounded-xl bg-muted/40 px-3 py-2 text-[12.5px] text-foreground/85" data-testid="schedule-window">
          {tx("schedule.window", "Sends only {{window}}; outside it, sending waits for the next slot.", { window: windowText })}
        </p>
      ) : null}
      {draft.channel !== "whatsapp" && (draft.steps ?? []).length ? (
        <p className="text-[12px] text-muted-foreground">
          {tx("schedule.sequence", "Then {{count}} follow-ups go out on their own days, only to people who have not replied.", { count: (draft.steps ?? []).length })}
        </p>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={tx("schedule.throttle", "Messages per minute")} hint={tx("schedule.throttleHint", "Slower sending protects your sender reputation.")}>
          <Input type="number" min={1} max={600} value={draft.throttle_per_minute ?? 30} onChange={(event) => set({ throttle_per_minute: Math.max(1, Math.min(600, Number(event.target.value) || 1)) })} />
        </Field>
        {draft.channel !== "whatsapp" ? (
          <div className="space-y-3 pt-1">
            <label className="flex items-center justify-between gap-3 text-[12.5px]">
              {tx("schedule.trackOpens", "Track opens")}
              <Switch checked={draft.track_opens ?? true} onChange={(next) => set({ track_opens: next })} label={tx("schedule.trackOpens", "Track opens")} />
            </label>
            <label className="flex items-center justify-between gap-3 text-[12.5px]">
              {tx("schedule.trackClicks", "Track clicks")}
              <Switch checked={draft.track_clicks ?? true} onChange={(next) => set({ track_clicks: next })} label={tx("schedule.trackClicks", "Track clicks")} />
            </label>
          </div>
        ) : null}
      </div>
      <div className="flex justify-end">
        <Button className="rounded-full" disabled={busy || (when === "later" && (!iso || inPast))} onClick={() => setConfirm(true)} data-testid="schedule-launch">
          <Send className="me-1.5 h-4 w-4" aria-hidden />
          {when === "now" ? tx("schedule.launch", "Launch campaign") : tx("schedule.confirmSchedule", "Schedule campaign")}
        </Button>
      </div>
      <Dialog open={confirm} onOpenChange={setConfirm}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{when === "now" ? tx("schedule.confirmNowTitle", "Send this campaign now?") : tx("schedule.confirmLaterTitle", "Schedule this campaign?")}</DialogTitle>
            <DialogDescription>
              {tx("schedule.confirmBody", "Real messages go to real people. You can pause or cancel while it sends.")}
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" className="rounded-full" onClick={() => setConfirm(false)}>
              {tx("cancel", "Cancel")}
            </Button>
            <Button className="rounded-full" disabled={busy} onClick={() => void launch()} data-testid="schedule-confirm">
              {busy ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
              {tx("schedule.confirm", "Confirm")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
