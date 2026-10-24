// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "@/i18n";
import type { InboundEvent } from "@/lib/types";

const PASSWORD = "Pa55-word-Never-Shown";

const handlers = new Map<string, (ev: InboundEvent) => void>();
const fakeClient = {
  status: "open",
  onStatus: () => () => {},
  onChat: vi.fn((chatId: string, handler: (ev: InboundEvent) => void) => {
    handlers.set(chatId, handler);
    return () => handlers.delete(chatId);
  }),
  sendCredentialAnswer: vi.fn(),
  sendMessage: vi.fn(),
};

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({ client: fakeClient, token: "tok" }),
}));

vi.mock("@/components/settings/GitConnectPanel", () => ({
  GitConnectPanel: ({ host, onConnected }: { host: string; onConnected?: (login: string | null) => void }) => (
    <button type="button" data-testid="fake-git-connect" onClick={() => onConnected?.("octocat")}>
      connect {host}
    </button>
  ),
}));

import { CredentialRequestCards } from "./CredentialRequestCard";

const requests: { url: string; body: Record<string, unknown> }[] = [];

function decodeBody(headers: Record<string, string>): Record<string, unknown> {
  const chunks = Object.keys(headers)
    .filter((key) => key.startsWith("X-Navin-File-Body-"))
    .sort((a, b) => Number(a.split("-").pop()) - Number(b.split("-").pop()))
    .map((key) => headers[key])
    .join("");
  if (!chunks) return {};
  const bytes = Uint8Array.from(atob(chunks), (char) => char.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>;
}

function emit(ev: Record<string, unknown>) {
  act(() => handlers.get("c1")?.(ev as unknown as InboundEvent));
}

beforeEach(() => {
  requests.length = 0;
  handlers.clear();
  fakeClient.sendCredentialAnswer.mockClear();
  fakeClient.sendMessage.mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({ url: String(url), body: decodeBody((init?.headers ?? {}) as Record<string, string>) });
      const names = ["password", "username"];
      return new Response(
        JSON.stringify({
          sites: [{ site: "app.example.com", names, refs: names.map((n) => `site:app.example.com/${n}`), session: false }],
          vault: "encrypted-file",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CredentialRequestCards", () => {
  it("saves a site login through the vault API and sends only a status on the socket", async () => {
    render(<CredentialRequestCards chatId="c1" />);
    emit({
      event: "credential_request",
      chat_id: "c1",
      request_id: "r1",
      kind: "site_login",
      host: "app.example.com",
      names: ["username", "password"],
      reason: "Log in to export the monthly report",
    });
    expect(await screen.findByTestId("credential-request-card")).toBeTruthy();
    expect(screen.getByText("Log in to export the monthly report")).toBeTruthy();
    expect(screen.getByText("Sign in to app.example.com")).toBeTruthy();

    fireEvent.change(screen.getByTestId("site-username"), { target: { value: "me@example.com" } });
    fireEvent.change(screen.getByTestId("site-password"), { target: { value: PASSWORD } });
    fireEvent.click(screen.getByTestId("site-save"));

    await waitFor(() => expect(fakeClient.sendCredentialAnswer).toHaveBeenCalledTimes(1));
    const save = requests.find((r) => r.url.includes("action=set"));
    expect(save?.url).toContain("/api/browser-credentials");
    expect(save?.body).toEqual({
      site: "app.example.com",
      values: { username: "me@example.com", password: PASSWORD },
    });
    expect(fakeClient.sendCredentialAnswer).toHaveBeenCalledWith("r1", "saved", "");
    expect(JSON.stringify(fakeClient.sendCredentialAnswer.mock.calls)).not.toContain(PASSWORD);
    expect(fakeClient.sendMessage).not.toHaveBeenCalled();

    emit({
      event: "credential_closed",
      chat_id: "c1",
      request_id: "r1",
      status: "saved",
      refs: ["site:app.example.com/username", "site:app.example.com/password"],
    });
    expect(await screen.findByTestId("credential-result")).toBeTruthy();
    expect(screen.getByText("Saved")).toBeTruthy();
    expect(screen.getByText("site:app.example.com/password")).toBeTruthy();
    expect(screen.queryByTestId("credential-request-card")).toBeNull();
    expect(document.body.innerHTML).not.toContain(PASSWORD);
  });

  it("declines without saving anything", async () => {
    render(<CredentialRequestCards chatId="c1" />);
    emit({ event: "credential_request", chat_id: "c1", request_id: "r2", kind: "site_login", host: "app.example.com" });
    fireEvent.click(await screen.findByTestId("credential-decline"));
    expect(fakeClient.sendCredentialAnswer).toHaveBeenCalledWith("r2", "declined", "");
    expect(requests.some((r) => r.url.includes("action=set"))).toBe(false);
  });

  it("git_host connects through the git panel and reports the login", async () => {
    render(<CredentialRequestCards chatId="c1" />);
    emit({ event: "credential_request", chat_id: "c1", request_id: "r3", kind: "git_host", host: "github.com", reason: "Push the branch" });
    expect(await screen.findByText("Connect github.com")).toBeTruthy();
    fireEvent.click(screen.getByTestId("fake-git-connect"));
    expect(fakeClient.sendCredentialAnswer).toHaveBeenCalledWith("r3", "connected", "octocat");
    emit({ event: "credential_closed", chat_id: "c1", request_id: "r3", status: "connected", login: "octocat" });
    expect(await screen.findByText("Connected as octocat")).toBeTruthy();
  });

  it("a turn end clears a card left open", async () => {
    render(<CredentialRequestCards chatId="c1" />);
    emit({ event: "credential_request", chat_id: "c1", request_id: "r4", kind: "git_host", host: "github.com" });
    expect(await screen.findByTestId("credential-request-card")).toBeTruthy();
    emit({ event: "turn_end", chat_id: "c1" });
    expect(screen.queryByTestId("credential-request-card")).toBeNull();
  });
});
