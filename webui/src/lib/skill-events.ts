export const SKILLS_CHANGED_EVENT = "navin:skills-changed";

export function notifySkillsChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(SKILLS_CHANGED_EVENT));
}
