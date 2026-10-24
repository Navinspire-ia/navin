// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Reply } from "@/lib/campaigns-types";

const api = vi.hoisted(() => ({
  previewImport: vi.fn(),
  commitImport: vi.fn(),
  sendReply: vi.fn(),
  updateReply: vi.fn(),
  fileToBase64: vi.fn(async () => "Y3N2"),
  fetchCampaignSettings: vi.fn(),
  saveCampaignSettings: vi.fn(),
}));
vi.mock("@/lib/campaigns-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/campaigns-api")>()),
  ...api,
}));

// TipTap is not needed to test the sequence logic: a plain textarea stands in.
vi.mock("./EmailEditor", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./EmailEditor")>()),
  EmailEditor: ({ value, onChange, placeholder }: { value: string; onChange: (html: string) => void; placeholder?: string }) => (
    <textarea aria-label="email body" placeholder={placeholder} value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));

import { useState } from "react";
import type { Campaign, CampaignSettings, VariantStats } from "@/lib/campaigns-types";
import { CampaignSettingsView } from "./CampaignSettingsView";
import { abWinner, followUpSubject, moveStep, SequenceEditor, sequenceDays, sendWindowSummary } from "./SequenceEditor";
import { crmListName } from "./CrmAudiencePicker";
import { emptyCampaign, stepProblems } from "./CampaignWizard";
import { ImportWizard } from "./ContactsView";
import { ReplyDetail, whatsappWindowEnd } from "./RepliesInbox";
import type { Tx } from "./campaigns-ui";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const tx: Tx = (_key, fallback, values) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ""));

describe("wizard validation", () => {
  it("blocks each step until it is complete", () => {
    const draft = emptyCampaign();
    expect(stepProblems("setup", draft, tx)).toEqual(["Give the campaign a name."]);
    expect(stepProblems("audience", { ...draft, name: "x" }, tx)).toHaveLength(1);
    expect(stepProblems("audience", { ...draft, list_ids: ["l1"] }, tx)).toEqual([]);
    expect(stepProblems("content", { ...draft, subject: "Hi", html: "<p></p>" }, tx)).toEqual(["Write the email body."]);
    expect(stepProblems("content", { ...draft, subject: "Hi", html: "<p>Hello</p>" }, tx)).toEqual([]);
    const wa = emptyCampaign("whatsapp");
    expect(stepProblems("content", wa, tx)).toEqual(["Pick an approved template or write a message."]);
    expect(stepProblems("content", { ...wa, wa_template: "offer", wa_params: ["{{first_name}}", ""] }, tx)).toEqual([
      "Fill every template parameter.",
    ]);
  });

  it("names a CRM list after its filters", () => {
    expect(crmListName(["leads"], { status: ["qualified"], country: ["France"], score_min: 60 }, (k) => k)).toBe(
      "CRM leads: qualified, France, score >= 60",
    );
  });
});

describe("import wizard", () => {
  it("previews a file, lets the user remap a column, then commits to a new list", async () => {
    api.previewImport.mockResolvedValue({
      import_id: "imp1",
      filename: "people.csv",
      columns: ["Mail", "Prenom", "Ville"],
      sample: [["a@b.co", "Ana", "Lyon"]],
      rows: 12,
      mapping: { Mail: "email", Prenom: "first_name", Ville: "ignore" },
    });
    api.commitImport.mockResolvedValue({ created: 12, updated: 0, skipped: 0, errors: [], list_id: "l9" });
    const onDone = vi.fn();
    render(<ImportWizard token="t" lists={[]} defaultListId="" tx={tx} onDone={onDone} />);
    const file = new File(["Mail,Prenom\n"], "people.csv", { type: "text/csv" });
    fireEvent.change(screen.getByTestId("import-file"), { target: { files: [file] } });
    await screen.findByText("people.csv: 12 rows, 3 columns");
    fireEvent.change(screen.getByLabelText("Field for Ville"), { target: { value: "city" } });
    fireEvent.click(screen.getByRole("button", { name: "Import 12 rows" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(api.commitImport).toHaveBeenCalledWith("t", {
      import_id: "imp1",
      mapping: { Mail: "email", Prenom: "first_name", Ville: "city" },
      new_list_name: "people",
      tags: [],
    });
  });
});

const reply: Reply = {
  id: "r1",
  channel: "email",
  campaign_id: "c1",
  campaign_name: "Spring",
  contact_id: "ct1",
  contact_name: "Ana",
  from_addr: "ana@b.co",
  subject: "Offer",
  body: "Can we talk tomorrow?",
  received_at: new Date().toISOString(),
  classification: "meeting",
  confidence: 0.9,
  summary: "Wants a call",
  status: "new",
  assigned_to: "",
  handoff_brief: "",
  draft_reply: "",
  answered_at: "",
};

describe("replies", () => {
  it("sends an email answer with a Re: subject", async () => {
    api.sendReply.mockResolvedValue({ ...reply, status: "answered" });
    const onChanged = vi.fn();
    render(<ReplyDetail token="t" tx={tx} reply={reply} teammates={["Jack"]} onChanged={onChanged} />);
    fireEvent.change(screen.getByPlaceholderText("Write your answer, or let the campaign agent draft it."), {
      target: { value: "Sure, 10am?" },
    });
    fireEvent.click(screen.getByTestId("reply-send"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(api.sendReply).toHaveBeenCalledWith("t", "r1", "Sure, 10am?", "Re: Offer");
  });

  it("closes WhatsApp answers after the 24h window", () => {
    const old = { ...reply, channel: "whatsapp" as const, received_at: new Date(Date.now() - 25 * 3600 * 1000).toISOString() };
    expect(whatsappWindowEnd(reply)).toBeNull();
    expect(whatsappWindowEnd(old)!.getTime()).toBeLessThan(Date.now());
    render(<ReplyDetail token="t" tx={tx} reply={{ ...old, draft_reply: "Hello" }} teammates={[]} onChanged={vi.fn()} />);
    expect(screen.getByTestId("wa-window").textContent).toMatch(/closed/);
    expect((screen.getByTestId("reply-send") as HTMLButtonElement).disabled).toBe(true);
  });
});


function SequenceHarness({ onChange }: { onChange: (draft: Partial<Campaign>) => void }) {
  const [draft, setDraft] = useState<Partial<Campaign>>({ ...emptyCampaign(), subject: "Quick question", html: "<p>Hi</p>" });
  return (
    <SequenceEditor
      draft={draft}
      tx={tx}
      attrKeys={[]}
      set={(patch) => setDraft((prev) => {
        const next = { ...prev, ...patch };
        onChange(next);
        return next;
      })}
    />
  );
}

describe("sequence editor", () => {
  it("adds, edits, reorders and deletes follow-ups, with a cumulative day timeline", () => {
    let latest: Partial<Campaign> = {};
    render(<SequenceHarness onChange={(draft) => { latest = draft; }} />);
    fireEvent.click(screen.getByTestId("sequence-add"));
    fireEvent.change(screen.getByLabelText("email body"), { target: { value: "<p>Bumping this</p>" } });
    fireEvent.click(screen.getByTestId("sequence-add"));
    fireEvent.change(screen.getByLabelText("email body"), { target: { value: "<p>Last try</p>" } });
    fireEvent.change(screen.getByLabelText("Wait before follow-up 2"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Condition of follow-up 2"), { target: { value: "no_open" } });
    expect(latest.steps?.map((step) => step.html)).toEqual(["<p>Bumping this</p>", "<p>Last try</p>"]);
    expect(screen.getByText(/Day 7 · Follow-up 2/)).toBeTruthy();   // 3 + 4 days
    fireEvent.click(screen.getAllByRole("button", { name: "Move up" })[1]);
    expect(latest.steps?.map((step) => step.html)).toEqual(["<p>Last try</p>", "<p>Bumping this</p>"]);
    expect(latest.steps?.[0].condition).toBe("no_open");
    fireEvent.click(screen.getByRole("button", { name: "Delete follow-up 2" }));
    expect(latest.steps).toHaveLength(1);
    expect(screen.getAllByTestId("sequence-step")).toHaveLength(1);
  });

  it("validates follow-ups, threads subjects and computes days", () => {
    const draft = { ...emptyCampaign(), subject: "Offer", html: "<p>x</p>" };
    const step = { id: "s1", delay_days: 3, condition: "no_reply" as const, subject: "", html: "<p></p>" };
    expect(stepProblems("content", { ...draft, steps: [step] }, tx)).toEqual(["Write follow-up 1 or delete it."]);
    expect(stepProblems("content", { ...draft, subject_variants: [" "] }, tx)).toEqual(["Fill or remove the empty A/B subject."]);
    expect(followUpSubject(step, "Offer")).toBe("Re: Offer");
    expect(followUpSubject(step, "Re: Offer")).toBe("Re: Offer");
    expect(followUpSubject({ ...step, subject: "Last call" }, "Offer")).toBe("Last call");
    expect(sequenceDays([step, { ...step, delay_days: 4 }, { ...step, delay_days: 7 }])).toEqual([3, 7, 14]);
    expect(moveStep([step], 0, -1)).toEqual([step]);
  });
});

describe("A/B results", () => {
  const variant = (letter: "A" | "B", sent: number, reply_rate: number, open_rate: number): VariantStats => ({
    variant: letter, subject: letter, sent, opened: 0, clicked: 0, replied: 0, open_rate, reply_rate,
  });
  it("names a winner on reply rate then open rate, only with enough sends", () => {
    expect(abWinner([variant("A", 19, 0.2, 0.5), variant("B", 40, 0.1, 0.4)])).toBeNull();
    expect(abWinner([variant("A", 25, 0.08, 0.5), variant("B", 25, 0.12, 0.3)])?.variant).toBe("B");
    expect(abWinner([variant("A", 25, 0.1, 0.5), variant("B", 25, 0.1, 0.3)])?.variant).toBe("A");
    expect(abWinner([variant("A", 25, 0.1, 0.5), variant("B", 25, 0.1, 0.5)])).toBeNull();
  });
});

describe("sending window settings", () => {
  const settings: CampaignSettings = {
    from_name: "Acme", from_email: "news@acme.io", reply_to: "", throttle_per_minute: 30, daily_cap: 500,
    company_address: "1 rue X", footer_html: "", unsubscribe_text: "Unsubscribe", tracking_enabled: false,
    tracking_base_url: "", auto_reply_mode: "draft", loop_enabled: true,
    whatsapp: { phone_number_id: "", waba_id: "", token: "", token_hint: "", configured: false },
    send_window: { enabled: false, days: [1, 2, 3, 4, 5], start: "09:00", end: "18:00", timezone: "Europe/Paris" },
    verify_domains: true,
  } as CampaignSettings;

  it("saves business hours and the domain check", async () => {
    api.fetchCampaignSettings.mockResolvedValue(settings);
    api.saveCampaignSettings.mockImplementation(async (_token: string, body: CampaignSettings) => body);
    render(<CampaignSettingsView token="t" tx={tx} />);
    const card = await screen.findByTestId("settings-send-window");
    fireEvent.click(card.querySelector('[role="switch"]') as Element);
    const days = card.querySelectorAll('[role="group"] button');
    fireEvent.click(days[5]);   // add Saturday
    fireEvent.click(days[0]);   // drop Monday
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "08:30" } });
    fireEvent.click(screen.getByRole("switch", { name: "Check domains before sending" }));
    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() => expect(api.saveCampaignSettings).toHaveBeenCalled());
    const body = api.saveCampaignSettings.mock.calls[0][1] as CampaignSettings;
    expect(body.send_window).toMatchObject({ enabled: true, days: [2, 3, 4, 5, 6], start: "08:30", end: "18:00" });
    expect(body.verify_domains).toBe(false);
    expect(sendWindowSummary({ ...body.send_window, timezone: "UTC" }, "en-US")).toBe("Tue-Sat 08:30-18:00 UTC");
  });
});
