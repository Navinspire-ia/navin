// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mock = vi.hoisted(() => ({
  props: [] as Array<Record<string, unknown>>,
}));

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
    i18n: { language: "en" },
  }),
}));

// The real settings screen needs the gateway; the window only has to mount it
// on the right section, without the page sidebar, and close from inside it.
vi.mock("@/components/settings/SettingsView", () => ({
  SettingsView: (props: Record<string, unknown>) => {
    mock.props.push(props);
    return (
      <div data-testid="settings-view" data-section={String(props.initialSection)}>
        <button type="button" onClick={props.onBackToChat as () => void}>
          inner back
        </button>
      </div>
    );
  },
}));

import { SettingsSectionDialog } from "./SettingsSectionDialog";

afterEach(() => {
  cleanup();
  mock.props.length = 0;
});

function renderDialog(section: "channels" | "team" | null) {
  const onClose = vi.fn();
  const view = render(
    <SettingsSectionDialog
      section={section}
      onClose={onClose}
      theme="light"
      onToggleTheme={vi.fn()}
      onModelNameChange={vi.fn()}
    />,
  );
  return { ...view, onClose };
}

describe("SettingsSectionDialog", () => {
  it("shows nothing while closed", () => {
    renderDialog(null);
    expect(screen.queryByTestId("settings-section-dialog")).toBeNull();
    expect(mock.props).toHaveLength(0);
  });

  it("opens Channels in a window with the settings screen, without the page sidebar", async () => {
    renderDialog("channels");
    const dialog = await screen.findByRole("dialog", { name: "Channels" });
    expect(dialog.getAttribute("data-section")).toBe("channels");
    const view = await screen.findByTestId("settings-view");
    expect(view.getAttribute("data-section")).toBe("channels");
    const props = mock.props.at(-1);
    expect(props?.showSidebar).toBe(false);
    expect(props?.embedded).toBe(true);
    expect(props?.initialSection).toBe("channels");
  });

  it("opens Team in the same window and closes from the X, Escape or the screen itself", async () => {
    const { onClose, rerender } = renderDialog("team");
    expect(await screen.findByRole("dialog", { name: "Team" })).toBeTruthy();
    expect((await screen.findByTestId("settings-view")).getAttribute("data-section")).toBe("team");

    fireEvent.click(screen.getByRole("button", { name: "inner back" }));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(2);

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(3);

    rerender(
      <SettingsSectionDialog
        section={null}
        onClose={onClose}
        theme="light"
        onToggleTheme={vi.fn()}
        onModelNameChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
