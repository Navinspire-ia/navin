// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ComposerPermissionMenu } from "./ComposerPermissionMenu";
import { resetMenuChoiceForTests } from "./menuChoice";

const mock = vi.hoisted(() => ({
  fetchExecPolicy: vi.fn(),
  updateExecPolicy: vi.fn(async (_token: string, state: { approval_mode?: string }) => state),
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
    i18n: { language: "en" },
  }),
}));
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: "tok" }) }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  fetchExecPolicy: mock.fetchExecPolicy,
  updateExecPolicy: mock.updateExecPolicy,
}));

beforeEach(() => {
  resetMenuChoiceForTests();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  mock.fetchExecPolicy.mockReset();
  mock.updateExecPolicy.mockClear();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("ComposerPermissionMenu", () => {
  it("reads the saved mode again when it opens, so a mode changed from a CLI can be picked back", async () => {
    // Full access when the window opened, then a CLI switched to "ask every command".
    mock.fetchExecPolicy
      .mockResolvedValueOnce({ approval_mode: "autonomous" })
      .mockResolvedValue({ approval_mode: "always" });
    render(<ComposerPermissionMenu />);
    const trigger = await screen.findByTestId("composer-permission-mode");
    expect(trigger.getAttribute("title")).toBe("Full access");

    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    await screen.findByRole("menu");
    expect(mock.fetchExecPolicy).toHaveBeenCalledTimes(2);
    await waitFor(() =>
      expect(screen.getByTestId("composer-permission-mode").getAttribute("title")).toBe("Ask every command"),
    );

    const fullAccess = screen.getByTestId("composer-permission-autonomous");
    fireEvent.pointerDown(fullAccess, { button: 0, pointerType: "mouse" });
    fireEvent.click(fullAccess);
    await waitFor(() =>
      expect(mock.updateExecPolicy).toHaveBeenCalledWith("tok", { approval_mode: "autonomous" }),
    );
  });
});
