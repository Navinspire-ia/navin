// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ComposerModeMenu, type ComposerModeTeam } from "./ComposerModeMenu";
import { resetMenuChoiceForTests } from "./menuChoice";

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
    i18n: { language: "en" },
  }),
}));

beforeEach(() => {
  resetMenuChoiceForTests();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function renderMenu(team: Partial<ComposerModeTeam> | null = {}) {
  const onChange = vi.fn();
  const handlers = { onToggle: vi.fn(), onOpenSettings: vi.fn() };
  render(
    <ComposerModeMenu
      mode="agent"
      isHero={false}
      onChange={onChange}
      team={team === null ? null : { enabled: false, ...handlers, ...team }}
    />,
  );
  const trigger = screen.getByRole("button", { name: "Turn mode" });
  return {
    onChange,
    ...handlers,
    trigger,
    open: async () => {
      fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
      fireEvent.click(trigger);
      return screen.findByRole("menu");
    },
  };
}

describe("Team mode in the composer mode menu", () => {
  it("shows the switch and Settings > Team before any team exists", async () => {
    const { open, onOpenSettings } = renderMenu({ ready: false });
    await open();
    const toggle = screen.getByTestId("composer-mode-team-toggle");
    expect(toggle.textContent).toContain("Team mode");
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false");
    const settings = screen.getByTestId("composer-mode-team-settings");
    expect(settings.textContent).toContain("Team settings");
    expect(settings.textContent).toContain("No teammate yet");
    fireEvent.click(settings);
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it("is a switch beside the modes, not one more mode", async () => {
    const { open, onChange, onToggle } = renderMenu({ enabled: true, ready: true });
    await open();
    // Every mode is still offered, and Agent stays the picked one.
    for (const name of ["Ask", "Agent", "Plan", "Review", "Security", "Debug", "Reverse", "Montage"]) {
      expect(screen.getByRole("menuitem", { name })).toBeTruthy();
    }
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("composer-mode-team-settings").textContent).not.toContain("No teammate yet");
    const toggle = screen.getByTestId("composer-mode-team-toggle");
    fireEvent.pointerDown(toggle, { button: 0, pointerType: "mouse" });
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(onToggle).toHaveBeenCalledWith(false);
    expect(onChange).not.toHaveBeenCalled();
    // The menu stays open so the switch is seen moving.
    expect(screen.getByTestId("composer-mode-team-toggle")).toBeTruthy();
  });

  it("marks the trigger while Team mode is on and says when the switch failed", async () => {
    const { open, trigger } = renderMenu({ enabled: true, failed: true });
    expect(screen.getByTestId("composer-mode-team-on")).toBeTruthy();
    expect(trigger.getAttribute("title")).toBe("Agent · Team mode");
    await open();
    expect(screen.getByTestId("composer-mode-team-failed").textContent).toContain(
      "Team mode could not be switched",
    );
  });

  it("opens the Team view only while teammates work in this chat", async () => {
    const onOpenView = vi.fn();
    const { open } = renderMenu({ enabled: true, ready: true, onOpenView });
    await open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Team view" }));
    expect(onOpenView).toHaveBeenCalledTimes(1);
  });

  it("offers no Team rows when the host gives none", async () => {
    const { open, trigger } = renderMenu(null);
    expect(screen.queryByTestId("composer-mode-team-on")).toBeNull();
    expect(trigger.getAttribute("title")).toBe("Agent");
    await open();
    expect(screen.queryByTestId("composer-mode-team-toggle")).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Team view" })).toBeNull();
  });
});
