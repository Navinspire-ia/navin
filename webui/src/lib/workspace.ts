import type { WorkspaceAccessMode, WorkspaceScopePayload } from "@/lib/types";

export function scopeWithAccessMode(
  scope: WorkspaceScopePayload,
  accessMode: WorkspaceAccessMode,
): WorkspaceScopePayload {
  return {
    ...scope,
    access_mode: accessMode,
    restrict_to_workspace: accessMode === "restricted",
  };
}

export function projectNameFromPath(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  return normalized.split("/").filter(Boolean).pop() || path;
}

export function shortWorkspacePath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 3) return path;
  return `.../${parts.slice(-3).join("/")}`;
}

/** A UNC path: `\\wsl.localhost\Ubuntu\home\me`, or any network share. */
function isUncPath(path: string): boolean {
  return /^(\\\\|\/\/)[^\\/]+[\\/]/.test(path);
}

export function isAbsoluteWorkspacePath(path: string): boolean {
  const trimmed = path.trim();
  return (
    trimmed === "~"
    || trimmed.startsWith("~/")
    || trimmed.startsWith("~\\")
    || trimmed.startsWith("/")
    || /^[A-Za-z]:[\\/]/.test(trimmed)
    // A project inside a WSL distribution is reached through a UNC path, and
    // rejecting those is what made such a project impossible to open by hand.
    || isUncPath(trimmed)
  );
}

export interface PathCrumb {
  label: string;
  path: string;
}

function crumbTrail(rest: string, base: string, separator: string): PathCrumb[] {
  const out: PathCrumb[] = [];
  let acc = base;
  for (const part of rest.split(/[\\/]/).filter(Boolean)) {
    acc = acc.endsWith(separator) ? `${acc}${part}` : `${acc}${separator}${part}`;
    out.push({ label: part, path: acc });
  }
  return out;
}

/**
 * The clickable trail above the folder browser.
 *
 * Three shapes have to work. A POSIX path splits at every slash. A drive path
 * has `C:\` as its root, not `/`. A UNC path has a root two segments long -
 * host and share - which cannot be split, because half of
 * `\\wsl.localhost\Ubuntu` addresses nothing; that shape matters because a
 * project inside a WSL distribution is only reachable through it.
 *
 * Splitting on "/" alone, as this used to, turned every Windows path into one
 * unusable segment and offered a "/" crumb leading somewhere that does not
 * exist on that host.
 */
export function pathCrumbs(path: string | null | undefined): PathCrumb[] {
  const trimmed = (path ?? "").trim();
  if (!trimmed) return [];

  const unc = /^(?:\\\\|\/\/)([^\\/]+)[\\/]([^\\/]+)(.*)$/.exec(trimmed);
  if (unc) {
    const [, host, share, rest] = unc;
    const root = `\\\\${host}\\${share}`;
    return [{ label: root, path: root }, ...crumbTrail(rest, root, "\\")];
  }

  const drive = /^([A-Za-z]:)[\\/](.*)$/.exec(trimmed);
  if (drive) {
    const [, letter, rest] = drive;
    return [{ label: `${letter}\\`, path: `${letter}\\` }, ...crumbTrail(rest, letter, "\\")];
  }

  return [{ label: "/", path: "/" }, ...crumbTrail(trimmed, "", "/")];
}

/**
 * Where the project folder actually lives, as a status-bar label.
 *
 * The backend's platform alone is not the answer: the Windows desktop app can
 * open a project inside a WSL distribution through `\\wsl.localhost\Ubuntu\...`
 * and that folder is Linux territory even though the backend runs on Windows.
 * Conversely a backend inside WSL can open `/mnt/c/...`, which is a Windows
 * drive. The path is consulted first; only a genuinely local path falls back
 * to the host platform.
 */
/**
 * A path placeholder shaped like this machine's own paths, built from the
 * home root the gateway reported: `C:\Users\me\project` on Windows,
 * `/home/me/project` on Linux and WSL, `/Users/me/project` on macOS. A fixed
 * `/Users/name/project` told Windows and Linux users to type a macOS path.
 */
export function exampleProjectPath(homePath: string | null | undefined): string | null {
  const home = (homePath ?? "").trim().replace(/[\\/]+$/, "");
  if (!home) return null;
  const windows = /^[A-Za-z]:$/.test(home) || /^[A-Za-z]:\\/.test(home) || home.startsWith("\\\\");
  return windows ? `${home}\\project` : `${home}/project`;
}

export function projectEnvironmentLabel(options: {
  projectPath: string | null | undefined;
  environment: string | null | undefined;
  distro?: string | null;
}): string | null {
  const { projectPath, environment, distro } = options;
  const path = (projectPath ?? "").trim();

  const wslUnc = /^(?:\\\\|\/\/)wsl(?:\.localhost|\$)[\\/]+([^\\/]+)/i.exec(path);
  if (wslUnc) return `WSL: ${wslUnc[1]}`;

  const windowsMount = /^\/mnt\/([a-z])(?:\/|$)/i.exec(path);
  if (environment === "wsl" && windowsMount) {
    return `Windows (${windowsMount[1].toUpperCase()}:)`;
  }

  switch (environment) {
    case "wsl":
      return distro ? `WSL: ${distro}` : "WSL";
    case "windows":
      return "Windows";
    case "macos":
      return "macOS";
    case "linux":
      return "Linux";
    default:
      return environment ? environment.toUpperCase() : null;
  }
}

export function selectedProjectScope(
  scope: WorkspaceScopePayload | null,
  defaultScope: WorkspaceScopePayload | null,
): WorkspaceScopePayload | null {
  if (!scope || !defaultScope) return null;
  return sameWorkspacePath(scope.project_path, defaultScope.project_path) ? null : scope;
}

export function normalizeWorkspacePath(path: string | null | undefined): string {
  const normalized = (path ?? "").replace(/\\/g, "/").replace(/\/+$/, "");
  return normalized || "/";
}

/**
 * A path inside a `.navin` folder is Navin's own storage (config, sessions,
 * memory). It must never be displayed or offered as a project workspace.
 */
export function isNavinInternalPath(path: string | null | undefined): boolean {
  if (!path) return false;
  return path.replace(/\\/g, "/").split("/").includes(".navin");
}

export function sameWorkspacePath(
  a: string | null | undefined,
  b: string | null | undefined,
): boolean {
  if (!a || !b) return false;
  return normalizeWorkspacePath(a) === normalizeWorkspacePath(b);
}

/** What the Code workbench should show when it has no project of its own.
 *
 * A session scope never comes back empty: with nothing selected it falls back
 * to navin's internal ~/.navin/workspace, the folder holding the agent's own
 * notes, skills and memory. "No project is open" and "the root is the internal
 * workspace" are therefore the same state, and listing that folder to someone
 * who came to write code is a dead end.
 *
 * The gate only replaces the file tree while there is genuinely nowhere to go:
 * a known project means the caller can reopen it instead, and accepting the
 * workspace means the user asked for that folder on purpose.
 */
export function workspaceGateState(options: {
  currentRoot: string | null | undefined;
  internalWorkspacePath: string | null | undefined;
  recentProjects: readonly { path: string }[];
  workspaceAccepted: boolean;
}): { onInternalWorkspace: boolean; hasKnownProject: boolean; showProjectGate: boolean } {
  const { currentRoot, internalWorkspacePath, recentProjects, workspaceAccepted } = options;
  const onInternalWorkspace = sameWorkspacePath(currentRoot, internalWorkspacePath);
  const hasKnownProject = recentProjects.some(
    (entry) => !sameWorkspacePath(entry.path, internalWorkspacePath),
  );
  return {
    onInternalWorkspace,
    hasKnownProject,
    showProjectGate: onInternalWorkspace && !hasKnownProject && !workspaceAccepted,
  };
}
