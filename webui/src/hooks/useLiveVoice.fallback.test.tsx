// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { NavinClient } from "@/lib/navin-client";
import { useLiveVoice } from "./useLiveVoice";

const mocks = vi.hoisted(() => ({
  status: vi.fn(), search: vi.fn(), pipeline: vi.fn(), realtime: vi.fn(), stop: vi.fn(async () => {}),
  mediaState: "idle", greet: vi.fn(), speak: vi.fn(),
  options: null as null | { onFallback: (code: string, detail?: string) => void;
    onTool: (name: string, args: Record<string, unknown>) => Promise<string> },
  t: (_key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? _key,
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  useTranslation: () => ({ t: mocks.t, i18n: { language: "fr" } }),
}));
vi.mock("@/lib/api", () => ({ fetchLiveRealtimeStatus: mocks.status, searchNavinDocs: mocks.search }));
vi.mock("@/hooks/useAccount", () => ({ storedAccountFirstName: () => "" }));
vi.mock("@/hooks/useVoiceSession", () => ({ useVoiceSession: () => ({
  state: mocks.mediaState, start: mocks.pipeline, stop: mocks.stop, speak: mocks.speak,
}) }));
vi.mock("@/hooks/useRealtimeVoiceSession", () => ({ useRealtimeVoiceSession: (options: typeof mocks.options) => {
  mocks.options = options;
  return { state: mocks.mediaState, start: mocks.realtime, stop: mocks.stop, greet: mocks.greet, speak: mocks.speak };
} }));

afterEach(() => { cleanup(); vi.clearAllMocks(); mocks.mediaState = "idle"; });

async function start(pipelineReady: boolean, onUtterance = vi.fn()) {
  mocks.status.mockResolvedValue({ available: true, engine: "auto", model: "amazon/nova-2-sonic", pipeline_ready: pipelineReady });
  const hook = renderHook(() => useLiveVoice({ client: { onChat: () => () => {} } as unknown as NavinClient, chatId: "chat", messages: [],
    isStreaming: false, pendingChoices: [], respondToChoice: vi.fn(), onUtterance, token: "token" }));
  await act(async () => { hook.result.current.toggle(); });
  expect(mocks.realtime).toHaveBeenCalledOnce();
  expect(mocks.pipeline).not.toHaveBeenCalled();
  return hook;
}

describe("Live realtime fallback", () => {
  it("delegates a real tool request to the current chat and rejects an empty request", async () => {
    const onUtterance = vi.fn();
    const hook = await start(true, onUtterance);
    await act(async () => {
      expect(await mocks.options!.onTool("ask_agent", { request: "Lance les tests du projet" })).toContain("Sent to the agent");
      expect(await mocks.options!.onTool("ask_agent", {})).toBe("error: empty request");
    });
    expect(onUtterance).toHaveBeenCalledExactlyOnceWith("Lance les tests du projet");
    expect(hook.result.current.lastPrompt).toBe("Lance les tests du projet");
  });

  it("returns documentation text from the real tool handler for Nova JSON encoding", async () => {
    mocks.search.mockResolvedValue([{ title: "Live voice", section: "Tools", excerpt: "Delegate work to the agent.", url: "https://navin.live/en/docs/live" }]);
    await start(true);
    const output = await mocks.options!.onTool("navin_docs", { query: "live voice" });
    expect(mocks.search).toHaveBeenCalledExactlyOnceWith("token", "live voice");
    expect(output).toContain("Delegate work to the agent.");
    expect(output).toContain("https://navin.live/en/docs/live");
  });

  it("opens the microphone without greeting or requesting a model response", async () => {
    const hook = await start(true);
    mocks.mediaState = "listening";
    hook.rerender();
    expect(hook.result.current.state).toBe("listening");
    expect(mocks.greet).not.toHaveBeenCalled();
    expect(mocks.speak).not.toHaveBeenCalled();
  });

  it("preserves the model failure without trying an unavailable pipeline", async () => {
    const hook = await start(false);
    await act(async () => { mocks.options!.onFallback("realtime_upstream_error", "Bedrock rejected session tools"); });
    expect(hook.result.current.enabled).toBe(false);
    expect(hook.result.current.error).toBe("realtimeUnavailable");
    expect(hook.result.current.errorDetail).toBe("Bedrock rejected session tools");
    expect(mocks.pipeline).not.toHaveBeenCalled();
  });

  it("continues with listening and voice when the pipeline is ready", async () => {
    const hook = await start(true);
    await act(async () => { mocks.options!.onFallback("realtime_busy"); });
    expect(mocks.pipeline).toHaveBeenCalledOnce();
    expect(hook.result.current.enabled).toBe(true);
    expect(hook.result.current.engine).toBe("pipeline");
    expect(hook.result.current.error).toBeNull();
  });
});
