// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "@/i18n";
import type { InboundEvent } from "@/lib/types";

const PASSWORD = "Pa55-word-Never-Shown";
const API_KEY = "hunter-Key-Never-Shown-123";
const HUNTER_DETAIL = {
  service: "hunter",
  name: "Hunter",
  category: "prospecting",
  auth: "apikey",
  auth_types: ["api_key"],
  homepage: "https://hunter.io",
  description: "Find and verify professional email addresses.",
  icon: "hunter.png",
  actions: 9,
  status: { connected: false },
  methods: [
    {
      type: "api_key",
      label: "API Key",
      fields: [{ key: "apiKey", label: "API Key", inputType: "password", required: true, secret: true }],
    },
  ],
  docs: "https://hunter.io/api-documentation/v2",
  base_url: "https://api.hunter.io/v2",
  actions_page: { service: "hunter", items: [], total: 0, page: 1, page_size: 10, pages: 1 },
  vault: "encrypted-file",
};

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
let connectAttempts = 0;

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
  connectAttempts = 0;
  handlers.clear();
  fakeClient.sendCredentialAnswer.mockClear();
  fakeClient.sendMessage.mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({ url: String(url), body: decodeBody((init?.headers ?? {}) as Record<string, string>) });
      if (String(url).includes("/api/connectors")) {
        if (String(url).includes("action=connect") && ++connectAttempts === 1) {
          return new Response("Hunter refused this credential (HTTP 401): No user found for the API key supplied.", {
            status: 400,
            headers: { "content-type": "text/plain" },
          });
        }
        const payload = String(url).includes("action=connect")
          ? { service: "hunter", name: "Hunter", status: { connected: true, account: "agent@example.com" } }
          : HUNTER_DETAIL;
        return new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } });
      }
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

  it("connector card checks an API key through the connectors API and sends only the account on the socket", async () => {
    render(<CredentialRequestCards chatId="c1" />);
    emit({
      event: "credential_request",
      chat_id: "c1",
      request_id: "r5",
      kind: "connector",
      host: "hunter",
      reason: "Find the emails of the new leads",
    });
    expect(await screen.findByText("Connect Hunter")).toBeTruthy();
    expect(screen.getByText("App connection needed")).toBeTruthy();
    expect(screen.getByText("Find the emails of the new leads")).toBeTruthy();
    expect(requests.some((r) => r.url.includes("/api/connectors?action=detail&service=hunter"))).toBe(true);

    fireEvent.click(screen.getByTestId("connector-connect"));
    expect(await screen.findByText("API Key is required.")).toBeTruthy();
    expect(requests.some((r) => r.url.includes("action=connect"))).toBe(false);

    fireEvent.change(screen.getByTestId("connector-field-apiKey"), { target: { value: "wrong-key-000000" } });
    fireEvent.click(screen.getByTestId("connector-connect"));
    expect(await screen.findByText("That did not work.")).toBeTruthy();
    expect(screen.getByText(/No user found for the API key supplied/)).toBeTruthy();
    expect(fakeClient.sendCredentialAnswer).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("connector-field-apiKey"), { target: { value: API_KEY } });
    fireEvent.click(screen.getByTestId("connector-connect"));
    await waitFor(() => expect(fakeClient.sendCredentialAnswer).toHaveBeenCalledWith("r5", "connected", "agent@example.com"));
    const connect = requests.filter((r) => r.url.includes("action=connect")).at(-1);
    expect(connect?.body).toEqual({ service: "hunter", method: "api_key", values: { apiKey: API_KEY } });
    expect(JSON.stringify(fakeClient.sendCredentialAnswer.mock.calls)).not.toContain(API_KEY);
    expect(fakeClient.sendMessage).not.toHaveBeenCalled();

    emit({ event: "credential_closed", chat_id: "c1", request_id: "r5", status: "connected", login: "agent@example.com" });
    expect(await screen.findByText("Connected as agent@example.com")).toBeTruthy();
    expect(document.body.innerHTML).not.toContain(API_KEY);
  });
});
