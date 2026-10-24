/*
 * Navin motion time controller.
 *
 * Injected before any page script when Navin renders a composition. It turns
 * the page clock into a virtual clock that only moves when the renderer seeks,
 * so every frame is a pure function of its timestamp:
 *   - performance.now(), Date.now() and new Date() read the virtual clock;
 *   - requestAnimationFrame callbacks are queued and flushed once per seek;
 *   - Math.random is a seeded PRNG (same particles on every render).
 * Timers (setTimeout / setInterval) stay real so loading code keeps working;
 * templates must not animate with them.
 *
 * Copyright (c) 2026-present Navinspire IA. SPDX-License-Identifier: AGPL-3.0-only
 */
(function () {
  'use strict';
  if (window.__navinClock) return;
  var cfg = window.__NAVIN_RENDER__ || null;
  var clock = { now: 0, rendering: !!cfg, frame: 0 };
  window.__navinClock = clock;
  if (!cfg) return;

  var seed = (cfg.seed | 0) || 1337;
  Math.random = function () {
    seed = (seed + 0x6d2b79f5) | 0;
    var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  var EPOCH = Date.UTC(2026, 0, 1, 12, 0, 0);
  var RealDate = Date;
  function NavinDate() {
    var args = Array.prototype.slice.call(arguments);
    if (!(this instanceof NavinDate)) {
      return new RealDate(EPOCH + clock.now).toString();
    }
    if (args.length === 0) return new RealDate(EPOCH + clock.now);
    return new (Function.prototype.bind.apply(RealDate, [null].concat(args)))();
  }
  NavinDate.prototype = RealDate.prototype;
  NavinDate.now = function () { return EPOCH + clock.now; };
  NavinDate.parse = RealDate.parse;
  NavinDate.UTC = RealDate.UTC;
  window.Date = NavinDate;

  try {
    Object.defineProperty(window.performance, 'now', {
      configurable: true,
      value: function () { return clock.now; },
    });
  } catch (err) {
    window.performance.now = function () { return clock.now; };
  }

  // The real frame callback stays reachable: the runtime waits for real
  // compositor frames so WebGL and video pixels are presented before capture.
  clock.realRaf = window.requestAnimationFrame.bind(window);

  var queue = [];
  var nextId = 1;
  window.requestAnimationFrame = function (cb) {
    var id = nextId++;
    queue.push({ id: id, cb: cb });
    return id;
  };
  window.cancelAnimationFrame = function (id) {
    for (var i = 0; i < queue.length; i++) {
      if (queue[i].id === id) { queue.splice(i, 1); return; }
    }
  };

  clock.set = function (ms) {
    clock.now = ms;
  };
  clock.flush = function () {
    var batch = queue;
    queue = [];
    for (var i = 0; i < batch.length; i++) {
      try { batch[i].cb(clock.now); } catch (err) { console.error(err); }
    }
  };
})();
