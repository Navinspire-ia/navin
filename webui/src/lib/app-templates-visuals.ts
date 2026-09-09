// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  Bot,
  Briefcase,
  CalendarDays,
  Factory,
  FileText,
  GraduationCap,
  Headphones,
  Home,
  Kanban,
  LayoutTemplate,
  Library,
  Megaphone,
  MessageSquare,
  Plane,
  Rocket,
  Scale,
  ScanSearch,
  Share2,
  ShoppingBag,
  Stethoscope,
  Ticket,
  Truck,
  Users,
  UtensilsCrossed,
  Wallet,
  Workflow,
} from "lucide-react";

export type TemplateVisual = {
  icon: LucideIcon;
  tile: string;
  ink: string;
  chip: string;
  label: string;
};

/** Solid tiles (same idea as MCP brand squares) so categories read at a glance. */
export const TEMPLATE_VISUALS: Record<string, TemplateVisual> = {
  chat: {
    icon: MessageSquare,
    tile: "bg-violet-600",
    ink: "text-white",
    chip: "bg-violet-600/18 text-violet-200",
    label: "Chat",
  },
  rag: {
    icon: Library,
    tile: "bg-indigo-600",
    ink: "text-white",
    chip: "bg-indigo-600/18 text-indigo-200",
    label: "RAG",
  },
  agents: {
    icon: Bot,
    tile: "bg-fuchsia-600",
    ink: "text-white",
    chip: "bg-fuchsia-600/18 text-fuchsia-200",
    label: "Agents",
  },
  search: {
    icon: ScanSearch,
    tile: "bg-sky-600",
    ink: "text-white",
    chip: "bg-sky-600/18 text-sky-200",
    label: "Search",
  },
  analytics: {
    icon: BarChart3,
    tile: "bg-cyan-600",
    ink: "text-white",
    chip: "bg-cyan-600/18 text-cyan-200",
    label: "Analytics",
  },
  automation: {
    icon: Workflow,
    tile: "bg-orange-600",
    ink: "text-white",
    chip: "bg-orange-600/18 text-orange-200",
    label: "Automation",
  },
  social: {
    icon: Share2,
    tile: "bg-pink-600",
    ink: "text-white",
    chip: "bg-pink-600/18 text-pink-200",
    label: "Social",
  },
  crm: {
    icon: Briefcase,
    tile: "bg-blue-600",
    ink: "text-white",
    chip: "bg-blue-600/18 text-blue-200",
    label: "CRM",
  },
  finance: {
    icon: Wallet,
    tile: "bg-emerald-600",
    ink: "text-white",
    chip: "bg-emerald-600/18 text-emerald-200",
    label: "Finance",
  },
  hr: {
    icon: Users,
    tile: "bg-teal-600",
    ink: "text-white",
    chip: "bg-teal-600/18 text-teal-200",
    label: "HR",
  },
  commerce: {
    icon: ShoppingBag,
    tile: "bg-amber-500",
    ink: "text-white",
    chip: "bg-amber-500/18 text-amber-200",
    label: "Commerce",
  },
  "real-estate": {
    icon: Home,
    tile: "bg-lime-600",
    ink: "text-white",
    chip: "bg-lime-600/18 text-lime-200",
    label: "Real estate",
  },
  operations: {
    icon: Kanban,
    tile: "bg-slate-600",
    ink: "text-white",
    chip: "bg-slate-500/20 text-slate-200",
    label: "Operations",
  },
  support: {
    icon: Headphones,
    tile: "bg-rose-600",
    ink: "text-white",
    chip: "bg-rose-600/18 text-rose-200",
    label: "Support",
  },
  healthcare: {
    icon: Stethoscope,
    tile: "bg-red-600",
    ink: "text-white",
    chip: "bg-red-600/18 text-red-200",
    label: "Healthcare",
  },
  booking: {
    icon: CalendarDays,
    tile: "bg-violet-500",
    ink: "text-white",
    chip: "bg-violet-500/18 text-violet-200",
    label: "Booking",
  },
  education: {
    icon: GraduationCap,
    tile: "bg-indigo-500",
    ink: "text-white",
    chip: "bg-indigo-500/18 text-indigo-200",
    label: "Education",
  },
  hospitality: {
    icon: UtensilsCrossed,
    tile: "bg-orange-500",
    ink: "text-white",
    chip: "bg-orange-500/18 text-orange-200",
    label: "Hospitality",
  },
  logistics: {
    icon: Truck,
    tile: "bg-yellow-600",
    ink: "text-white",
    chip: "bg-yellow-600/18 text-yellow-200",
    label: "Logistics",
  },
  marketing: {
    icon: Megaphone,
    tile: "bg-pink-500",
    ink: "text-white",
    chip: "bg-pink-500/18 text-pink-200",
    label: "Marketing",
  },
  content: {
    icon: FileText,
    tile: "bg-sky-500",
    ink: "text-white",
    chip: "bg-sky-500/18 text-sky-200",
    label: "CMS",
  },
  saas: {
    icon: Rocket,
    tile: "bg-violet-700",
    ink: "text-white",
    chip: "bg-violet-700/18 text-violet-200",
    label: "SaaS",
  },
  events: {
    icon: Ticket,
    tile: "bg-fuchsia-500",
    ink: "text-white",
    chip: "bg-fuchsia-500/18 text-fuchsia-200",
    label: "Events",
  },
  legal: {
    icon: Scale,
    tile: "bg-stone-600",
    ink: "text-white",
    chip: "bg-stone-500/20 text-stone-200",
    label: "Legal",
  },
  travel: {
    icon: Plane,
    tile: "bg-cyan-500",
    ink: "text-white",
    chip: "bg-cyan-500/18 text-cyan-200",
    label: "Travel",
  },
  manufacturing: {
    icon: Factory,
    tile: "bg-zinc-600",
    ink: "text-white",
    chip: "bg-zinc-500/20 text-zinc-200",
    label: "Manufacturing",
  },
};

const FALLBACK: TemplateVisual = {
  icon: LayoutTemplate,
  tile: "bg-primary",
  ink: "text-primary-foreground",
  chip: "bg-primary/15 text-primary",
  label: "App",
};

export function templateVisual(category: string): TemplateVisual {
  return TEMPLATE_VISUALS[category] ?? { ...FALLBACK, label: category || FALLBACK.label };
}

export function agentShortName(id: string): string {
  return id.replace(/-agent$/, "").replace(/-assistant$/, "").replace(/-/g, " ");
}
