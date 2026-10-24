/* Navin canvas kit runtime. Copyright (c) 2026-present Navinspire IA. AGPL-3.0-only.
 *
 * Renders a canvas document ({title, subtitle, blocks}) read from
 * <script type="application/json" id="navin-canvas-data"> into #navin-canvas.
 * Plain DOM + SVG, no dependencies, no network, no innerHTML (all text goes
 * through textContent so data can never inject markup).
 *
 * Public API: window.NavinCanvas = { render(root, doc), applyTheme(msg), blocks }.
 * Theme: the host may post {type: "navin:canvas-theme", mode, tokens} where
 * tokens maps kit names (bg, fg, card, muted, mutedFg, border, accent, good,
 * warn, bad, info, s1..s5, font) to CSS colors.
 */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var SERIES = ["var(--nv-s1)", "var(--nv-s2)", "var(--nv-s3)", "var(--nv-s4)", "var(--nv-s5)"];
  var SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];
  var SEVERITY_TONE = { critical: "bad", high: "bad", medium: "warn", low: "accent", info: "neutral" };
  var STATUS_TONE = {
    fixed: "good", resolved: "good", done: "good", pass: "good", passed: "good", success: "good",
    merged: "good", clean: "good", mergeable: "good", approved: "good", ok: "good",
    open: "warn", todo: "neutral", pending: "warn", queued: "neutral", running: "accent",
    in_progress: "accent", doing: "accent", review: "info", blocked: "bad", fail: "bad",
    failed: "bad", failure: "bad", error: "bad", conflicting: "bad", unresolved: "warn",
    wontfix: "neutral", skipped: "neutral", cancelled: "neutral", neutral: "neutral",
    block: "bad", warn: "warn", warning: "warn", active: "accent", planned: "neutral"
  };

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function svg(tag, attrs, text) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) { node.setAttribute(k, String(attrs[k])); });
    }
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }

  function fmt(v, digits) {
    if (!isNum(v)) return v === null || v === undefined ? "" : String(v);
    var abs = Math.abs(v);
    var max = digits !== undefined ? digits : (abs >= 100 ? 0 : abs >= 10 ? 1 : 2);
    try {
      return new Intl.NumberFormat("en-US", { maximumFractionDigits: max }).format(v);
    } catch (e) {
      return String(Math.round(v * 100) / 100);
    }
  }

  function fmtCompact(v) {
    if (!isNum(v)) return String(v);
    var abs = Math.abs(v);
    if (abs >= 1e9) return fmt(v / 1e9, 1) + "B";
    if (abs >= 1e6) return fmt(v / 1e6, 1) + "M";
    if (abs >= 1e4) return fmt(v / 1e3, 1) + "k";
    return fmt(v);
  }

  function toneFor(value) {
    var key = String(value || "").toLowerCase().replace(/[\s-]+/g, "_");
    return STATUS_TONE[key] || "neutral";
  }

  function badge(label, tone) {
    return el("span", "nv-badge nv-tone-" + (tone || toneFor(label)), label);
  }

  function withUnit(label, unit) {
    if (!unit) return label;
    var lower = String(label).toLowerCase();
    if (lower === String(unit).toLowerCase() || lower.indexOf("(" + String(unit).toLowerCase() + ")") !== -1) return label;
    return label + " (" + unit + ")";
  }

  function niceStep(span, count) {
    var raw = span / Math.max(1, count);
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var norm = raw / mag;
    var step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
    return step * mag;
  }

  function niceDomain(values) {
    var lo = Math.min(0, Math.min.apply(null, values));
    var hi = Math.max(0, Math.max.apply(null, values));
    if (lo === hi) hi = lo + 1;
    var step = niceStep(hi - lo, 4);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    var ticks = [];
    for (var t = lo; t <= hi + step / 2; t += step) ticks.push(Math.round(t / step) * step);
    return { lo: lo, hi: hi, ticks: ticks };
  }

  function seriesColor(series, index) {
    if (series.color) return "var(--nv-" + series.color + ")";
    if (series.role === "baseline") return "var(--nv-s5)";
    return SERIES[index % SERIES.length];
  }

  function legend(seriesList) {
    var ul = el("ul", "nv-legend");
    ul.setAttribute("aria-label", "Legend");
    seriesList.forEach(function (s, i) {
      var li = el("li");
      var sw = el("span", "nv-swatch");
      sw.style.background = seriesColor(s, i);
      li.appendChild(sw);
      li.appendChild(el("span", null, s.name));
      ul.appendChild(li);
    });
    return ul;
  }

  function figure(block, draw) {
    var fig = el("figure", "nv-figure");
    fig.appendChild(el("h3", "nv-block-title", block.title));
    if (block.series.length > 1) fig.appendChild(legend(block.series));
    var host = el("div");
    fig.appendChild(host);
    fig.appendChild(el("figcaption", null, block.caption));
    var lastWidth = 0;
    function paint() {
      var width = Math.max(280, Math.floor(host.clientWidth || 640));
      if (width === lastWidth) return;
      lastWidth = width;
      while (host.firstChild) host.removeChild(host.firstChild);
      host.appendChild(draw(width));
    }
    paint();
    if (typeof ResizeObserver === "function") {
      new ResizeObserver(paint).observe(host);
    }
    return fig;
  }

  function truncate(text, max) {
    text = String(text);
    return text.length > max ? text.slice(0, max - 1) + "." : text;
  }

  // ---- Bar chart ---------------------------------------------------------
  function barChart(block) {
    return figure(block, function (width) {
      var cats = block.categories;
      var series = block.series;
      var all = [];
      series.forEach(function (s) { s.values.forEach(function (v) { all.push(v); }); });
      var dom = niceDomain(all);
      var unit = block.unit;
      var horizontal = block.orientation === "horizontal";
      var root;
      if (horizontal) {
        var rowH = Math.max(18, 10 + series.length * 10);
        var labelW = Math.min(180, Math.max(60, Math.max.apply(null, cats.map(function (c) { return String(c).length; })) * 6.5 + 8));
        var m = { t: 8, r: 48, b: 42, l: labelW };
        var height = m.t + m.b + cats.length * (rowH + 8);
        var pw = width - m.l - m.r;
        root = svg("svg", { "class": "nv-chart", width: width, height: height, viewBox: "0 0 " + width + " " + height, role: "img", "aria-label": block.title });
        var x = function (v) { return m.l + ((v - dom.lo) / (dom.hi - dom.lo)) * pw; };
        dom.ticks.forEach(function (t) {
          root.appendChild(svg("line", { "class": "nv-grid", x1: x(t), x2: x(t), y1: m.t, y2: height - m.b }));
          root.appendChild(svg("text", { x: x(t), y: height - m.b + 14, "text-anchor": "middle", "class": "nv-num" }, fmtCompact(t)));
        });
        root.appendChild(svg("text", { "class": "nv-axis-label", x: m.l + pw / 2, y: height - 6, "text-anchor": "middle" }, withUnit(block.y_label, unit)));
        cats.forEach(function (c, ci) {
          var y0 = m.t + ci * (rowH + 8);
          root.appendChild(svg("text", { x: m.l - 8, y: y0 + rowH / 2 + 4, "text-anchor": "end" }, truncate(c, 28)));
          var bh = (rowH - 2) / series.length;
          series.forEach(function (s, si) {
            var v = s.values[ci];
            var x0 = x(Math.min(0, v));
            var w = Math.max(1, Math.abs(x(v) - x(0)));
            var r = svg("rect", { x: x0, y: y0 + si * bh, width: w, height: Math.max(2, bh - 2), rx: 2, fill: seriesColor(s, si) });
            r.appendChild(svg("title", null, s.name + " - " + c + ": " + fmt(v) + (unit ? " " + unit : "")));
            root.appendChild(r);
            if (cats.length * series.length <= 24) {
              root.appendChild(svg("text", { "class": "nv-value-label nv-num", x: x0 + w + 4, y: y0 + si * bh + bh / 2 + 3 }, fmtCompact(v)));
            }
          });
        });
        root.appendChild(svg("line", { "class": "nv-axis", x1: x(0), x2: x(0), y1: m.t, y2: height - m.b }));
        return root;
      }
      var mm = { t: 14, r: 12, b: 48, l: 58 };
      var h = 260;
      var plotW = width - mm.l - mm.r;
      var plotH = h - mm.t - mm.b;
      root = svg("svg", { "class": "nv-chart", width: width, height: h, viewBox: "0 0 " + width + " " + h, role: "img", "aria-label": block.title });
      var y = function (v) { return mm.t + plotH - ((v - dom.lo) / (dom.hi - dom.lo)) * plotH; };
      dom.ticks.forEach(function (t) {
        root.appendChild(svg("line", { "class": "nv-grid", x1: mm.l, x2: width - mm.r, y1: y(t), y2: y(t) }));
        root.appendChild(svg("text", { x: mm.l - 6, y: y(t) + 4, "text-anchor": "end", "class": "nv-num" }, fmtCompact(t)));
      });
      root.appendChild(svg("text", { "class": "nv-axis-label", transform: "translate(12," + (mm.t + plotH / 2) + ") rotate(-90)", "text-anchor": "middle" }, withUnit(block.y_label, unit)));
      root.appendChild(svg("text", { "class": "nv-axis-label", x: mm.l + plotW / 2, y: h - 6, "text-anchor": "middle" }, block.x_label));
      var band = plotW / cats.length;
      var inner = band * 0.72;
      var bw = inner / series.length;
      var maxChars = Math.max(4, Math.floor(band / 6.5));
      cats.forEach(function (c, ci) {
        var bx = mm.l + ci * band + (band - inner) / 2;
        root.appendChild(svg("text", { x: mm.l + ci * band + band / 2, y: mm.t + plotH + 16, "text-anchor": "middle" }, truncate(c, maxChars)));
        series.forEach(function (s, si) {
          var v = s.values[ci];
          var top = y(Math.max(0, v));
          var bh2 = Math.max(1, Math.abs(y(v) - y(0)));
          var r = svg("rect", { x: bx + si * bw + 1, y: top, width: Math.max(2, bw - 2), height: bh2, rx: 2, fill: seriesColor(s, si) });
          r.appendChild(svg("title", null, s.name + " - " + c + ": " + fmt(v) + (unit ? " " + unit : "")));
          root.appendChild(r);
          if (bw >= 22) {
            root.appendChild(svg("text", { "class": "nv-value-label nv-num", x: bx + si * bw + bw / 2, y: (v >= 0 ? top - 4 : top + bh2 + 11), "text-anchor": "middle" }, fmtCompact(v)));
          }
        });
      });
      root.appendChild(svg("line", { "class": "nv-axis", x1: mm.l, x2: width - mm.r, y1: y(0), y2: y(0) }));
      return root;
    });
  }

  // ---- Line chart --------------------------------------------------------
  function lineChart(block) {
    return figure(block, function (width) {
      var xs = block.x;
      var series = block.series;
      var all = [];
      series.forEach(function (s) { s.values.forEach(function (v) { if (isNum(v)) all.push(v); }); });
      var dom = niceDomain(all);
      var m = { t: 14, r: 16, b: 48, l: 58 };
      var h = 260;
      var plotW = width - m.l - m.r;
      var plotH = h - m.t - m.b;
      var root = svg("svg", { "class": "nv-chart", width: width, height: h, viewBox: "0 0 " + width + " " + h, role: "img", "aria-label": block.title });
      var x = function (i) { return m.l + (xs.length === 1 ? plotW / 2 : (i / (xs.length - 1)) * plotW); };
      var y = function (v) { return m.t + plotH - ((v - dom.lo) / (dom.hi - dom.lo)) * plotH; };
      dom.ticks.forEach(function (t) {
        root.appendChild(svg("line", { "class": "nv-grid", x1: m.l, x2: width - m.r, y1: y(t), y2: y(t) }));
        root.appendChild(svg("text", { x: m.l - 6, y: y(t) + 4, "text-anchor": "end", "class": "nv-num" }, fmtCompact(t)));
      });
      var every = Math.max(1, Math.ceil(xs.length / Math.max(2, Math.floor(plotW / 70))));
      xs.forEach(function (label, i) {
        if (i % every !== 0 && i !== xs.length - 1) return;
        root.appendChild(svg("text", { x: x(i), y: m.t + plotH + 16, "text-anchor": "middle" }, truncate(label, 12)));
      });
      root.appendChild(svg("line", { "class": "nv-axis", x1: m.l, x2: width - m.r, y1: m.t + plotH, y2: m.t + plotH }));
      root.appendChild(svg("text", { "class": "nv-axis-label", transform: "translate(12," + (m.t + plotH / 2) + ") rotate(-90)", "text-anchor": "middle" }, withUnit(block.y_label, block.unit)));
      root.appendChild(svg("text", { "class": "nv-axis-label", x: m.l + plotW / 2, y: h - 6, "text-anchor": "middle" }, block.x_label));
      series.forEach(function (s, si) {
        var color = seriesColor(s, si);
        var d = "";
        var pen = false;
        s.values.forEach(function (v, i) {
          if (!isNum(v)) { pen = false; return; }
          d += (pen ? " L" : " M") + x(i).toFixed(1) + " " + y(v).toFixed(1);
          pen = true;
        });
        root.appendChild(svg("path", { d: d.trim(), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
        if (xs.length <= 60) {
          s.values.forEach(function (v, i) {
            if (!isNum(v)) return;
            var c = svg("circle", { cx: x(i), cy: y(v), r: 3, fill: "var(--nv-card)", stroke: color, "stroke-width": 2 });
            c.appendChild(svg("title", null, s.name + " - " + xs[i] + ": " + fmt(v) + (block.unit ? " " + block.unit : "")));
            root.appendChild(c);
          });
        }
      });
      return root;
    });
  }

  // ---- Stat row ----------------------------------------------------------
  function stats(block) {
    var wrap = el("div");
    if (block.title) wrap.appendChild(el("h3", "nv-block-title", block.title));
    var grid = el("div", "nv-stats");
    block.items.forEach(function (item) {
      var tile = el("div", "nv-stat");
      tile.appendChild(el("span", "nv-stat-label", item.label));
      var value = el("span", "nv-stat-value", isNum(item.value) ? fmt(item.value) : item.value);
      if (item.unit) value.appendChild(el("span", "nv-stat-unit", item.unit));
      tile.appendChild(value);
      if (item.delta !== undefined && item.delta !== null && item.delta !== "") {
        var text = isNum(item.delta) ? (item.delta > 0 ? "+" : "") + fmt(item.delta) + (item.delta_unit || "") : String(item.delta);
        tile.appendChild(el("span", "nv-stat-delta nv-tone-" + (item.tone || "neutral"), text));
      }
      if (item.hint) tile.appendChild(el("span", "nv-stat-hint", item.hint));
      grid.appendChild(tile);
    });
    wrap.appendChild(grid);
    return wrap;
  }

  // ---- Table -------------------------------------------------------------
  function cellSortValue(v) {
    if (v && typeof v === "object") return v.sort !== undefined ? v.sort : (v.badge || v.text || "");
    return v === null || v === undefined ? "" : v;
  }

  function deltaTone(v, col) {
    if (!isNum(v) || v === 0 || !col.better) return null;
    var good = col.better === "lower" ? v < 0 : v > 0;
    return good ? "good" : "bad";
  }

  function renderCell(td, v, col) {
    if (v && typeof v === "object") {
      if (v.badge) { td.appendChild(badge(v.badge, v.tone)); return; }
      td.textContent = v.text !== undefined ? String(v.text) : "";
      if (v.tone) td.className += " nv-tone-" + v.tone;
      return;
    }
    if (isNum(v)) {
      var text = fmt(v);
      if (col.format === "delta" || col.format === "delta_pct") text = (v > 0 ? "+" : "") + text + (col.format === "delta_pct" ? "%" : "");
      td.textContent = text;
      var tone = deltaTone(v, col);
      if (tone) td.className += " nv-tone-" + tone;
      return;
    }
    td.textContent = v === null || v === undefined ? "" : String(v);
  }

  function table(block) {
    var wrap = el("div");
    if (block.title) wrap.appendChild(el("h3", "nv-block-title", block.title));
    var scroller = el("div", "nv-table-wrap");
    var tbl = el("table", "nv-table");
    var thead = el("thead");
    var hr = el("tr");
    var numeric = {};
    block.columns.forEach(function (col) {
      numeric[col.key] = col.align ? col.align === "right" : block.rows.every(function (r) {
        var v = r[col.key];
        return v === null || v === undefined || v === "" || isNum(v);
      });
    });
    var state = { key: block.sort ? block.sort.key : null, dir: block.sort ? block.sort.dir || "desc" : "desc" };
    var tbody = el("tbody");
    var ths = [];
    function paintRows() {
      var rows = block.rows.slice();
      if (state.key) {
        var k = state.key;
        rows.sort(function (a, b) {
          var av = cellSortValue(a[k]);
          var bv = cellSortValue(b[k]);
          var cmp = isNum(av) && isNum(bv) ? av - bv : String(av).localeCompare(String(bv), undefined, { numeric: true });
          return state.dir === "asc" ? cmp : -cmp;
        });
      }
      while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
      rows.forEach(function (row) {
        var tr = el("tr");
        block.columns.forEach(function (col) {
          var td = el("td", (numeric[col.key] ? "nv-right" : "") + (col.mono ? " nv-mono" : ""));
          renderCell(td, row[col.key], col);
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      ths.forEach(function (th) {
        if (th.getAttribute("data-key") === state.key) th.setAttribute("aria-sort", state.dir === "asc" ? "ascending" : "descending");
        else th.removeAttribute("aria-sort");
      });
    }
    block.columns.forEach(function (col) {
      var th = el("th", numeric[col.key] ? "nv-right" : "");
      th.setAttribute("scope", "col");
      th.setAttribute("data-key", col.key);
      var btn = el("button", null);
      btn.setAttribute("type", "button");
      btn.appendChild(el("span", null, withUnit(col.label, col.unit)));
      btn.appendChild(el("span", "nv-sort"));
      btn.addEventListener("click", function () {
        if (state.key === col.key) state.dir = state.dir === "asc" ? "desc" : "asc";
        else { state.key = col.key; state.dir = numeric[col.key] ? "desc" : "asc"; }
        paintRows();
      });
      th.appendChild(btn);
      ths.push(th);
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    tbl.appendChild(thead);
    tbl.appendChild(tbody);
    paintRows();
    scroller.appendChild(tbl);
    wrap.appendChild(scroller);
    if (block.caption) wrap.appendChild(el("p", "nv-caption", block.caption));
    return wrap;
  }

  // ---- Timeline ----------------------------------------------------------
  function timeline(block) {
    var wrap = el("div");
    if (block.title) wrap.appendChild(el("h3", "nv-block-title", block.title));
    var ol = el("ol", "nv-timeline");
    block.items.forEach(function (item) {
      var li = el("li", "nv-tl-item" + (item.milestone ? " nv-milestone" : "") + (item.critical ? " nv-critical" : ""));
      var when = item.date || "";
      if (item.end) when += " - " + item.end;
      li.appendChild(el("span", "nv-tl-date", when));
      li.appendChild(el("span", "nv-tl-mark"));
      var body = el("div", "nv-tl-body");
      var title = el("div", "nv-tl-title");
      title.appendChild(el("span", null, item.label));
      if (item.status) title.appendChild(badge(item.status));
      if (item.critical) title.appendChild(badge("critical path", "accent"));
      body.appendChild(title);
      if (item.detail) body.appendChild(el("span", "nv-tl-detail", item.detail));
      li.appendChild(body);
      ol.appendChild(li);
    });
    wrap.appendChild(ol);
    if (block.caption) wrap.appendChild(el("p", "nv-caption", block.caption));
    return wrap;
  }

  // ---- Findings ----------------------------------------------------------
  function findings(block) {
    var wrap = el("div");
    if (block.title) wrap.appendChild(el("h3", "nv-block-title", block.title));
    var counts = {};
    block.items.forEach(function (f) { counts[f.severity] = (counts[f.severity] || 0) + 1; });
    var summary = el("div", "nv-badges");
    summary.style.marginBottom = "8px";
    SEVERITY_ORDER.forEach(function (s) {
      if (counts[s]) summary.appendChild(badge(counts[s] + " " + s, SEVERITY_TONE[s]));
    });
    wrap.appendChild(summary);
    var list = el("div", "nv-findings");
    var items = block.items.slice().sort(function (a, b) {
      return SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity);
    });
    items.forEach(function (f) {
      var card = el("article", "nv-finding nv-sev-" + f.severity);
      var head = el("div", "nv-finding-head");
      head.appendChild(badge(f.severity, SEVERITY_TONE[f.severity]));
      head.appendChild(el("span", "nv-finding-title", f.title));
      if (f.status) head.appendChild(badge(f.status));
      card.appendChild(head);
      if (f.file) {
        card.appendChild(el("span", "nv-finding-loc nv-mono", f.line ? f.file + ":" + f.line : f.file));
      } else if (f.location) {
        card.appendChild(el("span", "nv-finding-loc nv-mono", f.location));
      }
      if (f.detail) card.appendChild(el("p", null, f.detail)).style.margin = "0";
      if (f.evidence) card.appendChild(el("pre", null, f.evidence));
      if (f.fix) card.appendChild(el("span", "nv-finding-fix", "Fix: " + f.fix));
      list.appendChild(card);
    });
    wrap.appendChild(list);
    return wrap;
  }

  // ---- Diff / before-after ----------------------------------------------
  function lineDiff(a, b) {
    var n = a.length, m = b.length;
    if (n * m > 400000) {
      return { left: a.map(function (t) { return { t: t, k: "del" }; }), right: b.map(function (t) { return { t: t, k: "add" }; }) };
    }
    var dp = [];
    for (var i = 0; i <= n; i++) { dp.push(new Array(m + 1).fill(0)); }
    for (i = n - 1; i >= 0; i--) {
      for (var j = m - 1; j >= 0; j--) {
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
    var left = [], right = [];
    i = 0; j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) { left.push({ t: a[i], k: "" }); right.push({ t: b[j], k: "" }); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { left.push({ t: a[i++], k: "del" }); }
      else { right.push({ t: b[j++], k: "add" }); }
    }
    while (i < n) left.push({ t: a[i++], k: "del" });
    while (j < m) right.push({ t: b[j++], k: "add" });
    return { left: left, right: right };
  }

  function diff(block) {
    var wrap = el("div");
    if (block.title) wrap.appendChild(el("h3", "nv-block-title", block.title));
    var grid = el("div", "nv-diff");
    var result = lineDiff(String(block.before.text).split("\n"), String(block.after.text).split("\n"));
    [[block.before, result.left], [block.after, result.right]].forEach(function (pair) {
      var col = el("div", "nv-diff-col");
      col.appendChild(el("h4", null, pair[0].label));
      var pre = el("pre", "nv-diff-lines");
      pair[1].forEach(function (line) {
        pre.appendChild(el("span", "nv-diff-line" + (line.k ? " nv-" + line.k : ""), line.t));
      });
      col.appendChild(pre);
      grid.appendChild(col);
    });
    wrap.appendChild(grid);
    if (block.caption) wrap.appendChild(el("p", "nv-caption", block.caption));
    return wrap;
  }

  function badges(block) {
    var wrap = el("div");
    if (block.title) wrap.appendChild(el("h3", "nv-block-title", block.title));
    var row = el("div", "nv-badges");
    block.items.forEach(function (b) { row.appendChild(badge(b.label, b.tone)); });
    wrap.appendChild(row);
    return wrap;
  }

  function text(block) {
    var wrap = el("div", "nv-text");
    if (block.title) wrap.appendChild(el("h3", "nv-block-title", block.title));
    String(block.text).split(/\n\s*\n/).forEach(function (para) {
      if (para.trim()) wrap.appendChild(el("p", null, para.trim()));
    });
    return wrap;
  }

  function section(block) {
    var sec = el("section", "nv-section");
    sec.appendChild(el("h2", null, block.title));
    if (block.description) sec.appendChild(el("p", "nv-desc", block.description));
    renderBlocks(sec, block.blocks);
    return sec;
  }

  var BLOCKS = {
    section: section, stats: stats, table: table, bar_chart: barChart,
    line_chart: lineChart, timeline: timeline, findings: findings, diff: diff,
    badges: badges, text: text
  };

  function renderBlocks(parent, blocks) {
    (blocks || []).forEach(function (block) {
      var fn = BLOCKS[block && block.type];
      if (!fn) {
        if (window.console) console.warn("navin canvas: unknown block type", block && block.type);
        return;
      }
      var node = fn(block);
      node.setAttribute("data-block", block.type);
      parent.appendChild(node);
    });
  }

  function render(root, doc) {
    while (root.firstChild) root.removeChild(root.firstChild);
    root.className = "nv-canvas";
    var head = el("header", "nv-head");
    head.appendChild(el("h1", null, doc.title));
    var meta = [doc.subtitle, doc.generated_at ? "Generated " + doc.generated_at : null].filter(Boolean).join(" | ");
    if (meta) head.appendChild(el("p", null, meta));
    root.appendChild(head);
    renderBlocks(root, doc.blocks);
    root.setAttribute("data-rendered", "true");
  }

  var TOKEN_NAMES = ["bg", "fg", "card", "muted", "mutedFg", "border", "accent", "good", "warn", "bad", "info", "s1", "s2", "s3", "s4", "s5", "font"];

  function applyTheme(msg) {
    if (!msg || msg.type !== "navin:canvas-theme") return;
    var style = document.documentElement.style;
    if (msg.mode === "dark" || msg.mode === "light") style.setProperty("color-scheme", msg.mode);
    var tokens = msg.tokens || {};
    TOKEN_NAMES.forEach(function (name) {
      var value = tokens[name];
      if (typeof value !== "string" || !value || /[;{}<>]|url\(/i.test(value)) return;
      style.setProperty("--nv-" + name.replace(/[A-Z]/g, function (c) { return "-" + c.toLowerCase(); }), value);
    });
    document.documentElement.setAttribute("data-theme", msg.mode === "dark" ? "dark" : "light");
  }

  window.addEventListener("message", function (event) {
    if (event.source !== window.parent && event.source !== window) return;
    applyTheme(event.data);
  });

  window.NavinCanvas = { render: render, applyTheme: applyTheme, blocks: BLOCKS };

  function boot() {
    var data = document.getElementById("navin-canvas-data");
    var root = document.getElementById("navin-canvas");
    if (!data || !root) return;
    var doc;
    try { doc = JSON.parse(data.textContent || "{}"); } catch (e) {
      root.appendChild(el("p", "nv-caption", "Canvas data could not be read."));
      return;
    }
    render(root, doc);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
