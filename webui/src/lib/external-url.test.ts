import { afterEach, describe, expect, it, vi } from "vitest";

import {
  attachmentDownloadUrl,
  externalHttpUrl,
  hrefActionFromClick,
  ideNavigationHash,
  shouldUseWindowOpenFallback,
} from "./external-url";

describe("shouldUseWindowOpenFallback", () => {
  it("never uses window.open inside the Tauri shell", () => {
    expect(shouldUseWindowOpenFallback(true)).toBe(false);
    expect(shouldUseWindowOpenFallback(false)).toBe(true);
  });
});

describe("externalHttpUrl", () => {
  it("returns an external http(s) href", () => {
    expect(externalHttpUrl("https://navin.live", "http://127.0.0.1:5173")).toBe(
      "https://navin.live/",
    );
    expect(externalHttpUrl("https://clawhub.ai", "http://127.0.0.1:5173")).toBe(
      "https://clawhub.ai/",
    );
  });

  it("ignores same-origin links", () => {
    expect(
      externalHttpUrl("http://127.0.0.1:5173/settings", "http://127.0.0.1:5173"),
    ).toBeNull();
  });

  it("ignores hash, relative and non-http hrefs", () => {
    expect(externalHttpUrl("#section", "http://127.0.0.1:5173")).toBeNull();
    expect(externalHttpUrl("/settings", "http://127.0.0.1:5173")).toBeNull();
    expect(externalHttpUrl("mailto:hi@navin.live", "http://127.0.0.1:5173")).toBeNull();
  });

  it("keeps New chat, Code and CRM hashes inside the IDE", () => {
    for (const hash of ["#/new", "#/chat/websocket%3A1", "#/code", "#/crm", "#/crm/contacts"]) {
      expect(externalHttpUrl(hash, "http://127.0.0.1:8766")).toBeNull();
      expect(externalHttpUrl(`http://127.0.0.1:8766/${hash}`, "http://127.0.0.1:8766")).toBeNull();
      expect(externalHttpUrl(`https://tauri.localhost/${hash}`, "http://127.0.0.1:8766")).toBeNull();
      expect(externalHttpUrl(`tauri://localhost/${hash}`, "http://127.0.0.1:8766")).toBeNull();
      expect(ideNavigationHash(hash, "http://127.0.0.1:8766")).toBe(hash);
      expect(ideNavigationHash(`http://127.0.0.1:8766/${hash}`, "http://127.0.0.1:8766")).toBe(hash);
      expect(ideNavigationHash(`https://tauri.localhost/${hash}`, "http://127.0.0.1:8766")).toBe(hash);
      expect(ideNavigationHash(`tauri://localhost/${hash}`, "http://127.0.0.1:8766")).toBe(hash);
    }
    expect(ideNavigationHash("/crm/contacts", "http://127.0.0.1:8766")).toBe("#/crm/contacts");
    expect(ideNavigationHash("/new", "http://127.0.0.1:8766")).toBe("#/new");
  });

  it("keeps the Career desk hash inside the IDE", () => {
    expect(externalHttpUrl("#/career", "http://127.0.0.1:8766")).toBeNull();
    expect(
      externalHttpUrl("http://127.0.0.1:8766/#/career?job=abc", "http://127.0.0.1:8766"),
    ).toBeNull();
    expect(externalHttpUrl("http://tauri.localhost/#/career", "http://127.0.0.1:8766")).toBeNull();
  });

  it("keeps Career desk hashes as in-IDE navigation", () => {
    expect(ideNavigationHash("#/career", "http://127.0.0.1:8766")).toBe("#/career");
    expect(
      ideNavigationHash("http://127.0.0.1:8766/#/career?job=abc", "http://127.0.0.1:8766"),
    ).toBe("#/career?job=abc");
    expect(ideNavigationHash("http://tauri.localhost/#/career", "http://127.0.0.1:8766")).toBe(
      "#/career",
    );
    expect(ideNavigationHash("/career", "http://127.0.0.1:8766")).toBe("#/career");
    expect(
      ideNavigationHash("https://www.linkedin.com/jobs/view/4242", "http://127.0.0.1:8766"),
    ).toBeNull();
  });

  it("keeps the Trading desk hash inside the IDE", () => {
    expect(externalHttpUrl("#/trading", "http://127.0.0.1:8766")).toBeNull();
    expect(
      externalHttpUrl("http://127.0.0.1:8766/#/trading?chat=websocket:1", "http://127.0.0.1:8766"),
    ).toBeNull();
    expect(externalHttpUrl("http://tauri.localhost/#/trading", "http://127.0.0.1:8766")).toBeNull();
    expect(externalHttpUrl("tauri://localhost/#/trading", "http://127.0.0.1:8766")).toBeNull();
  });

  it("keeps Trading desk hashes as in-IDE navigation", () => {
    expect(ideNavigationHash("#/trading", "http://127.0.0.1:8766")).toBe("#/trading");
    expect(
      ideNavigationHash("http://127.0.0.1:8766/#/trading?chat=websocket:1", "http://127.0.0.1:8766"),
    ).toBe("#/trading?chat=websocket:1");
    expect(ideNavigationHash("http://tauri.localhost/#/trading", "http://127.0.0.1:8766")).toBe(
      "#/trading",
    );
    expect(ideNavigationHash("/trading", "http://127.0.0.1:8766")).toBe("#/trading");
    expect(ideNavigationHash("tauri://localhost/#/trading", "http://127.0.0.1:8766")).toBe(
      "#/trading",
    );
    expect(
      ideNavigationHash("tauri://localhost/#/trading?chat=websocket:1", "http://tauri.localhost"),
    ).toBe("#/trading?chat=websocket:1");
  });

  it("keeps the Leads desk hash inside the IDE", () => {
    expect(externalHttpUrl("#/leads", "http://127.0.0.1:8766")).toBeNull();
    expect(
      externalHttpUrl("http://127.0.0.1:8766/#/leads?lead=abc", "http://127.0.0.1:8766"),
    ).toBeNull();
    expect(externalHttpUrl("http://tauri.localhost/#/leads", "http://127.0.0.1:8766")).toBeNull();
  });

  it("keeps Leads desk hashes as in-IDE navigation", () => {
    expect(ideNavigationHash("#/leads", "http://127.0.0.1:8766")).toBe("#/leads");
    expect(
      ideNavigationHash("http://127.0.0.1:8766/#/leads?lead=abc", "http://127.0.0.1:8766"),
    ).toBe("#/leads?lead=abc");
    expect(ideNavigationHash("http://tauri.localhost/#/leads", "http://127.0.0.1:8766")).toBe(
      "#/leads",
    );
    expect(ideNavigationHash("/leads", "http://127.0.0.1:8766")).toBe("#/leads");
  });

  it("keeps the Marketing desk hash inside the IDE", () => {
    expect(externalHttpUrl("#/marketing", "http://127.0.0.1:8766")).toBeNull();
    expect(
      externalHttpUrl("http://127.0.0.1:8766/#/marketing?chat=websocket:1", "http://127.0.0.1:8766"),
    ).toBeNull();
    expect(externalHttpUrl("http://tauri.localhost/#/marketing", "http://127.0.0.1:8766")).toBeNull();
    expect(externalHttpUrl("tauri://localhost/#/marketing", "http://127.0.0.1:8766")).toBeNull();
  });

  it("keeps Marketing desk hashes as in-IDE navigation", () => {
    expect(ideNavigationHash("#/marketing", "http://127.0.0.1:8766")).toBe("#/marketing");
    expect(
      ideNavigationHash("http://127.0.0.1:8766/#/marketing?chat=websocket:1", "http://127.0.0.1:8766"),
    ).toBe("#/marketing?chat=websocket:1");
    expect(ideNavigationHash("http://tauri.localhost/#/marketing", "http://127.0.0.1:8766")).toBe(
      "#/marketing",
    );
    expect(ideNavigationHash("/marketing", "http://127.0.0.1:8766")).toBe("#/marketing");
    expect(ideNavigationHash("tauri://localhost/#/marketing", "http://127.0.0.1:8766")).toBe(
      "#/marketing",
    );
    expect(
      ideNavigationHash("tauri://localhost/#/marketing?chat=websocket:1", "http://tauri.localhost"),
    ).toBe("#/marketing?chat=websocket:1");
  });

  it("sends official career boards out of the IDE", () => {
    expect(
      externalHttpUrl("https://www.linkedin.com/jobs/search/?keywords=Data", "http://127.0.0.1:8766"),
    ).toContain("linkedin.com");
    expect(
      externalHttpUrl("https://remotive.com/remote-jobs/api", "http://tauri.localhost"),
    ).toContain("remotive.com");
  });

  it("keeps the Tenders desk hash inside the IDE", () => {
    expect(externalHttpUrl("#/tenders", "http://127.0.0.1:8766")).toBeNull();
    expect(
      externalHttpUrl("http://127.0.0.1:8766/#/tenders?notice=abc", "http://127.0.0.1:8766"),
    ).toBeNull();
    expect(externalHttpUrl("http://tauri.localhost/#/tenders", "http://127.0.0.1:8766")).toBeNull();
    expect(externalHttpUrl("tauri://localhost/#/tenders", "http://127.0.0.1:8766")).toBeNull();
    expect(externalHttpUrl("http://ipc.localhost/", "http://127.0.0.1:8766")).toBeNull();
  });

  it("keeps Tenders desk hashes as in-IDE navigation", () => {
    expect(ideNavigationHash("#/tenders", "http://127.0.0.1:8766")).toBe("#/tenders");
    expect(
      ideNavigationHash("http://127.0.0.1:8766/#/tenders?notice=abc", "http://127.0.0.1:8766"),
    ).toBe("#/tenders?notice=abc");
    expect(
      ideNavigationHash("http://127.0.0.1:8766/#/tenders?pane=tenders", "http://127.0.0.1:8766"),
    ).toBe("#/tenders?pane=tenders");
    expect(ideNavigationHash("http://tauri.localhost/#/tenders", "http://127.0.0.1:8766")).toBe(
      "#/tenders",
    );
    expect(ideNavigationHash("/tenders", "http://127.0.0.1:8766")).toBe("#/tenders");
    expect(ideNavigationHash("tauri://localhost/#/tenders", "http://127.0.0.1:8766")).toBe(
      "#/tenders",
    );
    expect(
      ideNavigationHash("tauri://localhost/#/tenders?notice=abc", "http://tauri.localhost"),
    ).toBe("#/tenders?notice=abc");
    expect(ideNavigationHash("https://ted.europa.eu/en/notice/-/detail/1", "http://127.0.0.1:8766")).toBeNull();
  });

  it("sends official tender portals out of the IDE", () => {
    expect(
      externalHttpUrl("https://www.boamp.fr/pages/avis/?q=idweb:26-83870", "http://127.0.0.1:8766"),
    ).toContain("boamp.fr");
    expect(
      externalHttpUrl("https://ted.europa.eu/en/notice/-/detail/1", "http://tauri.localhost"),
    ).toContain("ted.europa.eu");
  });
});

describe("attachmentDownloadUrl", () => {
  it("treats the workspace download route as a file, not a navigation", () => {
    expect(
      attachmentDownloadUrl(
        "http://127.0.0.1:5173/api/sessions/k/file-download?path=a.pdf",
        "http://127.0.0.1:5173",
      ),
    ).toContain("/file-download");
  });

  it("treats media and note file routes the same way", () => {
    expect(
      attachmentDownloadUrl("http://127.0.0.1:5173/api/media/sig/x", "http://127.0.0.1:5173"),
    ).toContain("/api/media/");
    expect(
      attachmentDownloadUrl("http://127.0.0.1:5173/api/notes/file?id=1", "http://127.0.0.1:5173"),
    ).toContain("/api/notes/file");
  });

  it("leaves ordinary same-origin pages alone", () => {
    expect(
      attachmentDownloadUrl("http://127.0.0.1:5173/settings", "http://127.0.0.1:5173"),
    ).toBeNull();
  });
});

describe("hrefActionFromClick", () => {
  // Minimal DOM stand-ins: the vitest environment is node, and the function
  // only needs instanceof checks plus closest/getAttribute on the anchor.
  class FakeElement {}
  class FakeAnchor extends FakeElement {
    constructor(
      private href: string,
      private download: string | null,
    ) {
      super();
    }

    closest(selector: string): FakeAnchor | null {
      return selector === "a[href]" ? this : null;
    }

    getAttribute(name: string): string | null {
      if (name === "href") return this.href;
      if (name === "download") return this.download;
      return null;
    }

    hasAttribute(name: string): boolean {
      return name === "download" && this.download !== null;
    }
  }

  function click(href: string, download: string | null = null) {
    vi.stubGlobal("Element", FakeElement);
    vi.stubGlobal("HTMLAnchorElement", FakeAnchor);
    const event = {
      defaultPrevented: false,
      button: 0,
      target: new FakeAnchor(href, download),
    } as unknown as MouseEvent;
    return hrefActionFromClick(event);
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // The bug this guards: saveBlobInBrowser clicks an <a download href="blob:">
  // anchor. Intercepting that click re-saved the blob, which clicked a new
  // blob anchor, looping forever and cancelling every real download.
  it("keeps New chat, Code and CRM hashes as clicks inside the IDE", () => {
    expect(click("#/new")).toEqual({ kind: "internal", hash: "#/new" });
    expect(click("#/chat/websocket%3A1")).toEqual({ kind: "internal", hash: "#/chat/websocket%3A1" });
    expect(click("#/code")).toEqual({ kind: "internal", hash: "#/code" });
    expect(click("#/crm")).toEqual({ kind: "internal", hash: "#/crm" });
    expect(click("http://127.0.0.1:8766/#/crm/contacts")).toEqual({
      kind: "internal",
      hash: "#/crm/contacts",
    });
    expect(click("https://tauri.localhost/#/crm")).toEqual({ kind: "internal", hash: "#/crm" });
    expect(click("tauri://localhost/#/new")).toEqual({ kind: "internal", hash: "#/new" });
  });

  it("keeps a Career desk hash inside the IDE", () => {
    expect(click("#/career")).toEqual({ kind: "internal", hash: "#/career" });
    expect(click("http://127.0.0.1:8766/#/career?job=abc")).toEqual({
      kind: "internal",
      hash: "#/career?job=abc",
    });
  });

  it("keeps a Trading desk hash inside the IDE", () => {
    expect(click("#/trading")).toEqual({ kind: "internal", hash: "#/trading" });
    expect(click("http://127.0.0.1:8766/#/trading?chat=websocket:1")).toEqual({
      kind: "internal",
      hash: "#/trading?chat=websocket:1",
    });
    expect(click("tauri://localhost/#/trading?chat=websocket:1")).toEqual({
      kind: "internal",
      hash: "#/trading?chat=websocket:1",
    });
  });

  it("keeps a Leads desk hash inside the IDE", () => {
    expect(click("#/leads")).toEqual({ kind: "internal", hash: "#/leads" });
    expect(click("http://127.0.0.1:8766/#/leads?lead=abc")).toEqual({
      kind: "internal",
      hash: "#/leads?lead=abc",
    });
  });

  it("keeps a Tenders desk hash inside the IDE", () => {
    expect(click("#/tenders")).toEqual({ kind: "internal", hash: "#/tenders" });
    expect(click("http://127.0.0.1:8766/#/tenders?notice=abc")).toEqual({
      kind: "internal",
      hash: "#/tenders?notice=abc",
    });
    expect(click("tauri://localhost/#/tenders?notice=abc")).toEqual({
      kind: "internal",
      hash: "#/tenders?notice=abc",
    });
  });

  it("does not re-open a link the Tenders desk already handled", () => {
    vi.stubGlobal("Element", FakeElement);
    vi.stubGlobal("HTMLAnchorElement", FakeAnchor);
    const event = {
      defaultPrevented: true,
      button: 0,
      target: new FakeAnchor("https://ted.europa.eu/en/notice/-/detail/1", null),
    } as unknown as MouseEvent;
    expect(hrefActionFromClick(event)).toBeNull();
  });

  it("never intercepts a blob: download anchor", () => {
    expect(click("blob:http://127.0.0.1:5173/abc", "deck.json")).toBeNull();
  });

  it("never intercepts data: or javascript: anchors", () => {
    expect(click("data:text/plain;base64,aGk=", "a.txt")).toBeNull();
    expect(click("javascript:void(0)")).toBeNull();
  });

  it("still downloads the workspace file route", () => {
    const action = click(
      "http://127.0.0.1:5173/api/sessions/k/file-download?path=a.pdf",
      "a.pdf",
    );
    expect(action).toEqual({
      kind: "download",
      href: "http://127.0.0.1:5173/api/sessions/k/file-download?path=a.pdf",
      filename: "a.pdf",
    });
  });
});
