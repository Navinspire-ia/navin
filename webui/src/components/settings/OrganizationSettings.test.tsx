// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only
// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OrganizationSettings } from "@/components/settings/OrganizationSettings";

const STATUS = {
  present: true,
  import_path: "/home/u/.navin/organization.json",
  source: "imported",
  locked: false,
  path: "/home/u/.navin/organization.json",
  error: "",
  exclusive: true,
  organization: { id: "bank-x", name: "Bank X", support_contact: "it@bank-x.example" },
  gateway: {
    base_url: "https://llm.bank-x.example",
    auth: "user_key",
    header_names: [],
    ca_bundle: null,
    default_model: "gpt-4o",
    routes: {},
    models: [
      {
        id: "gpt-4o",
        label: "GPT-4o (Azure)",
        preset: "org:gpt-4o",
        context_window: 128000,
        max_output_tokens: null,
        vision: true,
        reasoning_effort: null,
        default: true,
        active: true,
      },
    ],
  },
  user_key: { configured: false, hint: null },
};

function bodyOf(init: RequestInit | undefined): Record<string, unknown> {
  const headers = (init?.headers ?? {}) as Record<string, string>;
  const chunks = Object.keys(headers)
    .filter((key) => key.startsWith("X-Navin-File-Body-"))
    .sort((a, b) => Number(a.split("-").pop()) - Number(b.split("-").pop()))
    .map((key) => headers[key])
    .join("");
  return chunks ? (JSON.parse(atob(chunks)) as Record<string, unknown>) : {};
}

function json(payload: unknown): Response {
  return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OrganizationSettings", () => {
  it("offers to import the organization file when there is none", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ present: false, import_path: "/home/u/.navin/organization.json" })));
    const { container } = render(<OrganizationSettings token="t" />);
    await waitFor(() => expect(container.querySelector('input[type="file"]')).not.toBeNull());
    expect(container.querySelector('input[type="file"]')?.getAttribute("accept")).toContain(".json");
    expect(screen.getAllByRole("button").length).toBe(1);
  });

  it("shows the organization and its models, and saves the personal key", async () => {
    const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), body: bodyOf(init) });
        return json(
          String(url).includes("action=key")
            ? { ...STATUS, user_key: { configured: true, hint: "sk-l\u2022\u2022\u2022\u20223456" } }
            : STATUS,
        );
      }),
    );
    const { container } = render(<OrganizationSettings token="t" />);
    expect(await screen.findByText("Bank X")).toBeTruthy();
    expect(screen.getByText("GPT-4o (Azure)")).toBeTruthy();
    expect(screen.getByText("https://llm.bank-x.example")).toBeTruthy();
    expect(screen.getByText("it@bank-x.example")).toBeTruthy();

    const input = container.querySelector('input[type="password"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sk-litellm-user-123456" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    await waitFor(() => expect(calls.some((call) => call.url.includes("action=key"))).toBe(true));
    expect(calls.find((call) => call.url.includes("action=key"))?.body).toEqual({ api_key: "sk-litellm-user-123456" });
    await waitFor(() => expect(input.value).toBe(""));
  });
});
