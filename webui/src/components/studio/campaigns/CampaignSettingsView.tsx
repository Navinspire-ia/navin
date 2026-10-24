// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, Loader2, MessageCircle, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { fetchCampaignSettings, saveCampaignSettings, testWhatsApp } from "@/lib/campaigns-api";
import type { CampaignSettings } from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import { Card, ErrorBanner, errorText, Field, Loading, localTimeZone, Notice, Switch, type Tx } from "./campaigns-ui";
import { WEEKDAYS, weekdayLabel } from "./SequenceEditor";

export function CampaignSettingsView({ token, tx, onSaved }: { token: string; tx: Tx; onSaved?: () => void }) {
  const [saved, setSaved] = useState<CampaignSettings | null>(null);
  const [draft, setDraft] = useState<CampaignSettings | null>(null);
  const [token2, setToken2] = useState("");
  const [secret2, setSecret2] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [waTo, setWaTo] = useState("");
  const [waBusy, setWaBusy] = useState(false);
  const [waResult, setWaResult] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let live = true;
    fetchCampaignSettings(token)
      .then((next) => {
        if (!live) return;
        setSaved(next);
        setDraft(next);
      })
      .catch((err) => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [token]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 3500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  if (!draft) return error ? <div className="p-6"><ErrorBanner message={error} /></div> : <Loading label={tx("loading", "Loading...")} />;
  const set = (patch: Partial<CampaignSettings>) => setDraft((prev) => (prev ? { ...prev, ...patch } : prev));
  const setWa = (patch: Partial<CampaignSettings["whatsapp"]>) =>
    setDraft((prev) => (prev ? { ...prev, whatsapp: { ...prev.whatsapp, ...patch } } : prev));
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved) || token2 !== "" || secret2 !== "";

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const next = await saveCampaignSettings(token, {
        ...draft,
        whatsapp: { ...draft.whatsapp, token: token2, app_secret: secret2 },
      });
      setSaved(next);
      setDraft(next);
      setToken2("");
      setSecret2("");
      setNotice(tx("settings.saved", "Settings saved."));
      onSaved?.();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const modes: Array<{ id: CampaignSettings["auto_reply_mode"]; label: string; body: string }> = [
    { id: "off", label: tx("settings.autoOff", "Classify only"), body: tx("settings.autoOffBody", "Replies are read and classified; you answer yourself.") },
    { id: "draft", label: tx("settings.autoDraft", "Draft answers"), body: tx("settings.autoDraftBody", "The campaign agent drafts an answer and a hand-off brief for you to review.") },
    { id: "send", label: tx("settings.autoSend", "Answer on its own"), body: tx("settings.autoSendBody", "The agent answers clear cases itself and hands the rest to a teammate or to you.") },
  ];

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-4 md:p-6">
      <div className="flex items-center gap-2">
        <h2 className="flex-1 text-[18px] font-semibold text-foreground">{tx("settings.title", "Campaign settings")}</h2>
        <Button size="sm" className="rounded-full" disabled={busy || !dirty} onClick={() => void save()}>
          {busy ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : <Save className="me-1.5 h-4 w-4" aria-hidden />}
          {tx("save", "Save")}
        </Button>
      </div>
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      <Notice message={notice} />

      <Card className="space-y-3 p-4">
        <p className="text-[13px] font-semibold text-foreground">{tx("settings.sender", "Sender")}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={tx("settings.fromName", "Sender name")}>
            <Input value={draft.from_name} maxLength={80} onChange={(event) => set({ from_name: event.target.value })} />
          </Field>
          <Field label={tx("settings.fromEmail", "Sender address")} hint={tx("settings.fromEmailHint", "From Settings > Channels > Email (SMTP).")}>
            <Input value={draft.from_email || tx("settings.noSmtp", "Not configured")} readOnly disabled />
          </Field>
          <Field label={tx("settings.replyTo", "Reply-to address")} hint={tx("settings.replyToHint", "Use the inbox Navin reads (IMAP) so replies are classified.")}>
            <Input type="email" value={draft.reply_to} onChange={(event) => set({ reply_to: event.target.value })} />
          </Field>
        </div>
      </Card>

      <Card className="space-y-3 p-4">
        <p className="text-[13px] font-semibold text-foreground">{tx("settings.legal", "Footer and unsubscribe")}</p>
        <Field label={tx("settings.address", "Company postal address")} hint={tx("settings.addressHint", "Required by anti-spam laws (CAN-SPAM, GDPR practice).")}>
          <Input value={draft.company_address} onChange={(event) => set({ company_address: event.target.value })} />
        </Field>
        <Field label={tx("settings.footer", "Footer (HTML allowed)")}>
          <Textarea rows={3} value={draft.footer_html} onChange={(event) => set({ footer_html: event.target.value })} />
        </Field>
        <Field label={tx("settings.unsubscribe", "Unsubscribe link text")}>
          <Input value={draft.unsubscribe_text} onChange={(event) => set({ unsubscribe_text: event.target.value })} placeholder={tx("settings.unsubscribePlaceholder", "Unsubscribe")} />
        </Field>
      </Card>

      <Card className="space-y-3 p-4">
        <p className="text-[13px] font-semibold text-foreground">{tx("settings.pace", "Pace and limits")}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={tx("settings.throttle", "Default messages per minute")}>
            <Input type="number" min={1} max={600} value={draft.throttle_per_minute} onChange={(event) => set({ throttle_per_minute: Math.max(1, Number(event.target.value) || 1) })} />
          </Field>
          <Field label={tx("settings.dailyCap", "Daily cap (0 = none)")} hint={tx("settings.dailyCapHint", "Most SMTP providers limit sends per day.")}>
            <Input type="number" min={0} value={draft.daily_cap} onChange={(event) => set({ daily_cap: Math.max(0, Number(event.target.value) || 0) })} />
          </Field>
        </div>
      </Card>

      <SendWindowCard draft={draft} set={set} tx={tx} />

      <Card className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[13px] font-semibold text-foreground">{tx("settings.tracking", "Open and click tracking")}</p>
            <p className="text-[12px] text-muted-foreground">
              {tx("settings.trackingHelp", "Opens, clicks and unsubscribe links need a public URL that reaches this Navin (a tunnel or your server). Without it, sending still works, but opens and clicks are not counted and replies stay the main signal.")}
            </p>
          </div>
          <Switch checked={draft.tracking_enabled} onChange={(next) => set({ tracking_enabled: next })} label={tx("settings.tracking", "Open and click tracking")} />
        </div>
        <Field label={tx("settings.baseUrl", "Public base URL")}>
          <Input value={draft.tracking_base_url} onChange={(event) => set({ tracking_base_url: event.target.value })} placeholder="https://navin.example.com" disabled={!draft.tracking_enabled} />
        </Field>
      </Card>

      <Card className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[13px] font-semibold text-foreground">{tx("settings.loop", "Campaign agent loop")}</p>
            <p className="text-[12px] text-muted-foreground">{tx("settings.loopHelp", "Sends scheduled campaigns on time and checks the inbox for replies, every minute or so.")}</p>
          </div>
          <Switch checked={draft.loop_enabled} onChange={(next) => set({ loop_enabled: next })} label={tx("settings.loop", "Campaign agent loop")} />
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          {modes.map((mode) => (
            <button
              key={mode.id}
              type="button"
              aria-pressed={draft.auto_reply_mode === mode.id}
              onClick={() => set({ auto_reply_mode: mode.id })}
              className={cn("rounded-xl border p-3 text-start", draft.auto_reply_mode === mode.id ? "border-[#1d6af0] bg-[#1d6af0]/[0.05]" : "border-border/70 hover:bg-muted/30")}
            >
              <span className="block text-[12.5px] font-semibold text-foreground">{mode.label}</span>
              <span className="block text-[11.5px] leading-snug text-muted-foreground">{mode.body}</span>
            </button>
          ))}
        </div>
      </Card>

      <Card className="space-y-3 p-4">
        <div className="flex items-center gap-2">
          <MessageCircle className="h-4 w-4 text-[#1f9d74]" aria-hidden />
          <p className="flex-1 text-[13px] font-semibold text-foreground">{tx("settings.whatsapp", "WhatsApp Cloud API")}</p>
          <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", draft.whatsapp.configured ? "bg-[#1f9d74]/10 text-[#1f9d74]" : "bg-muted text-muted-foreground")}>
            {draft.whatsapp.configured ? tx("settings.waReady", "Connected") : tx("settings.waMissing", "Not connected")}
          </span>
        </div>
        <p className="text-[12px] text-muted-foreground">
          {tx("settings.waHelp", "From Meta for Developers > your app > WhatsApp > API setup: the phone number id, the WhatsApp Business account id and a permanent access token. Campaigns use approved templates only.")}
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={tx("settings.waPhone", "Phone number id")}>
            <Input value={draft.whatsapp.phone_number_id} onChange={(event) => setWa({ phone_number_id: event.target.value })} />
          </Field>
          <Field label={tx("settings.waWaba", "WhatsApp Business account id")}>
            <Input value={draft.whatsapp.waba_id} onChange={(event) => setWa({ waba_id: event.target.value })} />
          </Field>
          <Field
            label={tx("settings.waToken", "Access token")}
            hint={draft.whatsapp.token_hint ? tx("settings.waTokenHint", "Saved ({{hint}}). Type a new one to replace it.", { hint: draft.whatsapp.token_hint }) : undefined}
            className="sm:col-span-2"
          >
            <Input type="password" autoComplete="off" value={token2} onChange={(event) => setToken2(event.target.value)} placeholder={draft.whatsapp.token_hint ? "••••••••" : ""} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Input value={waTo} onChange={(event) => setWaTo(event.target.value)} placeholder="+33612345678" className="max-w-[14rem]" aria-label={tx("settings.waTestTo", "Test number")} />
          <Button
            size="sm"
            variant="outline"
            className="rounded-full"
            disabled={waBusy || !waTo.trim() || dirty || !draft.whatsapp.configured}
            title={dirty ? tx("settings.saveFirst", "Save first") : undefined}
            onClick={() => {
              setWaBusy(true);
              setWaResult(null);
              testWhatsApp(token, waTo.trim())
                .then((result) => setWaResult({ ok: result.ok, text: result.detail }))
                .catch((err) => setWaResult({ ok: false, text: errorText(err) }))
                .finally(() => setWaBusy(false));
            }}
          >
            {waBusy ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
            {tx("settings.waTest", "Test")}
          </Button>
          {waResult ? <span role="status" className={cn("text-[12px]", waResult.ok ? "text-[#1f9d74]" : "text-[#c2410c]")}>{waResult.text}</span> : null}
        </div>

        <div className="space-y-3 border-t border-border/60 pt-3" data-testid="wa-webhook">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-[12.5px] font-semibold text-foreground">{tx("settings.waWebhook", "Receive replies (webhook)")}</p>
              <p className="text-[12px] text-muted-foreground">
                {tx("settings.waWebhookBody", "WhatsApp answers, delivery and read receipts arrive here. Read receipts count as opens.")}
              </p>
            </div>
            <Switch
              checked={Boolean(draft.whatsapp.webhook_enabled)}
              onChange={(next) => setWa({ webhook_enabled: next })}
              label={tx("settings.waWebhook", "Receive replies (webhook)")}
            />
          </div>
          {draft.whatsapp.webhook_enabled ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={tx("settings.waPort", "Local port")}>
                  <Input
                    type="number"
                    min={1024}
                    max={65535}
                    value={draft.whatsapp.webhook_port || 18795}
                    onChange={(event) => setWa({ webhook_port: Math.max(1, Number(event.target.value) || 18795) })}
                  />
                </Field>
                <Field
                  label={tx("settings.waSecret", "App secret")}
                  hint={draft.whatsapp.app_secret_hint ? tx("settings.waSecretHint", "Saved ({{hint}}). Type a new one to replace it.", { hint: draft.whatsapp.app_secret_hint }) : tx("settings.waSecretHelp", "Meta app > Settings > Basic. Used to verify that webhook calls come from Meta.")}
                >
                  <Input type="password" autoComplete="off" value={secret2} onChange={(event) => setSecret2(event.target.value)} placeholder={draft.whatsapp.app_secret_hint ? "••••••••" : ""} />
                </Field>
                <Field label={tx("settings.waVerify", "Verify token")} className="sm:col-span-2" hint={draft.whatsapp.verify_token ? undefined : tx("settings.waVerifyPending", "Generated when you save with the webhook on.")}>
                  <div className="flex gap-1.5">
                    <Input value={draft.whatsapp.verify_token || ""} readOnly className="font-mono" />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      className="h-10 w-10"
                      disabled={!draft.whatsapp.verify_token}
                      aria-label={tx("settings.copy", "Copy")}
                      onClick={() => {
                        void navigator.clipboard?.writeText(draft.whatsapp.verify_token).then(() => {
                          setCopied(true);
                          window.setTimeout(() => setCopied(false), 1500);
                        });
                      }}
                    >
                      {copied ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
                    </Button>
                  </div>
                </Field>
              </div>
              <p className="rounded-xl bg-muted/40 px-3 py-2 text-[12px] leading-relaxed text-muted-foreground">
                {tx(
                  "settings.waWebhookSteps",
                  "Expose port {{port}} publicly (reverse proxy or tunnel) and set the Callback URL https://<your public host>{{path}} with this verify token in Meta > WhatsApp > Configuration, subscribed to 'messages'.",
                  { port: draft.whatsapp.webhook_port || 18795, path: draft.whatsapp.webhook_path || "/whatsapp/webhook" },
                )}
              </p>
            </>
          ) : null}
        </div>
      </Card>
    </div>
  );
}


function timeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    const zones = intl.supportedValuesOf?.("timeZone");
    if (zones?.length) return zones;
  } catch {
    // Older engines: fall back to a short list below.
  }
  return ["UTC", "Europe/Paris", "Europe/London", "Africa/Casablanca", "Asia/Dubai", "Asia/Riyadh", "America/New_York", "America/Los_Angeles"];
}

/** Business-hours sending and the domain check, like a sales sequence tool. */
export function SendWindowCard({
  draft,
  set,
  tx,
}: {
  draft: CampaignSettings;
  set: (patch: Partial<CampaignSettings>) => void;
  tx: Tx;
}) {
  const window = draft.send_window ?? { enabled: false, days: [1, 2, 3, 4, 5], start: "09:00", end: "18:00", timezone: localTimeZone() };
  const zones = useMemo(timeZones, []);
  const setWindow = (patch: Partial<CampaignSettings["send_window"]>) => set({ send_window: { ...window, ...patch } });
  const invalidHours = window.start >= window.end;
  return (
    <Card className="space-y-3 p-4" testId="settings-send-window">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[13px] font-semibold text-foreground">{tx("settings.window", "Sending window")}</p>
          <p className="text-[12px] text-muted-foreground">
            {tx("settings.windowHelp", "Send only on business days and hours, in the time zone below. Campaigns and follow-ups wait for the next open slot.")}
          </p>
        </div>
        <Switch
          checked={window.enabled}
          onChange={(next) => setWindow(next && !draft.send_window?.enabled ? { enabled: true, timezone: window.timezone || localTimeZone() } : { enabled: next })}
          label={tx("settings.window", "Sending window")}
        />
      </div>
      <div className={cn("space-y-3", !window.enabled && "pointer-events-none opacity-50")} aria-disabled={!window.enabled}>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label={tx("settings.windowDays", "Days")}>
          {WEEKDAYS.map((day) => {
            const on = window.days.includes(day);
            return (
              <button
                key={day}
                type="button"
                aria-pressed={on}
                disabled={!window.enabled}
                onClick={() => setWindow({ days: on ? window.days.filter((value) => value !== day) : [...window.days, day].sort((a, b) => a - b) })}
                className={cn("h-8 min-w-[3rem] rounded-full px-3 text-[12px] capitalize", on ? "bg-[#1d6af0] text-white" : "bg-muted text-muted-foreground hover:text-foreground")}
              >
                {weekdayLabel(day)}
              </button>
            );
          })}
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={tx("settings.windowStart", "From")}>
            <Input type="time" value={window.start} disabled={!window.enabled} onChange={(event) => setWindow({ start: event.target.value })} />
          </Field>
          <Field label={tx("settings.windowEnd", "To")}>
            <Input type="time" value={window.end} disabled={!window.enabled} onChange={(event) => setWindow({ end: event.target.value })} />
          </Field>
          <Field label={tx("settings.windowZone", "Time zone")}>
            <Input
              list="campaign-time-zones"
              value={window.timezone}
              disabled={!window.enabled}
              onChange={(event) => setWindow({ timezone: event.target.value })}
              placeholder={localTimeZone()}
            />
            <datalist id="campaign-time-zones">
              {zones.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
          </Field>
        </div>
        {invalidHours ? <ErrorBanner message={tx("settings.windowInvalid", "The window must end after it starts.")} /> : null}
        {window.enabled && window.days.length === 0 ? <ErrorBanner message={tx("settings.windowNoDay", "Pick at least one day.")} /> : null}
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-border/60 pt-3">
        <div>
          <p className="text-[13px] font-semibold text-foreground">{tx("settings.verifyDomains", "Check domains before sending")}</p>
          <p className="text-[12px] text-muted-foreground">
            {tx("settings.verifyDomainsHelp", "Skips addresses whose domain cannot receive email, to protect your sender reputation.")}
          </p>
        </div>
        <Switch checked={draft.verify_domains ?? true} onChange={(next) => set({ verify_domains: next })} label={tx("settings.verifyDomains", "Check domains before sending")} />
      </div>
    </Card>
  );
}
