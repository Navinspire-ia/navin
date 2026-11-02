// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import AgentExecTerminal from "./AgentExecTerminal";

const mocks = vi.hoisted(() => ({
  create: vi.fn(), dispose: vi.fn(), write: vi.fn(), reset: vi.fn(), open: vi.fn(),
  calls: [] as string[],
}));
vi.mock("@xterm/xterm", () => ({ Terminal: class {
  options = {}; rows = 24; element: HTMLElement | undefined;
  constructor() { mocks.create(); }
  loadAddon() {} refresh() {}
  open(container: HTMLElement) {
    mocks.open(container);
    this.element = document.createElement("div");
    this.element.className = "xterm";
    container.appendChild(this.element);
  }
  reset() { mocks.reset(); mocks.calls.push("reset"); }
  write(text: string) { mocks.write(text); mocks.calls.push(`write:${text}`); }
  dispose() { mocks.dispose(); this.element?.remove(); }
} }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("@xterm/addon-webgl", () => ({ WebglAddon: class { onContextLoss() {} dispose() {} } }));
vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
afterEach(() => { cleanup(); vi.clearAllMocks(); mocks.calls.length = 0; });

it("reuses one renderer across 61 agent tabs and restores output when switching", () => {
  const unsubscribers = new Map<string, ReturnType<typeof vi.fn>>();
  const subscribe = vi.fn((id: string) => {
    const off = vi.fn();
    unsubscribers.set(id, off);
    return off;
  });
  const backlog = (id: string) => `Saved output of ${id}`;
  const tabs = (selected: number) => Array.from({ length: 61 }, (_, i) => (
    <AgentExecTerminal key={i} termId={`command-${i}`} active={i === selected}
      isDark backlog={backlog} subscribe={subscribe} />
  ));
  const { container, rerender, unmount } = render(<>{tabs(0)}</>);
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(mocks.open).toHaveBeenCalledTimes(1);
  expect(mocks.write).toHaveBeenLastCalledWith("Saved output of command-0");
  const hosts = container.querySelectorAll(".navin-terminal");
  expect(hosts[0].querySelector(".xterm")).not.toBeNull();

  for (const selected of [60, 17, 0, 42]) {
    mocks.calls.length = 0;
    rerender(<>{tabs(selected)}</>);
    // The new backlog is written only after the old screen is cleared.
    expect(mocks.calls).toEqual(["reset", `write:Saved output of command-${selected}`]);
    expect(container.querySelectorAll(".xterm")).toHaveLength(1);
    expect(hosts[selected].querySelector(".xterm")).not.toBeNull();
  }
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(mocks.open).toHaveBeenCalledTimes(1);
  expect(mocks.dispose).not.toHaveBeenCalled();
  // Every tab given up stopped its live feed; only command-42 still listens.
  for (const id of ["command-60", "command-17"]) expect(unsubscribers.get(id)).toHaveBeenCalledTimes(1);
  expect(unsubscribers.get("command-42")).not.toHaveBeenCalled();
  expect(subscribe).toHaveBeenCalledTimes(5);

  unmount();
  expect(unsubscribers.get("command-42")).toHaveBeenCalledTimes(1);
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
});

it("creates a fresh renderer for the next host once every tab unmounted", () => {
  const subscribe = vi.fn(() => () => {});
  const backlog = () => "";
  const first = render(<AgentExecTerminal termId="a" active isDark backlog={backlog} subscribe={subscribe} />);
  first.unmount();
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
  const second = render(<AgentExecTerminal termId="b" active isDark={false} backlog={backlog} subscribe={subscribe} />);
  expect(mocks.create).toHaveBeenCalledTimes(2);
  second.unmount();
  expect(mocks.dispose).toHaveBeenCalledTimes(2);
});
