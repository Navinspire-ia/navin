/*
 * Navin motion runtime (window.NavinMotion).
 *
 * Every template calls NavinMotion.mount({duration, setup}). The runtime:
 *   - applies the brand kit as CSS variables (--c-primary ... --font-heading)
 *     and registers brand font files with the FontFace API (no CDN);
 *   - waits for fonts, images and video layers, then exposes the render
 *     contract:
 *       window.__navinDuration  composition length in seconds
 *       window.__navinSeek(t)   async; paints the exact frame at t seconds
 *       window.__navinReady     true once the first frame is painted
 *       window.__navinError     set when setup failed
 *   - in render mode seeks GSAP timelines, Web Animations, <video> layers
 *     (awaiting 'seeked') and three.js renderers deterministically;
 *   - outside the renderer (a plain browser preview) plays in real time.
 *
 * Copyright (c) 2026-present Navinspire IA. SPDX-License-Identifier: AGPL-3.0-only
 */
(function () {
  'use strict';
  if (window.NavinMotion) return;

  var R = window.__NAVIN_RENDER__ || null;
  var clock = window.__navinClock || { now: 0, rendering: false, set: function () {}, flush: function () {} };
  var rendering = !!R;

  function queryJson(name) {
    try {
      var raw = new URLSearchParams(window.location.search).get(name);
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      return null;
    }
  }

  var DEFAULT_COLORS = {
    primary: '#6366F1',
    secondary: '#22D3EE',
    accent: '#F472B6',
    background: '#0A0B14',
    text: '#F5F7FF',
  };
  var SANS = '"Inter", "SF Pro Display", "Segoe UI", "Helvetica Neue", "Noto Sans", "DejaVu Sans", Arial, sans-serif';

  var props = window.__NAVIN_PROPS__ || queryJson('props') || {};
  var brandIn = window.__NAVIN_BRAND__ || queryJson('brand') || {};
  var canvasIn = (R && R.canvas) || queryJson('canvas') || {};

  var W = Math.round(Number(canvasIn.width) || window.innerWidth || 1920);
  var H = Math.round(Number(canvasIn.height) || window.innerHeight || 1080);
  var canvas = {
    width: W,
    height: H,
    fps: Number(canvasIn.fps) || 30,
    duration: Number(canvasIn.duration) || 0,
    transparent: !!canvasIn.transparent,
    glScale: Number(canvasIn.gl_scale) > 0 ? Math.min(1, Number(canvasIn.gl_scale)) : 1,
    portrait: H > W * 1.15,
    square: Math.abs(W - H) <= W * 0.15,
    landscape: W > H * 1.15,
  };

  function normalizeBrand(raw) {
    var colors = Object.assign({}, DEFAULT_COLORS, (raw && raw.colors) || {});
    Object.keys(colors).forEach(function (key) {
      if (!/^#?[0-9a-fA-F]{3,8}$/.test(String(colors[key] || '')) && !/^(rgb|hsl)a?\(/.test(String(colors[key]))) {
        colors[key] = DEFAULT_COLORS[key] || '#888888';
      } else if (/^[0-9a-fA-F]{3,8}$/.test(String(colors[key]))) {
        colors[key] = '#' + colors[key];
      }
    });
    var fonts = Object.assign({ heading: '', body: '' }, (raw && raw.fonts) || {});
    return {
      name: (raw && raw.name) || '',
      logo_url: (raw && raw.logo_url) || '',
      logo_dark_url: (raw && raw.logo_dark_url) || '',
      colors: colors,
      fonts: fonts,
      font_faces: (raw && raw.font_faces) || [],
      tagline: (raw && raw.tagline) || '',
      url: (raw && raw.url) || '',
    };
  }

  var brand = normalizeBrand(brandIn);

  function hexToRgb(hex) {
    var h = String(hex || '').replace('#', '');
    if (h.length === 3 || h.length === 4) h = h.split('').map(function (c) { return c + c; }).join('');
    var n = parseInt(h.slice(0, 6), 16);
    if (isNaN(n)) return { r: 128, g: 128, b: 128 };
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }
  function luminance(hex) {
    var c = hexToRgb(hex);
    var lin = [c.r, c.g, c.b].map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  }
  function rgba(hex, alpha) {
    var c = hexToRgb(hex);
    return 'rgba(' + c.r + ',' + c.g + ',' + c.b + ',' + alpha + ')';
  }
  function mix(a, b, k) {
    var x = hexToRgb(a);
    var y = hexToRgb(b);
    function ch(p, q) { return Math.round(p + (q - p) * k).toString(16).padStart(2, '0'); }
    return '#' + ch(x.r, y.r) + ch(x.g, y.g) + ch(x.b, y.b);
  }
  function onColor(hex) {
    return luminance(hex) > 0.42 ? '#0A0B14' : '#FFFFFF';
  }

  var NM = {
    props: props,
    brand: brand,
    canvas: canvas,
    rendering: rendering,
    duration: 0,
    u: Math.min(W, H) / 1080,
    timelines: [],
    renderers: [],
    videos: [],
    videoTextures: [],
    pending: [],
    rgba: rgba,
    mix: mix,
    onColor: onColor,
    luminance: luminance,
    hexToRgb: hexToRgb,
  };

  NM.px = function (value) { return value * NM.u; };
  NM.clamp = function (v, a, b) { return Math.max(a, Math.min(b, v)); };
  NM.ramp = function (t, a, b) { return NM.clamp((t - a) / Math.max(1e-6, b - a), 0, 1); };
  NM.smooth = function (x) { return x * x * (3 - 2 * x); };
  NM.lerp = function (a, b, k) { return a + (b - a) * k; };
  NM.prop = function (name, fallback) {
    var v = props[name];
    return v === undefined || v === null || v === '' ? fallback : v;
  };
  NM.isVideo = function (url) {
    return /\.(mp4|webm|mov|m4v|mkv|ogv)(\?|#|$)/i.test(String(url || ''));
  };
  NM.stage = function () { return document.getElementById('stage') || document.body; };

  function fontStack(family) {
    family = String(family || '').trim();
    if (!family) return SANS;
    return '"' + family.replace(/"/g, '') + '", ' + SANS;
  }

  function applyBrand() {
    var root = document.documentElement;
    var c = brand.colors;
    root.style.setProperty('--c-primary', c.primary);
    root.style.setProperty('--c-secondary', c.secondary);
    root.style.setProperty('--c-accent', c.accent);
    root.style.setProperty('--c-bg', c.background);
    root.style.setProperty('--c-text', c.text);
    root.style.setProperty('--c-on-primary', onColor(c.primary));
    root.style.setProperty('--c-on-accent', onColor(c.accent));
    root.style.setProperty('--c-muted', rgba(c.text, 0.66));
    root.style.setProperty('--c-line', rgba(c.text, 0.14));
    root.style.setProperty('--c-glass', rgba(c.text, 0.07));
    root.style.setProperty('--font-heading', fontStack(brand.fonts.heading));
    root.style.setProperty('--font-body', fontStack(brand.fonts.body || brand.fonts.heading));
    root.style.setProperty('--w', W + 'px');
    root.style.setProperty('--h', H + 'px');
    root.style.setProperty('--u', NM.u + 'px');
    root.classList.add(canvas.portrait ? 'nm-portrait' : canvas.square ? 'nm-square' : 'nm-landscape');
    if (canvas.transparent) root.classList.add('nm-transparent');
    if (rendering) root.classList.add('nm-rendering');
    document.body.style.width = W + 'px';
    document.body.style.height = H + 'px';
  }

  function loadFonts() {
    var faces = (brand.font_faces || []).map(function (face) {
      try {
        var ff = new FontFace(face.family, 'url(' + JSON.stringify(face.url) + ')', {
          weight: String(face.weight || 'normal'),
          style: face.style || 'normal',
        });
        return ff.load().then(function (loaded) { document.fonts.add(loaded); }).catch(function (err) {
          console.warn('brand font failed: ' + face.url + ' ' + err);
        });
      } catch (err) {
        return Promise.resolve();
      }
    });
    return Promise.all(faces).then(function () {
      // Warm every family the templates use (brand + vendored Inter) so
      // canvas text drawn during setup already has the right glyphs.
      if (!document.fonts || !document.fonts.load) return null;
      var families = [brand.fonts.heading, brand.fonts.body, 'Inter'].filter(Boolean);
      var loads = [];
      families.forEach(function (family) {
        ['400', '500', '600', '700', '800'].forEach(function (w) {
          loads.push(document.fonts.load(w + ' 64px "' + String(family).replace(/"/g, '') + '"', 'Aa0').catch(function () {}));
        });
      });
      return Promise.all(loads);
    });
  }

  function withTimeout(promise, ms, label) {
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { reject(new Error(label + ' timed out')); }, ms);
      promise.then(function (v) { clearTimeout(timer); resolve(v); }, function (e) { clearTimeout(timer); reject(e); });
    });
  }

  NM.image = function (url) {
    var img = new Image();
    img.decoding = 'sync';
    img.crossOrigin = 'anonymous';
    var p = new Promise(function (resolve, reject) {
      img.onload = function () {
        (img.decode ? img.decode() : Promise.resolve()).then(function () { resolve(img); }, function () { resolve(img); });
      };
      img.onerror = function () { reject(new Error('image failed to load: ' + url)); };
    });
    img.src = url;
    return withTimeout(p, 30000, 'image ' + url);
  };

  /**
   * A media layer (image or video) as a DOM element. Videos are muted,
   * registered for deterministic seeking and start at `start` seconds of
   * the source when the composition is at `at` seconds.
   */
  NM.media = function (url, opts) {
    opts = opts || {};
    if (!url) return Promise.resolve(null);
    if (!NM.isVideo(url) && opts.kind !== 'video') return NM.image(url);
    var v = document.createElement('video');
    v.muted = true;
    v.defaultMuted = true;
    v.playsInline = true;
    v.preload = 'auto';
    v.crossOrigin = 'anonymous';
    v.loop = opts.loop !== false;
    var p = new Promise(function (resolve, reject) {
      v.addEventListener('loadeddata', function () { resolve(v); }, { once: true });
      v.addEventListener('error', function () { reject(new Error('video failed to load: ' + url)); }, { once: true });
    });
    v.src = url;
    // Keep the element in the document: Chromium may skip presenting decoded
    // frames for detached videos, which leaves WebGL textures black. Templates
    // that show the video in the DOM simply move it.
    var holder = document.getElementById('nm-video-holder');
    if (!holder) {
      holder = document.createElement('div');
      holder.id = 'nm-video-holder';
      holder.style.cssText = 'position:absolute;left:0;top:0;width:2px;height:2px;overflow:hidden;opacity:0.001;pointer-events:none;z-index:-1';
      document.body.appendChild(holder);
    }
    holder.appendChild(v);
    v.load();
    NM.videos.push({
      el: v,
      start: Number(opts.start) || 0,
      rate: Number(opts.rate) || 1,
      at: Number(opts.at) || 0,
      loop: opts.loop !== false,
    });
    return withTimeout(p, 30000, 'video ' + url).then(function (el) {
      if (!rendering) el.play().catch(function () {});
      return el;
    });
  };

  /** three.js texture from an image, video or canvas element. */
  NM.texture = function (THREE, el) {
    var source = el;
    var copy = null;
    if (el && el.tagName === 'VIDEO') {
      // Video frames reach WebGL through a 2D canvas copy made after each
      // seek: direct video uploads come out black in software GL.
      copy = document.createElement('canvas');
      copy.width = Math.max(2, el.videoWidth || 2);
      copy.height = Math.max(2, el.videoHeight || 2);
      source = copy;
    }
    var tex = new THREE.Texture(source);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.minFilter = THREE.LinearFilter;
    tex.magFilter = THREE.LinearFilter;
    tex.generateMipmaps = false;
    tex.anisotropy = 4;
    tex.needsUpdate = true;
    if (copy) {
      var ctx = copy.getContext('2d', { alpha: false });
      NM.videoTextures.push({
        texture: tex,
        refresh: function () {
          try { ctx.drawImage(el, 0, 0, copy.width, copy.height); } catch (err) { /* frame not ready */ }
          tex.needsUpdate = true;
        },
      });
      try { ctx.drawImage(el, 0, 0, copy.width, copy.height); } catch (err) { /* first frame later */ }
    }
    return tex;
  };

  /** Crop a texture like CSS object-fit: cover onto a plane of planeAspect (w/h). */
  NM.coverTexture = function (tex, mediaAspect, planeAspect) {
    tex.repeat.set(1, 1);
    tex.offset.set(0, 0);
    if (mediaAspect > planeAspect) {
      tex.repeat.x = planeAspect / mediaAspect;
      tex.offset.x = (1 - tex.repeat.x) / 2;
    } else {
      tex.repeat.y = mediaAspect / planeAspect;
      tex.offset.y = (1 - tex.repeat.y) / 2;
    }
    return tex;
  };

  NM.mediaSize = function (el) {
    if (!el) return { w: 16, h: 9 };
    if (el.tagName === 'VIDEO') return { w: el.videoWidth || 16, h: el.videoHeight || 9 };
    return { w: el.naturalWidth || el.width || 16, h: el.naturalHeight || el.height || 9 };
  };

  /** Logo element: the brand logo image, or a typographic wordmark. */
  NM.logo = function (opts) {
    opts = opts || {};
    var url = (opts.dark && brand.logo_dark_url) || brand.logo_url;
    if (url) {
      return NM.image(url).then(function (img) {
        img.className = 'nm-logo-img ' + (opts.className || '');
        return img;
      }).catch(function (err) {
        console.warn(String(err));
        return wordmark(opts);
      });
    }
    return Promise.resolve(wordmark(opts));
  };
  function wordmark(opts) {
    var span = document.createElement('span');
    span.className = 'nm-wordmark ' + (opts.className || '');
    span.textContent = brand.name || 'Navin';
    return span;
  }

  /** Canvas with the brand logo (or wordmark) for use as a three.js texture. */
  NM.logoCanvas = function (opts) {
    opts = opts || {};
    var size = opts.size || 1024;
    var url = (opts.dark && brand.logo_dark_url) || brand.logo_url;
    var draw = function (img) {
      var c = document.createElement('canvas');
      var ctx = c.getContext('2d');
      if (img) {
        var s = NM.mediaSize(img);
        var k = size / Math.max(s.w, s.h);
        c.width = Math.max(2, Math.round(s.w * k));
        c.height = Math.max(2, Math.round(s.h * k));
        ctx.drawImage(img, 0, 0, c.width, c.height);
        return c;
      }
      var text = brand.name || 'Navin';
      var fs = Math.round(size * 0.22);
      ctx.font = '800 ' + fs + 'px ' + fontStack(brand.fonts.heading);
      var m = ctx.measureText(text);
      c.width = Math.max(2, Math.ceil(m.width + fs * 0.6));
      c.height = Math.ceil(fs * 1.5);
      ctx.font = '800 ' + fs + 'px ' + fontStack(brand.fonts.heading);
      ctx.fillStyle = opts.color || brand.colors.text;
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'center';
      ctx.fillText(text, c.width / 2, c.height / 2);
      return c;
    };
    if (!url) return Promise.resolve(draw(null));
    return NM.image(url).then(draw, function () { return draw(null); });
  };

  /** Split an element's text into masked words or characters. */
  NM.split = function (el, mode) {
    mode = mode || 'words';
    var text = el.textContent;
    el.setAttribute('aria-label', text);
    el.textContent = '';
    var parts = [];
    text.split(/(\s+)/).forEach(function (word) {
      if (!word) return;
      if (/^\s+$/.test(word)) {
        el.appendChild(document.createTextNode(' '));
        return;
      }
      var mask = document.createElement('span');
      mask.className = 'nm-mask';
      var inner = document.createElement('span');
      inner.className = 'nm-word';
      if (mode === 'chars') {
        Array.from(word).forEach(function (ch) {
          var c = document.createElement('span');
          c.className = 'nm-char';
          c.textContent = ch;
          inner.appendChild(c);
          parts.push(c);
        });
      } else {
        inner.textContent = word;
        parts.push(inner);
      }
      mask.appendChild(inner);
      el.appendChild(mask);
    });
    return parts;
  };

  /** Shrink an element's font size until it fits its box (max lines aware). */
  NM.fit = function (el, maxPx, minPx) {
    var size = maxPx;
    el.style.fontSize = size + 'px';
    var guard = 40;
    while (guard-- > 0 && size > minPx && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)) {
      size *= 0.93;
      el.style.fontSize = size + 'px';
    }
    return size;
  };

  NM.el = function (tag, className, text, parent) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined && text !== null) e.textContent = String(text);
    if (parent) parent.appendChild(e);
    return e;
  };

  /** A WebGL renderer sized to the composition, pixel ratio 1. */
  NM.three = function (THREE, opts) {
    opts = opts || {};
    var alpha = opts.alpha !== undefined ? !!opts.alpha : canvas.transparent;
    var r = new THREE.WebGLRenderer({
      antialias: opts.antialias !== false,
      alpha: alpha,
      preserveDrawingBuffer: true,
      powerPreference: 'high-performance',
    });
    // scale < 1 renders smooth content (gradients, bokeh) below output size;
    // the compositor upsamples it, which is much cheaper in software GL.
    r.setPixelRatio(NM.clamp((opts.scale ? Number(opts.scale) : 1) * canvas.glScale, 0.25, 1));
    r.setSize(W, H, false);
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.toneMapping = opts.toneMapping === undefined ? THREE.ACESFilmicToneMapping : opts.toneMapping;
    r.toneMappingExposure = opts.exposure || 1;
    if (alpha) r.setClearColor(0x000000, 0);
    r.domElement.className = 'nm-gl ' + (opts.className || '');
    NM.gl = true;
    var parent = opts.parent || NM.stage();
    // WebGL sits under the DOM copy unless a template asks otherwise.
    // Order: backgrounds (.nm-backdrop, .nm-bg) < WebGL < copy and overlays.
    var anchor = null;
    for (var i = 0; i < parent.children.length; i++) {
      var child = parent.children[i];
      if (!child.classList.contains('nm-backdrop') && !child.classList.contains('nm-bg')) { anchor = child; break; }
    }
    if (opts.front || !anchor) parent.appendChild(r.domElement);
    else parent.insertBefore(r.domElement, anchor);
    return r;
  };

  function videoTarget(rec, t) {
    var el = rec.el;
    var d = isFinite(el.duration) ? el.duration : 0;
    var local = rec.start + Math.max(0, t - rec.at) * rec.rate;
    if (d > 0) {
      if (rec.loop && local >= d) local = local % d;
      local = Math.min(local, Math.max(0, d - 0.002));
    }
    return local + 0.0005;
  }

  function seekVideo(rec, t) {
    var el = rec.el;
    var target = videoTarget(rec, t);
    if (!el.seeking && Math.abs(el.currentTime - target) < 1e-6 && el.readyState >= 2) return Promise.resolve();
    return new Promise(function (resolve) {
      var done = false;
      var timer = setTimeout(function () {
        console.warn('video seek timed out at ' + target.toFixed(3) + 's');
        finish();
      }, 10000);
      function finish() {
        if (done) return;
        done = true;
        clearTimeout(timer);
        el.removeEventListener('seeked', onSeeked);
        resolve();
      }
      function onSeeked() {
        if (el.readyState >= 2) finish();
        else el.addEventListener('canplay', finish, { once: true });
      }
      el.addEventListener('seeked', onSeeked);
      el.currentTime = target;
    });
  }

  function paint(t) {
    var gsap = window.gsap;
    if (rendering && gsap) gsap.globalTimeline.totalTime(t);
    NM.timelines.forEach(function (tl) {
      tl.totalTime(Math.max(0, Math.min(t, tl.totalDuration())), false);
    });
    if (rendering && document.getAnimations) {
      document.getAnimations().forEach(function (anim) {
        try {
          anim.pause();
          anim.currentTime = t * 1000;
        } catch (err) { /* finished or detached animation */ }
      });
    }
    NM.videoTextures.forEach(function (entry) { entry.refresh(); });
    clock.flush();
    NM.renderers.forEach(function (fn) { fn(t); });
  }

  NM.seek = function (t) {
    t = Math.max(0, Number(t) || 0);
    NM.time = t;
    clock.set(t * 1000);
    clock.frame = Math.round(t * canvas.fps);
    return Promise.all(NM.videos.map(function (rec) { return seekVideo(rec, t); })).then(function () {
      paint(t);
      if (!rendering || !clock.realRaf || (!NM.gl && !NM.videos.length)) return null;
      // WebGL canvases and video layers reach the screen through the
      // compositor: wait two real frames so the capture cannot see the
      // previous frame's pixels when several pages render in parallel.
      return new Promise(function (resolve) {
        clock.realRaf(function () { clock.realRaf(function () { resolve(); }); });
      });
    });
  };

  function preview() {
    var started = null;
    var real = window.requestAnimationFrame.bind(window);
    var loop = function (now) {
      if (started === null) started = now;
      var t = ((now - started) / 1000) % Math.max(0.1, NM.duration);
      NM.time = t;
      paint(t);
      real(loop);
    };
    real(loop);
  }

  function domReady() {
    if (document.readyState !== 'loading') return Promise.resolve();
    return new Promise(function (resolve) { document.addEventListener('DOMContentLoaded', resolve, { once: true }); });
  }

  NM.mount = function (def) {
    def = def || {};
    var requested = Number(canvas.duration) > 0 ? Number(canvas.duration) : Number(props.duration) > 0 ? Number(props.duration) : 0;
    NM.duration = requested || Number(def.duration) || 5;
    window.__navinDuration = NM.duration;
    var run = function () {
      return domReady()
        .then(function () {
          applyBrand();
          return withTimeout(loadFonts(), 30000, 'fonts');
        })
        .then(function () {
          var gsap = window.gsap;
          if (gsap) {
            gsap.ticker.lagSmoothing(0);
            if (rendering) {
              gsap.globalTimeline.pause();
              // 2D transforms only: GSAP's default promotes tweening elements to
              // GPU layers whose cached raster makes a frame depend on the frames
              // painted before it. Every frame must be a pure function of t.
              gsap.config({ force3D: false });
            }
          }
          return def.setup ? def.setup(NM) : {};
        })
        .then(function (api) {
          api = api || {};
          if (api.timeline) {
            api.timeline.pause();
            NM.timelines.push(api.timeline);
          }
          if (api.render) NM.renderers.push(api.render);
          return Promise.all(NM.pending).then(function () {
            return document.fonts ? document.fonts.ready : null;
          });
        })
        .then(function () {
          window.__navinSeek = NM.seek;
          return NM.seek(0);
        })
        .then(function () {
          window.__navinReady = true;
          document.documentElement.classList.add('nm-ready');
          if (!rendering) preview();
        })
        .catch(function (err) {
          window.__navinError = String((err && (err.stack || err.message)) || err);
          console.error('navin motion setup failed: ' + window.__navinError);
        });
    };
    run();
  };

  window.NavinMotion = NM;
})();
