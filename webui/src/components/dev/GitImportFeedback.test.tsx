// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useGitImport } from "@/hooks/useGitImport";
import i18n from "@/i18n";
import { setUnauthorizedRefresher } from "@/lib/api";
import { clearPendingGitImport, normalizeGitImportUrl } from "@/lib/git-auth";
import { OPEN_SETTINGS_SECTION_EVENT } from "@/lib/settings-events";

import { GitImportFeedback } from "./GitImportFeedback";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function Harness({ onImported }: { onImported: (path: string) => void }) {
  const state = useGitImport({
    token: "tok",
    parent: "/home/me/Projects",
    onImported: (result) => onImported(result.path),
  });
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void state.importRepo();
      }}
    >
      <input aria-label="repo" value={state.url} onChange={(e) => state.setUrl(e.target.value)} />
      <button type="submit">Import</button>
      <GitImportFeedback state={state} />
    </form>
  );
}

const fetchMock = vi.fn();

beforeEach(async () => {
  await i18n.changeLanguage("en");
  clearPendingGitImport();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  setUnauthorizedRefresher(null);
});

async function submit(value: string) {
  fireEvent.change(screen.getByLabelText("repo"), { target: { value } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
  });
}

describe("Import Git sign-in path", () => {
  it("turns a 401 auth_required into Connect, then Retry clones", async () => {
    const refresher = vi.fn(async () => "other-token");
    setUnauthorizedRefresher(refresher);
    fetchMock
      .mockResolvedValueOnce(json(401, {
        code: "auth_required",
        host: "github.com",
        message: "Authentication required",
      }))
      .mockResolvedValueOnce(json(200, { path: "/home/me/Projects/secret", name: "secret", url: "x" }));
    const onImported = vi.fn();
    const opened: string[] = [];
    const onOpen = (event: Event) => opened.push((event as CustomEvent).detail.section);
    window.addEventListener(OPEN_SETTINGS_SECTION_EVENT, onOpen);
    render(<Harness onImported={onImported} />);

    await submit("acme/secret");

    // owner/repo is expanded to a GitHub URL before cloning.
    const firstUrl = new URL(String(fetchMock.mock.calls[0][0]), "http://x");
    expect(firstUrl.pathname).toBe("/api/webui/git/clone");
    expect(firstUrl.searchParams.get("url")).toBe("https://github.com/acme/secret.git");
    // A forge sign-in is not a stale gateway token: no client refresh.
    expect(refresher).not.toHaveBeenCalled();

    const alert = screen.getByTestId("git-import-auth");
    expect(alert.textContent).toContain("This repository is private or needs sign-in for github.com");
    expect(alert.textContent).not.toContain("Import failed");
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();

    // Settings stays available as the secondary path.
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    expect(opened).toEqual(["git"]);
    window.removeEventListener(OPEN_SETTINGS_SECTION_EVENT, onOpen);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onImported).toHaveBeenCalledWith("/home/me/Projects/secret");
    expect(screen.queryByTestId("git-import-auth")).toBeNull();
  });

  it("keeps the pending retry when the field is remounted after Settings", async () => {
    fetchMock.mockResolvedValueOnce(json(401, {
      code: "auth_required",
      host: "gitlab.example.com",
      message: "Authentication required",
    }));
    const first = render(<Harness onImported={vi.fn()} />);
    await submit("https://gitlab.example.com/team/app.git");
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    first.unmount();

    render(<Harness onImported={vi.fn()} />);
    expect((screen.getByLabelText("repo") as HTMLInputElement).value).toBe(
      "https://gitlab.example.com/team/app.git",
    );
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
  });

  it("connects inline with a token, then clones again by itself", async () => {
    const calls: string[] = [];
    let clones = 0;
    fetchMock.mockImplementation(async (input: string) => {
      const url = new URL(String(input), "http://x");
      calls.push(url.pathname);
      if (url.pathname === "/api/webui/git/clone") {
        clones += 1;
        return clones === 1
          ? json(401, { code: "auth_required", host: "github.com", message: "Authentication required" })
          : json(200, { path: "/home/me/Projects/secret", name: "secret", url: "x" });
      }
      if (url.pathname === "/api/webui/git/accounts") {
        return json(200, { accounts: [], device_flow: { github: false } });
      }
      if (url.pathname === "/api/settings/update") {
        expect(url.searchParams.get("forge_host")).toBe("github.com");
        expect(url.searchParams.get("forge_token")).toBe("ghp_secret");
        return json(200, { forge: { hosts: [], env_tokens: [], gh_available: false, kinds: [] } });
      }
      if (url.pathname === "/api/webui/git/test") {
        return json(200, { ok: true, login: "octo" });
      }
      return json(404, { error: "unexpected" });
    });
    const onImported = vi.fn();
    const opened: string[] = [];
    const onOpen = (event: Event) => opened.push((event as CustomEvent).detail.section);
    window.addEventListener(OPEN_SETTINGS_SECTION_EVENT, onOpen);
    render(<Harness onImported={onImported} />);

    await submit("acme/secret");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Connect github.com" }));
    });
    // The sign-in card opens right here: no trip to Settings.
    expect(opened).toEqual([]);
    window.removeEventListener(OPEN_SETTINGS_SECTION_EVENT, onOpen);
    const panel = await screen.findByTestId("git-connect-panel");
    expect(panel.textContent).toContain("Connect github.com");
    const tokenInput = await screen.findByLabelText("Token");
    fireEvent.change(tokenInput, { target: { value: "ghp_secret" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Save and test/ }));
    });

    await waitFor(() => expect(onImported).toHaveBeenCalledWith("/home/me/Projects/secret"));
    expect(clones).toBe(2);
    expect(calls).toContain("/api/settings/update");
    // The outer Import form was not submitted by the token field.
    expect(calls.filter((path) => path === "/api/webui/git/clone")).toHaveLength(2);
    expect(screen.queryByTestId("git-import-auth")).toBeNull();
  });

  it("keeps other errors readable", async () => {
    fetchMock.mockResolvedValueOnce(json(400, { error: "Repository not found" }));
    render(<Harness onImported={vi.fn()} />);
    await submit("https://github.com/acme/missing.git");
    expect(screen.getByRole("alert").textContent).toBe("Import failed: Repository not found");
    expect(screen.queryByRole("button", { name: /Connect/ })).toBeNull();
  });

  it("only expands a bare owner/repo", () => {
    expect(normalizeGitImportUrl(" acme/app ")).toBe("https://github.com/acme/app.git");
    expect(normalizeGitImportUrl("acme/app.git")).toBe("https://github.com/acme/app.git");
    expect(normalizeGitImportUrl("git@github.com:acme/app.git")).toBe("git@github.com:acme/app.git");
    expect(normalizeGitImportUrl("https://gitlab.com/a/b")).toBe("https://gitlab.com/a/b");
  });
});
