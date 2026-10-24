// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Briefcase,
  Download,
  FileSpreadsheet,
  ListPlus,
  Loader2,
  Pencil,
  Plus,
  Search,
  Sparkles,
  Target,
  Trash2,
  Upload,
  Users,
} from "lucide-react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  addToList,
  commitImport,
  deleteContacts,
  deleteList,
  downloadText,
  exportContacts,
  fileToBase64,
  importFromLeads,
  listContacts,
  previewImport,
  removeFromList,
  saveContact,
  saveList,
} from "@/lib/campaigns-api";
import type { Contact, ContactList, ContactStatus, ImportPreview, ImportResult } from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import { CrmAudiencePicker } from "./CrmAudiencePicker";
import { EmailStatusBadge, EnrichDialog } from "./ProspectsView";
import {
  Chip,
  contactStatusLabel,
  EmptyState,
  ErrorBanner,
  errorText,
  Field,
  formatDateTime,
  Loading,
  Notice,
  Pager,
  SELECT_CLASS,
  type Tx,
} from "./campaigns-ui";

const PAGE = 50;

export const CONTACT_FIELDS = [
  "email",
  "phone",
  "first_name",
  "last_name",
  "company",
  "title",
  "city",
  "country",
  "tags",
] as const;

export function ContactsView({
  token,
  sessionKey,
  tx,
  lists,
  onListsChanged,
  initialListId = "",
}: {
  token: string;
  sessionKey: string | null;
  tx: Tx;
  lists: ContactList[];
  onListsChanged: () => Promise<void> | void;
  /** Open on this list (after a prospect import). */
  initialListId?: string;
}) {
  const [listId, setListId] = useState(initialListId);
  const [enrichOpen, setEnrichOpen] = useState(false);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<ContactStatus | "">("");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<Contact[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<Partial<Contact> | null>(null);
  const [importOpen, setImportOpen] = useState<"" | "file" | "crm">("");
  const [listDialog, setListDialog] = useState<Partial<ContactList> | null>(null);
  const [pendingDelete, setPendingDelete] = useState<"" | "contacts" | "list">("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listContacts(token, { q: q.trim(), list_id: listId, status, offset, limit: PAGE });
      setRows(page.items);
      setTotal(page.total);
      setError("");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [listId, offset, q, status, token]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 200);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    setOffset(0);
    setSelected(new Set());
  }, [listId, q, status]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const run = async (work: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    setError("");
    try {
      await work();
      if (done) setNotice(done);
      await load();
      await onListsChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const currentList = lists.find((list) => list.id === listId) ?? null;
  const allChecked = rows.length > 0 && rows.every((row) => selected.has(row.id));
  const importDone = (result: ImportResult) => {
    setImportOpen("");
    if (result.list_id) setListId(result.list_id);
    setNotice(
      tx("import.done", "{{created}} added, {{updated}} updated, {{skipped}} skipped.", {
        created: result.created,
        updated: result.updated,
        skipped: result.skipped,
      }),
    );
    void load();
    void onListsChanged();
  };

  return (
    <div className="flex h-full min-h-0">
      <aside className="hidden w-56 shrink-0 flex-col border-e border-border/60 bg-muted/10 md:flex">
        <div className="flex items-center justify-between px-3 pb-1 pt-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{tx("lists.title", "Lists")}</p>
          <button
            type="button"
            onClick={() => setListDialog({ name: "", description: "" })}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label={tx("lists.new", "New list")}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
        <nav className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          <ListButton active={!listId} onClick={() => setListId("")} label={tx("lists.all", "All contacts")} count={null} />
          {lists.map((list) => (
            <ListButton key={list.id} active={list.id === listId} onClick={() => setListId(list.id)} label={list.name} count={list.count} />
          ))}
        </nav>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="space-y-3 border-b border-border/60 p-3 md:p-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-[16px] font-semibold text-foreground">{currentList?.name ?? tx("lists.all", "All contacts")}</h2>
              <p className="text-[12px] text-muted-foreground">{tx("contacts.count", "{{count}} contacts", { count: total })}</p>
            </div>
            {currentList ? (
              <>
                <Button size="sm" variant="ghost" className="h-8 rounded-full" onClick={() => setListDialog(currentList)}>
                  <Pencil className="me-1 h-3.5 w-3.5" aria-hidden />
                  {tx("lists.rename", "Rename")}
                </Button>
                <Button size="sm" variant="ghost" className="h-8 rounded-full text-[#c2410c]" onClick={() => setPendingDelete("list")}>
                  <Trash2 className="me-1 h-3.5 w-3.5" aria-hidden />
                  {tx("lists.delete", "Delete list")}
                </Button>
              </>
            ) : null}
            <Button size="sm" variant="outline" className="h-8 rounded-full" onClick={() => setImportOpen("file")}>
              <Upload className="me-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("import.file", "Import CSV / Excel")}
            </Button>
            <Button size="sm" variant="outline" className="h-8 rounded-full" onClick={() => setImportOpen("crm")}>
              <Briefcase className="me-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("crm.import", "Import from CRM")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-8 rounded-full"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const result = await importFromLeads(token, listId ? { list_id: listId } : { new_list_name: tx("import.leadsList", "Leads desk") });
                  importDone(result);
                })
              }
            >
              <Target className="me-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("import.leads", "Import from Leads")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-8 rounded-full"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const file = await exportContacts(token, listId);
                  downloadText(file.filename, file.csv);
                })
              }
            >
              <Download className="me-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("contacts.export", "Export CSV")}
            </Button>
            <Button size="sm" className="h-8 rounded-full" onClick={() => setEditing({ status: "subscribed", tags: [], attrs: {}, list_ids: listId ? [listId] : [] })}>
              <Plus className="me-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("contacts.add", "Add contact")}
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[12rem] flex-1">
              <Search className="pointer-events-none absolute start-2.5 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden />
              <Input value={q} onChange={(event) => setQ(event.target.value)} placeholder={tx("contacts.search", "Search name, email, company, phone...")} className="ps-8" />
            </div>
            <select className={cn(SELECT_CLASS, "w-44")} value={status} onChange={(event) => setStatus(event.target.value as ContactStatus | "")} aria-label={tx("contacts.status", "Status")}>
              <option value="">{tx("contacts.anyStatus", "Any status")}</option>
              {(["subscribed", "unsubscribed", "bounced"] as ContactStatus[]).map((value) => (
                <option key={value} value={value}>
                  {contactStatusLabel(tx, value)}
                </option>
              ))}
            </select>
            <select className={cn(SELECT_CLASS, "w-44 md:hidden")} value={listId} onChange={(event) => setListId(event.target.value)} aria-label={tx("lists.title", "Lists")}>
              <option value="">{tx("lists.all", "All contacts")}</option>
              {lists.map((list) => (
                <option key={list.id} value={list.id}>
                  {list.name}
                </option>
              ))}
            </select>
          </div>
          {selected.size > 0 ? (
            <div className="flex flex-wrap items-center gap-2 rounded-xl bg-muted/40 px-3 py-2 text-[12.5px]">
              <span className="font-medium">{tx("contacts.selected", "{{count}} selected", { count: selected.size })}</span>
              <select
                className={cn(SELECT_CLASS, "h-8 w-48")}
                value=""
                onChange={(event) => {
                  const target = event.target.value;
                  if (!target) return;
                  void run(() => addToList(token, target, [...selected]), tx("contacts.added", "Added to the list."));
                }}
                aria-label={tx("contacts.addToList", "Add to list")}
              >
                <option value="">{tx("contacts.addToList", "Add to list")}</option>
                {lists.map((list) => (
                  <option key={list.id} value={list.id}>
                    {list.name}
                  </option>
                ))}
              </select>
              {listId ? (
                <Button size="sm" variant="ghost" className="h-8" onClick={() => void run(() => removeFromList(token, listId, [...selected]), tx("contacts.removed", "Removed from the list."))}>
                  <ListPlus className="me-1 h-3.5 w-3.5 rotate-45" aria-hidden />
                  {tx("contacts.removeFromList", "Remove from list")}
                </Button>
              ) : null}
              <Button size="sm" variant="ghost" className="h-8" onClick={() => setEnrichOpen(true)} data-testid="contacts-enrich">
                <Sparkles className="me-1 h-3.5 w-3.5" aria-hidden />
                {tx("enrich.action", "Enrich")}
              </Button>
              <Button size="sm" variant="ghost" className="h-8 text-[#c2410c]" onClick={() => setPendingDelete("contacts")}>
                <Trash2 className="me-1 h-3.5 w-3.5" aria-hidden />
                {tx("contacts.delete", "Delete")}
              </Button>
            </div>
          ) : null}
          <ErrorBanner message={error} onDismiss={() => setError("")} />
          <Notice message={notice} />
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {loading && !rows.length ? (
            <Loading label={tx("loading", "Loading...")} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<Users className="h-5 w-5" aria-hidden />}
              title={tx("contacts.empty", "No contact here yet")}
              body={tx("contacts.emptyBody", "Import a CSV or Excel file, pull people from your CRM or the Leads desk, or add them one by one.")}
              action={
                <Button size="sm" className="rounded-full" onClick={() => setImportOpen("file")}>
                  <FileSpreadsheet className="me-1.5 h-4 w-4" aria-hidden />
                  {tx("import.file", "Import CSV / Excel")}
                </Button>
              }
            />
          ) : (
            <table className="w-full text-[12.5px]">
              <thead className="sticky top-0 z-10 bg-background/95 text-start text-[11.5px] text-muted-foreground backdrop-blur">
                <tr className="border-b border-border/60">
                  <th className="w-8 px-3 py-2">
                    <input
                      type="checkbox"
                      checked={allChecked}
                      aria-label={tx("contacts.selectAll", "Select all")}
                      onChange={() => setSelected(allChecked ? new Set() : new Set(rows.map((row) => row.id)))}
                    />
                  </th>
                  <th className="px-2 py-2 text-start font-medium">{tx("contacts.name", "Name")}</th>
                  <th className="px-2 py-2 text-start font-medium">{tx("field.email", "Email")}</th>
                  <th className="hidden px-2 py-2 text-start font-medium lg:table-cell">{tx("field.phone", "Phone")}</th>
                  <th className="hidden px-2 py-2 text-start font-medium md:table-cell">{tx("field.company", "Company")}</th>
                  <th className="hidden px-2 py-2 text-start font-medium xl:table-cell">{tx("field.tags", "Tags")}</th>
                  <th className="px-2 py-2 text-start font-medium">{tx("contacts.status", "Status")}</th>
                  <th className="w-10" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b border-border/40 hover:bg-muted/25">
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        checked={selected.has(row.id)}
                        aria-label={tx("contacts.select", "Select {{name}}", { name: row.email || row.phone })}
                        onChange={() =>
                          setSelected((prev) => {
                            const next = new Set(prev);
                            if (next.has(row.id)) next.delete(row.id);
                            else next.add(row.id);
                            return next;
                          })
                        }
                      />
                    </td>
                    <td className="max-w-[12rem] truncate px-2 py-2 font-medium text-foreground">
                      {[row.first_name, row.last_name].filter(Boolean).join(" ") || "-"}
                      {row.title ? <span className="block truncate text-[11px] font-normal text-muted-foreground">{row.title}</span> : null}
                    </td>
                    <td className="max-w-[14rem] px-2 py-2">
                      <span className="block truncate">{row.email}</span>
                      {row.email ? <EmailStatusBadge status={row.attrs?.email_status} tx={tx} /> : null}
                    </td>
                    <td className="hidden px-2 py-2 lg:table-cell">{row.phone}</td>
                    <td className="hidden max-w-[10rem] truncate px-2 py-2 md:table-cell">{row.company}</td>
                    <td className="hidden px-2 py-2 xl:table-cell">
                      <span className="flex flex-wrap gap-1">
                        {row.tags.slice(0, 3).map((tag) => (
                          <Chip key={tag}>{tag}</Chip>
                        ))}
                      </span>
                    </td>
                    <td className="px-2 py-2">
                      <Chip tone={row.status === "subscribed" ? "green" : row.status === "bounced" ? "red" : "neutral"}>{contactStatusLabel(tx, row.status)}</Chip>
                    </td>
                    <td className="px-2 py-2">
                      <button type="button" onClick={() => setEditing(row)} className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={tx("contacts.edit", "Edit")}>
                        <Pencil className="h-3.5 w-3.5" aria-hidden />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Pager offset={offset} limit={PAGE} total={total} onChange={setOffset} tx={tx} />
        </div>
      </section>

      {editing ? (
        <ContactDialog
          contact={editing}
          lists={lists}
          tx={tx}
          onClose={() => setEditing(null)}
          onSave={async (contact) => {
            await saveContact(token, contact);
            setEditing(null);
            setNotice(tx("contacts.saved", "Contact saved."));
            await load();
            await onListsChanged();
          }}
        />
      ) : null}

      <Dialog open={importOpen !== ""} onOpenChange={(open) => (!open ? setImportOpen("") : undefined)}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{importOpen === "crm" ? tx("crm.import", "Import from CRM") : tx("import.file", "Import CSV / Excel")}</DialogTitle>
            <DialogDescription>
              {importOpen === "crm"
                ? tx("crm.importHelp", "Pick people from this project's CRM with filters, then add them to a list.")
                : tx("import.help", "Columns are matched automatically; check the mapping before importing.")}
            </DialogDescription>
          </DialogHeader>
          {importOpen === "crm" ? (
            <CrmAudiencePicker token={token} sessionKey={sessionKey} lists={lists} tx={tx} onImported={importDone} />
          ) : importOpen === "file" ? (
            <ImportWizard token={token} lists={lists} defaultListId={listId} tx={tx} onDone={importDone} />
          ) : null}
        </DialogContent>
      </Dialog>

      {listDialog ? (
        <ListDialog
          list={listDialog}
          tx={tx}
          onClose={() => setListDialog(null)}
          onSave={async (list) => {
            const saved = await saveList(token, list);
            setListDialog(null);
            await onListsChanged();
            setListId(saved.id);
          }}
        />
      ) : null}

      <EnrichDialog
        token={token}
        tx={tx}
        ids={[...selected]}
        open={enrichOpen}
        onClose={() => setEnrichOpen(false)}
        onDone={() => void load()}
      />
      <ConfirmDialog
        open={pendingDelete !== ""}
        title={pendingDelete === "list" ? tx("lists.deleteTitle", "Delete this list?") : tx("contacts.deleteTitle", "Delete {{count}} contacts?", { count: selected.size })}
        description={
          pendingDelete === "list"
            ? tx("lists.deleteHelp", "The contacts stay in your contacts; only the list goes.")
            : tx("contacts.deleteHelp", "They are removed from every list and future campaigns.")
        }
        confirmLabel={tx("contacts.delete", "Delete")}
        onCancel={() => setPendingDelete("")}
        onConfirm={() => {
          const kind = pendingDelete;
          setPendingDelete("");
          if (kind === "list" && listId) {
            void run(async () => {
              await deleteList(token, listId);
              setListId("");
            });
          } else if (kind === "contacts") {
            void run(async () => {
              await deleteContacts(token, [...selected]);
              setSelected(new Set());
            });
          }
        }}
      />
    </div>
  );
}

function ListButton({ active, onClick, label, count }: { active: boolean; onClick: () => void; label: string; count: number | null }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex min-h-9 w-full items-center gap-2 rounded-lg px-2 text-start text-[13px] transition-colors",
        active ? "bg-sidebar-accent font-medium" : "hover:bg-muted/60",
      )}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {count !== null ? <span className="text-[11px] tabular-nums text-muted-foreground">{count}</span> : null}
    </button>
  );
}

function ContactDialog({
  contact,
  lists,
  tx,
  onClose,
  onSave,
}: {
  contact: Partial<Contact>;
  lists: ContactList[];
  tx: Tx;
  onClose: () => void;
  onSave: (contact: Partial<Contact>) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Partial<Contact>>(contact);
  const [tags, setTags] = useState((contact.tags ?? []).join(", "));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<Contact>) => setDraft((prev) => ({ ...prev, ...patch }));
  const text = (key: keyof Contact, label: string, type = "text") => (
    <Field label={label}>
      <Input type={type} value={String(draft[key] ?? "")} onChange={(event) => set({ [key]: event.target.value } as Partial<Contact>)} />
    </Field>
  );
  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{contact.id ? tx("contacts.editTitle", "Edit contact") : tx("contacts.add", "Add contact")}</DialogTitle>
          <DialogDescription>{tx("contacts.dialogHelp", "Email for email campaigns, phone in international format (+33...) for WhatsApp.")}</DialogDescription>
        </DialogHeader>
        <ErrorBanner message={error} onDismiss={() => setError("")} />
        <div className="grid gap-3 sm:grid-cols-2">
          {text("first_name", tx("field.first_name", "First name"))}
          {text("last_name", tx("field.last_name", "Last name"))}
          {text("email", tx("field.email", "Email"), "email")}
          {text("phone", tx("field.phone", "Phone"), "tel")}
          {text("company", tx("field.company", "Company"))}
          {text("title", tx("field.title", "Job title"))}
          {text("city", tx("field.city", "City"))}
          {text("country", tx("field.country", "Country"))}
          <Field label={tx("field.tags", "Tags")} hint={tx("contacts.tagsHint", "Comma separated")}>
            <Input value={tags} onChange={(event) => setTags(event.target.value)} />
          </Field>
          <Field label={tx("contacts.status", "Status")}>
            <select className={SELECT_CLASS} value={draft.status ?? "subscribed"} onChange={(event) => set({ status: event.target.value as ContactStatus })}>
              {(["subscribed", "unsubscribed", "bounced"] as ContactStatus[]).map((value) => (
                <option key={value} value={value}>
                  {contactStatusLabel(tx, value)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {lists.length ? (
          <div>
            <p className="mb-1.5 text-[12px] font-medium text-foreground/85">{tx("lists.title", "Lists")}</p>
            <div className="flex flex-wrap gap-1.5">
              {lists.map((list) => {
                const on = (draft.list_ids ?? []).includes(list.id);
                return (
                  <button
                    key={list.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() => set({ list_ids: on ? (draft.list_ids ?? []).filter((id) => id !== list.id) : [...(draft.list_ids ?? []), list.id] })}
                    className={cn("rounded-full border px-2.5 py-0.5 text-[12px]", on ? "border-[#1d6af0] bg-[#1d6af0] text-white" : "border-border/70 hover:bg-muted/60")}
                  >
                    {list.name}
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}
        {contact.created_at ? (
          <p className="text-[11.5px] text-muted-foreground">
            {tx("contacts.meta", "Source: {{source}}, added {{when}}", { source: contact.source || "-", when: formatDateTime(contact.created_at) })}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" className="rounded-full" onClick={onClose}>
            {tx("cancel", "Cancel")}
          </Button>
          <Button
            className="rounded-full"
            disabled={busy || !(draft.email || draft.phone)}
            onClick={() => {
              setBusy(true);
              setError("");
              void onSave({ ...draft, tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean) })
                .catch((err) => setError(errorText(err)))
                .finally(() => setBusy(false));
            }}
          >
            {busy ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
            {tx("save", "Save")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ListDialog({
  list,
  tx,
  onClose,
  onSave,
}: {
  list: Partial<ContactList>;
  tx: Tx;
  onClose: () => void;
  onSave: (list: Partial<ContactList>) => Promise<void>;
}) {
  const [name, setName] = useState(list.name ?? "");
  const [description, setDescription] = useState(list.description ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{list.id ? tx("lists.renameTitle", "Rename list") : tx("lists.new", "New list")}</DialogTitle>
        </DialogHeader>
        <ErrorBanner message={error} />
        <Field label={tx("lists.name", "Name")}>
          <Input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} autoFocus />
        </Field>
        <Field label={tx("lists.description", "Description")}>
          <Input value={description} maxLength={200} onChange={(event) => setDescription(event.target.value)} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" className="rounded-full" onClick={onClose}>
            {tx("cancel", "Cancel")}
          </Button>
          <Button
            className="rounded-full"
            disabled={busy || !name.trim()}
            onClick={() => {
              setBusy(true);
              void onSave({ ...list, name: name.trim(), description })
                .catch((err) => setError(errorText(err)))
                .finally(() => setBusy(false));
            }}
          >
            {tx("save", "Save")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ImportWizard({
  token,
  lists,
  defaultListId,
  tx,
  onDone,
}: {
  token: string;
  lists: ContactList[];
  defaultListId: string;
  tx: Tx;
  onDone: (result: ImportResult) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [target, setTarget] = useState(defaultListId || "__new");
  const [newName, setNewName] = useState("");
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);

  const readFile = async (file: File | undefined) => {
    if (!file) return;
    if (!/\.(csv|xlsx|xls|txt)$/i.test(file.name)) {
      setError(tx("import.badType", "Choose a .csv or .xlsx file."));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const data = await fileToBase64(file);
      const next = await previewImport(token, file.name, data);
      setPreview(next);
      setMapping(next.mapping);
      setNewName(file.name.replace(/\.[^.]+$/, ""));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const hasAddress = Object.values(mapping).some((field) => field === "email" || field === "phone");
  const fieldOptions = [
    ...CONTACT_FIELDS.map((field) => ({ value: field, label: tx(`field.${field}`, field) })),
    { value: "ignore", label: tx("import.ignore", "Ignore") },
  ];

  if (!preview) {
    return (
      <div className="space-y-3">
        <ErrorBanner message={error} onDismiss={() => setError("")} />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            void readFile(event.dataTransfer.files[0]);
          }}
          className={cn(
            "flex w-full flex-col items-center gap-2 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors",
            dragging ? "border-[#1d6af0] bg-[#1d6af0]/[0.05]" : "border-border/80 hover:bg-muted/30",
          )}
          data-testid="import-drop"
        >
          {busy ? <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-hidden /> : <FileSpreadsheet className="h-7 w-7 text-muted-foreground" aria-hidden />}
          <span className="text-[13.5px] font-medium text-foreground">{tx("import.drop", "Drop a CSV or Excel file here, or click to choose")}</span>
          <span className="text-[12px] text-muted-foreground">{tx("import.dropHelp", "First row = column names. Email and/or phone columns are required.")}</span>
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,.xls,.txt,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          className="hidden"
          data-testid="import-file"
          onChange={(event) => void readFile(event.target.files?.[0])}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      <p className="text-[12.5px] text-muted-foreground">
        {tx("import.summary", "{{file}}: {{rows}} rows, {{columns}} columns", { file: preview.filename, rows: preview.rows, columns: preview.columns.length })}
      </p>
      <div className="overflow-x-auto rounded-xl border border-border/60">
        <table className="w-full text-[12px]">
          <thead className="bg-muted/30">
            <tr>
              {preview.columns.map((column) => (
                <th key={column} className="min-w-[9rem] px-2 py-2 text-start align-top font-medium">
                  <span className="mb-1 block truncate text-foreground">{column}</span>
                  <select
                    className={cn(SELECT_CLASS, "h-8 text-[12px]")}
                    value={mapping[column] ?? "ignore"}
                    aria-label={tx("import.mapFor", "Field for {{column}}", { column })}
                    onChange={(event) => setMapping((prev) => ({ ...prev, [column]: event.target.value }))}
                  >
                    {fieldOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                    {(mapping[column] ?? "").startsWith("attrs.") ? <option value={mapping[column]}>{mapping[column]}</option> : null}
                    <option value={`attrs.${column.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "")}`}>
                      {tx("import.keepAsAttr", "Keep as custom field")}
                    </option>
                  </select>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.sample.slice(0, 5).map((row, index) => (
              <tr key={index} className="border-t border-border/40">
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} className="max-w-[12rem] truncate px-2 py-1.5 text-muted-foreground">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!hasAddress ? <ErrorBanner message={tx("import.needAddress", "Map at least one column to Email or Phone.")} /> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={tx("import.target", "Add to list")}>
          <select className={SELECT_CLASS} value={target} onChange={(event) => setTarget(event.target.value)}>
            <option value="__new">{tx("import.newList", "New list")}</option>
            {lists.map((list) => (
              <option key={list.id} value={list.id}>
                {list.name}
              </option>
            ))}
          </select>
        </Field>
        {target === "__new" ? (
          <Field label={tx("import.newListName", "New list name")}>
            <Input value={newName} maxLength={80} onChange={(event) => setNewName(event.target.value)} />
          </Field>
        ) : null}
        <Field label={tx("import.tags", "Tags (comma separated)")} className="sm:col-span-2">
          <Input value={tags} onChange={(event) => setTags(event.target.value)} />
        </Field>
      </div>
      <div className="flex justify-between gap-2">
        <Button variant="ghost" className="rounded-full" onClick={() => setPreview(null)}>
          {tx("import.another", "Choose another file")}
        </Button>
        <Button
          className="rounded-full"
          disabled={busy || !hasAddress || (target === "__new" && !newName.trim())}
          onClick={() => {
            setBusy(true);
            setError("");
            void commitImport(token, {
              import_id: preview.import_id,
              mapping,
              ...(target === "__new" ? { new_list_name: newName.trim() } : { list_id: target }),
              tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean),
            })
              .then(onDone)
              .catch((err) => setError(errorText(err)))
              .finally(() => setBusy(false));
          }}
        >
          {busy ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
          {tx("import.commit", "Import {{rows}} rows", { rows: preview.rows })}
        </Button>
      </div>
    </div>
  );
}
