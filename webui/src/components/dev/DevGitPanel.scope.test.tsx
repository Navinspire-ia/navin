// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

// Regression: switching project while project A's git answer is still in
// flight painted A's files and branch on project B.

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";

import { DevGitPanel } from "./DevGitPanel";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function changes(file: string, branch: string): Response {
  return json(200, {
    available: true,
    is_repo: true,
    files: [{ path: file, status: "modified", staged: false }],
    branch,
    has_upstream: true,
    ahead: 0,
    behind: 0,
  });
}

const fetchMock = vi.fn();
/** Pending /git-changes answer of session web:a, released by the test. */
let releaseA: ((res: Response) => void) | null = null;
let rejectA: ((err: Error) => void) | null = null;

function route(input: string): Promise<Response> | Response {
  const url = new URL(String(input), "http://x");
  const path = decodeURIComponent(url.pathname);
  if (path.endsWith("/git-changes")) {
    if (path.includes("web:a")) {
      return new Promise<Response>((resolve, reject) => {
        releaseA = resolve;
        rejectA = reject;
      });
    }
    return changes("only-in-b.txt", "feature-b");
  }
  if (path.endsWith("/github/pr")) {
    return json(200, { available: false, branch: "", detail: "", pr: null, token_configured: true });
  }
  if (path.endsWith("/github/checks")) {
    return json(200, {
      available: false,
      branch: "",
      pr_url: null,
      detail: "",
      checks: { state: "none", total: 0, passed: 0, failed: 0, pending: 0, failing: [] },
    });
  }
  if (path === "/api/webui/git/accounts") {
    return json(200, { accounts: [], device_flow: { github: false } });
  }
  return json(404, { error: "not found" });
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
  releaseA = null;
  rejectA = null;
  fetchMock.mockImplementation(async (input: string) => route(input));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function panel(sessionKey: string) {
  return (
    <DevGitPanel token="tok" sessionKey={sessionKey} selectedPath={null} onShowDiff={() => {}} />
  );
}

describe("DevGitPanel project isolation", () => {
  it("drops project A's late git answer once project B is shown", async () => {
    const view = render(panel("web:a"));
    await waitFor(() => expect(releaseA).not.toBeNull());

    view.rerender(panel("web:b"));
    expect(await screen.findByText("only-in-b.txt")).toBeTruthy();

    await act(async () => {
      releaseA?.(changes("only-in-a.txt", "feature-a"));
      await Promise.resolve();
    });

    expect(screen.queryByText("only-in-a.txt")).toBeNull();
    expect(screen.queryByText(/feature-a/)).toBeNull();
    expect(screen.getByText("only-in-b.txt")).toBeTruthy();
  });

  it("does not show project A's late failure on project B", async () => {
    const view = render(panel("web:a"));
    await waitFor(() => expect(rejectA).not.toBeNull());

    view.rerender(panel("web:b"));
    expect(await screen.findByText("only-in-b.txt")).toBeTruthy();

    await act(async () => {
      rejectA?.(new Error("repo A exploded"));
      await Promise.resolve();
    });

    expect(screen.queryByText(/repo A exploded/)).toBeNull();
    expect(screen.getByText("only-in-b.txt")).toBeTruthy();
  });

  it("clears the previous project's files as soon as the session changes", async () => {
    const view = render(panel("web:b"));
    expect(await screen.findByText("only-in-b.txt")).toBeTruthy();

    view.rerender(panel("web:a"));
    // A is still loading: B's file list must not linger as if it were A's.
    expect(screen.queryByText("only-in-b.txt")).toBeNull();
  });
});
