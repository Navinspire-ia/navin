// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { CAREER_ROLE_MATRIX, careerValueLabels, findCareerRole, localizeCareerLabel, normalizeCareerValue, skillsForCareerRoles, uniqueCareerValues } from "./career-role-matrix";

export const CAREER_DOMAINS = [
  ["Intelligence artificielle générative", "Generative artificial intelligence"],
  ["Informatique / IT", "Information technology / IT"], ["Data et intelligence artificielle", "Data and artificial intelligence"],
  ["Cybersécurité", "Cybersecurity"], ["Santé", "Healthcare"], ["Finance et banque", "Finance and banking"],
  ["Assurance", "Insurance"], ["BTP et construction", "Construction"], ["Industrie", "Manufacturing"],
  ["Énergie", "Energy"], ["Télécommunications", "Telecommunications"], ["Transport et logistique", "Transport and logistics"],
  ["Commerce", "Retail"], ["Marketing et communication", "Marketing and communications"],
  ["Ressources humaines", "Human resources"], ["Conseil", "Consulting"], ["Hôtellerie et tourisme", "Hospitality and tourism"],
  ["Éducation", "Education"], ["Juridique", "Legal"], ["Agriculture", "Agriculture"], ["Secteur public", "Public sector"],
];
const DOMAIN_TAGS = ["IA générative", "IT", "Data", "Cybersécurité", "Santé", "Finance", "Assurance", "BTP", "Industrie", "Énergie", "Télécommunications", "Transport", "Commerce", "Marketing", "RH", "Conseil", "Hôtellerie", "Éducation", "Juridique", "Agriculture", "Public"];
const SKILLS = [
  ...["React", "TypeScript", "JavaScript", "Python", "Java", "C#", ".NET", "Node.js", "Angular", "Vue.js", "SQL", "PostgreSQL", "AWS", "Azure", "Google Cloud", "Docker", "Kubernetes", "Terraform", "CI/CD", "Linux", "Power BI", "Machine learning", "SAP", "Salesforce", "Figma", "AutoCAD", "BIM"].map(value => [value, value]),
  ["Gestion de projet", "Project management"], ["Agile / Scrum", "Agile / Scrum"], ["Analyse métier", "Business analysis"],
  ["Soins", "Nursing care"], ["Coordination", "Coordination"], ["Comptabilité", "Accounting"], ["Audit", "Audit"],
  ["Contrôle de gestion", "Management accounting"], ["Génie civil", "Civil engineering"], ["Maintenance industrielle", "Industrial maintenance"],
  ["Qualité / HSE", "Quality / HSE"], ["Supply chain", "Supply chain"], ["Achats", "Procurement"],
  ["Négociation", "Negotiation"], ["Recrutement", "Recruitment"], ["SEO", "SEO"], ["Gestion de paie", "Payroll"],
];

export function careerSuggestions(kind: "domains" | "roles" | "skills", language: string, context: { domains?: string[]; roles?: string[] } = {}) {
  const index = language.startsWith("fr") ? 0 : 1;
  let values: string[];
  if (kind === "domains") values = CAREER_DOMAINS.map(row => row[index]);
  else if (kind === "roles") {
    const tags = new Set((context.domains || []).flatMap(domain => {
      const at = CAREER_DOMAINS.findIndex(labels => labels.some(label => normalizeCareerValue(label) === normalizeCareerValue(domain)));
      return at < 0 ? [] : [DOMAIN_TAGS[at]];
    }));
    const relevant = (domains: string[]) => domains.some(domain => tags.has(domain));
    values = [...CAREER_ROLE_MATRIX].sort((a, b) => Number(relevant(b.domains)) - Number(relevant(a.domains)))
      .map(row => localizeCareerLabel(row.label, language));
  } else values = [...skillsForCareerRoles(context.roles || [], language), ...SKILLS.map(row => row[index]),
    ...CAREER_ROLE_MATRIX.flatMap(row => row.skills.map(skill => localizeCareerLabel(skill, language)))];
  return uniqueCareerValues(values).map(text => ({ key: text, text,
    aliases: kind === "roles" ? [...(findCareerRole(text)?.aliases || []), ...(findCareerRole(text)?.label || [])]
      : kind === "domains" ? CAREER_DOMAINS.find(labels => labels.includes(text)) || [] : [...(careerValueLabels(text) || [])] }));
}
