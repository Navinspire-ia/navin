// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MetagraphSidePanel } from "@/components/thread/MetagraphSidePanel";
import type { NavinClient } from "@/lib/navin-client";
import { ClientProvider } from "@/providers/ClientProvider";

function fakeClient(): NavinClient {
  return {
    status: "open",
    onStatus: () => () => {},
  } as unknown as NavinClient;
}

function render(sessionKey: string | null): string {
  return renderToStaticMarkup(
    createElement(
      ClientProvider,
      {
        client: fakeClient(),
        token: "t",
        children: createElement(MetagraphSidePanel, {
          sessionKey,
          onClose: () => {},
        }),
      },
    ),
  );
}

describe("MetagraphSidePanel", () => {
  it("renders the graph panel beside the chat with a close control", () => {
    const html = render("websocket:chat-1");
    expect(html).toContain('data-testid="metagraph-side-panel"');
    expect(html).toContain("Project graph");
    expect(html).toContain("Close");
  });

  it("animates in from the right edge", () => {
    const html = render(null);
    expect(html).toContain("translate-x-full");
  });
});