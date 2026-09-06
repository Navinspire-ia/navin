import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import { ConnectionBadge, connectionBadgeState } from "@/components/ConnectionBadge";
import { noteGatewayStalled, resetGatewayPace } from "@/lib/gateway-pace";
import type { NavinClient } from "@/lib/navin-client";
import type { ConnectionStatus } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

function fakeClient(status: ConnectionStatus): NavinClient {
  return {
    status,
    onStatus: () => () => {},
  } as unknown as NavinClient;
}

function render(status: ConnectionStatus): string {
  return renderToStaticMarkup(
    createElement(ClientProvider, {
      client: fakeClient(status),
      token: "t",
      children: createElement(ConnectionBadge),
    }),
  );
}

describe("connectionBadgeState", () => {
  it("is green only when the socket is open and the engine answers", () => {
    expect(connectionBadgeState("open", false)).toBe("connected");
    expect(connectionBadgeState("open", true)).toBe("busy");
    expect(connectionBadgeState("connecting", false)).toBe("busy");
    expect(connectionBadgeState("reconnecting", false)).toBe("busy");
    expect(connectionBadgeState("idle", false)).toBe("busy");
    expect(connectionBadgeState("closed", false)).toBe("down");
    expect(connectionBadgeState("error", true)).toBe("down");
  });
});

describe("ConnectionBadge", () => {
  beforeEach(() => resetGatewayPace());

  it("renders a quiet green dot with the connection label when all is well", () => {
    const html = render("open");
    expect(html).toContain('data-state="connected"');
    expect(html).toContain("bg-emerald-500");
    expect(html).not.toContain("animate-ping");
    expect(html).not.toContain("data-stall=");
  });

  it("turns amber and pulses while the engine is stalled, naming the engine in the hint", () => {
    noteGatewayStalled("timeout");
    const html = render("open");
    expect(html).toContain('data-state="busy"');
    expect(html).toContain('data-stall="timeout"');
    expect(html).toContain("animate-ping");
    expect(html).toContain("bg-amber-500");
    expect(html).not.toMatch(/browser|navigateur|\d+ms/i);
  });

  it("is red when the socket is gone, whatever the pace says", () => {
    noteGatewayStalled("unreachable");
    const html = render("error");
    expect(html).toContain('data-state="down"');
    expect(html).toContain("bg-red-500");
    expect(html).not.toContain("animate-ping");
  });

  it("is mounted in the sidebar footer, next to the notification bell", () => {
    const sidebar = readFileSync(resolve(__dirname, "./Sidebar.tsx"), "utf8");
    expect(sidebar).toContain('import { ConnectionBadge } from "@/components/ConnectionBadge";');
    expect(sidebar).toContain('<ConnectionBadge />\n        <NotificationCenter placement="sidebar" />');
  });
});
