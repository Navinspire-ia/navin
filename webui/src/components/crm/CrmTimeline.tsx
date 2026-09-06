import type { CrmAudit, CrmRecord } from "@/lib/api";
import {
  companyName,
  contactName,
  displayText,
  leadName,
  linkedName,
  opportunityName,
} from "@/lib/crm-format";

type Tx = (key: string, fallback: string) => string;

type Props = {
  timeline: CrmRecord[];
  audit?: CrmAudit[];
  contacts?: CrmRecord[];
  companies?: CrmRecord[];
  opportunities?: CrmRecord[];
  leads?: CrmRecord[];
  tx: Tx;
};

const KIND_LABEL: Record<string, [string, string]> = {
  contacts: ["crm.tabs.contacts", "Contact"],
  companies: ["crm.tabs.companies", "Entreprise"],
  leads: ["crm.tabs.leads", "Lead"],
  opportunities: ["crm.tabs.opportunities", "Opportunite"],
  activities: ["crm.tabs.activities", "Activite"],
  products: ["crm.tabs.products", "Produit"],
};

const ACTION_LABEL: Record<string, [string, string]> = {
  create: ["crm.audit.create", "cree"],
  update: ["crm.audit.update", "modifie"],
  delete: ["crm.audit.delete", "supprime"],
  stage: ["crm.audit.stage", "etape changee"],
  convert: ["crm.audit.convert", "converti"],
  invite: ["crm.audit.invite", "invitation"],
  accept: ["crm.audit.accept", "invitation acceptee"],
  decline: ["crm.audit.decline", "invitation refusee"],
  role: ["crm.audit.role", "role mis a jour"],
  kick: ["crm.audit.kick", "membre retire"],
  settings: ["crm.audit.settings", "parametres enregistres"],
};

function looksLikeId(value: string) {
  return /^[a-z]{1,6}-[a-f0-9]{6,}$/i.test(value) || /^AUDIT\s+/i.test(value);
}

function nameFromDetail(detail: unknown): string {
  const text = displayText(detail);
  if (!text) return "";
  if (text.startsWith("{") || text.startsWith("[")) {
    try {
      const parsed = JSON.parse(text) as Record<string, unknown>;
      const nested = parsed.after && typeof parsed.after === "object" ? (parsed.after as Record<string, unknown>) : parsed;
      const full = `${displayText(nested.firstName)} ${displayText(nested.lastName)}`.trim();
      return (
        displayText(nested.name) ||
        full ||
        displayText(nested.title) ||
        displayText(parsed.name) ||
        ""
      );
    } catch {
      return "";
    }
  }
  if (looksLikeId(text)) return "";
  return text;
}

function parseAuditTitle(raw: string): { action: string; kind: string } | null {
  const text = displayText(raw);
  if (!text) return null;
  const match = text.match(/^(create|update|delete|stage|convert|invite|accept|decline|role|kick|settings)\s+([a-z_]+)$/i);
  if (!match) return null;
  return { action: match[1].toLowerCase(), kind: match[2].toLowerCase() };
}

function humanAuditTitle(action: string, kind: string, detail: unknown, tx: Tx) {
  const noun = KIND_LABEL[kind] ? tx(KIND_LABEL[kind][0], KIND_LABEL[kind][1]) : displayText(kind);
  const verb = ACTION_LABEL[action] ? tx(ACTION_LABEL[action][0], ACTION_LABEL[action][1]) : displayText(action);
  const name = nameFromDetail(detail);
  if (action === "create" && noun) {
    return name ? `${noun} cree - ${name}` : `${noun} cree`;
  }
  if (action === "update" && noun) {
    return name ? `${noun} modifie - ${name}` : `${noun} modifie`;
  }
  if (action === "delete" && noun) {
    return name ? `${noun} supprime - ${name}` : `${noun} supprime`;
  }
  if (action === "stage") {
    return name ? `${tx("crm.audit.stage", "Etape changee")} - ${name}` : tx("crm.audit.stage", "Etape changee");
  }
  if (action === "convert") {
    return name ? `${tx("crm.audit.convert", "Lead converti")} - ${name}` : tx("crm.audit.convert", "Lead converti");
  }
  const title = [noun, verb].filter(Boolean).join(" ");
  return name ? `${title} - ${name}` : title;
}

function LinkedFact({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <p className="text-[11px] text-muted-foreground">
      {label}: {value}
    </p>
  );
}

export function CrmTimeline({
  timeline,
  audit,
  contacts = [],
  companies = [],
  opportunities = [],
  leads = [],
  tx,
}: Props) {
  const seen = new Set<string>();
  const rows: Array<{
    id: string;
    title: string;
    kind: string;
    at: number;
    body: string;
    contact: string;
    company: string;
    opportunity: string;
    lead: string;
  }> = [];

  const push = (row: {
    id: string;
    title: string;
    kind: string;
    at: number;
    body: string;
    contactId?: unknown;
    companyId?: unknown;
    opportunityId?: unknown;
    leadId?: unknown;
    action?: string;
    auditKind?: string;
    detail?: unknown;
    audit?: boolean;
  }) => {
    if (!row.id || seen.has(row.id)) return;
    seen.add(row.id);
    const parsed = parseAuditTitle(row.title);
    const isAudit = row.kind === "audit" || row.audit === true || Boolean(parsed);
    const action = displayText(row.action) || parsed?.action || "";
    const auditKind = displayText(row.auditKind) || parsed?.kind || "";
    const title = isAudit && action
      ? humanAuditTitle(action, auditKind, row.detail ?? row.body, tx)
      : displayText(row.title);
    const extracted = isAudit ? nameFromDetail(row.detail ?? row.body) : "";
    const body = isAudit
      ? extracted && !title.includes(extracted) ? extracted : ""
      : displayText(row.body);
    if (!title && !body) return;
    rows.push({
      id: row.id,
      title: title || body,
      kind: isAudit ? "" : displayText(row.kind),
      at: row.at,
      body: title ? body : "",
      contact: linkedName(contacts, row.contactId, contactName),
      company: linkedName(companies, row.companyId, companyName),
      opportunity: linkedName(opportunities, row.opportunityId, opportunityName),
      lead: linkedName(leads, row.leadId, leadName),
    });
  };

  for (const row of timeline) {
    push({
      id: row.id,
      title: displayText(row.title),
      kind: displayText(row.kind),
      at: Number(row.at || row.createdAt || 0),
      body: displayText(row.body),
      contactId: row.contactId,
      companyId: row.companyId,
      opportunityId: row.opportunityId,
      leadId: row.leadId,
      action: displayText(row.action),
      auditKind: displayText(row.auditKind || row.recordKind),
      detail: row.detail ?? row.body,
      audit: Boolean(row.audit),
    });
  }
  for (const row of audit || []) {
    push({
      id: row.id,
      title: `${row.action} ${row.kind}`.trim(),
      kind: "audit",
      at: row.createdAt,
      body: row.detail,
      action: row.action,
      auditKind: row.kind,
      detail: row.detail,
      audit: true,
    });
  }

  rows.sort((a, b) => b.at - a.at);

  return (
    <div>
      <h3 className="text-[12px] font-semibold">{tx("crm.history", "Historique")}</h3>
      <ol className="mt-2 space-y-2 text-[12px]">
        {rows.length === 0 ? (
          <li className="text-muted-foreground">{tx("crm.noTimeline", "Pas encore d'historique.")}</li>
        ) : (
          rows.slice(0, 80).map((row) => (
            <li key={row.id} className="border-l-2 border-violet-400/50 pl-2">
              <div className="font-medium">{row.title}</div>
              {row.kind ? (
                <div className="text-[11px] uppercase text-muted-foreground">
                  {tx(`crm.kinds.${row.kind}`, row.kind)}
                </div>
              ) : null}
              {row.body ? <p className="text-muted-foreground">{row.body}</p> : null}
              <LinkedFact label={tx("crm.linkContact", "Contact")} value={row.contact} />
              <LinkedFact label={tx("crm.companyName", "Entreprise")} value={row.company} />
              <LinkedFact label={tx("crm.linkDeal", "Opportunite")} value={row.opportunity} />
              <LinkedFact label={tx("crm.linkLead", "Lead")} value={row.lead} />
            </li>
          ))
        )}
      </ol>
    </div>
  );
}
