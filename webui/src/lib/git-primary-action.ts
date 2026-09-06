/** Which primary Source Control button to show. */

export type GitPrimaryAction = "commit" | "push" | "publish";

export function primaryGitAction(opts: {
  changeCount: number;
  ahead: number;
  hasUpstream: boolean;
  /** True right after a successful local commit, before status refresh. */
  pendingPush?: boolean;
}): GitPrimaryAction {
  // A branch created in the IDE or the terminal has no upstream yet: surface
  // "Publish Branch" as the primary action so a local branch is obvious at a
  // glance. Committing stays available from the dropdown, so the user can
  // either publish straight away or commit first, then publish.
  if (!opts.hasUpstream) return "publish";
  // After a commit, the next step is Push: leftover dirty files stay in the
  // list, but the button tells the user they have moved to the publish stage.
  if (opts.pendingPush || opts.ahead > 0) return "push";
  if (opts.changeCount > 0) return "commit";
  return "push";
}

export function canRunPrimaryGitAction(opts: {
  isRepo: boolean;
  changeCount: number;
  ahead: number;
  hasUpstream: boolean;
  hasMessage: boolean;
  pendingPush?: boolean;
}): boolean {
  if (!opts.isRepo) return false;
  const action = primaryGitAction(opts);
  if (action === "commit") return opts.hasMessage && opts.changeCount > 0;
  if (action === "publish") return true;
  return opts.ahead > 0 || Boolean(opts.pendingPush);
}

/** Amend and Undo Last Commit: refuse when HEAD is already on the remote. */
export function canRewriteLastCommit(opts: {
  isRepo: boolean;
  hasUpstream: boolean;
  ahead: number;
}): boolean {
  return opts.isRepo && (!opts.hasUpstream || opts.ahead > 0);
}
