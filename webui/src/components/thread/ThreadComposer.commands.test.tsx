// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";
import { OPEN_SETTINGS_DIALOG_EVENT } from "@/lib/settings-events";
import { resetMenuChoiceForTests } from "./menuChoice";
import { ComposerToolsMenu } from "./ComposerToolsMenu";
import { ThreadComposer, type ComposerTeamMode } from "./ThreadComposer";

const mock = vi.hoisted(() => ({
  t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
  token: null as string | null,
  fetchExecPolicy: vi.fn(async () => ({ approval_mode: "risky" as const })),
  updateExecPolicy: vi.fn(async (_token: string, state: { approval_mode?: string }) => state),
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  useTranslation: () => ({ t: mock.t, i18n: { language: "en" } }),
}));
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: mock.token }) }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  fetchExecPolicy: mock.fetchExecPolicy,
  updateExecPolicy: mock.updateExecPolicy,
}));

beforeEach(() => {
  window.localStorage.clear();
  window.location.hash = "#/code?chat=websocket%3Ademo";
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Element.prototype.scrollIntoView = vi.fn();
  mock.token = null;
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

/** Records the sections the composer asks to open in the settings window. */
function captureSettingsDialog(): () => string[] {
  const seen: string[] = [];
  const listener = (event: Event) => seen.push((event as CustomEvent<{ section: string }>).detail.section);
  window.addEventListener(OPEN_SETTINGS_DIALOG_EVENT, listener);
  onTestFinished(() => window.removeEventListener(OPEN_SETTINGS_DIALOG_EVENT, listener));
  return () => [...seen];
}

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

  it("opens the channels and app connectors in a window from the Connector button without sending", () => {
    const send = vi.fn();
    const opened = captureSettingsDialog();
    render(<ThreadComposer onSend={send} sessionKey="websocket:demo" turnMode="agent" />);
    const button = screen.getByRole("button", { name: "Connector" });
    // An icon like its neighbours: the word is gone from the chat, the name stays for screen readers.
    expect(button.textContent).toBe("");
    expect(button.getAttribute("title")).toBe("Connect apps the agent can use");
    fireEvent.click(button);
    // A window over the chat: no trip to the Settings page.
    expect(opened()).toEqual(["channels"]);
    expect(window.location.hash).toBe("#/code?chat=websocket%3Ademo");
    expect(send).not.toHaveBeenCalled();
  });

  it("disables the Connector button with the composer", () => {
    const opened = captureSettingsDialog();
    render(<ThreadComposer onSend={vi.fn()} disabled />);
    const button = screen.getByRole("button", { name: "Connector" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(opened()).toEqual([]);
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

  it("shows no text inside the box while the agent works", () => {
    const { rerender } = render(<ThreadComposer onSend={vi.fn()} onStop={vi.fn()} isStreaming />);
    const box = () => screen.getByRole("textbox", { name: "thread.composer.inputAria" }) as HTMLTextAreaElement;
    expect(box().placeholder).toBe("");
    rerender(<ThreadComposer onSend={vi.fn()} onStop={vi.fn()} isStreaming placeholder="Ask anything" />);
    expect(box().placeholder).toBe("");
    // Once the turn ends the usual prompt comes back.
    rerender(<ThreadComposer onSend={vi.fn()} onStop={vi.fn()} placeholder="Ask anything" />);
    expect(box().placeholder).toBe("Ask anything");
  });

  it("no language keeps the old 'agent working' hint", () => {
    const locales = import.meta.glob<{ thread: { composer: Record<string, string> } }>(
      "../../i18n/locales/*/common.json",
      { eager: true, import: "default" },
    );
    const entries = Object.entries(locales);
    expect(entries.length).toBeGreaterThanOrEqual(10);
    for (const [file, json] of entries) {
      expect(json.thread.composer, file).not.toHaveProperty("placeholderStreaming");
    }
  });
});

describe("narrow composer toolbar", () => {
  const TEMPLATE = { category: "ppt", name: "pitch", title: "Pitch deck", item_count: 8 };
  let restoreWidth: (() => void) | null = null;

  function composerWidth(width: number) {
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => width });
    restoreWidth = () => {
      Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
    };
  }

  beforeEach(() => resetMenuChoiceForTests());
  afterEach(() => {
    restoreWidth?.();
    restoreWidth = null;
  });

  function renderComposer(props: Partial<Parameters<typeof ThreadComposer>[0]> = {}) {
    const handlers = {
      onSend: vi.fn(),
      onTurnModeChange: vi.fn(),
      onOpenTeam: vi.fn(),
      onSelectDocumentTemplate: vi.fn(),
    };
    render(
      <ThreadComposer
        onSend={handlers.onSend}
        onSelectDocumentTemplate={handlers.onSelectDocumentTemplate}
        onStop={vi.fn()}
        sessionKey="websocket:demo"
        turnMode="agent"
        onTurnModeChange={handlers.onTurnModeChange}
        modelLabel="GLM 5.2"
        modelOptions={[
          { name: "glm", label: "GLM 5.2", model: "z-ai/glm-5.2", provider: "zai", active: true },
        ]}
        onModelSelect={vi.fn()}
        effortValue="high"
        effortOptions={["low", "medium", "high"]}
        onEffortSelect={vi.fn()}
        contextWindowValue={200_000}
        onContextWindowSelect={vi.fn()}
        onTranscribeAudio={vi.fn(async () => "")}
        documentTemplates={[TEMPLATE]}
        teamMode={{
          enabled: true,
          teamId: "dev",
          teams: [{ id: "dev", name: "Dev" }],
          members: [{ name: "Mark", avatar: "bot", color: "#1d6af0" }],
          onToggle: vi.fn(),
          onSelectTeam: vi.fn(),
          onOpenTeam: handlers.onOpenTeam,
        }}
        {...props}
      />,
    );
    return handlers;
  }

  async function openMore() {
    const trigger = screen.getByRole("button", { name: "More options" });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    return screen.findByRole("menu");
  }

  it("keeps the model, effort, context and send on the row and moves the rest to More", () => {
    composerWidth(480);
    renderComposer();
    expect(screen.getByRole("button", { name: "Choose model" }).textContent).toContain("GLM 5.2");
    expect(screen.getByRole("button", { name: "Choose thinking effort" })).toBeTruthy();
    expect(screen.getByTestId("composer-context-picker").textContent).toContain("200K");
    expect(screen.getByRole("button", { name: "thread.composer.send" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "More options" })).toBeTruthy();
    // Everything else left the row.
    expect(screen.queryByRole("button", { name: "Turn mode" })).toBeNull();
    expect(screen.queryByRole("button", { name: "thread.composer.attachImage" })).toBeNull();
    expect(screen.queryByTestId("composer-connectors")).toBeNull();
    expect(screen.queryByTestId("document-template-picker-button")).toBeNull();
    expect(screen.queryByTestId("composer-team-chip")).toBeNull();
    expect(screen.queryByRole("button", { name: "thread.composer.tools.voice" })).toBeNull();
  });

  it("keeps the full row on a wide composer", () => {
    composerWidth(900);
    renderComposer();
    expect(screen.getByRole("button", { name: "Turn mode" })).toBeTruthy();
    expect(screen.getByTestId("composer-connectors")).toBeTruthy();
    expect(screen.getByTestId("composer-team-chip")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "More options" })).toBeNull();
  });

  it("runs every moved control from the More menu", async () => {
    composerWidth(480);
    const { onTurnModeChange, onOpenTeam, onSend, onSelectDocumentTemplate } = renderComposer();

    await openMore();
    expect(screen.getByTestId("composer-tools-attach").textContent).toContain("thread.composer.attachImage");
    expect(screen.getByTestId("composer-tools-voice").textContent).toContain("thread.composer.tools.voice");
    fireEvent.click(screen.getByTestId("composer-tools-team"));
    expect(onOpenTeam).toHaveBeenCalledTimes(1);

    await openMore();
    fireEvent.click(screen.getByTestId("composer-tools-mode"));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Plan" }));
    expect(onTurnModeChange).toHaveBeenCalledWith("plan");

    await openMore();
    fireEvent.click(screen.getByTestId("composer-tools-template"));
    expect(await screen.findByTestId("document-template-gallery")).toBeTruthy();
    fireEvent.click(screen.getByTestId("document-template-option-ppt-pitch"));
    expect(onSelectDocumentTemplate).toHaveBeenCalledWith(TEMPLATE);
    expect(screen.queryByTestId("document-template-gallery")).toBeNull();

    await openMore();
    const opened = captureSettingsDialog();
    const connect = screen.getByTestId("composer-tools-connectors");
    expect(connect.textContent).toBe("Connect apps");
    fireEvent.click(connect);
    expect(opened()).toEqual(["channels"]);
    expect(window.location.hash).toBe("#/code?chat=websocket%3Ademo");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("queues from More while the agent works and keeps the row still while typing", async () => {
    composerWidth(480);
    renderComposer({ isStreaming: true });
    const buttonsBefore = screen.getAllByRole("button").length;
    fireEvent.change(screen.getByRole("textbox", { name: "thread.composer.inputAria" }), {
      target: { value: "check the logs next" },
    });
    expect(screen.getAllByRole("button")).toHaveLength(buttonsBefore);
    await openMore();
    // The mode cannot change during a turn, like on the wide row.
    expect(screen.getByTestId("composer-tools-mode").getAttribute("data-disabled")).not.toBeNull();
    fireEvent.click(screen.getByTestId("composer-tools-queue"));
    expect(await screen.findByTestId("composer-queue-list")).toBeTruthy();
  });

  it("changes the permissions from the More menu", async () => {
    mock.token = "tok";
    mock.updateExecPolicy.mockClear();
    render(<ComposerToolsMenu mode="agent" onModeChange={vi.fn()} />);
    await openMore();
    const row = await screen.findByTestId("composer-tools-permission");
    expect(row.textContent).toContain("Ask before deleting");
    fireEvent.click(row);
    fireEvent.click(await screen.findByTestId("composer-permission-always"));
    expect(mock.updateExecPolicy).toHaveBeenCalledWith("tok", { approval_mode: "always" });
  });
});
