// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "@/i18n";
import { ChannelSetupPanel } from "@/components/settings/channels/ChannelSetupPanel";
import { pageWindow } from "@/lib/connectors-api";
import type { NavinFeatureInfo } from "@/lib/types";

import { type ChannelCardEntry, ConnectorsSettings } from "./ConnectorsSettings";

const CATEGORIES = [
  { id: "communication", label: "Communication and messaging", label_fr: "Communication et messagerie", count: 6, connected: 1 },
  { id: "video", label: "Video conferencing", label_fr: "Visioconference", count: 3, connected: 0 },
  { id: "crm", label: "CRM and support", label_fr: "CRM et support", count: 3, connected: 0 },
  ...Array.from({ length: 9 }, (_, index) => ({
    id: `extra${index + 4}`,
    label: `Extra ${index + 4}`,
    label_fr: `Extra ${index + 4}`,
    count: 2,
    connected: 0,
  })),
];

function card(service: string, name: string, category: string, connected = false) {
  return {
    service,
    name,
    category,
    auth: "oauth",
    auth_types: ["oauth2"],
    homepage: `https://${service}.example.com`,
    description: `${name} connector`,
    icon: `${service}.svg`,
    actions: 12,
    status: connected ? { connected: true, enabled: true, account: "team-a", method: "oauth2" } : { connected: false },
  };
}

const calls: URL[] = [];

function respond(url: URL): unknown {
  const action = url.searchParams.get("action");
  if (action === "catalog") {
    const query = url.searchParams.get("q");
    const status = url.searchParams.get("status");
    if (query === "paged") {
      // A catalog larger than one request: two pages of one app.
      const page = Number(url.searchParams.get("page") || 1);
      const rows = page === 1 ? [card("slack", "Slack", "communication", true)] : [card("notion", "Notion", "documents")];
      return { items: rows, total: 2, page, page_size: 1, pages: 2, categories: CATEGORIES, matching: 2, catalog_total: 79, connected_total: 1 };
    }
    let items = [
      card("slack", "Slack", "communication", true),
      card("gmail", "Gmail", "communication"),
      ...Array.from({ length: 11 }, (_, index) => card(`meet${index}`, `Meet ${index}`, "video")),
      card("zoom", "Zoom", "video"),
      card("salesforce", "Salesforce", "crm"),
      card("hubspot", "HubSpot", "crm"),
    ];
    if (query === "nothing") items = [];
    if (status === "connected") items = items.filter((item) => item.status.connected);
    return { items, total: items.length, page: 1, page_size: 100, pages: 1, categories: CATEGORIES, matching: items.length, catalog_total: 79, connected_total: 1 };
  }
  if (action === "detail" && url.searchParams.get("service") === "hubspot") {
    // A key listed before the sign-in: the form still offers the sign-in first.
    return {
      ...card("hubspot", "HubSpot", "crm"),
      methods: [
        { type: "api_key", label: "Private app token", fields: [{ key: "apiKey", label: "Token", inputType: "password", required: true, secret: true }] },
        { type: "oauth2", label: "OAuth", fields: [], scopes: ["crm.objects.contacts.read"], family: "hubspot" },
      ],
      docs: "",
      base_url: "https://api.hubapi.com",
      oauth_app: {
        family: "hubspot", label: "HubSpot", client_id: "cid", has_secret: true, configured: true,
        redirect_uri: "http://127.0.0.1:8765/api/connectors/oauth/callback",
        suggested_redirect_uri: "http://127.0.0.1:8765/api/connectors/oauth/callback",
        app_url: "", services: ["hubspot"], scopes: [],
      },
      actions_page: { service: "hubspot", items: [], total: 0, page: 1, page_size: 10, pages: 1 },
      vault: "keyring",
    };
  }
  if (action === "detail") {
    return {
      ...card("slack", "Slack", "communication", true),
      methods: [{ type: "oauth2", label: "OAuth", fields: [], scopes: ["chat:write"], family: "slack" }],
      docs: "https://api.slack.com/methods",
      base_url: "https://slack.com/api",
      oauth_app: {
        family: "slack", label: "Slack", client_id: "cid", has_secret: true, configured: true,
        redirect_uri: "http://127.0.0.1:8765/api/connectors/oauth/callback",
        suggested_redirect_uri: "http://127.0.0.1:8765/api/connectors/oauth/callback",
        app_url: "https://api.slack.com/apps", services: ["slack", "slackbot"], scopes: ["chat:write"],
      },
      actions_page: { service: "slack", items: [], total: 0, page: 1, page_size: 10, pages: 1 },
      vault: "keyring",
    };
  }
  if (action === "actions") {
    return {
      service: "slack",
      items: [{ name: "send_message", description: "Send a message to a channel", op: "write", endpoint: "POST /chat.postMessage", params: [["channel", "string", 1, ""], ["text", "string", 1, ""]] }],
      total: 1,
      page: 1,
      page_size: 8,
      pages: 1,
    };
  }
  if (action === "test") return { service: "slack", name: "Slack", ok: true, status: { connected: true, enabled: true, account: "team-a" } };
  return {};
}

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const parsed = new URL(String(url), "http://localhost");
      calls.push(parsed);
      return new Response(JSON.stringify(respond(parsed)), { status: 200, headers: { "content-type": "application/json" } });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const catalogCalls = () => calls.filter((url) => url.searchParams.get("action") === "catalog");

describe("ConnectorsSettings", () => {
  it("shows every app with icons and status, pages locally and fetches the whole catalog once", async () => {
    render(<ConnectorsSettings token="tok" query="" />);
    const slack = await screen.findByTestId("connector-card-slack");
    expect(slack.textContent).toContain("Connected as team-a");
    const slackSrc = slack.querySelector("img")?.getAttribute("src") ?? "";
    expect(slackSrc).toMatch(/^data:image\/|slack/);
    expect(screen.getByTestId("connector-card-gmail").textContent).toContain("Not connected");
    expect(screen.getAllByTestId(/^connector-card-/)).toHaveLength(12);
    // The featured apps lead in their own order, then the others in catalog order.
    expect(screen.getAllByTestId(/^connector-card-/).slice(0, 4).map((el) => el.dataset.testid)).toEqual([
      "connector-card-gmail",
      "connector-card-slack",
      "connector-card-hubspot",
      "connector-card-meet0",
    ]);
    expect(screen.getByText("Page 1 of 2")).toBeTruthy();
    // The removed filters are gone.
    expect(screen.queryByTestId("connectors-auth")).toBeNull();
    expect(screen.queryByTestId("connectors-status")).toBeNull();
    expect(screen.queryByTestId("connectors-categories")).toBeNull();
    expect(screen.queryByText(/Keys and tokens stay/)).toBeNull();
    const call = catalogCalls().at(-1);
    expect(call?.searchParams.get("page_size")).toBe("100");
    expect(call?.searchParams.get("category")).toBeNull();

    fireEvent.click(screen.getByTestId("connectors-pagination-next"));
    expect(await screen.findByTestId("connector-card-salesforce")).toBeTruthy();
    expect(screen.queryByTestId("connector-card-slack")).toBeNull();
    expect(catalogCalls()).toHaveLength(1);
  });

  it("keeps only the categories of the active group and resets to page 1 when the group changes", async () => {
    const { rerender } = render(<ConnectorsSettings token="tok" query="" />);
    fireEvent.click(await screen.findByTestId("connectors-pagination-next"));
    expect(await screen.findByTestId("connector-card-salesforce")).toBeTruthy();
    rerender(<ConnectorsSettings token="tok" query="" categories={["crm", "prospecting"]} />);
    // Back on page 1, where HubSpot leads as a featured app.
    expect(await screen.findByTestId("connector-card-hubspot")).toBeTruthy();
    expect(await screen.findByTestId("connector-card-salesforce")).toBeTruthy();
    expect(screen.queryByTestId("connector-card-slack")).toBeNull();
    expect(screen.getAllByTestId(/^connector-card-/)).toHaveLength(2);
    expect(screen.queryByTestId("connectors-pagination")).toBeNull();
  });

  it("hides the section when the group has no app, and passes the status filter to the API", async () => {
    const { rerender, container } = render(<ConnectorsSettings token="tok" query="" categories={["erp"]} />);
    await waitFor(() => expect(catalogCalls()).toHaveLength(1));
    await waitFor(() => expect(container.querySelector('[data-testid="connectors-settings"]')).toBeNull());
    rerender(<ConnectorsSettings token="tok" query="" status="connected" />);
    expect(await screen.findByTestId("connector-card-slack")).toBeTruthy();
    expect(catalogCalls().at(-1)?.searchParams.get("status")).toBe("connected");
    await waitFor(() => expect(screen.getAllByTestId(/^connector-card-/)).toHaveLength(1));
    rerender(<ConnectorsSettings token="tok" query="nothing" />);
    await waitFor(() => expect(container.querySelector('[data-testid="connectors-settings"]')).toBeNull());
  });

  it("loads every catalog page when the apps outgrow one request", async () => {
    const onCounts = vi.fn();
    render(<ConnectorsSettings token="tok" query="paged" onCounts={onCounts} />);
    expect(await screen.findByTestId("connector-card-notion")).toBeTruthy();
    expect(screen.getByTestId("connector-card-slack")).toBeTruthy();
    expect(catalogCalls().map((url) => url.searchParams.get("page"))).toEqual(["1", "2"]);
    await waitFor(() => expect(onCounts).toHaveBeenLastCalledWith({ communication: 1, documents: 1 }));
  });

  it("reports the apps per category for the tab badges, and null when the catalog fails", async () => {
    const onCounts = vi.fn();
    const { rerender } = render(<ConnectorsSettings token="tok" query="" onCounts={onCounts} />);
    await waitFor(() => expect(onCounts).toHaveBeenLastCalledWith({ communication: 2, video: 12, crm: 2 }));
    rerender(<ConnectorsSettings token="tok" query="" status="connected" onCounts={onCounts} />);
    await waitFor(() => expect(onCounts).toHaveBeenLastCalledWith({ communication: 1 }));

    // A new callback from the parent is used without loading the catalog again.
    const loads = catalogCalls().length;
    const next = vi.fn();
    rerender(<ConnectorsSettings token="tok" query="" status="connected" onCounts={next} />);
    expect(catalogCalls()).toHaveLength(loads);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: "boom" }), { status: 500, headers: { "content-type": "application/json" } })),
    );
    rerender(<ConnectorsSettings token="tok" query="" status="available" onCounts={next} />);
    expect(await screen.findByTestId("connectors-error")).toBeTruthy();
    expect(next).toHaveBeenLastCalledWith(null);
  });

  it("opens the side panel with the connection state, a working test button and the agent actions", async () => {
    render(<ConnectorsSettings token="tok" query="" />);
    fireEvent.click(await screen.findByTestId("connector-card-slack"));
    expect(await screen.findByTestId("connector-detail")).toBeTruthy();
    expect(await screen.findByTestId("connector-connected")).toBeTruthy();
    expect(await screen.findByText("send_message")).toBeTruthy();
    expect(screen.getByTestId("connector-action-endpoint").textContent).toBe("POST /chat.postMessage");
    expect(screen.getByText("channel*, text*")).toBeTruthy();

    fireEvent.click(screen.getByTestId("connector-test"));
    expect(await screen.findByText("The connection works.")).toBeTruthy();
    expect(calls.some((url) => url.searchParams.get("action") === "test")).toBe(true);

    fireEvent.click(screen.getByTestId("connector-detail-close"));
    await waitFor(() => expect(screen.queryByTestId("connector-detail")).toBeNull());
  });

  const CHANNELS: readonly ChannelCardEntry[] = [
    { name: "telegram", label: "Telegram", kind: "Chat channel", bucket: "chat", icon: "channels/telegram.svg", connected: true, badges: ["Token"] },
    { name: "linkedin", label: "LinkedIn", kind: "Social network", bucket: "social", icon: "channels/linkedin.svg", connected: false, badges: ["OAuth"] },
    { name: "slack", label: "Slack", kind: "Chat channel", bucket: "chat", icon: "slack.svg", connected: false, badges: ["Token"], twin: "slack" },
  ];

  it("shows the channels as cards among the apps, with one card for a channel that is also an app", async () => {
    const onOpenChannel = vi.fn();
    const onCounts = vi.fn();
    const renderChannel = (name: string) => <p data-testid={`channel-setup-stub-${name}`}>{name}</p>;
    const props = { token: "tok", channels: CHANNELS, onOpenChannel, renderChannel, onCounts };
    const { rerender } = render(<ConnectorsSettings {...props} query="" />);
    const telegram = await screen.findByTestId("channel-card-telegram");
    expect(telegram.textContent).toContain("Chat channel");
    expect(telegram.textContent).toContain("Connected");
    expect(telegram.querySelector("img")?.getAttribute("src") ?? "").toMatch(/^data:image\/|telegram/);
    expect(screen.getByTestId("channel-card-linkedin").querySelector("img")?.getAttribute("src") ?? "").toMatch(/^data:image\/|linkedin/);
    // One grid: the featured apps first, the channels in the middle, then the other apps.
    const grid = screen.getByTestId("connectors-grid");
    const order = Array.from(
      grid.querySelectorAll<HTMLElement>('[data-testid^="connector-card-"], [data-testid^="channel-card-"]'),
      (el) => el.dataset.testid,
    );
    expect(order.slice(0, 6)).toEqual([
      "connector-card-gmail",
      "connector-card-slack",
      "connector-card-hubspot",
      "channel-card-telegram",
      "channel-card-linkedin",
      "connector-card-meet0",
    ]);
    // Slack shows once: the app card, marked as a channel too.
    expect(screen.queryByTestId("channel-card-slack")).toBeNull();
    expect(screen.getByTestId("connector-channel-badge-slack")).toBeTruthy();
    // The shared Slack card counts once, under its app category.
    await waitFor(() =>
      expect(onCounts).toHaveBeenLastCalledWith({ communication: 2, video: 12, crm: 2, "channel:chat": 1, "channel:social": 1 }),
    );

    fireEvent.click(telegram);
    expect(onOpenChannel).toHaveBeenCalledWith("telegram");

    // The shared card's dialog: the connection, then the channel setup, in a scrolling body.
    fireEvent.click(screen.getByTestId("connector-card-slack"));
    const body = await screen.findByTestId("connector-detail-body");
    expect(body.className).toContain("overflow-y-auto");
    const connection = screen.getByTestId("connector-connection");
    const channel = screen.getByTestId("connector-channel");
    expect(connection.compareDocumentPosition(channel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("channel-setup-stub-slack")).toBeTruthy();
    fireEvent.click(screen.getByTestId("connector-detail-close"));
    await waitFor(() => expect(screen.queryByTestId("connector-detail")).toBeNull());

    // A tab keeps its own channel buckets and app categories.
    rerender(<ConnectorsSettings {...props} query="" buckets={["social"]} categories={["social"]} />);
    expect(await screen.findByTestId("channel-card-linkedin")).toBeTruthy();
    expect(screen.queryByTestId("channel-card-telegram")).toBeNull();
    expect(screen.queryByTestId("connector-card-slack")).toBeNull();

    // When the app is not listed, the channel keeps a card of its own.
    rerender(<ConnectorsSettings {...props} query="nothing" />);
    expect(await screen.findByTestId("channel-card-slack")).toBeTruthy();
    expect(screen.queryByTestId("connector-card-slack")).toBeNull();
  });

  it("opens an app with the sign-in first when it offers one", async () => {
    render(<ConnectorsSettings token="tok" query="" categories={["crm"]} />);
    fireEvent.click(await screen.findByTestId("connector-card-hubspot"));
    await screen.findByTestId("connector-form-hubspot");
    const methods = screen.getAllByRole("tab");
    expect(methods.map((tab) => tab.getAttribute("data-testid"))).toEqual(["connector-method-oauth2", "connector-method-api_key"]);
    expect(methods[0].getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("connector-oauth-start")).toBeTruthy();
    expect(screen.queryByTestId("connector-key-form")).toBeNull();

    // The kind of action sits on the row of the action search, drawn like the sign-in choice.
    const op = screen.getByTestId("connector-actions-op");
    const toolbar = screen.getByTestId("connector-actions-toolbar");
    expect(op.parentElement).toBe(toolbar);
    expect(toolbar.contains(screen.getByTestId("connector-actions-search"))).toBe(true);
    expect(op.className).toBe(screen.getByRole("tablist").className);
    expect(op.querySelector('[role="radio"][aria-checked="true"]')?.className).toBe(methods[0].className);
  });

  it("sets a channel up in a dialog body that scrolls, the keys before the guide", () => {
    const onClose = vi.fn();
    const feature: NavinFeatureInfo = {
      name: "slack",
      display_name: "Slack",
      type: "channel",
      enabled: false,
      configured: false,
      installed: true,
      ready: false,
      status: "not_enabled",
      install_supported: true,
      requires_restart: false,
    };
    render(
      <ChannelSetupPanel
        token="tok"
        feature={feature}
        actionKey={null}
        showBrandLogos={false}
        onAction={vi.fn()}
        onFeaturesUpdate={vi.fn()}
        variant="dialog"
        onClose={onClose}
      />,
    );
    const body = screen.getByTestId("channel-detail-body");
    expect(body.className).toContain("overflow-y-auto");
    const firstField = body.querySelector("input");
    const guide = screen.getByTestId("channel-setup-help");
    expect(firstField).toBeTruthy();
    expect((firstField?.compareDocumentPosition(guide) ?? 0) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(screen.getByTestId("channel-detail-close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("page numbers keep the first, the last and the neighbours", () => {
    expect(pageWindow(1, 5)).toEqual([1, 2, 3, 4, 5]);
    expect(pageWindow(1, 12)).toEqual([1, 2, "gap", 12]);
    expect(pageWindow(6, 12)).toEqual([1, "gap", 5, 6, 7, "gap", 12]);
    expect(pageWindow(12, 12)).toEqual([1, "gap", 11, 12]);
  });
});
