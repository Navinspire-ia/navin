// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Settings > Team: the Collaboration Mode team. Each teammate is a real
 * colleague (name, job, email, bot avatar) with its own model, effort,
 * sandbox, prompt, tools and skills. The team ships pre-filled; everything
 * here is editable and saved as a whole.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Check, ChevronDown, Copy, Loader2, Mail, Plus, RotateCcw, Search, Trash2, X } from "lucide-react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { TeamBotAvatar } from "@/components/team/TeamBotAvatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { fetchCollabSettings, saveCollabSettings, sendCollabTestEmail, setCollabEnabled } from "@/lib/api";
import type {
  CollabCatalog,
  CollabPayload,
  CollabSettings,
  TeamGroup,
  TeamMember,
  TeamPattern,
  TeamRole,
  TeamSandbox,
} from "@/lib/team";
import { cn } from "@/lib/utils";

const MAX_PROMPT = 8000;
const SWATCHES = [
  "#6d5bd0", "#2f7de1", "#1f9d74", "#d1495b", "#d4892a",
  "#3a9fb5", "#8a6fdf", "#c2577f", "#5f8f3e", "#64748b",
];

const PATTERNS: Array<{ key: TeamPattern; label: string; help: string }> = [
  { key: "dev_tester", label: "Developer and Tester", help: "One builds, one tests and sends failures with evidence back until green." },
  { key: "dual_planning", label: "Dual Planning", help: "Two independent plans, cross-examined, merged into one argued plan." },
  { key: "adversarial_review", label: "Adversarial Review", help: "One builds, another attacks the result with proof: bugs, security, edge cases." },
  { key: "parallel_solutions", label: "Parallel Solutions", help: "Competing solutions in isolated git checkouts; the best parts are kept." },
  { key: "pipeline", label: "Architect, Developer, Reviewer", help: "Plan, implement, validate; any stage can send the work back." },
  { key: "debate", label: "Consensus and Debate", help: "Teammates argue and converge; past the round limit, you decide." },
];

const ROLES: Array<{ key: TeamRole; label: string }> = [
  { key: "architect", label: "Architect" },
  { key: "developer", label: "Developer" },
  { key: "tester", label: "Tester / QA" },
  { key: "security", label: "Security auditor" },
  { key: "reviewer", label: "Reviewer" },
  { key: "reverse", label: "Reverse engineer" },
  { key: "seo", label: "SEO specialist" },
  { key: "marketing", label: "Marketing" },
  { key: "sales", label: "Sales" },
  { key: "ads", label: "Ads manager" },
  { key: "campaigns", label: "Campaign manager" },
  { key: "designer", label: "UX/UI designer" },
  { key: "devops", label: "DevOps engineer" },
  { key: "infra", label: "Cloud infrastructure" },
  { key: "custom", label: "Custom" },
];

const SANDBOXES: Array<{ key: TeamSandbox; label: string; help: string }> = [
  { key: "workspace", label: "Shared project", help: "Works in the project tree with the rest of the team." },
  { key: "worktree", label: "Own git checkout", help: "Isolated copy from the last commit; its work is merged deliberately." },
  { key: "readonly", label: "Read only", help: "Reads and reports; cannot edit files." },
];

const selectClass = cn(
  "h-9 w-full rounded-full border border-input bg-background px-3 text-[13px]",
  "text-foreground shadow-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
);

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function sameTeam(a: CollabSettings | null, b: CollabSettings | null): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function slug(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
}

function uniqueName(base: string, members: TeamMember[]): string {
  const taken = new Set(members.map((m) => m.name.trim().toLowerCase()));
  if (!taken.has(base.toLowerCase())) return base;
  for (let n = 2; ; n += 1) {
    const candidate = `${base} ${n}`;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
}

function Switch({ checked, onChange, label, testId }: { checked: boolean; onChange: (next: boolean) => void; label: string; testId?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
        checked ? "bg-[#1d6af0]" : "bg-foreground/20",
      )}
      data-testid={testId}
    >
      <span
        className={cn(
          "inline-block h-4 w-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

function Group({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.075)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.24)]", className)}>
      <div className="divide-y divide-border/45">{children}</div>
    </div>
  );
}

function Row({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return (
    <div className="flex min-h-[62px] flex-col gap-3 px-4 py-3.5 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-x-9 sm:px-5">
      <div className="min-w-0 sm:flex-[1_1_12rem]">
        <div className="text-[14px] font-medium leading-5 text-foreground">{title}</div>
        {description ? <div className="mt-0.5 max-w-[40rem] text-[12px] leading-5 text-muted-foreground">{description}</div> : null}
      </div>
      {children ? <div className="min-w-0 max-w-full sm:shrink-0">{children}</div> : null}
    </div>
  );
}

function Field({ label, hint, children, className }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={cn("block min-w-0", className)}>
      <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-[11.5px] leading-4 text-muted-foreground/85">{hint}</span> : null}
    </label>
  );
}

/** A settings block folded by default; the header opens it. */
function Foldable({
  title, summary, children, testId, defaultOpen = true,
}: { title: string; summary?: string; children: ReactNode; testId?: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section data-testid={testId}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="mb-2 flex w-full items-center gap-2 rounded-xl px-1 py-1 text-start transition-colors hover:bg-muted/40"
      >
        <ChevronDown className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", !open && "-rotate-90 rtl:rotate-90")} aria-hidden />
        <span className="text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">{title}</span>
        {summary && !open ? <span className="min-w-0 truncate text-[12px] text-muted-foreground">{summary}</span> : null}
      </button>
      {open ? children : null}
    </section>
  );
}

// Calm tints that mark each team at a glance (by position, stable while editing).
const TEAM_TINTS = ["#2f7de1", "#1f9d74", "#d4892a", "#6d5bd0", "#d1495b", "#0e7490", "#be185d", "#4c9a2a"];

function teamTint(index: number): string {
  return TEAM_TINTS[index % TEAM_TINTS.length];
}

/** Model chooser that always opens downwards, scrolls, and can be searched. */
function ModelPicker({
  value,
  presets,
  label,
  mainLabel,
  searchLabel,
  onChange,
}: {
  value: string;
  presets: Array<{ name: string; label: string; model: string }>;
  label: string;
  mainLabel: string;
  searchLabel: string;
  onChange: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const name = (preset: { name: string; label: string }) => (preset.name === "default" ? mainLabel : preset.label);
  const current = presets.find((preset) => preset.name === value);
  const needle = query.trim().toLowerCase();
  const visible = needle
    ? presets.filter((preset) => `${name(preset)} ${preset.model}`.toLowerCase().includes(needle))
    : presets;
  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        onClick={() => setOpen((v) => !v)}
        className={cn(selectClass, "flex w-full items-center justify-between gap-2 text-start")}
      >
        <span className="min-w-0 truncate">
          {current ? `${name(current)} · ${current.model}` : value}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      </button>
      {open ? (
        <div className="absolute inset-x-0 top-full z-30 mt-1 overflow-hidden rounded-xl border border-border/70 bg-popover shadow-lg">
          <div className="border-b border-border/50 p-1.5">
            <Input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={searchLabel}
              aria-label={searchLabel}
              className="h-8 rounded-lg text-[12.5px]"
            />
          </div>
          <ul role="listbox" aria-label={label} className="max-h-64 overflow-y-auto overscroll-contain p-1 [scrollbar-width:thin]">
            {visible.map((preset) => (
              <li key={preset.name}>
                <button
                  type="button"
                  role="option"
                  aria-selected={preset.name === value}
                  onClick={() => { onChange(preset.name); setOpen(false); setQuery(""); }}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-start text-[12.5px] transition-colors hover:bg-muted/60",
                    preset.name === value && "bg-muted/50",
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-foreground">{name(preset)}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">{preset.model}</span>
                  </span>
                  {preset.name === value ? <Check className="h-3.5 w-3.5 shrink-0" aria-hidden /> : null}
                </button>
              </li>
            ))}
            {visible.length === 0 ? <li className="px-2.5 py-2 text-[12px] text-muted-foreground">-</li> : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function TeamSettings({ token, onChanged }: { token: string; onChanged?: () => void }) {
  const { t } = useTranslation();
  const tx = useCallback((key: string, fallback: string, values?: Record<string, unknown>) =>
    String(t(`settings.team.${key}`, { defaultValue: fallback, ...(values ?? {}) })), [t]);

  const [catalog, setCatalog] = useState<CollabCatalog | null>(null);
  const [saved, setSaved] = useState<CollabSettings | null>(null);
  const [draft, setDraft] = useState<CollabSettings | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<TeamMember | null>(null);
  const [pendingReset, setPendingReset] = useState(false);
  const [skillQuery, setSkillQuery] = useState("");
  const [selectedTeamId, setSelectedTeamId] = useState<string>("");
  const [pendingTeamDelete, setPendingTeamDelete] = useState<TeamGroup | null>(null);
  const [pane, setPane] = useState<"general" | "teams" | "members">("general");
  const [emailStatus, setEmailStatus] = useState<{ ready: boolean; hint: string } | null>(null);
  const [testingEmail, setTestingEmail] = useState(false);
  const [emailResult, setEmailResult] = useState<{ ok: boolean; text: string } | null>(null);

  const apply = useCallback((payload: CollabPayload, keepSelection = true) => {
    setCatalog(payload.catalog);
    if (payload.email) setEmailStatus(payload.email);
    setSaved(payload.collab);
    setDraft(payload.collab);
    setSelectedId((current) => {
      const ids = payload.collab.members.map((m) => m.id);
      return keepSelection && ids.includes(current) ? current : ids[0] ?? "";
    });
    setSelectedTeamId((current) => {
      const ids = (payload.collab.teams ?? []).map((team) => team.id);
      return keepSelection && ids.includes(current) ? current : payload.collab.default_team || ids[0] || "";
    });
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    fetchCollabSettings(token)
      .then((payload) => { if (live) { apply(payload, false); setError(null); } })
      .catch((err) => { if (live) setError(errorText(err)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [token, apply]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 3500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const dirty = useMemo(() => !sameTeam(draft, saved), [draft, saved]);
  const selected = draft?.members.find((m) => m.id === selectedId) ?? null;
  const presetLabel = useCallback((name: string) => {
    const preset = catalog?.presets.find((p) => p.name === name);
    if (!preset) return name;
    return preset.name === "default" ? tx("mainModel", "Main model") : preset.label;
  }, [catalog, tx]);

  const update = (patch: Partial<CollabSettings>) => setDraft((prev) => (prev ? { ...prev, ...patch } : prev));
  const updateMember = (id: string, patch: Partial<TeamMember>) =>
    setDraft((prev) => prev ? { ...prev, members: prev.members.map((m) => (m.id === id ? { ...m, ...patch } : m)) } : prev);

  const roleDefaults = (role: TeamRole) => catalog?.roles.find((r) => r.key === role);

  const toggleEnabled = async (next: boolean) => {
    setToggling(true);
    setError(null);
    try {
      const payload = await setCollabEnabled(token, next);
      // Only the switch changed on the server: keep unsaved roster edits.
      setSaved((prev) => (prev ? { ...prev, enabled: payload.collab.enabled } : payload.collab));
      setDraft((prev) => (prev ? { ...prev, enabled: payload.collab.enabled } : payload.collab));
      onChanged?.();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setToggling(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      apply(await saveCollabSettings(token, draft));
      setNotice(tx("saved", "Team saved."));
      onChanged?.();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  const resetTeam = async () => {
    setPendingReset(false);
    setSaving(true);
    setError(null);
    try {
      apply(await saveCollabSettings(token, { reset: true }), false);
      setNotice(tx("restored", "Default team restored."));
      onChanged?.();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  const addMember = () => {
    if (!draft) return;
    const defaults = roleDefaults("developer");
    const name = uniqueName(tx("newName", "New teammate"), draft.members);
    let id = slug(name) || "member";
    const ids = new Set(draft.members.map((m) => m.id));
    for (let n = 2; ids.has(id); n += 1) id = `${slug(name)}-${n}`;
    const member: TeamMember = {
      id,
      name,
      role: "developer",
      title: tx("roles.developer", "Developer"),
      specialty: "",
      email: "",
      avatar: catalog?.avatars[draft.members.length % (catalog?.avatars.length || 1)] ?? "round",
      color: SWATCHES[draft.members.length % SWATCHES.length],
      model_preset: "default",
      reasoning_effort: defaults?.effort ?? "medium",
      sandbox: defaults?.sandbox ?? "workspace",
      prompt: defaults?.prompt ?? "",
      tools: [...(defaults?.tools ?? [])],
      skills: [...(defaults?.skills ?? [])],
      enabled: true,
    };
    update({ members: [...draft.members, member] });
    setSelectedId(id);
  };

  const duplicateMember = (source: TeamMember) => {
    if (!draft) return;
    const name = uniqueName(source.name, draft.members);
    const ids = new Set(draft.members.map((m) => m.id));
    let id = slug(name) || "member";
    for (let n = 2; ids.has(id); n += 1) id = `${slug(name)}-${n}`;
    update({ members: [...draft.members, { ...source, id, name, email: "", tools: [...source.tools], skills: [...source.skills] }] });
    setSelectedId(id);
  };

  const deleteMember = (member: TeamMember) => {
    setPendingDelete(null);
    if (!draft) return;
    const members = draft.members.filter((m) => m.id !== member.id);
    update({
      members,
      teams: draft.teams.map((team) => ({ ...team, members: team.members.filter((id) => id !== member.id) })),
    });
    if (selectedId === member.id) setSelectedId(members[0]?.id ?? "");
  };

  const applyRoleDefaults = (member: TeamMember) => {
    const defaults = roleDefaults(member.role);
    if (!defaults) return;
    updateMember(member.id, {
      tools: [...defaults.tools],
      skills: [...defaults.skills],
      prompt: defaults.prompt,
      sandbox: defaults.sandbox,
      reasoning_effort: defaults.effort || null,
    });
  };

  const updateTeam = (id: string, patch: Partial<TeamGroup>) =>
    setDraft((prev) => prev ? { ...prev, teams: prev.teams.map((team) => (team.id === id ? { ...team, ...patch } : team)) } : prev);

  const addTeam = () => {
    if (!draft) return;
    const taken = new Set(draft.teams.map((team) => team.name.trim().toLowerCase()));
    let name = tx("newTeam", "New team");
    for (let n = 2; taken.has(name.toLowerCase()); n += 1) name = `${tx("newTeam", "New team")} ${n}`;
    const ids = new Set(draft.teams.map((team) => team.id));
    let id = slug(name) || "team";
    for (let n = 2; ids.has(id); n += 1) id = `${slug(name) || "team"}-${n}`;
    update({ teams: [...draft.teams, { id, name, members: [] }] });
    setSelectedTeamId(id);
  };

  const deleteTeam = (team: TeamGroup) => {
    setPendingTeamDelete(null);
    if (!draft) return;
    const teams = draft.teams.filter((t) => t.id !== team.id);
    update({
      teams,
      default_team: draft.default_team === team.id ? teams[0]?.id ?? "" : draft.default_team,
    });
    if (selectedTeamId === team.id) setSelectedTeamId(teams[0]?.id ?? "");
  };

  if (loading) {
    return (
      <div className="flex h-48 items-center justify-center rounded-[24px] border border-border/50 bg-card/75 text-sm text-muted-foreground">
        <Loader2 className="me-2 h-4 w-4 animate-spin" />
        {tx("loading", "Loading the team...")}
      </div>
    );
  }
  if (!draft || !catalog) {
    return (
      <div className="rounded-[18px] border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
        {error ?? tx("loadError", "The team could not be loaded.")}
      </div>
    );
  }

  const filteredSkills = catalog.skills.filter((skill) => {
    const q = skillQuery.trim().toLowerCase();
    return !q || skill.name.toLowerCase().includes(q) || skill.description.toLowerCase().includes(q);
  });

  return (
    <div className="space-y-6" data-testid="team-settings">
      <div role="tablist" aria-label={tx("panes", "Team settings")} className="flex flex-wrap gap-1.5 rounded-2xl border border-border/50 bg-card/60 p-1">
        {([
          ["general", tx("paneGeneral", "General")],
          ["teams", `${tx("teams", "Teams")} (${draft.teams.length})`],
          ["members", `${tx("roster", "Members")} (${draft.members.length})`],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={pane === key}
            onClick={() => setPane(key)}
            className={cn(
              "h-8 rounded-xl px-3.5 text-[12.5px] font-medium transition-colors",
              pane === key ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {pane === "general" ? (
        <div className="space-y-6">
      <section>
        <Group>
          <Row
            title={tx("switchTitle", "Collaboration Mode")}
            description={tx(
              "switchHelp",
              "On: call your team by name in any chat (Agent, Plan, Build, Review, Reverse). Each teammate runs on its own model and you watch them work together live.",
            )}
          >
            <div className="flex items-center gap-2">
              {toggling ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden /> : null}
              <Switch
                checked={draft.enabled}
                onChange={(next) => void toggleEnabled(next)}
                label={tx("switchTitle", "Collaboration Mode")}
                testId="settings-collab-toggle"
              />
            </div>
          </Row>
        </Group>
      </section>

      <section>
        <Group>
          <Row
            title={tx("pattern", "Collaboration mode")}
            description={tx(`patterns.${draft.pattern}.help`, PATTERNS.find((p) => p.key === draft.pattern)?.help ?? "")}
          >
            <select
              value={draft.pattern}
              onChange={(event) => update({ pattern: event.target.value as TeamPattern })}
              aria-label={tx("pattern", "Collaboration mode")}
              className={cn(selectClass, "w-[min(280px,62vw)]")}
            >
              {PATTERNS.map((pattern) => (
                <option key={pattern.key} value={pattern.key}>
                  {tx(`patterns.${pattern.key}.label`, pattern.label)}
                </option>
              ))}
            </select>
          </Row>
        </Group>
      </section>

      <Foldable
        title={tx("rules", "How the team works")}
        summary={tx("rulesSummary", "{{rounds}} rounds, budget {{budget}}", {
          rounds: draft.max_rounds,
          budget: draft.budget_tokens ? draft.budget_tokens.toLocaleString("en-US") : "∞",
        })}
      >
        <Group>
          <Row title={tx("maxRounds", "Maximum rounds")} description={tx("maxRoundsHelp", "Assignments per teammate per mission before Navin stops and asks you.")}>
            <Input
              type="number"
              min={1}
              max={20}
              value={draft.max_rounds}
              onChange={(event) => update({ max_rounds: Math.max(1, Math.min(20, Number(event.target.value) || 1)) })}
              className="h-9 w-24 rounded-full px-4 text-[13px]"
              aria-label={tx("maxRounds", "Maximum rounds")}
            />
          </Row>
          <Row title={tx("budget", "Token budget per teammate")} description={tx("budgetHelp", "A teammate stops when one run exceeds it. 0 means no cap.")}>
            <Input
              type="number"
              min={0}
              step={10000}
              value={draft.budget_tokens}
              onChange={(event) => update({ budget_tokens: Math.max(0, Number(event.target.value) || 0) })}
              className="h-9 w-36 rounded-full px-4 text-[13px]"
              aria-label={tx("budget", "Token budget per teammate")}
            />
          </Row>
          <Row title={tx("autoFeedback", "Automatic feedback loop")} description={tx("autoFeedbackHelp", "Teammates send findings to each other and iterate without waiting for you.")}>
            <Switch checked={draft.auto_feedback} onChange={(next) => update({ auto_feedback: next })} label={tx("autoFeedback", "Automatic feedback loop")} />
          </Row>
          <Row title={tx("humanApproval", "Human approval for critical operations")} description={tx("humanApprovalHelp", "Deploy, push, data deletion, migrations and secrets are listed for you instead of run.")}>
            <Switch checked={draft.human_approval_critical} onChange={(next) => update({ human_approval_critical: next })} label={tx("humanApproval", "Human approval for critical operations")} />
          </Row>
        </Group>
      </Foldable>

      <Foldable
        testId="team-manager"
        title={tx("manager", "Human manager")}
        summary={draft.manager_email || tx("managerUnset", "not set")}
      >
        <Group>
          <div className="space-y-3 px-4 py-3">
            <p className="text-[12px] text-muted-foreground">
              {tx("managerHelp", "Teammates and Navin email this person for finished deliverables, decisions only a human can take, and blockers. Marketing teammates send campaigns from the Marketing desk.")}
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={tx("managerName", "Name")}>
                <Input
                  value={draft.manager_name ?? ""}
                  maxLength={80}
                  onChange={(event) => update({ manager_name: event.target.value })}
                  placeholder={tx("managerNamePlaceholder", "e.g. Aymen")}
                />
              </Field>
              <Field label={tx("managerEmail", "Email")}>
                <Input
                  type="email"
                  value={draft.manager_email ?? ""}
                  maxLength={254}
                  onChange={(event) => update({ manager_email: event.target.value })}
                  placeholder="name@company.com"
                />
              </Field>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11.5px] font-medium",
                  emailStatus?.ready ? "bg-[#1f9d74]/10 text-[#1f9d74]" : "bg-[#d4892a]/12 text-[#a15c07] dark:text-[#f0b35a]",
                )}
              >
                <Mail className="h-3.5 w-3.5" aria-hidden />
                {emailStatus?.ready
                  ? tx("smtpReady", "Email channel ready")
                  : tx("smtpMissing", "Email channel not configured: Settings > Channels > Email")}
              </span>
              <Button
                size="sm"
                variant="outline"
                className="rounded-full"
                disabled={testingEmail || dirty || !saved?.manager_email || !emailStatus?.ready}
                title={dirty ? tx("testEmailSaveFirst", "Save the team first") : undefined}
                onClick={() => {
                  setTestingEmail(true);
                  setEmailResult(null);
                  sendCollabTestEmail(token)
                    .then((answer) => setEmailResult({ ok: true, text: tx("testEmailSent", "Test email sent to {{to}}.", { to: answer.to }) }))
                    .catch((err) => setEmailResult({ ok: false, text: errorText(err) }))
                    .finally(() => setTestingEmail(false));
                }}
              >
                {testingEmail ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                {tx("testEmail", "Send a test email")}
              </Button>
              {emailResult ? (
                <span role="status" className={cn("text-[12px]", emailResult.ok ? "text-[#1f9d74]" : "text-[#c2410c]")}>
                  {emailResult.text}
                </span>
              ) : null}
            </div>
          </div>
          <div className="space-y-2 border-t border-border/50 px-4 py-3" data-testid="team-email-contacts">
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="text-[13px] font-medium text-foreground">{tx("contacts", "Email contacts")}</p>
                <p className="text-[12px] text-muted-foreground">
                  {tx("contactsHelp", "Agents can email one or several of these people by name, together with the manager.")}
                </p>
              </div>
              <Button
                size="sm"
                variant="outline"
                className="rounded-full"
                disabled={(draft.email_contacts?.length ?? 0) >= 50}
                onClick={() => update({ email_contacts: [...(draft.email_contacts ?? []), { name: "", email: "" }] })}
              >
                <Plus className="me-1 h-3.5 w-3.5" aria-hidden />
                {tx("addContact", "Add contact")}
              </Button>
            </div>
            {(draft.email_contacts ?? []).map((contact, index) => (
              <div key={index} className="flex items-center gap-2">
                <Input
                  value={contact.name}
                  maxLength={80}
                  placeholder={tx("contactName", "Name")}
                  aria-label={tx("contactName", "Name")}
                  onChange={(event) => update({
                    email_contacts: (draft.email_contacts ?? []).map((c, i) => (i === index ? { ...c, name: event.target.value } : c)),
                  })}
                  className="max-w-[12rem]"
                />
                <Input
                  type="email"
                  value={contact.email}
                  maxLength={254}
                  placeholder="name@company.com"
                  aria-label={tx("managerEmail", "Email")}
                  onChange={(event) => update({
                    email_contacts: (draft.email_contacts ?? []).map((c, i) => (i === index ? { ...c, email: event.target.value } : c)),
                  })}
                />
                <button
                  type="button"
                  onClick={() => update({ email_contacts: (draft.email_contacts ?? []).filter((_, i) => i !== index) })}
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                  aria-label={tx("removeContact", "Remove contact")}
                >
                  <X className="h-4 w-4" aria-hidden />
                </button>
              </div>
            ))}
          </div>
          <Row
            title={tx("allowAny", "Allow any email address")}
            description={tx("allowAnyHelp", "Off: only the manager and the contacts above. On: agents may write to any address, still limited per hour. Campaigns to lists go through the Marketing desk.")}
          >
            <Switch
              checked={Boolean(draft.email_allow_any)}
              onChange={(next) => update({ email_allow_any: next })}
              label={tx("allowAny", "Allow any email address")}
            />
          </Row>
        </Group>
      </Foldable>

        </div>
      ) : null}
      {pane === "teams" ? (
      <section data-testid="team-groups">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 px-1">
          <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
            {tx("teams", "Teams")} <span className="font-normal text-muted-foreground">({draft.teams.length})</span>
          </h2>
          <Button size="sm" variant="outline" className="rounded-full" onClick={addTeam}>
            <Plus className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("addTeam", "New team")}
          </Button>
        </div>
        <p className="mb-3 px-1 text-[12px] leading-5 text-muted-foreground">
          {tx("teamsHelp", "Each chat works with one team, picked in the chat. A teammate can be in several teams.")}
        </p>
        <div className="grid gap-4 lg:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]">
          <div className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
            {draft.teams.map((team, teamIndex) => {
              const tint = teamTint(teamIndex);
              const people = team.members
                .map((id) => draft.members.find((m) => m.id === id))
                .filter((m): m is TeamMember => Boolean(m));
              return (
                <button
                  key={team.id}
                  type="button"
                  onClick={() => setSelectedTeamId(team.id)}
                  aria-current={team.id === selectedTeamId ? "true" : undefined}
                  data-testid="team-card"
                  style={{
                    backgroundColor: `${tint}${team.id === selectedTeamId ? "29" : "14"}`,
                    borderColor: `${tint}${team.id === selectedTeamId ? "99" : "33"}`,
                    boxShadow: team.id === selectedTeamId ? `inset 3px 0 0 ${tint}` : `inset 3px 0 0 ${tint}88`,
                  }}
                  className="flex min-w-[13rem] flex-col gap-1.5 rounded-2xl border px-3 py-2.5 text-start transition-[background-color,border-color] hover:brightness-[1.03] lg:min-w-0"
                >
                  <span className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-foreground">{team.name || team.id}</span>
                    {draft.default_team === team.id ? (
                      <span className="shrink-0 rounded-full bg-[#1d6af0]/12 px-2 py-0.5 text-[10.5px] font-medium text-[#1d6af0] dark:text-blue-300">
                        {tx("defaultBadge", "Default")}
                      </span>
                    ) : null}
                  </span>
                  <span className="flex items-center -space-x-1.5 rtl:space-x-reverse">
                    {people.slice(0, 6).map((m) => (
                      <TeamBotAvatar key={m.id} avatar={m.avatar} color={m.color} size={22} className="ring-2 ring-card" title={m.name} />
                    ))}
                    {people.length === 0 ? (
                      <span className="text-[11.5px] text-muted-foreground">{tx("teamEmpty", "No teammate")}</span>
                    ) : people.length > 6 ? (
                      <span className="ps-2.5 text-[11px] text-muted-foreground">+{people.length - 6}</span>
                    ) : null}
                  </span>
                </button>
              );
            })}
            {draft.teams.length === 0 ? (
              <p className="px-1 text-[12.5px] text-muted-foreground">{tx("noTeams", "No team yet. Create one.")}</p>
            ) : null}
          </div>

          {(() => {
            const team = draft.teams.find((t) => t.id === selectedTeamId);
            if (!team) return null;
            return (
              <Group>
                <div className="grid gap-4 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end sm:px-5">
                  <Field label={tx("fields.teamName", "Team name")}>
                    <Input
                      value={team.name}
                      maxLength={40}
                      onChange={(e) => updateTeam(team.id, { name: e.target.value })}
                      className="h-9 rounded-full px-4 text-[13px]"
                    />
                  </Field>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant={draft.default_team === team.id ? "secondary" : "outline"}
                      className="h-9 rounded-full"
                      disabled={draft.default_team === team.id}
                      onClick={() => update({ default_team: team.id })}
                    >
                      {draft.default_team === team.id ? tx("isDefault", "Default team") : tx("makeDefault", "Make default")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-9 rounded-full text-destructive hover:text-destructive"
                      onClick={() => setPendingTeamDelete(team)}
                    >
                      <Trash2 className="me-1.5 h-3.5 w-3.5" aria-hidden />
                      {tx("delete", "Delete")}
                    </Button>
                  </div>
                </div>
                <div className="px-4 py-3.5 sm:px-5">
                  <span className="mb-2 block text-[12px] font-medium text-muted-foreground">
                    {tx("teamMembers", "Members")} <span className="font-normal">({team.members.length})</span>
                  </span>
                  <div className="grid gap-1.5 sm:grid-cols-2">
                    {draft.members.map((m) => {
                      const on = team.members.includes(m.id);
                      return (
                        <label
                          key={m.id}
                          className={cn(
                            "flex cursor-pointer items-center gap-2.5 rounded-xl border px-2.5 py-2 transition-colors",
                            on ? "border-[#1d6af0]/40 bg-[#1d6af0]/[0.06]" : "border-border/50 hover:bg-muted/40",
                            !m.enabled && "opacity-60",
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={on}
                            onChange={() =>
                              updateTeam(team.id, {
                                members: on ? team.members.filter((id) => id !== m.id) : [...team.members, m.id],
                              })
                            }
                            className="h-3.5 w-3.5 accent-[#1d6af0]"
                          />
                          <TeamBotAvatar avatar={m.avatar} color={m.color} size={26} />
                          <span className="min-w-0">
                            <span className="block truncate text-[13px] font-medium text-foreground">{m.name}</span>
                            <span className="block truncate text-[11.5px] text-muted-foreground">
                              {m.title || tx(`roles.${m.role}`, m.role)}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              </Group>
            );
          })()}
        </div>
      </section>

      ) : null}
      {pane === "members" ? (
      <section>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 px-1">
          <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
            {tx("roster", "Members")} <span className="font-normal text-muted-foreground">({draft.members.length})</span>
          </h2>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" className="rounded-full" onClick={() => setPendingReset(true)} disabled={saving}>
              <RotateCcw className="me-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("restore", "Restore default team")}
            </Button>
            <Button size="sm" variant="outline" className="rounded-full" onClick={addMember} disabled={draft.members.length >= 32}>
              <Plus className="me-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("add", "Add teammate")}
            </Button>
          </div>
        </div>

        <p className="mb-3 px-1 text-[12px] leading-5 text-muted-foreground">
          {tx("directoryHelp", "Every teammate, with its own identity, model and switch. A switched-off teammate is never called.")}
        </p>
        <div className="grid gap-4 lg:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]">
          <div className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
            {draft.members.map((member) => (
              <button
                key={member.id}
                type="button"
                onClick={() => setSelectedId(member.id)}
                aria-current={member.id === selectedId ? "true" : undefined}
                className={cn(
                  "flex min-w-[13rem] items-center gap-2.5 rounded-2xl border px-3 py-2.5 text-start transition-colors lg:min-w-0",
                  member.id === selectedId
                    ? "border-[#1d6af0]/45 bg-[#1d6af0]/[0.06]"
                    : "border-border/50 bg-card/70 hover:bg-muted/40",
                  !member.enabled && "opacity-60",
                )}
              >
                <TeamBotAvatar avatar={member.avatar} color={member.color} size={34} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13.5px] font-medium text-foreground">{member.name || tx("unnamed", "Unnamed")}</span>
                  <span className="block truncate text-[11.5px] text-muted-foreground">
                    {member.title || tx(`roles.${member.role}`, member.role)}
                  </span>
                  <span className="block truncate text-[11px] text-muted-foreground/80">
                    {presetLabel(member.model_preset)}
                    {member.reasoning_effort ? ` · ${tx(`efforts.${member.reasoning_effort}`, member.reasoning_effort)}` : ""}
                  </span>
                </span>
                <span onClick={(event) => event.stopPropagation()} className="shrink-0">
                  <Switch
                    checked={member.enabled}
                    onChange={(next) => updateMember(member.id, { enabled: next })}
                    label={tx("memberEnabled", "{{name}} available", { name: member.name })}
                  />
                </span>
              </button>
            ))}
            {draft.members.length === 0 ? (
              <p className="px-1 text-[12.5px] text-muted-foreground">{tx("empty", "No teammate yet. Add one or restore the default team.")}</p>
            ) : null}
          </div>

          {selected ? (
            <Group>
              <div className="flex flex-wrap items-center gap-3 px-4 py-3.5 sm:px-5">
                <TeamBotAvatar avatar={selected.avatar} color={selected.color} size={44} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[15px] font-semibold text-foreground">{selected.name || tx("unnamed", "Unnamed")}</div>
                  <div className="truncate text-[12px] text-muted-foreground">
                    {[selected.title, selected.email].filter(Boolean).join(" · ")}
                  </div>
                </div>
                <Button size="sm" variant="ghost" className="rounded-full" onClick={() => duplicateMember(selected)} disabled={draft.members.length >= 32}>
                  <Copy className="me-1.5 h-3.5 w-3.5" aria-hidden />
                  {tx("duplicate", "Duplicate")}
                </Button>
                <Button size="sm" variant="ghost" className="rounded-full text-destructive hover:text-destructive" onClick={() => setPendingDelete(selected)}>
                  <Trash2 className="me-1.5 h-3.5 w-3.5" aria-hidden />
                  {tx("delete", "Delete")}
                </Button>
              </div>

              <div className="grid gap-4 px-4 py-4 sm:grid-cols-2 sm:px-5">
                <Field label={tx("fields.name", "Name")}>
                  <Input value={selected.name} maxLength={40} onChange={(e) => updateMember(selected.id, { name: e.target.value })} className="h-9 rounded-full px-4 text-[13px]" />
                </Field>
                <Field label={tx("fields.title", "Job title")}>
                  <Input value={selected.title} maxLength={60} placeholder={tx("fields.titlePlaceholder", "e.g. Senior Developer")} onChange={(e) => updateMember(selected.id, { title: e.target.value })} className="h-9 rounded-full px-4 text-[13px]" />
                </Field>
                <Field label={tx("fields.role", "Role")} hint={tx("fields.roleHint", "The role decides the default tools, skills and prompt.")}>
                  <div className="flex gap-2">
                    <select
                      value={selected.role}
                      onChange={(e) => updateMember(selected.id, { role: e.target.value as TeamRole })}
                      className={selectClass}
                    >
                      {ROLES.map((role) => (
                        <option key={role.key} value={role.key}>{tx(`roles.${role.key}`, role.label)}</option>
                      ))}
                    </select>
                    <Button size="sm" variant="outline" className="h-9 shrink-0 rounded-full" onClick={() => applyRoleDefaults(selected)}>
                      {tx("applyRole", "Apply role defaults")}
                    </Button>
                  </div>
                </Field>
                <Field label={tx("fields.email", "Email")} hint={tx("fields.emailHint", "Signs this teammate's commits.")}>
                  <Input type="email" value={selected.email} placeholder="name@team.navin.local" onChange={(e) => updateMember(selected.id, { email: e.target.value })} className="h-9 rounded-full px-4 text-[13px]" />
                </Field>
                <Field label={tx("fields.specialty", "Specialty")} className="sm:col-span-2">
                  <Input value={selected.specialty} maxLength={200} placeholder={tx("fields.specialtyPlaceholder", "e.g. Payments, React performance, OAuth")} onChange={(e) => updateMember(selected.id, { specialty: e.target.value })} className="h-9 rounded-full px-4 text-[13px]" />
                </Field>

                <div className="sm:col-span-2">
                  <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">{tx("fields.avatar", "Avatar")}</span>
                  <div className="flex flex-wrap items-center gap-2">
                    {catalog.avatars.map((avatar) => (
                      <button
                        key={avatar}
                        type="button"
                        onClick={() => updateMember(selected.id, { avatar })}
                        aria-pressed={selected.avatar === avatar}
                        aria-label={avatar}
                        className={cn(
                          "rounded-full p-0.5 ring-offset-2 ring-offset-background transition-shadow",
                          selected.avatar === avatar ? "ring-2 ring-[#1d6af0]" : "hover:ring-1 hover:ring-border",
                        )}
                      >
                        <TeamBotAvatar avatar={avatar} color={selected.color} size={34} />
                      </button>
                    ))}
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {SWATCHES.map((color) => (
                      <button
                        key={color}
                        type="button"
                        onClick={() => updateMember(selected.id, { color })}
                        aria-pressed={selected.color.toLowerCase() === color}
                        aria-label={color}
                        className={cn(
                          "h-6 w-6 rounded-full ring-offset-2 ring-offset-background",
                          selected.color.toLowerCase() === color && "ring-2 ring-foreground/60",
                        )}
                        style={{ backgroundColor: color }}
                      />
                    ))}
                    <Input
                      value={selected.color}
                      maxLength={7}
                      onChange={(e) => updateMember(selected.id, { color: e.target.value.trim() })}
                      aria-label={tx("fields.color", "Color")}
                      className={cn(
                        "h-8 w-28 rounded-full px-3 font-mono text-[12px]",
                        !/^#[0-9a-fA-F]{6}$/.test(selected.color) && "border-destructive/60",
                      )}
                    />
                  </div>
                </div>

                <Field label={tx("fields.model", "Model")} hint={tx("fields.modelHint", "Each teammate really runs on this model.")}>
                  <ModelPicker
                    value={selected.model_preset}
                    presets={catalog.presets}
                    label={tx("fields.model", "Model")}
                    mainLabel={tx("mainModel", "Main model")}
                    searchLabel={tx("modelSearch", "Search a model")}
                    onChange={(name) => updateMember(selected.id, { model_preset: name })}
                  />
                </Field>
                <Field label={tx("fields.effort", "Reasoning effort")}>
                  <select
                    value={selected.reasoning_effort ?? ""}
                    onChange={(e) => updateMember(selected.id, { reasoning_effort: e.target.value || null })}
                    className={selectClass}
                  >
                    {catalog.efforts.map((effort) => (
                      <option key={effort || "auto"} value={effort}>
                        {effort ? tx(`efforts.${effort}`, effort) : tx("efforts.auto", "Auto")}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field
                  label={tx("fields.sandbox", "Sandbox")}
                  hint={tx(`sandboxes.${selected.sandbox}.help`, SANDBOXES.find((s) => s.key === selected.sandbox)?.help ?? "")}
                  className="sm:col-span-2"
                >
                  <select value={selected.sandbox} onChange={(e) => updateMember(selected.id, { sandbox: e.target.value as TeamSandbox })} className={cn(selectClass, "sm:w-[min(320px,100%)]")}>
                    {SANDBOXES.map((sandbox) => (
                      <option key={sandbox.key} value={sandbox.key}>{tx(`sandboxes.${sandbox.key}.label`, sandbox.label)}</option>
                    ))}
                  </select>
                </Field>

                <Field
                  label={tx("fields.prompt", "Prompt")}
                  hint={tx("fields.promptHint", "{{count}} / {{max}} characters. Added to this teammate's identity and team rules.", { count: selected.prompt.length, max: MAX_PROMPT })}
                  className="sm:col-span-2"
                >
                  <Textarea
                    value={selected.prompt}
                    maxLength={MAX_PROMPT}
                    rows={6}
                    onChange={(e) => updateMember(selected.id, { prompt: e.target.value })}
                    className="rounded-2xl px-4 py-3 text-[13px] leading-5"
                  />
                </Field>

                <div className="sm:col-span-2">
                  <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">
                    {tx("fields.tools", "Tools")} <span className="font-normal">({selected.tools.length})</span>
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {catalog.tools.map((tool) => {
                      const on = selected.tools.includes(tool);
                      return (
                        <button
                          key={tool}
                          type="button"
                          aria-pressed={on}
                          onClick={() => updateMember(selected.id, { tools: on ? selected.tools.filter((x) => x !== tool) : [...selected.tools, tool] })}
                          className={cn(
                            "rounded-full border px-2.5 py-1 font-mono text-[11.5px] transition-colors",
                            on ? "border-[#1d6af0]/40 bg-[#1d6af0]/10 text-foreground" : "border-border/60 text-muted-foreground hover:bg-muted/50",
                          )}
                        >
                          {tool}
                        </button>
                      );
                    })}
                  </div>
                  <span className="mt-1 block text-[11.5px] text-muted-foreground/85">
                    {tx("fields.toolsHint", "None selected means every subagent tool. The team tool is always kept so teammates can talk.")}
                  </span>
                </div>

                <div className="sm:col-span-2">
                  <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">
                    {tx("fields.skills", "Skills")} <span className="font-normal">({selected.skills.length})</span>
                  </span>
                  {selected.skills.length ? (
                    <div className="mb-2 flex flex-wrap gap-1.5">
                      {selected.skills.map((skill) => (
                        <span key={skill} className="inline-flex items-center gap-1 rounded-full border border-[#1d6af0]/40 bg-[#1d6af0]/10 py-1 pe-1.5 ps-2.5 text-[11.5px] text-foreground">
                          {skill}
                          <button
                            type="button"
                            aria-label={tx("removeSkill", "Remove {{name}}", { name: skill })}
                            onClick={() => updateMember(selected.id, { skills: selected.skills.filter((x) => x !== skill) })}
                            className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                          >
                            <X className="h-3 w-3" aria-hidden />
                          </button>
                        </span>
                      ))}
                    </div>
                  ) : null}
                  <div className="relative">
                    <Search className="pointer-events-none absolute start-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
                    <Input
                      value={skillQuery}
                      onChange={(e) => setSkillQuery(e.target.value)}
                      placeholder={tx("skillSearch", "Search skills")}
                      className="h-9 rounded-full ps-8 text-[13px]"
                    />
                  </div>
                  <div className="mt-2 max-h-56 overflow-y-auto rounded-2xl border border-border/50">
                    {filteredSkills.slice(0, 80).map((skill) => {
                      const on = selected.skills.includes(skill.name);
                      return (
                        <button
                          key={skill.name}
                          type="button"
                          aria-pressed={on}
                          onClick={() => updateMember(selected.id, { skills: on ? selected.skills.filter((x) => x !== skill.name) : [...selected.skills, skill.name] })}
                          className={cn(
                            "flex w-full items-start gap-2 border-b border-border/40 px-3 py-2 text-start last:border-b-0 hover:bg-muted/40",
                            on && "bg-[#1d6af0]/[0.06]",
                          )}
                        >
                          <span className={cn("mt-1 h-3 w-3 shrink-0 rounded-sm border", on ? "border-[#1d6af0] bg-[#1d6af0]" : "border-border")} aria-hidden />
                          <span className="min-w-0">
                            <span className="block text-[12.5px] font-medium text-foreground">{skill.name}</span>
                            {skill.description ? <span className="block truncate text-[11.5px] text-muted-foreground">{skill.description}</span> : null}
                          </span>
                        </button>
                      );
                    })}
                    {filteredSkills.length === 0 ? (
                      <p className="px-3 py-2.5 text-[12px] text-muted-foreground">{tx("noSkills", "No matching skill")}</p>
                    ) : null}
                  </div>
                </div>
              </div>
            </Group>
          ) : null}
        </div>
      </section>
      ) : null}

      <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-[18px] border border-border/50 bg-card/95 px-4 py-3 shadow-[0_10px_40px_rgba(15,23,42,0.10)] backdrop-blur-xl">
        <div className="min-w-0 text-[13px]">
          {error ? (
            <span className="font-medium text-destructive">{error}</span>
          ) : notice ? (
            <span className="font-medium text-blue-600 dark:text-blue-300">{notice}</span>
          ) : dirty ? (
            <span className="font-medium text-blue-600 dark:text-blue-300">{tx("unsaved", "Unsaved changes")}</span>
          ) : (
            <span className="text-muted-foreground">{tx("upToDate", "Team is up to date")}</span>
          )}
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" className="rounded-full" disabled={!dirty || saving} onClick={() => { setDraft(saved); setError(null); }}>
            {tx("discard", "Discard")}
          </Button>
          <Button size="sm" className="rounded-full" disabled={!dirty || saving} onClick={() => void save()} data-testid="team-save">
            {saving ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
            {tx("save", "Save team")}
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={tx("deleteTitle", "Remove {{name}}?", { name: pendingDelete?.name ?? "" })}
        description={tx("deleteHelp", "This teammate leaves the team when you save.")}
        confirmLabel={tx("delete", "Delete")}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => pendingDelete && deleteMember(pendingDelete)}
      />
      <ConfirmDialog
        open={pendingTeamDelete !== null}
        title={tx("deleteTeamTitle", "Delete the {{name}} team?", { name: pendingTeamDelete?.name ?? "" })}
        description={tx("deleteTeamHelp", "Its teammates stay in the directory. The team is removed when you save.")}
        confirmLabel={tx("delete", "Delete")}
        onCancel={() => setPendingTeamDelete(null)}
        onConfirm={() => pendingTeamDelete && deleteTeam(pendingTeamDelete)}
      />
      <ConfirmDialog
        open={pendingReset}
        title={tx("restoreTitle", "Restore the default team?")}
        description={tx("restoreHelp", "Your teammates, teams, prompts, tools and skills are replaced by the defaults.")}
        confirmLabel={tx("restoreConfirm", "Restore")}
        onCancel={() => setPendingReset(false)}
        onConfirm={() => void resetTeam()}
      />
    </div>
  );
}
