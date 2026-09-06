"""Instrumented preview: capture script + telemetry ring buffer.

The Dev workbench preview loads user apps through a local injection proxy
(``preview_proxy.py``). The proxy inserts ``PROBE_JS`` into every HTML
response; the probe captures console output, uncaught errors, and failed
network calls, then posts them back to the proxy on the same origin
(``/__navin_probe__/telemetry``), which records them here.

The gateway exposes the recorded entries (token-protected) so the UI can show
a live console panel and seed the chat composer with a digest the agent can
act on.

The probe also answers ``postMessage`` commands from the workbench (hard
reload, cookie / cache clearing, in-page screenshot) and implements the design
mode: on ``pick-start`` it outlines the element under the pointer with the
component that rendered it and, on click, posts a description of that element
back to the parent, which turns it into an instruction for the agent.
"""

from __future__ import annotations

import itertools
import json
import threading
from collections import deque
from typing import Any

# Same-origin paths handled by the injection proxy itself.
PROBE_PATH_PREFIX = "/__navin_probe__"
PROBE_SCRIPT_PATH = f"{PROBE_PATH_PREFIX}/probe.js"
PROBE_TELEMETRY_PATH = f"{PROBE_PATH_PREFIX}/telemetry"

MAX_ENTRIES_PER_PORT = 500
MAX_TEXT_CHARS = 2000
MAX_BATCH_ITEMS = 100

_ALLOWED_LEVELS = frozenset(
    {"log", "info", "warn", "error", "debug", "pageerror", "network"}
)

# Kept dependency-free and ES5-adjacent so it runs in any preview page.
PROBE_JS = r"""(function () {
  if (window.__navinProbeInstalled) return;
  window.__navinProbeInstalled = true;
  var queue = [];
  var MAX_QUEUE = 200;
  var MAX_TEXT = 2000;

  function fmt(value) {
    if (value === undefined) return "undefined";
    if (value === null) return "null";
    if (typeof value === "string") return value;
    if (value instanceof Error) {
      return String(value.message || value) + (value.stack ? "\n" + value.stack : "");
    }
    try {
      return JSON.stringify(value);
    } catch (e) {
      return String(value);
    }
  }

  function push(entry) {
    if (queue.length >= MAX_QUEUE) queue.shift();
    if (entry.text && entry.text.length > MAX_TEXT) {
      entry.text = entry.text.slice(0, MAX_TEXT) + "\u2026";
    }
    entry.ts = Date.now();
    queue.push(entry);
  }

  function flush() {
    if (!queue.length) return;
    var batch = queue.splice(0, queue.length);
    try {
      fetch("/__navin_probe__/telemetry", {
        method: "POST",
        headers: { "Content-Type": "text/plain" },
        body: JSON.stringify({ items: batch }),
        keepalive: true,
      }).catch(function () {});
    } catch (e) {
      /* never break the host page */
    }
  }

  ["log", "info", "warn", "error", "debug"].forEach(function (level) {
    var original = console[level];
    console[level] = function () {
      var parts = [];
      for (var i = 0; i < arguments.length; i++) parts.push(fmt(arguments[i]));
      push({ level: level, text: parts.join(" ") });
      if (original) original.apply(console, arguments);
    };
  });

  window.addEventListener("error", function (event) {
    var text = event.message || "Unhandled error";
    if (event.filename) {
      text += " (" + event.filename + ":" + (event.lineno || 0) + ")";
    }
    if (event.error && event.error.stack) text += "\n" + event.error.stack;
    push({ level: "pageerror", text: text });
    flush();
  });

  window.addEventListener("unhandledrejection", function (event) {
    push({ level: "pageerror", text: "Unhandled rejection: " + fmt(event.reason) });
    flush();
  });

  var originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      if (url.indexOf("/__navin_probe__/") !== -1) {
        return originalFetch.apply(window, arguments);
      }
      var method = (init && init.method) || (input && input.method) || "GET";
      var startedAt = Date.now();
      return originalFetch.apply(window, arguments).then(
        function (response) {
          if (!response.ok) {
            push({
              level: "network",
              text: method + " " + url + " -> HTTP " + response.status,
              url: url,
              method: method,
              status: response.status,
              durationMs: Date.now() - startedAt,
            });
          }
          return response;
        },
        function (error) {
          push({
            level: "network",
            text: method + " " + url + " failed: " + fmt(error),
            url: url,
            method: method,
            status: 0,
            durationMs: Date.now() - startedAt,
          });
          throw error;
        }
      );
    };
  }

  var OriginalXHROpen = XMLHttpRequest.prototype.open;
  var OriginalXHRSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__navinProbe = { method: method, url: String(url || "") };
    return OriginalXHROpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    var meta = this.__navinProbe;
    if (meta && meta.url.indexOf("/__navin_probe__/") === -1) {
      var startedAt = Date.now();
      this.addEventListener("loadend", function () {
        if (this.status === 0 || this.status >= 400) {
          push({
            level: "network",
            text: meta.method + " " + meta.url + " -> HTTP " + this.status,
            url: meta.url,
            method: meta.method,
            status: this.status,
            durationMs: Date.now() - startedAt,
          });
        }
      });
    }
    return OriginalXHRSend.apply(this, arguments);
  };

  function isTrustedPreviewHost(origin) {
    try {
      var host = new URL(origin).hostname.toLowerCase();
      return host === "127.0.0.1" || host === "localhost" || host === "[::1]";
    } catch (e) {
      return false;
    }
  }

  function replyTo(event, id, ok, extra) {
    var payload = { source: "navin-preview-result", id: id, ok: ok };
    if (extra) {
      for (var key in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, key)) payload[key] = extra[key];
      }
    }
    try {
      event.source.postMessage(payload, event.origin);
    } catch (e) {}
  }

  function clearPreviewCookies() {
    var parts = (document.cookie || "").split(";");
    for (var i = 0; i < parts.length; i++) {
      var name = parts[i].split("=")[0].trim();
      if (!name) continue;
      document.cookie = name + "=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";
    }
  }

  function clearPreviewCache(done) {
    var finish = function () { done(); };
    try {
      if (window.caches && caches.keys) {
        caches.keys().then(function (keys) {
          return Promise.all(keys.map(function (key) { return caches.delete(key); }));
        }).then(finish, finish);
        return;
      }
    } catch (e) {}
    finish();
  }

  function captureScreenshot(done) {
    try {
      var w = window.innerWidth || document.documentElement.clientWidth;
      var h = window.innerHeight || document.documentElement.clientHeight;
      if (!w || !h) {
        done(null, "empty viewport");
        return;
      }
      var canvas = document.createElement("canvas");
      var scale = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.floor(w * scale);
      canvas.height = Math.floor(h * scale);
      var ctx = canvas.getContext("2d");
      if (!ctx) {
        done(null, "no canvas");
        return;
      }
      var bg = "#ffffff";
      try {
        bg = getComputedStyle(document.body).backgroundColor || bg;
      } catch (e) {}
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      var clone = document.documentElement.cloneNode(true);
      var junk = clone.querySelectorAll("script, iframe");
      for (var i = 0; i < junk.length; i++) {
        if (junk[i].parentNode) junk[i].parentNode.removeChild(junk[i]);
      }
      var serialized = new XMLSerializer().serializeToString(clone);
      var svg =
        '<svg xmlns="http://www.w3.org/2000/svg" width="' + w + '" height="' + h + '">' +
        '<foreignObject width="100%" height="100%">' + serialized + "</foreignObject></svg>";
      var img = new Image();
      img.onload = function () {
        try {
          ctx.setTransform(scale, 0, 0, scale, 0, 0);
          ctx.drawImage(img, 0, 0, w, h);
          done(canvas.toDataURL("image/png"), null);
        } catch (err) {
          done(null, String(err));
        }
      };
      img.onerror = function () { done(null, "svg render failed"); };
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    } catch (err) {
      done(null, String(err));
    }
  }

  // ---- Design mode ---------------------------------------------------------
  // The workbench turns it on with "pick-start". Hovering outlines the element
  // under the pointer with the component that rendered it, a click describes
  // that element to the parent window (which opens the prompt for the agent),
  // Escape hands control back. "pick-resume" clears a selection, "pick-stop"
  // removes everything again. Nothing here runs unless the parent asked.
  var pick = {
    active: false,
    frozen: false,
    hover: null,
    parentWin: null,
    parentOrigin: "*",
    box: null,
    label: null,
    name: null,
    hint: null,
    style: null
  };
  var PICK_SWALLOWED = ["mousedown", "mouseup", "pointerdown", "pointerup", "dblclick", "contextmenu"];
  var PICK_IDLE_BORDER = "#3b82f6";
  var PICK_IDLE_FILL = "rgba(59, 130, 246, 0.10)";
  var PICK_SELECTED_BORDER = "#1d4ed8";
  var PICK_SELECTED_FILL = "rgba(37, 99, 235, 0.16)";

  function fiberName(type) {
    if (!type) return "";
    if (typeof type === "function") return type.displayName || type.name || "";
    if (typeof type === "object") {
      if (type.displayName) return type.displayName;
      if (type.render) return fiberName(type.render);
      if (type.type) return fiberName(type.type);
    }
    return "";
  }

  function isNoiseName(name) {
    return (
      !name ||
      name === "Fragment" ||
      name === "Suspense" ||
      name === "Profiler" ||
      /Provider$|Consumer$|Boundary$|^Context$/.test(name)
    );
  }

  function reactFiberOf(el) {
    var node = el;
    while (node && node.nodeType === 1) {
      var keys = Object.keys(node);
      for (var i = 0; i < keys.length; i++) {
        if (keys[i].indexOf("__reactFiber$") === 0 || keys[i].indexOf("__reactInternalInstance$") === 0) {
          return node[keys[i]];
        }
      }
      node = node.parentElement;
    }
    return null;
  }

  function sourceFromStack(stack) {
    var lines = String(stack || "").split("\n");
    for (var i = 0; i < lines.length; i++) {
      var m = /((?:https?:\/\/[^\s()]*?|\/[^\s():]*?)\.(?:tsx|jsx|ts|js|mjs|vue|svelte))(?:\?[^\s():]*)?:(\d+):(\d+)/.exec(lines[i]);
      if (!m) continue;
      var url = m[1];
      if (/node_modules|\.vite\/deps|react-dom|jsx-dev-runtime|jsx-runtime|\/react\/|\/react\.js/.test(url)) continue;
      var file = url;
      try { file = new URL(url).pathname; } catch (e) {}
      return { file: file, line: parseInt(m[2], 10) || 0 };
    }
    return null;
  }

  function reactInfo(el) {
    var fiber = reactFiberOf(el);
    if (!fiber) return null;
    var names = [];
    var f = fiber["return"];
    while (f && names.length < 6) {
      var n = fiberName(f.type);
      if (!isNoiseName(n) && names.indexOf(n) === -1) names.push(n);
      f = f["return"];
    }
    var ownerName = "";
    var owner = fiber._debugOwner;
    if (owner) ownerName = owner.type ? fiberName(owner.type) : (owner.name || "");
    var source = null;
    if (fiber._debugSource && fiber._debugSource.fileName) {
      source = { file: String(fiber._debugSource.fileName), line: fiber._debugSource.lineNumber || 0 };
    } else if (fiber._debugStack) {
      source = sourceFromStack(fiber._debugStack.stack || fiber._debugStack);
    }
    return {
      framework: "react",
      name: !isNoiseName(ownerName) ? ownerName : (names[0] || ""),
      ancestors: names,
      source: source
    };
  }

  function vueInfo(el) {
    var node = el;
    while (node && node.nodeType === 1) {
      var inst = node.__vueParentComponent;
      if (inst) {
        var names = [];
        var cur = inst;
        while (cur && names.length < 6) {
          var t = cur.type || {};
          var n = t.name || t.__name || (t.__file ? String(t.__file).split("/").pop().replace(/\.vue$/, "") : "");
          if (n && names.indexOf(n) === -1) names.push(n);
          cur = cur.parent;
        }
        var file = inst.type && inst.type.__file ? String(inst.type.__file) : "";
        return { framework: "vue", name: names[0] || "", ancestors: names, source: file ? { file: file, line: 0 } : null };
      }
      var vm = node.__vue__;
      if (vm && vm.$options) {
        var opts = vm.$options;
        var name2 = opts.name || (opts.__file ? String(opts.__file).split("/").pop().replace(/\.vue$/, "") : "");
        return { framework: "vue", name: name2, ancestors: name2 ? [name2] : [], source: opts.__file ? { file: String(opts.__file), line: 0 } : null };
      }
      node = node.parentElement;
    }
    return null;
  }

  function componentInfo(el) {
    try {
      return reactInfo(el) || vueInfo(el);
    } catch (e) {
      return null;
    }
  }

  function cssEscape(value) {
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return String(value).replace(/([^\w-])/g, "\\$1");
  }

  function isUniqueSelector(selector) {
    try {
      return document.querySelectorAll(selector).length === 1;
    } catch (e) {
      return false;
    }
  }

  function buildSelector(el) {
    var parts = [];
    var node = el;
    var depth = 0;
    while (node && node.nodeType === 1 && depth < 6) {
      var tag = node.tagName.toLowerCase();
      if (tag === "html" || tag === "body") {
        parts.unshift(tag);
        break;
      }
      if (node.id) {
        parts.unshift("#" + cssEscape(node.id));
        break;
      }
      var segment = tag;
      var testId = node.getAttribute && node.getAttribute("data-testid");
      if (testId) {
        segment += '[data-testid="' + testId.replace(/"/g, '\\"') + '"]';
      } else if (node.parentElement) {
        var kids = node.parentElement.children;
        var sameTag = 0;
        var index = 0;
        for (var i = 0; i < kids.length; i++) {
          if (kids[i].tagName === node.tagName) {
            sameTag++;
            if (kids[i] === node) index = sameTag;
          }
        }
        if (sameTag > 1) segment += ":nth-of-type(" + index + ")";
      }
      parts.unshift(segment);
      if (isUniqueSelector(parts.join(" > "))) break;
      node = node.parentElement;
      depth++;
    }
    return parts.join(" > ");
  }

  function elementText(el) {
    var text = "";
    try {
      text = el.innerText || el.textContent || "";
    } catch (e) {}
    text = String(text).replace(/\s+/g, " ").trim();
    return text.length > 140 ? text.slice(0, 140) + "\u2026" : text;
  }

  function describeElement(el) {
    var rect = el.getBoundingClientRect();
    var info = componentInfo(el);
    var attrs = {};
    var keep = ["href", "src", "alt", "placeholder", "name", "type", "role", "aria-label", "title"];
    for (var i = 0; i < keep.length; i++) {
      var value = el.getAttribute && el.getAttribute(keep[i]);
      if (value) attrs[keep[i]] = String(value).slice(0, 200);
    }
    var className = typeof el.className === "string"
      ? el.className
      : (el.getAttribute && el.getAttribute("class")) || "";
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || "",
      classes: String(className).replace(/\s+/g, " ").trim().slice(0, 300),
      testId: (el.getAttribute && el.getAttribute("data-testid")) || "",
      text: elementText(el),
      selector: buildSelector(el),
      rect: {
        x: Math.round(rect.left),
        y: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height)
      },
      viewport: {
        width: window.innerWidth || document.documentElement.clientWidth || 0,
        height: window.innerHeight || document.documentElement.clientHeight || 0
      },
      component: info ? info.name : "",
      ancestors: info ? info.ancestors : [],
      framework: info ? info.framework : "",
      source: info && info.source ? info.source : null,
      attributes: attrs,
      url: String(location.href),
      title: String(document.title || "")
    };
  }

  function pickLabelFor(el) {
    var info = componentInfo(el);
    var tag = el.tagName.toLowerCase();
    if (info && info.name) return info.name + " \u00b7 " + tag;
    return el.id ? tag + "#" + el.id : tag;
  }

  function ensurePickOverlay() {
    if (pick.box) return;
    var box = document.createElement("div");
    box.setAttribute("data-navin-pick", "box");
    box.style.cssText =
      "position:fixed;left:0;top:0;width:0;height:0;pointer-events:none;z-index:2147483646;" +
      "border:2px solid " + PICK_IDLE_BORDER + ";background:" + PICK_IDLE_FILL + ";" +
      "border-radius:3px;box-sizing:border-box;display:none;";
    var label = document.createElement("div");
    label.setAttribute("data-navin-pick", "label");
    label.style.cssText =
      "position:fixed;left:0;top:0;pointer-events:none;z-index:2147483647;background:#2563eb;color:#fff;" +
      "font:600 11px/1.35 system-ui,-apple-system,'Segoe UI',sans-serif;padding:3px 7px;border-radius:6px;" +
      "white-space:nowrap;max-width:70vw;overflow:hidden;text-overflow:ellipsis;" +
      "box-shadow:0 2px 8px rgba(0,0,0,0.25);display:none;";
    var name = document.createElement("div");
    var hint = document.createElement("div");
    hint.style.cssText = "font-weight:400;opacity:0.85;";
    label.appendChild(name);
    label.appendChild(hint);
    var style = document.createElement("style");
    style.setAttribute("data-navin-pick", "style");
    style.textContent = "*{cursor:crosshair!important}";
    var host = document.body || document.documentElement;
    host.appendChild(box);
    host.appendChild(label);
    pick.box = box;
    pick.label = label;
    pick.name = name;
    pick.hint = hint;
    pick.style = style;
  }

  function hidePickOverlay() {
    if (!pick.box) return;
    pick.box.style.display = "none";
    pick.label.style.display = "none";
  }

  function positionPickOverlay(el) {
    var r = el.getBoundingClientRect();
    var box = pick.box;
    var label = pick.label;
    box.style.display = "block";
    box.style.left = r.left + "px";
    box.style.top = r.top + "px";
    box.style.width = Math.max(r.width, 0) + "px";
    box.style.height = Math.max(r.height, 0) + "px";
    pick.name.textContent = pickLabelFor(el);
    label.style.display = "block";
    var viewW = window.innerWidth || document.documentElement.clientWidth || 0;
    var viewH = window.innerHeight || document.documentElement.clientHeight || 0;
    var labelH = label.offsetHeight || 34;
    var labelW = label.offsetWidth || 160;
    var top = r.top - labelH - 4;
    if (top < 2) top = Math.min(r.bottom + 4, viewH - labelH - 2);
    var left = Math.max(2, Math.min(r.left, viewW - labelW - 2));
    label.style.left = left + "px";
    label.style.top = Math.max(2, top) + "px";
  }

  function isPickOverlayNode(el) {
    return !!(el && el.getAttribute && el.getAttribute("data-navin-pick"));
  }

  function pickTargetAt(x, y) {
    var el = document.elementFromPoint ? document.elementFromPoint(x, y) : null;
    if (!el || isPickOverlayNode(el)) return null;
    if (el === document.documentElement || el === document.body) return null;
    return el;
  }

  function notifyPickParent(extra) {
    if (!pick.parentWin) return;
    var payload = { source: "navin-preview-result" };
    for (var key in extra) {
      if (Object.prototype.hasOwnProperty.call(extra, key)) payload[key] = extra[key];
    }
    try {
      pick.parentWin.postMessage(payload, pick.parentOrigin);
    } catch (e) {}
  }

  function swallowPickEvent(event) {
    if (!pick.active) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.stopImmediatePropagation) event.stopImmediatePropagation();
  }

  function onPickMove(event) {
    if (!pick.active || pick.frozen) return;
    var el = pickTargetAt(event.clientX, event.clientY);
    if (!el) {
      pick.hover = null;
      hidePickOverlay();
      return;
    }
    pick.hover = el;
    positionPickOverlay(el);
  }

  function onPickClick(event) {
    if (!pick.active) return;
    swallowPickEvent(event);
    var el = pickTargetAt(event.clientX, event.clientY) || pick.hover;
    if (!el) return;
    // The outline stays on the chosen element while the parent shows its
    // prompt; another click simply retargets it.
    pick.frozen = true;
    pick.hover = el;
    positionPickOverlay(el);
    pick.box.style.borderColor = PICK_SELECTED_BORDER;
    pick.box.style.background = PICK_SELECTED_FILL;
    notifyPickParent({ event: "pick", element: describeElement(el) });
  }

  function onPickKey(event) {
    if (!pick.active) return;
    if (event.key === "Escape" || event.keyCode === 27) {
      swallowPickEvent(event);
      stopPicking();
      notifyPickParent({ event: "pick-cancel" });
    }
  }

  function onPickViewportChange() {
    if (pick.active && pick.hover) positionPickOverlay(pick.hover);
  }

  function resumePicking() {
    pick.frozen = false;
    pick.hover = null;
    if (pick.box) {
      pick.box.style.borderColor = PICK_IDLE_BORDER;
      pick.box.style.background = PICK_IDLE_FILL;
    }
    hidePickOverlay();
  }

  function startPicking(event, hint) {
    ensurePickOverlay();
    pick.parentWin = event.source;
    pick.parentOrigin = event.origin || "*";
    pick.hint.textContent = hint || "Click to select \u00b7 Esc to exit";
    if (pick.active) {
      resumePicking();
      return;
    }
    pick.active = true;
    resumePicking();
    document.addEventListener("mousemove", onPickMove, true);
    document.addEventListener("click", onPickClick, true);
    for (var i = 0; i < PICK_SWALLOWED.length; i++) {
      document.addEventListener(PICK_SWALLOWED[i], swallowPickEvent, true);
    }
    document.addEventListener("keydown", onPickKey, true);
    window.addEventListener("scroll", onPickViewportChange, true);
    window.addEventListener("resize", onPickViewportChange);
    var head = document.head || document.documentElement;
    if (head && !pick.style.parentNode) head.appendChild(pick.style);
  }

  function stopPicking() {
    if (!pick.active) return;
    pick.active = false;
    pick.frozen = false;
    pick.hover = null;
    document.removeEventListener("mousemove", onPickMove, true);
    document.removeEventListener("click", onPickClick, true);
    for (var i = 0; i < PICK_SWALLOWED.length; i++) {
      document.removeEventListener(PICK_SWALLOWED[i], swallowPickEvent, true);
    }
    document.removeEventListener("keydown", onPickKey, true);
    window.removeEventListener("scroll", onPickViewportChange, true);
    window.removeEventListener("resize", onPickViewportChange);
    hidePickOverlay();
    if (pick.style && pick.style.parentNode) pick.style.parentNode.removeChild(pick.style);
  }

  window.addEventListener("message", function (event) {
    var data = event.data;
    if (!data || data.source !== "navin-preview") return;
    if (!isTrustedPreviewHost(event.origin || "")) return;
    var id = data.id;
    var cmd = data.cmd;
    if (cmd === "pick-start") {
      try {
        startPicking(event, typeof data.hint === "string" ? data.hint : "");
        replyTo(event, id, true, { active: true });
      } catch (e) {
        replyTo(event, id, false, { error: String(e) });
      }
      return;
    }
    if (cmd === "pick-resume") {
      if (pick.active) resumePicking();
      replyTo(event, id, true, { active: pick.active });
      return;
    }
    if (cmd === "pick-stop") {
      stopPicking();
      replyTo(event, id, true, { active: false });
      return;
    }
    if (cmd === "hard-reload") {
      try { location.reload(); } catch (e) { replyTo(event, id, false, { error: String(e) }); }
      return;
    }
    if (cmd === "clear-cookies") {
      try {
        clearPreviewCookies();
        replyTo(event, id, true, null);
      } catch (e) {
        replyTo(event, id, false, { error: String(e) });
      }
      return;
    }
    if (cmd === "clear-cache") {
      clearPreviewCache(function () { replyTo(event, id, true, null); });
      return;
    }
    if (cmd === "screenshot") {
      captureScreenshot(function (dataUrl, error) {
        if (dataUrl) replyTo(event, id, true, { dataUrl: dataUrl });
        else replyTo(event, id, false, { error: error || "screenshot failed" });
      });
    }
  });

  setInterval(flush, 1500);
  window.addEventListener("pagehide", flush);
})();
"""


def _sanitize_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    level = raw.get("level")
    if level not in _ALLOWED_LEVELS:
        return None
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    entry: dict[str, Any] = {
        "level": level,
        "text": text[:MAX_TEXT_CHARS],
        "ts": raw.get("ts") if isinstance(raw.get("ts"), (int, float)) else None,
    }
    for key in ("url", "method"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            entry[key] = value[:500]
    for key in ("status", "durationMs"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            entry[key] = int(value)
    return entry


class TelemetryStore:
    """Thread-safe ring buffer of probe entries, keyed by target port."""

    def __init__(self, max_entries: int = MAX_ENTRIES_PER_PORT) -> None:
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: dict[int, deque[dict[str, Any]]] = {}
        self._counter = itertools.count(1)

    def record(self, port: int, items: Any) -> int:
        """Validate and store a probe batch. Returns entries accepted."""
        if not isinstance(items, list):
            return 0
        accepted = 0
        with self._lock:
            bucket = self._entries.setdefault(
                port, deque(maxlen=self._max_entries)
            )
            for raw in items[:MAX_BATCH_ITEMS]:
                entry = _sanitize_entry(raw)
                if entry is None:
                    continue
                entry["id"] = next(self._counter)
                bucket.append(entry)
                accepted += 1
        return accepted

    def record_payload(self, port: int, body: bytes) -> int:
        """Record a raw JSON POST body sent by the probe."""
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            return 0
        if not isinstance(payload, dict):
            return 0
        return self.record(port, payload.get("items"))

    def entries(
        self, port: int, *, after_id: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._lock:
            bucket = self._entries.get(port)
            if not bucket:
                return []
            selected = [e for e in bucket if e["id"] > after_id]
        return selected[-limit:]

    def clear(self, port: int) -> None:
        with self._lock:
            self._entries.pop(port, None)

    def counts(self, port: int) -> dict[str, int]:
        with self._lock:
            bucket = self._entries.get(port)
            snapshot = list(bucket) if bucket else []
        counts: dict[str, int] = {}
        for entry in snapshot:
            counts[entry["level"]] = counts.get(entry["level"], 0) + 1
        return counts

    def digest(self, port: int, *, max_lines: int = 30) -> str:
        """Human/agent-readable summary of recent problems on this preview."""
        entries = self.entries(port, limit=self._max_entries)
        problems = [
            e for e in entries if e["level"] in ("error", "pageerror", "network", "warn")
        ]
        if not problems:
            return ""
        lines = [
            f"Preview telemetry for http://localhost:{port} "
            f"({len(problems)} problem(s) captured):"
        ]
        for entry in problems[-max_lines:]:
            label = {
                "pageerror": "UNCAUGHT",
                "network": "NETWORK",
                "error": "CONSOLE.ERROR",
                "warn": "CONSOLE.WARN",
            }.get(entry["level"], entry["level"].upper())
            first_line = entry["text"].splitlines()[0][:300]
            lines.append(f"- [{label}] {first_line}")
        return "\n".join(lines)


TELEMETRY = TelemetryStore()


def inject_probe(html: bytes) -> bytes:
    """Insert the probe script tag into an HTML document.

    Injected early (end of ``<head>``) so console output produced during app
    bootstrap is captured too. Falls back to prepending when no head/body tag
    is found (fragment responses).
    """
    tag = f'<script src="{PROBE_SCRIPT_PATH}"></script>'.encode()
    lowered = html.lower()
    for marker in (b"</head>", b"</body>"):
        idx = lowered.find(marker)
        if idx != -1:
            return html[:idx] + tag + html[idx:]
    idx = lowered.find(b"<html")
    if idx != -1:
        end = lowered.find(b">", idx)
        if end != -1:
            return html[: end + 1] + tag + html[end + 1 :]
    return tag + html
