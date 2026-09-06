/* Navin WebUI service worker - cache the installable shell for offline reopen.
 *
 * Network-first for navigations and JS/CSS so a live gateway always wins.
 * Cache-first for static icons / manifest. API and WebSocket traffic are never
 * cached. Offline: serve the cached shell so the installed PWA still opens.
 */
const CACHE_NAME = "navin-webui-shell-v1";
const SHELL_URLS = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/logo/navin-mark.svg",
  "/logo/navin-mark-192.png",
  "/logo/navin-mark-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL_URLS))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE_NAME)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

function isShellAsset(url) {
  const { pathname } = url;
  if (SHELL_URLS.includes(pathname)) return true;
  if (pathname.startsWith("/logo/")) return true;
  if (pathname === "/manifest.webmanifest") return true;
  return false;
}

function shouldBypass(url) {
  const { pathname } = url;
  if (pathname.startsWith("/api/")) return true;
  if (pathname.startsWith("/webui/")) return true;
  if (pathname.startsWith("/auth/")) return true;
  return false;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (shouldBypass(url)) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
    return;
  }

  if (isShellAsset(url)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // App bundles (hashed assets): network-first with cache fallback.
  if (
    pathnameLooksLikeAsset(url.pathname)
    || request.destination === "script"
    || request.destination === "style"
    || request.destination === "worker"
  ) {
    event.respondWith(networkFirst(request));
  }
});

function pathnameLooksLikeAsset(pathname) {
  return /\.(?:js|css|mjs|wasm|map|woff2?|png|svg|webp|ico)$/i.test(pathname);
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const fresh = await fetch(request);
    if (fresh && fresh.ok) {
      void cache.put(request, fresh.clone());
    }
    return fresh;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    if (request.mode === "navigate") {
      const shell = await cache.match("/index.html");
      if (shell) return shell;
    }
    throw new Error("offline and no cache entry");
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;
  const fresh = await fetch(request);
  if (fresh && fresh.ok) {
    void cache.put(request, fresh.clone());
  }
  return fresh;
}
