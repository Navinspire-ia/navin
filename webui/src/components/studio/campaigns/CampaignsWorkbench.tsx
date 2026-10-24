// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Inbox,
  LayoutDashboard,
  LayoutTemplate,
  MessageSquare,
  Radar,
  Send,
  Settings2,
  Sparkles,
  Users,
} from "lucide-react";

import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { fetchSettings } from "@/lib/api";
import {
  fetchCampaignDashboard,
  listCampaigns,
  listContacts,
  listLists,
  listTemplates,
  runCampaignLoop,
} from "@/lib/campaigns-api";
import type {
  Campaign,
  CampaignDashboard,
  CampaignStatus,
  ContactList,
  ReplyClass,
  Template,
} from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";
import { CampaignDetailView, CampaignsListView } from "./CampaignsList";
import { CampaignsDashboardView } from "./CampaignsDashboard";
import { CampaignSettingsView } from "./CampaignSettingsView";
import { CampaignWizard, emptyCampaign } from "./CampaignWizard";
import { ContactsView } from "./ContactsView";
import { ProspectsView } from "./ProspectsView";
import { RepliesInbox } from "./RepliesInbox";
import { TemplatesView } from "./TemplatesView";
import { ErrorBanner, errorText, Loading, useCampaignsTx } from "./campaigns-ui";

export type CampaignsTab = "dashboard" | "campaigns" | "prospects" | "contacts" | "templates" | "replies" | "settings";

const TABS: CampaignsTab[] = ["dashboard", "campaigns", "prospects", "contacts", "templates", "replies", "settings"];

function tabFromHash(): CampaignsTab {
  if (typeof window === "undefined") return "dashboard";
  const query = window.location.hash.split("?")[1] ?? "";
  const tab = new URLSearchParams(query).get("tab") as CampaignsTab | null;
  return tab && TABS.includes(tab) ? tab : "dashboard";
}

export function CampaignsWorkbench({
  sessionKey = null,
  chatOpen,
  onToggleChat,
  onSeed,
}: {
  /** The active chat session: the CRM picker reads that project's CRM. */
  sessionKey?: string | null;
  chatOpen?: boolean;
  onToggleChat?: () => void;
  onSeed?: (text: string) => void;
}) {
  const tx = useCampaignsTx();
  const { token } = useClient();
  const [tab, setTabState] = useState<CampaignsTab>(tabFromHash);
  const [dashboard, setDashboard] = useState<CampaignDashboard | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaignStatus, setCampaignStatus] = useState<CampaignStatus | "">("");
  const [campaignsLoading, setCampaignsLoading] = useState(true);
  const [lists, setLists] = useState<ContactList[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [attrKeys, setAttrKeys] = useState<string[]>([]);
  const [teammates, setTeammates] = useState<string[]>([]);
  const [editing, setEditing] = useState<Partial<Campaign> | null>(null);
  const [openCampaignId, setOpenCampaignId] = useState("");
  const [replyFilter, setReplyFilter] = useState<{ classification: ReplyClass | ""; campaign: string }>({ classification: "", campaign: "" });
  const [error, setError] = useState("");

  const setTab = useCallback((next: CampaignsTab) => {
    setTabState(next);
    setEditing(null);
    if (next !== "campaigns") setOpenCampaignId("");
    const base = window.location.hash.split("?")[0] || "#/campaigns";
    if (base.startsWith("#/campaigns")) {
      const params = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
      if (next === "dashboard") params.delete("tab");
      else params.set("tab", next);
      const query = params.toString();
      window.history.replaceState(null, "", `${base}${query ? `?${query}` : ""}`);
    }
  }, []);

  const loadDashboard = useCallback(async () => {
    try {
      setDashboard(await fetchCampaignDashboard(token));
      setError("");
    } catch (err) {
      setError(errorText(err));
    }
  }, [token]);
  const loadCampaigns = useCallback(async () => {
    setCampaignsLoading(true);
    try {
      setCampaigns(await listCampaigns(token, campaignStatus));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setCampaignsLoading(false);
    }
  }, [campaignStatus, token]);
  const loadLists = useCallback(async () => {
    try {
      setLists(await listLists(token));
    } catch (err) {
      setError(errorText(err));
    }
  }, [token]);
  const loadTemplates = useCallback(async () => {
    try {
      setTemplates(await listTemplates(token));
    } catch (err) {
      setError(errorText(err));
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    void loadDashboard();
    void loadLists();
    void loadTemplates();
  }, [loadDashboard, loadLists, loadTemplates, token]);
  useEffect(() => {
    if (token) void loadCampaigns();
  }, [loadCampaigns, token]);
  // Custom import columns become {{attrs.*}} merge tags in the editors.
  useEffect(() => {
    if (!token) return;
    let live = true;
    listContacts(token, { limit: 50 })
      .then((page) => {
        if (!live) return;
        const keys = new Set<string>();
        for (const contact of page.items) for (const key of Object.keys(contact.attrs ?? {})) keys.add(key);
        setAttrKeys([...keys].sort().slice(0, 30));
      })
      .catch(() => undefined);
    fetchSettings(token)
      .then((settings) => {
        if (live) setTeammates((settings.collab_members ?? []).map((member) => member.name).filter(Boolean));
      })
      .catch(() => undefined);
    return () => { live = false; };
  }, [token]);
  // Live numbers while something is sending.
  useEffect(() => {
    if (!token || tab !== "dashboard") return;
    const timer = window.setInterval(() => void loadDashboard(), 15000);
    return () => window.clearInterval(timer);
  }, [loadDashboard, tab, token]);

  const refreshAll = useCallback(() => {
    void loadDashboard();
    void loadCampaigns();
  }, [loadCampaigns, loadDashboard]);

  const labels: Record<CampaignsTab, { label: string; icon: typeof Send }> = useMemo(
    () => ({
      dashboard: { label: tx("tabs.dashboard", "Dashboard"), icon: LayoutDashboard },
      campaigns: { label: tx("tabs.campaigns", "Campaigns"), icon: Send },
      prospects: { label: tx("tabs.prospects", "Prospects"), icon: Radar },
      contacts: { label: tx("tabs.contacts", "Contacts and lists"), icon: Users },
      templates: { label: tx("tabs.templates", "Templates"), icon: LayoutTemplate },
      replies: { label: tx("tabs.replies", "Replies"), icon: Inbox },
      settings: { label: tx("tabs.settings", "Settings"), icon: Settings2 },
    }),
    [tx],
  );
  const newReplies = dashboard?.totals.replies_new ?? 0;

  const openNew = (listId?: string) => {
    setTabState("campaigns");
    setOpenCampaignId("");
    setEditing(listId ? { ...emptyCampaign(), list_ids: [listId] } : emptyCampaign());
  };
  const [contactsListId, setContactsListId] = useState("");

  let body: React.ReactNode;
  if (!token) {
    body = <Loading label={tx("loading", "Loading...")} />;
  } else if (editing) {
    body = (
      <CampaignWizard
        token={token}
        sessionKey={sessionKey}
        tx={tx}
        initial={editing}
        lists={lists}
        templates={templates}
        attrKeys={attrKeys}
        onClose={() => {
          setEditing(null);
          refreshAll();
        }}
        onSaved={() => void loadCampaigns()}
        onLaunched={(campaign) => {
          setEditing(null);
          setOpenCampaignId(campaign.id);
          refreshAll();
        }}
        onListsChanged={loadLists}
        onTemplatesChanged={loadTemplates}
      />
    );
  } else if (tab === "dashboard") {
    body = dashboard ? (
      <CampaignsDashboardView
        data={dashboard}
        tx={tx}
        onNewCampaign={openNew}
        onOpenCampaign={(campaign) => {
          setTab("campaigns");
          setOpenCampaignId(campaign.id);
        }}
        onOpenReplies={(classification) => {
          setReplyFilter({ classification: classification ?? "", campaign: "" });
          setTab("replies");
        }}
        onOpenSettings={() => setTab("settings")}
        onRunLoop={async () => {
          try {
            const loop = await runCampaignLoop(token);
            setDashboard((prev) => (prev ? { ...prev, loop } : prev));
            await loadDashboard();
          } catch (err) {
            setError(errorText(err));
          }
        }}
      />
    ) : (
      <Loading label={tx("loading", "Loading...")} />
    );
  } else if (tab === "campaigns") {
    body = openCampaignId ? (
      <CampaignDetailView
        token={token}
        campaignId={openCampaignId}
        tx={tx}
        onBack={() => {
          setOpenCampaignId("");
          refreshAll();
        }}
        onEdit={(campaign) => setEditing(campaign)}
        onChanged={() => refreshAll()}
        onOpenReplies={(campaignId) => {
          setReplyFilter({ classification: "", campaign: campaignId });
          setTab("replies");
        }}
      />
    ) : (
      <CampaignsListView
        campaigns={campaigns}
        loading={campaignsLoading}
        tx={tx}
        status={campaignStatus}
        onStatus={setCampaignStatus}
        onNew={openNew}
        onOpen={(campaign) => {
          if (campaign.status === "draft") setEditing(campaign);
          else setOpenCampaignId(campaign.id);
        }}
      />
    );
  } else if (tab === "prospects") {
    body = (
      <ProspectsView
        token={token}
        tx={tx}
        lists={lists}
        onImported={loadLists}
        onOpenList={(listId) => {
          setContactsListId(listId);
          setTab("contacts");
        }}
        onCreateCampaign={(listId) => openNew(listId)}
      />
    );
  } else if (tab === "contacts") {
    body = (
      <ContactsView
        key={contactsListId || "all"}
        token={token}
        sessionKey={sessionKey}
        tx={tx}
        lists={lists}
        onListsChanged={loadLists}
        initialListId={contactsListId}
      />
    );
  } else if (tab === "templates") {
    body = <TemplatesView token={token} tx={tx} templates={templates} attrKeys={attrKeys} onChanged={loadTemplates} />;
  } else if (tab === "replies") {
    body = (
      <RepliesInbox
        token={token}
        tx={tx}
        campaigns={campaigns}
        teammates={teammates}
        initialClassification={replyFilter.classification}
        initialCampaign={replyFilter.campaign}
        onChanged={() => void loadDashboard()}
      />
    );
  } else {
    body = <CampaignSettingsView token={token} tx={tx} onSaved={() => void loadDashboard()} />;
  }

  return (
    <div className="flex h-full min-h-0 bg-background" data-testid="campaigns-workbench">
      <aside className="hidden w-52 shrink-0 flex-col border-e border-border/60 bg-muted/15 p-3 md:flex">
        <p className="px-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{tx("rail", "Campaigns")}</p>
        <nav className="mt-2 space-y-0.5">
          {TABS.map((item) => {
            const Icon = labels[item].icon;
            return (
              <button
                key={item}
                type="button"
                aria-current={tab === item && !editing ? "page" : undefined}
                onClick={() => setTab(item)}
                className={cn(
                  "flex min-h-10 w-full items-center gap-2 rounded-lg px-2 py-1.5 text-start text-[13px] transition-[background-color,transform] duration-150 active:scale-[0.97]",
                  tab === item ? "bg-sidebar-accent font-medium" : "hover:bg-muted/60",
                )}
              >
                <Icon className="h-3.5 w-3.5 text-[#1d6af0]" aria-hidden />
                <span className="flex-1">{labels[item].label}</span>
                {item === "replies" && newReplies > 0 ? (
                  <span className="rounded-full bg-[#6d5bd0] px-1.5 text-[10.5px] font-semibold text-white">{newReplies}</span>
                ) : null}
              </button>
            );
          })}
        </nav>
        {onSeed ? (
          <button
            type="button"
            onClick={() =>
              onSeed(
                tx(
                  "seedPrompt",
                  "Campaign agent: review the new campaign replies, classify them, draft the answers and prepare a hand-off brief for each interested lead.",
                ),
              )
            }
            className="mt-auto flex items-center gap-2 rounded-xl border border-border/60 px-2.5 py-2 text-start text-[12px] text-foreground/85 hover:bg-muted/50"
          >
            <Sparkles className="h-3.5 w-3.5 text-[#6d5bd0]" aria-hidden />
            {tx("askAgent", "Ask the campaign agent")}
          </button>
        ) : null}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col" style={{ paddingInlineEnd: chatOpen ? undefined : NOTIFICATION_GUTTER }}>
        <div className="flex items-center gap-1 overflow-x-auto border-b border-border/60 px-3 py-2 md:hidden">
          {TABS.map((item) => {
            const Icon = labels[item].icon;
            return (
              <button
                key={item}
                type="button"
                onClick={() => setTab(item)}
                className={cn("flex min-h-9 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-[12px]", tab === item ? "bg-sidebar-accent font-medium" : "hover:bg-muted/60")}
              >
                <Icon className="h-3.5 w-3.5 text-[#1d6af0]" aria-hidden />
                {labels[item].label}
              </button>
            );
          })}
        </div>
        {onToggleChat ? (
          <div className="flex justify-end border-b border-border/60 px-3 py-1.5">
            <button
              type="button"
              onClick={onToggleChat}
              aria-pressed={Boolean(chatOpen)}
              className={cn(
                "inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[12px] font-medium transition-colors",
                chatOpen ? "bg-[#1d6af0]/12 text-[#1d6af0]" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <MessageSquare className="h-3.5 w-3.5" aria-hidden />
              {chatOpen ? tx("hideChat", "Hide chat") : tx("chat", "Chat")}
            </button>
          </div>
        ) : null}
        {error ? (
          <div className="px-4 pt-3">
            <ErrorBanner message={error} onDismiss={() => setError("")} />
          </div>
        ) : null}
        <div className="min-h-0 flex-1 overflow-y-auto">{body}</div>
      </section>
    </div>
  );
}
