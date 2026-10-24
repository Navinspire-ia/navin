// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";
import { SiteLoginPicker } from "@/components/montage/SiteLoginPicker";
import type { BrowserCredentialSite } from "@/lib/browser-credentials-api";

import { SiteLoginsPanel } from "./SiteLoginsPanel";

const PASSWORD = "Pa55-word-Never-Shown";

interface Site { site: string; names: string[]; refs: string[]; session: boolean; updated: number }

let sites: Site[] = [];
const requests: { action: string; body: Record<string, unknown> }[] = [];

function decodeBody(headers: Record<string, string>): Record<string, unknown> {
  const chunks = Object.keys(headers)
    .filter((key) => key.startsWith("X-Navin-File-Body-"))
    .sort((a, b) => Number(a.split("-").pop()) - Number(b.split("-").pop()))
    .map((key) => headers[key])
    .join("");
  const bytes = Uint8Array.from(atob(chunks), (char) => char.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>;
}

function row(site: string, names: string[]): Site {
  return { site, names, refs: names.map((n) => `site:${site}/${n}`), session: false, updated: 1760000000 };
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
  sites = [];
  requests.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const action = new URL(url, "http://x").searchParams.get("action") ?? "";
      const body = decodeBody((init?.headers ?? {}) as Record<string, string>);
      requests.push({ action, body });
      if (action === "set") {
        const site = String(body.site);
        const names = Object.keys(body.values as Record<string, string>).sort();
        sites = [...sites.filter((s) => s.site !== site), row(site, names)];
      }
      return new Response(JSON.stringify({ sites, vault: "keyring" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SiteLoginsPanel (embedded)", () => {
  it("saves the marketing product login through the vault API, prefilled from the desk", async () => {
    const onSaved = vi.fn();
    const { container } = render(
      <SiteLoginsPanel compact token="tok" site="https://app.acme.io/login" onSaved={onSaved} />,
    );
    expect(await screen.findByTestId("site-logins-panel")).toBeTruthy();
    await waitFor(() => expect(requests[0]).toEqual({ action: "list", body: {} }));
    expect((screen.getByTestId("site-url") as HTMLInputElement).value).toBe("https://app.acme.io/login");
    expect(screen.getByText("Saved as app.acme.io")).toBeTruthy();

    fireEvent.change(screen.getByTestId("site-username"), { target: { value: "demo@acme.io" } });
    fireEvent.change(screen.getByTestId("site-password"), { target: { value: PASSWORD } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("site-save"));
    });

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(onSaved.mock.calls[0][0]).toBe("app.acme.io");
    expect(requests.find((r) => r.action === "set")?.body).toEqual({
      site: "app.acme.io",
      values: { username: "demo@acme.io", password: PASSWORD },
    });
    // The saved site shows in the compact list and the secret left the DOM.
    expect(await screen.findByText("app.acme.io")).toBeTruthy();
    expect(container.innerHTML).not.toContain(PASSWORD);
  });

  it("lets Montage add a login inline and selects it", async () => {
    const onChange = vi.fn<(next: BrowserCredentialSite | null) => void>();
    render(<SiteLoginPicker token="tok" value={null} onChange={onChange} />);
    fireEvent.click(await screen.findByTestId("site-login-picker-add"));
    fireEvent.change(await screen.findByTestId("site-url"), { target: { value: "localhost:5173" } });
    fireEvent.change(screen.getByTestId("site-password"), { target: { value: PASSWORD } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("site-save"));
    });
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ site: "localhost:5173" })));
    expect(screen.queryByTestId("site-logins-panel")).toBeNull();
  });
});
