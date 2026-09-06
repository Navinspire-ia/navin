/**
 * CRM workbench: dashboard, contacts, companies, leads, opportunities, activities.
 * Same SQLite store the agent `crm` tool reads and writes.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import {
  Building2,
  CalendarDays,
  Contact,
  FolderOpen,
  Kanban,
  LayoutDashboard,
  ListTodo,
  Package,
  Target,
  Trophy,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CrmActivities } from "@/components/crm/CrmActivities";
import { CrmCompanies } from "@/components/crm/CrmCompanies";
import { CrmContacts } from "@/components/crm/CrmContacts";
import { CrmDashboardView } from "@/components/crm/CrmDashboard";
import { CrmConvertDialog } from "@/components/crm/CrmConvertDialog";
import { CrmLeads } from "@/components/crm/CrmLeads";
import { CrmMembers } from "@/components/crm/CrmMembers";
import { CrmOpportunities } from "@/components/crm/CrmOpportunities";
import { CrmProducts } from "@/components/crm/CrmProducts";
import { CrmRecordDialog, type CrmEditorKind } from "@/components/crm/CrmRecordDialog";
import { CrmSettingsDialog } from "@/components/crm/CrmSettingsDialog";
import { DevProjectSelector } from "@/components/dev/DevProjectSelector";
import { Button } from "@/components/ui/button";
import { useAccount } from "@/hooks/useAccount";
import { useOrgRole } from "@/hooks/useOrgRole";
import {
  acceptCrmInvite,
  createCrmRecord,
  crmOutreach,
  deleteCrmRecord,
  fetchCrmAudit,
  fetchCrmCalendar,
  fetchCrmFollowups,
  fetchCrmInsights,
  fetchCrmLines,
  fetchCrmTimeline,
  inviteCrmMember,
  kickCrmMember,
  setCrmMemberRole,
  updateCrmRecord,
  updateCrmSettings,
  type CrmAudit,
  type CrmChannelStatus,
  type CrmDashboard,
  type CrmInsights,
  type CrmInvite,
  type CrmKind,
  type CrmMember,
  type CrmRecord,
  type CrmSettings,
} from "@/lib/api";
import { queryFromHash, tabFromHash, writeCrmHash, type CrmTab } from "@/lib/crm-format";
import { createCrmQueryClient, useCrmMutations, useCrmWorkspace } from "@/lib/crm-query";
import type { RecentProjectEntry } from "@/lib/types";
import { cn } from "@/lib/utils";
import { isNavinInternalPath } from "@/lib/workspace";
import { useClient } from "@/providers/ClientProvider";
import { useCrmUi } from "@/store/crm-ui";

type Props = {
  sessionKey: string | null;
  projectPath: string | null;
  projectName?: string | null;
  recentProjects?: RecentProjectEntry[];
  onSelectProject?: (path: string, name?: string) => void;
};

export function CrmWorkbench(props: Props) {
  const [queryClient] = useState(() => createCrmQueryClient());
  return (
    <QueryClientProvider client={queryClient}>
      <CrmWorkbenchInner {...props} />
    </QueryClientProvider>
  );
}

function CrmWorkbenchInner({
  sessionKey,
  projectPath,
  projectName,
  recentProjects,
  onSelectProject,
}: Props) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const { token } = useClient();
  const { account } = useAccount();
  const orgRole = useOrgRole();
  const actor = useMemo(() => {
    const email = (account?.email || "").trim();
    if (email) return email;
    return (account?.name || "").trim();
  }, [account?.email, account?.name]);

  const [tab, setTab] = useState<CrmTab>(tabFromHash);
  const tabRef = useRef(tab);
  tabRef.current = tab;
  const [oppView, setOppView] = useState<"table" | "pipeline">(
    () => (queryFromHash().get("view") === "table" ? "table" : "pipeline"),
  );
  const [actView, setActView] = useState<"list" | "calendar">(
    () => (queryFromHash().get("view") === "calendar" ? "calendar" : "list"),
  );
  const [dash, setDash] = useState<CrmDashboard | null>(null);
  const [settings, setSettings] = useState<CrmSettings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [setupDismissed, setSetupDismissed] = useState(false);
  const [editor, setEditor] = useState<{ kind: CrmEditorKind; record?: CrmRecord } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{ kind: CrmKind; id: string; title: string } | null>(null);
  const [ready, setReady] = useState(false);
  const [records, setRecords] = useState<Record<CrmKind, CrmRecord[]>>({
    companies: [],
    contacts: [],
    leads: [],
    opportunities: [],
    activities: [],
    products: [],
    opportunity_lines: [],
    files: [],
  });
  const [members, setMembers] = useState<CrmMember[]>([]);
  const [invites, setInvites] = useState<CrmInvite[]>([]);
  const [channels, setChannels] = useState<{
    email: CrmChannelStatus;
    whatsapp: CrmChannelStatus;
    teams: CrmChannelStatus;
  } | null>(null);
  const [followups, setFollowups] = useState<Array<{ opportunity?: CrmRecord; reason?: string }>>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<CrmRecord[]>([]);
  const [audit, setAudit] = useState<CrmAudit[]>([]);
  const [insight, setInsight] = useState<CrmInsights | null>(null);
  const [membersOpen, setMembersOpen] = useState(false);
  const [month, setMonth] = useState(() => new Date());
  const [calendarRows, setCalendarRows] = useState<CrmRecord[]>([]);
  const convertLead = useCrmUi((state) => state.convertLead);
  const openConvert = useCrmUi((state) => state.openConvert);
  const closeConvert = useCrmUi((state) => state.closeConvert);
  const resetFilters = useCrmUi((state) => state.resetFilters);

  const treeKey = sessionKey || "";
  const workspaceQuery = useCrmWorkspace(token, treeKey, actor, month);
  const mutations = useCrmMutations(token, treeKey, actor);
  const hasProject = Boolean(projectPath && !isNavinInternalPath(projectPath));
  const myRole = members.find((row) => {
    const keys = [row.identity, row.email, row.handle].map((item) => item.toLowerCase());
    return keys.includes(actor.toLowerCase());
  })?.role || null;
  const canWrite = myRole !== "viewer" && orgRole !== "viewer" && settings?.canWrite !== false;
  const currency = settings?.currency || dash?.currency || "EUR";
  const locale = settings?.locale || dash?.locale || "fr-FR";
  const configured = Boolean(settings?.configured ?? dash?.settingsConfigured);

  const requestDelete = (kind: CrmEditorKind, row: CrmRecord) => {
    const titles: Record<CrmEditorKind, string> = {
      contacts: tx("crm.confirmDeleteContact", "Supprimer ce contact ?"),
      companies: tx("crm.confirmDeleteCompany", "Supprimer cette entreprise ?"),
      leads: tx("crm.confirmDeleteLead", "Supprimer ce lead ?"),
      opportunities: tx("crm.confirmDeleteDeal", "Supprimer cette opportunite ?"),
      activities: tx("crm.confirmDeleteActivity", "Supprimer cette activite ?"),
      products: tx("crm.confirmDeleteProduct", "Supprimer ce produit ?"),
    };
    setPendingDelete({ kind, id: row.id, title: titles[kind] });
  };

  const applyWorkspace = useCallback((data: NonNullable<typeof workspaceQuery.data>) => {
    setDash(data.dash);
    if (data.settings) setSettings(data.settings);
    setFollowups((data.dash.followups || []) as Array<{ opportunity?: CrmRecord; reason?: string }>);
    setRecords((prev) => ({
      ...prev,
      contacts: data.contacts,
      companies: data.companies,
      leads: data.leads,
      opportunities: data.opportunities,
      activities: data.activities,
      products: data.products,
      files: data.files,
    }));
    setMembers(data.members);
    setInvites(data.invites);
    setChannels(data.channels);
    setCalendarRows(data.calendar);
    setError(null);
    setReady(true);
  }, []);

  useEffect(() => {
    if (workspaceQuery.data) applyWorkspace(workspaceQuery.data);
    else if (workspaceQuery.isFetched || workspaceQuery.isError) setReady(true);
    if (workspaceQuery.error) {
      setError(workspaceQuery.error instanceof Error ? workspaceQuery.error.message : String(workspaceQuery.error));
    }
  }, [applyWorkspace, workspaceQuery.data, workspaceQuery.error, workspaceQuery.isError, workspaceQuery.isFetched]);

  const reload = useCallback(async () => {
    const result = await workspaceQuery.refetch();
    if (result.data) applyWorkspace(result.data);
    await mutations.invalidate();
  }, [applyWorkspace, mutations, workspaceQuery]);

  useEffect(() => {
    if (!ready || !hasProject || configured || setupDismissed || settings === null) return;
    setSettingsOpen(true);
  }, [configured, hasProject, ready, setupDismissed, settings === null]);

  useEffect(() => {
    resetFilters();
  }, [resetFilters, tab]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (tab === "opportunities" && oppView === "table") params.set("view", "table");
    if (tab === "opportunities" && oppView === "pipeline") params.set("view", "pipeline");
    if (tab === "activities" && actView === "calendar") params.set("view", "calendar");
    writeCrmHash(tab, params);
  }, [actView, oppView, tab]);

  useEffect(() => {
    const onHash = () => {
      const next = tabFromHash();
      if (tabRef.current !== next) resetFilters();
      setTab(next);
      const query = queryFromHash();
      setOppView(query.get("view") === "table" ? "table" : "pipeline");
      setActView(query.get("view") === "calendar" ? "calendar" : "list");
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!token || !treeKey || tab !== "activities") return;
    const start = Math.floor(new Date(month.getFullYear(), month.getMonth(), 1).getTime() / 1000);
    const end = Math.floor(new Date(month.getFullYear(), month.getMonth() + 1, 1).getTime() / 1000);
    void fetchCrmCalendar(token, treeKey, start, end)
      .then((payload) => setCalendarRows(payload.records))
      .catch(() => setCalendarRows([]));
  }, [month, tab, token, treeKey]);

  const closeRecord = useCallback(() => {
    setOpenId(null);
    setTimeline([]);
    setAudit([]);
    setInsight(null);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (editor || convertLead || settingsOpen || membersOpen || pendingDelete) return;
      if (!openId) return;
      event.preventDefault();
      closeRecord();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeRecord, convertLead, editor, membersOpen, openId, pendingDelete, settingsOpen]);

  const openRecord = useCallback(
    async (kind: CrmKind, id: string) => {
      if (!token || !treeKey) return;
      setOpenId(id);
      try {
        const [tl, ins, lines, hist] = await Promise.all([
          fetchCrmTimeline(token, treeKey, kind, id),
          fetchCrmInsights(token, treeKey, kind, id),
          kind === "opportunities" ? fetchCrmLines(token, treeKey, id) : Promise.resolve({ records: [] }),
          fetchCrmAudit(token, treeKey, kind, id).catch(() => ({ records: [] })),
        ]);
        setTimeline(tl.records);
        setAudit(hist.records);
        setInsight(ins);
        if (kind === "opportunities") {
          setRecords((prev) => ({ ...prev, opportunity_lines: lines.records }));
        }
      } catch {
        setTimeline([]);
        setAudit([]);
        setInsight(null);
      }
    },
    [token, treeKey],
  );

  const create = useCallback(
    async (kind: CrmKind, body: Record<string, unknown>) => {
      if (!token || !treeKey || busy) return;
      setBusy(true);
      try {
        const created = await createCrmRecord(token, treeKey, kind, { ...body, actor });
        setEditor(null);
        await reload();
        if (kind === "opportunity_lines") {
          const oppId = String(body.opportunityId || created.record.opportunityId || openId || "");
          if (oppId) void openRecord("opportunities", oppId);
        } else {
          void openRecord(kind, created.record.id);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [actor, busy, openId, openRecord, reload, token, treeKey],
  );

  const saveRecord = useCallback(
    async (kind: CrmEditorKind, body: Record<string, unknown>, existing?: CrmRecord) => {
      if (!token || !treeKey || busy) return;
      if (existing?.id) {
        setBusy(true);
        try {
          await updateCrmRecord(token, treeKey, kind, existing.id, { ...body, actor });
          setEditor(null);
          await reload();
          void openRecord(kind, existing.id);
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
        } finally {
          setBusy(false);
        }
        return;
      }
      await create(kind, body);
    },
    [actor, busy, create, openRecord, reload, token, treeKey],
  );

  const saveSettings = useCallback(
    async (body: Record<string, string>) => {
      if (!token || !treeKey || busy) return;
      setBusy(true);
      try {
        const result = await updateCrmSettings(token, treeKey, { ...body, actor });
        setSettings(result.settings);
        setSetupDismissed(true);
        setSettingsOpen(false);
        await reload();
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [actor, busy, reload, token, treeKey],
  );

  const confirmDelete = useCallback(async () => {
    if (!token || !treeKey || !pendingDelete) return;
    setBusy(true);
    try {
      await deleteCrmRecord(token, treeKey, pendingDelete.kind, pendingDelete.id, actor);
      if (openId === pendingDelete.id) {
        setOpenId(null);
        setTimeline([]);
        setAudit([]);
        setInsight(null);
      }
      const deletedKind = pendingDelete.kind;
      const keepOpen = openId && deletedKind === "opportunity_lines" ? openId : null;
      setPendingDelete(null);
      await reload();
      if (keepOpen) void openRecord("opportunities", keepOpen);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [actor, openId, openRecord, pendingDelete, reload, token, treeKey]);

  const tabs: { id: CrmTab; label: string; icon: typeof LayoutDashboard }[] = [
    { id: "dashboard", label: tx("crm.tabs.dashboard", "Dashboard"), icon: LayoutDashboard },
    { id: "contacts", label: tx("crm.tabs.contacts", "Contacts"), icon: Contact },
    { id: "companies", label: tx("crm.tabs.companies", "Entreprises"), icon: Building2 },
    { id: "leads", label: tx("crm.tabs.leads", "Leads"), icon: Target },
    { id: "opportunities", label: tx("crm.tabs.opportunities", "Opportunites"), icon: Trophy },
    { id: "activities", label: tx("crm.tabs.activities", "Activites"), icon: ListTodo },
    { id: "products", label: tx("crm.tabs.products", "Produits"), icon: Package },
  ];

  return (
    <div className="flex h-full min-h-0 bg-background">
      <aside className="hidden w-48 shrink-0 flex-col border-r border-border/60 bg-muted/15 p-3 md:flex">
        <p className="px-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {tx("crm.rail", "CRM")}
        </p>
        <nav className="mt-2 space-y-0.5">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                resetFilters();
                setTab(item.id);
                setOpenId(null);
              }}
              className={cn(
                "flex min-h-10 w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] transition-[background-color,transform] duration-150 active:scale-[0.96]",
                tab === item.id ? "bg-sidebar-accent font-medium" : "hover:bg-muted/60",
              )}
            >
              <item.icon className="h-3.5 w-3.5 text-violet-500" aria-hidden />
              {item.label}
            </button>
          ))}
        </nav>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <nav className="flex gap-1 overflow-x-auto border-b border-border/60 px-3 py-2 md:hidden">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                resetFilters();
                setTab(item.id);
                setOpenId(null);
              }}
              className={cn(
                "flex min-h-10 shrink-0 cursor-pointer items-center gap-1.5 rounded-lg px-2.5 text-[12px]",
                tab === item.id ? "bg-sidebar-accent font-medium" : "hover:bg-muted/60",
              )}
            >
              <item.icon className="h-3.5 w-3.5 text-violet-500" aria-hidden />
              {item.label}
            </button>
          ))}
        </nav>
        <header className="flex flex-wrap items-center gap-2 border-b border-border/60 px-4 py-2.5">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 cursor-pointer text-[12px]"
            onClick={() => setSettingsOpen(true)}
          >
            <Building2 className="mr-1.5 h-3.5 w-3.5" />
            {tx("crm.configureCompany", "Configurer l'entreprise")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 cursor-pointer text-[12px]"
            onClick={() => setMembersOpen(true)}
          >
            <Users className="mr-1.5 h-3.5 w-3.5" />
            {tx("crm.members", "Membres")}
          </Button>
          <h1 className="text-sm font-semibold" style={{ textWrap: "balance" } as never}>
            {tabs.find((item) => item.id === tab)?.label}
          </h1>
          {tab === "opportunities" ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 cursor-pointer text-[11px]"
              onClick={() => setOppView((prev) => (prev === "pipeline" ? "table" : "pipeline"))}
            >
              <Kanban className="mr-1 h-3.5 w-3.5" />
              {oppView === "pipeline" ? tx("crm.viewTable", "Liste") : tx("crm.viewPipeline", "Pipeline")}
            </Button>
          ) : null}
          {tab === "activities" ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 cursor-pointer text-[11px]"
              onClick={() => setActView((prev) => (prev === "calendar" ? "list" : "calendar"))}
            >
              <CalendarDays className="mr-1 h-3.5 w-3.5" />
              {actView === "calendar" ? tx("crm.viewList", "Liste") : tx("crm.viewCalendar", "Calendrier")}
            </Button>
          ) : null}
          {onSelectProject ? (
            <DevProjectSelector
              projectPath={projectPath}
              projectName={projectName ?? null}
              recentProjects={recentProjects ?? []}
              onSelectProject={onSelectProject}
              compact
            />
          ) : null}
        </header>

        {error ? (
          <p className="border-b border-border/50 px-4 py-2 text-[12px] text-red-500">{error}</p>
        ) : null}
        {hasProject && !configured ? (
          <p className="border-b border-border/50 px-4 py-1.5 text-[12px] text-muted-foreground">
            {tx("crm.currencyNotConfigured", "Devise non configuree - montants en EUR par defaut")}
          </p>
        ) : null}

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-4">
          {!hasProject ? (
            <div className="flex h-full min-h-0 flex-1 flex-col items-center justify-center gap-4 px-4 text-center">
              <FolderOpen className="h-8 w-8 text-violet-500" aria-hidden />
              <div className="max-w-md space-y-1.5">
                <p className="text-sm font-semibold">
                  {tx("crm.needProjectTitle", "Choisis un projet pour le CRM")}
                </p>
                <p className="text-[13px] text-muted-foreground">
                  {tx(
                    "crm.needProject",
                    "Le CRM est rattache a un dossier. Ouvre un projet pour voir le dashboard, les listes et les parametres.",
                  )}
                </p>
              </div>
              {onSelectProject ? (
                <DevProjectSelector
                  projectPath={projectPath}
                  projectName={projectName ?? null}
                  recentProjects={recentProjects ?? []}
                  onSelectProject={onSelectProject}
                  variant="empty"
                />
              ) : null}
            </div>
          ) : null}

          {hasProject && tab === "dashboard" ? (
            <CrmDashboardView
              dash={dash}
              followups={followups}
              currency={currency}
              locale={locale}
              canWrite={canWrite}
              tx={tx}
              onAdd={(kind) => setEditor({ kind })}
              onOpenDeal={(id) => {
                setTab("opportunities");
                void openRecord("opportunities", id);
              }}
              onCreateFollowups={() => {
                if (!token || !treeKey) return;
                void fetchCrmFollowups(token, treeKey, true, actor)
                  .then((payload) => {
                    setFollowups(payload.items);
                    void reload();
                  })
                  .catch((err) => setError(err instanceof Error ? err.message : String(err)));
              }}
            />
          ) : null}

          {hasProject && tab === "contacts" ? (
            <CrmContacts
              contacts={records.contacts}
              companies={records.companies}
              opportunities={records.opportunities}
              leads={records.leads}
              members={members}
              locale={locale}
              busy={busy}
              openId={openId}
              timeline={timeline}
              audit={audit}
              insight={insight}
              channels={channels}
              canWrite={canWrite}
              tx={tx}
              token={token}
              sessionKey={treeKey}
              actor={actor}
              onAdd={() => setEditor({ kind: "contacts" })}
              onEdit={(row) => setEditor({ kind: "contacts", record: row })}
              onDelete={(row) => requestDelete("contacts", row)}
              onOpen={(id) => void openRecord("contacts", id)}
              onClose={closeRecord}
              onOutreach={async (body) => {
                await crmOutreach(token, treeKey, { ...body, actor });
                if (openId) await openRecord("contacts", openId);
                await reload();
              }}
            />
          ) : null}

          {hasProject && tab === "companies" ? (
            <CrmCompanies
              companies={records.companies}
              contacts={records.contacts}
              opportunities={records.opportunities}
              activities={records.activities}
              files={records.files}
              members={members}
              busy={busy}
              openId={openId}
              timeline={timeline}
              audit={audit}
              insight={insight}
              canWrite={canWrite}
              currency={currency}
              locale={locale}
              tx={tx}
              onAdd={() => setEditor({ kind: "companies" })}
              onEdit={(row) => setEditor({ kind: "companies", record: row })}
              onDelete={(row) => requestDelete("companies", row)}
              onOpen={(id) => void openRecord("companies", id)}
              onClose={closeRecord}
              onUpdate={(id, body) => {
                if (!token) return;
                void updateCrmRecord(token, treeKey, "companies", id, { ...body, actor }).then(() => reload());
              }}
              onOpenContact={(id) => {
                setTab("contacts");
                void openRecord("contacts", id);
              }}
              onOpenDeal={(id) => {
                setTab("opportunities");
                void openRecord("opportunities", id);
              }}
            />
          ) : null}

          {hasProject && tab === "leads" ? (
            <CrmLeads
              leads={records.leads}
              members={members}
              busy={busy}
              openId={openId}
              timeline={timeline}
              audit={audit}
              insight={insight}
              canWrite={canWrite}
              locale={locale}
              tx={tx}
              onAdd={() => setEditor({ kind: "leads" })}
              onEdit={(row) => setEditor({ kind: "leads", record: row })}
              onDelete={(row) => requestDelete("leads", row)}
              onOpen={(id) => void openRecord("leads", id)}
              onClose={closeRecord}
              onConvert={(row) => openConvert(row)}
              onOpenOpportunity={(id) => {
                setTab("opportunities");
                void openRecord("opportunities", id);
              }}
            />
          ) : null}

          {hasProject && tab === "opportunities" ? (
            <CrmOpportunities
              rows={records.opportunities}
              products={records.products}
              lines={records.opportunity_lines}
              contacts={records.contacts}
              companies={records.companies}
              members={members}
              view={oppView}
              busy={busy}
              dash={dash}
              openId={openId}
              insight={insight}
              timeline={timeline}
              audit={audit}
              channels={channels}
              canWrite={canWrite}
              currency={currency}
              locale={locale}
              tx={tx}
              token={token}
              sessionKey={treeKey}
              actor={actor}
              onAdd={() => setEditor({ kind: "opportunities" })}
              onEdit={(row) => setEditor({ kind: "opportunities", record: row })}
              onDelete={(row) => requestDelete("opportunities", row)}
              onMove={async (id, stage) => {
                if (!token || !treeKey || !canWrite) return;
                await updateCrmRecord(token, treeKey, "opportunities", id, { stage, actor });
                await reload();
              }}
              onOpen={(id) => void openRecord("opportunities", id)}
              onClose={closeRecord}
              onAddProduct={() => setEditor({ kind: "products" })}
              onAddLine={(opportunityId, body) => void create("opportunity_lines", { ...body, opportunityId })}
              onDeleteLine={(row) =>
                setPendingDelete({
                  kind: "opportunity_lines",
                  id: row.id,
                  title: tx("crm.confirmDeleteLine", "Supprimer cette ligne ?"),
                })
              }
              onOutreach={async (body) => {
                await crmOutreach(token, treeKey, { ...body, actor });
                if (openId) await openRecord("opportunities", openId);
                await reload();
              }}
            />
          ) : null}

          {hasProject && tab === "activities" ? (
            <CrmActivities
              rows={records.activities}
              calendarRows={calendarRows}
              contacts={records.contacts}
              companies={records.companies}
              opportunities={records.opportunities}
              leads={records.leads}
              view={actView}
              busy={busy}
              month={month}
              openId={openId}
              canWrite={canWrite}
              locale={locale}
              tx={tx}
              onAdd={() => setEditor({ kind: "activities" })}
              onEdit={(row) => setEditor({ kind: "activities", record: row })}
              onDelete={(row) => requestDelete("activities", row)}
              onOpen={(id) => void openRecord("activities", id)}
              onClose={closeRecord}
              onPrevMonth={() => setMonth((prev) => new Date(prev.getFullYear(), prev.getMonth() - 1, 1))}
              onNextMonth={() => setMonth((prev) => new Date(prev.getFullYear(), prev.getMonth() + 1, 1))}
              onPickDay={(unix) => {
                if (!canWrite) return;
                setEditor({
                  kind: "activities",
                  record: { id: "", kind: "reunion", title: "", at: unix + 10 * 3600 },
                });
              }}
            />
          ) : null}

          {hasProject && tab === "products" ? (
            <CrmProducts
              products={records.products}
              openId={openId}
              canWrite={canWrite}
              currency={currency}
              locale={locale}
              tx={tx}
              onAdd={() => setEditor({ kind: "products" })}
              onEdit={(row) => setEditor({ kind: "products", record: row })}
              onDelete={(row) => requestDelete("products", row)}
              onOpen={(id) => void openRecord("products", id)}
              onClose={closeRecord}
            />
          ) : null}
        </div>
      </section>
      <CrmMembers
        open={membersOpen}
        members={members}
        invites={invites}
        actor={actor}
        myRole={myRole}
        busy={busy}
        tx={tx}
        onClose={() => setMembersOpen(false)}
        onInvite={async (identity, role) => {
          await inviteCrmMember(token, treeKey, { identity, email: identity, role, actor });
          await reload();
        }}
        onAccept={async (inviteId, accept) => {
          await acceptCrmInvite(token, treeKey, { id: inviteId, accept, actor });
          await reload();
        }}
        onRole={async (id, role) => {
          await setCrmMemberRole(token, treeKey, { id, role, actor });
          await reload();
        }}
        onKick={async (id) => {
          await kickCrmMember(token, treeKey, { id, actor });
          await reload();
        }}
      />
      <CrmSettingsDialog
        open={settingsOpen}
        busy={busy}
        error={error}
        settings={settings}
        tx={tx}
        onClose={() => {
          setSetupDismissed(true);
          setSettingsOpen(false);
        }}
        onSave={(body) => void saveSettings(body)}
      />
      {editor ? (
        <CrmRecordDialog
          open
          kind={editor.kind}
          record={editor.record}
          busy={busy}
          error={error}
          currency={currency}
          locale={locale}
          defaultCountry={settings?.country || "FR"}
          companies={records.companies}
          contacts={records.contacts}
          opportunities={records.opportunities}
          leads={records.leads}
          members={members}
          actor={actor}
          tx={tx}
          onClose={() => setEditor(null)}
          onSubmit={(body) => void saveRecord(editor.kind, body, editor.record)}
        />
      ) : null}
      <CrmConvertDialog
        open={Boolean(convertLead)}
        lead={convertLead}
        companies={records.companies}
        contacts={records.contacts}
        members={members}
        currency={currency}
        locale={locale}
        actor={actor}
        busy={busy || mutations.convert.isPending}
        error={error}
        tx={tx}
        onClose={closeConvert}
        onSubmit={(body) => {
          if (!convertLead) return;
          setBusy(true);
          mutations.convert
            .mutateAsync({ id: convertLead.id, body })
            .then(async (result) => {
              closeConvert();
              await reload();
              setTab("opportunities");
              void openRecord("opportunities", result.opportunity.id);
              setError(null);
            })
            .catch((err) => setError(err instanceof Error ? err.message : String(err)))
            .finally(() => setBusy(false));
        }}
      />
      <ConfirmDialog
        open={pendingDelete !== null}
        title={pendingDelete?.title || tx("crm.delete", "Supprimer")}
        description={tx("crm.confirmDeleteHelp", "Cette action est definitive.")}
        confirmLabel={tx("crm.delete", "Supprimer")}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}
