import { describe, expect, it } from "vitest";

import {
  canRewriteLastCommit,
  canRunPrimaryGitAction,
  primaryGitAction,
} from "./git-primary-action";

describe("primaryGitAction", () => {
  it("shows Publish Branch on an unpublished branch, clean or dirty", () => {
    expect(
      primaryGitAction({ changeCount: 0, ahead: 1, hasUpstream: false }),
    ).toBe("publish");
    expect(
      primaryGitAction({ changeCount: 0, ahead: 0, hasUpstream: false }),
    ).toBe("publish");
    // A local branch stays "Publish Branch" even with pending edits: the user
    // can commit from the menu first, then publish.
    expect(
      primaryGitAction({ changeCount: 3, ahead: 0, hasUpstream: false }),
    ).toBe("publish");
  });

  it("shows Commit when there are local edits on a published branch", () => {
    expect(
      primaryGitAction({ changeCount: 1, ahead: 0, hasUpstream: true }),
    ).toBe("commit");
  });

  it("shows Push after a local commit, even if some files are still dirty", () => {
    expect(
      primaryGitAction({ changeCount: 3, ahead: 1, hasUpstream: true }),
    ).toBe("push");
    expect(
      primaryGitAction({
        changeCount: 3,
        ahead: 0,
        hasUpstream: true,
        pendingPush: true,
      }),
    ).toBe("push");
  });

  it("shows Push when the branch tracks a remote and the tree is clean", () => {
    expect(
      primaryGitAction({ changeCount: 0, ahead: 2, hasUpstream: true }),
    ).toBe("push");
  });
});

describe("canRunPrimaryGitAction", () => {
  it("requires a commit message before Commit", () => {
    expect(
      canRunPrimaryGitAction({
        isRepo: true,
        changeCount: 2,
        ahead: 0,
        hasUpstream: true,
        hasMessage: false,
      }),
    ).toBe(false);
    expect(
      canRunPrimaryGitAction({
        isRepo: true,
        changeCount: 2,
        ahead: 0,
        hasUpstream: true,
        hasMessage: true,
      }),
    ).toBe(true);
  });

  it("lets Push run after a commit even before ahead is refreshed", () => {
    expect(
      canRunPrimaryGitAction({
        isRepo: true,
        changeCount: 2,
        ahead: 0,
        hasUpstream: true,
        hasMessage: false,
        pendingPush: true,
      }),
    ).toBe(true);
  });

  it("lets Publish Branch run with an empty message", () => {
    expect(
      canRunPrimaryGitAction({
        isRepo: true,
        changeCount: 0,
        ahead: 1,
        hasUpstream: false,
        hasMessage: false,
      }),
    ).toBe(true);
  });
});

describe("canRewriteLastCommit", () => {
  it("allows amend/undo when the tip is not on the remote", () => {
    expect(
      canRewriteLastCommit({ isRepo: true, hasUpstream: false, ahead: 0 }),
    ).toBe(true);
    expect(
      canRewriteLastCommit({ isRepo: true, hasUpstream: true, ahead: 2 }),
    ).toBe(true);
  });

  it("refuses amend/undo when HEAD matches the upstream", () => {
    expect(
      canRewriteLastCommit({ isRepo: true, hasUpstream: true, ahead: 0 }),
    ).toBe(false);
    expect(
      canRewriteLastCommit({ isRepo: false, hasUpstream: false, ahead: 1 }),
    ).toBe(false);
  });
});
