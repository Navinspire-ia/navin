"""JavaScript helpers injected into the page by the document converters.

Everything here runs inside Chromium against the laid-out document: boxes come
from ``getBoundingClientRect``, styling from ``getComputedStyle``. The helpers
are shared so a heading looks the same whether it ends up in a slide, a Word
paragraph or a spreadsheet cell.
"""

from __future__ import annotations

if __package__:
    from ._chromium import RESULT_ELEMENT_ID
else:  # copied as a standalone folder into a user workspace
    from _chromium import RESULT_ELEMENT_ID

_HELPERS = r"""
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD"]);
  const round = (v) => Math.round(v * 100) / 100;

  const color = (raw) => {
    const m = /rgba?\(([^)]+)\)/.exec(raw || "");
    if (!m) return null;
    const parts = m[1].split(",").map((s) => parseFloat(s));
    const alpha = parts.length > 3 ? parts[3] : 1;
    if (!(alpha > 0.02)) return null;
    const hex = parts
      .slice(0, 3)
      .map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0"))
      .join("");
    return { hex: hex.toUpperCase(), alpha };
  };

  // H is 0 for flow documents, which run past any viewport: there the page
  // has no bottom edge to clip against.
  const visible = (cs, rect) => {
    if (cs.display === "none" || cs.visibility === "hidden") return false;
    if (parseFloat(cs.opacity || "1") < 0.05) return false;
    if (rect.width < 1 || rect.height < 1) return false;
    if (rect.right <= 0 || rect.bottom <= 0 || rect.left >= W) return false;
    if (H > 0 && rect.top >= H) return false;
    return true;
  };

  // Page containers, without the ones nested inside another.
  const pageRoots = (selectors) => {
    for (const selector of selectors) {
      const found = Array.from(document.querySelectorAll(selector)).filter(
        (el) => el.getBoundingClientRect().width > W * 0.5,
      );
      const roots = found.filter((el) => !found.some((other) => other !== el && other.contains(el)));
      if (roots.length) return roots;
    }
    return [document.body];
  };

  const contentBox = (el, cs, rect) => {
    const pad = (v) => parseFloat(v) || 0;
    const left = rect.left + pad(cs.borderLeftWidth) + pad(cs.paddingLeft);
    const top = rect.top + pad(cs.borderTopWidth) + pad(cs.paddingTop);
    const right = rect.right - pad(cs.borderRightWidth) - pad(cs.paddingRight);
    const bottom = rect.bottom - pad(cs.borderBottomWidth) - pad(cs.paddingBottom);
    return { x: left, y: top, w: Math.max(right - left, 1), h: Math.max(bottom - top, 1) };
  };

  const fontStack = (raw) =>
    (raw || "")
      .split(",")
      .map((name) => name.trim().replace(/^["']|["']$/g, ""))
      .filter(Boolean);

  const fontFamily = (raw) => fontStack(raw)[0] || "Arial";

  const transformText = (text, mode) => {
    if (mode === "uppercase") return text.toUpperCase();
    if (mode === "lowercase") return text.toLowerCase();
    if (mode === "capitalize") return text.replace(/\b\p{L}/gu, (c) => c.toUpperCase());
    return text;
  };

  const isBlockish = (el) => {
    const d = getComputedStyle(el).display;
    return (
      d === "block" || d === "flex" || d === "grid" || d === "list-item" ||
      d === "table" || d === "table-row" || d === "table-cell" || d === "flow-root" ||
      d === "inline-block" || d === "inline-flex" || d === "inline-grid"
    );
  };

  const hasText = (el) => (el.textContent || "").trim().length > 0;

  // Grid and flex rows hold side-by-side blocks, which a flow format can only
  // reproduce as a row of cells. Returns the column count, 0 when not a row.
  const columnsOf = (el, cs) => {
    const kids = Array.from(el.children).filter(
      (kid) =>
        !SKIP_TAGS.has(kid.tagName) &&
        visible(getComputedStyle(kid), kid.getBoundingClientRect()) &&
        hasText(kid),
    );
    if (kids.length < 2) return 0;
    if (cs.display === "grid" || cs.display === "inline-grid") {
      const cols = (cs.gridTemplateColumns || "").split(" ").filter(Boolean).length;
      return cols > 1 ? cols : 0;
    }
    if (cs.display === "flex" || cs.display === "inline-flex") {
      if ((cs.flexDirection || "row").startsWith("column")) return 0;
      if ((cs.flexWrap || "nowrap") !== "nowrap") return 0;
      // Baselines rarely land on the exact same pixel from one page to the
      // next, and an unstable answer here means the same banner is read as a
      // row on page 1 and as stacked text on page 2.
      const tops = kids.map((kid) => kid.getBoundingClientRect().top);
      return Math.max(...tops) - Math.min(...tops) <= 8 ? kids.length : 0;
    }
    return 0;
  };

  // Gradient text paints through the background with a transparent fill.
  const gradientStops = (cs) => {
    const raw = (cs.backgroundImage || "").trim();
    if (!/gradient\(/.test(raw)) return null;
    const found = [];
    const re = /(rgba?\([^)]+\))\s*(-?[\d.]+)?(%)?/g;
    let match;
    while ((match = re.exec(raw)) !== null) {
      const parsed = color(match[1]);
      if (!parsed) continue;
      found.push({
        hex: parsed.hex,
        pos: match[3] ? Math.max(0, Math.min(100, parseFloat(match[2]))) : null,
      });
    }
    if (found.length < 2) return null;
    found.forEach((stop, index) => {
      if (stop.pos === null) stop.pos = (index / (found.length - 1)) * 100;
    });
    const angleMatch = /linear-gradient\(\s*(-?[\d.]+)deg/.exec(raw);
    return { stops: found, angle: angleMatch ? parseFloat(angleMatch[1]) : 180 };
  };

  const runColor = (el, cs) => {
    const filled = color(cs.webkitTextFillColor || cs.color);
    if (filled) return { color: filled.hex, gradient: null };
    const clipped = cs.webkitBackgroundClip === "text" || cs.backgroundClip === "text";
    if (clipped) {
      el.setAttribute("data-h2x-gradient-text", "1");
      const gradient = gradientStops(cs);
      if (gradient) return { color: gradient.stops[0].hex, gradient };
    }
    const fallback = color(cs.color);
    return { color: fallback ? fallback.hex : "000000", gradient: null };
  };

  // Text runs of a container or of a loose group of nodes, split wherever
  // styling changes so bold or colored fragments survive into Office.
  const runsOf = (root) => {
    const runs = [];
    // Text sitting after a block sibling ("<strong>Date</strong>July 25")
    // needs a separator, or the two collapse into "DateJuly".
    let pendingBreak = false;
    const walk = (nodes) => {
      for (const node of nodes) {
        if (node.nodeType === 3) {
          const el = node.parentElement;
          if (!el) continue;
          const cs = getComputedStyle(el);
          let text = node.textContent || "";
          if (!/^pre/.test(cs.whiteSpace)) text = text.replace(/\s+/g, " ");
          if (!text) continue;
          if (pendingBreak) {
            pendingBreak = false;
            if (!/^\s/.test(text) && runs.length) text = " " + text;
          }
          const paint = runColor(el, cs);
          const weight = parseInt(cs.fontWeight, 10) || 400;
          const spacing = parseFloat(cs.letterSpacing);
          const highlight = color(cs.backgroundColor);
          runs.push({
            text: transformText(text, cs.textTransform),
            font: fontFamily(cs.fontFamily),
            families: fontStack(cs.fontFamily),
            size: round(parseFloat(cs.fontSize) || 16),
            bold: weight >= 600,
            italic: cs.fontStyle === "italic" || cs.fontStyle === "oblique",
            underline: (cs.textDecorationLine || "").includes("underline"),
            strike: (cs.textDecorationLine || "").includes("line-through"),
            color: paint.color,
            gradient: paint.gradient,
            highlight: el !== root && highlight && highlight.alpha > 0.5 ? highlight.hex : null,
            spacing: Number.isFinite(spacing) ? round(spacing) : 0,
          });
          continue;
        }
        if (node.nodeType !== 1) continue;
        if (SKIP_TAGS.has(node.tagName)) continue;
        if (node.tagName === "BR") {
          runs.push({ text: "\n", newline: true });
          continue;
        }
        // Two blocks side by side own separate text; without a separator they
        // would be read as one word ("DateFriday").
        const block = isBlockish(node);
        const last = runs[runs.length - 1];
        if (block && last && last.text && !/\s$/.test(last.text)) {
          runs.push({ ...last, text: " " });
        }
        walk(node.childNodes);
        if (block) pendingBreak = true;
      }
    };
    walk(Array.isArray(root) ? root : root.childNodes);
    while (runs.length && !runs[0].newline && !runs[0].text.trim()) runs.shift();
    while (runs.length && !runs[runs.length - 1].newline && !runs[runs.length - 1].text.trim()) {
      runs.pop();
    }
    return runs;
  };

  // Only pictures that can be re-embedded are extracted. Remote or vector
  // sources are left alone: hiding one without a replacement deletes it.
  const embeddable = (src) => {
    if (!src) return false;
    if (/^https?:/i.test(src)) return false;
    if (/^data:/i.test(src)) return /^data:image\/(png|jpe?g|gif|webp);base64,/i.test(src);
    return !/\.svgz?($|[?#])/i.test(src);
  };

  const pictureSource = (el, cs) => {
    if (el.tagName === "IMG") {
      return { src: el.currentSrc || el.src || "", fit: cs.objectFit || "fill" };
    }
    const bg = cs.backgroundImage || "";
    const m = /^url\((["']?)(.*?)\1\)$/.exec(bg.trim());
    if (!m) return null;
    const size = (cs.backgroundSize || "").trim();
    const fit = size === "cover" ? "cover" : size === "contain" ? "contain" : "fill";
    return { src: m[2], fit };
  };

  const publish = (out) => {
    const payload = JSON.stringify(out).replace(/</g, "\\u003c");
    const holder = document.createElement("script");
    holder.type = "application/json";
    holder.id = "__RESULT_ID__";
    holder.textContent = payload;
    document.body.appendChild(holder);
  };
"""


def script(body: str, width: int, height: int) -> str:
    """Wrap a measuring body with the shared helpers and page dimensions."""
    helpers = _HELPERS.replace("__RESULT_ID__", RESULT_ELEMENT_ID)
    return "(() => {\n" + f"  const W = {width}, H = {height};\n" + helpers + body + "\n})();"
