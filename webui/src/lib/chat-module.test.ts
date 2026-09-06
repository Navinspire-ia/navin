import { describe, expect, it } from "vitest";

import {
  inferChatModuleFromPreview,
  isChatOwningModule,
  normalizeChatModuleView,
  projectModuleKey,
  resolveChatOpenView,
  resolveProjectOpenView,
  viewForCreatedChat,
} from "./chat-module";

describe("chat-module", () => {
  it("maps code alias to internal dev view", () => {
    expect(normalizeChatModuleView("code")).toBe("dev");
    expect(normalizeChatModuleView("CODE")).toBe("dev");
    expect(normalizeChatModuleView("montage")).toBe("montage");
  });

  it("opens a code chat back in the code module", () => {
    expect(
      resolveChatOpenView("websocket:1", { "websocket:1": "dev" }, "chat"),
    ).toBe("dev");
    expect(
      resolveChatOpenView("websocket:1", { "websocket:1": "code" }, "marketing"),
    ).toBe("dev");
  });

  it("falls back to chat when no module is known", () => {
    expect(resolveChatOpenView("websocket:missing", {}, "chat")).toBe("chat");
  });

  it("infers the desk from the preview slash when no tag is stored", () => {
    expect(
      resolveChatOpenView("websocket:1", {}, "chat", "/forge ship the landing"),
    ).toBe("dev");
    expect(
      resolveChatOpenView("websocket:1", {}, "dev", "/ask what is auth"),
    ).toBe("chat");
    expect(
      resolveChatOpenView("websocket:1", {}, "chat", "/meeting Build report"),
    ).toBe("meeting");
  });

  it("lets the preview slash override a stale Code tag", () => {
    expect(
      resolveChatOpenView(
        "websocket:1",
        { "websocket:1": "dev" },
        "dev",
        "/ask cc",
      ),
    ).toBe("chat");
    expect(
      resolveChatOpenView(
        "websocket:1",
        { "websocket:1": "montage" },
        "chat",
        "/forge ship it",
      ),
    ).toBe("dev");
  });

  it("detects chat-owning modules", () => {
    expect(isChatOwningModule("dev")).toBe(true);
    expect(isChatOwningModule("code")).toBe(true);
    expect(isChatOwningModule("chat")).toBe(false);
    expect(isChatOwningModule("crm")).toBe(true);
    expect(isChatOwningModule("trading")).toBe(true);
    expect(isChatOwningModule("settings")).toBe(false);
  });
});

describe("inferChatModuleFromPreview", () => {
  it("maps known composer slashes", () => {
    expect(inferChatModuleFromPreview("/forge hello")).toBe("dev");
    expect(inferChatModuleFromPreview("/ask hello")).toBe("chat");
    expect(inferChatModuleFromPreview("/meeting notes")).toBe("meeting");
    expect(inferChatModuleFromPreview("/crm pipeline")).toBe("crm");
    expect(inferChatModuleFromPreview("/trading scan nasdaq")).toBe("trading");
    expect(inferChatModuleFromPreview("/career find data engineer")).toBe("career");
    expect(inferChatModuleFromPreview("plain text")).toBe(null);
  });
});

describe("project module tags", () => {
  it("keys a folder the same with or without a trailing separator", () => {
    expect(projectModuleKey("/home/me/app/")).toBe("/home/me/app");
    expect(projectModuleKey("  /home/me/app  ")).toBe("/home/me/app");
    expect(projectModuleKey("C:\\work\\app\\")).toBe("C:/work/app");
    expect(projectModuleKey("/")).toBe("/");
    expect(projectModuleKey(null)).toBe("");
  });

  it("opens a folder tagged code in the code workbench", () => {
    const tags = { "/home/me/app": "code" };
    expect(resolveProjectOpenView("/home/me/app/", tags, "chat")).toBe("dev");
    expect(resolveProjectOpenView("/home/me/app", tags, "chat")).toBe("dev");
  });

  it("keeps the current workbench when the folder has no tag", () => {
    expect(resolveProjectOpenView("/home/me/other", {}, "montage")).toBe("montage");
    expect(resolveProjectOpenView("/home/me/other", {}, "chat")).toBe("chat");
    expect(resolveProjectOpenView(null, { "": "dev" }, "chat")).toBe("chat");
  });

  it("ignores a stored module that is not a real one", () => {
    expect(resolveProjectOpenView("/home/me/app", { "/home/me/app": "nope" }, "chat")).toBe(
      "chat",
    );
  });
});

describe("viewForCreatedChat", () => {
  it("sends plain New chat to Tchat, even from Code", () => {
    expect(viewForCreatedChat("dev")).toBe("chat");
    expect(viewForCreatedChat("montage")).toBe("chat");
    expect(viewForCreatedChat("chat")).toBe("chat");
  });

  it("keeps the workbench for its own entry points", () => {
    expect(viewForCreatedChat("dev", { keepWorkbench: true, isWorkbench: true })).toBe("dev");
    expect(viewForCreatedChat("notes", { keepWorkbench: true, isWorkbench: true })).toBe(
      "notes",
    );
  });

  it("never invents a workbench when the current view is not one", () => {
    expect(viewForCreatedChat("chat", { keepWorkbench: true, isWorkbench: false })).toBe("chat");
    expect(viewForCreatedChat("settings", { keepWorkbench: true, isWorkbench: false })).toBe(
      "chat",
    );
  });
});
