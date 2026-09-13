import { describe, expect, it } from "vitest";
import { careerRoleShares, initializeCompanyCriteria, updateCompanyCriteria } from "./career-company-autofill";
import { emptyProspecting, missionSourcesForCountries, type ProspectCriteria } from "./career-prospecting";
import { CAREER_ROLE_MATRIX, careerValueKey, skillsForCareerRoles } from "./career-role-matrix";
import { CAREER_DOMAINS, careerSuggestions } from "./career-suggestions";

const initial = (patch: Partial<ProspectCriteria> = {}) => initializeCompanyCriteria({ ...structuredClone(emptyProspecting.criteria), sources: [], ...patch }, "fr");
const change = <K extends keyof ProspectCriteria>(criteria: ProspectCriteria, key: K, value: ProspectCriteria[K]) => updateCompanyCriteria(criteria, key, value, "fr");

describe("company setup defaults", () => {
  it("selects every relevant source without API keys and preserves a saved opt-out", () => {
    let criteria = initial({ countries: ["MA"] });
    expect(criteria.sources).toEqual(missionSourcesForCountries(["MA"]).filter(source => !source.provider).map(source => source.id));
    expect(criteria.sources).not.toContain("google_jobs");
    expect(criteria.sources).not.toContain("freework");
    expect(criteria.platforms).toEqual(expect.arrayContaining(["linkedin", "freelancer", "freelancermap"]));
    expect(criteria.platforms).not.toContain("malt");
    criteria = change(criteria, "platforms", criteria.platforms.filter(source => source !== "freelancer"));
    criteria = change(criteria, "sources", criteria.sources.filter(source => source !== "remotive"));
    criteria = initializeCompanyCriteria(JSON.parse(JSON.stringify(criteria)), "fr");
    criteria = change(criteria, "countries", ["FR"]);
    expect(criteria.sources).toContain("freework");
    expect(criteria.sources).toContain("freelancescope");
    expect(criteria.sources).not.toContain("remotive");
    expect(criteria.platforms).toContain("malt");
    expect(criteria.platforms).not.toContain("freelancer");
    criteria = change(criteria, "sources", [...criteria.sources, "google_jobs"]);
    criteria = change(criteria, "countries", ["AE"]);
    expect(criteria.sources).toContain("google_jobs");
    expect(criteria.sources).not.toContain("freework");
  });

  it("fills candidate roles, domains and associated skills from company choices", () => {
    let criteria = initial();
    criteria = change(criteria, "domain", "Santé");
    criteria = change(criteria, "roles", ["Infirmier", "Coordinateur de soins"]);
    expect(criteria.skills).toContain("Soins");
    expect(criteria.skills).toContain("Coordination");
    expect(criteria.skills).not.toContain("React");
    expect(criteria.profile_domain).toBe("Santé");
    expect(criteria.profile_roles).toEqual(criteria.roles);
    expect(criteria.profile_skills).toEqual(criteria.skills);
    expect(new Set(criteria.skills.map(careerValueKey)).size).toBe(criteria.skills.length);
  });

  it("removes obsolete automatic skills while keeping manual additions", () => {
    let criteria = change(initial(), "roles", ["Développeur frontend"]);
    criteria = change(criteria, "skills", [...criteria.skills, "Protocole Atlas"]);
    criteria = change(criteria, "roles", ["Comptable"]);
    expect(criteria.skills).toContain("Comptabilité");
    expect(criteria.skills).toContain("Protocole Atlas");
    expect(criteria.skills).not.toContain("React");
    expect(criteria.profile_roles).toEqual(["Comptable"]);
    expect(criteria.profile_skills).toEqual(criteria.skills);
  });

  it("does not restore removed skills on the next role change or after reopening", () => {
    let criteria = change(initial(), "roles", ["Développeur frontend"]);
    criteria = change(criteria, "skills", criteria.skills.filter(skill => skill !== "React"));
    criteria = change(criteria, "roles", [...criteria.roles, "Développeur full stack"]);
    criteria = initializeCompanyCriteria(JSON.parse(JSON.stringify(criteria)), "fr");
    expect(criteria.skills).not.toContain("React");
    expect(criteria.profile_skills).not.toContain("React");
    criteria = change(criteria, "skills", [...criteria.skills, "React"]);
    expect(criteria.profile_skills).toContain("React");
  });

  it("lets candidate roles and skills diverge from the company without overwriting edits", () => {
    let criteria = change(initial(), "roles", ["Infirmier"]);
    criteria = change(criteria, "profile_roles", ["Comptable"]);
    expect(criteria.profile_skills).toContain("Comptabilité");
    expect(criteria.profile_skills).not.toContain("Soins");
    criteria = change(criteria, "profile_skills", ["Compétence spécifique"]);
    criteria = change(criteria, "roles", ["Infirmier", "Coordinateur de soins"]);
    expect(criteria.profile_roles).not.toContain("Infirmier");
    expect(criteria.profile_roles).toContain("Comptable");
    expect(criteria.profile_skills).toContain("Compétence spécifique");
    expect(criteria.profile_skills).not.toContain("Comptabilité");
    expect(initializeCompanyCriteria(JSON.parse(JSON.stringify(criteria)), "fr")).toEqual(criteria);
  });

  it("respects existing candidate criteria and user-defined roles", () => {
    const criteria = initial({ roles: ["Infirmier"], profile_roles: ["Métier Atlas"], profile_skills: ["Protocole Atlas"], profile_domain: "Industrie" });
    expect(criteria.profile_roles).toEqual(["Métier Atlas"]);
    expect(criteria.profile_skills).toEqual(["Protocole Atlas"]);
    expect(criteria.profile_domain).toBe("Industrie");
    expect(skillsForCareerRoles(["Métier Atlas"], "fr")).toEqual([]);
    const inherited = initial({ roles: ["Développeur frontend"], skills: ["React"], profile_roles: ["Développeur frontend"], profile_skills: ["React"] });
    expect(inherited.profile_skills).toEqual(inherited.skills);
    expect(inherited.profile_skills).toContain("HTML");
  });
});

describe("role and skill matrix", () => {
  it("offers specialized GenAI roles with French and English aliases and skills", () => {
    for (const language of ["fr", "en"]) {
      const roles = careerSuggestions("roles", language, { domains: ["Intelligence artificielle générative"] });
      expect(roles[0].text).toBe(language === "fr" ? "Ingénieur IA générative" : "Generative AI engineer");
      expect(roles[0].aliases).toContain("IA gen");
      const skills = skillsForCareerRoles(["Intelligence generative", "GenAI"], language);
      expect(skills).toEqual(skillsForCareerRoles([roles[0].text], language));
      expect(skills).toEqual(expect.arrayContaining(["LLM", "RAG", "Embeddings", "Prompt engineering", "LLMOps"]));
      expect(skills).toContain(language === "fr" ? "Recherche vectorielle" : "Vector search");
    }
    expect(CAREER_ROLE_MATRIX.filter(role => role.domains.includes("IA générative")).length).toBeGreaterThanOrEqual(8);
  });
  it("covers all company sectors with useful localized skills and unique roles", () => {
    expect(CAREER_ROLE_MATRIX.length).toBeGreaterThanOrEqual(75);
    expect(new Set(CAREER_ROLE_MATRIX.map(row => row.label[1])).size).toBe(CAREER_ROLE_MATRIX.length);
    expect(new Set(CAREER_ROLE_MATRIX.flatMap(row => row.domains)).size).toBe(CAREER_DOMAINS.length);
    for (const row of CAREER_ROLE_MATRIX) {
      expect(row.skills.length).toBeGreaterThanOrEqual(4);
      expect(row.skills.every(skill => skill[0] && skill[1])).toBe(true);
      expect(skillsForCareerRoles([row.label[0]], "en")).toEqual(skillsForCareerRoles([row.label[1]], "en"));
    }
  });
  it("recognizes aliases and accents without duplicate skills across roles or languages", () => {
    expect(skillsForCareerRoles(["Infirmiere"], "fr")).toContain("Soins");
    expect(skillsForCareerRoles(["DevOps", "Ingénieur DevOps"], "fr")).toEqual(skillsForCareerRoles(["DevOps engineer"], "fr"));
    let criteria = change(initial(), "roles", ["Infirmier"]);
    criteria = updateCompanyCriteria(criteria, "roles", ["Nurse", "Care coordinator"], "en");
    expect(new Set(criteria.skills.map(careerValueKey)).size).toBe(criteria.skills.length);
  });
  it("prioritizes relevant roles and skills while keeping other choices available", () => {
    const roles = careerSuggestions("roles", "fr", { domains: ["Santé"] });
    expect(roles[0].text).toBe("Infirmier");
    expect(roles.some(row => row.text === "Développeur frontend")).toBe(true);
    expect(careerSuggestions("skills", "fr", { roles: ["Infirmier"] })[0].text).toBe("Soins");
  });
});

describe("search priorities", () => {
  it("normalizes the requested 50/20/20 allocation and handles automatic and paused roles", () => {
    const roles = ["IA gen", "Dataeng", "Fullstack"];
    const shares = careerRoleShares(roles, { "IA gen": 50, Dataeng: 20, Fullstack: 20 });
    expect(shares["IA gen"]).toBeCloseTo(55.56, 2);
    expect(shares.Dataeng).toBeCloseTo(22.22, 2);
    expect(careerRoleShares(roles, { "IA gen": 50 })).toEqual({ "IA gen": 50, Dataeng: 25, Fullstack: 25 });
    expect(careerRoleShares(roles, { "IA gen": 100, Dataeng: 0 })).toEqual({ "IA gen": 100, Dataeng: 0, Fullstack: 0 });
    expect(careerRoleShares(["IA gen"], { "IA gen": 0 })).toEqual({ "IA gen": 0 });
    expect(careerRoleShares([], {})).toEqual({});
  });
  it("retains priorities through save and language changes and removes deleted roles", () => {
    let criteria = change(initial(), "roles", ["Ingénieur IA générative", "Data engineer", "Développeur full stack"]);
    criteria = change(criteria, "role_priorities", { "Ingénieur IA générative": 50, "Data engineer": 20, "Développeur full stack": 20 });
    criteria = initializeCompanyCriteria(JSON.parse(JSON.stringify(criteria)), "fr");
    expect(criteria.role_priorities["Ingénieur IA générative"]).toBe(50);
    expect(criteria.role_skills["Ingénieur IA générative"]).toContain("RAG");
    expect(criteria.role_skills["Ingénieur IA générative"]).not.toContain("React");
    criteria = updateCompanyCriteria(criteria, "roles", ["Generative AI engineer", "Data engineer"], "en");
    expect(criteria.role_priorities).toEqual({ "Generative AI engineer": 50, "Data engineer": 20 });
    expect(criteria.role_skills["Generative AI engineer"]).toContain("Vector search");
    expect(criteria.role_skills).not.toHaveProperty("Développeur full stack");
  });
});
