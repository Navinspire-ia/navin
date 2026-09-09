// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  htmlLooksLikeNavin,
  isNavinSelfPreviewUrlSync,
  normalizePreviewUrl,
  osBrowserPreviewUrl,
} from "./preview-url";

function stubLocation(href: string) {
  vi.stubGlobal("window", {
    location: new URL(href),
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("isNavinSelfPreviewUrlSync", () => {
  it("treats localhost and 127.0.0.1 on the same port as the editor", () => {
    stubLocation("http://localhost:5173/dev");
    expect(isNavinSelfPreviewUrlSync("http://127.0.0.1:5173")).toBe(true);
    expect(isNavinSelfPreviewUrlSync("http://localhost:5173/")).toBe(true);
  });

  it("does not treat another loopback port as Navin", () => {
    stubLocation("http://127.0.0.1:5173/");
    expect(isNavinSelfPreviewUrlSync("http://127.0.0.1:5174")).toBe(false);
    expect(isNavinSelfPreviewUrlSync("http://127.0.0.1:3000")).toBe(false);
  });

  it("always rejects the gateway port", () => {
    stubLocation("http://172.29.12.4:5173/");
    expect(isNavinSelfPreviewUrlSync("http://127.0.0.1:8765")).toBe(true);
  });

  it("treats tauri.localhost as the editor, never as a project preview", () => {
    stubLocation("http://tauri.localhost/#/code");
    expect(isNavinSelfPreviewUrlSync("http://tauri.localhost/")).toBe(true);
    expect(isNavinSelfPreviewUrlSync("http://tauri.localhost/#/code")).toBe(true);
  });
});

describe("htmlLooksLikeNavin", () => {
  it("recognizes the editor shell", () => {
    expect(htmlLooksLikeNavin('<html data-navin-webui>')).toBe(true);
    expect(htmlLooksLikeNavin("Loading Navin…")).toBe(true);
    expect(htmlLooksLikeNavin("<html><title>CRM</title></html>")).toBe(false);
  });
});

describe("normalizePreviewUrl", () => {
  it("adds http when the scheme is missing", () => {
    expect(normalizePreviewUrl("127.0.0.1:3000")).toBe("http://127.0.0.1:3000");
  });
});

describe("osBrowserPreviewUrl", () => {
  it("prefers the typed project URL over the telemetry proxy", () => {
    stubLocation("http://127.0.0.1:8766/#/code");
    expect(
      osBrowserPreviewUrl({
        browserUrl: "http://127.0.0.1:5176",
        browserSrc: "http://127.0.0.1:18792/",
        previewTargetPort: 5176,
      }),
    ).toBe("http://127.0.0.1:5176");
  });

  it("reconstructs from the preview port when the bar is empty", () => {
    stubLocation("http://127.0.0.1:8766/#/code");
    expect(
      osBrowserPreviewUrl({
        browserUrl: "",
        browserSrc: "http://127.0.0.1:18792/",
        previewTargetPort: 5175,
      }),
    ).toBe("http://127.0.0.1:5175");
  });

  it("never returns the editor itself", () => {
    stubLocation("http://127.0.0.1:8766/#/code");
    expect(
      osBrowserPreviewUrl({
        browserUrl: "http://127.0.0.1:8766/#/code",
        browserSrc: "http://127.0.0.1:8766/",
        previewTargetPort: 8766,
      }),
    ).toBe("");
  });
});
