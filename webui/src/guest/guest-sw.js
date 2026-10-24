// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only
// Shared-session guest page worker. Two jobs, no caching:
// - notifications (mobile browsers need a service worker) and back to the page on tap;
// - the host's shared site: every request of the site frame (an iframe of the
//   guest page) goes to the page, which tunnels it to the host through the
//   encrypted share and hands the answer back. The page's own files pass by.
/* global self, URL, fetch, MessageChannel, setTimeout, clearTimeout, Response */
const SITE_TIMEOUT_MS = 90_000;
const NULL_BODY = new Set([101, 103, 204, 205, 304]);
const REDIRECTS = new Set([301, 302, 303, 307, 308]);
// Client ids, learnt as requests come: the guest page's own, and the site frame's.
const pageClients = new Set();
const siteClients = new Set();
let owner = null;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const page = windows.find((w) => w.url.includes("/s/"));
      return page ? page.focus() : undefined;
    }),
  );
});

self.addEventListener("message", (event) => {
  // The guest page that shows the site (the one with the share's key).
  if (event.data && event.data.type === "site-owner" && event.source) owner = event.source.id;
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (new URL(request.url).origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    // The guest page itself is a top-level document; the site is its iframe.
    if (request.destination === "iframe") {
      if (event.resultingClientId) siteClients.add(event.resultingClientId);
      event.respondWith(tunnel(request));
    }
    return;
  }
  const id = event.clientId;
  if (!id || pageClients.has(id)) return;
  if (siteClients.has(id)) {
    event.respondWith(tunnel(request));
    return;
  }
  // Not seen yet (or this worker restarted): ask what kind of client it is.
  event.respondWith(
    self.clients.get(id).then((client) => {
      if (client && isSiteClient(client)) {
        siteClients.add(id);
        return tunnel(request);
      }
      pageClients.add(id);
      return fetch(request);
    }),
  );
});

function isSiteClient(client) {
  if (client.type === "window") return client.frameType === "nested";
  // A worker started by the site; the guest page's own live under /assets/.
  return !new URL(client.url).pathname.startsWith("/assets/");
}

async function tunnelPage() {
  const windows = await self.clients.matchAll({ type: "window" });
  const pages = windows.filter((w) => w.frameType === "top-level" && new URL(w.url).pathname.startsWith("/s/"));
  // matchAll lists the most recently focused first.
  return pages.find((w) => w.id === owner) || pages[0] || null;
}

async function tunnel(request) {
  const page = await tunnelPage();
  if (!page) return failure(503, "");
  const url = new URL(request.url);
  const body = request.method === "GET" || request.method === "HEAD" ? null : await request.arrayBuffer();
  const headers = [];
  request.headers.forEach((value, name) => headers.push([name, value]));
  const channel = new MessageChannel();
  const answer = new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ error: "timeout" }), SITE_TIMEOUT_MS);
    channel.port1.onmessage = (event) => {
      clearTimeout(timer);
      resolve(event.data);
    };
  });
  const message = { type: "site-fetch", method: request.method, path: url.pathname + url.search, headers, body };
  page.postMessage(message, body ? [channel.port2, body] : [channel.port2]);
  const res = await answer;
  if (!res || res.error) return failure(res && res.error === "timeout" ? 504 : 502, (res && res.message) || "");
  const location = res.headers.find(([name]) => name === "location");
  if (REDIRECTS.has(res.status) && location) {
    return Response.redirect(new URL(location[1], self.location.origin).href, res.status);
  }
  const status = res.status >= 200 && res.status <= 599 ? res.status : 502;
  const empty = NULL_BODY.has(status) || request.method === "HEAD";
  return new Response(empty ? null : res.body, { status, headers: res.headers.filter(([name]) => name !== "location") });
}

function failure(status, message) {
  const text = message || "The shared site is not reachable right now.";
  const page = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<body style="margin:0;display:grid;place-items:center;min-height:100vh;font:14px system-ui,sans-serif;color:#6b7280;text-align:center;padding:24px">
<p>${text.replace(/[<>&"]/g, "")}</p></body>`;
  return new Response(page, { status, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}
