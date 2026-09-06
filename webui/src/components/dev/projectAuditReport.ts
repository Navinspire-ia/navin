/**
 * Printable HTML report for the 360 project audit.
 *
 * The export path is deliberately browser-native: we build a self-contained
 * HTML document (inline CSS, inline Mermaid SVG) and hand it to the print
 * engine, which produces the PDF. No PDF library, no server round-trip, and
 * the result matches what the user saw on screen.
 */

import type {
  AuditFinding,
  AuditFindingsSection,
  ProjectAuditPayload,
} from "@/lib/types";

export type ReportSection =
  | "overview"
  | "api"
  | "architecture"
  | "rbac"
  | "sql"
  | "infra"
  | "pipeline"
  | "security"
  | "performance"
  | "notes";

export const REPORT_SECTIONS: ReportSection[] = [
  "overview",
  "api",
  "architecture",
  "rbac",
  "sql",
  "infra",
  "pipeline",
  "security",
  "performance",
  "notes",
];

/** Translation lookup, prewired to the app locale by the caller. */
type Tx = (key: string, fallback: string) => string;

function esc(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: "#dc2626",
  high: "#ea580c",
  medium: "#d97706",
  low: "#0284c7",
};

const METHOD_COLORS: Record<string, string> = {
  GET: "#059669",
  POST: "#2563eb",
  PUT: "#d97706",
  PATCH: "#7c3aed",
  DELETE: "#dc2626",
};

function scoreColor(score: number): string {
  if (score >= 80) return "#059669";
  if (score >= 60) return "#d97706";
  if (score >= 40) return "#ea580c";
  return "#dc2626";
}

function badge(text: string, color: string): string {
  return `<span class="badge" style="background:${color}1a;color:${color}">${esc(text)}</span>`;
}

function methodBadge(method: string): string {
  return badge(method, METHOD_COLORS[method] ?? "#52525b");
}

function kpi(label: string, value: string, hint = ""): string {
  return `<div class="kpi"><div class="kpi-label">${esc(label)}</div><div class="kpi-value">${esc(value)}</div>${
    hint ? `<div class="kpi-hint">${esc(hint)}</div>` : ""
  }</div>`;
}

function sectionTitle(title: string): string {
  return `<h2 class="section-title">${esc(title)}</h2>`;
}

function findingsHtml(section: AuditFindingsSection, tx: Tx, emptyLabel: string): string {
  if (!section.findings.length) {
    return `<p class="ok">${esc(emptyLabel)}</p>`;
  }
  const summary = (["critical", "high", "medium", "low"] as const)
    .map((level) =>
      badge(
        `${section.summary[level] ?? 0} ${tx(`dev.audit.severity.${level}`, level)}`,
        SEVERITY_COLORS[level],
      ),
    )
    .join(" ");
  const rows = section.findings
    .map(
      (finding: AuditFinding) => `
      <div class="finding">
        <div>${badge(tx(`dev.audit.severity.${finding.severity}`, finding.severity), SEVERITY_COLORS[finding.severity])}
          <strong>${esc(finding.message)}</strong></div>
        <div class="mono muted">${esc(finding.file)}:${finding.line} · ${esc(finding.category)}</div>
        ${finding.snippet ? `<pre>${esc(finding.snippet)}</pre>` : ""}
        <div class="muted">→ ${esc(finding.recommendation)}</div>
      </div>`,
    )
    .join("");
  return `<p>${summary}</p>${rows}`;
}

export function buildAuditReportHtml(
  audit: ProjectAuditPayload,
  sections: ReportSection[],
  tx: Tx,
  options: { locale: string; architectureSvg?: string | null },
): string {
  const fmt = new Intl.NumberFormat(options.locale === "fr" ? "fr-FR" : "en-US");
  const n = (value: number) => fmt.format(value);
  const { overview } = audit;
  const generated = new Date(audit.generated_at).toLocaleString(
    options.locale === "fr" ? "fr-FR" : undefined,
  );
  const parts: string[] = [];
  const has = (section: ReportSection) => sections.includes(section);

  // ------------------------------------------------------------- overview
  if (has("overview")) {
    parts.push(sectionTitle(tx("dev.audit.nav.overview", "Overview")));
    parts.push(`<div class="score-row">
      <div class="score" style="color:${scoreColor(overview.health_score)}">${overview.health_score}<span>/100</span>
        <div class="kpi-label">${esc(tx("dev.audit.health", "Global health"))}</div></div>
      <div class="score" style="color:${scoreColor(overview.scores.security)}">${overview.scores.security}<span>/100</span>
        <div class="kpi-label">${esc(tx("dev.audit.nav.security", "Security"))}</div></div>
      <div class="score" style="color:${scoreColor(overview.scores.performance)}">${overview.scores.performance}<span>/100</span>
        <div class="kpi-label">${esc(tx("dev.audit.nav.performance", "Performance"))}</div></div>
    </div>`);
    parts.push(`<p>${overview.stack.map((item) => badge(item, "#4f46e5")).join(" ")}</p>`);
    if (overview.git.is_repo) {
      parts.push(
        `<p class="muted">${esc(tx("dev.audit.report.branch", "Branch"))}: <strong>${esc(overview.git.branch ?? "")}</strong>
         · ${n(overview.git.commit_count ?? 0)} commits
         · ${overview.git.contributors?.length ?? 0} ${esc(tx("dev.audit.contributors", "contributors"))}
         ${overview.git.dirty ? ` · ${esc(tx("dev.audit.dirty", "uncommitted changes"))}` : ""}</p>`,
      );
    }
    parts.push(`<div class="kpi-grid">
      ${kpi(tx("dev.audit.kpi.files", "Files"), n(overview.totals.files))}
      ${kpi(tx("dev.audit.kpi.codeLines", "Lines of code"), n(overview.totals.code_lines), `${n(overview.totals.lines)} ${tx("dev.audit.kpi.totalLines", "lines total")}`)}
      ${kpi(tx("dev.audit.kpi.endpoints", "API endpoints"), n(overview.api_endpoints))}
      ${kpi(tx("dev.audit.kpi.sql", "SQL queries"), n(overview.sql_queries))}
      ${kpi(tx("dev.audit.kpi.tests", "Test files"), n(overview.tests.files))}
      ${kpi(tx("dev.audit.kpi.docs", "Doc files"), n(overview.docs.files))}
      ${kpi("TODO / FIXME", n(overview.todos.todo + overview.todos.fixme + overview.todos.hack))}
      ${kpi(
        tx("dev.audit.kpi.deps", "Dependencies"),
        n(overview.dependencies.managers.reduce((acc, m) => acc + m.runtime + m.dev, 0)),
      )}
    </div>`);
    parts.push(`<h3>${esc(tx("dev.audit.languages", "Languages"))}</h3>
      <table><thead><tr><th>${esc(tx("dev.audit.report.language", "Language"))}</th><th>${esc(
        tx("dev.audit.filesLabel", "files"),
      )}</th><th>${esc(tx("dev.audit.lines", "lines"))}</th><th>%</th></tr></thead><tbody>
      ${overview.languages
        .map(
          (lang) =>
            `<tr><td>${esc(lang.name)}</td><td>${n(lang.files)}</td><td>${n(lang.lines)}</td><td>${lang.percent}%</td></tr>`,
        )
        .join("")}
      </tbody></table>`);
    if (overview.git.last_commits?.length) {
      parts.push(`<h3>${esc(tx("dev.audit.recentCommits", "Recent commits"))}</h3>
        <table><tbody>${overview.git.last_commits
          .map(
            (commit) =>
              `<tr><td class="mono">${esc(commit.hash)}</td><td>${esc(commit.message)}</td><td>${esc(commit.author)}</td><td>${esc(commit.date)}</td></tr>`,
          )
          .join("")}</tbody></table>`);
    }
  }

  // ------------------------------------------------------------------ api
  if (has("api")) {
    parts.push(sectionTitle(tx("dev.audit.nav.api", "API Modules")));
    parts.push(
      `<p class="muted">${n(audit.api.total)} endpoints · ${audit.api.modules.length} modules · ${esc(audit.api.frameworks.join(", "))}</p>`,
    );
    for (const module of audit.api.modules) {
      parts.push(`<h3 class="mono">${esc(module.module)} <span class="muted">(${esc(module.framework)})</span></h3>
        <table><tbody>${module.endpoints
          .map(
            (endpoint) =>
              `<tr><td style="width:70px">${methodBadge(endpoint.method)}</td><td class="mono">${esc(endpoint.path)}</td><td>${
                endpoint.auth
                  ? badge(tx("dev.audit.report.guarded", "guarded"), "#059669")
                  : badge(tx("dev.audit.report.noGuard", "no guard"), "#d97706")
              }</td><td class="muted">L${endpoint.line}</td></tr>`,
          )
          .join("")}</tbody></table>`);
    }
    if (!audit.api.modules.length) {
      parts.push(`<p class="muted">${esc(tx("dev.audit.noApi", "No HTTP endpoint detected in this project."))}</p>`);
    }
  }

  // --------------------------------------------------------- architecture
  if (has("architecture")) {
    parts.push(sectionTitle(tx("dev.audit.nav.architecture", "Architecture")));
    if (options.architectureSvg) {
      parts.push(`<div class="diagram">${options.architectureSvg}</div>`);
    } else {
      parts.push(`<pre>${esc(audit.architecture.mermaid)}</pre>`);
    }
    parts.push(`<table><thead><tr><th>${esc(tx("dev.audit.archLayers", "Detected layers"))}</th><th>${esc(
      tx("dev.audit.report.folders", "Folders"),
    )}</th><th>${esc(tx("dev.audit.filesLabel", "files"))}</th></tr></thead><tbody>
      ${audit.architecture.layers
        .map(
          (layer) =>
            `<tr><td>${esc(layer.name)}</td><td class="mono">${esc(layer.dirs.map((d) => `${d}/`).join("  "))}</td><td>${n(layer.files)}</td></tr>`,
        )
        .join("")}</tbody></table>`);
  }

  // ----------------------------------------------------------------- rbac
  if (has("rbac")) {
    parts.push(sectionTitle(tx("dev.audit.nav.rbac", "Permissions / RBAC")));
    parts.push(`<div class="kpi-grid">
      ${kpi(tx("dev.audit.kpi.protected", "With auth guard"), n(audit.permissions.protected_endpoints))}
      ${kpi(tx("dev.audit.kpi.unprotected", "No guard detected"), n(audit.permissions.unprotected_count))}
      ${kpi(tx("dev.audit.kpi.mechanisms", "Auth mechanisms"), n(audit.permissions.mechanisms.length))}
      ${kpi(tx("dev.audit.kpi.roles", "Roles found"), n(audit.permissions.roles.length))}
    </div>`);
    if (audit.permissions.roles.length) {
      parts.push(
        `<p>${audit.permissions.roles.map((role) => badge(`${role.name} · ${role.occurrences}`, "#4f46e5")).join(" ")}</p>`,
      );
    }
    if (audit.permissions.unprotected_endpoints.length) {
      parts.push(`<h3>${esc(tx("dev.audit.unprotectedTitle", "Endpoints without a detected guard (heuristic)"))}</h3>
        <table><tbody>${audit.permissions.unprotected_endpoints
          .map(
            (endpoint) =>
              `<tr><td style="width:70px">${methodBadge(endpoint.method)}</td><td class="mono">${esc(endpoint.path)}</td><td class="mono muted">${esc(endpoint.module)}:${endpoint.line}</td></tr>`,
          )
          .join("")}</tbody></table>`);
    }
    if (audit.permissions.mechanisms.length) {
      parts.push(`<h3>${esc(tx("dev.audit.mechanismsTitle", "Detected auth / permission code"))}</h3>
        <table><tbody>${audit.permissions.mechanisms
          .slice(0, 120)
          .map(
            (mechanism) =>
              `<tr><td>${esc(mechanism.kind)}</td><td class="mono">${esc(mechanism.name)}</td><td class="mono muted">${esc(mechanism.file)}:${mechanism.line}</td></tr>`,
          )
          .join("")}</tbody></table>`);
    }
  }

  // ------------------------------------------------------------------ sql
  if (has("sql")) {
    parts.push(sectionTitle(tx("dev.audit.nav.sql", "SQL Queries")));
    parts.push(
      `<p>${badge(`score ${audit.sql.score ?? 100}/100`, "#4f46e5")} ${badge(
        `${audit.sql.files_scanned ?? 0} files`,
        "#64748b",
      )} ${Object.entries(audit.sql.by_kind)
        .map(([kind, count]) => badge(`${kind} · ${count}`, "#4f46e5"))
        .join(" ")}</p>`,
    );
    if (audit.sql.findings?.length) {
      parts.push(`<h3>${esc(tx("dev.audit.sqlFindings", "SQL security & performance"))}</h3>`);
      parts.push(
        findingsHtml(
          {
            score: audit.sql.score ?? 100,
            summary: audit.sql.summary ?? { critical: 0, high: 0, medium: 0, low: 0 },
            findings: audit.sql.findings,
            scored_findings: audit.sql.scored_findings,
            engine: audit.sql.engine,
          },
          tx,
          tx("dev.audit.noSqlFindings", "No SQL security or performance issue detected."),
        ),
      );
    }
    parts.push(`<h3>${esc(tx("dev.audit.sqlCatalog", "Query catalog"))}</h3>`);
    for (const query of audit.sql.queries) {
      parts.push(`<div class="finding">
        <div>${badge(query.kind, "#4f46e5")} <span class="mono muted">${esc(query.file)}:${query.line}</span>
        ${query.risky ? badge(tx("dev.audit.report.risky", "risky"), SEVERITY_COLORS.critical) : ""}</div>
        <pre>${esc(query.sql)}</pre>
        ${query.risky ? `<div class="muted">→ ${esc(query.risk_reason)}</div>` : ""}
      </div>`);
    }
    if (!audit.sql.queries.length) {
      parts.push(`<p class="muted">${esc(tx("dev.audit.noSql", "No SQL query detected in this project."))}</p>`);
    }
  }

  // ---------------------------------------------------------------- infra
  if (has("infra")) {
    parts.push(sectionTitle(tx("dev.audit.nav.infra", "Infrastructure")));
    for (const compose of audit.infrastructure.docker_compose) {
      parts.push(`<h3>Docker Compose · <span class="mono">${esc(compose.file)}</span></h3>
        <table><thead><tr><th>Service</th><th>Image</th><th>${esc(tx("dev.audit.ports", "Ports"))}</th><th>${esc(
          tx("dev.audit.dependsOn", "Depends on"),
        )}</th></tr></thead><tbody>
        ${compose.services
          .map(
            (service) =>
              `<tr><td><strong>${esc(service.name)}</strong></td><td class="mono">${esc(service.image)}</td><td>${esc(service.ports.join(", "))}</td><td>${esc(service.depends_on.join(", "))}</td></tr>`,
          )
          .join("")}</tbody></table>`);
    }
    if (audit.infrastructure.dockerfiles.length) {
      parts.push(`<h3>Dockerfiles</h3><table><tbody>${audit.infrastructure.dockerfiles
        .map(
          (dockerfile) =>
            `<tr><td class="mono">${esc(dockerfile.path)}</td><td class="mono">${esc(dockerfile.base_images.join(" / "))}</td><td>${
              dockerfile.exposed_ports.length ? `EXPOSE ${esc(dockerfile.exposed_ports.join(", "))}` : ""
            }</td></tr>`,
        )
        .join("")}</tbody></table>`);
    }
    if (audit.infrastructure.ci.length) {
      parts.push(`<h3>${esc(tx("dev.audit.ciTitle", "Continuous integration"))}</h3>
        <table><tbody>${audit.infrastructure.ci
          .map(
            (workflow) =>
              `<tr><td><strong>${esc(workflow.name)}</strong></td><td>${esc(workflow.triggers.join(", "))}</td><td class="mono muted">${esc(workflow.path)}</td></tr>`,
          )
          .join("")}</tbody></table>`);
    }
    if (audit.infrastructure.makefile_targets.length) {
      parts.push(`<h3>${esc(tx("dev.audit.makeTitle", "Makefile targets"))}</h3>
        <p>${audit.infrastructure.makefile_targets.map((target) => badge(`make ${target}`, "#52525b")).join(" ")}</p>`);
    }
    if (audit.infrastructure.env_files.length) {
      parts.push(`<h3>${esc(tx("dev.audit.envTitle", "Environment files"))}</h3>
        <table><tbody>${audit.infrastructure.env_files
          .map(
            (env) =>
              `<tr><td class="mono">${esc(env.path)}</td><td>${
                env.is_example
                  ? badge("example", "#52525b")
                  : env.gitignored
                    ? badge("gitignored", "#059669")
                    : badge(tx("dev.audit.notIgnored", "not gitignored"), "#dc2626")
              }</td><td class="mono muted">${esc(env.keys.join(", "))}</td></tr>`,
          )
          .join("")}</tbody></table>`);
    }
  }

  // ------------------------------------------------------------- pipeline
  if (has("pipeline")) {
    parts.push(sectionTitle(tx("dev.audit.nav.pipeline", "Core & Pipeline")));
    if (audit.pipeline.entrypoints.length) {
      parts.push(`<h3>${esc(tx("dev.audit.entrypoints", "Entry points"))}</h3>
        <table><tbody>${audit.pipeline.entrypoints
          .map((entry) => `<tr><td class="mono">${esc(entry.path)}</td><td>${esc(entry.kind)}</td></tr>`)
          .join("")}</tbody></table>`);
    }
    if (audit.pipeline.core_layers.length) {
      parts.push(`<h3>${esc(tx("dev.audit.coreLayers", "Core layers"))}</h3>
        <table><tbody>${audit.pipeline.core_layers
          .map(
            (layer) =>
              `<tr><td class="mono">${esc(layer.path)}</td><td>${esc(layer.description)}</td><td>${n(layer.files)} ${esc(
                tx("dev.audit.filesLabel", "files"),
              )}</td></tr>`,
          )
          .join("")}</tbody></table>`);
    }
    if (audit.pipeline.middleware.length) {
      parts.push(`<h3>${esc(tx("dev.audit.middleware", "Request pipeline / middleware"))}</h3>
        <table><tbody>${audit.pipeline.middleware
          .map(
            (middleware) =>
              `<tr><td class="mono">${esc(middleware.snippet)}</td><td class="mono muted">${esc(middleware.file)}:${middleware.line}</td></tr>`,
          )
          .join("")}</tbody></table>`);
    }
  }

  // ------------------------------------------------------------- security
  if (has("security")) {
    parts.push(sectionTitle(tx("dev.audit.nav.security", "Security")));
    parts.push(
      `<p class="score-inline" style="color:${scoreColor(audit.security.score)}">${esc(
        tx("dev.audit.securityScore", "Security score"),
      )}: <strong>${audit.security.score}/100</strong></p>`,
    );
    parts.push(
      findingsHtml(
        audit.security,
        tx,
        tx("dev.audit.noSecurityFindings", "No security risk detected by static analysis."),
      ),
    );
  }

  // ---------------------------------------------------------- performance
  if (has("performance")) {
    parts.push(sectionTitle(tx("dev.audit.nav.performance", "Performance")));
    parts.push(
      `<p class="score-inline" style="color:${scoreColor(audit.performance.score)}">${esc(
        tx("dev.audit.perfScore", "Performance score"),
      )}: <strong>${audit.performance.score}/100</strong></p>`,
    );
    parts.push(
      findingsHtml(
        audit.performance,
        tx,
        tx("dev.audit.noPerfFindings", "No performance risk detected by static analysis."),
      ),
    );
  }

  // ---------------------------------------------------------------- notes
  if (has("notes")) {
    parts.push(sectionTitle(tx("dev.audit.nav.notes", "Key Notes")));
    const noteColors: Record<string, string> = {
      risk: SEVERITY_COLORS.critical,
      strength: "#059669",
      action: "#d97706",
      info: "#0284c7",
    };
    for (const note of audit.notes) {
      parts.push(`<div class="finding" style="border-left:3px solid ${noteColors[note.kind] ?? "#52525b"}">
        <div><strong>${esc(note.title)}</strong></div>
        <div class="muted">${esc(note.body)}</div>
      </div>`);
    }
  }

  // Single-section exports carry the section name so the suggested PDF
  // filename tells reports apart.
  const sectionSuffix =
    sections.length === 1
      ? ` - ${tx(`dev.audit.nav.${sections[0]}`, sections[0])}`
      : "";
  const title = `${tx("dev.audit.title", "360° Vision")} - ${audit.project.name}${sectionSuffix}`;
  return `<!doctype html>
<html lang="${esc(options.locale)}">
<head>
<meta charset="utf-8">
<title>${esc(title)}</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: #18181b; margin: 32px; font-size: 12px; line-height: 1.5; }
  h1 { font-size: 22px; margin: 0 0 2px; }
  h3 { font-size: 13px; margin: 14px 0 6px; }
  .section-title { font-size: 16px; margin: 26px 0 10px; padding-bottom: 4px;
                   border-bottom: 2px solid #e4e4e7; break-after: avoid; }
  .header-path { font-family: ui-monospace, Menlo, Consolas, monospace; color: #71717a; font-size: 11px; }
  .muted { color: #71717a; }
  .mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; }
  .ok { color: #059669; font-weight: 600; }
  .badge { display: inline-block; border-radius: 9999px; padding: 1px 8px;
           font-size: 10.5px; font-weight: 700; margin: 1px 2px 1px 0; }
  .kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 10px 0; }
  .kpi { border: 1px solid #e4e4e7; border-radius: 10px; padding: 8px 10px; }
  .kpi-label { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.04em; color: #71717a; }
  .kpi-value { font-size: 17px; font-weight: 700; }
  .kpi-hint { font-size: 10px; color: #71717a; }
  .score-row { display: flex; gap: 36px; margin: 12px 0; }
  .score { font-size: 30px; font-weight: 800; }
  .score span { font-size: 14px; color: #a1a1aa; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; margin: 6px 0 12px; }
  th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #f0f0f2; vertical-align: top; }
  th { font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: #71717a; }
  pre { background: #f4f4f5; border-radius: 8px; padding: 8px 10px; overflow-x: hidden;
        white-space: pre-wrap; word-break: break-word;
        font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 10.5px; margin: 6px 0; }
  .finding { border: 1px solid #e4e4e7; border-radius: 10px; padding: 8px 12px; margin: 6px 0;
             break-inside: avoid; }
  .diagram { text-align: center; margin: 10px 0; }
  .diagram svg { max-width: 100%; height: auto; }
  .report-footer { margin-top: 28px; padding-top: 8px; border-top: 1px solid #e4e4e7;
                   font-size: 10px; color: #a1a1aa; }
  @page { size: A4; margin: 14mm 12mm; }
  @media print { body { margin: 0; } }
</style>
</head>
<body>
  <h1>${esc(title)}</h1>
  <div class="header-path">${esc(audit.project.path)}</div>
  <div class="muted">${esc(tx("dev.audit.generatedAt", "Generated"))} ${esc(generated)} · ${(audit.duration_ms / 1000).toFixed(1)} s</div>
  ${parts.join("\n")}
  <div class="report-footer">${esc(
    tx(
      "dev.audit.report.footer",
      "Report generated by local static analysis. Scores are indicators, not certifications.",
    ),
  )}</div>
</body>
</html>`;
}

export { printHtmlDocument as printHtmlReport } from "@/lib/printHtml";
