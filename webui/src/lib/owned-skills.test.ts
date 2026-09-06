import { describe, expect, it } from "vitest";

import { isOwnedSkillSource, ownedSkillSummaries } from "./owned-skills";
import type { SkillSummary } from "./types";

function skill(name: string, source: string): SkillSummary {
  return {
    name,
    description: name,
    source,
    available: true,
  };
}

describe("ownedSkillSummaries", () => {
  it("keeps add-skill and install-skill entries in catalog order", () => {
    const skills = [
      skill("builtin-one", "builtin"),
      skill("team-playbook", "workspace"),
      skill("home-note", "user"),
      skill("from-pack", "plugin:demo"),
    ];
    expect(ownedSkillSummaries(skills).map((item) => item.name)).toEqual([
      "team-playbook",
      "home-note",
      "from-pack",
    ]);
  });

  it("rejects bundled builtins", () => {
    expect(isOwnedSkillSource("builtin")).toBe(false);
    expect(isOwnedSkillSource("workspace")).toBe(true);
    expect(isOwnedSkillSource("plugin:seo-pack")).toBe(true);
  });
});
