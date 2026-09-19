// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import AgentExecTerminal from "./AgentExecTerminal";

const mocks = vi.hoisted(() => ({ create: vi.fn(), dispose: vi.fn(), write: vi.fn() }));
vi.mock("@xterm/xterm", () => ({ Terminal: class {
  options = {}; rows = 24;
  constructor() { mocks.create(); }
  loadAddon() {} open() {} refresh() {}
  write(text: string) { mocks.write(text); }
  dispose() { mocks.dispose(); }
} }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("@xterm/addon-webgl", () => ({ WebglAddon: class { onContextLoss() {} dispose() {} } }));
vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("allocates one renderer for 61 agent tabs and restores output when switching", () => {
  const unsubscribe = vi.fn();
  const subscribe = vi.fn(() => unsubscribe);
  const backlog = (id: string) => `Saved output of ${id}`;
  const tabs = (selected: number) => Array.from({ length: 61 }, (_, i) => (
    <AgentExecTerminal key={i} termId={`command-${i}`} active={i === selected}
      isDark backlog={backlog} subscribe={subscribe} />
  ));
  const { rerender, unmount } = render(<>{tabs(0)}</>);
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(mocks.write).toHaveBeenLastCalledWith("Saved output of command-0");
  rerender(<>{tabs(60)}</>);
  expect(mocks.create).toHaveBeenCalledTimes(2);
  expect(mocks.dispose).toHaveBeenCalledTimes(1);
  expect(unsubscribe).toHaveBeenCalledTimes(1);
  expect(mocks.write).toHaveBeenLastCalledWith("Saved output of command-60");
  unmount();
  expect(mocks.dispose).toHaveBeenCalledTimes(2);
});
