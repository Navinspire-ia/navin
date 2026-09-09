// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  currentPageLocation,
  gatewayHttpBase,
  gatewayOrigin,
  isDesktopAppHttpHost,
  isDesktopAppUrl,
  isDesktopShell,
  isInternalDesktopUrl,
  isNavinShellUrl,
  isTauriAppHost,
  isTauriCustomScheme,
  resolveGatewayWsUrl,
  resolveRuntimeProfile,
  tauriInvoke,
  tauriIpcAvailable,
  tauriOpenUrl,
  type PageLocation,
} from "./desktop";

function page(
  port: string,
  hostname = "127.0.0.1",
  protocol = "http:",
): PageLocation {
  return {
    port,
    hostname,
    protocol,
    host: port ? `${hostname}:${port}` : hostname,
  };
}

describe("resolveGatewayWsUrl", () => {
  it("derives from the page origin in a plain browser", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/ws",
      token: "tok",
      location: page("8765"),
    });
    expect(url).toBe("ws://127.0.0.1:8765/ws?token=tok");
  });

  it("derives from the page origin in the desktop shell, whatever the port", () => {
    // The Tauri webview is navigated to http://127.0.0.1:<port>, so the page
    // origin is the gateway. A dynamic port must need no extra plumbing.
    for (const port of ["8766", "8767", "18999"]) {
      const url = resolveGatewayWsUrl({
        wsPath: "/",
        token: "tok",
        location: page(port),
      });
      expect(url).toBe(`ws://127.0.0.1:${port}/?token=tok`);
    }
  });

  it("uses wss on an https page", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      location: page("", "example.test", "https:"),
    });
    expect(url).toBe("wss://example.test/?token=tok");
  });

  it("prefers the address the gateway advertises for itself", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      advertisedWsUrl: "ws://127.0.0.1:8766/",
      location: page("8080"),
    });
    expect(url).toBe("ws://127.0.0.1:8766/?token=tok");
  });

  it("joins the token with & when the advertised url already has a query", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      advertisedWsUrl: "ws://127.0.0.1:8766/?x=1",
      location: page("8080"),
    });
    expect(url).toBe("ws://127.0.0.1:8766/?x=1&token=tok");
  });

  it("accepts the navin-host bridge scheme", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      advertisedWsUrl: "navin-host://gateway/ws",
      location: page("8766"),
    });
    expect(url).toBe("navin-host://gateway/ws?token=tok");
  });

  it("repairs a wildcard bind address into the page hostname", () => {
    // A gateway bound to every interface advertises its bind address. That is
    // not a destination: connecting to 0.0.0.0 fails on Windows and macOS.
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      advertisedWsUrl: "ws://0.0.0.0:8766/ws",
      location: page("8766", "192.168.1.20"),
    });
    expect(url).toBe("ws://192.168.1.20:8766/ws?token=tok");
  });

  it("repairs an IPv6 wildcard bind address and brackets the replacement", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      advertisedWsUrl: "ws://[::]:8766/ws",
      location: page("8766", "::1"),
    });
    expect(url).toBe("ws://[::1]:8766/ws?token=tok");
  });

  it("routes through the Vite proxy for a dev bundle on :5173", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      advertisedWsUrl: "ws://127.0.0.1:8766/",
      devBundle: true,
      location: page("5173"),
    });
    expect(url).toBe("ws://127.0.0.1:5173/__navin_ws?token=tok");
  });

  it("keeps a non-root path behind the Vite proxy", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/ws",
      token: "tok",
      devBundle: true,
      location: page("5173"),
    });
    expect(url).toBe("ws://127.0.0.1:5173/__navin_ws/ws?token=tok");
  });

  it("does NOT use the Vite proxy for a packaged build served on :5173", () => {
    // Regression: the proxy branch keyed off the page port alone, so a
    // gateway configured on 5173 rewrote its socket to /__navin_ws, a path
    // only the dev server serves. The socket then never opened.
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      devBundle: false,
      location: page("5173"),
    });
    expect(url).toBe("ws://127.0.0.1:5173/?token=tok");
  });

  it("uses wss behind the Vite proxy on an https page", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      devBundle: true,
      location: page("5173", "tunnel.test", "https:"),
    });
    expect(url).toBe("wss://tunnel.test:5173/__navin_ws?token=tok");
  });

  it("brackets an IPv6 page hostname behind the Vite proxy", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      devBundle: true,
      location: page("5173", "::1"),
    });
    expect(url).toBe("ws://[::1]:5173/__navin_ws?token=tok");
  });

  it("falls back to loopback when there is no page", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/ws",
      token: "tok",
      location: null,
    });
    expect(url).toBe("ws://127.0.0.1:8765/ws?token=tok");
  });

  it("still honours an advertised url when there is no page", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/ws",
      token: "tok",
      advertisedWsUrl: "ws://10.0.0.5:9000/ws",
      location: null,
    });
    expect(url).toBe("ws://10.0.0.5:9000/ws?token=tok");
  });

  it("normalises a ws path that has no leading slash", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "ws",
      token: "tok",
      location: page("8766"),
    });
    expect(url).toBe("ws://127.0.0.1:8766/ws?token=tok");
  });

  it("percent-encodes the token", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "a b+c/d=",
      location: page("8766"),
    });
    expect(url).toBe("ws://127.0.0.1:8766/?token=a%20b%2Bc%2Fd%3D");
  });

  it("omits the token query when there is no token", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "",
      location: page("8766"),
    });
    expect(url).toBe("ws://127.0.0.1:8766/");
  });
});

describe("runtime profile contract", () => {
  it("selects each existing transport mode explicitly", () => {
    expect(resolveRuntimeProfile({
      devBundle: true,
      location: page("5173"),
      desktopShell: false,
    })).toEqual({
      name: "browser-vite",
      wsTransport: "vite-proxy",
      httpBase: "",
    });
    expect(resolveRuntimeProfile({
      location: page("8765"),
      desktopShell: false,
    }).name).toBe("browser-direct");
    expect(resolveRuntimeProfile({
      location: page("8766"),
      desktopShell: true,
    }).name).toBe("desktop-http");
    expect(resolveRuntimeProfile({
      advertisedWsUrl: "navin-host://gateway/ws",
      desktopShell: true,
    }).name).toBe("desktop-bridge");
  });

  it("lets callers lock a profile independently of ambient dev state", () => {
    const url = resolveGatewayWsUrl({
      wsPath: "/",
      token: "tok",
      advertisedWsUrl: "ws://127.0.0.1:8766/",
      devBundle: true,
      location: page("5173"),
      profile: {
        name: "browser-direct",
        wsTransport: "gateway-origin",
        httpBase: "",
      },
    });
    expect(url).toBe("ws://127.0.0.1:8766/?token=tok");
  });
});

describe("shell detection", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is false in a plain browser", () => {
    vi.stubGlobal("window", { location: page("8765") });
    expect(isDesktopShell()).toBe(false);
    expect(tauriIpcAvailable()).toBe(false);
    expect(tauriInvoke()).toBeNull();
    expect(tauriOpenUrl()).toBeNull();
  });

  it("is true when only __TAURI__.core.invoke is exposed", () => {
    const invoke = vi.fn();
    vi.stubGlobal("window", {
      location: page("8766"),
      __TAURI__: { core: { invoke } },
    });
    expect(isDesktopShell()).toBe(true);
    expect(tauriIpcAvailable()).toBe(true);
    expect(tauriInvoke()).toBe(invoke);
  });

  it("is true when only __TAURI_INTERNALS__ is exposed", () => {
    // withGlobalTauri can be off while the IPC bootstrap is still injected;
    // the folder picker used to be the only helper that saw this shell.
    vi.stubGlobal("window", {
      location: page("8766"),
      __TAURI_INTERNALS__: {},
    });
    expect(isDesktopShell()).toBe(true);
    expect(tauriIpcAvailable()).toBe(true);
    expect(tauriInvoke()).toBeNull();
  });

  it("is true for the embedded navinHost bridge", () => {
    vi.stubGlobal("window", { location: page("8766"), navinHost: {} });
    expect(isDesktopShell()).toBe(true);
    // navinHost is not Tauri IPC: a native command must not be attempted.
    expect(tauriIpcAvailable()).toBe(false);
  });

  it("exposes the native link opener when granted", () => {
    const openUrl = vi.fn();
    vi.stubGlobal("window", {
      location: page("8766"),
      __TAURI__: { opener: { openUrl } },
    });
    expect(tauriOpenUrl()).toBe(openUrl);
    expect(isDesktopShell()).toBe(false);
  });
});

describe("gateway base", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps gateway HTTP calls relative", () => {
    expect(gatewayHttpBase()).toBe("");
  });

  it("reads the live page location", () => {
    vi.stubGlobal("window", { location: page("8766") });
    expect(currentPageLocation()).toEqual(page("8766"));
    expect(gatewayOrigin()).toBe("http://127.0.0.1:8766");
  });

  it("falls back to loopback with no page", () => {
    vi.stubGlobal("window", undefined);
    expect(currentPageLocation()).toBeNull();
    expect(gatewayOrigin()).toBe("http://127.0.0.1:8765");
  });
});

describe("tauri custom protocol hosts", () => {
  it("recognizes the in-webview hosts and rejects the public web", () => {
    expect(isTauriAppHost("tauri.localhost")).toBe(true);
    expect(isTauriAppHost("ipc.localhost")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/code")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/new")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/chat/websocket%3A1")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/crm")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/crm/contacts")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/tenders")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/tenders?notice=abc")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/tenders?pane=tenders")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/career")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/career?job=abc")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/trading")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/trading?chat=websocket:1")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/leads")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/leads?lead=abc")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/marketing")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/marketing?chat=websocket:1")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/tools")).toBe(true);
    expect(isInternalDesktopUrl("http://tauri.localhost/#/notes")).toBe(true);
    expect(isInternalDesktopUrl("https://navin.live")).toBe(false);
    expect(isInternalDesktopUrl("https://ted.europa.eu/en/notice/-/detail/123")).toBe(false);
    expect(isInternalDesktopUrl("http://127.0.0.1:8766/#/code")).toBe(false);
    expect(isDesktopAppHttpHost("127.0.0.1")).toBe(true);
    expect(isDesktopAppHttpHost("tauri.localhost")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/new")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/chat/websocket%3A1")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/crm")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/crm/contacts")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/new")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/chat/websocket%3A1")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/crm")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/crm/contacts")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/new")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/chat/websocket%3A1")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/crm")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/crm/contacts")).toBe(true);
    expect(isInternalDesktopUrl("https://tauri.localhost/#/new")).toBe(true);
    expect(isInternalDesktopUrl("https://tauri.localhost/#/crm")).toBe(true);
    expect(isInternalDesktopUrl("tauri://localhost/#/crm/contacts")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/tenders?notice=abc")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/tenders?pane=tenders")).toBe(true);
    expect(isDesktopAppUrl("http://tauri.localhost/#/tenders")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/career?job=abc")).toBe(true);
    expect(isDesktopAppUrl("http://tauri.localhost/#/career")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/trading?chat=websocket:1")).toBe(true);
    expect(isDesktopAppUrl("http://tauri.localhost/#/trading")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/leads?lead=abc")).toBe(true);
    expect(isDesktopAppUrl("http://tauri.localhost/#/leads")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/leads")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/leads?lead=abc")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/leads?pane=book")).toBe(true);
    expect(isInternalDesktopUrl("https://tauri.localhost/#/leads")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/career")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/career?job=abc")).toBe(true);
    expect(isInternalDesktopUrl("https://tauri.localhost/#/career")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/marketing")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/marketing?chat=websocket:1")).toBe(true);
    expect(isInternalDesktopUrl("https://tauri.localhost/#/marketing")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/tenders")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/tenders?notice=abc")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/tenders?pane=tenders")).toBe(true);
    expect(isInternalDesktopUrl("https://tauri.localhost/#/tenders")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/trading")).toBe(true);
    expect(isDesktopAppUrl("https://tauri.localhost/#/trading?chat=websocket:1")).toBe(true);
    expect(isInternalDesktopUrl("https://tauri.localhost/#/trading")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/marketing")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/marketing?chat=websocket:1")).toBe(true);
    expect(isDesktopAppUrl("http://tauri.localhost/#/marketing")).toBe(true);
    expect(isDesktopAppUrl("http://tauri.localhost/#/tools")).toBe(true);
    expect(isDesktopAppUrl("http://127.0.0.1:8766/#/notes")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/career")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/trading")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/trading?chat=websocket:1")).toBe(true);
    expect(isInternalDesktopUrl("tauri://localhost/#/trading")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/leads")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/marketing")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/marketing?chat=websocket:1")).toBe(true);
    expect(isInternalDesktopUrl("tauri://localhost/#/marketing")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/tenders")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/tenders?notice=abc")).toBe(true);
    expect(isDesktopAppUrl("tauri://localhost/#/tenders?pane=tenders")).toBe(true);
    expect(isInternalDesktopUrl("tauri://localhost/#/tenders")).toBe(true);
    expect(isInternalDesktopUrl("tauri://localhost/#/tenders?notice=abc")).toBe(true);
    expect(isInternalDesktopUrl("tauri://localhost/#/tenders?pane=tenders")).toBe(true);
    expect(isTauriCustomScheme("tauri:")).toBe(true);
    expect(isTauriCustomScheme("tauri")).toBe(true);
    expect(isTauriCustomScheme("https:")).toBe(false);
    expect(isDesktopAppUrl("https://ted.europa.eu/en/notice/-/detail/123")).toBe(false);
    expect(isDesktopAppUrl("https://www.linkedin.com/jobs/view/4242")).toBe(false);
    expect(isDesktopAppUrl("https://recherche-entreprises.api.gouv.fr/docs/")).toBe(false);
    expect(isDesktopAppUrl("https://docs.apollo.io/")).toBe(false);
    expect(isDesktopAppUrl("https://www.linkedin.com/company/navin")).toBe(false);
  });
});

describe("isNavinShellUrl", () => {
  it("keeps the gateway and tauri splash in the WebView", () => {
    expect(isNavinShellUrl("http://127.0.0.1:8766/#/code", "http://127.0.0.1:8766/#/code")).toBe(
      true,
    );
    expect(isNavinShellUrl("http://tauri.localhost/", "http://tauri.localhost/")).toBe(true);
    expect(isNavinShellUrl("tauri://localhost/#/new", "")).toBe(true);
  });

  it("sends a project preview port to the OS browser", () => {
    expect(isNavinShellUrl("http://127.0.0.1:5176/", "http://127.0.0.1:8766/#/code")).toBe(false);
    expect(isNavinShellUrl("http://localhost:5175/", "http://127.0.0.1:8766/#/code")).toBe(false);
    expect(isNavinShellUrl("http://127.0.0.1:3000/", "")).toBe(false);
  });

  it("treats the current page origin as the editor even on a dynamic port", () => {
    expect(isNavinShellUrl("http://127.0.0.1:8770/", "http://127.0.0.1:8770/#/code")).toBe(true);
  });
});
