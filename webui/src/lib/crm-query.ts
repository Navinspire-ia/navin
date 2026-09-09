// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";

import {
  convertCrmLead,
  createCrmRecord,
  deleteCrmRecord,
  fetchCrmAudit,
  fetchCrmCalendar,
  fetchCrmChannels,
  fetchCrmDashboard,
  fetchCrmInsights,
  fetchCrmLines,
  fetchCrmList,
  fetchCrmMembers,
  fetchCrmProducts,
  fetchCrmSettings,
  fetchCrmTimeline,
  updateCrmRecord,
  updateCrmSettings,
  type CrmKind,
  type CrmRecord,
} from "@/lib/api";

export function createCrmQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 8_000, refetchOnWindowFocus: false },
    },
  });
}

export const crmKeys = {
  all: (treeKey: string) => ["crm", treeKey] as const,
  workspace: (treeKey: string, actor: string, month: string) =>
    ["crm", treeKey, "workspace", actor, month] as const,
  open: (treeKey: string, kind: string, id: string) => ["crm", treeKey, "open", kind, id] as const,
};

export async function fetchCrmWorkspace(token: string, treeKey: string, actor: string, month: Date) {
  const monthKey = format(month, "yyyy-MM");
  const start = Math.floor(new Date(month.getFullYear(), month.getMonth(), 1).getTime() / 1000);
  const end = Math.floor(new Date(month.getFullYear(), month.getMonth() + 1, 1).getTime() / 1000);
  const [dash, settings, contacts, companies, leads, opportunities, activities, products, files, roster, channels, calendar] =
    await Promise.all([
      fetchCrmDashboard(token, treeKey),
      fetchCrmSettings(token, treeKey, actor).catch(() => null),
      fetchCrmList(token, treeKey, "contacts"),
      fetchCrmList(token, treeKey, "companies"),
      fetchCrmList(token, treeKey, "leads"),
      fetchCrmList(token, treeKey, "opportunities"),
      fetchCrmList(token, treeKey, "activities"),
      fetchCrmProducts(token, treeKey).catch(() => ({ records: [] as CrmRecord[] })),
      fetchCrmList(token, treeKey, "files").catch(() => ({ records: [] as CrmRecord[] })),
      fetchCrmMembers(token, treeKey, actor).catch(() => ({
        members: [],
        invites: [],
        source: "local",
      })),
      fetchCrmChannels(token, treeKey).catch(() => null),
      fetchCrmCalendar(token, treeKey, start, end).catch(() => ({ records: [] as CrmRecord[] })),
    ]);
  return {
    monthKey,
    dash,
    settings,
    contacts: contacts.records,
    companies: companies.records,
    leads: leads.records,
    opportunities: opportunities.records,
    activities: activities.records,
    products: products.records,
    files: files.records,
    members: roster.members || [],
    invites: roster.invites || [],
    channels,
    calendar: calendar.records,
  };
}

export function useCrmWorkspace(token: string, treeKey: string, actor: string, month: Date) {
  const monthKey = format(month, "yyyy-MM");
  return useQuery({
    queryKey: crmKeys.workspace(treeKey, actor, monthKey),
    queryFn: () => fetchCrmWorkspace(token, treeKey, actor, month),
    enabled: Boolean(token && treeKey),
  });
}

export function useCrmOpenRecord(token: string, treeKey: string, kind: CrmKind | null, id: string | null) {
  return useQuery({
    queryKey: crmKeys.open(treeKey, kind || "", id || ""),
    enabled: Boolean(token && treeKey && kind && id),
    queryFn: async () => {
      if (!kind || !id) return null;
      const [timeline, insight, lines, audit] = await Promise.all([
        fetchCrmTimeline(token, treeKey, kind, id),
        fetchCrmInsights(token, treeKey, kind, id),
        kind === "opportunities" ? fetchCrmLines(token, treeKey, id) : Promise.resolve({ records: [] as CrmRecord[] }),
        fetchCrmAudit(token, treeKey, kind, id).catch(() => ({ records: [] })),
      ]);
      return {
        timeline: timeline.records,
        insight,
        lines: lines.records,
        audit: audit.records,
      };
    },
  });
}

export function useCrmMutations(token: string, treeKey: string, actor: string) {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: crmKeys.all(treeKey) });

  const create = useMutation({
    mutationFn: (input: { kind: CrmKind; body: Record<string, unknown> }) =>
      createCrmRecord(token, treeKey, input.kind, { ...input.body, actor }),
    onSuccess: invalidate,
  });

  const update = useMutation({
    mutationFn: (input: { kind: CrmKind; id: string; body: Record<string, unknown> }) =>
      updateCrmRecord(token, treeKey, input.kind, input.id, { ...input.body, actor }),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (input: { kind: CrmKind; id: string }) =>
      deleteCrmRecord(token, treeKey, input.kind, input.id, actor),
    onSuccess: invalidate,
  });

  const convert = useMutation({
    mutationFn: (input: { id: string; body: Record<string, unknown> }) =>
      convertCrmLead(token, treeKey, input.id, { ...input.body, owner: actor, actor }),
    onSuccess: invalidate,
  });

  const saveSettings = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      updateCrmSettings(token, treeKey, { ...body, actor }),
    onSuccess: invalidate,
  });

  return { create, update, remove, convert, saveSettings, invalidate };
}

export type CrmWorkspaceData = Awaited<ReturnType<typeof fetchCrmWorkspace>>;
