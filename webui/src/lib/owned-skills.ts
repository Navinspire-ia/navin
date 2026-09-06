import type { SkillSummary } from "@/lib/types";

/** True for skills the user added or installed (not bundled builtins). */
export function isOwnedSkillSource(source: string | null | undefined): boolean {
  const value = (source || "").trim();
  return value === "workspace" || value === "user" || value.startsWith("plugin:");
}

/** Workspace, user-home, and installed plugin skills, in catalog order. */
export function ownedSkillSummaries(skills: SkillSummary[]): SkillSummary[] {
  return skills.filter((skill) => isOwnedSkillSource(skill.source));
}
