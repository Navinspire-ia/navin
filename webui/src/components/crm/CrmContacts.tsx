// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useMemo } from "react";

import { CrmAddCard } from "@/components/crm/CrmAddCard";
import { CrmExportMenu } from "@/components/crm/CrmExportMenu";
import { CrmFicheFact, CrmFichePane } from "@/components/crm/CrmFichePane";
import { CrmInsightCard } from "@/components/crm/CrmInsights";
import { CrmListToolbar, type CrmToolbarFacet } from "@/components/crm/CrmListToolbar";
import { CrmMasterDetail } from "@/components/crm/CrmMasterDetail";
import { CrmOutreach } from "@/components/crm/CrmOutreach";
import { CrmPagination } from "@/components/crm/CrmPagination";
import { CrmTimeline } from "@/components/crm/CrmTimeline";
import type { CrmAudit, CrmChannelStatus, CrmInsights, CrmMember, CrmRecord } from "@/lib/api";
import { countryLabel, LEAD_SOURCES, optionList } from "@/lib/crm-catalog";
import { companyName, CONTACT_STATUSES, contactName, displayText } from "@/lib/crm-format";
import {
  countryFilterOptions,
  countLabel,
  ownerFilterOptions,
  sourceFilterOptions,
  tagFilterOptions,
  useCrmFilteredPage,
} from "@/lib/crm-list";
import { cn } from "@/lib/utils";

type Props = {
  contacts: CrmRecord[];
  companies: CrmRecord[];
  opportunities?: CrmRecord[];
  leads?: CrmRecord[];
  members?: CrmMember[];
  busy: boolean;
  openId: string | null;
  timeline: CrmRecord[];
  audit?: CrmAudit[];
  insight: CrmInsights | null;
  channels: { email: CrmChannelStatus; whatsapp: CrmChannelStatus; teams: CrmChannelStatus } | null;
  canWrite: boolean;
  locale?: string;
  tx: (key: string, fallback: string) => string;
  onAdd: () => void;
  onEdit: (row: CrmRecord) => void;
  onDelete: (row: CrmRecord) => void;
  onOpen: (id: string) => void;
  onClose: () => void;
  onOutreach: (body: Record<string, unknown>) => Promise<void>;
  token: string;
  sessionKey: string;
  actor: string;
};

export function CrmContacts({
  contacts,
  companies,
  opportunities = [],
  leads = [],
  members = [],
  openId,
  timeline,
  audit,
  insight,
  channels,
  canWrite,
  locale = "fr-FR",
  tx,
  onAdd,
  onEdit,
  onDelete,
  onOpen,
  onClose,
  onOutreach,
  token,
  sessionKey,
  actor,
}: Props) {
  const extraHaystack = useCallback(
    (row: CrmRecord) => companyName(companies.find((item) => item.id === row.companyId)),
    [companies],
  );
  const { filter, facets, setFilter, setFacet, filtered, slice, page, setPage, pageSize } = useCrmFilteredPage(
    contacts,
    { extraHaystack },
    members,
  );
  const lang = locale.toLowerCase().startsWith("en") ? "en" : "fr";
  const facetsDef = useMemo<CrmToolbarFacet[]>(() => {
    const next: CrmToolbarFacet[] = [
      {
        key: "status",
        label: tx("crm.status", "Statut"),
        options: optionList(CONTACT_STATUSES, (value) => tx(`crm.statuses.${value}`, value)),
      },
    ];
    const owners = ownerFilterOptions(contacts, members);
    if (owners.length) next.push({ key: "owner", label: tx("crm.owner", "Proprietaire"), options: owners });
    const countries = countryFilterOptions(contacts, lang);
    if (countries.length) next.push({ key: "country", label: tx("crm.country", "Pays"), options: countries });
    next.push({
      key: "source",
      label: tx("crm.source", "Source"),
      options: sourceFilterOptions(contacts, LEAD_SOURCES, (value) => tx(`crm.sources.${value}`, value)),
    });
    const tags = tagFilterOptions(contacts);
    if (tags.length) next.push({ key: "tag", label: tx("crm.tag", "Tag"), options: tags });
    return next;
  }, [contacts, lang, members, tx]);

  const open = contacts.find((row) => row.id === openId);
  const company = companies.find((row) => row.id === open?.companyId);
  const emptyFiltered = contacts.length > 0 && filtered.length === 0;

  return (
    <CrmMasterDetail
      toolbar={
        <CrmListToolbar
          query={filter}
          onQueryChange={setFilter}
          facets={facetsDef}
          facetValues={facets}
          onFacetChange={setFacet}
          countLabel={countLabel(
            filtered.length,
            tx("crm.noun.contact", "contact"),
            tx("crm.noun.contacts", "contacts"),
          )}
          tx={tx}
          actions={
            <CrmExportMenu
              kind="contacts"
              rows={filtered}
              companies={companies}
              locale={locale}
              tx={tx}
            />
          }
        />
      }
      list={
        <>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {canWrite ? (
              <div className="pb-3">
                <CrmAddCard label={tx("crm.addContactCard", "+ Ajouter un contact")} onClick={onAdd} compact />
              </div>
            ) : null}
            <ul className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/60">
              {contacts.length === 0 ? (
                <li className="px-3 py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyContacts", "Ajoute un contact, ou demande-le a Navin.")}
                </li>
              ) : emptyFiltered ? (
                <li className="px-3 py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyFilter", "Aucun resultat pour ces filtres.")}
                </li>
              ) : (
                slice.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      onClick={() => onOpen(row.id)}
                      className={cn(
                        "flex min-h-10 w-full cursor-pointer items-center justify-between px-3 py-2 text-left text-[13px] hover:bg-muted/40",
                        openId === row.id && "bg-muted/50",
                      )}
                    >
                      <span className="font-medium">{contactName(row)}</span>
                      <span className="text-[11px] text-muted-foreground">
                        {displayText(row.email) || displayText(row.phone)}
                      </span>
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
          <CrmPagination page={page} pageSize={pageSize} total={filtered.length} onPage={setPage} tx={tx} />
        </>
      }
      fiche={
        <CrmFichePane
          open={Boolean(open)}
          title={open ? contactName(open) : ""}
          emptyLabel={tx("crm.pickContact", "Choisis un contact")}
          canWrite={canWrite}
          tx={tx}
          onClose={onClose}
          onEdit={open ? () => onEdit(open) : undefined}
          onDelete={open ? () => onDelete(open) : undefined}
          extraActions={
            canWrite && open ? (
              <CrmOutreach
                token={token}
                sessionKey={sessionKey}
                actor={actor}
                contact={open}
                channels={channels}
                tx={tx}
                onOutreach={onOutreach}
              />
            ) : null
          }
        >
          {open ? (
            <>
              <dl className="grid gap-2 rounded-xl border border-border/60 p-3 text-[12px] sm:grid-cols-2">
                <CrmFicheFact label={tx("crm.title", "Fonction")} value={open.title} />
                <CrmFicheFact label={tx("crm.companyName", "Entreprise")} value={companyName(company)} />
                <CrmFicheFact label={tx("crm.email", "Email")} value={open.email} />
                <CrmFicheFact label={tx("crm.phone", "Telephone")} value={open.phone} />
                <CrmFicheFact
                  label={tx("crm.country", "Pays")}
                  value={open.country ? countryLabel(String(open.country)) : ""}
                />
                <CrmFicheFact label={tx("crm.whatsapp", "WhatsApp")} value={open.whatsapp} />
                <CrmFicheFact label={tx("crm.linkedin", "LinkedIn")} value={open.linkedin} />
                <CrmFicheFact label={tx("crm.source", "Source")} value={open.source} />
                <CrmFicheFact
                  label={tx("crm.status", "Statut")}
                  value={open.status ? tx(`crm.statuses.${String(open.status)}`, String(open.status)) : ""}
                />
              </dl>
              <CrmInsightCard insight={insight} tx={tx} />
              <CrmTimeline
                timeline={timeline}
                audit={audit}
                contacts={contacts}
                companies={companies}
                opportunities={opportunities}
                leads={leads}
                tx={tx}
              />
            </>
          ) : null}
        </CrmFichePane>
      }
    />
  );
}
