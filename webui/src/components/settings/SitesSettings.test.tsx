// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "@/i18n";
import i18n from "@/i18n";
import { SETTINGS_NAV_ITEMS } from "./SettingsView";
import { SitesSettings } from "./SitesSettings";
import { configurationCommand, configurationHash } from "@/lib/configuration-command";

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

function row(site: string, names: string[], session = false): Site {
  return { site, names, refs: names.map((n) => `site:${site}/${n}`), session, updated: 1760000000 };
}

beforeEach(() => {
  sites = [row("app.example.com", ["password", "username"], true)];
  requests.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const action = new URL(url, "http://x").searchParams.get("action") ?? "";
      const body = decodeBody((init?.headers ?? {}) as Record<string, string>);
      requests.push({ action, body });
      if (action === "set") {
        const values = body.values as Record<string, string>;
        const site = String(body.site);
        const current = sites.find((s) => s.site === site);
        const names = [...new Set([...(current?.names ?? []), ...Object.keys(values)])].sort();
        sites = [...sites.filter((s) => s.site !== site), row(site, names, current?.session ?? false)];
      } else if (action === "delete") {
        const site = String(body.site);
        const current = sites.find((s) => s.site === site);
        if (current && body.name) {
          const names = current.names.filter((n) => n !== body.name);
          sites = sites.map((s) => (s.site === site ? row(site, names, s.session) : s)).filter((s) => s.names.length || s.session);
        } else {
          sites = sites.filter((s) => s.site !== site);
        }
      } else if (action === "forget_session") {
        sites = sites.map((s) => (s.site === body.site ? { ...s, session: false } : s)).filter((s) => s.names.length || s.session);
      }
      return new Response(JSON.stringify({ sites, vault: "encrypted-file" }), {
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

describe("SitesSettings", () => {
  it("lists saved sites with refs, session badge and vault type", async () => {
    render(<SitesSettings token="tok" />);
    expect(await screen.findByText("app.example.com")).toBeTruthy();
    expect(screen.getByText("site:app.example.com/password")).toBeTruthy();
    expect(screen.getByText("site:app.example.com/username")).toBeTruthy();
    expect(screen.getByText("Session saved")).toBeTruthy();
    expect(screen.getByText("Encrypted file")).toBeTruthy();
    expect(screen.getByText("Ask the agent: log in with site:app.example.com and record a demo")).toBeTruthy();
    expect(requests[0]).toEqual({ action: "list", body: {} });
  });

  it("saves a login, normalizes the URL and clears the password from the DOM", async () => {
    const { container } = render(<SitesSettings token="tok" />);
    await screen.findByText("app.example.com");
    fireEvent.change(screen.getByTestId("site-url"), { target: { value: "http://localhost:5173/login" } });
    expect(screen.getByText("Saved as localhost:5173")).toBeTruthy();
    fireEvent.change(screen.getByTestId("site-username"), { target: { value: "demo@example.com" } });
    fireEvent.change(screen.getByTestId("site-password"), { target: { value: PASSWORD } });
    expect((screen.getByTestId("site-password") as HTMLInputElement).type).toBe("password");
    fireEvent.click(screen.getByTestId("site-save"));

    expect(await screen.findByText("localhost:5173")).toBeTruthy();
    const set = requests.find((r) => r.action === "set");
    expect(set?.body).toEqual({ site: "localhost:5173", values: { username: "demo@example.com", password: PASSWORD } });
    await waitFor(() => expect((screen.getByTestId("site-password") as HTMLInputElement).value).toBe(""));
    expect(container.innerHTML).not.toContain(PASSWORD);
    for (const input of Array.from(container.querySelectorAll("input"))) expect(input.value).not.toBe(PASSWORD);
    expect(screen.getByText("site:localhost:5173/password")).toBeTruthy();
  });

  it("removes the login, forgets the session and deletes a site", async () => {
    sites = [row("app.example.com", ["password", "username"], true), row("crm.example.com", ["password"], true)];
    render(<SitesSettings token="tok" />);
    await screen.findByText("crm.example.com");

    fireEvent.click(screen.getAllByRole("button", { name: /Forget saved session/ })[1]);
    await waitFor(() => expect(requests.some((r) => r.action === "forget_session" && r.body.site === "crm.example.com")).toBe(true));

    fireEvent.click(screen.getAllByRole("button", { name: /Remove login/ })[0]);
    await waitFor(() =>
      expect(requests.filter((r) => r.action === "delete").map((r) => r.body)).toEqual([
        { site: "app.example.com", name: "password" },
        { site: "app.example.com", name: "username" },
      ]),
    );
    // The session survives "Remove login".
    await waitFor(() => expect(screen.queryByText("site:app.example.com/password")).toBeNull());
    expect(screen.getByText("app.example.com")).toBeTruthy();

    fireEvent.click(screen.getAllByRole("button", { name: /Delete site/ })[0]);
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete site" }));
    await waitFor(() => expect(requests.some((r) => r.action === "delete" && !r.body.name && r.body.site === "app.example.com")).toBe(true));
    await waitFor(() => expect(screen.queryByText("app.example.com")).toBeNull());
  });

  it("is a nav item next to Git & GitHub, deep-linkable and labelled in en/fr/ar", () => {
    const keys = SETTINGS_NAV_ITEMS.map((item) => item.key);
    expect(keys.indexOf("sites")).toBe(keys.indexOf("git") - 1);
    expect(configurationHash("sites")).toBe("#/settings?section=sites");
    expect(configurationCommand("/settings sites")).toEqual({ section: "sites" });
    expect(i18n.getFixedT("en")("settings.nav.sites")).toBe("Sites & logins");
    expect(i18n.getFixedT("fr")("settings.nav.sites")).toBe("Sites et identifiants");
    expect(i18n.getFixedT("ar")("settings.nav.sites")).toBe("المواقع وبيانات الدخول");
  });
});
