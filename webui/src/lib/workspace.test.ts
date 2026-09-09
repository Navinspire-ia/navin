// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  exampleProjectPath,
  isAbsoluteWorkspacePath,
  pathCrumbs,
  projectEnvironmentLabel,
  sameWorkspacePath,
  selectedProjectScope,
  workspaceGateState,
} from "./workspace";
import type { WorkspaceScopePayload } from "@/lib/types";

// The paths the gateway actually serves on this machine: /api/webui/fs/roots
// reports the internal workspace under kind "workspace", and /api/workspaces
// hands back the very same path as default_scope.project_path whenever no
// project is selected. That coincidence is the whole reason the Code module
// used to open navin's own folder.
const INTERNAL = "/home/aymen/.navin/workspace";
const PROJECT = "/home/aymen/projects/deploy7/navin-ai-v2";

function gate(options: Partial<Parameters<typeof workspaceGateState>[0]> = {}) {
  return workspaceGateState({
    currentRoot: INTERNAL,
    internalWorkspacePath: INTERNAL,
    recentProjects: [],
    workspaceAccepted: false,
    ...options,
  });
}

describe("workspaceGateState", () => {
  it("asks for a project when the root is the internal workspace and nothing is known", () => {
    const state = gate();
    expect(state.onInternalWorkspace).toBe(true);
    expect(state.hasKnownProject).toBe(false);
    expect(state.showProjectGate).toBe(true);
  });

  it("stays out of the way once a real project is open", () => {
    const state = gate({ currentRoot: PROJECT, recentProjects: [{ path: PROJECT }] });
    expect(state.onInternalWorkspace).toBe(false);
    expect(state.showProjectGate).toBe(false);
  });

  it("does not gate when a project can be reopened instead", () => {
    // The caller restores recent_projects[0], so the gate would flash and
    // vanish; the badge is still wanted while the restore lands.
    const state = gate({ recentProjects: [{ path: PROJECT }] });
    expect(state.onInternalWorkspace).toBe(true);
    expect(state.hasKnownProject).toBe(true);
    expect(state.showProjectGate).toBe(false);
  });

  it("lets the user through to the internal workspace on purpose", () => {
    expect(gate({ workspaceAccepted: true }).showProjectGate).toBe(false);
  });

  it("ignores the internal workspace sitting in the recent list", () => {
    // Opening it deliberately records it as a recent project; that must not
    // count as "a project exists" the next time around.
    const state = gate({ recentProjects: [{ path: INTERNAL }] });
    expect(state.hasKnownProject).toBe(false);
    expect(state.showProjectGate).toBe(true);
  });

  it("holds off until the roots call has answered", () => {
    // internalWorkspacePath is null on the first render; gating then would
    // flash the empty state over a perfectly good project.
    const state = gate({ currentRoot: PROJECT, internalWorkspacePath: null });
    expect(state.onInternalWorkspace).toBe(false);
    expect(state.showProjectGate).toBe(false);
  });

  it("tolerates a trailing separator and Windows separators", () => {
    expect(gate({ currentRoot: `${INTERNAL}/` }).showProjectGate).toBe(true);
    const windows = "C:/Users/gadhg/.navin/workspace";
    expect(
      workspaceGateState({
        currentRoot: "C:\\Users\\gadhg\\.navin\\workspace",
        internalWorkspacePath: windows,
        recentProjects: [],
        workspaceAccepted: false,
      }).showProjectGate,
    ).toBe(true);
  });
});

describe("selectedProjectScope", () => {
  // The guard that decides whether to restore the last opened project. It has
  // to answer "null" for the internal workspace, otherwise the restore never
  // runs: the scope is never actually empty.
  function scope(path: string): WorkspaceScopePayload {
    return {
      project_path: path,
      project_name: path.split("/").pop() ?? path,
      access_mode: "restricted",
      restrict_to_workspace: true,
    } as WorkspaceScopePayload;
  }

  it("treats the internal workspace as no project selected", () => {
    expect(selectedProjectScope(scope(INTERNAL), scope(INTERNAL))).toBeNull();
  });

  it("reports a real project as selected", () => {
    expect(selectedProjectScope(scope(PROJECT), scope(INTERNAL))?.project_path).toBe(PROJECT);
  });
});

describe("sameWorkspacePath", () => {
  it("never matches on a missing path", () => {
    expect(sameWorkspacePath(null, null)).toBe(false);
    expect(sameWorkspacePath(INTERNAL, undefined)).toBe(false);
  });
});

// A project inside a WSL distribution is reached from Windows through
// \\wsl.localhost\<distro>\..., and that shape used to be rejected outright:
// the picker refused the path before the gateway - which handles it fine - was
// ever asked.
describe("isAbsoluteWorkspacePath", () => {
  it("accepts the shapes it always did", () => {
    expect(isAbsoluteWorkspacePath("/home/me/app")).toBe(true);
    expect(isAbsoluteWorkspacePath("~")).toBe(true);
    expect(isAbsoluteWorkspacePath("~/app")).toBe(true);
    expect(isAbsoluteWorkspacePath("C:\\src\\app")).toBe(true);
  });

  it("accepts a project inside a WSL distribution", () => {
    expect(isAbsoluteWorkspacePath("\\\\wsl.localhost\\Ubuntu\\home\\me\\app")).toBe(true);
    expect(isAbsoluteWorkspacePath("\\\\wsl$\\Ubuntu\\home\\me")).toBe(true);
  });

  it("accepts an ordinary network share too", () => {
    expect(isAbsoluteWorkspacePath("\\\\fileserver\\share\\app")).toBe(true);
  });

  it("still refuses a relative path", () => {
    expect(isAbsoluteWorkspacePath("app")).toBe(false);
    expect(isAbsoluteWorkspacePath("./app")).toBe(false);
    expect(isAbsoluteWorkspacePath("../app")).toBe(false);
    expect(isAbsoluteWorkspacePath("")).toBe(false);
  });

  it("refuses a bare UNC prefix that addresses nothing", () => {
    expect(isAbsoluteWorkspacePath("\\\\")).toBe(false);
    expect(isAbsoluteWorkspacePath("\\\\server")).toBe(false);
  });
});

describe("pathCrumbs", () => {
  const labels = (path: string) => pathCrumbs(path).map((c) => c.label);
  const last = (path: string) => pathCrumbs(path).at(-1);

  it("splits a POSIX path at every slash", () => {
    expect(labels("/home/me/app")).toEqual(["/", "home", "me", "app"]);
    expect(last("/home/me/app")?.path).toBe("/home/me/app");
  });

  it("gives a drive path its own root instead of a slash", () => {
    expect(labels("C:\\src\\app")).toEqual(["C:\\", "src", "app"]);
    expect(last("C:\\src\\app")?.path).toBe("C:\\src\\app");
  });

  it("keeps host and distribution together as one root", () => {
    // Half of \\wsl.localhost\Ubuntu addresses nothing, so it cannot be a crumb.
    expect(labels("\\\\wsl.localhost\\Ubuntu\\home\\me")).toEqual([
      "\\\\wsl.localhost\\Ubuntu",
      "home",
      "me",
    ]);
  });

  it("builds a path for every crumb that leads back to that folder", () => {
    expect(pathCrumbs("\\\\wsl.localhost\\Ubuntu\\home\\me").map((c) => c.path)).toEqual([
      "\\\\wsl.localhost\\Ubuntu",
      "\\\\wsl.localhost\\Ubuntu\\home",
      "\\\\wsl.localhost\\Ubuntu\\home\\me",
    ]);
  });

  it("handles a distribution root with nothing after it", () => {
    expect(labels("\\\\wsl.localhost\\Ubuntu")).toEqual(["\\\\wsl.localhost\\Ubuntu"]);
  });

  it("accepts forward slashes in a UNC path, as typed by hand", () => {
    expect(labels("//wsl.localhost/Ubuntu/home")).toEqual(["\\\\wsl.localhost\\Ubuntu", "home"]);
  });

  it("has nothing to show for an empty path", () => {
    expect(pathCrumbs("")).toEqual([]);
    expect(pathCrumbs(null)).toEqual([]);
  });

  it("shows the root alone at the top of a filesystem", () => {
    expect(labels("/")).toEqual(["/"]);
    expect(labels("C:\\")).toEqual(["C:\\"]);
  });
});

describe("projectEnvironmentLabel", () => {
  it("reads the distribution out of a WSL UNC path, whatever the host is", () => {
    // The exact bug from the field: backend on Windows, project inside WSL.
    // The footer said WINDOWS; the folder lives in Ubuntu.
    expect(
      projectEnvironmentLabel({
        projectPath: "\\\\wsl.localhost\\Ubuntu\\home\\me\\app",
        environment: "windows",
      }),
    ).toBe("WSL: Ubuntu");
    expect(
      projectEnvironmentLabel({
        projectPath: "//wsl$/Debian/srv",
        environment: "windows",
      }),
    ).toBe("WSL: Debian");
  });

  it("names the distribution when the backend itself runs inside WSL", () => {
    expect(
      projectEnvironmentLabel({
        projectPath: "/home/me/app",
        environment: "wsl",
        distro: "Ubuntu",
      }),
    ).toBe("WSL: Ubuntu");
    expect(
      projectEnvironmentLabel({ projectPath: "/home/me/app", environment: "wsl" }),
    ).toBe("WSL");
  });

  it("recognizes a Windows drive mounted inside WSL", () => {
    expect(
      projectEnvironmentLabel({
        projectPath: "/mnt/c/Users/me/app",
        environment: "wsl",
        distro: "Ubuntu",
      }),
    ).toBe("Windows (C:)");
  });

  it("falls back to the host platform for local paths", () => {
    expect(
      projectEnvironmentLabel({ projectPath: "C:\\src\\app", environment: "windows" }),
    ).toBe("Windows");
    expect(
      projectEnvironmentLabel({ projectPath: "/Users/me/app", environment: "macos" }),
    ).toBe("macOS");
    expect(
      projectEnvironmentLabel({ projectPath: "/home/me/app", environment: "linux" }),
    ).toBe("Linux");
  });

  it("has nothing to say before the environment is known", () => {
    expect(projectEnvironmentLabel({ projectPath: null, environment: null })).toBeNull();
  });
});

describe("exampleProjectPath", () => {
  it("shapes the placeholder like the host's own home", () => {
    expect(exampleProjectPath("/home/me")).toBe("/home/me/project");
    expect(exampleProjectPath("/Users/me/")).toBe("/Users/me/project");
    expect(exampleProjectPath("C:\\Users\\me")).toBe("C:\\Users\\me\\project");
    expect(exampleProjectPath("C:\\")).toBe("C:\\project");
    expect(exampleProjectPath("\\\\wsl.localhost\\Ubuntu\\home\\me")).toBe(
      "\\\\wsl.localhost\\Ubuntu\\home\\me\\project",
    );
  });

  it("leaves the caller its fallback when no home is known yet", () => {
    expect(exampleProjectPath(null)).toBeNull();
    expect(exampleProjectPath("   ")).toBeNull();
  });
});
