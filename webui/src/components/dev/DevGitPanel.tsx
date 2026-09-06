import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  CircleDot,
  CloudUpload,
  ExternalLink,
  FileSearch,
  GitBranch,
  GitPullRequest,
  Loader2,
  Minus,
  Plus,
  RefreshCw,
  ScanSearch,
  Sparkles,
  Undo2,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  commitGitChanges,
  createGithubPr,
  discardGitPaths,
  fetchFixCiPrompt,
  fetchGitRemote,
  fetchGithubChecks,
  fetchGithubPr,
  fetchGitChanges,
  fetchReviewPrompt,
  generateGitCommitMessage,
  gitBranchOp,
  gitConflictAction,
  pullGitChanges,
  stageGitPaths,
  stashGitChanges,
  syncGitChanges,
  undoLastGitCommit,
} from "@/lib/api";
import { DevCheckpointsPanel } from "@/components/dev/DevCheckpointsPanel";
import { DevGitTimeline } from "@/components/dev/DevGitTimeline";
import { DevProveChange } from "@/components/dev/DevProveChange";
import { forgeCopy, forgeSetupHint } from "@/lib/forge";
import {
  canRewriteLastCommit,
  canRunPrimaryGitAction,
  primaryGitAction,
} from "@/lib/git-primary-action";
import { publishNotification } from "@/lib/notification-bus";
import type {
  GithubCiStatusPayload,
  GithubPrViewPayload,
  GitChangesPayload,
  GitStashEntry,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_LETTER: Record<string, string> = {
  modified: "M",
  added: "A",
  deleted: "D",
  renamed: "R",
  copied: "C",
  typechange: "T",
  conflict: "!",
  untracked: "U",
};

type CommitAction = "commit-push" | "commit" | "push" | "pull" | "publish";

export function DevGitPanel({
  token,
  sessionKey,
  selectedPath,
  onShowDiff,
  onDidCommit,
  onWorkingTreeChanged,
  onRunAction,
  projectPath,
  openCheckpointsSignal,
}: {
  token: string;
  sessionKey: string;
  selectedPath: string | null;
  onShowDiff: (relativePath: string) => void;
  onDidCommit?: () => void;
  /**
   * Fired after an operation rewrites tracked files on disk (discard, stash,
   * pull, sync, undo). Lets the editor reload open files so a discarded change
   * disappears live instead of lingering until the tab is reopened. When
   * `paths` is given the change is limited to those files; otherwise every open
   * file is refreshed.
   */
  onWorkingTreeChanged?: (paths?: string[]) => void;
  onRunAction?: (text: string) => void;
  /** Absolute project root; enables "Prove this change" (Evolve bench). */
  projectPath?: string | null;
  /** Bumped by the chat's checkpoint CTA; switches to the Checkpoints tab. */
  openCheckpointsSignal?: number;
}) {
  const { t, i18n } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [payload, setPayload] = useState<GitChangesPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<CommitAction | string | null>(null);
  const [pendingPush, setPendingPush] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [tab, setTab] = useState<"changes" | "history" | "checkpoints">(
    "changes",
  );
  useEffect(() => {
    if (openCheckpointsSignal) setTab("checkpoints");
  }, [openCheckpointsSignal]);
  const [historyBump, setHistoryBump] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [branchOpen, setBranchOpen] = useState(false);
  const [branches, setBranches] = useState<string[]>([]);
  const [newBranch, setNewBranch] = useState("");
  const [prView, setPrView] = useState<GithubPrViewPayload | null>(null);
  const [ci, setCi] = useState<GithubCiStatusPayload | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const branchRef = useRef<HTMLDivElement | null>(null);
  const messageBoxRef = useRef<HTMLTextAreaElement | null>(null);
  const [discardTarget, setDiscardTarget] = useState<string[] | "all" | null>(
    null,
  );
  const [stashDropTarget, setStashDropTarget] = useState<GitStashEntry | null>(
    null,
  );

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchGitChanges(token, sessionKey)
      .then((next) => {
        setPayload(next);
        setSelected((prev) => {
          if (!next.is_repo) return new Set();
          const paths = new Set(next.files.map((file) => file.path));
          const kept = [...prev].filter((path) => paths.has(path));
          return new Set(kept);
        });
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
        setPayload(null);
      })
      .finally(() => setLoading(false));

    void fetchGithubPr(token, sessionKey)
      .then(setPrView)
      .catch(() => setPrView(null));
    void fetchGithubChecks(token, sessionKey)
      .then(setCi)
      .catch(() => setCi(null));
  }, [sessionKey, token]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 15_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useLayoutEffect(() => {
    const el = messageBoxRef.current;
    if (!el) return;
    el.style.height = "0px";
    const next = Math.min(Math.max(el.scrollHeight, 40), 132);
    el.style.height = `${next}px`;
  }, [message]);

  useEffect(() => {
    if (!menuOpen && !branchOpen) return;
    const close = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
      if (branchRef.current && !branchRef.current.contains(event.target as Node)) {
        setBranchOpen(false);
      }
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [menuOpen, branchOpen]);

  const changeCount = payload?.is_repo ? payload.files.length : 0;
  const ahead = payload?.ahead ?? 0;
  const behind = payload?.behind ?? 0;
  const branch = payload?.branch ?? "";
  const stagedCount = useMemo(
    () => (payload?.files ?? []).filter((file) => file.staged).length,
    [payload],
  );
  const selectedList = useMemo(() => [...selected], [selected]);
  const conflictBusy =
    Boolean(payload?.merge_in_progress) || Boolean(payload?.rebase_in_progress);

  /**
   * GitLab calls it a merge request, GitHub and Forgejo a pull request. The
   * panel follows the forge behind `origin` so the buttons match the site the
   * user is about to land on.
   */
  const forgeKind = prView?.forge ?? ci?.forge ?? "unknown";
  const forge = useMemo(() => {
    const copy = forgeCopy(forgeKind);
    return {
      draft: tx(copy.draft.key, copy.draft.fallback),
      create: tx(copy.create.key, copy.create.fallback),
      commitAndCreate: tx(
        copy.commitAndCreate.key,
        copy.commitAndCreate.fallback,
      ),
      draftOpenedNotice: tx(copy.draftOpened.key, copy.draftOpened.fallback),
      openedNotice: tx(copy.opened.key, copy.opened.fallback),
      reusedNotice: tx(copy.reused.key, copy.reused.fallback),
      commitNotice: tx(copy.committed.key, copy.committed.fallback),
      failedNotice: tx(copy.failed.key, copy.failed.fallback),
    };
  }, [forgeKind, tx]);

  /**
   * The one setup step that used to read "gh CLI not installed": no token for
   * this host, or a host whose forge type could not be guessed.
   */
  const setupHint = useMemo(() => forgeSetupHint(prView), [prView]);
  const forgeSetupText = useMemo(() => {
    if (!setupHint) return null;
    return t(setupHint.key, {
      defaultValue: setupHint.fallback,
      host: setupHint.host,
    });
  }, [setupHint, t]);

  const generateMessage = useCallback(() => {
    if (busy !== null || changeCount === 0) return;
    const useSelection = selectedList.length > 0;
    const stagedOnly =
      !useSelection && stagedCount > 0 && stagedCount < changeCount;
    const paths = useSelection
      ? selectedList
      : stagedOnly
        ? (payload?.files ?? [])
            .filter((file) => file.staged)
            .map((file) => file.path)
        : [];
    setBusy("generate");
    setError(null);
    generateGitCommitMessage(token, sessionKey, paths, "", i18n.language)
      .then((next) => {
        const text = (next.message || "").trim();
        if (!text) {
          throw new Error(
            tx("dev.git.generateMessageEmpty", "The model returned an empty commit message"),
          );
        }
        setMessage(text);
      })
      .catch((err) => {
        const detail = err instanceof Error ? err.message : String(err);
        publishNotification({
          level: "error",
          source: "session",
          title: tx("dev.git.generateMessageFailed", "Could not write a commit message"),
          detail,
          key: "git:commit-message:error",
        });
        setError(detail);
      })
      .finally(() => setBusy(null));
  }, [
    busy,
    changeCount,
    payload?.files,
    selectedList,
    sessionKey,
    stagedCount,
    token,
    tx,
    i18n.language,
  ]);

  const runAction = useCallback(
    (action: CommitAction) => {
      setMenuOpen(false);
      if (action === "pull") {
        setBusy("pull");
        pullGitChanges(token, sessionKey)
          .then(() => {
            publishNotification({
              level: "success",
              source: "session",
              title: tx("dev.git.pulledNotice", "Pulled latest changes"),
              key: "git:pull",
            });
            refresh();
            onDidCommit?.();
            onWorkingTreeChanged?.();
          })
          .catch((err) => {
            const detail = err instanceof Error ? err.message : String(err);
            publishNotification({
              level: "error",
              source: "session",
              title: tx("dev.git.failedNotice", "Git operation failed"),
              detail,
              key: "git:pull:error",
            });
            setError(detail);
          })
          .finally(() => setBusy(null));
        return;
      }

      const wantsCommit = action !== "push" && action !== "publish";
      const text = message.trim();
      if (wantsCommit && !text) return;
      setBusy(action);
      const useSelection = selectedList.length > 0;
      const stagedOnly = !useSelection && stagedCount > 0 && stagedCount < changeCount;
      const shouldPush = action === "commit-push" || action === "push" || action === "publish";
      commitGitChanges(
        token,
        sessionKey,
        text,
        shouldPush,
        wantsCommit,
        "",
        useSelection
          ? { paths: selectedList }
          : stagedOnly
            ? { stagedOnly: true }
            : {},
      )
        .then((result) => {
          setMessage("");
          setSelected(new Set());
          const published = action === "publish" || result.pushed;
          if (result.committed && !result.pushed) setPendingPush(true);
          if (published) setPendingPush(false);
          publishNotification({
            level: "success",
            source: "session",
            title: published && !result.committed
              ? tx("dev.git.publishedNotice", "Branch published")
              : result.pushed
                ? tx("dev.git.pushedNotice", "Changes committed and pushed")
                : result.committed
                  ? tx("dev.git.committedNotice", "Changes committed")
                  : tx("dev.git.nothingNotice", "Nothing to commit"),
            detail: branch ? branch : undefined,
            key: "git:commit",
          });
          refresh();
          setHistoryBump((value) => value + 1);
          onDidCommit?.();
        })
        .catch((err) => {
          const detail = err instanceof Error ? err.message : String(err);
          publishNotification({
            level: "error",
            source: "session",
            title: tx("dev.git.failedNotice", "Git operation failed"),
            detail,
            key: "git:commit:error",
          });
          setError(detail);
        })
        .finally(() => setBusy(null));
    },
    [
      branch,
      changeCount,
      message,
      onDidCommit,
      onWorkingTreeChanged,
      refresh,
      selectedList,
      sessionKey,
      stagedCount,
      token,
      tx,
    ],
  );

  const notifyGitError = (err: unknown) => {
    const detail = err instanceof Error ? err.message : String(err);
    publishNotification({
      level: "error",
      source: "session",
      title: tx("dev.git.failedNotice", "Git operation failed"),
      detail,
      key: "git:action:error",
    });
    setError(detail);
  };

  const togglePath = (path: string, staged: boolean) => {
    setBusy("stage");
    stageGitPaths(token, sessionKey, [path], !staged)
      .then((next) => {
        setPayload(next);
        setSelected((prev) => {
          const copy = new Set(prev);
          if (!staged) copy.add(path);
          else copy.delete(path);
          return copy;
        });
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const stageAll = (stage: boolean) => {
    setMenuOpen(false);
    setBusy(stage ? "stage-all" : "unstage-all");
    stageGitPaths(token, sessionKey, [], stage, "", { all: true })
      .then((next) => {
        setPayload(next);
        setSelected(new Set());
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const discardPaths = (paths?: string[]) => {
    setDiscardTarget(!paths?.length ? "all" : paths);
  };

  const confirmDiscard = () => {
    const target = discardTarget;
    setDiscardTarget(null);
    if (target == null) return;
    const paths = target === "all" ? undefined : target;
    setMenuOpen(false);
    setBusy("discard");
    discardGitPaths(token, sessionKey, paths)
      .then((next) => {
        setPayload(next);
        setSelected(new Set());
        publishNotification({
          level: "success",
          source: "session",
          title: tx("dev.git.discardedNotice", "Changes discarded"),
          key: "git:discard",
        });
        onDidCommit?.();
        onWorkingTreeChanged?.(paths);
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const stashChanges = () => {
    setMenuOpen(false);
    setBusy("stash");
    stashGitChanges(token, sessionKey)
      .then((next) => {
        setPayload(next);
        setSelected(new Set());
        publishNotification({
          level: "success",
          source: "session",
          title: tx("dev.git.stashedNotice", "Changes stashed"),
          key: "git:stash",
        });
        onDidCommit?.();
        onWorkingTreeChanged?.();
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const stashPop = () => {
    setMenuOpen(false);
    setBusy("stash-pop");
    stashGitChanges(token, sessionKey, "", "pop")
      .then((next) => {
        setPayload(next);
        publishNotification({
          level: "success",
          source: "session",
          title: tx("dev.git.stashPoppedNotice", "Stash restored"),
          key: "git:stash-pop",
        });
        onDidCommit?.();
        onWorkingTreeChanged?.();
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const stashApplyAt = (entry: GitStashEntry) => {
    setMenuOpen(false);
    setBusy("stash-apply");
    stashGitChanges(token, sessionKey, "", "apply", entry.index)
      .then((next) => {
        setPayload(next);
        publishNotification({
          level: "success",
          source: "session",
          title: tx("dev.git.stashAppliedNotice", "Stash applied"),
          detail: entry.message || entry.label,
          key: "git:stash-apply",
        });
        onDidCommit?.();
        onWorkingTreeChanged?.();
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const stashDropAt = (entry: GitStashEntry) => {
    setStashDropTarget(entry);
  };

  const confirmStashDrop = () => {
    const entry = stashDropTarget;
    setStashDropTarget(null);
    if (!entry) return;
    setMenuOpen(false);
    setBusy("stash-drop");
    stashGitChanges(token, sessionKey, "", "drop", entry.index)
      .then((next) => {
        setPayload(next);
        publishNotification({
          level: "success",
          source: "session",
          title: tx("dev.git.stashDroppedNotice", "Stash dropped"),
          detail: entry.message || entry.label,
          key: "git:stash-drop",
        });
        onDidCommit?.();
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const pullRebase = () => {
    setMenuOpen(false);
    setBusy("pull-rebase");
    pullGitChanges(token, sessionKey, "", { rebase: true })
      .then((result) => {
        if (result.rebase_in_progress || result.merge_in_progress) {
          publishNotification({
            level: "warning",
            source: "session",
            title: tx("dev.git.rebaseInProgress", "Rebase in progress"),
            detail: result.detail,
            key: "git:pull-rebase:conflict",
          });
        } else {
          publishNotification({
            level: "success",
            source: "session",
            title: tx("dev.git.pulledRebaseNotice", "Pulled with rebase"),
            key: "git:pull-rebase",
          });
        }
        refresh();
        onDidCommit?.();
        onWorkingTreeChanged?.();
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const undoLastCommit = () => {
    setMenuOpen(false);
    setBusy("undo");
    undoLastGitCommit(token, sessionKey)
      .then((next) => {
        setPayload(next);
        setMessage("");
        setPendingPush(false);
        publishNotification({
          level: "success",
          source: "session",
          title: tx("dev.git.undoneCommitNotice", "Last commit undone"),
          detail: branch || undefined,
          key: "git:undo",
        });
        refresh();
        setHistoryBump((value) => value + 1);
        onDidCommit?.();
        onWorkingTreeChanged?.();
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const syncChanges = () => {
    setMenuOpen(false);
    setBusy("sync");
    syncGitChanges(token, sessionKey)
      .then((result) => {
        setPendingPush(false);
        publishNotification({
          level: "success",
          source: "session",
          title: tx("dev.git.syncedNotice", "Branch synced"),
          detail: branch || undefined,
          key: "git:sync",
        });
        refresh();
        setHistoryBump((value) => value + 1);
        onDidCommit?.();
        onWorkingTreeChanged?.();
        return result;
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const amendCommit = () => {
    setMenuOpen(false);
    setBusy("amend");
    const useSelection = selectedList.length > 0;
    const stagedOnly = !useSelection && stagedCount > 0 && stagedCount < changeCount;
    commitGitChanges(
      token,
      sessionKey,
      message.trim(),
      false,
      true,
      "",
      {
        amend: true,
        ...(useSelection
          ? { paths: selectedList }
          : stagedOnly
            ? { stagedOnly: true }
            : {}),
      },
    )
      .then(() => {
        setMessage("");
        setSelected(new Set());
        publishNotification({
          level: "success",
          source: "session",
          title: tx("dev.git.amendedNotice", "Commit amended"),
          detail: branch || undefined,
          key: "git:amend",
        });
        refresh();
        setHistoryBump((value) => value + 1);
        onDidCommit?.();
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const fetchRemote = () => {
    setMenuOpen(false);
    setBusy("fetch");
    fetchGitRemote(token, sessionKey)
      .then((next) => {
        setPayload(next);
        publishNotification({
          level: "success",
          source: "session",
          title: tx("dev.git.fetchedNotice", "Fetched from remotes"),
          key: "git:fetch",
        });
      })
      .catch(notifyGitError)
      .finally(() => setBusy(null));
  };

  const openBranchMenu = () => {
    setBranchOpen((open) => !open);
    void gitBranchOp(token, sessionKey, "list")
      .then((result) => setBranches(result.branches ?? []))
      .catch(() => setBranches([]));
  };

  const switchBranch = (name: string) => {
    setBusy("branch");
    gitBranchOp(token, sessionKey, "checkout", name)
      .then(() => {
        setBranchOpen(false);
        refresh();
        onDidCommit?.();
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  const createBranch = () => {
    const name = newBranch.trim();
    if (!name) return;
    setBusy("branch");
    gitBranchOp(token, sessionKey, "create", name)
      .then(() => {
        setNewBranch("");
        setBranchOpen(false);
        refresh();
        onDidCommit?.();
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  const openPr = (draft: boolean = true) => {
    setMenuOpen(false);
    setBusy("pr");
    createGithubPr(token, sessionKey, {
      title: message.trim() || branch,
      draft,
    })
      .then((result) => {
        const sha = result.head_sha ? ` @ ${result.head_sha}` : "";
        publishNotification({
          level: "success",
          source: "session",
          title: result.created
            ? draft
              ? forge.draftOpenedNotice
              : forge.openedNotice
            : forge.reusedNotice,
          detail: result.pr_url
            ? `${result.pr_url}${sha}`
            : sha.trim() || undefined,
          key: "git:pr",
        });
        refresh();
      })
      .catch((err) => {
        const detail = err instanceof Error ? err.message : String(err);
        publishNotification({
          level: "error",
          source: "session",
          title: forge.failedNotice,
          // A missing token is a setup step, not a failure to decipher:
          // say which host needs one and where to add it.
          detail: forgeSetupText ?? detail,
          key: "git:pr:error",
        });
        setError(forgeSetupText ?? detail);
      })
      .finally(() => setBusy(null));
  };

  const commitAndCreatePr = () => {
    setMenuOpen(false);
    const text = message.trim();
    if (!text || changeCount === 0) return;
    setBusy("commit-pr");
    const useSelection = selectedList.length > 0;
    const stagedOnly = !useSelection && stagedCount > 0 && stagedCount < changeCount;
    commitGitChanges(
      token,
      sessionKey,
      text,
      false,
      true,
      "",
      useSelection
        ? { paths: selectedList }
        : stagedOnly
          ? { stagedOnly: true }
          : {},
    )
      .then(() =>
        createGithubPr(token, sessionKey, {
          title: text || branch,
          draft: true,
        }),
      )
      .then((result) => {
        setMessage("");
        setSelected(new Set());
        const sha = result.head_sha ? ` @ ${result.head_sha}` : "";
        publishNotification({
          level: "success",
          source: "session",
          title: forge.commitNotice,
          detail: result.pr_url
            ? `${result.pr_url}${sha}`
            : sha.trim() || undefined,
          key: "git:commit-pr",
        });
        refresh();
        setHistoryBump((value) => value + 1);
        onDidCommit?.();
      })
      .catch((err) => {
        const detail = err instanceof Error ? err.message : String(err);
        publishNotification({
          level: "error",
          source: "session",
          title: tx("dev.git.failedNotice", "Git operation failed"),
          detail: forgeSetupText ?? detail,
          key: "git:commit-pr:error",
        });
        setError(forgeSetupText ?? detail);
      })
      .finally(() => setBusy(null));
  };

  const reviewChanges = () => {
    setBusy("review");
    fetchReviewPrompt(token, sessionKey)
      .then((result) => {
        if (!result.available || !result.prompt) {
          setError(result.detail || tx("dev.git.reviewUnavailable", "Nothing to review."));
          return;
        }
        if (onRunAction) {
          onRunAction(result.prompt);
        } else {
          void navigator.clipboard?.writeText(result.prompt);
        }
        publishNotification({
          level: "info",
          source: "session",
          title: tx("dev.git.reviewStarted", "Review prompt ready"),
          detail: t("dev.git.reviewFiles", {
            defaultValue: "{{n}} file(s) under review",
            n: result.files.length,
          }),
          key: "git:review",
        });
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  const explainCi = () => {
    setBusy("explain-ci");
    fetchFixCiPrompt(token, sessionKey, "", "explain")
      .then((result) => {
        if (onRunAction) {
          onRunAction(result.prompt);
        } else {
          void navigator.clipboard?.writeText(result.prompt);
        }
        publishNotification({
          level: "info",
          source: "session",
          title: tx("dev.git.explainCiStarted", "CI diagnosis prompt ready"),
          detail: result.detail || undefined,
          key: "git:explain-ci",
        });
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  const fixCi = () => {
    setBusy("fix-ci");
    fetchFixCiPrompt(token, sessionKey)
      .then((result) => {
        if (onRunAction) {
          onRunAction(result.prompt);
        } else {
          void navigator.clipboard?.writeText(result.prompt);
        }
        publishNotification({
          level: "info",
          source: "session",
          title: tx("dev.git.fixCiStarted", "Fix CI prompt ready"),
          detail: result.failing
            ? tx("dev.git.fixCiFailing", "{{n}} failing check(s)").replace(
                "{{n}}",
                String(result.failing),
              )
            : result.detail || undefined,
          key: "git:fix-ci",
        });
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  const runConflict = (action: "abort" | "continue") => {
    setBusy(action);
    gitConflictAction(token, sessionKey, action)
      .then(() => {
        refresh();
        onDidCommit?.();
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  const isRepo = Boolean(payload?.is_repo);
  const hasUpstream = Boolean(payload?.has_upstream);
  const stashCount = payload?.stash_count ?? 0;
  const stashes = payload?.stashes ?? [];
  const canAmend = canRewriteLastCommit({ isRepo, hasUpstream, ahead });
  const canUndo = canAmend;
  const canCommit = isRepo && changeCount > 0 && message.trim().length > 0;
  const canPush = isRepo && (ahead > 0 || !hasUpstream);
  const primary = primaryGitAction({
    changeCount,
    ahead,
    hasUpstream,
    pendingPush,
  });
  const primaryDisabled =
    busy !== null
    || conflictBusy
    || !canRunPrimaryGitAction({
      isRepo,
      changeCount,
      ahead,
      hasUpstream,
      hasMessage: message.trim().length > 0,
      pendingPush,
    });
  const primaryLabel =
    primary === "publish"
      ? tx("dev.git.publishBranch", "Publish Branch")
      : primary === "push"
        ? tx("dev.git.push", "Push")
        : tx("dev.git.commit", "Commit");
  const ciState = ci?.checks?.state;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ConfirmDialog
        open={discardTarget !== null}
        title={tx("dev.git.discardTitle", "Discard changes?")}
        description={
          discardTarget === "all"
            ? tx(
                "dev.git.discardAllConfirm",
                "Discard all uncommitted changes? This cannot be undone.",
              )
            : tx(
                "dev.git.discardConfirm",
                "Discard changes in this file? This cannot be undone.",
              )
        }
        confirmLabel={tx("dev.git.discard", "Discard Changes")}
        onCancel={() => setDiscardTarget(null)}
        onConfirm={confirmDiscard}
      />
      <ConfirmDialog
        open={stashDropTarget !== null}
        title={tx("dev.git.stashDropTitle", "Drop this stash?")}
        description={tx(
          "dev.git.stashDropConfirm",
          "Drop this stash? This cannot be undone.",
        )}
        confirmLabel={tx("dev.git.stashDrop", "Drop")}
        onCancel={() => setStashDropTarget(null)}
        onConfirm={confirmStashDrop}
      />
      <div className="shrink-0 px-2 pb-1 pt-1.5">
        <div className="flex rounded-lg bg-muted/60 p-0.5">
          {(
            [
              {
                id: "changes" as const,
                label:
                  tx("dev.git.changes", "Changes") +
                  (changeCount > 0 ? ` · ${changeCount}` : ""),
              },
              { id: "history" as const, label: tx("dev.git.history", "History") },
              {
                id: "checkpoints" as const,
                label: tx("dev.git.checkpoints", "Checkpoints"),
              },
            ]
          ).map((entry) => (
            <button
              key={entry.id}
              type="button"
              onClick={() => setTab(entry.id)}
              className={cn(
                "flex-1 cursor-pointer truncate rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
                tab === entry.id
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
              aria-pressed={tab === entry.id}
            >
              {entry.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "history" ? (
        <DevGitTimeline
          token={token}
          sessionKey={sessionKey}
          refreshSignal={historyBump}
        />
      ) : tab === "checkpoints" ? (
        <DevCheckpointsPanel
          token={token}
          sessionKey={sessionKey}
          onRestored={() => {
            refresh();
            setHistoryBump((n) => n + 1);
            onDidCommit?.();
          }}
        />
      ) : (
        <>
          <div className="flex shrink-0 items-center justify-between gap-2 px-2 pb-1.5 pt-1">
            <div className="relative min-w-0" ref={branchRef}>
              <button
                type="button"
                onClick={openBranchMenu}
                disabled={!isRepo || busy !== null}
                className="flex max-w-full items-center gap-1 rounded-md bg-muted/70 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                <GitBranch className="h-3 w-3 shrink-0" aria-hidden />
                <span className="max-w-[110px] truncate">{branch || "-"}</span>
                {ahead > 0 ? (
                  <span className="flex items-center text-amber-500">
                    {ahead}
                    <ArrowUp className="h-3 w-3" aria-hidden />
                  </span>
                ) : null}
                {behind > 0 ? (
                  <span className="flex items-center text-sky-500">
                    {behind}
                    <ArrowDown className="h-3 w-3" aria-hidden />
                  </span>
                ) : null}
                <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
              </button>
              {branchOpen ? (
                <div className="absolute left-0 top-full z-30 mt-1 w-56 rounded-md border border-border bg-popover p-1 shadow-md">
                  <div className="flex gap-1 border-b border-border/50 p-1">
                    <input
                      value={newBranch}
                      onChange={(event) => setNewBranch(event.target.value)}
                      placeholder={tx("dev.git.newBranch", "New branch")}
                      className="min-w-0 flex-1 rounded border border-border/60 bg-background px-1.5 py-1 text-[11px]"
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          createBranch();
                        }
                      }}
                    />
                    <button
                      type="button"
                      onClick={createBranch}
                      className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                      title={tx("dev.git.createBranch", "Create branch")}
                    >
                      <Plus className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  </div>
                  <ul className="max-h-48 overflow-y-auto py-0.5">
                    {branches.map((name) => (
                      <li key={name}>
                        <button
                          type="button"
                          onClick={() => switchBranch(name)}
                          className={cn(
                            "flex w-full items-center gap-1 truncate rounded px-2 py-1 text-left text-[11px]",
                            name === branch
                              ? "bg-muted text-foreground"
                              : "text-muted-foreground hover:bg-muted/70 hover:text-foreground",
                          )}
                        >
                          {name === branch ? (
                            <CircleDot className="h-3 w-3 shrink-0" aria-hidden />
                          ) : (
                            <span className="w-3" />
                          )}
                          <span className="truncate">{name}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
            <DevProveChange token={token} projectPath={projectPath ?? null} />
            <button
              type="button"
              onClick={refresh}
              className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={tx("dev.git.refresh", "Refresh changes")}
            >
              {loading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              )}
            </button>
          </div>

          {conflictBusy ? (
            <div className="mx-2 mb-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5">
              <p className="text-[11px] font-medium text-amber-700 dark:text-amber-300">
                {payload?.rebase_in_progress
                  ? tx("dev.git.rebaseInProgress", "Rebase in progress")
                  : tx("dev.git.mergeInProgress", "Merge in progress")}
              </p>
              <div className="mt-1 flex gap-1">
                <button
                  type="button"
                  onClick={() => runConflict("continue")}
                  disabled={busy !== null}
                  className="rounded border border-border/60 px-1.5 py-0.5 text-[11px] hover:bg-muted"
                >
                  {tx("dev.git.conflictContinue", "Continue")}
                </button>
                <button
                  type="button"
                  onClick={() => runConflict("abort")}
                  disabled={busy !== null}
                  className="rounded border border-border/60 px-1.5 py-0.5 text-[11px] hover:bg-muted"
                >
                  {tx("dev.git.conflictAbort", "Abort")}
                </button>
              </div>
            </div>
          ) : null}

          {isRepo ? (
            <div className="shrink-0 space-y-1.5 border-b border-border/50 px-2 pb-2">
              <div className="relative">
              <textarea
                ref={messageBoxRef}
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                    event.preventDefault();
                    runAction(primary);
                  }
                }}
                placeholder={
                  changeCount > 0
                    ? selectedList.length > 0
                      ? tx(
                          "dev.git.messageSelectedPlaceholder",
                          "Commit message (selected files)",
                        )
                      : stagedCount > 0 && stagedCount < changeCount
                        ? tx(
                            "dev.git.messageStagedPlaceholder",
                            "Commit message (staged only)",
                          )
                        : tx("dev.git.messagePlaceholder", "Commit message")
                    : tx("dev.git.cleanPlaceholder", "Working tree clean")
                }
                rows={1}
                disabled={busy !== null || changeCount === 0}
                className="w-full resize-none overflow-y-auto rounded-md border border-border/60 bg-background py-1.5 pl-2 pr-16 text-[12px] leading-5 text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
              />
              <button
                type="button"
                onClick={generateMessage}
                disabled={busy !== null || changeCount === 0}
                title={tx(
                  "dev.git.generateMessageHint",
                  "Write a commit message from the current changes",
                )}
                aria-label={tx(
                  "dev.git.generateMessageHint",
                  "Write a commit message from the current changes",
                )}
                data-testid="git-generate-commit-message"
                className="absolute right-1 top-1 inline-flex items-center gap-1 rounded-md px-2 py-1 text-[13px] font-bold tracking-wide text-foreground/90 transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === "generate" ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <>
                    <Sparkles className="h-4 w-4" aria-hidden />
                    IA
                  </>
                )}
              </button>
              </div>
              <div className="relative flex" ref={menuRef}>
                <button
                  type="button"
                  onClick={() => runAction(primary)}
                  disabled={primaryDisabled}
                  className="flex flex-1 items-center justify-center gap-1.5 rounded-l-md bg-primary px-2 py-1.5 text-[12px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {busy ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : primary === "publish" ? (
                    <CloudUpload className="h-3.5 w-3.5" aria-hidden />
                  ) : (
                    <Check className="h-3.5 w-3.5" aria-hidden />
                  )}
                  {primaryLabel}
                </button>
                <button
                  type="button"
                  onClick={() => setMenuOpen((open) => !open)}
                  disabled={busy !== null}
                  className="flex items-center rounded-r-md border-l border-primary-foreground/20 bg-primary px-1.5 text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                  aria-label={tx("dev.git.moreActions", "More actions")}
                  aria-expanded={menuOpen}
                >
                  <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                </button>
                {menuOpen ? (
                  <div className="absolute right-0 top-full z-20 mt-1 max-h-[min(70vh,28rem)] w-60 overflow-y-auto rounded-md border border-border bg-popover p-1 shadow-md">
                    <button
                      type="button"
                      onClick={() => runAction("commit")}
                      disabled={!canCommit}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.commit", "Commit")}
                    </button>
                    <button
                      type="button"
                      onClick={() => runAction("commit-push")}
                      disabled={!canCommit}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.commitPush", "Commit & Push")}
                    </button>
                    <button
                      type="button"
                      onClick={commitAndCreatePr}
                      disabled={!canCommit}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {forge.commitAndCreate}
                    </button>
                    <button
                      type="button"
                      onClick={amendCommit}
                      disabled={busy !== null || !canAmend}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.amend", "Commit (Amend)")}
                    </button>
                    <button
                      type="button"
                      onClick={undoLastCommit}
                      disabled={busy !== null || !canUndo}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.undoCommit", "Undo Last Commit")}
                    </button>
                    {hasUpstream ? (
                      <button
                        type="button"
                        onClick={() => runAction("push")}
                        disabled={!canPush}
                        className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {tx("dev.git.push", "Push")}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => runAction("publish")}
                        disabled={busy !== null}
                        className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {tx("dev.git.publishBranch", "Publish Branch")}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => openPr(true)}
                      disabled={busy !== null || !isRepo}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {forge.create}
                    </button>
                    <button
                      type="button"
                      onClick={() => runAction("pull")}
                      disabled={busy !== null}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.pull", "Pull")}
                    </button>
                    <button
                      type="button"
                      onClick={pullRebase}
                      disabled={busy !== null || conflictBusy}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.pullRebase", "Pull (Rebase)")}
                    </button>
                    <button
                      type="button"
                      onClick={syncChanges}
                      disabled={busy !== null || conflictBusy}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.sync", "Sync")}
                    </button>
                    <button
                      type="button"
                      onClick={fetchRemote}
                      disabled={busy !== null}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.fetch", "Fetch")}
                    </button>
                    <div className="my-1 border-t border-border/60" />
                    <button
                      type="button"
                      onClick={() => stageAll(true)}
                      disabled={busy !== null || changeCount === 0}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.stageAll", "Stage All Changes")}
                    </button>
                    <button
                      type="button"
                      onClick={() => stageAll(false)}
                      disabled={busy !== null || stagedCount === 0}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.unstageAll", "Unstage All Changes")}
                    </button>
                    <button
                      type="button"
                      onClick={stashChanges}
                      disabled={busy !== null || changeCount === 0}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.stash", "Stash")}
                    </button>
                    <button
                      type="button"
                      onClick={stashPop}
                      disabled={busy !== null || stashCount === 0}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.stashPop", "Stash Pop")}
                    </button>
                    {stashes.length > 0 ? (
                      <div className="mt-0.5 border-t border-border/60 pt-0.5">
                        <p className="px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                          {tx("dev.git.stashList", "Stashes")}
                        </p>
                        {stashes.map((entry) => (
                          <div
                            key={`${entry.index}-${entry.label}`}
                            className="flex items-start gap-1 rounded px-1 py-1 hover:bg-muted/60"
                          >
                            <span
                              className="min-w-0 flex-1 truncate px-1 text-[11px] text-popover-foreground"
                              title={entry.label}
                            >
                              {entry.message || entry.label}
                            </span>
                            <button
                              type="button"
                              onClick={() => stashApplyAt(entry)}
                              disabled={busy !== null}
                              className="shrink-0 rounded px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                            >
                              {tx("dev.git.stashApply", "Apply")}
                            </button>
                            <button
                              type="button"
                              onClick={() => stashDropAt(entry)}
                              disabled={busy !== null}
                              className="shrink-0 rounded px-1.5 py-0.5 text-[10px] text-destructive hover:bg-muted disabled:opacity-50"
                            >
                              {tx("dev.git.stashDrop", "Drop")}
                            </button>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => discardPaths()}
                      disabled={busy !== null || changeCount === 0}
                      className="w-full rounded px-2 py-1.5 text-left text-[12px] text-destructive transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {tx("dev.git.discardAll", "Discard All Changes")}
                    </button>
                  </div>
                ) : null}
              </div>

              <div className="flex flex-wrap gap-1">
                <button
                  type="button"
                  onClick={() => openPr(true)}
                  disabled={busy !== null || !isRepo}
                  className="inline-flex items-center gap-1 rounded border border-border/60 px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                  <GitPullRequest className="h-3 w-3" aria-hidden />
                  {forge.draft}
                </button>
                {changeCount > 0 ? (
                  <button
                    type="button"
                    onClick={reviewChanges}
                    disabled={busy !== null || !isRepo}
                    className="inline-flex items-center gap-1 rounded border border-border/60 px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                    title={tx(
                      "dev.git.reviewHint",
                      "Asks the agent to review the pending diff (bugs, security, tests) before committing.",
                    )}
                  >
                    <ScanSearch className="h-3 w-3" aria-hidden />
                    {tx("dev.git.review", "Review")}
                  </button>
                ) : null}
                {ciState === "failure" || (ci?.checks?.failing ?? 0) > 0 ? (
                  <>
                    <button
                      type="button"
                      onClick={fixCi}
                      disabled={busy !== null}
                      className="inline-flex items-center gap-1 rounded border border-amber-500/40 px-1.5 py-0.5 text-[11px] text-amber-700 hover:bg-amber-500/10 dark:text-amber-300 disabled:opacity-50"
                    >
                      <Wrench className="h-3 w-3" aria-hidden />
                      {tx("dev.git.fixCi", "Fix CI")}
                    </button>
                    <button
                      type="button"
                      onClick={explainCi}
                      disabled={busy !== null}
                      className="inline-flex items-center gap-1 rounded border border-amber-500/40 px-1.5 py-0.5 text-[11px] text-amber-700 hover:bg-amber-500/10 dark:text-amber-300 disabled:opacity-50"
                      title={tx(
                        "dev.git.explainCiHint",
                        "Asks the agent to diagnose the CI failure without changing anything.",
                      )}
                    >
                      <FileSearch className="h-3 w-3" aria-hidden />
                      {tx("dev.git.explainCi", "Explain CI")}
                    </button>
                  </>
                ) : null}
                {prView?.pr?.url ? (
                  <a
                    href={prView.pr.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 rounded border border-border/60 px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <ExternalLink className="h-3 w-3" aria-hidden />
                    #{prView.pr.number}
                    {ciState ? ` · ${ciState}` : ""}
                  </a>
                ) : null}
              </div>

              {forgeSetupText ? (
                <p className="text-[11px] leading-4 text-muted-foreground">
                  {forgeSetupText}
                  {setupHint?.env ? (
                    <>
                      {" "}
                      <span className="text-muted-foreground/70">
                        {t("dev.git.forgeTokenEnvHint", {
                          defaultValue: "You can also export {{env}}.",
                          env: setupHint.env,
                        })}
                      </span>
                    </>
                  ) : null}
                </p>
              ) : null}

              {changeCount > 0 ? (
                <p className="text-[11px] text-amber-500">
                  {t("dev.git.uncommittedNotice", {
                    defaultValue: "{{n}} file(s) with uncommitted work",
                    n: changeCount,
                  })}
                  {stagedCount > 0
                    ? ` · ${t("dev.git.stagedCount", {
                        defaultValue: "{{n}} staged",
                        n: stagedCount,
                      })}`
                    : ""}
                </p>
              ) : ahead > 0 ? (
                <p className="text-[11px] text-amber-500">
                  {t("dev.git.unpushedNotice", {
                    defaultValue: "{{n}} commit(s) not pushed",
                    n: ahead,
                  })}
                </p>
              ) : null}
            </div>
          ) : null}

          <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2 pt-1">
            {error ? (
              <p className="px-2 py-1.5 text-[12px] text-destructive">{error}</p>
            ) : null}
            {!error && payload && !payload.is_repo ? (
              <div className="flex flex-col items-center gap-2 px-3 py-6 text-center text-muted-foreground">
                <GitBranch className="h-6 w-6 opacity-40" aria-hidden />
                <p className="text-[12px] leading-5">
                  {tx("dev.git.notRepo", "This project is not a git repository.")}
                </p>
              </div>
            ) : null}
            {!error && payload?.is_repo && payload.files.length === 0 ? (
              <p className="px-2 py-1.5 text-[12px] text-muted-foreground">
                {tx("dev.git.clean", "Working tree clean.")}
              </p>
            ) : null}
            {payload?.is_repo
              ? payload.files.map((file) => (
                  <div
                    key={file.path}
                    className={cn(
                      "group flex w-full items-center gap-1.5 rounded-md px-1 py-1",
                      selectedPath === file.path
                        ? "bg-background shadow-sm ring-1 ring-border/60"
                        : "hover:bg-muted/60",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={file.staged || selected.has(file.path)}
                      onChange={() => togglePath(file.path, file.staged)}
                      disabled={busy !== null}
                      className="h-3.5 w-3.5 shrink-0 accent-foreground"
                      aria-label={tx("dev.git.stageToggle", "Stage file")}
                      title={
                        file.staged
                          ? tx("dev.git.unstage", "Unstage")
                          : tx("dev.git.stage", "Stage")
                      }
                    />
                    <button
                      type="button"
                      onClick={() => onShowDiff(file.path)}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      title={file.path}
                    >
                      <span
                        className={cn(
                          "w-4 shrink-0 text-center font-mono text-[11px] font-bold",
                          file.status === "deleted" || file.status === "conflict"
                            ? "text-destructive"
                            : "text-muted-foreground",
                        )}
                      >
                        {STATUS_LETTER[file.status] ?? "M"}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-foreground">
                        {file.path}
                      </span>
                    </button>
                    <div className="flex shrink-0 items-center opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                      <button
                        type="button"
                        onClick={() => togglePath(file.path, file.staged)}
                        disabled={busy !== null}
                        className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                        title={
                          file.staged
                            ? tx("dev.git.unstage", "Unstage")
                            : tx("dev.git.stage", "Stage")
                        }
                        aria-label={
                          file.staged
                            ? tx("dev.git.unstage", "Unstage")
                            : tx("dev.git.stage", "Stage")
                        }
                      >
                        {file.staged ? (
                          <Minus className="h-3.5 w-3.5" aria-hidden />
                        ) : (
                          <Plus className="h-3.5 w-3.5" aria-hidden />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => discardPaths([file.path])}
                        disabled={busy !== null}
                        className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-destructive disabled:opacity-50"
                        title={tx("dev.git.discard", "Discard Changes")}
                        aria-label={tx("dev.git.discard", "Discard Changes")}
                      >
                        <Undo2 className="h-3.5 w-3.5" aria-hidden />
                      </button>
                    </div>
                  </div>
                ))
              : null}
          </div>
        </>
      )}
    </div>
  );
}
