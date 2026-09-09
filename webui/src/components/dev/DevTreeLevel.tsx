// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { MouseEvent as ReactMouseEvent } from "react";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  MoreHorizontal,
} from "lucide-react";

import type {
  FileDiagnosticsPayload,
  FileTreeEntry,
  ReviewChangeEntry,
} from "@/lib/types";
import { cn } from "@/lib/utils";

import { FileTypeIcon, FolderTypeIcon } from "./FileTypeIcon";
import {
  GIT_STATUS_LETTERS,
  gitStatusColor,
  lookupTreeNode,
  reviewStatusBadge,
  treeDirIsExpanded,
} from "./devWorkbenchUtils";

export type TreeNodeState = {
  entries: FileTreeEntry[];
  loading: boolean;
  error: string | null;
};

export function DevTreeLevel({
  parentKey,
  nodes,
  expanded,
  rootPath = null,
  activePath,
  dirtyPaths,
  reviewByPath,
  reviewDirs,
  gitStatusByPath,
  gitDirs,
  diagnostics,
  depth,
  emptyLabel,
  onToggleDir,
  onOpenFile,
  onContextMenu,
  menuLabel,
  renaming,
  renameError,
  onRenameChange,
  onRenameSubmit,
  onRenameCancel,
  onRequestRename,
  onRequestDelete,
  onClipboardKey,
}: {
  parentKey: string;
  nodes: Record<string, TreeNodeState>;
  expanded: Set<string>;
  rootPath?: string | null;
  activePath: string | null;
  dirtyPaths: Set<string>;
  reviewByPath: Map<string, ReviewChangeEntry>;
  reviewDirs: Set<string>;
  gitStatusByPath: Map<string, string>;
  gitDirs: Set<string>;
  diagnostics: Record<string, FileDiagnosticsPayload>;
  depth: number;
  emptyLabel: string;
  onToggleDir: (path: string) => void;
  onOpenFile: (entry: FileTreeEntry) => void;
  onContextMenu: (entry: FileTreeEntry, event: ReactMouseEvent) => void;
  menuLabel: string;
  renaming: { path: string; name: string } | null;
  renameError: string | null;
  onRenameChange: (name: string) => void;
  onRenameSubmit: () => void;
  onRenameCancel: () => void;
  onRequestRename: (entry: FileTreeEntry) => void;
  onRequestDelete: (entry: FileTreeEntry) => void;
  onClipboardKey: (entry: FileTreeEntry, key: "x" | "c" | "v") => void;
}) {
  const state = lookupTreeNode(nodes, parentKey);
  if (!state) return null;
  if (!state.loading && !state.error && state.entries.length === 0) {
    return (
      <p
        className="py-1 text-[11px] italic text-muted-foreground/70"
        style={{ paddingLeft: `${26 + depth * 14}px` }}
      >
        {emptyLabel}
      </p>
    );
  }
  return (
    <div>
      {state.entries.map((entry) => {
        const isOpen =
          entry.type === "dir" && treeDirIsExpanded(expanded, entry.path, rootPath);
        const review = entry.type === "file" ? reviewByPath.get(entry.path) : undefined;
        const reviewBadge = review ? reviewStatusBadge(review.status) : null;
        const dirHasChanges =
          entry.type === "dir" && (reviewDirs.has(entry.path) || gitDirs.has(entry.path));
        const diag = entry.type === "file" ? diagnostics[entry.path] : undefined;
        const severity: "error" | "warning" | null =
          diag && diag.errors > 0 ? "error" : diag && diag.warnings > 0 ? "warning" : null;
        const gitState = entry.type === "file" ? gitStatusByPath.get(entry.path) : undefined;
        const isNew = gitState === "untracked" || gitState === "added" || review?.status === "created";
        const isChanged =
          dirtyPaths.has(entry.path) ||
          (review !== undefined && review.status !== "created") ||
          (gitState !== undefined && !isNew);
        // Name tint priority, VS Code-style: errors > warnings > new > changed.
        const nameClass =
          severity === "error"
            ? "text-red-500"
            : severity === "warning"
              ? "text-orange-400"
              : isNew
                ? "text-emerald-500"
                : isChanged
                  ? "text-amber-500"
                  : undefined;
        const gitLetter =
          !reviewBadge && gitState ? GIT_STATUS_LETTERS[gitState] ?? "M" : null;
        if (renaming?.path === entry.path) {
          return (
            <div key={entry.path}>
              <div
                className="flex items-center gap-1.5 py-0.5 pr-2"
                style={{ paddingLeft: `${8 + depth * 14}px` }}
              >
                {entry.type === "dir" ? (
                  <FolderTypeIcon name={entry.name} className="ml-[18px]" />
                ) : (
                  <FileTypeIcon name={entry.name} className="ml-[18px]" />
                )}
                <input
                  autoFocus
                  value={renaming.name}
                  onChange={(event) => onRenameChange(event.target.value)}
                  onFocus={(event) => {
                    // Select the stem, not the extension: renaming a file
                    // almost never means renaming its type.
                    const dot = entry.name.lastIndexOf(".");
                    event.target.setSelectionRange(
                      0,
                      dot > 0 ? dot : entry.name.length,
                    );
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      onRenameSubmit();
                    } else if (event.key === "Escape") {
                      event.preventDefault();
                      onRenameCancel();
                    }
                  }}
                  onBlur={onRenameCancel}
                  className="min-w-0 flex-1 rounded border border-primary/60 bg-background px-1 py-0 text-[12.5px] leading-5 outline-none"
                />
              </div>
              {renameError ? (
                <p
                  className="pb-1 text-[11px] text-destructive"
                  style={{ paddingLeft: `${26 + depth * 14}px` }}
                >
                  {renameError}
                </p>
              ) : null}
            </div>
          );
        }
        return (
          <div key={entry.path}>
            <div className="group relative">
            <button
              type="button"
              onClick={() =>
                entry.type === "dir" ? onToggleDir(entry.path) : onOpenFile(entry)
              }
              onContextMenu={(event) => onContextMenu(entry, event)}
              onKeyDown={(event) => {
                if (event.key === "F2") {
                  event.preventDefault();
                  onRequestRename(entry);
                } else if (event.key === "Delete") {
                  event.preventDefault();
                  onRequestDelete(entry);
                } else if (event.ctrlKey || event.metaKey) {
                  const key = event.key.toLowerCase();
                  if (key !== "x" && key !== "c" && key !== "v") return;
                  event.preventDefault();
                  onClipboardKey(entry, key as "x" | "c" | "v");
                }
              }}
              style={{ paddingLeft: `${8 + depth * 14}px` }}
              className={cn(
                "flex w-full min-w-0 items-center gap-1.5 rounded-md py-1 pr-2 text-left text-[12.5px] leading-5 transition-colors",
                activePath === entry.path
                  ? "bg-primary/10 text-primary"
                  : "text-foreground/80 hover:bg-muted/60 hover:text-foreground",
                entry.heavy && "opacity-60",
              )}
            >
              {entry.type === "dir" ? (
                <>
                  {isOpen ? (
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  )}
                  <FolderTypeIcon name={entry.name} open={isOpen} />
                </>
              ) : (
                <FileTypeIcon name={entry.name} className="ml-[18px]" />
              )}
              <span className={cn("truncate", nameClass)}>
                {entry.name}
              </span>
              {dirtyPaths.has(entry.path) ? (
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500/90" aria-hidden />
              ) : null}
              <span className="ml-auto flex shrink-0 items-center gap-1 pl-1">
                {severity && diag ? (
                  <span
                    className={cn(
                      "text-[10px] font-semibold tabular-nums",
                      severity === "error" ? "text-red-500" : "text-orange-400",
                    )}
                    title={
                      severity === "error"
                        ? `${diag.errors} error(s)`
                        : `${diag.warnings} warning(s)`
                    }
                    aria-hidden
                  >
                    {severity === "error" ? diag.errors : diag.warnings}
                  </span>
                ) : null}
                {reviewBadge ? (
                  <span
                    className={cn("text-[10px] font-semibold", reviewBadge.className)}
                    aria-hidden
                  >
                    {reviewBadge.letter}
                  </span>
                ) : gitLetter && gitState ? (
                  <span
                    className={cn("text-[10px] font-semibold", gitStatusColor(gitState))}
                    aria-hidden
                  >
                    {gitLetter}
                  </span>
                ) : null}
                {dirHasChanges ? (
                  <span
                    className="h-1.5 w-1.5 rounded-full bg-amber-500/80"
                    aria-hidden
                  />
                ) : null}
              </span>
            </button>
            {/* Same menu as right-click, for pointers that have no second
                button and for anyone who never thinks to try one. */}
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onContextMenu(entry, event);
              }}
              className="absolute right-1 top-1/2 -translate-y-1/2 rounded bg-background/95 p-0.5 text-muted-foreground opacity-0 shadow-sm transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
              aria-label={`${menuLabel} - ${entry.name}`}
              title={menuLabel}
            >
              <MoreHorizontal className="h-3 w-3" aria-hidden />
            </button>
            </div>
            {isOpen ? (
              !lookupTreeNode(nodes, entry.path, rootPath) ||
              (lookupTreeNode(nodes, entry.path, rootPath)?.loading &&
                !lookupTreeNode(nodes, entry.path, rootPath)?.entries.length) ? (
                <div
                  className="flex items-center gap-1.5 py-1 text-[11px] text-muted-foreground"
                  style={{ paddingLeft: `${26 + depth * 14}px` }}
                >
                  <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                </div>
              ) : (
                <DevTreeLevel
                  parentKey={entry.path}
                  nodes={nodes}
                  expanded={expanded}
                  rootPath={rootPath}
                  activePath={activePath}
                  dirtyPaths={dirtyPaths}
                  reviewByPath={reviewByPath}
                  reviewDirs={reviewDirs}
                  gitStatusByPath={gitStatusByPath}
                  gitDirs={gitDirs}
                  diagnostics={diagnostics}
                  depth={depth + 1}
                  emptyLabel={emptyLabel}
                  onToggleDir={onToggleDir}
                  onOpenFile={onOpenFile}
                  onContextMenu={onContextMenu}
                  menuLabel={menuLabel}
                  renaming={renaming}
                  renameError={renameError}
                  onRenameChange={onRenameChange}
                  onRenameSubmit={onRenameSubmit}
                  onRenameCancel={onRenameCancel}
                  onRequestRename={onRequestRename}
                  onRequestDelete={onRequestDelete}
                  onClipboardKey={onClipboardKey}
                />
              )
            ) : null}
          </div>
        );
      })}
      {state.error ? (
        <p className="px-2 py-1 text-[11px] text-destructive">{state.error}</p>
      ) : null}
    </div>
  );
}
