// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetMenuChoiceForTests } from "./menuChoice";
import { ThreadComposer, type ComposerTeamMode } from "./ThreadComposer";

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

describe("team picker in the model menu", () => {
  function renderPicker(team: Partial<ComposerTeamMode> = {}) {
    const onSelectTeam = vi.fn();
    const onModelSelect = vi.fn();
    const view = render(
      <ThreadComposer
        onSend={vi.fn()}
        modelLabel="GLM 5.2"
        modelOptions={[
          { name: "glm", label: "GLM 5.2", model: "z-ai/glm-5.2", provider: "zai", active: true },
          { name: "opus", label: "Opus", model: "anthropic/claude-opus-5", provider: "anthropic", active: false },
        ]}
        onModelSelect={onModelSelect}
        teamMode={{
          enabled: true,
          teamId: "dev",
          teams: [{ id: "dev", name: "Dev" }, { id: "sales", name: "Sales" }],
          members: [],
          onToggle: vi.fn(),
          onSelectTeam,
          ...team,
        }}
      />,
    );
    const trigger = screen.getByRole("button", { name: "Choose model" });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    return { ...view, onSelectTeam, onModelSelect };
  }

  beforeEach(() => resetMenuChoiceForTests());

  it("switches the team on press, once, even when WebKit lands the click on another row", async () => {
    const { onSelectTeam, onModelSelect } = renderPicker();
    const sales = await screen.findByTestId("composer-team-option-sales");
    fireEvent.pointerDown(sales, { button: 0, pointerType: "mouse" });
    // WebKitGTK / WKWebView: the click of that same press reaches another row.
    fireEvent.click(screen.getByTestId("composer-team-option-off"));

    expect(onSelectTeam).toHaveBeenCalledTimes(1);
    expect(onSelectTeam).toHaveBeenCalledWith("sales");
    expect(onModelSelect).not.toHaveBeenCalled();
    // The menu stays open so the user sees the check move.
    expect(screen.getByTestId("composer-team-picker")).toBeTruthy();
  });

  it("still switches from the keyboard or a tap, and shows a failed switch", async () => {
    const { onSelectTeam } = renderPicker({ switchFailed: true });
    fireEvent.click(await screen.findByTestId("composer-team-option-off"));
    expect(onSelectTeam).toHaveBeenCalledWith("off");
    expect(screen.getByRole("alert").textContent).toContain("The team could not be changed");
    // The current team is not sent again.
    fireEvent.click(screen.getByTestId("composer-team-option-dev"));
    expect(onSelectTeam).toHaveBeenCalledTimes(1);
  });
});

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

  it("opens the channels and app connectors from the Connector button without sending", () => {
    const send = vi.fn();
    render(<ThreadComposer onSend={send} sessionKey="websocket:demo" turnMode="agent" />);
    const button = screen.getByRole("button", { name: "Connector" });
    expect(button.getAttribute("title")).toBe("Connect apps the agent can use");
    fireEvent.click(button);
    expect(window.location.hash).toBe("#/settings?chat=websocket%3Ademo&section=channels");
    expect(send).not.toHaveBeenCalled();
  });

  it("disables the Connector button with the composer", () => {
    render(<ThreadComposer onSend={vi.fn()} disabled />);
    const button = screen.getByRole("button", { name: "Connector" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(window.location.hash).toBe("#/code?chat=websocket%3Ademo");
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
