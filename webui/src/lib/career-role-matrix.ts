// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

// Editable starting points for searches, not mandatory job requirements.
export type CareerLabel = readonly [string, string];
type Skill = string | CareerLabel;
export interface CareerRole {
  label: CareerLabel;
  domains: string[];
  skills: CareerLabel[];
  aliases: string[];
}
const role = (fr: string, en: string, domains: string[], skills: Skill[], aliases: string[] = []): CareerRole =>
  ({ label: [fr, en], domains, skills: skills.map(value => typeof value === "string" ? [value, value] : value), aliases });

export const CAREER_ROLE_MATRIX: CareerRole[] = [
  role("Développeur full stack", "Full stack developer", ["IT"], ["JavaScript", "TypeScript", "React", "Node.js", "SQL", "Git", "REST API"], ["Fullstack", "Full-stack developer"]),
  role("Développeur frontend", "Frontend developer", ["IT"], ["HTML", "CSS", "JavaScript", "TypeScript", "React", ["Accessibilité web", "Web accessibility"], "Git"], ["Front-end developer"]),
  role("Développeur backend", "Backend developer", ["IT"], ["REST API", "SQL", "Git", ["Tests unitaires", "Unit testing"], ["Conception d'API", "API design"]], ["Back-end developer"]),
  role("Développeur Java", "Java developer", ["IT"], ["Java", "Spring Boot", "SQL", "JUnit", "Maven", "Git"]),
  role("Développeur Python", "Python developer", ["IT", "Data"], ["Python", "FastAPI", "SQL", "Pytest", "Git"]),
  role("Développeur .NET", ".NET developer", ["IT"], ["C#", ".NET", "ASP.NET Core", "SQL Server", "Entity Framework", "Git"]),
  role("Développeur mobile", "Mobile developer", ["IT"], [["Développement mobile", "Mobile development"], "REST API", ["Tests mobiles", "Mobile testing"], "Git"]),
  role("Ingénieur DevOps", "DevOps engineer", ["IT"], ["Linux", "Docker", "Kubernetes", "Terraform", "CI/CD", "Git"], ["DevOps"]),
  role("Ingénieur SRE", "Site reliability engineer", ["IT"], ["Linux", "Kubernetes", "Observability", ["Gestion des incidents", "Incident management"], "SLO / SLI", "Python"], ["SRE"]),
  role("Architecte cloud", "Cloud architect", ["IT"], [["Architecture cloud", "Cloud architecture"], "Terraform", "IAM", ["Sécurité cloud", "Cloud security"], "FinOps"], ["Cloud architecte"]),
  role("Architecte solutions", "Solutions architect", ["IT", "Conseil"], [["Architecture logicielle", "Software architecture"], ["Intégration de systèmes", "Systems integration"], "REST API", ["Modélisation", "Modelling"]]),
  role("Administrateur systèmes", "Systems administrator", ["IT"], ["Linux", "Windows Server", "Active Directory", ["Sauvegarde", "Backup"], "PowerShell"]),
  role("Ingénieur réseaux", "Network engineer", ["IT", "Télécommunications"], ["TCP/IP", "LAN / WAN", "VPN", ["Pare-feu", "Firewalls"], ["Supervision réseau", "Network monitoring"]]),
  role("Administrateur bases de données", "Database administrator", ["IT", "Data"], ["SQL", ["Optimisation SQL", "SQL optimisation"], ["Sauvegarde", "Backup"], ["Réplication", "Replication"], ["Sécurité des données", "Data security"]], ["DBA"]),
  role("Data engineer", "Data engineer", ["Data", "IT"], ["Python", "SQL", "ETL / ELT", "Apache Spark", "Airflow", ["Qualité des données", "Data quality"]], ["Data engineering", "Dataeng", "Ingénieur données"]),
  role("Data analyst", "Data analyst", ["Data"], ["SQL", "Excel", "Power BI", ["Analyse de données", "Data analysis"], ["Statistiques", "Statistics"]]),
  role("Data scientist", "Data scientist", ["Data"], ["Python", "SQL", "Machine learning", ["Statistiques", "Statistics"], ["Évaluation de modèles", "Model evaluation"]]),
  role("Ingénieur machine learning", "Machine learning engineer", ["Data", "IT"], ["Python", "PyTorch", "MLOps", "Docker", ["Évaluation de modèles", "Model evaluation"]], ["ML engineer"]),
  role("Ingénieur IA générative", "Generative AI engineer", ["IA générative", "Data", "IT"], ["Python", "LLM", "RAG", "Prompt engineering", "Embeddings", ["Recherche vectorielle", "Vector search"], ["Évaluation de modèles", "Model evaluation"], "LLMOps"], ["AI engineer", "Ingénieur IA", "Intelligence générative", "Intelligence artificielle générative", "IA gen", "IA générative", "GenAI", "Generative AI", "GenAI engineer", "LLM engineer"]),
  role("Architecte IA générative", "Generative AI architect", ["IA générative", "Data", "IT", "Conseil"], ["LLM", "RAG", ["Architecture cloud", "Cloud architecture"], ["Agents IA", "AI agents"], ["Sécurité des modèles", "Model security"], ["Optimisation des coûts", "Cost optimisation"]], ["GenAI architect", "Architecte GenAI"]),
  role("Ingénieur agents IA", "AI agent engineer", ["IA générative", "IT", "Data"], ["Python", "LLM", ["Agents IA", "AI agents"], "LangGraph", "MCP", ["Appels d'outils", "Tool calling"], ["Évaluation d'agents", "Agent evaluation"]], ["Agentic AI engineer", "Ingénieur IA agentique"]),
  role("Ingénieur RAG", "RAG engineer", ["IA générative", "Data", "IT"], ["Python", "RAG", "Embeddings", ["Recherche vectorielle", "Vector search"], ["Recherche hybride", "Hybrid search"], "Reranking", ["Évaluation de modèles", "Model evaluation"]]),
  role("Ingénieur LLMOps", "LLMOps engineer", ["IA générative", "Data", "IT"], ["Python", "LLMOps", "Docker", "Kubernetes", ["Inférence de modèles", "Model inference"], ["Observabilité LLM", "LLM observability"], ["Évaluation de modèles", "Model evaluation"]]),
  role("Spécialiste fine-tuning LLM", "LLM fine-tuning specialist", ["IA générative", "Data"], ["Python", "PyTorch", "Transformers", "Fine-tuning", "LoRA / QLoRA", ["Préparation de données", "Data preparation"], ["Évaluation de modèles", "Model evaluation"]]),
  role("Consultant IA générative", "Generative AI consultant", ["IA générative", "Conseil", "Data"], [["Analyse des cas d'usage", "Use case analysis"], "LLM", "Prompt engineering", ["Prototypage", "Prototyping"], ["Gouvernance IA", "AI governance"], ["Conduite du changement", "Change management"]], ["Consultant GenAI"]),
  role("Product owner IA générative", "Generative AI product owner", ["IA générative", "Conseil", "IT"], ["LLM", ["Analyse des cas d'usage", "Use case analysis"], ["Gestion du backlog", "Backlog management"], ["Évaluation de modèles", "Model evaluation"], ["Gouvernance IA", "AI governance"], "Agile / Scrum"]),
  role("Consultant BI", "BI consultant", ["Data", "Conseil"], ["SQL", "Power BI", "DAX", ["Modélisation de données", "Data modelling"], "ETL / ELT"]),
  role("Consultant cybersécurité", "Cybersecurity consultant", ["Cybersécurité", "IT"], [["Analyse des risques", "Risk assessment"], "IAM", "SIEM", ["Audit de sécurité", "Security auditing"], "ISO 27001"]),
  role("Analyste SOC", "SOC analyst", ["Cybersécurité"], ["SIEM", "EDR", ["Analyse de journaux", "Log analysis"], ["Réponse aux incidents", "Incident response"], "MITRE ATT&CK"]),
  role("Pentester", "Penetration tester", ["Cybersécurité"], [["Tests d'intrusion", "Penetration testing"], "OWASP", "Burp Suite", "Linux", ["Rapports de sécurité", "Security reporting"]]),
  role("Consultant GRC", "GRC consultant", ["Cybersécurité", "Conseil"], [["Gouvernance", "Governance"], ["Analyse des risques", "Risk assessment"], ["Conformité", "Compliance"], "ISO 27001", ["Audit", "Audit"]]),
  role("Chef de projet", "Project manager", ["Conseil", "IT"], [["Gestion de projet", "Project management"], ["Planification", "Planning"], ["Gestion budgétaire", "Budget management"], ["Gestion des risques", "Risk management"], ["Coordination", "Coordination"]], ["Project lead"]),
  role("Product owner", "Product owner", ["IT", "Conseil"], ["Agile / Scrum", ["Gestion du backlog", "Backlog management"], "User stories", ["Priorisation", "Prioritisation"], ["Recette fonctionnelle", "User acceptance testing"]], ["PO"]),
  role("Scrum master", "Scrum master", ["IT", "Conseil"], ["Agile / Scrum", ["Facilitation", "Facilitation"], ["Amélioration continue", "Continuous improvement"], ["Coaching d'équipe", "Team coaching"]]),
  role("Business analyst", "Business analyst", ["Conseil", "IT", "Finance"], [["Analyse métier", "Business analysis"], ["Recueil des besoins", "Requirements gathering"], "BPMN", "SQL", ["Recette fonctionnelle", "User acceptance testing"]]),
  role("Consultant SAP", "SAP consultant", ["IT", "Conseil"], ["SAP", ["Analyse métier", "Business analysis"], ["Paramétrage ERP", "ERP configuration"], ["Migration de données", "Data migration"], ["Recette fonctionnelle", "User acceptance testing"]]),
  role("Consultant Salesforce", "Salesforce consultant", ["IT", "Conseil"], ["Salesforce", "CRM", ["Automatisation des processus", "Process automation"], ["Analyse métier", "Business analysis"]]),
  role("Testeur QA", "QA tester", ["IT"], [["Tests fonctionnels", "Functional testing"], ["Plans de test", "Test plans"], ["Gestion des anomalies", "Defect management"], "SQL", "Postman"], ["QA analyst"]),
  role("Ingénieur QA automatisation", "QA automation engineer", ["IT"], ["Playwright", "Selenium", "CI/CD", ["Tests d'API", "API testing"], ["Tests de régression", "Regression testing"]]),
  role("Designer UX/UI", "UX/UI designer", ["IT", "Marketing"], ["Figma", ["Recherche utilisateur", "User research"], ["Prototypage", "Prototyping"], "Design systems", ["Accessibilité web", "Web accessibility"]]),
  role("Technicien support", "Support technician", ["IT"], [["Support utilisateurs", "User support"], ["Diagnostic technique", "Technical troubleshooting"], "ITIL", "Microsoft 365", ["Gestion des tickets", "Ticket management"]], ["Helpdesk"]),
  role("Infirmier", "Nurse", ["Santé"], [["Soins", "Nursing care"], ["Surveillance clinique", "Clinical monitoring"], ["Hygiène hospitalière", "Hospital hygiene"], ["Dossier patient", "Patient records"], ["Éducation du patient", "Patient education"]], ["Infirmière", "IDE", "Registered nurse"]),
  role("Médecin", "Doctor", ["Santé"], [["Diagnostic clinique", "Clinical diagnosis"], ["Prise en charge médicale", "Medical care"], ["Dossier patient", "Patient records"], ["Coordination des soins", "Care coordination"]], ["Physician"]),
  role("Coordinateur de soins", "Care coordinator", ["Santé"], [["Coordination", "Coordination"], ["Parcours de soins", "Care pathways"], ["Planification", "Planning"], ["Dossier patient", "Patient records"]]),
  role("Pharmacien", "Pharmacist", ["Santé"], [["Dispensation", "Dispensing"], ["Pharmacovigilance", "Pharmacovigilance"], ["Gestion des stocks", "Inventory management"], ["Conseil au patient", "Patient counselling"]]),
  role("Comptable", "Accountant", ["Finance"], [["Comptabilité", "Accounting"], ["Clôture comptable", "Financial closing"], ["Rapprochement bancaire", "Bank reconciliation"], "Excel", ["Fiscalité", "Taxation"]]),
  role("Contrôleur de gestion", "Financial controller", ["Finance"], [["Contrôle de gestion", "Management accounting"], ["Budget et prévisions", "Budgeting and forecasting"], ["Analyse des écarts", "Variance analysis"], "Excel", "Power BI"]),
  role("Analyste financier", "Financial analyst", ["Finance"], [["Analyse financière", "Financial analysis"], ["Modélisation financière", "Financial modelling"], "Excel", ["Évaluation d'entreprise", "Business valuation"]]),
  role("Auditeur financier", "Financial auditor", ["Finance", "Conseil"], [["Audit", "Audit"], ["Contrôle interne", "Internal controls"], ["Comptabilité", "Accounting"], ["Analyse des risques", "Risk assessment"]]),
  role("Gestionnaire assurance", "Insurance administrator", ["Assurance"], [["Gestion des contrats", "Contract administration"], ["Gestion des sinistres", "Claims handling"], ["Relation client", "Customer relations"], ["Analyse des risques", "Risk assessment"]]),
  role("Actuaire", "Actuary", ["Assurance", "Finance"], [["Statistiques", "Statistics"], ["Modélisation des risques", "Risk modelling"], ["Tarification", "Pricing"], "Python", "Excel"]),
  role("Ingénieur civil", "Civil engineer", ["BTP"], [["Génie civil", "Civil engineering"], "AutoCAD", ["Calcul de structures", "Structural analysis"], ["Suivi de chantier", "Site supervision"], "BIM"]),
  role("Conducteur de travaux", "Construction manager", ["BTP"], [["Suivi de chantier", "Site supervision"], ["Planification", "Planning"], ["Gestion budgétaire", "Budget management"], ["Coordination des sous-traitants", "Subcontractor coordination"], ["Sécurité de chantier", "Site safety"]]),
  role("Architecte bâtiment", "Building architect", ["BTP"], [["Conception architecturale", "Architectural design"], "AutoCAD", "Revit", "BIM", ["Coordination technique", "Technical coordination"]]),
  role("BIM coordinateur", "BIM coordinator", ["BTP"], ["BIM", "Revit", "Navisworks", ["Détection de conflits", "Clash detection"], ["Coordination technique", "Technical coordination"]]),
  role("Économiste de la construction", "Quantity surveyor", ["BTP"], [["Métrés", "Quantity takeoff"], ["Estimation des coûts", "Cost estimating"], ["Appels d'offres", "Tendering"], ["Suivi budgétaire", "Cost control"]]),
  role("Technicien de maintenance", "Maintenance technician", ["Industrie"], [["Maintenance industrielle", "Industrial maintenance"], ["Maintenance préventive", "Preventive maintenance"], ["Diagnostic de pannes", "Fault diagnosis"], "GMAO / CMMS", ["Lecture de plans", "Technical drawing interpretation"]]),
  role("Ingénieur mécanique", "Mechanical engineer", ["Industrie"], [["Conception mécanique", "Mechanical design"], "CAO / CAD", ["Dimensionnement", "Engineering calculations"], ["Simulation", "Simulation"], ["Industrialisation", "Industrialisation"]]),
  role("Ingénieur électrique", "Electrical engineer", ["Industrie", "Énergie"], [["Électrotechnique", "Electrical engineering"], ["Schémas électriques", "Electrical schematics"], ["Dimensionnement", "Engineering calculations"], ["Mise en service", "Commissioning"]]),
  role("Automaticien", "Automation engineer", ["Industrie"], ["PLC / API", "SCADA", ["Automatisme industriel", "Industrial automation"], ["Mise en service", "Commissioning"], ["Diagnostic de pannes", "Fault diagnosis"]]),
  role("Responsable qualité HSE", "Quality and HSE manager", ["Industrie", "BTP", "Énergie"], [["Qualité / HSE", "Quality / HSE"], ["Analyse des risques", "Risk assessment"], ["Audit", "Audit"], ["Amélioration continue", "Continuous improvement"], "ISO 9001"]),
  role("Ingénieur énergies renouvelables", "Renewable energy engineer", ["Énergie"], [["Énergies renouvelables", "Renewable energy"], ["Études de faisabilité", "Feasibility studies"], ["Dimensionnement", "Engineering calculations"], ["Efficacité énergétique", "Energy efficiency"]]),
  role("Ingénieur procédés", "Process engineer", ["Énergie", "Industrie"], [["Génie des procédés", "Process engineering"], "P&ID", ["Simulation de procédés", "Process simulation"], ["Sécurité des procédés", "Process safety"]]),
  role("Technicien télécoms", "Telecommunications technician", ["Télécommunications"], [["Fibre optique", "Fibre optics"], ["Réseaux télécoms", "Telecommunications networks"], ["Installation et maintenance", "Installation and maintenance"], ["Mesures et diagnostic", "Testing and troubleshooting"]]),
  role("Ingénieur radio", "Radio network engineer", ["Télécommunications"], ["4G / 5G", ["Planification radio", "Radio planning"], ["Optimisation réseau", "Network optimisation"], ["Mesures radio", "Radio measurements"]]),
  role("Responsable logistique", "Logistics manager", ["Transport"], ["Supply chain", ["Gestion des stocks", "Inventory management"], ["Planification transport", "Transport planning"], "WMS / TMS", ["Gestion des fournisseurs", "Supplier management"]]),
  role("Acheteur", "Buyer", ["Transport", "Commerce", "Industrie"], [["Achats", "Procurement"], ["Négociation", "Negotiation"], ["Sourcing fournisseurs", "Supplier sourcing"], ["Gestion des contrats", "Contract administration"], ["Analyse des coûts", "Cost analysis"]]),
  role("Gestionnaire supply chain", "Supply chain planner", ["Transport"], ["Supply chain", ["Prévision de la demande", "Demand forecasting"], ["Approvisionnement", "Replenishment"], "ERP", "Excel"]),
  role("Commercial", "Sales representative", ["Commerce"], [["Prospection", "Prospecting"], ["Négociation", "Negotiation"], "CRM", ["Relation client", "Customer relations"], ["Proposition commerciale", "Sales proposals"]], ["Sales", "Business developer"]),
  role("Responsable grands comptes", "Key account manager", ["Commerce"], [["Gestion de comptes", "Account management"], ["Négociation", "Negotiation"], "CRM", ["Stratégie commerciale", "Sales strategy"]], ["Account manager", "KAM"]),
  role("Responsable e-commerce", "E-commerce manager", ["Commerce", "Marketing"], ["E-commerce", ["Gestion de catalogue", "Catalogue management"], ["Conversion", "Conversion optimisation"], "Web analytics", "CRM"]),
  role("Responsable marketing", "Marketing manager", ["Marketing"], [["Stratégie marketing", "Marketing strategy"], ["Gestion de campagnes", "Campaign management"], "Web analytics", ["Gestion budgétaire", "Budget management"]]),
  role("Consultant SEO", "SEO consultant", ["Marketing"], ["SEO", ["Audit technique", "Technical auditing"], ["Recherche de mots-clés", "Keyword research"], "Google Search Console", ["Stratégie de contenu", "Content strategy"]]),
  role("Community manager", "Community manager", ["Marketing"], [["Réseaux sociaux", "Social media"], ["Création de contenu", "Content creation"], ["Modération", "Moderation"], ["Calendrier éditorial", "Editorial planning"]]),
  role("Graphiste", "Graphic designer", ["Marketing"], ["Adobe Illustrator", "Adobe Photoshop", ["Identité visuelle", "Visual identity"], ["Mise en page", "Layout design"]]),
  role("Recruteur", "Recruiter", ["RH"], [["Recrutement", "Recruitment"], ["Sourcing candidats", "Candidate sourcing"], ["Entretiens", "Interviewing"], "ATS", ["Évaluation des compétences", "Skills assessment"]], ["Talent acquisition"]),
  role("Gestionnaire de paie", "Payroll specialist", ["RH", "Finance"], [["Gestion de paie", "Payroll"], ["Administration du personnel", "Personnel administration"], ["Droit du travail", "Employment law"], "Excel"]),
  role("Responsable RH", "HR manager", ["RH"], [["Gestion RH", "HR management"], ["Recrutement", "Recruitment"], ["Relations sociales", "Employee relations"], ["Développement des compétences", "Learning and development"]]),
  role("Consultant en organisation", "Organisation consultant", ["Conseil"], [["Analyse des processus", "Process analysis"], ["Conduite du changement", "Change management"], ["Gestion de projet", "Project management"], ["Facilitation", "Facilitation"]]),
  role("Directeur d'hôtel", "Hotel manager", ["Hôtellerie"], [["Gestion hôtelière", "Hotel management"], ["Relation client", "Customer relations"], ["Gestion d'équipe", "Team management"], ["Gestion budgétaire", "Budget management"]]),
  role("Réceptionniste", "Receptionist", ["Hôtellerie"], [["Accueil", "Guest reception"], ["Gestion des réservations", "Reservation management"], "PMS", ["Relation client", "Customer relations"]]),
  role("Chef de cuisine", "Head chef", ["Hôtellerie"], [["Production culinaire", "Culinary production"], "HACCP", ["Gestion des stocks", "Inventory management"], ["Gestion d'équipe", "Team management"]]),
  role("Formateur", "Trainer", ["Éducation"], [["Ingénierie pédagogique", "Instructional design"], ["Animation de formation", "Training delivery"], ["Évaluation des acquis", "Learning assessment"], "LMS"]),
  role("Enseignant", "Teacher", ["Éducation"], [["Pédagogie", "Teaching methods"], ["Préparation de cours", "Lesson planning"], ["Évaluation des acquis", "Learning assessment"], ["Gestion de classe", "Classroom management"]]),
  role("Juriste", "Legal counsel", ["Juridique"], [["Analyse juridique", "Legal analysis"], ["Rédaction de contrats", "Contract drafting"], ["Veille juridique", "Legal research"], ["Conformité", "Compliance"]]),
  role("Délégué à la protection des données", "Data protection officer", ["Juridique", "Cybersécurité"], [["Protection des données", "Data protection"], "RGPD / GDPR", ["Analyse d'impact", "Impact assessments"], ["Audit", "Audit"]], ["DPO"]),
  role("Ingénieur agronome", "Agronomist", ["Agriculture"], [["Agronomie", "Agronomy"], ["Gestion des cultures", "Crop management"], ["Analyse des sols", "Soil analysis"], ["Irrigation", "Irrigation"]]),
  role("Responsable exploitation agricole", "Farm manager", ["Agriculture"], [["Gestion d'exploitation", "Farm management"], ["Planification agricole", "Agricultural planning"], ["Gestion d'équipe", "Team management"], ["Gestion budgétaire", "Budget management"]]),
  role("Gestionnaire marchés publics", "Public procurement specialist", ["Public", "Juridique"], [["Marchés publics", "Public procurement"], ["Appels d'offres", "Tendering"], ["Analyse des offres", "Bid evaluation"], ["Suivi des contrats", "Contract monitoring"]]),
  role("Chargé de développement territorial", "Regional development officer", ["Public"], [["Développement territorial", "Regional development"], ["Gestion de projet", "Project management"], ["Concertation", "Stakeholder consultation"], ["Recherche de financements", "Funding applications"]]),
];

export const normalizeCareerValue = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase().replace(/[-_/]+/g, " ").replace(/\s+/g, " ").trim();
export const localizeCareerLabel = (label: CareerLabel, language: string) => label[language.startsWith("fr") ? 0 : 1];
const valueKeys = new Map<string, string>();
const valueLabels = new Map<string, CareerLabel>();
for (const row of CAREER_ROLE_MATRIX) {
  const roleKey = `role:${normalizeCareerValue(row.label[1])}`;
  valueLabels.set(roleKey, row.label);
  for (const label of [...row.label, ...row.aliases]) valueKeys.set(normalizeCareerValue(label), roleKey);
  for (const skill of row.skills) {
    const skillKey = `skill:${normalizeCareerValue(skill[1])}`;
    valueLabels.set(skillKey, skill);
    for (const label of skill) valueKeys.set(normalizeCareerValue(label), skillKey);
  }
}
export const careerValueKey = (value: string) => valueKeys.get(normalizeCareerValue(value)) || normalizeCareerValue(value);
export const careerValueLabels = (value: string) => valueLabels.get(careerValueKey(value));
export const localizeCareerValue = (value: string, language: string) => {
  const labels = careerValueLabels(value);
  return labels ? localizeCareerLabel(labels, language) : value;
};
export function uniqueCareerValues(values: string[]): string[] {
  const seen = new Set<string>();
  return values.filter(value => { const key = careerValueKey(value); if (!key || seen.has(key)) return false; seen.add(key); return true; });
}
export function findCareerRole(value: string): CareerRole | undefined {
  const key = normalizeCareerValue(value);
  return CAREER_ROLE_MATRIX.find(row => [...row.label, ...row.aliases].some(label => normalizeCareerValue(label) === key));
}
export function skillsForCareerRoles(roles: string[], language: string): string[] {
  return uniqueCareerValues(roles.flatMap(value => findCareerRole(value)?.skills.map(skill => localizeCareerLabel(skill, language)) || []));
}
