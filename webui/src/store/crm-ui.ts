import { create } from "zustand";

import type { CrmRecord } from "@/lib/api";
import { tabFromHash, type CrmEditorKind, type CrmTab } from "@/lib/crm-format";

type EditorState = { kind: CrmEditorKind; record?: CrmRecord } | null;

export type CrmFacetFilters = {
  status: string;
  owner: string;
  country: string;
  source: string;
  tag: string;
  kind: string;
  currency: string;
  dateFrom: string;
  dateTo: string;
};

export const EMPTY_CRM_FACETS: CrmFacetFilters = {
  status: "",
  owner: "",
  country: "",
  source: "",
  tag: "",
  kind: "",
  currency: "",
  dateFrom: "",
  dateTo: "",
};

type CrmUiState = {
  tab: CrmTab;
  oppView: "table" | "pipeline";
  actView: "list" | "calendar";
  openId: string | null;
  editor: EditorState;
  convertLead: CrmRecord | null;
  settingsOpen: boolean;
  filter: string;
  facets: CrmFacetFilters;
  setTab: (tab: CrmTab) => void;
  setOppView: (view: "table" | "pipeline") => void;
  setActView: (view: "list" | "calendar") => void;
  setOpenId: (id: string | null) => void;
  openEditor: (kind: CrmEditorKind, record?: CrmRecord) => void;
  closeEditor: () => void;
  openConvert: (lead: CrmRecord) => void;
  closeConvert: () => void;
  setSettingsOpen: (open: boolean) => void;
  setFilter: (value: string) => void;
  setFacet: <K extends keyof CrmFacetFilters>(key: K, value: CrmFacetFilters[K]) => void;
  resetFilters: () => void;
};

export const useCrmUi = create<CrmUiState>((set) => ({
  tab: typeof window === "undefined" ? "dashboard" : tabFromHash(),
  oppView: "pipeline",
  actView: "list",
  openId: null,
  editor: null,
  convertLead: null,
  settingsOpen: false,
  filter: "",
  facets: { ...EMPTY_CRM_FACETS },
  setTab: (tab) => set({ tab, openId: null, filter: "", facets: { ...EMPTY_CRM_FACETS } }),
  setOppView: (oppView) => set({ oppView }),
  setActView: (actView) => set({ actView }),
  setOpenId: (openId) => set({ openId }),
  openEditor: (kind, record) => set({ editor: { kind, record } }),
  closeEditor: () => set({ editor: null }),
  openConvert: (lead) => set({ convertLead: lead }),
  closeConvert: () => set({ convertLead: null }),
  setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
  setFilter: (filter) => set({ filter }),
  setFacet: (key, value) => set((state) => ({ facets: { ...state.facets, [key]: value } })),
  resetFilters: () => set({ filter: "", facets: { ...EMPTY_CRM_FACETS } }),
}));
