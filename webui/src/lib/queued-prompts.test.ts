// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, beforeEach, describe, expect, it } from "vitest";

import { ApiError } from "./api-error";

import {
  QUEUED_PROMPTS_LIMIT,
  QUEUED_PROMPT_MAX_BYTES,
  clampQueuedText,
  failedMultitaskTargets,
  firstMultitaskError,
  normalizeQueuedOptions,
  normalizeQueuedPrompt,
  planMultitaskDispatch,
  queuedPromptAttachmentCount,
  queuedPromptIsMultitaskable,
  queuedPromptIsSendable,
  queuedPromptLabel,
  queuedPromptsStorageKey,
  readQueuedPrompts,
  reorderQueuedPrompts,
  restoreFailedQueuedPrompts,
  shiftQueuedPrompt,
  shouldRestoreFailedMultitask,
  storeQueuedPrompts,
  type QueuedPrompt,
} from "./queued-prompts";

function prompt(id: string, text = id): QueuedPrompt {
  return { id, text };
}

const STORAGE_KEY = "navin.webui.composerQueuedGuidance.v1:chat-1";

/** The suite runs on the node environment, so the queue's storage helpers get a
 * minimal localStorage to talk to. */
function installStorageStub(): () => void {
  const store = new Map<string, string>();
  const localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, String(value)),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  } satisfies Storage;
  const globals = globalThis as { window?: unknown };
  const previous = globals.window;
  globals.window = { localStorage };
  return () => {
    if (previous === undefined) delete globals.window;
    else globals.window = previous;
  };
}

describe("queuedPromptsStorageKey", () => {
  it("scopes the key per chat and ignores blank ids", () => {
    expect(queuedPromptsStorageKey("chat-1")).toBe(STORAGE_KEY);
    expect(queuedPromptsStorageKey("  chat-1  ")).toBe(STORAGE_KEY);
    expect(queuedPromptsStorageKey("")).toBeNull();
    expect(queuedPromptsStorageKey(null)).toBeNull();
    expect(queuedPromptsStorageKey(undefined)).toBeNull();
  });
});

describe("normalizeQueuedOptions", () => {
  it("keeps the attachments that identify a turn", () => {
    const options = normalizeQueuedOptions({
      cliApps: [{ name: "claude" }],
      mcpPresets: [{ name: "linear" }],
      fileMentions: [{ path: "src/app.ts", kind: "file" }],
      documentTemplate: { category: "ppt", name: "aurora_glass", language: "fr" },
      mediaTemplate: { id: "stock-portrait-169", title: "Portrait studio", format: "16:9" },
    });
    expect(options).toEqual({
      cliApps: [{ name: "claude" }],
      mcpPresets: [{ name: "linear" }],
      fileMentions: [{ path: "src/app.ts", kind: "file" }],
      documentTemplate: { category: "ppt", name: "aurora_glass", language: "fr" },
      mediaTemplate: { id: "stock-portrait-169", title: "Portrait studio", format: "16:9" },
    });
  });

  it("drops malformed entries and returns undefined when nothing is left", () => {
    expect(
      normalizeQueuedOptions({
        cliApps: [{ name: "" }, { display_name: "no name" }, "nope"],
        fileMentions: [{ kind: "file" }],
        documentTemplate: { category: "ppt" },
      }),
    ).toBeUndefined();
    expect(normalizeQueuedOptions(null)).toBeUndefined();
    expect(normalizeQueuedOptions("nope")).toBeUndefined();
  });

  it("keeps up to 6 media references and drops malformed ones", () => {
    const refs = Array.from({ length: 8 }, (_, index) => ({
      id: `ref-${index}`,
      title: `Ref ${index}`,
    }));
    const options = normalizeQueuedOptions({
      mediaTemplates: [...refs, { title: "no id" }, "nope"],
    });
    expect(options?.mediaTemplates).toHaveLength(6);
    expect(options?.mediaTemplates?.[0]).toEqual({ id: "ref-0", title: "Ref 0" });
  });
});

describe("clampQueuedText", () => {
  it("leaves anything the server would accept untouched", () => {
    const long = "x".repeat(50_000);
    expect(clampQueuedText(long)).toBe(long);
  });

  it("trims to the byte ceiling without splitting a code point", () => {
    // Each emoji is 4 UTF-8 bytes, so an odd budget must land on a boundary.
    const clamped = clampQueuedText("😀".repeat(10), 14);
    expect(clamped).toBe("😀".repeat(3));
    expect(new TextEncoder().encode(clamped).byteLength).toBe(12);
  });
});

describe("normalizeQueuedPrompt", () => {
  it("trims the text and keeps prompts far beyond the old 4000-char cap", () => {
    const restored = normalizeQueuedPrompt({ id: "a", text: `  ${"x".repeat(50_000)}  ` }, 0);
    expect(restored?.text).toHaveLength(50_000);
  });

  it("truncates only past the server text limit", () => {
    const restored = normalizeQueuedPrompt(
      { id: "a", text: "x".repeat(QUEUED_PROMPT_MAX_BYTES + 500) },
      0,
    );
    expect(restored?.text).toHaveLength(QUEUED_PROMPT_MAX_BYTES);
  });

  it("mints an id when the stored entry has none", () => {
    expect(normalizeQueuedPrompt({ text: "hello" }, 3)?.id).toBe("queued-prompt-restored-3");
  });

  it("rejects entries with neither text nor attachment", () => {
    expect(normalizeQueuedPrompt({ id: "a", text: "   " }, 0)).toBeNull();
    expect(normalizeQueuedPrompt({ id: "a" }, 0)).toBeNull();
    expect(normalizeQueuedPrompt("nope", 0)).toBeNull();
  });

  it("keeps an attachment-only prompt and infers the attachment kind", () => {
    const restored = normalizeQueuedPrompt(
      {
        id: "a",
        text: "",
        images: [
          { dataUrl: "data:image/png;base64,AAA" },
          { dataUrl: "data:application/pdf;base64,BBB", name: " report.pdf " },
          { dataUrl: "https://example.com/x.png" },
        ],
      },
      0,
    );
    expect(restored?.images).toEqual([
      { dataUrl: "data:image/png;base64,AAA", kind: "image" },
      { dataUrl: "data:application/pdf;base64,BBB", kind: "file", name: "report.pdf" },
    ]);
  });
});

describe("localStorage round-trip", () => {
  const restoreStorage = installStorageStub();
  afterAll(restoreStorage);

  beforeEach(() => {
    window.localStorage.clear();
  });

  it("restores prompts with their attachments and options", () => {
    const queued: QueuedPrompt[] = [
      {
        id: "a",
        text: "review the layout",
        images: [{ dataUrl: "data:image/png;base64,AAA", kind: "image", name: "shot.png" }],
        options: { fileMentions: [{ path: "webui/src/app.tsx", kind: "file" }] },
      },
    ];
    storeQueuedPrompts(STORAGE_KEY, queued);
    expect(readQueuedPrompts(STORAGE_KEY)).toEqual(queued);
  });

  it("removes the key when the queue is emptied", () => {
    storeQueuedPrompts(STORAGE_KEY, [prompt("a")]);
    storeQueuedPrompts(STORAGE_KEY, []);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(readQueuedPrompts(STORAGE_KEY)).toEqual([]);
  });

  it("caps the queue at the limit on both write and read", () => {
    const many = Array.from({ length: QUEUED_PROMPTS_LIMIT + 5 }, (_, i) => prompt(`p${i}`));
    storeQueuedPrompts(STORAGE_KEY, many);
    expect(readQueuedPrompts(STORAGE_KEY)).toHaveLength(QUEUED_PROMPTS_LIMIT);
  });

  /** Persist under a byte budget so the degradation ladder can be observed. */
  function storeWithQuota(prompts: QueuedPrompt[], quota: number): void {
    const raw = window.localStorage;
    const limited = {
      ...raw,
      getItem: (key: string) => raw.getItem(key),
      removeItem: (key: string) => raw.removeItem(key),
      setItem: (key: string, value: string) => {
        if (value.length > quota) throw new Error("QuotaExceededError");
        raw.setItem(key, value);
      },
    } as Storage;
    (window as { localStorage: Storage }).localStorage = limited;
    try {
      storeQueuedPrompts(STORAGE_KEY, prompts);
    } finally {
      (window as { localStorage: Storage }).localStorage = raw;
    }
  }

  const heavyQueue: QueuedPrompt[] = Array.from({ length: 8 }, (_, i) => ({
    id: `p${i}`,
    text: `prompt ${i}`,
    images: [{ dataUrl: `data:image/png;base64,${"A".repeat(200)}`, kind: "image" as const }],
  }));

  it("drops the inlined images before dropping any prompt", () => {
    storeWithQuota(heavyQueue, 400);
    const restored = readQueuedPrompts(STORAGE_KEY);
    expect(restored.map((p) => p.id)).toEqual(heavyQueue.map((p) => p.id));
    expect(restored.every((p) => !p.images)).toBe(true);
  });

  it("keeps the most recent prompts when even the text does not fit", () => {
    storeWithQuota(heavyQueue, 120);
    const restored = readQueuedPrompts(STORAGE_KEY);
    expect(restored.length).toBeGreaterThan(0);
    expect(restored.length).toBeLessThan(heavyQueue.length);
    expect(restored.at(-1)?.id).toBe("p7");
  });

  it("survives corrupted storage", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not json");
    expect(readQueuedPrompts(STORAGE_KEY)).toEqual([]);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ nope: true }));
    expect(readQueuedPrompts(STORAGE_KEY)).toEqual([]);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([{ text: "kept" }, null, 7]));
    expect(readQueuedPrompts(STORAGE_KEY).map((p) => p.text)).toEqual(["kept"]);
  });
});

describe("reordering", () => {
  const queue = [prompt("a"), prompt("b"), prompt("c")];

  it("moves a prompt onto the position of its drop target", () => {
    expect(reorderQueuedPrompts(queue, "c", "a").map((p) => p.id)).toEqual(["c", "a", "b"]);
    expect(reorderQueuedPrompts(queue, "a", "c").map((p) => p.id)).toEqual(["b", "c", "a"]);
  });

  it("returns the queue untouched for a no-op or unknown id", () => {
    expect(reorderQueuedPrompts(queue, "a", "a")).toBe(queue);
    expect(reorderQueuedPrompts(queue, "a", "zzz")).toBe(queue);
    expect(reorderQueuedPrompts(queue, "zzz", "a")).toBe(queue);
  });

  it("shifts by one position and clamps at the edges", () => {
    expect(shiftQueuedPrompt(queue, "b", -1).map((p) => p.id)).toEqual(["b", "a", "c"]);
    expect(shiftQueuedPrompt(queue, "b", 1).map((p) => p.id)).toEqual(["a", "c", "b"]);
    expect(shiftQueuedPrompt(queue, "a", -1)).toBe(queue);
    expect(shiftQueuedPrompt(queue, "c", 1)).toBe(queue);
    expect(shiftQueuedPrompt(queue, "zzz", 1)).toBe(queue);
  });
});

describe("prompt summaries", () => {
  it("labels an attachment-only prompt with its file names", () => {
    expect(
      queuedPromptLabel({
        id: "a",
        text: "",
        images: [
          { dataUrl: "data:image/png;base64,AAA", name: "one.png" },
          { dataUrl: "data:image/png;base64,BBB", name: "two.png" },
        ],
      }),
    ).toBe("one.png, two.png");
    expect(queuedPromptLabel({ id: "a", text: "  hello  " })).toBe("hello");
    expect(queuedPromptLabel({ id: "a", text: "z".repeat(1148) })).toBe(
      "[Pasted Content 1148 chars]",
    );
    expect(queuedPromptLabel({ id: "a", text: "" }, "Attachment")).toBe("Attachment");
  });

  it("counts every attachment carried by the prompt", () => {
    expect(
      queuedPromptAttachmentCount({
        id: "a",
        text: "go",
        images: [{ dataUrl: "data:image/png;base64,AAA" }],
        options: {
          cliApps: [{ name: "claude" }],
          fileMentions: [{ path: "a.ts", kind: "file" }, { path: "b.ts", kind: "file" }],
          documentTemplate: { category: "ppt", name: "aurora_glass" },
          mediaTemplates: [
            { id: "stock-portrait-169", title: "Portrait studio" },
            { id: "style-editorial-11", title: "Editorial grade" },
          ],
        },
      }),
    ).toBe(7);
    expect(queuedPromptAttachmentCount(prompt("a"))).toBe(0);
  });

  it("treats an attachment-only prompt as sendable", () => {
    expect(queuedPromptIsSendable(prompt("a", "go"))).toBe(true);
    expect(
      queuedPromptIsSendable({ id: "a", text: " ", images: [{ dataUrl: "data:image/png;base64,A" }] }),
    ).toBe(true);
    expect(queuedPromptIsSendable({ id: "a", text: "   " })).toBe(false);
  });

  it("only text-only prompts qualify for multitask dispatch", () => {
    expect(queuedPromptIsMultitaskable(prompt("a", "go"))).toBe(true);
    expect(queuedPromptIsMultitaskable({ id: "a", text: "   " })).toBe(false);
    expect(
      queuedPromptIsMultitaskable({
        id: "a",
        text: "go",
        images: [{ dataUrl: "data:image/png;base64,A" }],
      }),
    ).toBe(false);
    expect(
      queuedPromptIsMultitaskable({
        id: "a",
        text: "go",
        options: { fileMentions: [{ path: "src/app.ts", kind: "file" }] },
      }),
    ).toBe(false);
    expect(
      queuedPromptIsMultitaskable({
        id: "a",
        text: "go",
        options: { cliApps: [{ name: "claude" }] },
      }),
    ).toBe(false);
    expect(
      queuedPromptIsMultitaskable({
        id: "a",
        text: "go",
        options: { mcpPresets: [{ name: "linear" }] },
      }),
    ).toBe(false);
    expect(
      queuedPromptIsMultitaskable({
        id: "a",
        text: "go",
        options: { documentTemplate: { category: "ppt", name: "aurora_glass" } },
      }),
    ).toBe(false);
    expect(
      queuedPromptIsMultitaskable({
        id: "a",
        text: "go",
        options: { mediaTemplate: { id: "stock-portrait-169", title: "Portrait" } },
      }),
    ).toBe(false);
  });
});

describe("multitask dispatch", () => {
  const textA = prompt("a", "fix login");
  const textB = prompt("b", "write tests");
  const withFile: QueuedPrompt = {
    id: "c",
    text: "review this",
    options: { fileMentions: [{ path: "src/app.ts", kind: "file" }] },
  };

  it("takes only text-only prompts and leaves the rest in place", () => {
    expect(planMultitaskDispatch([textA, withFile, textB])).toEqual({
      targets: [textA, textB],
      remaining: [withFile],
    });
  });

  it("restores failed targets at the front of whatever arrived since", () => {
    expect(restoreFailedQueuedPrompts([withFile], [textB, textA])).toEqual([
      textB,
      textA,
      withFile,
    ]);
    expect(restoreFailedQueuedPrompts([withFile], [])).toEqual([withFile]);
  });

  it("restores only when the server refused, never when the spawn may be in flight", () => {
    expect(shouldRestoreFailedMultitask(new ApiError(504, "loop timed out"))).toBe(false);
    expect(shouldRestoreFailedMultitask(new ApiError(502, "gateway"))).toBe(false);
    expect(shouldRestoreFailedMultitask(new Error("Request timed out after 20000ms"))).toBe(false);
    expect(shouldRestoreFailedMultitask(new ApiError(409, "Cannot spawn"))).toBe(true);
    expect(shouldRestoreFailedMultitask(new ApiError(400, "a prompt is required"))).toBe(true);
    expect(shouldRestoreFailedMultitask(new ApiError(401, "Unauthorized"))).toBe(true);
    expect(shouldRestoreFailedMultitask(new ApiError(404, "session not found"))).toBe(true);
    expect(shouldRestoreFailedMultitask(new ApiError(500, "gateway exploded"))).toBe(false);
    expect(shouldRestoreFailedMultitask(new Error("network"))).toBe(true);
    const abort = new Error("The user aborted a request.");
    abort.name = "AbortError";
    expect(shouldRestoreFailedMultitask(abort)).toBe(false);
  });

  it("keeps going after one spawn fails and only restores the rejected ones", () => {
    const results: PromiseSettledResult<unknown>[] = [
      { status: "fulfilled", value: { ok: true } },
      { status: "rejected", reason: new ApiError(409, "Cannot spawn") },
      { status: "rejected", reason: new ApiError(504, "the agent loop did not answer in time") },
    ];
    const targets = [textA, textB, prompt("d", "third")];
    expect(failedMultitaskTargets(targets, results)).toEqual([textB]);
    const reason = firstMultitaskError(results);
    expect(reason).toBeInstanceOf(ApiError);
    expect((reason as ApiError).status).toBe(409);
  });

  it("does not surface a 504 as a composer error when the spawn is in flight", () => {
    const results: PromiseSettledResult<unknown>[] = [
      { status: "rejected", reason: new ApiError(504, "the agent loop did not answer in time") },
    ];
    expect(firstMultitaskError(results)).toBeNull();
    expect(failedMultitaskTargets([textA], results)).toEqual([]);
  });

  it("does not restore or surface a client abort", () => {
    const abort = new Error("The user aborted a request.");
    abort.name = "AbortError";
    const results: PromiseSettledResult<unknown>[] = [
      { status: "rejected", reason: abort },
    ];
    expect(firstMultitaskError(results)).toBeNull();
    expect(failedMultitaskTargets([textA], results)).toEqual([]);
  });
});

describe("queued messages panel (composer source lock)", () => {
  const composer = readFileSync(
    resolve(__dirname, "../components/thread/ThreadComposer.tsx"),
    "utf8",
  );

  it("shows one line per message with hover actions, Cursor-style", () => {
    expect(composer).toContain('data-testid="composer-queue"');
    expect(composer).toContain('data-testid="composer-queue-list"');
    expect(composer).toContain('data-testid="composer-queue-send"');
    expect(composer).toContain('data-testid="composer-queue-delete"');
    expect(composer).toContain('data-testid="composer-queue-lead"');
    expect(composer).toContain("min-w-0 flex-1 truncate");
    expect(composer).toContain("group-hover/queued:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100");
    expect(composer).not.toContain("line-clamp-3 whitespace-pre-wrap");
    expect(composer).not.toContain("<GripVertical");
  });

  it("keeps Start Multitasking visible whenever a session exists, disabled with a reason otherwise", () => {
    expect(composer).toContain('data-testid="composer-queue-multitask"');
    expect(composer).toContain("multitaskAvailable={Boolean(clientToken && sessionKey)}");
    expect(composer).toContain("multitaskCount={multitaskableCount}");
    expect(composer).toContain("const multitaskDisabled = multitaskBusy || multitaskCount === 0;");
    expect(composer).toContain("labels.multitaskUnavailable");
    expect(composer).not.toContain("multitaskCount > 0 ? (\n            <Button");
  });

  it("sits above the composer card as its own card, not inside the focus ring", () => {
    const slot = composer.indexOf('data-testid="composer-queue-slot"');
    const card = composer.indexOf('"group/composer relative mx-auto flex w-full flex-col');
    expect(slot).toBeGreaterThan(-1);
    expect(card).toBeGreaterThan(slot);
    expect(composer).toContain('"queued-prompt-row group/queued flex h-[26px] items-center');
    expect(composer).toContain("composer-status-strip relative mb-1.5 overflow-hidden rounded-[12px]");
  });

  it("lets the list fold behind the count without dropping the messages", () => {
    expect(composer).toContain('data-testid="composer-queue-toggle"');
    expect(composer).toContain('data-testid="composer-queue-close"');
    expect(composer).toContain("const [collapsed, setCollapsed] = useState(false);");
  });
});
