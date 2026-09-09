// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Integration check for the preview probe command protocol.
 * Loads PROBE_JS in a fake window and verifies clear-cookies / clear-cache /
 * screenshot / hard-reload postMessage replies.
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import vm from "node:vm";

function loadProbeJs(): string {
  // webui/src/lib -> repo root
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
  const python = path.join(root, ".venv/bin/python");
  const result = spawnSync(
    python,
    ["-c", "from navin.webui.preview_probe import PROBE_JS; print(PROBE_JS)"],
    {
      cwd: root,
      encoding: "utf-8",
      env: { ...process.env, PYTHONPATH: root },
    },
  );
  if (result.status !== 0) {
    throw new Error(result.stderr || result.error?.message || "failed to load PROBE_JS");
  }
  return result.stdout;
}

type ProbeReply = {
  source?: string;
  id?: number;
  ok?: boolean;
  dataUrl?: string;
} & Record<string, unknown>;
type ProbeMessageEvent = { data?: unknown; origin: string; source?: unknown };
type ProbeListener = (event: ProbeMessageEvent) => void;

/** Just enough of an element for the design-mode picker to describe it. */
type FakeElement = {
  nodeType: 1;
  tagName: string;
  id: string;
  className: string;
  textContent: string;
  innerText: string;
  parentElement: FakeElement | null;
  children: FakeElement[];
  style: Record<string, string>;
  offsetWidth: number;
  offsetHeight: number;
  attributes: Record<string, string>;
  getAttribute(name: string): string | null;
  setAttribute(name: string, value: string): void;
  appendChild(child: FakeElement): FakeElement;
  removeChild(child: FakeElement): FakeElement;
  parentNode: FakeElement | null;
  getBoundingClientRect(): { left: number; top: number; right: number; bottom: number; width: number; height: number };
} & Record<string, unknown>;

function fakeElement(
  tag: string,
  options: {
    id?: string;
    className?: string;
    text?: string;
    attrs?: Record<string, string>;
    rect?: { left: number; top: number; width: number; height: number };
  } = {},
): FakeElement {
  const rect = options.rect ?? { left: 0, top: 0, width: 0, height: 0 };
  const el: FakeElement = {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    id: options.id ?? "",
    className: options.className ?? "",
    textContent: options.text ?? "",
    innerText: options.text ?? "",
    parentElement: null,
    parentNode: null,
    children: [],
    style: {},
    offsetWidth: 120,
    offsetHeight: 30,
    attributes: { ...(options.attrs ?? {}) },
    getAttribute(name) {
      if (name === "class") return this.className || null;
      return this.attributes[name] ?? null;
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
    appendChild(child) {
      child.parentElement = this;
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    removeChild(child) {
      this.children = this.children.filter((node) => node !== child);
      child.parentElement = null;
      child.parentNode = null;
      return child;
    },
    getBoundingClientRect() {
      return {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        right: rect.left + rect.width,
        bottom: rect.top + rect.height,
      };
    },
  };
  return el;
}

function makeWindow(origin = "http://127.0.0.1:4173") {
  const listeners = new Map<string, Set<ProbeListener>>();
  const parentListeners = new Map<string, Set<ProbeListener>>();
  const documentListeners = new Map<string, Set<(event: unknown) => void>>();
  const cookieJar = new Map<string, string>();
  const cacheKeys = new Set(["v1", "v2"]);
  let reloaded = false;
  let elementAtPoint: FakeElement | null = null;
  const head = fakeElement("head");
  const body = fakeElement("body");
  const html = fakeElement("html");
  html.appendChild(head);
  html.appendChild(body);

  const parent = {
    postMessage(data: unknown) {
      const event = {
        data,
        origin: origin,
        source: parent,
      };
      for (const fn of parentListeners.get("message") ?? []) fn(event);
    },
    addEventListener(type: string, fn: ProbeListener) {
      if (!parentListeners.has(type)) parentListeners.set(type, new Set());
      parentListeners.get(type)!.add(fn);
    },
  };

  const windowObj = {
    __navinProbeInstalled: false,
    location: {
      href: `${origin}/`,
      reload() {
        reloaded = true;
      },
    },
    innerWidth: 320,
    innerHeight: 200,
    devicePixelRatio: 1,
    console: { log() {}, info() {}, warn() {}, error() {}, debug() {} },
    fetch: async () => ({ ok: true, status: 200 }),
    addEventListener(type: string, fn: ProbeListener) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(fn);
    },
    removeEventListener(type: string, fn: ProbeListener) {
      listeners.get(type)?.delete(fn);
    },
    setInterval() {
      return 1;
    },
    XMLHttpRequest: class {
      open() {}
      send() {}
      addEventListener() {}
      status = 200;
    },
    Image: class {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(_v: string) {
        queueMicrotask(() => this.onload?.());
      }
    },
    XMLSerializer: class {
      serializeToString() {
        return "<div>hi</div>";
      }
    },
    URL,
    encodeURIComponent,
    atob,
    btoa,
    document: {
      cookie: {
        get length() {
          return undefined;
        },
        toString() {
          return [...cookieJar.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
        },
      },
      documentElement: Object.assign(html, {
        clientWidth: 320,
        clientHeight: 200,
        cloneNode() {
          return {
            querySelectorAll() {
              return [];
            },
          };
        },
      }),
      head,
      body,
      title: "Fake app",
      createElement(tag: string) {
        if (tag === "canvas") {
          return {
            width: 0,
            height: 0,
            getContext() {
              return {
                fillStyle: "",
                fillRect() {},
                setTransform() {},
                drawImage() {},
              };
            },
            toDataURL() {
              return "data:image/png;base64,aaa";
            },
          };
        }
        return fakeElement(tag);
      },
      elementFromPoint() {
        return elementAtPoint;
      },
      querySelectorAll() {
        // Every selector is "unique": the picker stops at the first segment.
        return elementAtPoint ? [elementAtPoint] : [];
      },
      addEventListener(type: string, fn: (event: unknown) => void) {
        if (!documentListeners.has(type)) documentListeners.set(type, new Set());
        documentListeners.get(type)!.add(fn);
      },
      removeEventListener(type: string, fn: (event: unknown) => void) {
        documentListeners.get(type)?.delete(fn);
      },
    },
    getComputedStyle() {
      return { backgroundColor: "#fff" };
    },
    caches: {
      keys: async () => [...cacheKeys],
      delete: async (key: string) => {
        cacheKeys.delete(key);
        return true;
      },
    },
  };

  // Cookie getter/setter on document
  Object.defineProperty(windowObj.document, "cookie", {
    get() {
      return [...cookieJar.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
    },
    set(value: string) {
      const [pair] = value.split(";");
      const [name, ...rest] = pair.split("=");
      const n = name.trim();
      const v = rest.join("=");
      if (value.includes("expires=Thu, 01 Jan 1970")) cookieJar.delete(n);
      else cookieJar.set(n, v);
    },
    configurable: true,
  });

  cookieJar.set("session", "abc");
  cookieJar.set("theme", "dark");

  const context = vm.createContext({
    window: windowObj,
    document: windowObj.document,
    location: windowObj.location,
    console: windowObj.console,
    setInterval: windowObj.setInterval,
    URL: windowObj.URL,
    Image: windowObj.Image,
    XMLSerializer: windowObj.XMLSerializer,
    XMLHttpRequest: windowObj.XMLHttpRequest,
    encodeURIComponent,
    getComputedStyle: windowObj.getComputedStyle,
    caches: windowObj.caches,
  });

  function dispatchToProbe(data: unknown, fromOrigin = "http://127.0.0.1:8765") {
    const event = {
      data,
      origin: fromOrigin,
      source: {
        postMessage(payload: unknown) {
          parent.postMessage(payload);
        },
      },
    };
    for (const fn of listeners.get("message") ?? []) fn(event);
  }

  function waitReply(id: number, timeoutMs = 1000): Promise<ProbeReply> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("timeout")), timeoutMs);
      const onMsg: ProbeListener = (event) => {
        const data = event.data as ProbeReply | undefined;
        if (data?.source !== "navin-preview-result") return;
        if (data?.id !== id) return;
        clearTimeout(timer);
        parentListeners.get("message")?.delete(onMsg);
        resolve(data);
      };
      parent.addEventListener("message", onMsg);
    });
  }

  function waitEvent(name: string, timeoutMs = 1000): Promise<ProbeReply> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("timeout")), timeoutMs);
      const onMsg: ProbeListener = (event) => {
        const data = event.data as ProbeReply | undefined;
        if (data?.source !== "navin-preview-result" || data?.event !== name) return;
        clearTimeout(timer);
        parentListeners.get("message")?.delete(onMsg);
        resolve(data);
      };
      parent.addEventListener("message", onMsg);
    });
  }

  function fireDocument(type: string, event: Record<string, unknown>) {
    const full = {
      preventDefault() {
        full.defaultPrevented = true;
      },
      stopPropagation() {},
      stopImmediatePropagation() {},
      defaultPrevented: false,
      ...event,
    };
    for (const fn of documentListeners.get(type) ?? []) fn(full);
    return full;
  }

  return {
    install(probeJs: string) {
      vm.runInContext(probeJs, context);
    },
    dispatchToProbe,
    waitReply,
    waitEvent,
    fireDocument,
    setElementAt(el: FakeElement | null) {
      elementAtPoint = el;
    },
    head,
    body,
    get reloaded() {
      return reloaded;
    },
    get cookies() {
      // The literal types document.cookie as an object; defineProperty above
      // replaces it with a real string-returning accessor at runtime.
      return windowObj.document.cookie as unknown as string;
    },
    get remainingCaches() {
      return [...cacheKeys];
    },
  };
}

describe("preview probe command protocol", () => {
  const probeJs = loadProbeJs();

  it("embeds the navin-preview command handlers", () => {
    expect(probeJs).toContain("navin-preview");
    expect(probeJs).toContain("navin-preview-result");
    expect(probeJs).toContain("hard-reload");
    expect(probeJs).toContain("clear-cookies");
    expect(probeJs).toContain("clear-cache");
    expect(probeJs).toContain("screenshot");
  });

  it("clears cookies and replies ok", async () => {
    const win = makeWindow();
    win.install(probeJs);
    expect(win.cookies).toContain("session=abc");
    const replyP = win.waitReply(1);
    win.dispatchToProbe({ source: "navin-preview", id: 1, cmd: "clear-cookies" });
    const reply = await replyP;
    expect(reply.ok).toBe(true);
    expect(win.cookies).toBe("");
  });

  it("clears cache and replies ok", async () => {
    const win = makeWindow();
    win.install(probeJs);
    const replyP = win.waitReply(2);
    win.dispatchToProbe({ source: "navin-preview", id: 2, cmd: "clear-cache" });
    const reply = await replyP;
    expect(reply.ok).toBe(true);
    expect(win.remainingCaches).toEqual([]);
  });

  it("hard-reloads the page", async () => {
    const win = makeWindow();
    win.install(probeJs);
    win.dispatchToProbe({ source: "navin-preview", id: 3, cmd: "hard-reload" });
    // hard-reload does not always reply (location.reload)
    await new Promise((r) => setTimeout(r, 20));
    expect(win.reloaded).toBe(true);
  });

  it("returns a screenshot data URL", async () => {
    const win = makeWindow();
    win.install(probeJs);
    const replyP = win.waitReply(4);
    win.dispatchToProbe({ source: "navin-preview", id: 4, cmd: "screenshot" });
    const reply = await replyP;
    expect(reply.ok).toBe(true);
    expect(reply.dataUrl).toMatch(/^data:image\/png;base64,/);
  });

  it("ignores untrusted origins", async () => {
    const win = makeWindow();
    win.install(probeJs);
    let got = false;
    const replyP = win.waitReply(5, 150).then(
      () => {
        got = true;
      },
      () => {
        got = false;
      },
    );
    win.dispatchToProbe(
      { source: "navin-preview", id: 5, cmd: "clear-cookies" },
      "https://evil.example",
    );
    await replyP;
    expect(got).toBe(false);
    expect(win.cookies).toContain("session=abc");
  });
});

describe("preview probe design mode", () => {
  const probeJs = loadProbeJs();

  /** Replies are synchronous: listen before dispatching. */
  function command(
    win: ReturnType<typeof makeWindow>,
    id: number,
    cmd: string,
    extra: Record<string, unknown> = {},
  ): Promise<ProbeReply> {
    const replyP = win.waitReply(id);
    win.dispatchToProbe({ source: "navin-preview", id, cmd, ...extra });
    return replyP;
  }

  /** A React-rendered button: fiber chain Button -> ProjectGroupHeader -> ChatList. */
  function reactButton() {
    const chatList = { type: function ChatList() {}, return: null, _debugOwner: null };
    const header = {
      type: Object.assign(function ProjectGroupHeader() {}, {}),
      return: chatList,
    };
    const hostFiber = {
      type: "button",
      return: header,
      _debugOwner: header,
      _debugSource: { fileName: "/home/me/proj/webui/src/sidebar/ProjectGroupHeader.tsx", lineNumber: 42 },
    };
    const nav = fakeElement("nav");
    const button = fakeElement("button", {
      className: "flex items-center gap-2",
      text: "  navin-ai-v2 \n",
      attrs: { "data-testid": "project-group-header", "aria-label": "Toggle project" },
      rect: { left: 16, top: 64, width: 250, height: 28 },
    });
    button["__reactFiber$abc123"] = hostFiber;
    nav.appendChild(button);
    return button;
  }

  it("arms on pick-start, outlines the hovered element with its component", async () => {
    const win = makeWindow();
    win.install(probeJs);
    const button = reactButton();
    win.setElementAt(button);

    const reply = await command(win, 10, "pick-start", { hint: "Cliquez" });
    expect(reply.ok).toBe(true);
    expect(reply.active).toBe(true);
    // Crosshair cursor while active, overlay nodes appended to the body.
    expect(win.head.children.some((node) => node.tagName === "STYLE")).toBe(true);
    const box = win.body.children.find((node) => node.attributes["data-navin-pick"] === "box");
    const label = win.body.children.find((node) => node.attributes["data-navin-pick"] === "label");
    expect(box && label).toBeTruthy();

    win.fireDocument("mousemove", { clientX: 20, clientY: 70 });
    expect(box!.style.display).toBe("block");
    expect(box!.style.left).toBe("16px");
    expect(box!.style.width).toBe("250px");
    expect(label!.children[0]!.textContent).toBe("ProjectGroupHeader \u00b7 button");
    expect(label!.children[1]!.textContent).toBe("Cliquez");
  });

  it("describes the clicked element to the parent and swallows the click", async () => {
    const win = makeWindow();
    win.install(probeJs);
    const button = reactButton();
    win.setElementAt(button);
    await command(win, 11, "pick-start");

    const pickP = win.waitEvent("pick");
    const click = win.fireDocument("click", { clientX: 20, clientY: 70 });
    const picked = await pickP;
    expect(click.defaultPrevented).toBe(true);
    const element = picked.element as Record<string, unknown>;
    expect(element.tag).toBe("button");
    expect(element.component).toBe("ProjectGroupHeader");
    expect(element.ancestors).toEqual(["ProjectGroupHeader", "ChatList"]);
    expect(element.source).toEqual({
      file: "/home/me/proj/webui/src/sidebar/ProjectGroupHeader.tsx",
      line: 42,
    });
    expect(element.selector).toBe('button[data-testid="project-group-header"]');
    expect(element.text).toBe("navin-ai-v2");
    expect(element.classes).toBe("flex items-center gap-2");
    expect(element.attributes).toEqual({ "aria-label": "Toggle project" });
    expect(element.rect).toEqual({ x: 16, y: 64, width: 250, height: 28 });
    expect(element.url).toBe("http://127.0.0.1:4173/");

    // Hovering elsewhere no longer moves the outline until the parent resumes.
    const other = fakeElement("p", { rect: { left: 300, top: 300, width: 50, height: 20 } });
    win.setElementAt(other);
    win.fireDocument("mousemove", { clientX: 310, clientY: 310 });
    const box = win.body.children.find((node) => node.attributes["data-navin-pick"] === "box")!;
    expect(box.style.left).toBe("16px");
    expect((await command(win, 12, "pick-resume")).active).toBe(true);
    win.fireDocument("mousemove", { clientX: 310, clientY: 310 });
    expect(box.style.left).toBe("300px");
  });

  it("falls back to the tag for plain DOM and reads React 19 stacks", async () => {
    const win = makeWindow();
    win.install(probeJs);
    const plain = fakeElement("section", { id: "hero", rect: { left: 0, top: 10, width: 300, height: 100 } });
    win.setElementAt(plain);
    await command(win, 13, "pick-start");
    let pickP = win.waitEvent("pick");
    win.fireDocument("click", { clientX: 5, clientY: 20 });
    let element = (await pickP).element as Record<string, unknown>;
    expect(element.component).toBe("");
    expect(element.selector).toBe("#hero");
    expect(element.source).toBeNull();

    const modern = fakeElement("li", { rect: { left: 0, top: 0, width: 10, height: 10 } });
    modern["__reactFiber$xyz"] = {
      type: "li",
      return: { type: function ChatRow() {}, return: null },
      _debugOwner: { name: "ChatRow" },
      _debugStack: {
        stack: [
          "Error",
          "    at jsxDEV (http://localhost:5173/node_modules/.vite/deps/react_jsx-dev-runtime.js?v=1:250:1)",
          "    at ChatRow (http://localhost:5173/src/components/ChatRow.tsx?t=1712:88:12)",
        ].join("\n"),
      },
    };
    win.setElementAt(modern);
    await command(win, 14, "pick-resume");
    pickP = win.waitEvent("pick");
    win.fireDocument("click", { clientX: 5, clientY: 5 });
    element = (await pickP).element as Record<string, unknown>;
    expect(element.component).toBe("ChatRow");
    expect(element.source).toEqual({ file: "/src/components/ChatRow.tsx", line: 88 });
  });

  it("leaves on Escape and on pick-stop, removing the cursor override", async () => {
    const win = makeWindow();
    win.install(probeJs);
    win.setElementAt(fakeElement("div"));
    await command(win, 15, "pick-start");
    expect(win.head.children.some((node) => node.tagName === "STYLE")).toBe(true);

    const cancelP = win.waitEvent("pick-cancel");
    win.fireDocument("keydown", { key: "Escape" });
    await cancelP;
    expect(win.head.children.some((node) => node.tagName === "STYLE")).toBe(false);

    await command(win, 16, "pick-start");
    expect((await command(win, 17, "pick-stop")).active).toBe(false);
    expect(win.head.children.some((node) => node.tagName === "STYLE")).toBe(false);
    // Clicks are the page's own again.
    const click = win.fireDocument("click", { clientX: 1, clientY: 1 });
    expect(click.defaultPrevented).toBe(false);
  });

  it("ignores pick-start from an untrusted origin", async () => {
    const win = makeWindow();
    win.install(probeJs);
    win.dispatchToProbe({ source: "navin-preview", id: 18, cmd: "pick-start" }, "https://evil.example");
    await new Promise((r) => setTimeout(r, 20));
    expect(win.head.children.some((node) => node.tagName === "STYLE")).toBe(false);
  });
});
