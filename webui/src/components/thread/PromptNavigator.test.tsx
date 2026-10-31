// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";
import type { UIMessage } from "@/lib/types";

import { PromptNavigator } from "./PromptNavigator";

function message(role: UIMessage["role"], content: string, id: string): UIMessage {
  return { id, role, content, createdAt: 1 };
}

// The first page of a tool-heavy chat: its last two prompts only.
const LOADED: UIMessage[] = [
  message("user", "fourth prompt", "u3"),
  message("assistant", "done", "a3"),
  message("user", "fifth prompt", "u4"),
];
const ALL = ["first prompt", "second prompt", "third prompt", "fourth prompt", "fifth prompt"]
  .map((text, index) => ({ index, text, created_at: null }));

function rows(): string[] {
  return screen
    .getAllByRole("button", { name: /^Jump to prompt/ })
    .map((button) => button.textContent ?? "");
}

describe("PromptNavigator", () => {
  afterEach(() => {
    cleanup();
  });

  it("lists every prompt of the chat, not only the loaded page", async () => {
    await i18n.changeLanguage("en");
    const loadPrompts = vi.fn(async () => ALL);
    render(
      <PromptNavigator
        messages={LOADED}
        userMessageOffset={3}
        loadPrompts={loadPrompts}
        onJumpToPrompt={vi.fn()}
        onJumpToPromptIndex={vi.fn(async () => true)}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open prompt navigator" }));
    expect(screen.getByRole("status").textContent).toContain("Loading earlier prompts");
    await waitFor(() => expect(rows()).toHaveLength(5));
    expect(rows()[0]).toContain("first prompt");
    expect(rows()[4]).toContain("fifth prompt");
    expect(loadPrompts).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByRole("textbox", { name: "Search prompts" }), {
      target: { value: "second" },
    });
    expect(rows()).toHaveLength(1);
  });

  it("loads the pages up to an earlier prompt, and says so when it cannot", async () => {
    await i18n.changeLanguage("en");
    const onJumpToPrompt = vi.fn();
    let finish: (reached: boolean) => void = () => undefined;
    const onJumpToPromptIndex = vi.fn(
      () => new Promise<boolean>((resolve) => { finish = resolve; }),
    );
    render(
      <PromptNavigator
        messages={LOADED}
        userMessageOffset={3}
        loadPrompts={async () => ALL}
        onJumpToPrompt={onJumpToPrompt}
        onJumpToPromptIndex={onJumpToPromptIndex}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open prompt navigator" }));
    await waitFor(() => expect(rows()).toHaveLength(5));

    fireEvent.click(screen.getByRole("button", { name: "Jump to prompt: first prompt" }));
    expect(onJumpToPromptIndex).toHaveBeenCalledWith(0);
    // One jump at a time while its pages load.
    const second = screen.getByRole("button", { name: "Jump to prompt: second prompt" });
    expect((second as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { finish(false); });
    expect(screen.getByRole("alert").textContent).toContain("could not be loaded");
    expect(screen.getByRole("dialog")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Jump to prompt: second prompt" }));
    expect(onJumpToPromptIndex).toHaveBeenLastCalledWith(1);
    await act(async () => { finish(true); });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // A loaded prompt jumps at once, by its message id.
    fireEvent.click(screen.getByRole("button", { name: "Open prompt navigator" }));
    await waitFor(() => expect(rows()).toHaveLength(5));
    fireEvent.click(screen.getByRole("button", { name: "Jump to prompt: fifth prompt" }));
    expect(onJumpToPrompt).toHaveBeenCalledWith("u4");
    expect(onJumpToPromptIndex).toHaveBeenCalledTimes(2);
  });

  it("keeps the prompts on screen when the full list fails", async () => {
    await i18n.changeLanguage("en");
    render(
      <PromptNavigator
        messages={LOADED}
        userMessageOffset={3}
        loadPrompts={async () => { throw new Error("offline"); }}
        onJumpToPrompt={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open prompt navigator" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Earlier prompts"));
    expect(
      screen.getAllByRole("button", { name: /^Jump to prompt/ }).map((b) => b.getAttribute("aria-label")),
    ).toEqual(["Jump to prompt: fourth prompt", "Jump to prompt: fifth prompt"]);
  });

  it("does not ask for the list when the whole chat is loaded", async () => {
    await i18n.changeLanguage("en");
    const loadPrompts = vi.fn(async () => ALL);
    render(
      <PromptNavigator
        messages={LOADED}
        userMessageOffset={0}
        loadPrompts={loadPrompts}
        onJumpToPrompt={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open prompt navigator" }));
    expect(rows()).toHaveLength(2);
    expect(loadPrompts).not.toHaveBeenCalled();
  });
});
