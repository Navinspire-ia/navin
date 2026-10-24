// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Realtime Live engine: one voice-to-voice model (Nova Sonic, GPT Realtime,
 * Gemini Live... through the Navin gateway) speaking the OpenAI Realtime
 * protocol over the local gateway bridge (/api/live/realtime).
 *
 * The voice model is the conversation, the Navin agent is the worker: every
 * request that needs doing (code, files, commands, git, settings, research)
 * goes to the agent through the `ask_agent` tool, and the agent's messages,
 * approvals and questions come back to be said aloud. Questions about Navin
 * itself are answered from its documentation (`navin_docs`).
 *
 * Event names of both protocol generations are understood (beta
 * `response.audio.delta`, GA `response.output_audio.delta`...).
 */

import { TOUR_PLACES } from "./ui-tour";
import { realtimeSessionVoice } from "./live-voices";

export type RealtimeToolHandler = (name: string, args: Record<string, unknown>) => Promise<string>;

export interface RealtimeLiveCallbacks {
  onReady?: (info: { provider: string; model: string }) => void;
  onAudio: (base64: string) => void;
  /** The user started talking: cut the voice (barge-in). */
  onUserSpeechStart?: () => void;
  onUserSpeechStop?: () => void;
  onUserTranscript?: (text: string) => void;
  onAssistantTranscript?: (text: string) => void;
  onResponseStart?: () => void;
  onResponseDone?: () => void;
  onTool: RealtimeToolHandler;
  /** Fatal for the session; `code` starting with "realtime_" means "use the pipeline". */
  onError: (code: string, message: string) => void;
  onClose?: () => void;
}

export interface RealtimeSessionOptions {
  url: string;
  /** Opens the socket (the desktop host bridge, or the browser's WebSocket). */
  socketFactory?: (url: string) => WebSocket;
  instructions: string;
  voice?: string;
  language?: string;
  /** OpenAI direct: ask for the user's words as text too. */
  transcribeInput?: boolean;
}

const ASK_DEDUP_MS = 20_000;

/**
 * How long the voice model may stay silent once it owes an answer: after a
 * response was asked for, or after the user spoke. A model can accept the
 * session (`session.created`) and then never answer; without this the call sat
 * on "Listening" forever.
 */
export const REALTIME_UNRESPONSIVE_MS = 10_000;
/** Frames that only set the session up: they prove nothing about the model answering. */
const SETUP_FRAMES = new Set(["session.created", "session.updated", "navin.ready"]);
/**
 * How long the bridge may take to reach the model. The gateway itself gives
 * the upstream 15 s (and one retry); past this the socket is considered dead
 * so the call never sits on "Starting" with nothing happening.
 */
export const REALTIME_OPEN_TIMEOUT_MS = 40_000;

export const LIVE_TOOLS = [
  {
    type: "function",
    name: "ask_agent",
    description:
      "ALWAYS use this to act on the user's project: it is your only way to do anything. Hand the request to the Navin agent " +
      "working in this chat: run a command, call tools, read or edit code and files, run tests, git, build, start or show the app, " +
      "create, fix, audit, research, documents. Never use navin_docs for these. " +
      "Write the request as the user would type it, complete and precise, in the user's language. Call it once per request. " +
      "The agent does the work and reports back; you relay its updates.",
    parameters: {
      type: "object",
      properties: {
        request: {
          type: "string",
          description:
            "The user's request written exactly as the user would type it in the chat: first person, the user's own words and language, " +
            "no 'The user wants...', no commentary, no questions of your own.",
        },
      },
      required: ["request"],
    },
  },
  {
    type: "function",
    name: "stop_agent",
    description: "Stop what the agent is doing right now (the user said stop, cancel, wait...).",
    parameters: { type: "object", properties: {} },
  },
  {
    type: "function",
    name: "answer_approval",
    description:
      "Answer the approval the agent is waiting for (a command or change that needs the user's go). " +
      "Only after the user clearly said yes or no to it.",
    parameters: {
      type: "object",
      properties: { allow: { type: "boolean", description: "true = approve, false = refuse." } },
      required: ["allow"],
    },
  },
  {
    type: "function",
    name: "answer_question",
    description: "Answer the question card the agent asked (pick an option, skip, or answer in the user's words).",
    parameters: {
      type: "object",
      properties: { answer: { type: "string", description: "The option label or letter, 'skip', or free text." } },
      required: ["answer"],
    },
  },
  {
    type: "function",
    name: "chat_status",
    description:
      "What is happening in this chat now: whether the agent is working, its last message, and any pending approval or question.",
    parameters: { type: "object", properties: {} },
  },
  {
    type: "function",
    name: "navin_docs",
    description:
      "Search Navin's documentation, only for questions about the Navin product itself (its features, settings, shortcuts, plans, providers). " +
      "Never for work on the user's project (commands, code, tests, files, the app): that is ask_agent. Query with short English keywords.",
    parameters: {
      type: "object",
      properties: { query: { type: "string", description: "English keywords, e.g. 'configure ollama local model'." } },
      required: ["query"],
    },
  },
  {
    type: "function",
    name: "open_in_app",
    description:
      "Open a page of Navin on the user's screen to show them something: a view (chat, code, a module) or a Settings section.",
    parameters: {
      type: "object",
      properties: { place: { type: "string", enum: TOUR_PLACES, description: "Where to go, e.g. settings/providers." } },
      required: ["place"],
    },
  },
  {
    type: "function",
    name: "screen_controls",
    description: "List the buttons, tabs, links and fields visible on the user's screen right now, with their labels.",
    parameters: { type: "object", properties: {} },
  },
  {
    type: "function",
    name: "show_control",
    description:
      "Point at a control on screen (it lights up) to show the user where it is; with click true, also press it for them. " +
      "Use the label exactly as screen_controls gives it. Controls marked 'user only' are never pressed: show them and let the user click.",
    parameters: {
      type: "object",
      properties: {
        label: { type: "string", description: "The control's label as listed by screen_controls." },
        click: { type: "boolean", description: "true to press it after pointing (only to open or show something)." },
      },
      required: ["label"],
    },
  },
] as const;

export function liveInstructions(language: string): string {
  return [
    "You are the live voice of Navin: a senior software engineer pairing with the user on their project, out loud. Behave like an expert human colleague, not an assistant: direct, precise, calm, and proactive.",
    `Speak the user's language (their interface language is ${language}). Keep turns short (one to three sentences) unless they ask for detail; talk naturally, never read code, paths, logs or long lists aloud; summarize and say the details are in the chat.`,
    "Your hands are the Navin agent working in this chat: it reads and edits code, runs commands and tests, uses git, starts and shows the app, researches. For anything to do or check in the project, call ask_agent. You never pretend to have done work yourself.",
    "Write each ask_agent request like a lead engineer writes a ticket: the goal, the concrete changes or checks expected, constraints the user gave, and how to verify (tests to run, the app to open and what to look at). Include decisions already made in this conversation. Then tell the user in a few words what you launched.",
    "Work like a pro on a whole project: understand the goal first and ask one short question only when a choice really changes the result; break big work into steps and run them one after another, telling the user where you are; after each step, check the agent's report and propose the next step or fix what failed.",
    "Messages that start with [Navin agent] are updates from the agent: relay what matters (done, failed, needs a decision) briefly, with the real outcome (tests passed or failed, what changed). Never invent results, file contents or test outcomes; when unsure, call chat_status.",
    "When an update says an approval is waiting, explain in one sentence what it does and its risk, ask yes or no, and call answer_approval only after a clear answer. When the agent asks a question, read the options and call answer_question with the user's choice.",
    "If the user says stop, cancel or wait while the agent works, call stop_agent at once. If they change their mind mid-task, stop, then send the new request.",
    "Never read a plan, a review, a security report or any long message out loud. Say what it is (a plan in 6 steps, a review with 4 findings) and offer to sum it up. When the user accepts, call chat_status and explain lastAgentMessage yourself like a colleague: the gist in a few sentences, what is critical or risky, and what the agent needs from the user. Then propose the next action (for example fixing the critical points); when the user agrees, call ask_agent with that action, precise and complete.",
    "When the user asks to do something (launch, run, test, fix, create, check, open, install, commit...), act at once with ask_agent instead of explaining how; the user should not have to type prompts. Do not ask permission to send it.",
    "The ask_agent request is posted in the chat as the user's own message: write it as the user speaking in first person, in their language and words (\"Cree une application 3D de courses de chevaliers\"), never \"The user wants...\" and never your own reasoning or questions.",
    "For questions about Navin itself (features, settings, how to), call navin_docs and answer from what it returns; if the docs do not say, say so.",
    "When the user wants to see how to do something in Navin, show it on their screen like a colleague sitting next to them: open_in_app to go to the right page, screen_controls to see what is there, then show_control to light up the button, with click true when pressing it only opens or shows something. Say in a few words what you are showing as you go, one step at a time.",
    "Never press anything that deletes, pays, subscribes, pushes code, changes keys or passwords, or signs out: light it up, explain what it does, and let the user press it.",
  ].join("\n");
}

type Frame = Record<string, unknown> & { type?: string };

/** "closed (4502: realtime_unreachable)" for an abnormal close, "" for a clean one or no detail. */
function describeClose(event: unknown): string {
  const { code, reason } = (event ?? {}) as { code?: unknown; reason?: unknown };
  if (typeof code !== "number" || code === 1000 || code === 1001) return "";
  const text = typeof reason === "string" ? reason.trim() : "";
  return `closed (${code}${text ? `: ${text}` : ""})`;
}

export class RealtimeLiveSession {
  private ws: WebSocket | null = null;
  private readonly handledCalls = new Set<string>();
  /** Last ask_agent request sent, to drop the same request re-emitted under a new call id. */
  private lastAsk: { request: string; at: number } | null = null;
  private responding = false;
  private closed = false;
  private managedNova = false;
  /** When the model must have shown a sign of life by (ms), or null when it owes nothing. */
  private replyDeadline: number | null = null;
  private watchdog: ReturnType<typeof setInterval> | null = null;
  private failSession: ((code: string, message: string) => void) | null = null;

  constructor(private readonly options: RealtimeSessionOptions, private readonly cb: RealtimeLiveCallbacks) {}

  /** Resolves once the bridge reached the model; rejects with the bridge's error code. */
  open(): Promise<{ provider: string; model: string }> {
    return new Promise((resolve, reject) => {
      let settled = false;
      let ws: WebSocket;
      try {
        ws = this.options.socketFactory ? this.options.socketFactory(this.options.url) : new WebSocket(this.options.url);
      } catch (error) {
        // A bad URL or a host bridge that refuses: say so instead of throwing out of the Promise.
        reject(Object.assign(new Error(error instanceof Error ? error.message : String(error)), { code: "realtime_unreachable" }));
        return;
      }
      this.ws = ws;
      const fail = (code: string, message: string) => {
        if (!settled) {
          settled = true;
          clearTimeout(openTimer);
          reject(Object.assign(new Error(message || code), { code }));
        } else {
          this.cb.onError(code, message);
        }
      };
      const openTimer = setTimeout(() => {
        if (settled || this.closed) return;
        fail("realtime_unreachable", "the live bridge did not answer in time");
        // Dropping the dead socket is part of giving up, not a second failure.
        this.close();
      }, REALTIME_OPEN_TIMEOUT_MS);
      this.failSession = fail;
      ws.onmessage = (event) => {
        let frame: Frame;
        try {
          frame = JSON.parse(String(event.data)) as Frame;
        } catch {
          return;
        }
        if (!SETUP_FRAMES.has(String(frame.type ?? ""))) this.replyDeadline = null;
        if (frame.type === "navin.ready") {
          const info = { provider: String(frame.provider ?? ""), model: String(frame.model ?? "") };
          this.managedNova = info.provider === "navin" && info.model.toLowerCase().includes("sonic");
          this.configure(info.model);
          if (!settled) {
            settled = true;
            clearTimeout(openTimer);
            resolve(info);
          }
          this.cb.onReady?.(info);
          this.startWatchdog();
          return;
        }
        if (frame.type === "error") {
          const error = (frame.error ?? {}) as { code?: string; message?: string; type?: string };
          const code = String(error.code ?? error.type ?? "realtime_error");
          // A provider can accept the socket and then reject its audio session.
          // Surface that failure so Live can fall back instead of staying silent.
          const harmless = ["response_cancel_not_active", "input_audio_buffer_commit_empty"].includes(code);
          if (!settled || !harmless) {
            fail(code.startsWith("realtime_") ? code : "realtime_upstream_error", String(error.message ?? code));
          }
          return;
        }
        this.handle(frame);
      };
      ws.onerror = () => fail("realtime_unreachable", "");
      ws.onclose = (event) => {
        clearTimeout(openTimer);
        const detail = describeClose(event);
        if (!settled) fail("realtime_unreachable", detail || "closed");
        else if (!this.closed) this.cb.onError("realtime_dropped", detail);
        this.cb.onClose?.();
      };
    });
  }

  private startWatchdog(): void {
    if (this.watchdog !== null) return;
    this.watchdog = setInterval(() => {
      if (this.closed || this.replyDeadline === null || Date.now() < this.replyDeadline) return;
      this.replyDeadline = null;
      this.stopWatchdog();
      // Plain words only: this reaches the person on the call.
      this.failSession?.("realtime_unresponsive", "");
    }, 1_000);
  }

  private stopWatchdog(): void {
    if (this.watchdog !== null) clearInterval(this.watchdog);
    this.watchdog = null;
  }

  /** The model owes an answer from now on (kept if an earlier one is still owed). */
  private expectReply(): void {
    if (this.replyDeadline === null) this.replyDeadline = Date.now() + REALTIME_UNRESPONSIVE_MS;
  }

  /**
   * The microphone hears the user speak (called while they talk, so the
   * deadline counts from their last word: some models stay silent during a
   * long sentence). A working model reacts (speech start, transcript,
   * response); one still silent past the deadline is treated as unavailable so
   * the call can fall back instead of listening forever. Ignored while the
   * model itself is answering: that is its own voice.
   */
  noteLocalSpeech(): void {
    if (this.closed || this.responding) return;
    this.replyDeadline = Date.now() + REALTIME_UNRESPONSIVE_MS;
  }

  private send(frame: Frame): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(frame));
  }

  private configure(model: string): void {
    const voice = realtimeSessionVoice(this.options.voice, model, this.options.language ?? "en");
    this.send({
      type: "session.update",
      session: {
        modalities: ["audio", "text"],
        instructions: this.options.instructions,
        ...(voice ? { voice } : {}),
        input_audio_format: "pcm16",
        output_audio_format: "pcm16",
        ...(this.options.transcribeInput ? { input_audio_transcription: { model: "whisper-1" } } : {}),
        turn_detection: { type: "server_vad", silence_duration_ms: 650, prefix_padding_ms: 300 },
        tools: LIVE_TOOLS,
        tool_choice: "auto",
      },
    });
  }

  appendAudio(base64: string): void {
    this.send({ type: "input_audio_buffer.append", audio: base64 });
  }

  /** Something the agent said or needs: the voice relays it now. */
  announce(text: string): void {
    const clean = text.trim();
    if (!clean) return;
    this.send({
      type: "conversation.item.create",
      item: { type: "message", role: "user", content: [{ type: "input_text", text: `[Navin agent] ${clean}` }] },
    });
    this.requestResponse();
  }

  /** The opening line of the call, said by the voice itself in the user's language. */
  greet(text: string): void {
    const clean = text.trim();
    if (!clean || this.responding) return;
    this.responding = true;
    this.expectReply();
    this.send({
      type: "response.create",
      response: { instructions: `The call just started. Greet the user by saying exactly this, nothing else: "${clean}"` },
    });
  }

  private requestResponse(): void {
    if (this.responding) {
      // One response at a time: the next one is asked when this one is done.
      this.pendingResponse = true;
      return;
    }
    this.responding = true;
    this.expectReply();
    this.send({ type: "response.create" });
  }

  private pendingResponse = false;

  /** Cut the voice short (the user skipped it). */
  cancelResponse(): void {
    if (this.responding) this.send({ type: "response.cancel" });
  }

  private handle(frame: Frame): void {
    switch (frame.type) {
      case "input_audio_buffer.speech_started":
        this.cb.onUserSpeechStart?.();
        return;
      case "input_audio_buffer.speech_stopped":
        this.cb.onUserSpeechStop?.();
        return;
      case "conversation.item.input_audio_transcription.completed":
        if (typeof frame.transcript === "string" && frame.transcript.trim()) this.cb.onUserTranscript?.(frame.transcript.trim());
        return;
      case "response.created":
        this.responding = true;
        this.cb.onResponseStart?.();
        return;
      case "response.audio.delta":
      case "response.output_audio.delta":
        if (typeof frame.delta === "string") this.cb.onAudio(frame.delta);
        return;
      case "response.audio_transcript.done":
      case "response.output_audio_transcript.done":
        if (typeof frame.transcript === "string" && frame.transcript.trim()) this.cb.onAssistantTranscript?.(frame.transcript.trim());
        return;
      case "response.function_call_arguments.done":
        void this.runTool(String(frame.call_id ?? ""), String(frame.name ?? ""), frame.arguments);
        return;
      case "response.done": {
        this.responding = false;
        // Bridges that only report calls in the final response.
        const output = ((frame.response as { output?: unknown[] } | undefined)?.output ?? []) as Frame[];
        for (const item of output) {
          if (item.type === "function_call") void this.runTool(String(item.call_id ?? ""), String(item.name ?? ""), item.arguments);
        }
        this.cb.onResponseDone?.();
        if (this.pendingResponse) {
          this.pendingResponse = false;
          this.requestResponse();
        }
        return;
      }
      default:
        return;
    }
  }

  private async runTool(callId: string, name: string, rawArgs: unknown): Promise<void> {
    if (!callId || !name || this.handledCalls.has(callId)) return;
    this.handledCalls.add(callId);
    let output: string;
    try {
      const args: unknown = typeof rawArgs === "string" ? JSON.parse(rawArgs.trim() || "{}") : rawArgs ?? {};
      if (!args || typeof args !== "object" || Array.isArray(args)) throw new Error("Tool arguments must be a JSON object");
      const request = name === "ask_agent" ? String((args as { request?: unknown }).request ?? "").trim().toLowerCase() : "";
      const now = Date.now();
      if (request && this.lastAsk && this.lastAsk.request === request && now - this.lastAsk.at < ASK_DEDUP_MS) {
        // Some voice models (Nova) emit the same request twice: sending it again
        // would queue a second identical run in the chat.
        output = "Already sent to the agent, which is working on it.";
      } else {
        if (request) this.lastAsk = { request, at: now };
        output = await this.cb.onTool(name, args as Record<string, unknown>);
      }
    } catch (error) {
      output = `error: ${error instanceof Error ? error.message : String(error)}`;
    }
    // Bedrock requires JSON tool content, including acknowledgements and errors.
    // Encode here as well as in the bridge so older gateways receive valid JSON.
    if (this.managedNova) {
      let parsed: unknown;
      try { parsed = JSON.parse(output); } catch { parsed = null; }
      if (!parsed || typeof parsed !== "object") output = JSON.stringify({ result: output });
    }
    this.send({ type: "conversation.item.create", item: { type: "function_call_output", call_id: callId, output } });
    this.requestResponse();
  }

  close(): void {
    this.closed = true;
    this.stopWatchdog();
    const ws = this.ws;
    this.ws = null;
    try {
      ws?.close();
    } catch {
      // already closed
    }
  }
}

/**
 * The Live bridge on the same gateway as the chat socket: same host and
 * scheme, its own path, authorized with the API token.
 */
export function realtimeBridgeUrl(chatSocketUrl: string, apiToken: string): string {
  const base = typeof window !== "undefined" ? window.location.href : "http://127.0.0.1/";
  const url = new URL(chatSocketUrl, base);
  if (url.protocol === "http:") url.protocol = "ws:";
  if (url.protocol === "https:") url.protocol = "wss:";
  url.pathname = "/api/live/realtime";
  url.search = `?token=${encodeURIComponent(apiToken)}`;
  url.hash = "";
  return url.toString();
}
