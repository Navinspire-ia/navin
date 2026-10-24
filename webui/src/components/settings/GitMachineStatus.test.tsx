// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GitMachinePayload } from "@/lib/types";

import { GitMachineStatus } from "./GitMachineStatus";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    fetchGitMachine: vi.fn(),
    setGitIdentity: vi.fn(),
    storeGitTokenInHelper: vi.fn(),
    switchGitRemoteToHttps: vi.fn(),
  };
});

vi.mock("@/lib/api", () => api);
vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

function machine(overrides: Partial<GitMachinePayload> = {}): GitMachinePayload {
  return {
    platform: { os: "windows", label: "Windows 11", arch: "AMD64", wsl: false },
    git: { available: true, version: "2.47.0", path: "C:\\Program Files\\Git\\cmd\\git.exe", distro: null, bridge_ok: true, min_version: "2.31" },
    identity: { name: null, email: null, name_scope: null, email_scope: null },
    credential_helper: {
      helpers: ["manager"],
      active: "manager",
      secure: true,
      recommended: { helper: "manager", command: "git config --global credential.helper manager", note: "" },
    },
    ssh: { keys: [] },
    gh: { available: false },
    remotes: [],
    checks: [],
    root: null,
    ...overrides,
  };
}

const WINGET = "winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements";

describe("GitMachineStatus", () => {
  beforeEach(() => vi.resetAllMocks());
  afterEach(() => cleanup());

  it("gives the winget command when Windows has no git", async () => {
    api.fetchGitMachine.mockResolvedValue(
      machine({
        git: { available: false, version: null, path: null, distro: null, bridge_ok: false, min_version: "2.31" },
        checks: [
          { id: "git_missing", level: "error", params: { os: "windows" }, fix: { command: WINGET, url: "https://git-scm.com/download/win" } },
        ],
      }),
    );
    render(<GitMachineStatus token="tok" session="websocket:a" />);
    expect(await screen.findByText("settings.git.machine.gitMissing")).toBeTruthy();
    expect(screen.getByText(WINGET)).toBeTruthy();
    expect(api.fetchGitMachine).toHaveBeenCalledWith("tok", { session: "websocket:a" });
  });

  it("saves a missing commit identity and checks again", async () => {
    api.fetchGitMachine.mockResolvedValue(
      machine({ checks: [{ id: "identity_missing", level: "warn", params: { missing: ["name", "email"] }, fix: { action: "identity" } }] }),
    );
    api.setGitIdentity.mockResolvedValue({ ok: true, name: "Me", email: "me@example.com", changed: ["user.name", "user.email"], distro: null, error: null });
    const onChanged = vi.fn();
    render(<GitMachineStatus token="tok" session="websocket:a" onChanged={onChanged} />);
    await screen.findByTestId("git-identity-form");
    fireEvent.change(screen.getByLabelText("settings.git.machine.nameLabel"), { target: { value: "Me" } });
    fireEvent.change(screen.getByLabelText("settings.git.machine.emailLabel"), { target: { value: "me@example.com" } });
    fireEvent.click(screen.getByText("settings.git.machine.saveIdentity"));
    await waitFor(() =>
      expect(api.setGitIdentity).toHaveBeenCalledWith("tok", { name: "Me", email: "me@example.com", session: "websocket:a" }),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(api.fetchGitMachine).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("settings.git.machine.identitySaved")).toBeTruthy();
  });

  it("switches an SSH remote without a key to HTTPS", async () => {
    api.fetchGitMachine.mockResolvedValue(
      machine({
        checks: [
          {
            id: "remote_ssh_no_key",
            level: "warn",
            params: { remote: "origin", host: "github.com" },
            fix: { action: "use_https", remote: "origin", url: "https://github.com/o/r.git" },
          },
        ],
      }),
    );
    api.switchGitRemoteToHttps.mockResolvedValue({ ok: true, remote: "origin", url: "https://github.com/o/r.git" });
    render(<GitMachineStatus token="tok" session="websocket:a" />);
    fireEvent.click(await screen.findByText("settings.git.machine.useHttps"));
    await waitFor(() => expect(api.switchGitRemoteToHttps).toHaveBeenCalledWith("tok", "origin", "websocket:a"));
  });

  it("shows the error of a failed check", async () => {
    api.fetchGitMachine.mockRejectedValue(new Error("engine offline"));
    render(<GitMachineStatus token="tok" />);
    expect((await screen.findByRole("alert")).textContent).toContain("engine offline");
  });

  it("renders nothing on an engine without the route", async () => {
    api.fetchGitMachine.mockRejectedValue(new api.ApiError(404, "not found"));
    const onAvailability = vi.fn();
    const { container } = render(<GitMachineStatus token="tok" onAvailability={onAvailability} />);
    await waitFor(() => expect(onAvailability).toHaveBeenLastCalledWith(false));
    expect(container.innerHTML).toBe("");
  });
});
