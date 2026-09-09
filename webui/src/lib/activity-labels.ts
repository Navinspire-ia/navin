/**
 * Human one-line verbs for the chat activity timeline.
 *
 * Specialized rows (search, web/file read, shell, CLI, MCP, file edits) keep
 * their own UI. This map only names the tools that would otherwise show as
 * "Using <tool>". Lookups are by the last dotted segment, lowercased, so a
 * namespaced call still matches. When the trace carries an `action` field,
 * a more specific verb wins and the generic tool label stays the fallback.
 */

export type ActivityLabel = {
  key: string;
  defaultValue: string;
};

function label(id: string, defaultValue: string): ActivityLabel {
  return { key: `message.activityTool.${id}`, defaultValue };
}

export const GENERIC_ACTIVITY_LABELS = {
  searching: label("searching", "Searching"),
  reading: label("reading", "Reading"),
  command: label("command", "Command"),
  using: label("using", "Using"),
  done: label("done", "Done"),
  working: label("working", "Working"),
} as const satisfies Record<string, ActivityLabel>;

const TOOL_ACTIVITY_LABELS: Record<string, ActivityLabel> = {
  notes: label("notes", "Checked notes"),
  crm: label("crm", "Updated CRM"),
  board: label("board", "Updated board"),
  cron: label("cron", "Scheduled reminder"),
  browser: label("browser", "Opened browser"),
  scrape: label("scrape", "Scraped page"),
  generate_image: label("generateImage", "Generated image"),
  generate_video: label("generateVideo", "Generated video"),
  generate_music: label("generateMusic", "Generated music"),
  generate_speech: label("generateSpeech", "Generated speech"),
  db_query: label("dbQuery", "Queried database"),
  git: label("git", "Checked git"),
  spawn: label("spawn", "Started sub-agent"),
  notify: label("notify", "Sent notification"),
  present_artifact: label("presentArtifact", "Presented artifact"),
  open_file_preview: label("openPreview", "Opened preview"),
  open_preview: label("openPreview", "Opened preview"),
  open_in_editor: label("openInEditor", "Opened in editor"),
  open_terminal: label("openTerminal", "Opened terminal"),
  start_app: label("startApp", "Started app"),
  skill: label("skill", "Looked up skill"),
  set_composer_mode: label("setComposerMode", "Switched composer mode"),
  list_exec_sessions: label("listSessions", "Listed sessions"),
  write_stdin: label("writeStdin", "Wrote to session"),
  create_goal: label("createGoal", "Created goal"),
  update_goal: label("updateGoal", "Updated goal"),
  montage: label("montage", "Built montage"),
  code_index: label("codeIndex", "Indexed code"),
  message: label("message", "Sent message"),
  lsp: label("lsp", "Checked language server"),
  lint: label("lint", "Ran linter"),
  test_run: label("testRun", "Ran tests"),
  ask_user: label("askUser", "Asked a question"),
  verify: label("verify", "Verified project"),
  list_dir: label("listDir", "Listed folder"),
  find_files: label("findFiles", "Looked up files"),
  grep: label("grep", "Searched codebase"),
  code_review: label("codeReview", "Reviewed code"),
  debug_repair: label("debugRepair", "Debugged"),
  metagraph: label("metagraph", "Updated graph"),
  mobile: label("mobile", "Used mobile device"),
  computer: label("computer", "Used the desktop"),
  manage_files: label("manageFiles", "Managed files"),
  pr_comments: label("prComments", "Checked PR comments"),
  my: label("my", "Checked identity"),
  security_scan: label("securityScan", "Scanned security"),
};

const TOOL_ACTION_LABELS: Record<string, Record<string, ActivityLabel>> = {
  git: {
    status: label("gitStatus", "Checked git status"),
    diff: label("gitDiff", "Reviewed git diff"),
    log: label("gitLog", "Read git log"),
    show: label("gitShow", "Showed git commit"),
    blame: label("gitBlame", "Checked git blame"),
    branches: label("gitBranches", "Listed branches"),
    add: label("gitAdd", "Staged files"),
    commit: label("gitCommit", "Created commit"),
    restore: label("gitRestore", "Restored files"),
    switch: label("gitSwitch", "Switched branch"),
    stash: label("gitStash", "Stashed changes"),
    stash_list: label("gitStashList", "Listed stashes"),
    stash_pop: label("gitStashPop", "Applied stash"),
    fetch: label("gitFetch", "Fetched remote"),
    push: label("gitPush", "Pushed commits"),
    pull: label("gitPull", "Pulled remote"),
    merge: label("gitMerge", "Merged branch"),
    rebase: label("gitRebase", "Rebased branch"),
    reset: label("gitReset", "Reset git"),
  },
  browser: {
    navigate: label("browserNavigate", "Opened page"),
    snapshot: label("browserSnapshot", "Captured snapshot"),
    screenshot: label("browserScreenshot", "Took screenshot"),
    click: label("browserClick", "Clicked"),
    type: label("browserType", "Typed"),
    select: label("browserSelect", "Selected option"),
    press_key: label("browserPressKey", "Pressed key"),
    send_keys: label("browserPressKey", "Pressed key"),
    scroll: label("browserScroll", "Scrolled"),
    scroll_infinite: label("browserScroll", "Scrolled"),
    evaluate: label("browserEvaluate", "Ran page script"),
    console: label("browserConsole", "Read console"),
    content: label("browserContent", "Read page"),
    extract: label("browserContent", "Read page"),
    network: label("browserNetwork", "Inspected network"),
    response_body: label("browserResponse", "Read response"),
    cdp: label("browserCdp", "Ran CDP command"),
    back: label("browserBack", "Went back"),
    wait: label("browserWait", "Waited"),
    close: label("browserClose", "Closed browser"),
    tabs: label("browserTabs", "Listed tabs"),
    switch_tab: label("browserSwitchTab", "Switched tab"),
    close_tab: label("browserCloseTab", "Closed tab"),
    new_tab: label("browserNewTab", "Opened tab"),
    upload_file: label("browserUpload", "Uploaded file"),
    find_text: label("browserFind", "Searched page"),
    search_page: label("browserFind", "Searched page"),
    search: label("browserFind", "Searched page"),
    find_elements: label("browserFind", "Searched page"),
    save_as_pdf: label("browserPdf", "Saved PDF"),
    dropdown_options: label("browserDropdown", "Listed options"),
    done: label("browserDone", "Finished browser task"),
    history: label("browserHistory", "Read browser history"),
    bu: label("browserBu", "Ran browser action"),
    record_start: label("browserRecordStart", "Started recording"),
    record_stop: label("browserRecordStop", "Stopped recording"),
  },
  computer: {
    screen: label("computerScreenshot", "Viewed the Screen"),
    screenshot: label("computerScreenshot", "Viewed the Screen"),
    permissions: label("computerPermissions", "Prepared Computer permissions"),
    left_click: label("computerClick", "Clicked"),
    click: label("computerClick", "Clicked"),
    right_click: label("computerRightClick", "Right-clicked"),
    middle_click: label("computerClick", "Clicked"),
    double_click: label("computerDoubleClick", "Double-clicked"),
    triple_click: label("computerDoubleClick", "Double-clicked"),
    mouse_move: label("computerMove", "Moved the mouse"),
    move: label("computerMove", "Moved the mouse"),
    left_click_drag: label("computerDrag", "Dragged"),
    drag: label("computerDrag", "Dragged"),
    left_mouse_down: label("computerDrag", "Dragged"),
    left_mouse_up: label("computerDrag", "Dragged"),
    scroll: label("computerScroll", "Scrolled"),
    type: label("computerType", "Typed"),
    key: label("computerKey", "Pressed key"),
    hold_key: label("computerKey", "Pressed key"),
    wait: label("computerWait", "Waited"),
    cursor_position: label("computerCursor", "Checked the cursor"),
    zoom: label("computerZoom", "Zoomed in"),
    windows: label("computerWindows", "Listed windows"),
    focus_window: label("computerFocus", "Switched window"),
    snapshot: label("computerSnapshot", "Read the accessibility tree"),
    screen_info: label("computerScreenInfo", "Checked the screen"),
    status: label("computerStatus", "Checked desktop status"),
    resume: label("computerResume", "Resumed after takeover"),
    close: label("computerClose", "Released the desktop"),
    done: label("computerClose", "Released the desktop"),
  },
  notes: {
    search: label("notesSearch", "Searched notes"),
    read: label("notesRead", "Read note"),
    list: label("notesList", "Listed notes"),
  },
  board: {
    list: label("boardList", "Listed board"),
    next: label("boardNext", "Took next task"),
    plan: label("boardPlan", "Planned board"),
    get: label("boardGet", "Opened task"),
    create: label("boardCreate", "Created task"),
    update: label("boardUpdate", "Updated task"),
    move: label("boardMove", "Moved task"),
    claim: label("boardClaim", "Claimed task"),
    comment: label("boardComment", "Commented on task"),
    delete: label("boardDelete", "Deleted task"),
  },
  cron: {
    add: label("cronAdd", "Scheduled reminder"),
    list: label("cronList", "Listed reminders"),
    remove: label("cronRemove", "Removed reminder"),
  },
  manage_files: {
    delete: label("manageDelete", "Deleted files"),
    move: label("manageMove", "Moved files"),
    copy: label("manageCopy", "Copied files"),
    mkdir: label("manageMkdir", "Created folder"),
  },
  test_run: {
    run: label("testRun", "Ran tests"),
    detect: label("testRunDetect", "Detected test suites"),
  },
  lint: {
    file: label("lintFile", "Linted file"),
    changed: label("lintChanged", "Linted changes"),
    project: label("lintProject", "Linted project"),
    fix: label("lintFix", "Auto-fixed lint"),
    available: label("lintAvailable", "Listed linters"),
  },
  verify: {
    check: label("verifyCheck", "Verified project"),
    fix: label("verifyFix", "Fixed and re-verified"),
    snapshot: label("verifySnapshot", "Saved snapshot"),
    rollback: label("verifyRollback", "Rolled back"),
    snapshots: label("verifySnapshots", "Listed snapshots"),
  },
};

export function actionFromToolArgs(args: string): string | null {
  const compact = args.trim();
  if (!compact) return null;
  try {
    const parsed = JSON.parse(compact) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const action = (parsed as Record<string, unknown>).action;
    if (typeof action !== "string") return null;
    const normalized = action.trim().toLowerCase();
    return normalized || null;
  } catch {
    return null;
  }
}

export function isSpawnTool(name: string): boolean {
  return (name.trim().toLowerCase().split(".").pop() || "") === "spawn";
}

/**
 * The human name of what a ``spawn`` was asked to do: label, role, then task.
 *
 * Without it the activity line said a subagent had started and nothing else,
 * which reads as if the request had gone nowhere.
 */
export function spawnTargetFromArgs(args: string): string {
  const compact = args.trim();
  if (!compact) return "";
  let parsed: unknown;
  try {
    parsed = JSON.parse(compact);
  } catch {
    return "";
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return "";
  const record = parsed as Record<string, unknown>;
  for (const key of ["label", "agent", "task"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

export function activityLabelForTool(name: string, action?: string | null): ActivityLabel | null {
  const compact = name.toLowerCase().split(".").pop() || "";
  if (!compact) return null;
  const normalizedAction = (action || "").trim().toLowerCase();
  if (normalizedAction) {
    const specific = TOOL_ACTION_LABELS[compact]?.[normalizedAction];
    if (specific) return specific;
  }
  return TOOL_ACTIVITY_LABELS[compact] ?? null;
}
