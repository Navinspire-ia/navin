import { afterEach, describe, expect, it, vi } from "vitest";
import { extensionPackageBrowser, saveExtensionPackage } from "./browser-extension";

vi.mock("./notification-bus", () => ({ publishNotification: vi.fn() }));

afterEach(() => vi.unstubAllGlobals());

describe("Edge download compatibility", () => {
  it("uses the Edge package when the server has it", () => {
    expect(extensionPackageBrowser("edge", { chrome: true, edge: true })).toBe("edge");
  });
  it("uses the compatible Chromium package with older servers", () => {
    expect(extensionPackageBrowser("edge", { chrome: true, firefox: true })).toBe("chrome");
    expect(extensionPackageBrowser("edge", { chrome: true, edge: false })).toBe("chrome");
  });
  it("does not advertise a missing package or reuse Chromium for Firefox", () => {
    expect(extensionPackageBrowser("edge", { chrome: false })).toBeNull();
    expect(extensionPackageBrowser("edge")).toBeNull();
    expect(extensionPackageBrowser("firefox", { chrome: true })).toBeNull();
  });
});

describe("extension packages in Tauri", () => {
  it.each(["/home/user/Downloads/chrome.zip", "/Users/user/Downloads/chrome.zip", "C:\\Users\\user\\Downloads\\chrome.zip"])(
    "saves the original ZIP bytes through the native dialog: %s", async path => {
      const invoke = vi.fn().mockResolvedValue(path);
      vi.stubGlobal("window", { __TAURI__: { core: { invoke } } });
      await saveExtensionPackage({ name: "chrome.zip", mime: "application/zip", data: "UEsDBAD/" });
      expect(invoke).toHaveBeenCalledWith("save_bytes", { filename: "chrome.zip", contents: [80, 75, 3, 4, 0, 255] });
    },
  );

  it("respects cancellation without trying a WebView blob download", async () => {
    vi.stubGlobal("window", { __TAURI__: { core: { invoke: vi.fn().mockResolvedValue(false) } } });
    const createElement = vi.fn();
    vi.stubGlobal("document", { createElement });
    await saveExtensionPackage({ name: "firefox.zip", mime: "application/zip", data: "UEs=" });
    expect(createElement).not.toHaveBeenCalled();
  });
});
