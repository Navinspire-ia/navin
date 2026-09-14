// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { DEFAULT_PLATFORM_CATALOG, missionSourcesForCountries, platformRelevant, suggestedMissionSources, type CompanyAutofillField, type MissionSource, type ProspectCriteria } from "./career-prospecting";
import { careerValueKey, skillsForCareerRoles, uniqueCareerValues } from "./career-role-matrix";

const split = (value: string) => value.split(",").map(part => part.trim()).filter(Boolean);
const values = (criteria: ProspectCriteria, field: CompanyAutofillField) => field === "profile_domain" ? split(criteria.profile_domain) : criteria[field];
const keySet = (items: string[]) => new Set(items.map(careerValueKey));
const sameValues = (left: string[], right: string[]) => left.length === right.length && left.every(value => keySet(right).has(careerValueKey(value)));
const publicPlatforms = (criteria: ProspectCriteria) => DEFAULT_PLATFORM_CATALOG.filter(source => source.indexed_profiles
  && platformRelevant(source.markets, criteria.profile_countries.length ? criteria.profile_countries : criteria.countries)).map(source => source.id);
const write = (criteria: ProspectCriteria, field: CompanyAutofillField, items: string[]): ProspectCriteria =>
  ({ ...criteria, [field]: field === "profile_domain" ? items.join(", ") : items });

function roleSettings(criteria: ProspectCriteria, language: string): ProspectCriteria {
  const priorities = new Map(Object.entries(criteria.role_priorities || {}).map(([role, value]) => [careerValueKey(role), value]));
  return { ...criteria,
    role_priorities: Object.fromEntries(criteria.roles.filter(role => priorities.has(careerValueKey(role))).map(role => [role, priorities.get(careerValueKey(role))!])),
    role_skills: Object.fromEntries([...new Set([...criteria.roles, ...criteria.profile_roles])].map(role => [role,
      [...new Set([...skillsForCareerRoles([role], language), ...skillsForCareerRoles([role], language.startsWith("fr") ? "en" : "fr")])]])) };
}

export function careerRoleShares(roles: string[], priorities: Record<string, number>): Record<string, number> {
  const specified = roles.filter(role => priorities[role] !== undefined);
  const total = specified.reduce((sum, role) => sum + priorities[role], 0);
  const automatic = roles.length > specified.length ? Math.max(0, 100 - total) / (roles.length - specified.length) : 0;
  const weights = roles.map(role => priorities[role] ?? automatic);
  const sum = weights.reduce((a, b) => a + b, 0);
  return Object.fromEntries(roles.map((role, index) => [role, sum ? weights[index] * 100 / sum : 0]));
}

function sync(criteria: ProspectCriteria, field: CompanyAutofillField, suggested: string[]): ProspectCriteria {
  const autofill = criteria.autofill!;
  const current = values(criteria, field);
  const generated = keySet(autofill.generated[field] || []);
  const dismissed = keySet(autofill.dismissed[field] || []);
  const desired = uniqueCareerValues(suggested).filter(value => !dismissed.has(careerValueKey(value)));
  const wanted = keySet(desired);
  const manual = keySet(current.filter(value => !generated.has(careerValueKey(value))));
  const kept = current.filter(value => manual.has(careerValueKey(value)) || wanted.has(careerValueKey(value)));
  return { ...write(criteria, field, uniqueCareerValues([...kept, ...desired])),
    autofill: { ...autofill, generated: { ...autofill.generated, [field]: desired.filter(value => !manual.has(careerValueKey(value))) } } };
}

function profileSkills(criteria: ProspectCriteria, language: string): string[] {
  const profiles = keySet(criteria.profile_roles);
  const company = keySet(criteria.roles);
  if (profiles.size === company.size && [...profiles].every(role => company.has(role))) return criteria.skills;
  const companyDefaults = keySet(skillsForCareerRoles(criteria.roles, language));
  const selected = keySet(criteria.skills);
  const defaults = skillsForCareerRoles(criteria.profile_roles, language).filter(skill =>
    !companyDefaults.has(careerValueKey(skill)) || selected.has(careerValueKey(skill)));
  return uniqueCareerValues([...defaults, ...criteria.skills.filter(skill => !companyDefaults.has(careerValueKey(skill)))]);
}

export function initializeCompanyCriteria(criteria: ProspectCriteria, language: string, catalog?: MissionSource[]): ProspectCriteria {
  const legacyFloor = Math.max(criteria.sale_rate || 0, criteria.min_rate || 0);
  criteria = { ...criteria, sale_rate_remote: criteria.sale_rate_remote ?? legacyFloor, sale_rate_onsite: criteria.sale_rate_onsite ?? legacyFloor };
  if (criteria.autofill?.version === 1) return roleSettings(criteria, language);
  let next: ProspectCriteria = { ...criteria, autofill: { version: 1, generated: {}, dismissed: {} } };
  next.autofill!.generated.sources = criteria.sources.filter(id => missionSourcesForCountries([], catalog).some(source => source.id === id && !source.provider));
  next.autofill!.generated.platforms = criteria.platforms.filter(id => DEFAULT_PLATFORM_CATALOG.some(source => source.id === id && source.indexed_profiles));
  if (sameValues(criteria.profile_roles, criteria.roles)) next.autofill!.generated.profile_roles = criteria.profile_roles;
  if (sameValues(criteria.profile_skills, criteria.skills)) next.autofill!.generated.profile_skills = criteria.profile_skills;
  if (criteria.profile_domain === criteria.domain) next.autofill!.generated.profile_domain = split(criteria.profile_domain);
  next = sync(next, "skills", skillsForCareerRoles(next.roles, language));
  // Existing candidate preferences take precedence over the initial defaults.
  if (!next.profile_roles.length || next.autofill!.generated.profile_roles) next = sync(next, "profile_roles", next.roles);
  if (!next.profile_domain.trim() || next.autofill!.generated.profile_domain) next = sync(next, "profile_domain", split(next.domain));
  if (!next.profile_skills.length || next.autofill!.generated.profile_skills) next = sync(next, "profile_skills", profileSkills(next, language));
  next = sync(next, "sources", suggestedMissionSources(next.countries, next.track, catalog));
  return roleSettings(sync(next, "platforms", publicPlatforms(next)), language);
}

export function updateCompanyCriteria<K extends keyof ProspectCriteria>(criteria: ProspectCriteria, field: K, value: ProspectCriteria[K], language: string, catalog?: MissionSource[]): ProspectCriteria {
  let next = initializeCompanyCriteria(criteria, language, catalog);
  const tracked: CompanyAutofillField[] = ["skills", "profile_roles", "profile_skills", "profile_domain", "sources", "platforms"];
  if (tracked.includes(field as CompanyAutofillField)) {
    const key = field as CompanyAutofillField;
    const previous = values(next, key);
    const chosen = typeof value === "string" ? split(value) : value as string[];
    const selected = keySet(chosen);
    const autofill = next.autofill!;
    const dismissed = uniqueCareerValues([...(autofill.dismissed[key] || []), ...previous.filter(item => !selected.has(careerValueKey(item)))])
      .filter(item => !selected.has(careerValueKey(item)));
    next = { ...next, autofill: { ...autofill, dismissed: { ...autofill.dismissed, [key]: dismissed },
      generated: { ...autofill.generated, [key]: (autofill.generated[key] || []).filter(item => selected.has(careerValueKey(item))) } } };
  }
  next = { ...next, [field]: value };
  if (field === "roles") {
    next = sync(next, "skills", skillsForCareerRoles(next.roles, language));
    next = sync(next, "profile_roles", next.roles);
  }
  if (field === "roles" || field === "skills" || field === "profile_roles") next = sync(next, "profile_skills", profileSkills(next, language));
  if (field === "domain") next = sync(next, "profile_domain", split(next.domain));
  if (field === "countries" || field === "track") next = sync(next, "sources", suggestedMissionSources(next.countries, next.track, catalog));
  if (field === "countries" || field === "profile_countries") next = sync(next, "platforms", publicPlatforms(next));
  return roleSettings(next, language);
}
