// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deleteBrowserCredentialSite,
  forgetBrowserSession,
  listBrowserCredentials,
  normalizeSite,
  removeBrowserCredential,
  saveBrowserCredentials,
  siteLoginInstruction,
} from "@/lib/browser-credentials-api";

const SNAPSHOT = { sites: [], vault: "keyring" };

function decodeBody(headers: Record<string, string>): unknown {
  const chunks = Object.keys(headers)
    .filter((key) => key.startsWith("X-Navin-File-Body-"))
    .sort((a, b) => Number(a.split("-").pop()) - Number(b.split("-").pop()))
    .map((key) => headers[key])
    .join("");
  const bytes = Uint8Array.from(atob(chunks), (char) => char.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

function mockFetch() {
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () =>
    new Response(JSON.stringify(SNAPSHOT), { status: 200, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function call(fetchMock: ReturnType<typeof mockFetch>, index = 0) {
  const [url, init] = fetchMock.mock.calls[index] as [string, RequestInit];
  const headers = init.headers as Record<string, string>;
  return { url, init, headers, body: decodeBody(headers) };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("browser credentials API", () => {
  it("uses the accounts request shape: GET, bearer token, body in chunked headers", async () => {
    const fetchMock = mockFetch();
    await expect(listBrowserCredentials("tok")).resolves.toEqual(SNAPSHOT);
    const { url, init, headers, body } = call(fetchMock);
    expect(url).toBe("/api/browser-credentials?action=list");
    expect(init.method).toBeUndefined();
    expect(init.body).toBeUndefined();
    expect(headers.Authorization).toBe("Bearer tok");
    expect(body).toEqual({});
  });

  it("sends only non-empty values on save", async () => {
    const fetchMock = mockFetch();
    await saveBrowserCredentials("tok", "app.example.com", { username: "ana", password: "s3cret!", totp: "  " });
    const { url, body } = call(fetchMock);
    expect(url).toBe("/api/browser-credentials?action=set");
    expect(body).toEqual({ site: "app.example.com", values: { username: "ana", password: "s3cret!" } });
  });

  it("never sends a request when there is nothing to save or no site", async () => {
    const fetchMock = mockFetch();
    await expect(saveBrowserCredentials("tok", "app.example.com", { username: "", password: "" })).rejects.toThrow();
    await expect(saveBrowserCredentials("tok", " ", { password: "x" })).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("builds delete, remove-one and forget_session bodies", async () => {
    const fetchMock = mockFetch();
    await removeBrowserCredential("tok", "app.example.com", "password");
    await deleteBrowserCredentialSite("tok", "app.example.com");
    await forgetBrowserSession("tok", "localhost:5173");
    expect(call(fetchMock, 0).url).toBe("/api/browser-credentials?action=delete");
    expect(call(fetchMock, 0).body).toEqual({ site: "app.example.com", name: "password" });
    expect(call(fetchMock, 1).body).toEqual({ site: "app.example.com" });
    expect(call(fetchMock, 2).url).toBe("/api/browser-credentials?action=forget_session");
    expect(call(fetchMock, 2).body).toEqual({ site: "localhost:5173" });
  });

  it("normalizes a URL like the gateway does", () => {
    expect(normalizeSite("https://App.Example.com:443/login?x=1")).toBe("app.example.com");
    expect(normalizeSite("http://app.example.com:80")).toBe("app.example.com");
    expect(normalizeSite("localhost:5173")).toBe("localhost:5173");
    expect(normalizeSite("https://staging.example.com:8443/")).toBe("staging.example.com:8443");
    expect(normalizeSite("site:app.example.com/password")).toBe("app.example.com");
    expect(normalizeSite("")).toBe("");
    expect(normalizeSite("not a url")).toBe("");
  });

  it("hands the agent refs only", () => {
    const text = siteLoginInstruction({ site: "app.example.com", names: ["password", "username"] });
    expect(text).toContain("site:app.example.com/password");
    expect(text).toContain("site:app.example.com/username");
    expect(text).toContain("browser(action=login, url=https://app.example.com)");
    expect(siteLoginInstruction({ site: "localhost:5173", names: ["password"] })).toContain("url=http://localhost:5173");
  });
});
