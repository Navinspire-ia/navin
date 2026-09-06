import { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  CircleCheck,
  CircleDot,
  Download,
  ExternalLink,
  Github,
  KeyRound,
  Loader2,
  MessageSquare,
  RefreshCw,
  Settings2,
  User,
  Wrench,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  fetchGithubIssues,
  setGithubIssuesRepo,
  updateBoard,
  type ForgeTokenSetup,
  type GithubCliInstall,
  type GithubIssue,
  type GithubIssuesPayload,
} from "@/lib/api";
import { copyTextOrNotify } from "@/lib/clipboard";
import { forgeEnvVar } from "@/lib/forge";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

type IssueState = "open" | "closed" | "all";

/**
 * What replaces "install the gh CLI" on GitLab and Forgejo: the panel reads
 * issues over REST, so the only thing that can be missing is a token.
 */
function TokenSetupCard({
  setup,
  forgeLabel,
  tx,
}: {
  setup: ForgeTokenSetup;
  forgeLabel: string;
  tx: (key: string, fallback: string) => string;
}) {
  const { t } = useTranslation();
  const env = setup.env?.[0] || forgeEnvVar(setup.forge) || "";
  return (
    <div className="mx-auto flex max-w-[28rem] flex-col items-stretch gap-3 text-left">
      <div className="flex items-center gap-2 text-foreground">
        <KeyRound className="h-5 w-5 opacity-70" aria-hidden />
        <p className="text-sm font-semibold">
          {t("projectHome.issues.tokenMissing", {
            host: setup.host,
            defaultValue: "No token for {{host}}. Add it in Settings > Git.",
          })}
        </p>
      </div>
      <p className="text-[12.5px] leading-relaxed text-muted-foreground">
        {t("projectHome.issues.tokenWhy", {
          forge: forgeLabel,
          defaultValue:
            "Navin reads {{forge}} issues over its REST API: a personal access token with repository access is all it needs, no CLI to install.",
        })}
      </p>
      {env ? (
        <div className="rounded-lg border border-border/60 bg-background/80 px-3 py-2">
          <p className="mb-1 text-[10.5px] font-medium uppercase tracking-wide text-muted-foreground">
            {tx("projectHome.issues.tokenEnv", "Or export it in your shell")}
          </p>
          <button
            type="button"
            onClick={() => void copyTextOrNotify(`export ${env}=<token>`)}
            title={tx("projectHome.issues.copyCommand", "Copy command")}
            className="w-full truncate text-left font-mono text-[12px] text-foreground hover:underline"
          >
            {`export ${env}=<token>`}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function GhInstallCard({
  install,
  tx,
}: {
  install: GithubCliInstall;
  tx: (key: string, fallback: string) => string;
}) {
  const { t } = useTranslation();
  const copy = useCallback((text: string) => {
    void copyTextOrNotify(text);
  }, []);
  return (
    <div className="mx-auto flex max-w-[28rem] flex-col items-stretch gap-3 text-left">
      <div className="flex items-center gap-2 text-foreground">
        <Github className="h-5 w-5 opacity-70" aria-hidden />
        <p className="text-sm font-semibold">
          {tx("projectHome.issues.ghMissing", "The GitHub CLI (gh) is not installed on this machine.")}
        </p>
      </div>
      <p className="text-[12.5px] leading-relaxed text-muted-foreground">
        {t("projectHome.issues.ghDetected", {
          os: install.label,
          defaultValue:
            "Detected host: {{os}}. Install it, then authenticate, then refresh this panel.",
        })}
      </p>
      <div className="rounded-lg border border-border/60 bg-background/80 px-3 py-2">
        <p className="mb-1 text-[10.5px] font-medium uppercase tracking-wide text-muted-foreground">
          {install.label}
        </p>
        <button
          type="button"
          onClick={() => copy(install.command)}
          title={tx("projectHome.issues.copyCommand", "Copy command")}
          className="w-full truncate text-left font-mono text-[12px] text-foreground hover:underline"
        >
          {install.command}
        </button>
      </div>
      <div className="rounded-lg border border-border/60 bg-background/80 px-3 py-2">
        <p className="mb-1 text-[10.5px] font-medium uppercase tracking-wide text-muted-foreground">
          {tx("projectHome.issues.ghAuth", "Then sign in")}
        </p>
        <button
          type="button"
          onClick={() => copy(install.auth_command)}
          title={tx("projectHome.issues.copyCommand", "Copy command")}
          className="w-full truncate text-left font-mono text-[12px] text-foreground hover:underline"
        >
          {install.auth_command}
        </button>
      </div>
      <details className="text-[12px] text-muted-foreground">
        <summary className="cursor-pointer select-none hover:text-foreground">
          {tx("projectHome.issues.ghOtherOs", "Other systems (Windows, macOS, Linux deb / rpm)")}
        </summary>
        <ul className="mt-2 space-y-1.5">
          {install.alternatives.map((option) => (
            <li key={option.id}>
              <span className="block text-[10.5px] font-medium uppercase tracking-wide">
                {option.label}
              </span>
              <button
                type="button"
                onClick={() => copy(option.command)}
                className="w-full truncate text-left font-mono text-[11.5px] text-foreground/90 hover:underline"
              >
                {option.command}
              </button>
            </li>
          ))}
        </ul>
      </details>
      <a
        href={install.docs_url}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground hover:underline"
      >
        {tx("projectHome.issues.ghDocs", "Official installers on cli.github.com")}
        <ExternalLink className="h-3 w-3" aria-hidden />
      </a>
    </div>
  );
}

function relativeDay(iso: string | null): string {
  if (!iso) return "";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return "";
  const days = Math.max(0, Math.floor((Date.now() - ts) / 86_400_000));
  if (days === 0) return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (days < 30) return `${days}d`;
  return new Date(ts).toLocaleDateString();
}

/**
 * Issues of the bound project, read over the forge REST API (GitHub, GitLab
 * or Forgejo/Gitea), with a one-click import of open issues into the board
 * and a per-issue "Fix with agent" action. Used by Project Home's Issues tab.
 *
 * The tracker is configurable: a project may follow the issues of another
 * repository (an upstream, a public tracker), on another forge, instead of
 * its own remote. The choice is stored per project, so the import and the
 * agent follow it too.
 */
export function ProjectIssuesPanel({
  sessionKey,
  projectPath,
  onRunAction,
}: {
  sessionKey: string | null;
  /** Bound project directory; overrides the session workspace for `gh`. */
  projectPath?: string | null;
  onRunAction?: (text: string) => void;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [payload, setPayload] = useState<GithubIssuesPayload | null>(null);
  const [state, setState] = useState<IssueState>("open");
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editingRepo, setEditingRepo] = useState(false);
  const [repoDraft, setRepoDraft] = useState("");
  const [savingRepo, setSavingRepo] = useState(false);

  const repo = payload?.repo ?? null;
  // GitHub, GitLab and Forgejo all call these issues; only the host changes.
  const forgeLabel = payload?.forge_label || "GitHub";

  const load = useCallback(
    async (nextState: IssueState = state) => {
      if (!token || !sessionKey) return;
      setLoading(true);
      setError(null);
      try {
        const data = await fetchGithubIssues(
          token,
          sessionKey,
          nextState,
          "",
          projectPath,
        );
        setPayload(data);
        if (!data.ok) setError(data.detail);
      } catch (err) {
        setPayload(null);
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [token, sessionKey, state, projectPath],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const importToBoard = useCallback(async () => {
    if (!token || !sessionKey || importing) return;
    setImporting(true);
    setError(null);
    setNotice(null);
    try {
      // Import exactly the tracker on screen, not the session folder's remote.
      await updateBoard(token, sessionKey, { action: "sync_github", repo });
      setNotice(tx("projectHome.issues.imported", "Open issues imported to the task board."));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setImporting(false);
    }
  }, [token, sessionKey, importing, load, repo, tx]);

  const saveRepo = useCallback(
    async (next: string) => {
      if (!token || !sessionKey || savingRepo) return;
      setSavingRepo(true);
      setError(null);
      setNotice(null);
      try {
        const result = await setGithubIssuesRepo(token, sessionKey, next, "", projectPath);
        setEditingRepo(false);
        setNotice(
          result.repo
            ? t("projectHome.issues.repoSaved", {
                repo: result.repo,
                defaultValue: "Issues now read from {{repo}}.",
              })
            : tx("projectHome.issues.repoCleared", "Issues now follow this project's git remote."),
        );
        await load();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSavingRepo(false);
      }
    },
    [token, sessionKey, savingRepo, projectPath, load, t, tx],
  );

  const fixWithAgent = useCallback(
    (issue: GithubIssue) => {
      if (!onRunAction) return;
      const brief = t("projectHome.issues.fixPrompt", {
        number: issue.number,
        title: issue.title,
        url: issue.url,
        forge: forgeLabel,
        defaultValue:
          "Fix {{forge}} issue #{{number}} - \"{{title}}\" ({{url}}). Reproduce the problem, implement a clean fix on an isolated task branch, run the relevant tests until they are fully green, commit, and open a pull request if PR-on-done is enabled. Only then close the issue through the board (board action=sync_github closes it with a comment referencing the fix). Never close the issue if the fix is not tested and committed.",
      });
      // An issue from another repository would otherwise be looked up (and
      // closed) in this folder's remote.
      const scope = repo
        ? t("projectHome.issues.fixPromptRepo", {
            repo,
            defaultValue:
              " This issue lives in {{repo}}, not in this project's remote: target that repository for every issue operation. The fix itself belongs to this project's checkout.",
          })
        : "";
      onRunAction(`${brief}${scope}`);
      setNotice(
        t("projectHome.issues.fixSent", {
          number: issue.number,
          defaultValue: "Issue #{{number}} sent to the agent for a fix.",
        }),
      );
    },
    [onRunAction, repo, forgeLabel, t],
  );

  const issues: GithubIssue[] = useMemo(() => payload?.issues ?? [], [payload]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border/60 px-3 py-2">
        <Github className="h-4 w-4 text-muted-foreground" aria-hidden />
        <span className="text-[13px] font-semibold">
          {t("projectHome.issues.titleForge", {
            forge: forgeLabel,
            defaultValue: "{{forge}} issues",
          })}
        </span>
        {payload ? (
          <span className="text-[11.5px] text-muted-foreground">{issues.length}</span>
        ) : null}
        <div className="ml-1 flex items-center gap-0.5 rounded-md border border-border/60 p-0.5">
          {(["open", "closed", "all"] as IssueState[]).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => {
                setState(option);
                void load(option);
              }}
              className={cn(
                "rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
                state === option
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {tx(`projectHome.issues.state.${option}`, option)}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => {
            setRepoDraft(repo ?? "");
            setEditingRepo((open) => !open);
          }}
          title={tx(
            "projectHome.issues.repoHint",
            "Choose which repository these issues come from: this project's git remote, another one on the same host (owner/name), or a full URL on any GitHub, GitLab or Forgejo server.",
          )}
          className={cn(
            "flex min-w-0 items-center gap-1 rounded-md border border-border/60 px-1.5 py-1 text-[11px] font-medium transition-colors hover:bg-muted/60",
            repo ? "text-foreground" : "text-muted-foreground",
          )}
        >
          <Settings2 className="h-3 w-3 shrink-0" aria-hidden />
          <span className="max-w-[14rem] truncate">
            {repo || tx("projectHome.issues.repoOwn", "this project")}
          </span>
        </button>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => void importToBoard()}
          disabled={importing || !payload?.ok}
          title={tx(
            "projectHome.issues.importHint",
            "Create a board task for every open issue not on the board yet (deduplicated by URL).",
          )}
          className="flex items-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-foreground hover:text-background disabled:pointer-events-none disabled:opacity-50"
        >
          {importing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <Download className="h-3.5 w-3.5" aria-hidden />
          )}
          {tx("projectHome.issues.import", "Import to board")}
        </button>
        <button
          type="button"
          onClick={() => void load()}
          title={tx("dev.board.refresh", "Refresh")}
          className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
        </button>
      </div>

      {editingRepo ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void saveRepo(repoDraft);
          }}
          className="flex flex-wrap items-center gap-2 border-b border-border/60 bg-muted/30 px-3 py-2"
        >
          <label
            htmlFor="issues-repo"
            className="text-[11.5px] font-medium text-muted-foreground"
          >
            {tx("projectHome.issues.repoLabel", "Issues repository")}
          </label>
          <input
            id="issues-repo"
            autoFocus
            value={repoDraft}
            onChange={(event) => setRepoDraft(event.target.value)}
            placeholder="owner/name"
            spellCheck={false}
            className="min-w-0 flex-1 rounded-md border border-border/60 bg-background px-2 py-1 text-[12px] outline-none focus:border-foreground/40"
          />
          <button
            type="submit"
            disabled={savingRepo}
            className="flex items-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-[11.5px] font-medium transition-colors hover:bg-foreground hover:text-background disabled:pointer-events-none disabled:opacity-50"
          >
            {savingRepo ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <CircleCheck className="h-3.5 w-3.5" aria-hidden />
            )}
            {tx("projectHome.issues.repoSave", "Use this repository")}
          </button>
          {repo ? (
            <button
              type="button"
              onClick={() => void saveRepo("")}
              disabled={savingRepo}
              className="rounded-md border border-border/60 px-2 py-1 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
            >
              {tx("projectHome.issues.repoReset", "Back to the project remote")}
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => setEditingRepo(false)}
            title={tx("common.cancel", "Cancel")}
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
          <p className="w-full text-[11px] text-muted-foreground">
            {tx(
              "projectHome.issues.repoHelp",
              "Paste owner/name (read on this project's own host) or a full URL to follow another server: GitHub, GitLab or Forgejo. Leave it empty to use this project's own remote. Reading a repository needs a token for that host in Settings > Git.",
            )}
          </p>
        </form>
      ) : null}

      {error ? (
        <div className="flex items-center gap-2 border-b border-border/60 bg-destructive/10 px-3 py-1.5 text-[11.5px] text-destructive">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="truncate">{error}</span>
        </div>
      ) : null}
      {notice ? (
        <div className="flex items-center gap-2 border-b border-border/60 bg-emerald-500/10 px-3 py-1.5 text-[11.5px] text-emerald-700 dark:text-emerald-300">
          <CircleCheck className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="truncate">{notice}</span>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading && !payload ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
          </div>
        ) : issues.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-muted-foreground">
            {payload?.token_setup ? (
              <TokenSetupCard
                setup={payload.token_setup}
                forgeLabel={forgeLabel}
                tx={tx}
              />
            ) : payload?.install?.needed ? (
              <GhInstallCard install={payload.install} tx={tx} />
            ) : (
              <>
                <Github className="h-6 w-6 opacity-50" aria-hidden />
                <p className="max-w-[26rem] text-sm">
                  {payload?.ok === false
                    ? payload.detail ||
                      tx(
                        "projectHome.issues.unavailable",
                        "Issues unavailable: add a token for this host in Settings > Git, then use a repository with a GitHub, GitLab or Forgejo remote, or point this panel at another repository.",
                      )
                    : tx("projectHome.issues.empty", "No issue in this state.")}
                </p>
              </>
            )}
          </div>
        ) : (
          <ul className="space-y-1.5">
            {issues.map((issue, index) => (
              <motion.li
                key={issue.url || issue.number}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: Math.min(index, 12) * 0.02, duration: 0.18 }}
                className="rounded-xl border border-border/60 bg-background/70 px-3 py-2.5"
              >
                <div className="flex items-start gap-2.5">
                  {issue.state === "closed" ? (
                    <CircleCheck
                      className="mt-0.5 h-4 w-4 shrink-0 text-violet-500"
                      aria-hidden
                    />
                  ) : (
                    <CircleDot
                      className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500"
                      aria-hidden
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <a
                        href={issue.url}
                        target="_blank"
                        rel="noreferrer"
                        className="min-w-0 truncate text-sm font-medium hover:underline"
                        title={issue.title}
                      >
                        {issue.title}
                      </a>
                      <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                        #{issue.number}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-muted-foreground">
                      {issue.author ? (
                        <span className="flex items-center gap-1">
                          <User className="h-3 w-3" aria-hidden />
                          {issue.author}
                        </span>
                      ) : null}
                      {issue.updated_at ? <span>{relativeDay(issue.updated_at)}</span> : null}
                      {issue.comments > 0 ? (
                        <span className="flex items-center gap-1">
                          <MessageSquare className="h-3 w-3" aria-hidden />
                          {issue.comments}
                        </span>
                      ) : null}
                      {issue.labels.slice(0, 4).map((label) => (
                        <span
                          key={label}
                          className="rounded bg-muted px-1 py-0.5 text-[10px]"
                        >
                          {label}
                        </span>
                      ))}
                    </div>
                    {issue.body ? (
                      <p className="mt-1 line-clamp-2 text-[12px] leading-relaxed text-muted-foreground">
                        {issue.body}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {onRunAction && issue.state !== "closed" ? (
                      <button
                        type="button"
                        onClick={() => fixWithAgent(issue)}
                        title={tx(
                          "projectHome.issues.fixHint",
                          "Send this issue to the agent: fix, tests green, commit, then close the issue and re-sync the board.",
                        )}
                        className="flex items-center gap-1 rounded-md border border-border/60 px-1.5 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-foreground hover:text-background"
                      >
                        <Wrench className="h-3 w-3" aria-hidden />
                        {tx("projectHome.issues.fix", "Fix with agent")}
                      </button>
                    ) : null}
                    <a
                      href={issue.url}
                      target="_blank"
                      rel="noreferrer"
                      title={issue.url}
                      className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                    >
                      <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                    </a>
                  </div>
                </div>
              </motion.li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export default ProjectIssuesPanel;
