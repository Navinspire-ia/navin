// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { StrictMode } from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import AgentExecTerminal from "./AgentExecTerminal";
import { terminalTheme } from "./terminalTheme";

const mocks = vi.hoisted(() => ({
  create: vi.fn(), dispose: vi.fn(), write: vi.fn(), reset: vi.fn(), open: vi.fn(),
  refresh: vi.fn(), webglDispose: vi.fn(),
  calls: [] as string[],
  instances: [] as { options: Record<string, unknown> }[],
  contextLoss: [] as (() => void)[],
  webglThrows: false,
}));
vi.mock("@xterm/xterm", () => ({ Terminal: class {
  options: Record<string, unknown>; rows = 24; element: HTMLElement | undefined;
  constructor(opts: Record<string, unknown> = {}) { this.options = { ...opts }; mocks.create(); mocks.instances.push(this); }
  loadAddon() {} refresh() { mocks.refresh(); }
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
vi.mock("@xterm/addon-webgl", () => ({ WebglAddon: class {
  constructor() { if (mocks.webglThrows) throw new Error("WebGL2 not supported"); }
  onContextLoss(cb: () => void) { mocks.contextLoss.push(cb); }
  dispose() { mocks.webglDispose(); }
} }));
vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
afterEach(async () => {
  cleanup(); await settle(); vi.clearAllMocks();
  mocks.calls.length = 0; mocks.instances.length = 0; mocks.contextLoss.length = 0; mocks.webglThrows = false;
});

/** The pool disposes after the React commit, in a microtask. */
const settle = () => new Promise<void>((resolve) => queueMicrotask(resolve));

/** Same contract as DevWorkbench.subscribeAgentTerm: a Set of handlers per id. */
function makeFeed() {
  const listeners = new Map<string, Set<(chunk: string) => void>>();
  const subscribe = vi.fn((id: string, handler: (chunk: string) => void) => {
    let set = listeners.get(id);
    if (!set) { set = new Set(); listeners.set(id, set); }
    set.add(handler);
    return () => { set!.delete(handler); };
  });
  const emit = (id: string, chunk: string) => { for (const h of [...(listeners.get(id) ?? [])]) h(chunk); };
  const live = () => [...listeners.values()].reduce((n, s) => n + s.size, 0);
  return { subscribe, emit, live, listeners };
}

it("reuses one renderer across 61 agent tabs and restores output when switching", async () => {
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

  unmount(); await settle();
  expect(unsubscribers.get("command-42")).toHaveBeenCalledTimes(1);
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
});

it("creates a fresh renderer for the next host once every tab unmounted", async () => {
  const subscribe = vi.fn(() => () => {});
  const backlog = () => "";
  const first = render(<AgentExecTerminal termId="a" active isDark backlog={backlog} subscribe={subscribe} />);
  first.unmount(); await settle();
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
  const second = render(<AgentExecTerminal termId="b" active isDark={false} backlog={backlog} subscribe={subscribe} />);
  expect(mocks.create).toHaveBeenCalledTimes(2);
  second.unmount(); await settle();
  expect(mocks.dispose).toHaveBeenCalledTimes(2);
});

it("StrictMode double mount creates the renderer once and disposes it once at the end", async () => {
  const feed = makeFeed();
  const { container, unmount } = render(
    <StrictMode>
      <AgentExecTerminal termId="a" active isDark backlog={() => "hello"} subscribe={feed.subscribe} />
    </StrictMode>,
  );
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(mocks.dispose).not.toHaveBeenCalled();
  expect(container.querySelectorAll(".xterm")).toHaveLength(1);
  // The simulated unmount must not leave a second live handler behind.
  expect(feed.live()).toBe(1);
  feed.emit("a", "live");
  expect(mocks.write.mock.calls.filter(([t]) => t === "live")).toHaveLength(1);
  unmount(); await settle();
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
  expect(feed.live()).toBe(0);
});

it("keeps the renderer when the host remounts the tab per command (key = term id)", async () => {
  // DevWorkbench renders only the active agent tab, GuestLivePanel keys it by id:
  // switching command unmounts the old tab and mounts a new one in the same commit.
  const feed = makeFeed();
  const backlog = (id: string) => `out ${id}`;
  const view = (id: string) => (
    <AgentExecTerminal key={id} termId={id} active isDark backlog={backlog} subscribe={feed.subscribe} />
  );
  const { container, rerender, unmount } = render(view("cmd-1"));
  for (const id of ["cmd-2", "cmd-3", "cmd-4"]) {
    rerender(view(id));
    expect(mocks.write).toHaveBeenLastCalledWith(`out ${id}`);
    expect(container.querySelectorAll(".xterm")).toHaveLength(1);
  }
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(mocks.dispose).not.toHaveBeenCalled();
  expect(feed.live()).toBe(1);
  unmount(); await settle();
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
});

it("two hosts mounted at once share one renderer and dispose it once", async () => {
  const feed = makeFeed();
  const backlog = (id: string) => `out ${id}`;
  const workbench = render(<AgentExecTerminal termId="wb" active isDark backlog={backlog} subscribe={feed.subscribe} />);
  const guest = render(<AgentExecTerminal termId="guest" active isDark backlog={backlog} subscribe={feed.subscribe} />);
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(document.querySelectorAll(".xterm")).toHaveLength(1);
  expect(guest.container.querySelector(".xterm")).not.toBeNull();
  // The host that lost the renderer stopped listening; its output cannot leak in.
  expect(feed.live()).toBe(1);
  mocks.write.mockClear();
  feed.emit("wb", "wb chunk");
  expect(mocks.write).not.toHaveBeenCalled();
  feed.emit("guest", "guest chunk");
  expect(mocks.write).toHaveBeenCalledWith("guest chunk");
  workbench.unmount(); await settle();
  expect(mocks.dispose).not.toHaveBeenCalled();
  expect(guest.container.querySelector(".xterm")).not.toBeNull();
  guest.unmount(); await settle();
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
  expect(feed.live()).toBe(0);
});

it("gives the renderer back to the first host when the second host unmounts", async () => {
  const feed = makeFeed();
  const backlog = (id: string) => `out ${id}`;
  const workbench = render(<AgentExecTerminal termId="wb" active isDark backlog={backlog} subscribe={feed.subscribe} />);
  const guest = render(<AgentExecTerminal termId="guest" active isDark backlog={backlog} subscribe={feed.subscribe} />);
  guest.unmount(); await settle();
  // The workbench tab is still mounted and active: it must not stay blank and deaf.
  expect(workbench.container.querySelector(".xterm")).not.toBeNull();
  expect(feed.live()).toBe(1);
  mocks.write.mockClear();
  feed.emit("wb", "wb chunk");
  expect(mocks.write).toHaveBeenCalledWith("wb chunk");
  workbench.unmount(); await settle();
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
});

it("drops a live chunk for the previous command once the tab switched", async () => {
  const feed = makeFeed();
  const backlog = (id: string) => `out ${id}`;
  const tabs = (selected: string) => ["a", "b"].map((id) => (
    <AgentExecTerminal key={id} termId={id} active={id === selected} isDark backlog={backlog} subscribe={feed.subscribe} />
  ));
  const { rerender, unmount } = render(<>{tabs("a")}</>);
  feed.emit("a", "a before");
  expect(mocks.write).toHaveBeenLastCalledWith("a before");
  rerender(<>{tabs("b")}</>);
  mocks.calls.length = 0;
  feed.emit("a", "a late");
  feed.emit("b", "b live");
  expect(mocks.calls).toEqual(["write:b live"]);
  expect(feed.listeners.get("a")?.size ?? 0).toBe(0);
  unmount(); await settle();
});

it("applies an isDark change to the pooled terminal", async () => {
  const feed = makeFeed();
  const props = { termId: "a", active: true, backlog: () => "", subscribe: feed.subscribe };
  const { rerender, unmount } = render(<AgentExecTerminal {...props} isDark />);
  const term = mocks.instances[0];
  expect(term.options.theme).toEqual(terminalTheme(true, true));
  mocks.refresh.mockClear();
  rerender(<AgentExecTerminal {...props} isDark={false} />);
  expect(term.options.theme).toEqual(terminalTheme(false, true));
  expect(mocks.refresh).toHaveBeenCalled();
  rerender(<AgentExecTerminal {...props} isDark />);
  expect(term.options.theme).toEqual(terminalTheme(true, true));
  expect(mocks.create).toHaveBeenCalledTimes(1);
  unmount(); await settle();
});

it("a re-parented terminal picks up the theme of the tab it moves into", async () => {
  const feed = makeFeed();
  const first = render(<AgentExecTerminal termId="a" active isDark backlog={() => ""} subscribe={feed.subscribe} />);
  const second = render(<AgentExecTerminal termId="b" active isDark={false} backlog={() => ""} subscribe={feed.subscribe} />);
  expect(mocks.instances[0].options.theme).toEqual(terminalTheme(false, true));
  second.unmount(); await settle();
  first.unmount(); await settle();
});

it("a theme change restyles the renderer only from the tab that shows it", async () => {
  const feed = makeFeed();
  const host = (termId: string, isDark: boolean) => (
    <AgentExecTerminal termId={termId} active isDark={isDark} backlog={() => ""} subscribe={feed.subscribe} />
  );
  const workbench = render(host("wb", true));
  const guest = render(host("guest", true));
  const term = mocks.instances[0];
  // The guest borrowed the renderer: the workbench's own theme change leaves it alone.
  workbench.rerender(host("wb", false));
  expect(term.options.theme).toEqual(terminalTheme(true, true));
  guest.rerender(host("guest", false));
  expect(term.options.theme).toEqual(terminalTheme(false, true));
  guest.unmount(); await settle();
  workbench.unmount(); await settle();
});

it("WebGL context loss falls back to the DOM renderer without throwing", async () => {
  const feed = makeFeed();
  const backlog = (id: string) => `out ${id}`;
  const tabs = (selected: string) => ["a", "b"].map((id) => (
    <AgentExecTerminal key={id} termId={id} active={id === selected} isDark backlog={backlog} subscribe={feed.subscribe} />
  ));
  const { rerender, unmount } = render(<>{tabs("a")}</>);
  expect(mocks.contextLoss).toHaveLength(1);
  expect(() => mocks.contextLoss[0]()).not.toThrow();
  expect(mocks.webglDispose).toHaveBeenCalledTimes(1);
  // The pooled terminal keeps working after the fallback.
  rerender(<>{tabs("b")}</>);
  expect(mocks.write).toHaveBeenLastCalledWith("out b");
  feed.emit("b", "after loss");
  expect(mocks.write).toHaveBeenLastCalledWith("after loss");
  expect(mocks.create).toHaveBeenCalledTimes(1);
  unmount(); await settle();
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
});

it("falls back to the DOM renderer when WebGL cannot be created", async () => {
  mocks.webglThrows = true;
  const feed = makeFeed();
  const { container, unmount } = render(
    <AgentExecTerminal termId="a" active isDark backlog={() => "saved"} subscribe={feed.subscribe} />,
  );
  expect(container.querySelector(".xterm")).not.toBeNull();
  expect(mocks.write).toHaveBeenLastCalledWith("saved");
  unmount(); await settle();
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
});
