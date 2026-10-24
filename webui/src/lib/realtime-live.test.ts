import { describe, expect, it, vi } from "vitest";

import { LIVE_TOOLS, REALTIME_OPEN_TIMEOUT_MS, REALTIME_UNRESPONSIVE_MS, RealtimeLiveSession, realtimeBridgeUrl } from "./realtime-live";

class FakeSocket {
  static OPEN = 1;
  readyState = 1;
  sent: Record<string, unknown>[] = [];
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.onclose?.();
  }
  emit(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

describe("RealtimeLiveSession", () => {
  it("chooses Ambre for an automatic French Nova voice and respects explicit male voices", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    try {
      for (const [voice, expected] of [[undefined, "ambre"], ["auto", "ambre"], ["florian", "florian"]]) {
        const socket = new FakeSocket();
        const session = new RealtimeLiveSession(
          { url: "ws://local", socketFactory: () => socket as unknown as WebSocket, instructions: "", language: "fr", voice },
          { onAudio: vi.fn(), onTool: vi.fn(), onError: vi.fn() },
        );
        const opened = session.open();
        socket.emit({ type: "navin.ready", provider: "navin", model: "amazon/nova-2-sonic" });
        await opened;
        expect((socket.sent[0].session as { voice: string }).voice).toBe(expected);
        session.close();
      }
    } finally { vi.unstubAllGlobals(); }
  });

  it("returns JSON acknowledgements and errors to Nova, preserving tool arguments and call IDs", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    try {
      const socket = new FakeSocket();
      const onTool = vi.fn(async (name: string) => {
        if (name === "broken") throw new Error("tool failed");
        return name === "structured" ? '{"status":"ok"}' : "Sent to the agent";
      });
      const session = new RealtimeLiveSession(
        { url: "ws://local", socketFactory: () => socket as unknown as WebSocket, instructions: "" },
        { onAudio: vi.fn(), onTool, onError: vi.fn() },
      );
      const opened = session.open();
      socket.emit({ type: "navin.ready", provider: "navin", model: "amazon/nova-2-sonic" });
      await opened;
      for (const [callId, name, args] of [["first", "ask_agent", { request: "Run tests" }],
        ["second", "broken", "{}"], ["third", "structured", "{}"], ["fourth", "ask_agent", "[1,2]"]]) {
        socket.emit({ type: "response.function_call_arguments.done", call_id: callId, name, arguments: args });
      }
      await vi.waitFor(() => expect(socket.sent.filter((frame) => frame.type === "conversation.item.create")).toHaveLength(4));
      const outputs = socket.sent.filter((frame) => frame.type === "conversation.item.create")
        .map((frame) => frame.item as { call_id: string; output: string });
      const payload = (id: string) => JSON.parse(outputs.find((item) => item.call_id === id)!.output);
      expect(onTool).toHaveBeenCalledWith("ask_agent", { request: "Run tests" });
      expect(onTool).toHaveBeenCalledTimes(3);
      expect(payload("first")).toEqual({ result: "Sent to the agent" });
      expect(payload("second")).toEqual({ result: "error: tool failed" });
      expect(payload("third")).toEqual({ status: "ok" });
      expect(payload("fourth").result).toContain("Tool arguments must be a JSON object");
      session.close();
    } finally { vi.unstubAllGlobals(); }
  });

  it("gives up on a bridge that never answers instead of staying on Starting forever", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeSocket);
    try {
      const socket = new FakeSocket();
      const onError = vi.fn();
      const session = new RealtimeLiveSession(
        { url: "ws://x", socketFactory: () => socket as unknown as WebSocket, instructions: "" },
        { onAudio: vi.fn(), onTool: vi.fn(), onError },
      );
      const opened = session.open();
      const outcome = opened.then(() => "opened", (error: { code?: string; message: string }) => `${error.code}:${error.message}`);
      await vi.advanceTimersByTimeAsync(REALTIME_OPEN_TIMEOUT_MS - 1);
      await vi.advanceTimersByTimeAsync(2);
      await expect(outcome).resolves.toBe("realtime_unreachable:the live bridge did not answer in time");
      // The late close of the socket we gave up on is not a second failure.
      expect(onError).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it("names the gateway's close code when the bridge refuses or drops the session", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    try {
      let socket = new FakeSocket();
      const session = new RealtimeLiveSession(
        { url: "ws://x", socketFactory: () => socket as unknown as WebSocket, instructions: "" },
        { onAudio: vi.fn(), onTool: vi.fn(), onError: vi.fn() },
      );
      const opened = session.open();
      (socket.onclose as unknown as (event: { code: number; reason: string }) => void)({ code: 4502, reason: "realtime_unreachable" });
      await expect(opened).rejects.toMatchObject({ code: "realtime_unreachable", message: "closed (4502: realtime_unreachable)" });

      socket = new FakeSocket();
      const onError = vi.fn();
      const live = new RealtimeLiveSession(
        { url: "ws://x", socketFactory: () => socket as unknown as WebSocket, instructions: "" },
        { onAudio: vi.fn(), onTool: vi.fn(), onError },
      );
      const ready = live.open();
      socket.emit({ type: "navin.ready", provider: "openai", model: "gpt-realtime" });
      await ready;
      (socket.onclose as unknown as (event: { code: number; reason: string }) => void)({ code: 1011, reason: "" });
      expect(onError).toHaveBeenCalledWith("realtime_dropped", "closed (1011)");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("rejects with a readable code when the socket cannot even be created", async () => {
    const session = new RealtimeLiveSession(
      { url: "ws://x", socketFactory: () => { throw new Error("host bridge offline"); }, instructions: "" },
      { onAudio: vi.fn(), onTool: vi.fn(), onError: vi.fn() },
    );
    await expect(session.open()).rejects.toMatchObject({ code: "realtime_unreachable", message: "host bridge offline" });
  });

  it("reports an upstream rejection after the socket opens instead of staying silent", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    try {
      const socket = new FakeSocket();
      const onError = vi.fn();
      const session = new RealtimeLiveSession(
        { url: "ws://x", socketFactory: () => socket as unknown as WebSocket, instructions: "" },
        { onAudio: vi.fn(), onTool: vi.fn(), onError },
      );
      const opened = session.open();
      socket.emit({ type: "navin.ready", provider: "navin", model: "amazon/nova-2-sonic" });
      await opened;
      socket.emit({ type: "error", error: { code: "response_cancel_not_active" } });
      expect(onError).not.toHaveBeenCalled();
      socket.emit({ type: "error", error: { type: "ValidationException", message: "Audio session refused" } });
      expect(onError).toHaveBeenCalledWith("realtime_upstream_error", "Audio session refused");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("configures the voice with Navin's tools, runs a tool call and relays agent updates", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const socket = new FakeSocket();
    const onTool = vi.fn(async () => "sent");
    const session = new RealtimeLiveSession(
      { url: "ws://x/api/live/realtime", socketFactory: () => socket as unknown as WebSocket, instructions: "be Navin" },
      { onAudio: vi.fn(), onTool, onError: vi.fn() },
    );
    const opened = session.open();
    socket.emit({ type: "navin.ready", provider: "navin", model: "amazon/nova-2-sonic" });
    await expect(opened).resolves.toEqual({ provider: "navin", model: "amazon/nova-2-sonic" });
    const update = socket.sent[0] as { type: string; session: { tools: unknown[]; instructions: string } };
    expect(update.type).toBe("session.update");
    expect(update.session.tools).toHaveLength(LIVE_TOOLS.length);

    socket.emit({ type: "response.function_call_arguments.done", call_id: "c1", name: "ask_agent", arguments: '{"request":"lance les tests"}' });
    await vi.waitFor(() => expect(socket.sent.some((f) => (f as { item?: { type?: string } }).item?.type === "function_call_output")).toBe(true));
    expect(onTool).toHaveBeenCalledWith("ask_agent", { request: "lance les tests" });
    // The same call reported again in response.done is not run twice.
    socket.emit({ type: "response.done", response: { output: [{ type: "function_call", call_id: "c1", name: "ask_agent", arguments: "{}" }] } });
    expect(onTool).toHaveBeenCalledTimes(1);

    session.announce("Tests verts.");
    expect(JSON.stringify(socket.sent)).toContain("[Navin agent] Tests verts.");
    vi.unstubAllGlobals();
  });

  it("sends a request repeated under a new call id only once, and steers project work to ask_agent", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    try {
      const socket = new FakeSocket();
      const onTool = vi.fn(async () => "sent");
      const session = new RealtimeLiveSession(
        { url: "ws://x", socketFactory: () => socket as unknown as WebSocket, instructions: "" },
        { onAudio: vi.fn(), onTool, onError: vi.fn() },
      );
      const opened = session.open();
      socket.emit({ type: "navin.ready", provider: "navin", model: "amazon/nova-2-sonic" });
      await opened;
      socket.emit({ type: "response.function_call_arguments.done", call_id: "a", name: "ask_agent", arguments: '{"request":"Lance les tests"}' });
      socket.emit({ type: "response.function_call_arguments.done", call_id: "b", name: "ask_agent", arguments: '{"request":"lance les tests "}' });
      socket.emit({ type: "response.function_call_arguments.done", call_id: "c", name: "ask_agent", arguments: '{"request":"lance le build"}' });
      await vi.waitFor(() => expect(socket.sent.filter((f) => f.type === "conversation.item.create")).toHaveLength(3));
      expect(onTool).toHaveBeenCalledTimes(2);
      const outputs = socket.sent.filter((f) => f.type === "conversation.item.create").map((f) => f.item as { call_id: string; output: string });
      expect(JSON.parse(outputs.find((o) => o.call_id === "b")!.output).result).toContain("Already sent");
      const tools = Object.fromEntries(LIVE_TOOLS.map((t) => [t.name, t.description]));
      expect(tools.ask_agent).toMatch(/run a command, call tools/);
      expect(tools.navin_docs).toMatch(/Never for work on the user's project/);
      const ask = LIVE_TOOLS.find((t) => t.name === "ask_agent")!;
      expect(JSON.stringify(ask.parameters)).toMatch(/first person.*no 'The user wants/);
      session.close();
    } finally { vi.unstubAllGlobals(); }
  });

  it("a bridge refusal rejects open with its code, for the pipeline fallback", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const socket = new FakeSocket();
    const session = new RealtimeLiveSession(
      { url: "ws://x", socketFactory: () => socket as unknown as WebSocket, instructions: "" },
      { onAudio: vi.fn(), onTool: vi.fn(), onError: vi.fn() },
    );
    const opened = session.open();
    socket.emit({ type: "error", error: { code: "realtime_busy", message: "429" } });
    await expect(opened).rejects.toMatchObject({ code: "realtime_busy" });
    vi.unstubAllGlobals();
  });
});

describe("realtimeBridgeUrl", () => {
  it("targets the same gateway as the chat socket, with the API token", () => {
    expect(realtimeBridgeUrl("ws://127.0.0.1:8799/__navin_ws?token=ws&client_id=a", "api tok")).toBe(
      "ws://127.0.0.1:8799/api/live/realtime?token=api%20tok",
    );
    expect(realtimeBridgeUrl("https://navin.example/x", "t")).toBe("wss://navin.example/api/live/realtime?token=t");
  });

  it("opens the call with the greeting, said once by the voice itself", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const socket = new FakeSocket();
    const session = new RealtimeLiveSession(
      { url: "ws://x/api/live/realtime", socketFactory: () => socket as unknown as WebSocket, instructions: "be Navin" },
      { onAudio: vi.fn(), onTool: vi.fn(async () => ""), onError: vi.fn() },
    );
    const opened = session.open();
    socket.emit({ type: "navin.ready", provider: "gemini", model: "gemini-3.8-live" });
    await opened;
    session.greet("Salut Aymen, je suis là et je t'écoute.");
    session.greet("again");
    const creates = socket.sent.filter((f) => f.type === "response.create") as { response: { instructions: string } }[];
    expect(creates).toHaveLength(1);
    expect(creates[0].response.instructions).toContain("Salut Aymen, je suis là et je t'écoute.");
  });
});

describe("a voice model that never answers", () => {
  async function openSession() {
    const socket = new FakeSocket();
    const onError = vi.fn();
    const session = new RealtimeLiveSession(
      { url: "ws://local", socketFactory: () => socket as unknown as WebSocket, instructions: "" },
      { onAudio: vi.fn(), onTool: vi.fn(), onError },
    );
    const opened = session.open();
    socket.emit({ type: "navin.ready", provider: "navin", model: "amazon/nova-2-sonic" });
    await opened;
    socket.emit({ type: "session.created", session: { id: "s1" } });
    return { socket, session, onError };
  }

  it("is reported once the user spoke and it stayed silent, so Live falls back", async () => {
    vi.useFakeTimers();
    try {
      const { session, onError } = await openSession();
      session.noteLocalSpeech();
      vi.advanceTimersByTime(REALTIME_UNRESPONSIVE_MS + 1_500);
      expect(onError).toHaveBeenCalledWith("realtime_unresponsive", "");
      session.close();
    } finally { vi.useRealTimers(); }
  });

  it("is reported when a requested answer never comes", async () => {
    vi.useFakeTimers();
    try {
      const { session, onError } = await openSession();
      session.greet("Bonjour");
      vi.advanceTimersByTime(REALTIME_UNRESPONSIVE_MS + 1_500);
      expect(onError).toHaveBeenCalledWith("realtime_unresponsive", "");
      session.close();
    } finally { vi.useRealTimers(); }
  });

  it("is not reported for a model that reacts, nor while the user stays quiet", async () => {
    vi.useFakeTimers();
    try {
      const { socket, session, onError } = await openSession();
      vi.advanceTimersByTime(REALTIME_UNRESPONSIVE_MS * 3);
      session.noteLocalSpeech();
      vi.advanceTimersByTime(2_000);
      socket.emit({ type: "input_audio_buffer.speech_started" });
      vi.advanceTimersByTime(REALTIME_UNRESPONSIVE_MS * 2);
      expect(onError).not.toHaveBeenCalled();
      session.close();
    } finally { vi.useRealTimers(); }
  });

  it("counts from the user's last word, so a long sentence is not cut short", async () => {
    vi.useFakeTimers();
    try {
      const { session, onError } = await openSession();
      for (let second = 0; second < 20; second += 1) {
        session.noteLocalSpeech();
        vi.advanceTimersByTime(1_000);
      }
      expect(onError).not.toHaveBeenCalled();
      vi.advanceTimersByTime(REALTIME_UNRESPONSIVE_MS + 1_500);
      expect(onError).toHaveBeenCalledWith("realtime_unresponsive", "");
      session.close();
    } finally { vi.useRealTimers(); }
  });

  it("does not count the model's own voice as the user speaking", async () => {
    vi.useFakeTimers();
    try {
      const { socket, session, onError } = await openSession();
      socket.emit({ type: "response.created" });
      session.noteLocalSpeech();
      vi.advanceTimersByTime(REALTIME_UNRESPONSIVE_MS * 2);
      expect(onError).not.toHaveBeenCalled();
      session.close();
    } finally { vi.useRealTimers(); }
  });
});
