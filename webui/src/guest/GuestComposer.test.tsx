// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetMenuChoiceForTests } from "@/components/thread/menuChoice";
import { GuestComposer } from "./GuestComposer";

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key,
    i18n: { language: "en" },
  }),
}));

beforeEach(() => {
  window.localStorage.clear();
  resetMenuChoiceForTests();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderComposer(props: Partial<Parameters<typeof GuestComposer>[0]> = {}) {
  const handlers = { onSend: vi.fn(), onStop: vi.fn() };
  const view = render(<GuestComposer canDrive streaming={false} {...handlers} {...props} />);
  // The bordered box around the text: the mode menu and the buttons live inside it.
  const box = view.container.querySelector("textarea")?.parentElement as HTMLElement;
  return { ...view, ...handlers, box };
}

async function openModes() {
  const trigger = screen.getByRole("button", { name: "Turn mode" });
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
  fireEvent.click(trigger);
  return screen.findByRole("menu");
}

describe("guest composer", () => {
  it("offers the guest modes in a menu inside the box, without Montage", async () => {
    const { box } = renderComposer();
    expect(within(box).getByRole("button", { name: "Turn mode" }).textContent).toContain("Agent");
    const menu = await openModes();
    const labels = within(menu).getAllByRole("menuitem").map((item) => item.textContent?.trim());
    expect(labels).toEqual(["Ask", "Agent", "Plan", "Review", "Security", "Debug", "Reverse"]);
    // No row of radio buttons any more, and no model or context line.
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.queryByRole("meter")).toBeNull();
  });

  it("sends with the picked mode and remembers it", async () => {
    const { box, onSend } = renderComposer();
    const menu = await openModes();
    const review = within(menu).getByRole("menuitem", { name: /Review/ });
    fireEvent.pointerDown(review, { button: 0, pointerType: "mouse" });
    fireEvent.click(review);
    expect(within(box).getByRole("button", { name: "Turn mode" }).textContent).toContain("Review");
    fireEvent.change(within(box).getByRole("textbox"), { target: { value: "check the login flow" } });
    fireEvent.click(within(box).getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("/inspect check the login flow");
    expect(window.localStorage.getItem("navin.guest.mode")).toBe("review");
  });

  it("puts stop at the right inside the box while the agent works", () => {
    const { box, onStop, onSend } = renderComposer({ streaming: true });
    const stop = within(box).getByRole("button", { name: "Stop the agent" });
    // Nothing typed: stop is the only action at the right.
    expect(within(box).queryByRole("button", { name: "Send" })).toBeNull();
    expect(stop.parentElement?.className).toContain("ms-auto");
    fireEvent.click(stop);
    expect(onStop).toHaveBeenCalledTimes(1);
    // A prompt typed mid-run can still go after the current turn.
    fireEvent.change(within(box).getByRole("textbox"), { target: { value: "then add a test" } });
    fireEvent.click(within(box).getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("/forge then add a test");
    expect(within(box).getByRole("button", { name: "Stop the agent" })).toBeTruthy();
  });

  it("shows a read-only guest neither the modes nor stop", () => {
    const { box } = renderComposer({ canDrive: false, streaming: true });
    expect(within(box).queryByRole("button", { name: "Turn mode" })).toBeNull();
    expect(within(box).queryByRole("button", { name: "Stop the agent" })).toBeNull();
    expect((within(box).getByRole("textbox") as HTMLTextAreaElement).disabled).toBe(true);
  });
});
