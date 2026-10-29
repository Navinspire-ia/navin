// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useContext, useState, useSyncExternalStore, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ChevronRight, CircleAlert, CircleCheck, FilePen, Folder, FolderOpen, Loader2, Users } from "lucide-react";
import { useTranslation } from "react-i18next";

import { JournalRow } from "@/components/thread/activity/ActivityJournal";
import { StateBadge, SubagentJournalContext, subagentState } from "@/components/thread/SubagentsSidePanel";
import { DiffPair } from "@/components/thread/activity/DiffPair";
import { FileEditGroup, type FileEditSummary } from "@/components/thread/activity/FileEditRow";
import {
  fileBaseName,
  groupJournalPhases,
  type ActivityJournalEntry,
  type JournalPhase,
  type JournalPhaseKey,
} from "@/components/thread/activity/activityJournalModel";
import type { FileEditDisplayMode } from "@/lib/local-preferences";
import type { ReviewChangeEntry } from "@/lib/types";
import type { CliAppInfo, McpPresetInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

const PHASE_TITLE: Record<JournalPhaseKey, [key: string, fallback: string]> = {
  explore: ["message.journalPhaseExplore", "Project exploration"],
  edit: ["message.journalPhaseEdit", "Changes"],
  agents: ["message.journalPhaseAgents", "Sub-agents"],
  command: ["message.journalPhaseCommand", "Commands"],
  check: ["message.journalPhaseCheck", "Checks"],
  test: ["message.journalPhaseTest", "Tests"],
  attention: ["message.journalPhaseAttention", "Points that need you"],
};

const PHASE_COUNT: Record<Exclude<JournalPhaseKey, "attention">, [key: string, fallback: string]> = {
  explore: ["message.journalPhaseOperations", "{{count}} operations"],
  edit: ["message.journalPhaseFiles", "{{count}} files"],
  agents: ["message.journalPhaseAgentCount", "{{count}} agents"],
  command: ["message.journalPhaseCommands", "{{count}} commands"],
  check: ["message.journalPhaseChecks", "{{count}} checks"],
  test: ["message.journalPhaseTestRuns", "{{count}} runs"],
};

const FOLD = { type: "spring", duration: 0.36, bounce: 0 } as const;

/**
 * A finished turn read by section: what the agent looked at, what it changed,
 * what it ran, and what is left for the user. Each section folds to one line
 * with its count; opened, it shows the same rows and diffs as the live journal.
 */
export function JournalPhases({
  entries,
  fileEdits,
  fileEditDisplayMode,
  onOpenFilePreview,
  cliAppsByName,
  mcpPresetsByName,
  defaultOpen = false,
  resultHeading = false,
  live = false,
  reviewFiles = [],
}: {
  entries: ActivityJournalEntry[];
  fileEdits: FileEditSummary[];
  fileEditDisplayMode: FileEditDisplayMode;
  onOpenFilePreview?: (path: string) => void;
  cliAppsByName?: Map<string, CliAppInfo>;
  mcpPresetsByName?: Map<string, McpPresetInfo>;
  defaultOpen?: boolean;
  /** The answer follows: title it, with a switch for every section. */
  resultHeading?: boolean;
  /** The turn is running: the section being worked on stays open to follow it. */
  live?: boolean;
  /** The chat's pending review: line totals for the files, and the files subagents wrote. */
  reviewFiles?: ReviewChangeEntry[];
}) {
  const { t } = useTranslation();
  // The exploration count is the rows it lists: unnamed reads are not shown, so not counted.
  const changedFiles = live ? [] : mergeChangedFiles(fileEdits, reviewFiles);
  const phases = withChangedFiles(groupJournalPhases(entries), changedFiles).map((phase) =>
    phase.key === "explore" ? { ...phase, count: sectionRows(phase, "").length } : phase);
  // Only what the user toggled is stored; everything else follows the turn.
  const [chosen, setChosen] = useState<Partial<Record<JournalPhaseKey, boolean>>>({});
  if (phases.length === 0) return null;
  const latest = entries.length ? entries[entries.length - 1] : undefined;
  const followed = live && latest ? phases.find((phase) => phase.entries.includes(latest) && phase.key !== "attention")?.key : undefined;
  const isOpen = (key: JournalPhaseKey) => chosen[key] ?? (defaultOpen || key === followed);
  const allOpen = phases.every((phase) => isOpen(phase.key));
  const setAll = (value: boolean) => setChosen(Object.fromEntries(phases.map((phase) => [phase.key, value])));

  return (
    <div className="flex flex-col" data-testid="journal-phases">
      {phases.map((phase) => (
        <PhaseSection
          key={phase.key}
          phase={phase}
          open={isOpen(phase.key)}
          onToggle={() => setChosen((current) => ({ ...current, [phase.key]: !isOpen(phase.key) }))}
        >
          {/* Live, edits stay rows where they happened; the diff cards come once settled. */}
          {phase.key === "edit" && changedFiles.length > 0 ? (
            <AgentFilesTree
              changes={changedFiles}
              edits={fileEdits}
              fileEditDisplayMode={fileEditDisplayMode}
              onOpenFilePreview={onOpenFilePreview}
            />
          ) : (
            phase.key === "agents" ? (
              phase.entries.map((entry) => <AgentSpawnRow key={entry.id} entry={entry} />)
            ) : phase.key === "explore" ? (
              <ExploreGroups
                rows={sectionRows(phase, t("message.journalPhaseToolOutput", { defaultValue: "full tool output" }), t("message.journalPhaseUnnamedFile", { defaultValue: "file not named in the log" }))}
                renderRow={(entry) => (
                  <JournalRow key={entry.id} entry={entry} streaming={live} plain onOpenFilePreview={onOpenFilePreview} />
                )}
              />
            ) : sectionRows(phase, t("message.journalPhaseToolOutput", { defaultValue: "full tool output" }), t("message.journalPhaseUnnamedFile", { defaultValue: "file not named in the log" })).map((entry) => (
              <JournalRow
                key={entry.id}
                entry={entry}
                streaming={live}
                plain
                onOpenFilePreview={onOpenFilePreview}
                cliApp={entry.kind === "cli" && entry.name ? cliAppsByName?.get(entry.name.toLowerCase()) : undefined}
                mcpPreset={
                  entry.kind === "mcp" && entry.mcpRun
                    ? mcpPresetsByName?.get(entry.mcpRun.presetName.toLowerCase())
                    : undefined
                }
              />
            ))
          )}
        </PhaseSection>
      ))}
      {resultHeading ? (
        // The same row as a section title: its mark in the icon column, the title
        // at the same size, the one control where the counts sit.
        <div
          className="flex min-h-10 items-center gap-2.5 border-t border-foreground/[0.06] px-1 py-2"
          data-testid="journal-result-heading"
        >
          <CircleCheck aria-hidden className="h-[18px] w-[18px] shrink-0 text-[hsl(var(--tone-edit))]" strokeWidth={2} />
          <h3 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-foreground">
            {t("message.journalPhaseResult", { defaultValue: "Final result" })}
          </h3>
          <button
            type="button"
            onClick={() => setAll(!allOpen)}
            aria-expanded={allOpen}
            data-testid="journal-result-details"
            className={cn(
              "shrink-0 rounded-md px-1.5 py-0.5 text-[13.5px] text-muted-foreground transition-colors",
              "hover:text-foreground active:scale-[0.96] motion-reduce:active:scale-100",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            {allOpen
              ? t("message.journalPhaseHideDetails", { defaultValue: "Hide details" })
              : t("message.journalPhaseDetails", { defaultValue: "Details" })}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function samePath(a: string, b: string): boolean {
  const norm = (value: string) => value.replace(/\\/g, "/").replace(/^\.\//, "");
  const left = norm(a);
  const right = norm(b);
  return left === right || left.endsWith(`/${right}`) || right.endsWith(`/${left}`);
}

/**
 * Every file the turn touched, once. A file still pending review takes the
 * review's numbers (what the bar at the top shows); the turn's own edits that
 * are no longer pending keep their own; pending files the turn did not edit
 * itself are the subagents' work.
 */
export function mergeChangedFiles(edits: FileEditSummary[], review: ReviewChangeEntry[]): ReviewChangeEntry[] {
  const out: ReviewChangeEntry[] = [];
  const used = new Set<ReviewChangeEntry>();
  for (const edit of edits) {
    if (edit.status === "error") continue;
    const pending = review.find((change) =>
      !used.has(change) && [edit.path, edit.absolute_path].some((path) => path && samePath(path, change.path)));
    if (pending) used.add(pending);
    out.push({
      path: edit.path,
      display_path: edit.path,
      status: pending?.status ?? "modified",
      added: pending?.added ?? edit.added,
      deleted: pending?.deleted ?? edit.deleted,
    } as ReviewChangeEntry);
  }
  for (const change of review) if (!used.has(change)) out.push(change);
  return out;
}

/** The changes section counts the merged files (creating it when the turn edited nothing itself). */
function withChangedFiles(phases: JournalPhase[], files: ReviewChangeEntry[]): JournalPhase[] {
  if (files.length === 0) return phases;
  const added = files.reduce((sum, change) => sum + (change.added ?? 0), 0);
  const deleted = files.reduce((sum, change) => sum + (change.deleted ?? 0), 0);
  const edit = phases.find((phase) => phase.key === "edit");
  if (edit) {
    return phases.map((phase) => (phase === edit ? { ...phase, count: files.length, added, deleted } : phase));
  }
  const extra: JournalPhase = { key: "edit", entries: [], errors: 0, count: files.length, added, deleted, running: false };
  const at = phases.findIndex((phase) => phase.key !== "explore");
  return at < 0 ? [...phases, extra] : [...phases.slice(0, at), extra, ...phases.slice(at)];
}

type FileTreeNode = {
  name: string;
  folders: Map<string, FileTreeNode>;
  files: ReviewChangeEntry[];
  added: number;
  deleted: number;
  total: number;
};

function emptyNode(name: string): FileTreeNode {
  return { name, folders: new Map(), files: [], added: 0, deleted: 0, total: 0 };
}

/** Folders and subfolders, line totals summed upward, single-child chains merged ("restaurant/backend"). */
export function buildFileTree(changes: ReviewChangeEntry[]): FileTreeNode {
  const root = emptyNode("");
  for (const change of changes) {
    const parts = (change.display_path || change.path).replace(/\\/g, "/").split("/").filter(Boolean);
    let node = root;
    const visit = (target: FileTreeNode) => {
      target.added += change.added ?? 0;
      target.deleted += change.deleted ?? 0;
      target.total += 1;
    };
    visit(node);
    for (const part of parts.slice(0, -1)) {
      let next = node.folders.get(part);
      if (!next) {
        next = emptyNode(part);
        node.folders.set(part, next);
      }
      node = next;
      visit(node);
    }
    node.files.push(change);
  }
  const compact = (node: FileTreeNode): FileTreeNode => {
    for (const [key, child] of node.folders) node.folders.set(key, compact(child));
    if (node.name && node.files.length === 0 && node.folders.size === 1) {
      const only = [...node.folders.values()][0];
      return { ...only, name: `${node.name}/${only.name}` };
    }
    return node;
  };
  return compact(root);
}

type TreeContext = {
  edits: Map<string, FileEditSummary>;
  fileEditDisplayMode: FileEditDisplayMode;
  onOpenFilePreview?: (path: string) => void;
};

function AgentFilesTree({
  changes,
  edits = [],
  fileEditDisplayMode,
  onOpenFilePreview,
}: {
  changes: ReviewChangeEntry[];
  edits?: FileEditSummary[];
  fileEditDisplayMode: FileEditDisplayMode;
  onOpenFilePreview?: (path: string) => void;
}) {
  const tree = buildFileTree(changes);
  const context: TreeContext = {
    edits: new Map(edits.map((edit) => [edit.path, edit])),
    fileEditDisplayMode,
    onOpenFilePreview,
  };
  return (
    <div className="flex flex-col" data-testid="journal-agent-files">
      <TreeChildren node={tree} depth={0} context={context} />
    </div>
  );
}

function TreeChildren({
  node,
  depth,
  context,
}: {
  node: FileTreeNode;
  depth: number;
  context: TreeContext;
}) {
  const folders = [...node.folders.values()].sort((a, b) => a.name.localeCompare(b.name));
  const files = [...node.files].sort((a, b) => (a.display_path || a.path).localeCompare(b.display_path || b.path));
  return (
    <>
      {folders.map((folder) => (
        <FolderRow key={folder.name} node={folder} depth={depth} context={context} />
      ))}
      {files.map((change) => (
        <AgentFileRow key={change.path} change={change} depth={depth} context={context} />
      ))}
    </>
  );
}

function FolderRow({
  node,
  depth,
  context,
}: {
  node: FileTreeNode;
  depth: number;
  context: TreeContext;
}) {
  // The first level is open; deeper folders wait for a click.
  const [open, setOpen] = useState(depth === 0);
  return (
    <div data-testid="journal-agent-folder">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        style={{ paddingInlineStart: `${depth * 16 + 4}px` }}
        className={cn(
          "flex min-h-7 w-full min-w-0 items-center gap-2 rounded-md py-0.5 pe-1 text-start",
          "transition-colors hover:bg-foreground/[0.04]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "rtl:[&:not(.rotate-90)]:rotate-180 h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200",
            open && "rotate-90",
          )}
        />
        {open
          ? <FolderOpen aria-hidden className="h-3.5 w-3.5 shrink-0 text-foreground/55" strokeWidth={1.8} />
          : <Folder aria-hidden className="h-3.5 w-3.5 shrink-0 text-foreground/55" strokeWidth={1.8} />}
        <span className="min-w-0 flex-1 truncate font-medium text-foreground/85">{node.name}</span>
        <span className="shrink-0 text-[12.5px] tabular-nums text-muted-foreground">{node.total}</span>
        {node.added + node.deleted > 0 ? (
          <DiffPair added={node.added} deleted={node.deleted} className="shrink-0 text-[13px] tabular-nums" />
        ) : null}
      </button>
      {open ? <TreeChildren node={node} depth={depth + 1} context={context} /> : null}
    </div>
  );
}

/** One changed file: name and line totals. The turn's own edits unfold their diff here; others open the preview. */
function AgentFileRow({
  change,
  depth,
  context,
}: {
  change: ReviewChangeEntry;
  depth: number;
  context: TreeContext;
}) {
  const [open, setOpen] = useState(false);
  const full = change.display_path || change.path;
  const label = full.replace(/\\/g, "/").split("/").pop() || full;
  const edit = context.edits.get(change.path);
  const indent = depth * 16 + 4 + 22;
  const onClick = edit
    ? () => setOpen((value) => !value)
    : () => context.onOpenFilePreview?.(change.path);
  return (
    <div>
      <button
        type="button"
        onClick={onClick}
        disabled={!edit && !context.onOpenFilePreview}
        aria-expanded={edit ? open : undefined}
        title={full}
        data-testid="journal-agent-file"
        style={{ paddingInlineStart: `${indent}px` }}
        className={cn(
          "flex min-h-7 w-full min-w-0 items-center gap-2 rounded-md py-0.5 pe-1 text-start",
          "transition-colors hover:bg-foreground/[0.04] disabled:cursor-default",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <FilePen aria-hidden className="h-3.5 w-3.5 shrink-0 text-foreground/55" strokeWidth={1.8} />
        <span className="min-w-0 flex-1 truncate text-foreground/85">{label}</span>
        {(change.added ?? 0) + (change.deleted ?? 0) > 0 ? (
          <DiffPair added={change.added ?? 0} deleted={change.deleted ?? 0} className="shrink-0 text-[13px] tabular-nums" />
        ) : null}
      </button>
      {edit && open ? (
        <div style={{ paddingInlineStart: `${indent}px` }} className="pb-1">
          <FileEditGroup
            edits={[edit]}
            displayMode={context.fileEditDisplayMode === "summary" ? "diff" : context.fileEditDisplayMode}
            onOpenFilePreview={context.onOpenFilePreview}
          />
        </div>
      ) : null}
    </div>
  );
}

const EXPLORE_GROUPS: Array<{ key: string; match: (entry: ActivityJournalEntry) => boolean; label: [string, string] }> = [
  { key: "read", match: (entry) => entry.kind === "read", label: ["message.journalExploreReads", "Reads"] },
  { key: "search", match: (entry) => entry.kind === "search", label: ["message.journalExploreSearches", "Searches"] },
  { key: "list", match: (entry) => entry.kind === "tool" && ["list_dir", "find_files", "glob", "tree"].includes(entry.tool ?? ""), label: ["message.journalExploreListings", "Folder listings"] },
  { key: "index", match: (entry) => entry.kind === "tool" && ["code_index", "metagraph", "lsp"].includes(entry.tool ?? ""), label: ["message.journalExploreIndex", "Code index"] },
];

/** Exploration by kind of action (reads, searches, listings, index), each group with its count; nothing left out. */
function ExploreGroups({
  rows,
  renderRow,
}: {
  rows: ActivityJournalEntry[];
  renderRow: (entry: ActivityJournalEntry) => ReactNode;
}) {
  const { t } = useTranslation();
  // Each row in exactly one group: the first that claims it.
  const owner = (entry: ActivityJournalEntry) => EXPLORE_GROUPS.find((group) => group.match(entry))?.key ?? "other";
  const groups = EXPLORE_GROUPS.map((group) => ({ ...group, rows: rows.filter((entry) => owner(entry) === group.key) }));
  const other = rows.filter((entry) => owner(entry) === "other");
  const all = [...groups, { key: "other", match: () => true, label: ["message.journalExploreOther", "Other"] as [string, string], rows: other }]
    .filter((group) => group.rows.length > 0);
  if (all.length === 1) return <>{all[0].rows.map(renderRow)}</>;
  return (
    <>
      {all.map((group) => (
        <ExploreGroup key={group.key} title={t(group.label[0], { defaultValue: group.label[1] })} count={group.rows.length}>
          {group.rows.map(renderRow)}
        </ExploreGroup>
      ))}
    </>
  );
}

function ExploreGroup({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <div data-testid="journal-explore-group">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={cn(
          "flex min-h-7 w-full items-center gap-2 rounded-md px-1 py-0.5 text-start text-[13.5px] font-medium text-foreground/80",
          "transition-colors hover:bg-foreground/[0.04]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <ChevronRight
          aria-hidden
          className={cn("rtl:[&:not(.rotate-90)]:rotate-180 h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200", open && "rotate-90")}
        />
        <span className="min-w-0 flex-1 truncate">{title}</span>
        <span className="shrink-0 text-[12.5px] tabular-nums text-muted-foreground">{count}</span>
      </button>
      {open ? <div className="flex flex-col gap-0.5 ps-5">{children}</div> : null}
    </div>
  );
}

const NO_CARDS = { getSnapshot: () => EMPTY_CARDS, subscribe: () => () => undefined };
const EMPTY_CARDS = { cards: [] as Array<{ label?: string; task_id?: string; done: boolean; phase: string; error?: string | null }> };

/** One sub-agent the turn started: its name, its state, its details one click away (the Sub-agents panel). */
function AgentSpawnRow({ entry }: { entry: ActivityJournalEntry }) {
  const { t } = useTranslation();
  const { store, openPanel } = useContext(SubagentJournalContext);
  const source = (store ?? NO_CARDS) as typeof NO_CARDS;
  const { cards } = useSyncExternalStore(source.subscribe, source.getSnapshot, source.getSnapshot);
  const name = entry.target || t("message.journalPhaseAgentUnnamed", { defaultValue: "Sub-agent" });
  const card = cards.find((item) => item.label && (item.label === entry.target || entry.target?.startsWith(item.label)));
  const state = card
    ? subagentState({ done: card.done, phase: card.phase, error: card.error ?? null })
    : entry.status === "error"
      ? "failed"
      : null;
  return (
    <button
      type="button"
      onClick={() => openPanel?.()}
      disabled={!openPanel}
      title={name}
      data-testid="journal-agent-row"
      className={cn(
        "flex min-h-8 w-full min-w-0 items-center gap-2 rounded-md px-1 py-0.5 text-start",
        "transition-colors hover:bg-foreground/[0.04] disabled:cursor-default",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      {state === "running"
        ? <Loader2 aria-hidden className="h-3.5 w-3.5 shrink-0 animate-spin text-foreground/55 motion-reduce:animate-none" />
        : <Users aria-hidden className="h-3.5 w-3.5 shrink-0 text-foreground/55" strokeWidth={1.8} />}
      <span className="min-w-0 flex-1 truncate text-foreground/85">{name}</span>
      {state ? <StateBadge state={state} /> : null}
      {openPanel ? (
        <ChevronRight aria-hidden className="rtl:rotate-180 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      ) : null}
    </button>
  );
}

const TOOL_RESULT_PATH = /(^|[\\/])\.navin[\\/]tool-results[\\/]/;

/**
 * One line per thing done. A run of reads and searches folded into one
 * "Explored 3 files, 1 search" row is listed back as its operations, so the
 * section reads like the steps it stands for.
 */
function sectionRows(phase: JournalPhase, toolOutputLabel: string, unnamedLabel = "?"): ActivityJournalEntry[] {
  return phase.entries.flatMap((entry) => {
    if (entry.kind !== "explore" || !entry.operations?.length) return [entry];
    // Every action is listed, even one whose target the trace did not keep.
    return entry.operations.map((operation, index) => ({
      id: `${entry.id}:${index}`,
      seq: entry.seq,
      kind: operation.kind,
      tone: operation.kind,
      status: operation.status,
      durationMs: operation.durationMs,
      ...(operation.kind === "read"
        ? {
            path: operation.path,
            // The agent re-reading an output too long for its context: name it, not its call id.
            file: !operation.path?.trim()
              ? unnamedLabel
              : TOOL_RESULT_PATH.test(operation.path) ? toolOutputLabel : fileBaseName(operation.path),
          }
        : { query: operation.query, tool: operation.tool }),
    }));
  });
}

function PhaseSection({
  phase,
  open,
  onToggle,
  children,
}: {
  phase: JournalPhase;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const [titleKey, titleFallback] = PHASE_TITLE[phase.key];
  const meta = phase.key === "attention"
    ? String(phase.count)
    : t(PHASE_COUNT[phase.key][0], { count: phase.count, defaultValue: PHASE_COUNT[phase.key][1] });

  return (
    <section
      className="border-b border-foreground/[0.06] last:border-b-0"
      data-testid={`journal-phase-${phase.key}`}
      data-open={open}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className={cn(
          "flex w-full min-h-10 cursor-pointer items-center gap-2.5 rounded-md px-1 py-2 text-start",
          "transition-colors duration-200 hover:bg-foreground/[0.03]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <PhaseIcon phase={phase} />
        {/* Lighter than "Final result": the steps are the path, the answer is the destination. */}
        <span className="min-w-0 flex-1 truncate text-[14.5px] font-medium text-foreground/85">
          {t(titleKey, { defaultValue: titleFallback })}
        </span>
        {phase.key === "test" || phase.key === "command" ? <RunProgress phase={phase} /> : null}
        <span className="inline-flex shrink-0 items-center gap-2 text-[13.5px] tabular-nums text-muted-foreground">
          <span>{meta}</span>
          {phase.errors > 0 && phase.key !== "attention" && phase.key !== "edit" ? (
            <span className="text-[hsl(var(--tone-error))]" data-testid={`journal-phase-${phase.key}-errors`}>
              {t("message.journalPhaseErrors", { count: phase.errors, defaultValue: "{{count}} errors" })}
            </span>
          ) : null}
          {phase.key === "edit" && (phase.added > 0 || phase.deleted > 0) ? (
            <DiffPair added={phase.added} deleted={phase.deleted} />
          ) : null}
        </span>
        <ChevronRight
          aria-hidden
          className={cn(
            "rtl:[&:not(.rotate-90)]:rotate-180 h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200",
            open && "rotate-90",
          )}
        />
      </button>
      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            key="body"
            initial={reduceMotion ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={FOLD}
            className="overflow-hidden"
          >
            <div className="flex flex-col gap-0.5 pb-3 ps-7 text-[14.5px] leading-6" data-testid={`journal-phase-${phase.key}-body`}>
              {children}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  );
}

/**
 * On the Tests line: quick or long (with the usual minutes), a thin bar and the
 * real percent the runner reports, so a long run never looks frozen.
 */
type RunFacts = { running: boolean; percent?: number; expectedS?: number; kind?: "quick" | "long" };

/** What a test run or a command says about its progress and usual duration. */
function runFacts(entry: ActivityJournalEntry): RunFacts | null {
  const running = entry.status === "running";
  if (entry.kind === "shell" || entry.kind === "cli") {
    const run = entry.shellRun;
    return { running, percent: run?.percent, expectedS: run?.expectedS, kind: run?.runKind };
  }
  if (entry.tool === "test_run" || entry.tool === "verify") {
    return { running, percent: entry.percent, expectedS: entry.expectedS, kind: entry.testKind };
  }
  return null;
}

/**
 * On the Tests and Commands lines: quick or long (with the usual minutes), a
 * thin bar and the percent (the runner's own, else estimated from earlier
 * runs), so a long run never looks frozen, even with its output piped to tail.
 */
function RunProgress({ phase }: { phase: JournalPhase }) {
  const { t } = useTranslation();
  const runs = phase.entries.map(runFacts).filter((facts): facts is RunFacts => facts !== null);
  const current = [...runs].reverse().find((facts) => facts.running) ?? runs[runs.length - 1];
  if (!current) return null;
  const isTest = phase.key === "test";
  // A finished command already shows its duration; the bar is for the wait.
  if (!current.running && !isTest) return null;
  const running = current.running;
  const percent = running
    ? typeof current.percent === "number" ? Math.max(0, Math.min(100, current.percent)) : undefined
    : 100;
  const minutes = current.expectedS ? Math.max(1, Math.round(current.expectedS / 60)) : null;
  const kind = current.kind;
  const kindLabel = kind === "long"
    ? isTest
      ? t("message.journalTestLong", { defaultValue: "Long tests" })
      : t("message.journalCommandLong", { defaultValue: "Long command" })
    : kind === "quick"
      ? isTest
        ? t("message.journalTestQuick", { defaultValue: "Quick tests" })
        : t("message.journalCommandQuick", { defaultValue: "Quick command" })
      : null;
  return (
    <span className="inline-flex min-w-0 shrink items-center gap-2 text-[12.5px] text-muted-foreground" data-testid="journal-test-progress">
      {kindLabel ? (
        <span className="shrink-0">
          {kindLabel}
          {kind === "long" && minutes
            ? ` ${t("message.journalTestEstimate", { count: minutes, defaultValue: "~{{count}} min" })}`
            : ""}
        </span>
      ) : null}
      {running || percent === 100 ? (
        <span className="relative h-1 w-16 shrink-0 overflow-hidden rounded-full bg-muted/60" aria-hidden>
          {percent === undefined ? (
            <span className="absolute inset-y-0 w-1/3 animate-[task-progress-slide_1.2s_ease-in-out_infinite] rounded-full bg-sky-500/80 motion-reduce:animate-none" />
          ) : (
            <span className="block h-full rounded-full bg-sky-500/85 transition-[width] duration-300" style={{ width: `${percent}%` }} />
          )}
        </span>
      ) : null}
      {percent !== undefined && (running || percent === 100) ? (
        <span className="shrink-0 tabular-nums" data-testid="journal-test-percent">{Math.round(percent)}%</span>
      ) : null}
    </span>
  );
}

function PhaseIcon({ phase }: { phase: JournalPhase }) {
  const { t } = useTranslation();
  if (phase.running) {
    return (
      <Loader2
        aria-label={t("message.activityJournalRunning", { defaultValue: "Running" })}
        className="h-[18px] w-[18px] shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none"
      />
    );
  }
  if (phase.key === "attention") {
    return <CircleAlert aria-hidden className="h-[18px] w-[18px] shrink-0 text-[hsl(var(--tone-shell))]" strokeWidth={2} />;
  }
  return (
    <CircleCheck
      aria-label={t("message.activityJournalDone", { defaultValue: "Done" })}
      className="h-[18px] w-[18px] shrink-0 text-[hsl(var(--tone-edit))]"
      strokeWidth={2}
    />
  );
}
