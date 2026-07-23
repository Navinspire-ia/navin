import { useCallback, useEffect, useMemo, useState } from "react";
import { MessageSquare, Pencil, Plus, Trash2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { fetchTeamRoster, updateTeamRoster } from "@/lib/api";
import type { TeamMember, TeamRosterPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

const MEMBER_COMMANDS = [
  "/team",
  "/forge",
  "/blueprint",
  "/inspect",
  "/fortify",
  "/probe",
  "/turbo",
  "/pulse",
  "/studio",
  "/campaign",
  "/seo",
  "/leads",
];

export function memberCallPrefix(member: TeamMember): string {
  const specialty = member.specialty ? ` Specialty: ${member.specialty}.` : "";
  const skills = member.skills.length
    ? ` Apply your skills: ${member.skills.join(", ")}.`
    : "";
  return (
    `${member.command} You are ${member.name}, ${member.role} in our organization.`
    + `${specialty}${skills} Stay in this role for the whole task.`
  );
}

type Draft = {
  id: string;
  name: string;
  role: string;
  specialty: string;
  avatar: string;
  command: string;
  skills: string;
  manager: string | null;
};

function draftFromMember(member: TeamMember): Draft {
  return { ...member, skills: member.skills.join(", ") };
}

function emptyDraft(manager: string | null): Draft {
  return {
    id: "",
    name: "",
    role: "",
    specialty: "",
    avatar: "👤",
    command: "/team",
    skills: "",
    manager,
  };
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

export function TeamOrgChart({ onCall }: { onCall?: (message: string) => void }) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const { token } = useClient();
  const [roster, setRoster] = useState<TeamRosterPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Draft | null>(null);
  const [isNew, setIsNew] = useState(false);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    void fetchTeamRoster(token)
      .then((payload) => {
        if (!cancelled) setRoster(payload);
      })
      .catch(() => {
        if (!cancelled) setError("load");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const persist = useCallback(
    (members: TeamMember[]) => {
      if (!token || !roster) return;
      const next = { ...roster, members };
      setRoster(next);
      void updateTeamRoster(token, next)
        .then(setRoster)
        .catch(() => setError("save"));
    },
    [token, roster],
  );

  const levels = useMemo(() => {
    const members = roster?.members ?? [];
    const roots = members.filter((m) => !m.manager);
    const byManager = new Map<string, TeamMember[]>();
    for (const member of members) {
      if (!member.manager) continue;
      const list = byManager.get(member.manager) ?? [];
      list.push(member);
      byManager.set(member.manager, list);
    }
    const rows: TeamMember[][] = [];
    let current = roots;
    const seen = new Set<string>();
    while (current.length > 0) {
      rows.push(current);
      current.forEach((m) => seen.add(m.id));
      current = current.flatMap((m) => byManager.get(m.id) ?? []).filter(
        (m) => !seen.has(m.id),
      );
    }
    // Anything unreachable (defensive) goes on a last row.
    const leftovers = members.filter((m) => !seen.has(m.id));
    if (leftovers.length > 0) rows.push(leftovers);
    return rows;
  }, [roster]);

  const saveDraft = useCallback(() => {
    if (!editing || !roster) return;
    const name = editing.name.trim();
    const role = editing.role.trim();
    if (!name || !role) return;
    const id = isNew ? slugify(name) || `member-${Date.now()}` : editing.id;
    const member: TeamMember = {
      id,
      name,
      role,
      specialty: editing.specialty.trim(),
      avatar: editing.avatar.trim() || "👤",
      command: editing.command,
      skills: editing.skills
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
        .slice(0, 16),
      manager: editing.manager,
    };
    const members = isNew
      ? [...roster.members, member]
      : roster.members.map((m) => (m.id === member.id ? member : m));
    persist(members);
    setEditing(null);
  }, [editing, isNew, roster, persist]);

  const removeMember = useCallback(
    (id: string) => {
      if (!roster) return;
      const members = roster.members
        .filter((m) => m.id !== id)
        .map((m) => (m.manager === id ? { ...m, manager: null } : m));
      persist(members);
    },
    [roster, persist],
  );

  if (!roster) {
    return (
      <div className="px-1 py-3 text-[12.5px] text-muted-foreground">
        {error
          ? tx("studio.org.loadError", "Could not load the organization.")
          : tx("studio.org.loading", "Loading the organization…")}
      </div>
    );
  }

  return (
    <section>
      <div className="mb-2.5 flex items-center justify-between">
        <h2 className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
          {tx("studio.org.title", "Organization")}
        </h2>
        <button
          type="button"
          onClick={() => {
            setIsNew(true);
            setEditing(emptyDraft(roster.members[0]?.id ?? null));
          }}
          className="flex items-center gap-1 rounded-md border border-border/60 px-2 py-1 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {tx("studio.org.addMember", "Add member")}
        </button>
      </div>

      <div className="flex flex-col items-center gap-0">
        {levels.map((row, rowIndex) => (
          <div key={rowIndex} className="flex w-full flex-col items-center">
            {rowIndex > 0 && (
              <div className="h-4 w-px bg-border/70" aria-hidden />
            )}
            <div className="flex w-full flex-wrap justify-center gap-2.5">
              {row.map((member) => (
                <div
                  key={member.id}
                  className="group flex w-[240px] flex-col gap-1.5 rounded-xl border border-border/60 bg-background p-3 transition-colors hover:border-foreground/40"
                >
                  <div className="flex items-center gap-2.5">
                    <span
                      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-border/60 bg-muted/40 text-[17px]"
                      aria-hidden
                    >
                      {member.avatar}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-semibold text-foreground">
                        {member.name}
                      </span>
                      <span className="block truncate text-[11.5px] text-muted-foreground">
                        {member.role}
                      </span>
                    </span>
                  </div>
                  {member.specialty ? (
                    <p className="line-clamp-2 text-[11.5px] leading-snug text-muted-foreground">
                      {member.specialty}
                    </p>
                  ) : null}
                  {member.skills.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {member.skills.slice(0, 3).map((skill) => (
                        <span
                          key={skill}
                          className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
                        >
                          {skill}
                        </span>
                      ))}
                      {member.skills.length > 3 ? (
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                          +{member.skills.length - 3}
                        </span>
                      ) : null}
                    </div>
                  ) : null}
                  <div className="mt-0.5 flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() =>
                        onCall?.(
                          `${memberCallPrefix(member)}\n\nIntroduce yourself in one line, then ask for the mission.`,
                        )
                      }
                      className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-[11.5px] font-medium text-foreground transition-colors hover:bg-foreground hover:text-background"
                    >
                      <MessageSquare className="h-3.5 w-3.5" aria-hidden />
                      {tx("studio.org.call", "Call")}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setIsNew(false);
                        setEditing(draftFromMember(member));
                      }}
                      aria-label={tx("studio.org.edit", "Edit")}
                      title={tx("studio.org.edit", "Edit")}
                      className="rounded-md p-1.5 text-muted-foreground opacity-0 transition-all hover:bg-muted hover:text-foreground group-hover:opacity-100"
                    >
                      <Pencil className="h-3.5 w-3.5" aria-hidden />
                    </button>
                    <button
                      type="button"
                      onClick={() => removeMember(member.id)}
                      aria-label={tx("studio.org.remove", "Remove")}
                      title={tx("studio.org.remove", "Remove")}
                      className="rounded-md p-1.5 text-muted-foreground opacity-0 transition-all hover:bg-muted hover:text-destructive group-hover:opacity-100"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {editing ? (
        <div className="mt-3 rounded-xl border border-border/60 bg-muted/20 p-3.5">
          <div className="mb-2.5 flex items-center justify-between">
            <span className="text-[12.5px] font-semibold text-foreground">
              {isNew
                ? tx("studio.org.addMember", "Add member")
                : tx("studio.org.editMember", "Edit member")}
            </span>
            <button
              type="button"
              onClick={() => setEditing(null)}
              aria-label={tx("studio.org.cancel", "Cancel")}
              className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <input
              type="text"
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              placeholder={tx("studio.org.fieldName", "Name (e.g. Nova)")}
              className="rounded-lg border border-border/60 bg-background px-2.5 py-1.5 text-[12.5px] text-foreground outline-none placeholder:text-muted-foreground/70 focus:border-foreground/50"
            />
            <input
              type="text"
              value={editing.role}
              onChange={(e) => setEditing({ ...editing, role: e.target.value })}
              placeholder={tx("studio.org.fieldRole", "Role (e.g. Marketing Lead)")}
              className="rounded-lg border border-border/60 bg-background px-2.5 py-1.5 text-[12.5px] text-foreground outline-none placeholder:text-muted-foreground/70 focus:border-foreground/50"
            />
            <input
              type="text"
              value={editing.specialty}
              onChange={(e) => setEditing({ ...editing, specialty: e.target.value })}
              placeholder={tx("studio.org.fieldSpecialty", "Specialty / functions")}
              className="rounded-lg border border-border/60 bg-background px-2.5 py-1.5 text-[12.5px] text-foreground outline-none placeholder:text-muted-foreground/70 focus:border-foreground/50 sm:col-span-2"
            />
            <input
              type="text"
              value={editing.avatar}
              onChange={(e) => setEditing({ ...editing, avatar: e.target.value })}
              placeholder={tx("studio.org.fieldAvatar", "Avatar (emoji)")}
              className="rounded-lg border border-border/60 bg-background px-2.5 py-1.5 text-[12.5px] text-foreground outline-none placeholder:text-muted-foreground/70 focus:border-foreground/50"
            />
            <select
              value={editing.command}
              onChange={(e) => setEditing({ ...editing, command: e.target.value })}
              aria-label={tx("studio.org.fieldCommand", "Workflow command")}
              className="rounded-lg border border-border/60 bg-background px-2.5 py-1.5 text-[12.5px] text-foreground outline-none focus:border-foreground/50"
            >
              {MEMBER_COMMANDS.map((command) => (
                <option key={command} value={command}>
                  {command}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={editing.skills}
              onChange={(e) => setEditing({ ...editing, skills: e.target.value })}
              placeholder={tx(
                "studio.org.fieldSkills",
                "Skills, comma-separated (e.g. campaign-manager, copywriting-agent)",
              )}
              className="rounded-lg border border-border/60 bg-background px-2.5 py-1.5 text-[12.5px] text-foreground outline-none placeholder:text-muted-foreground/70 focus:border-foreground/50 sm:col-span-2"
            />
            <select
              value={editing.manager ?? ""}
              onChange={(e) =>
                setEditing({ ...editing, manager: e.target.value || null })
              }
              aria-label={tx("studio.org.fieldManager", "Reports to")}
              className="rounded-lg border border-border/60 bg-background px-2.5 py-1.5 text-[12.5px] text-foreground outline-none focus:border-foreground/50"
            >
              <option value="">
                {tx("studio.org.noManager", "— top level (no manager)")}
              </option>
              {roster.members
                .filter((m) => m.id !== editing.id)
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} — {m.role}
                  </option>
                ))}
            </select>
          </div>
          <div className="mt-2.5 flex justify-end gap-1.5">
            <button
              type="button"
              onClick={() => setEditing(null)}
              className="rounded-md border border-border/60 px-2.5 py-1 text-[12px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              {tx("studio.org.cancel", "Cancel")}
            </button>
            <button
              type="button"
              onClick={saveDraft}
              disabled={!editing.name.trim() || !editing.role.trim()}
              className="rounded-md bg-foreground px-2.5 py-1 text-[12px] font-semibold text-background disabled:opacity-40"
            >
              {tx("studio.org.save", "Save")}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
