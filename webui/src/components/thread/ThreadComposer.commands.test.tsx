// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThreadComposer } from "./ThreadComposer";

const mock = vi.hoisted(() => ({
  t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  useTranslation: () => ({ t: mock.t, i18n: { language: "en" } }),
}));
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: null }) }));

beforeEach(() => {
  window.localStorage.clear();
  window.location.hash = "#/code?chat=websocket%3Ademo";
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function enter(content: string) {
  const input = screen.getByRole("textbox", { name: "thread.composer.inputAria" });
  fireEvent.change(input, { target: { value: content } });
  fireEvent.keyDown(input, { key: "Enter" });
  return input as HTMLTextAreaElement;
}

describe("configuration commands in the chat composer", () => {
  it.each([
    ["/settings", "overview"], ["/models", "models"], ["/model", "models"],
    ["/settings providers", "providers"], [" /SETTINGS\tmodels ", "models"],
    ["/mcp", "tools"], ["/security", "advanced"], ["/skills", "skills"],
    ["/web", "browser"],
  ])("opens %s with one Enter without sending it to the model", (command, section) => {
    const send = vi.fn();
    render(<ThreadComposer onSend={send} sessionKey="websocket:demo" turnMode="agent" />);
    expect(enter(command).value).toBe("");
    expect(window.location.hash).toBe(`#/settings?chat=websocket%3Ademo&section=${section}`);
    expect(send).not.toHaveBeenCalled();
  });

  it("opens configuration while a turn is running and no model is configured", () => {
    const send = vi.fn();
    const stop = vi.fn();
    const setup = vi.fn();
    render(<ThreadComposer onSend={send} onStop={stop} isStreaming modelNeedsSetup onModelBadgeClick={setup} />);
    enter("/models");
    expect(window.location.hash).toBe("#/settings?section=models");
    expect(send).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
    expect(setup).not.toHaveBeenCalled();
    expect(screen.queryByTestId("composer-queue-list")).toBeNull();
  });

  it("opens a configuration suggestion when selected from the palette", () => {
    const send = vi.fn();
    render(<ThreadComposer onSend={send} />);
    enter("/sett");
    expect(window.location.hash).toBe("#/settings?section=overview");
    expect(send).not.toHaveBeenCalled();
  });

  it("also opens settings from the send button before a model has been configured", () => {
    const send = vi.fn();
    const setup = vi.fn();
    render(<ThreadComposer onSend={send} modelNeedsSetup onModelBadgeClick={setup} />);
    fireEvent.change(screen.getByRole("textbox", { name: "thread.composer.inputAria" }), { target: { value: "/settings" } });
    fireEvent.click(screen.getByRole("button", { name: "thread.composer.send" }));
    expect(window.location.hash).toBe("#/settings?section=overview");
    expect(send).not.toHaveBeenCalled();
    expect(setup).not.toHaveBeenCalled();
  });

  it("prioritizes the exact command over an engine suggestion mentioning models", () => {
    const send = vi.fn();
    render(<ThreadComposer onSend={send} slashCommands={[
      { command: "/forge", title: "Build", description: "Use models to build", icon: "hammer", lifecycle: "agent_turn", acceptsArgs: true },
    ]} />);
    enter("/models");
    expect(window.location.hash).toBe("#/settings?section=models");
    expect(send).not.toHaveBeenCalled();
  });

  it("shows an invalid settings section without starting an agent turn", () => {
    const send = vi.fn();
    render(<ThreadComposer onSend={send} />);
    enter("/settings not-a-section");
    expect(screen.getByText("Unknown settings section. Use /settings, /settings models or /settings providers.")).toBeTruthy();
    expect(window.location.hash).toContain("#/code");
    expect(send).not.toHaveBeenCalled();
  });

  it.each(["/permission auto", "/model my-preset", "/forge repair the app", "/skills list"])(
    "preserves engine commands and their arguments: %s", (command) => {
      const send = vi.fn();
      render(<ThreadComposer onSend={send} turnMode="agent" slashCommands={[
        { command: "/permission", title: "Permissions", description: "", icon: "shield", lifecycle: "side_channel", acceptsArgs: true },
      ]} />);
      enter(command);
      expect(send.mock.calls[0][0]).toBe(command);
      expect(window.location.hash).toContain("#/code");
      if (command.startsWith("/permission")) expect(send.mock.calls[0][2]).toMatchObject({ sideChannel: true });
    },
  );
});
