// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";

import { DevGitPanel } from "./DevGitPanel";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

interface Scenario {
  tokenConfigured: boolean;
  ahead: number;
  /** Responses of /git-commit, in order. */
  commits: Response[];
}

const fetchMock = vi.fn();
let scenario: Scenario;
const calls: string[] = [];

function route(input: string): Response {
  const url = new URL(String(input), "http://x");
  const path = url.pathname;
  calls.push(path);
  if (path.endsWith("/git-changes")) {
    return json(200, {
      available: true,
      is_repo: true,
      files: [],
      branch: "main",
      has_upstream: true,
      ahead: scenario.ahead,
      behind: 0,
    });
  }
  if (path.endsWith("/github/pr")) {
    return json(200, {
      available: true,
      branch: "main",
      detail: "",
      pr: null,
      forge: "github",
      host: "github.com",
      token_configured: scenario.tokenConfigured,
    });
  }
  if (path.endsWith("/github/checks")) {
    return json(200, {
      available: false,
      branch: "main",
      pr_url: null,
      detail: "",
      checks: { state: "none", total: 0, passed: 0, failed: 0, pending: 0, failing: [] },
      forge: "github",
      host: "github.com",
    });
  }
  if (path.endsWith("/git-commit")) {
    return scenario.commits.shift() ?? json(200, { committed: false, pushed: true });
  }
  if (path === "/api/webui/git/accounts") {
    return json(200, { accounts: [], device_flow: { github: false } });
  }
  if (path === "/api/settings/update") {
    return json(200, { forge: { hosts: [], env_tokens: [], gh_available: false, kinds: [] } });
  }
  if (path === "/api/webui/git/test") {
    return json(200, { ok: true, login: "octo" });
  }
  return json(404, { error: "not found" });
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
  calls.length = 0;
  fetchMock.mockImplementation(async (input: string) => route(input));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderPanel() {
  return render(
    <DevGitPanel token="tok" sessionKey="web:1" selectedPath={null} onShowDiff={() => {}} />,
  );
}

async function connectWithToken() {
  const tokenInput = await screen.findByLabelText("Token");
  fireEvent.change(tokenInput, { target: { value: "ghp_secret" } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /Save and test/ }));
  });
}

describe("DevGitPanel inline Git connect", () => {
  it("shows the connect card when no account is set for the remote host", async () => {
    scenario = { tokenConfigured: false, ahead: 0, commits: [] };
    renderPanel();
    const card = await screen.findByTestId("git-connect-panel");
    expect(card.textContent).toContain("Connect github.com");
    // Device flow is off on this engine: the token fallback is right there.
    expect(await screen.findByLabelText("Token")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Open Git settings/ })).toBeTruthy();
  });

  it("turns a push auth failure into the connect card, then pushes again", async () => {
    scenario = {
      tokenConfigured: true,
      ahead: 1,
      commits: [
        json(500, { error: "fatal: Authentication failed for 'https://github.com/acme/app.git/'" }),
        json(200, { committed: false, pushed: true, branch: "main" }),
      ],
    };
    renderPanel();
    const push = await screen.findByRole("button", { name: "Push" });
    await waitFor(() => expect((push as HTMLButtonElement).disabled).toBe(false));
    await act(async () => {
      fireEvent.click(push);
    });

    const card = await screen.findByTestId("git-connect-panel");
    expect(card.textContent).toContain("Connect github.com");
    // No raw git error under the card.
    expect(screen.queryByText(/Authentication failed/)).toBeNull();

    await connectWithToken();
    await waitFor(() =>
      expect(calls.filter((path) => path.endsWith("/git-commit"))).toHaveLength(2),
    );
    await waitFor(() => expect(screen.queryByTestId("git-connect-panel")).toBeNull());
  });
});
