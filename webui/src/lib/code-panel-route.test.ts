// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  codePanelFromParam,
  codePanelHref,
  legacyCodePanelForPath,
  rewriteToCodePanel,
} from "./code-panel-route";

describe("codePanelFromParam", () => {
  it("accepts the panels Code hosts", () => {
    expect(codePanelFromParam("project")).toBe("project");
    expect(codePanelFromParam("templates")).toBe("templates");
    expect(codePanelFromParam("evolve")).toBe("evolve");
  });

  it("tolerates casing and surrounding spaces", () => {
    expect(codePanelFromParam(" Evolve ")).toBe("evolve");
  });

  it("opens plain Code for anything else", () => {
    expect(codePanelFromParam(null)).toBeNull();
    expect(codePanelFromParam("")).toBeNull();
    expect(codePanelFromParam("evolution")).toBeNull();
  });
});

describe("legacyCodePanelForPath", () => {
  it("maps the standalone hashes that moved into Code", () => {
    expect(legacyCodePanelForPath("/evolve")).toBe("evolve");
    expect(legacyCodePanelForPath("/project")).toBe("project");
    expect(legacyCodePanelForPath("/home")).toBe("project");
    expect(legacyCodePanelForPath("/templates")).toBe("templates");
  });

  it("leaves other modules alone", () => {
    expect(legacyCodePanelForPath("/code")).toBeNull();
    expect(legacyCodePanelForPath("/notes")).toBeNull();
  });
});

describe("rewriteToCodePanel", () => {
  it("keeps the chat a shared Evolve link was taken from", () => {
    const chatKey = "websocket:6f1c0a4e-2f6d-4c2e-9c4a-8b1d3f5e7a90";
    const result = rewriteToCodePanel(
      "evolve",
      `chat=${encodeURIComponent(chatKey)}`,
      null,
    );
    expect(result.activeKey).toBe(chatKey);
    expect(result.hash).toBe(
      `#/code?chat=${encodeURIComponent(chatKey)}&panel=evolve`,
    );
  });

  it("falls back to the chat already open when the link carries none", () => {
    const result = rewriteToCodePanel("evolve", "", "websocket:current");
    expect(result.activeKey).toBe("websocket:current");
    expect(result.hash).toBe("#/code?panel=evolve");
  });

  it("carries the other query parameters over", () => {
    const result = rewriteToCodePanel("project", "chat=a&ref=mail", null);
    expect(result.hash).toBe("#/code?chat=a&ref=mail&panel=project");
  });

  it("replaces a panel the link already targeted", () => {
    const result = rewriteToCodePanel("evolve", "panel=project", null);
    expect(result.hash).toBe("#/code?panel=evolve");
    expect(result.activeKey).toBeNull();
  });
});

describe("codePanelHref", () => {
  it("keeps the chat when opening Evolve from the review bar", () => {
    const chatKey = "websocket:6f1c0a4e-2f6d-4c2e-9c4a-8b1d3f5e7a90";
    expect(codePanelHref("evolve", chatKey)).toBe(
      `#/code?chat=${encodeURIComponent(chatKey)}&panel=evolve`,
    );
  });

  it("still opens the panel when no chat is bound", () => {
    expect(codePanelHref("evolve")).toBe("#/code?panel=evolve");
    expect(codePanelHref("evolve", "  ")).toBe("#/code?panel=evolve");
  });
});
