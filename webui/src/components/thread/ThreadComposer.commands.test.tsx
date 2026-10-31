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

  it("turns Team mode off from the switch and says so when it could not", async () => {
    const onToggle = vi.fn();
    renderPicker({ onToggle, toggleFailed: true, enabled: false });
    // Off: no team list under the switch, but the failure stays readable.
    expect(screen.queryByTestId("composer-team-picker")).toBeNull();
    expect((await screen.findByTestId("composer-team-toggle-failed")).textContent).toContain(
      "Team mode could not be switched",
    );
    fireEvent.pointerDown(screen.getByTestId("composer-team-toggle"), { button: 0, pointerType: "mouse" });
    fireEvent.click(screen.getByTestId("composer-team-toggle"));
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(onToggle).toHaveBeenCalledWith(true);
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

describe("messages typed while the agent works", () => {
  function typeAndPress(content: string, init: { altKey?: boolean } = {}) {
    const input = screen.getByRole("textbox", { name: "thread.composer.inputAria" });
    fireEvent.change(input, { target: { value: content } });
    fireEvent.keyDown(input, { key: "Enter", ...init });
  }

  it("line up in the queue with their options instead of joining the live turn", async () => {
    const send = vi.fn();
    render(<ThreadComposer onSend={send} onStop={vi.fn()} isStreaming />);
    typeAndPress("first follow-up");
    typeAndPress("second follow-up");
    expect(send).not.toHaveBeenCalled();
    expect(await screen.findByTestId("composer-queue-list")).toBeTruthy();
    expect(screen.getAllByTestId("composer-queue-send")).toHaveLength(2);
    expect(screen.getAllByTestId("composer-queue-delete")).toHaveLength(2);
    // The round button queues too: it never stops the run nor sends.
    fireEvent.change(screen.getByRole("textbox", { name: "thread.composer.inputAria" }), {
      target: { value: "third follow-up" },
    });
    const queueButtons = screen.getAllByRole("button", { name: "Queue this message" });
    fireEvent.click(queueButtons[queueButtons.length - 1]);
    expect(screen.getAllByTestId("composer-queue-send")).toHaveLength(3);
    expect(send).not.toHaveBeenCalled();
  });

  it("Alt+Enter and Send now still reach the agent in the live turn", async () => {
    const send = vi.fn();
    render(<ThreadComposer onSend={send} onStop={vi.fn()} isStreaming />);
    typeAndPress("look at the logs first", { altKey: true });
    expect(send).toHaveBeenCalledTimes(1);
    // The chat's turn mode may add its prefix (/forge in Build): the text is the user's.
    expect(send.mock.calls[0][0]).toMatch(/(^|\s)look at the logs first$/);
    expect(screen.queryByTestId("composer-queue-list")).toBeNull();

    typeAndPress("then fix the test");
    fireEvent.click(await screen.findByTestId("composer-queue-send"));
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[1][0]).toMatch(/(^|\s)then fix the test$/);
  });

  it("keeps the composer's footprint while typing, so the chat does not move", () => {
    // The queue button used to appear at the first key and leave when the
    // message was queued: the chips beside it shrank and grew while typing.
    render(<ThreadComposer onSend={vi.fn()} onStop={vi.fn()} isStreaming />);
    const buttonsBefore = screen.getAllByRole("button").length;
    const queue = screen.getByTestId("composer-queue-add") as HTMLButtonElement;
    expect(queue.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "thread.composer.inputAria" }), {
      target: { value: "one more thing" },
    });
    expect((screen.getByTestId("composer-queue-add") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getAllByRole("button")).toHaveLength(buttonsBefore);
  });

  it("does not show the queue button when the agent is idle", () => {
    render(<ThreadComposer onSend={vi.fn()} onStop={vi.fn()} />);
    expect(screen.queryByTestId("composer-queue-add")).toBeNull();
  });

  it("keeps the 'agent working' hint short in every language, so it fits one line", () => {
    // A long hint wrapped in a narrow chat column, the empty composer grew a
    // line, and the chat jumped at every first key and every queued message.
    const locales = import.meta.glob<{ thread: { composer: { placeholderStreaming: string } } }>(
      "../../i18n/locales/*/common.json",
      { eager: true, import: "default" },
    );
    const entries = Object.entries(locales);
    expect(entries.length).toBeGreaterThanOrEqual(10);
    for (const [file, json] of entries) {
      const hint = json.thread.composer.placeholderStreaming;
      expect(hint.length, file).toBeLessThanOrEqual(56);
      expect(hint, file).toMatch(/Alt\+/);
    }
  });
});
