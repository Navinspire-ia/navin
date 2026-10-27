// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import i18n from "@/i18n";
import { MessageBubble } from "@/components/MessageBubble";
import type { UIMessage } from "@/lib/types";

beforeEach(async () => {
  await i18n.changeLanguage("en");
});
afterEach(cleanup);

const userTurn = (content: string) =>
  ({ id: "u1", role: "user", content, createdAt: 0 }) as unknown as UIMessage;

describe("App Factory request in the chat", () => {
  it("shows /apps android as a request card with the Android mark, not a bare word", () => {
    render(<MessageBubble message={userTurn("/apps android")} />);
    const card = screen.getByTestId("app-factory-request");
    expect(card.textContent).toContain("Create the Android app");
    expect(card.textContent).toContain(".apk / .aab");
    expect(card.textContent).toContain("os/android");
    expect(card.querySelector('svg[data-platform="android"]')).not.toBeNull();
    expect(card.textContent).not.toContain("/apps");
  });

  it("uses the platform's own mark for a desktop target, in the user's language", async () => {
    await i18n.changeLanguage("fr");
    render(<MessageBubble message={userTurn("/apps windows")} />);
    const card = screen.getByTestId("app-factory-request");
    expect(card.textContent).toContain("Créer l'application Windows");
    expect(card.querySelector('svg[data-platform="windows"]')).not.toBeNull();
  });

  it("keeps a longer message as normal text", () => {
    render(<MessageBubble message={userTurn("/apps android with push notifications")} />);
    expect(screen.queryByTestId("app-factory-request")).toBeNull();
    expect(document.body.textContent).toContain("with push notifications");
  });
});
