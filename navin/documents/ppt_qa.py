# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Visual QA for a deck, run on the slides before they become a PPTX.

``templates/ppt/_engine/quality.json`` has always declared what a good slide is:
seven weighted categories, nine rules, a threshold of 85. Both presentation
skills tell the agent to redo any slide scoring under it. Nothing computed that
score, so the instruction could not be followed. This module computes it.

Each slide is laid out in Chromium at 1920x1080 - the frame ``html2pptx`` uses,
so QA and conversion agree on what is on the slide and what hangs off it - and
read the way an audience would: does a box run off the edge, do two of them
cover each other, is that grey caption legible from the back of the room, does
the slide carry a title, is the chart accompanied by the sentence that says what
it means. Every slide comes back scored out of 100 with, for each finding, the
thing to change.

The declared rules leave three categories without a check: ``hierarchy`` weighs
20 points, ``image`` 10, and no rule addressed them. The missing ones are here -
a slide with no title, a title that is not a heading and therefore reaches
PowerPoint as an anonymous textbox, a stretched picture - because a category
that cannot lose points is a category that means nothing.

Every template's ``metadata.json`` declares ``text_policy:
replace_all_visible_text``, and nothing read it, so a deck could be delivered
with its first two slides written for the subject and the rest still describing
the template's imaginary company. ``template-copy`` compares the deck against
the template it was copied from and blocks on anything left word for word;
filler blocks the same way, because a slide of placeholder copy still scores in
the low nineties and would otherwise pass the threshold.

Usage::

    python3 -m navin.documents.ppt_qa slides/
    python3 -m navin.documents.ppt_qa slides/ --json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

if __package__:
    from . import _dom
    from ._chromium import ConversionError, find_chromium, measure
else:  # copied as a standalone folder into a user workspace
    import _dom
    from _chromium import ConversionError, find_chromium, measure

SLIDE_WIDTH_PX = 1920
SLIDE_HEIGHT_PX = 1080
DEFAULT_THRESHOLD = 85

# The weights the engine declares. Kept as a fallback for a converters folder
# materialized without the engine beside it.
FALLBACK_WEIGHTS = {
    "layout": 20,
    "readability": 20,
    "hierarchy": 20,
    "balance": 15,
    "data": 10,
    "image": 10,
    "theme": 5,
}

# code -> (category, penalty, what to do about it)
RULES: dict[str, tuple[str, int, str]] = {
    # layout - the nine declared rules start here
    "overflow": ("layout", 8, "The box runs past the 1920x1080 slide and will be clipped. Shorten the copy, or switch to a layout that has room for it."),
    "overlaps": ("layout", 7, "Two text boxes cover the same pixels, so one prints over the other. Move them apart, or drop one."),
    "margins": ("layout", 5, "Content sits closer to the edge than the theme margin allows. Keep it inside --nv-margin."),
    # readability
    "contrast": ("readability", 8, "Text against its background falls under the readable ratio. Use --nv-text on a light surface, --nv-on-accent on a filled one."),
    "density-overflow": ("readability", 6, "Too much copy for one slide: over 80 words of body, or more than 6 bullets. Cut it, or split the slide in two."),
    "tiny-text": ("readability", 5, "Text this small is unreadable from the back of a room. Nothing under 18px on a 1920px slide."),
    # hierarchy
    "no-title": ("hierarchy", 8, "The slide has no title. An audience reads the title first, and a deck without them has no outline."),
    "title-not-heading": ("hierarchy", 7, "The title is not an h1/h2, so it reaches PowerPoint as an anonymous textbox: no outline entry, no name in the thumbnail panel. Keep the class, change the tag."),
    "title-too-long": ("hierarchy", 4, "The title runs past 90 characters. A slide title is an assertion, not a paragraph."),
    "flat-hierarchy": ("hierarchy", 5, "The title is barely larger than the body, so nothing leads the eye. Use the .nv-title scale from the theme."),
    # balance
    "repeat-layout": ("balance", 6, "Three slides in a row with the same composition. Alternate layouts, or the deck reads as one long slide."),
    "dead-space": ("balance", 5, "The slide is mostly empty. Give it real content, merge it with its neighbour, or use a layout built for one idea."),
    "thin-slide": ("balance", 6, "The slide is a title and one line. Add three cards, a process, a KPI row, or a real photo beside the copy."),
    "missing-section-break": ("balance", 6, "An 8+ page talk needs a chapter curtain (section-break). Split the story."),
    "missing-data-hero": ("data", 6, "An 8+ page talk needs one giant number slide (big-numbers or kpi)."),
    "missing-gallery": ("image", 5, "The deck has photos but no gallery. Put them on one grid."),
    "flat-rhythm": ("balance", 5, "Three slides in a row share the same light or dark family. Flip the tone."),
    "crowded": ("balance", 5, "Too many separate blocks competing on one slide. Group them, or split the slide."),
    # data
    "chart-insight": ("data", 6, "A chart with no insight line. Say what the data means, in one sentence, next to it."),
    "empty": ("data", 5, "Sample copy left in place. Every block gets real content, or it is removed and the layout rebalanced."),
    "template-copy": ("data", 8, "This text is still the bundled template's, word for word. Every template declares text_policy replace_all_visible_text: rewrite the slide for this deck's subject, or delete it."),
    # image
    "image-missing": ("image", 6, "The picture did not load, so the slide converts with an empty frame. Fix the src, or remove the frame."),
    "image-distorted": ("image", 5, "The picture is stretched. Set one dimension and let the other follow, or use object-fit: cover."),
    "image-pixelated": ("image", 4, "The picture is shown far larger than its pixels allow. Use a bigger source."),
    # theme
    "theme-colors": ("theme", 5, "A colour that is not in the theme token set. Use var(--nv-*), or the deck cannot change theme."),
    "theme-unlinked": ("theme", 5, "The slide declares no theme: it links no theme.css and defines no --nv-* tokens. Materialize it with ppt_design.py rather than hand-rolling colours."),
}

_MEASURE_BODY = r"""
  const out = { findings: [], images: [], signature: "", empty: false };
  const add = (code, detail) => out.findings.push({ code, detail: String(detail || "") });

  const root = document.querySelector(".nv-slide") || document.body;
  const frame = { x: 0, y: 0, w: W, h: H };

  const lum = (hex) => {
    const parts = [0, 2, 4].map((i) => parseInt(hex.substr(i, 2), 16) / 255);
    const linear = parts.map((c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  };

  const contrast = (a, b) => {
    const high = Math.max(lum(a), lum(b));
    const low = Math.min(lum(a), lum(b));
    return (high + 0.05) / (low + 0.05);
  };

  // What is actually behind the text: the nearest ancestor painting something
  // opaque *under it*. Containment is checked geometrically, because slides are
  // full of absolutely positioned labels that sit outside the parent that owns
  // them - a chart label above its bar is on the slide, not on the bar. A
  // full-bleed deck often paints nothing opaque at all, so the page background
  // is the honest last answer.
  const backdrop = (el) => {
    const box = el.getBoundingClientRect();
    const middle = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    let node = el;
    while (node && node !== document.documentElement) {
      const found = color(getComputedStyle(node).backgroundColor);
      if (found && found.alpha > 0.5) {
        const painted = node.getBoundingClientRect();
        const covers = node === el ||
          (middle.x >= painted.left && middle.x <= painted.right &&
           middle.y >= painted.top && middle.y <= painted.bottom);
        if (covers) return found.hex;
      }
      node = node.parentElement;
    }
    const body = color(getComputedStyle(document.body).backgroundColor);
    return body ? body.hex : "FFFFFF";
  };

  const label = (el) => {
    const tag = el.tagName.toLowerCase();
    const cls = (el.className || "").toString().trim().split(/\s+/).filter(Boolean)[0];
    const text = (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 48);
    return (cls ? tag + "." + cls : tag) + (text ? ' "' + text + '"' : "");
  };

  const words = (text) => (text.trim().match(/\S+/g) || []).length;

  // Slide furniture: a brand mark, a page number, a confidentiality mention.
  // It is deliberately discreet, so it is not held to the size and contrast a
  // reader has to take in from the back of the room.
  const CHROME = ".nv-footer, .nv-foot, .nv-page, .nv-page-number, .nv-logo, .nv-credit, .nv-watermark";
  const isChrome = (el) => !!(el.closest && el.closest(CHROME));

  const themeMargin = () => {
    const declared = getComputedStyle(document.documentElement).getPropertyValue("--nv-margin");
    const parsed = parseFloat(declared);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 64;
  };

  // A text box, defined exactly as html2pptx defines one, so a finding here
  // names a shape that will exist in the deck.
  const boxes = [];
  const images = [];
  const classes = new Set();

  const walk = (el) => {
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (SKIP_TAGS.has(el.tagName)) return;
    if (!visible(cs, rect)) {
      // visible() drops what sits off the canvas, which is the defect itself
      // on a fixed-size slide.
      if (hasText(el) && cs.display !== "none" && cs.visibility !== "hidden" &&
          rect.width >= 1 && rect.height >= 1 && (rect.left >= W || rect.top >= H)) {
        add("overflow", label(el));
      }
      return;
    }

    for (const name of (el.className || "").toString().split(/\s+/)) {
      if (name.startsWith("nv-")) classes.add(name);
    }

    const picture = pictureSource(el, cs);
    if (picture) {
      images.push({
        src: picture.src,
        x: round(rect.left), y: round(rect.top),
        w: round(rect.width), h: round(rect.height),
      });
      if (el.tagName === "IMG") {
        const natural = { w: el.naturalWidth || 0, h: el.naturalHeight || 0 };
        if (!el.complete || natural.w < 1) {
          add("image-missing", label(el) + " " + (el.getAttribute("src") || "").slice(0, 60));
        } else if (rect.width > 1 && rect.height > 1) {
          const fit = cs.objectFit || "fill";
          if (fit === "fill") {
            const drift = Math.abs((rect.width / rect.height) / (natural.w / natural.h) - 1);
            if (drift > 0.05) add("image-distorted", label(el));
          }
          if (natural.w < rect.width * 0.7) {
            add("image-pixelated", label(el) + " " + natural.w + "px shown at " + round(rect.width) + "px");
          }
        }
        return;
      }
    }

    const blockKids = Array.from(el.children).filter(
      (kid) => !SKIP_TAGS.has(kid.tagName) && isBlockish(kid) && hasText(kid),
    );
    if (hasText(el) && blockKids.length === 0) {
      const runs = runsOf(el);
      if (runs.length) {
        const size = parseFloat(cs.fontSize) || 0;
        const weight = parseInt(cs.fontWeight, 10) || 400;
        const text = (el.textContent || "").trim();
        boxes.push({
          el, rect, size, weight, text,
          bold: weight >= 600,
          cls: (el.className || "").toString(),
          tag: el.tagName,
        });
        if (rect.right > W + 2 || rect.bottom > H + 2 || rect.left < -2 || rect.top < -2) {
          add("overflow", label(el));
        }
        const ink = color(cs.color);
        if (ink && text.length > 2) {
          const ratio = contrast(ink.hex, backdrop(el));
          // Body copy owes the full 4.5:1 the quality brief asks for. Display
          // sizes and slide furniture owe the large-text floor, which is what
          // keeps a deliberate credit line from reading as a defect.
          const large = size >= 24 || (size >= 19 && weight >= 600);
          if (ratio < (large || isChrome(el) ? 3 : 4.5)) {
            add("contrast", label(el) + " at " + ratio.toFixed(1) + ":1");
          }
        }
        if (size > 0 && size < 18 && !isChrome(el)) {
          add("tiny-text", label(el) + " at " + round(size) + "px");
        }
      }
      return;
    }
    for (const kid of Array.from(el.children)) walk(kid);
  };

  walk(document.body);

  out.images = images;
  out.empty = boxes.length === 0 && images.length === 0;

  // Margins: only text is held to them. Artwork bleeding to the edge is a
  // design choice; a sentence starting 8px from it is an accident.
  const margin = themeMargin();
  const keepOut = Math.max(24, margin * 0.5);
  for (const box of boxes) {
    const r = box.rect;
    if (r.left < keepOut - 1 || r.top < keepOut - 1 ||
        r.right > W - keepOut + 1 || r.bottom > H - keepOut + 1) {
      if (r.right <= W + 2 && r.bottom <= H + 2 && r.left >= -2 && r.top >= -2) {
        add("margins", label(box.el));
      }
    }
  }

  // Overlaps: text boxes are leaves, so two of them sharing pixels is one
  // printing over the other rather than a container holding its child.
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const a = boxes[i].rect;
      const b = boxes[j].rect;
      const overlap =
        Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) *
        Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
      if (overlap <= 0) continue;
      const smaller = Math.min(a.width * a.height, b.width * b.height);
      if (smaller > 0 && overlap > smaller * 0.35) {
        add("overlaps", label(boxes[i].el) + " over " + label(boxes[j].el));
      }
    }
  }

  // Hierarchy. The title is what the audience reads first and what PowerPoint
  // needs to build an outline, so it is checked as a structure, not as styling.
  const titled = Array.from(root.querySelectorAll(".nv-title"));
  const headings = Array.from(root.querySelectorAll("h1, h2"));
  const title = titled[0] || headings[0] || null;
  if (!out.empty && boxes.length) {
    if (!title) {
      // A quote or a single statement is its own title, and the slide is the
      // sentence. A slide carrying several blocks and leading with none of
      // them has genuinely lost its title.
      const spoken = boxes.filter((box) => !isChrome(box.el));
      if (spoken.length > 2) add("no-title", "no .nv-title, no h1/h2");
    } else {
      const text = (title.textContent || "").trim();
      if (!/^H[12]$/.test(title.tagName)) add("title-not-heading", label(title));
      if (text.length > 90) add("title-too-long", text.length + " characters");
      const titleSize = parseFloat(getComputedStyle(title).fontSize) || 0;
      const bodySizes = boxes
        .filter((box) => box.el !== title && words(box.text) > 3)
        .map((box) => box.size);
      const body = bodySizes.length ? Math.max.apply(null, bodySizes) : 0;
      if (titleSize > 0 && body > 0 && titleSize < body * 1.35) {
        add("flat-hierarchy", "title " + round(titleSize) + "px against body " + round(body) + "px");
      }
    }
  }

  // Density, counted on the body alone: a title and its insight line are not
  // what makes a slide heavy.
  const chrome = /nv-(title|kicker|insight|foot|page|logo|eyebrow)/;
  let bodyWords = 0;
  for (const box of boxes) {
    if (chrome.test(box.cls)) continue;
    if (title && (box.el === title || title.contains(box.el))) continue;
    bodyWords += words(box.text);
  }
  const bullets = root.querySelectorAll("li").length;
  if (bodyWords > 80) add("density-overflow", bodyWords + " words of body copy");
  if (bullets > 6) add("density-overflow", bullets + " bullets");

  // Balance. Emptiness is only a fault where there was something to spread
  // out: a cover or a statement slide is meant to hold one idea and a lot of
  // air, and calling that a defect would teach the deck to crowd itself.
  const content = boxes.filter((box) => !isChrome(box.el));
  const layout = (document.documentElement.getAttribute("data-layout") || "").toLowerCase();
  const oneIdea = /^(cover|hero|statement|section-break|closing|quote|full-image|full-bleed-hero)$/;
  const rich = root.querySelector(
    ".nv-item, .nv-step, .nv-stat, .nv-img, .nv-panel, .nv-chart, .nv-card, .nv-node, .nv-cell, .nv-bar"
  );
  if (!oneIdea.test(layout) && content.length > 0 && content.length < 4 && !rich) {
    add("thin-slide", "title and a line; add cards, a process, a KPI or a real photo");
  }
  if (content.length >= 2 && !oneIdea.test(layout)) {
    let left = W, top = H, right = 0, bottom = 0;
    const spread = (r) => {
      left = Math.min(left, r.left); top = Math.min(top, r.top);
      right = Math.max(right, r.right); bottom = Math.max(bottom, r.bottom);
    };
    for (const box of content) spread(box.rect);
    for (const image of images) {
      if (image.w > 40 && image.h > 40) {
        spread({ left: image.x, top: image.y, right: image.x + image.w, bottom: image.y + image.h });
      }
    }
    // Measured down the page rather than by area: a slide is read top to
    // bottom, and what an audience sees as empty is the band under the last
    // line, not the columns a centred layout leaves on either side.
    const usable = Math.max(1, H - 2 * margin);
    const filled = Math.max(0, Math.min(bottom, H) - Math.max(top, 0)) / usable;
    if (filled < 0.35) add("dead-space", round(filled * 100) + "% of the page height used");
  }
  if (boxes.length > 16) add("crowded", boxes.length + " separate text blocks");

  // Data: a chart has to say what it means, because a shape does not argue on
  // its own. A table is left out on purpose - its rows carry their own labels,
  // and the declared rule names a chart slide.
  const dataViz = root.querySelector(".nv-chart, canvas, svg.nv-chart, .nv-donut, .nv-gauge");
  if (dataViz) {
    const insight = root.querySelector(".nv-insight, .nv-takeaway, [data-insight]");
    const line = insight && (insight.textContent || "").trim();
    if (!line || words(line) < 2) add("chart-insight", label(dataViz));
  }

  // Sample copy the template shipped with, left in place. The bundled decks
  // use several lorem variants - "Lorem dolor sit amet consectetur ..." - so
  // matching the exact phrase "lorem ipsum" passed a deck whose pricing and
  // contact slides were still filler. Dummy contact runs ("(+62) 000 0000
  // 0000") ship in the same slides and are just as unpresentable.
  const sample = /\blorem\b|dolor sit amet|consectet(?:ur|uer) adipiscing|eiusmod tempor|placeholder|your (?:title|text|logo|name|company) here|title goes here|body copy here|\bTBD\b|xxxx|\b0{3,}[\s-]0{4,}\b/i;
  for (const box of boxes) {
    if (sample.test(box.text)) add("empty", label(box.el));
  }

  // What the slide looks like from a distance, for the deck-level check on
  // three identical compositions in a row. The declared layout id when the
  // slide has one, its shape otherwise.
  const declared = document.documentElement.getAttribute("data-layout") ||
    document.body.getAttribute("data-layout") || "";
  out.signature = declared ||
    (Array.from(classes).sort().join(",") + "|" + Math.round(boxes.length / 3) + "|" + (images.length ? "img" : ""));

  publish(out);
"""

# Findings that no score can buy back. A deck still carrying the template's
# own sentences is not a deck that scored badly, it is a deck nobody wrote:
# under the weighted score a slide of pure filler still lands in the low 90s
# and sails past the threshold.
BLOCKING = frozenset({
    "empty",
    "template-copy",
    "thin-slide",
    "missing-section-break",
    "missing-data-hero",
})

# Long enough that a shared chunk means copied prose rather than a heading two
# unrelated decks happen to share ("Our Team", "Thank You").
_VERBATIM_MIN_CHARS = 25

_THEME_LINK_RE = re.compile(r"""<link[^>]+href=["'][^"']*theme\.css""", re.I)
_TOKEN_RE = re.compile(r"--nv-[a-z0-9-]+\s*:", re.I)
_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3,8})\b")
_NEUTRAL_HEX = {"#000", "#fff", "#000000", "#ffffff"}


def _dedupe(findings: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    """One entry per code, with a count: nine identical overlaps are one problem."""
    grouped: dict[str, dict[str, Any]] = {}
    for finding in findings:
        code = str(finding.get("code") or "")
        if code not in RULES:
            continue
        entry = grouped.setdefault(code, {"code": code, "count": 0, "examples": []})
        entry["count"] += 1
        detail = str(finding.get("detail") or "").strip()
        if detail and len(entry["examples"]) < 3:
            entry["examples"].append(detail)
    return list(grouped.values())


def weights() -> dict[str, int]:
    """The category weights, from the engine that declares them."""
    try:
        if __package__:
            from . import ppt_design
        else:  # pragma: no cover - standalone workspace copy
            import ppt_design

        declared = json.loads(
            (ppt_design.engine_dir() / "quality.json").read_text(encoding="utf-8")
        )
        found = declared.get("weights")
        if isinstance(found, dict) and found:
            return {str(name): int(value) for name, value in found.items()}
    except Exception:  # pragma: no cover - engine not materialized beside the tools
        pass
    return dict(FALLBACK_WEIGHTS)


def threshold() -> int:
    try:
        if __package__:
            from . import ppt_design
        else:  # pragma: no cover - standalone workspace copy
            import ppt_design

        declared = json.loads(
            (ppt_design.engine_dir() / "quality.json").read_text(encoding="utf-8")
        )
        return int(declared.get("threshold", DEFAULT_THRESHOLD))
    except Exception:  # pragma: no cover
        return DEFAULT_THRESHOLD


def score_slide(findings: list[dict[str, Any]], points: dict[str, int] | None = None) -> dict[str, Any]:
    """Turn one slide's findings into a score out of 100, per category.

    A repeated fault costs more than a single one but not linearly: four
    stretched pictures is one habit to fix, not four separate failures.
    """
    categories = dict(points or weights())
    reported: list[dict[str, Any]] = []
    for entry in findings:
        category, penalty, fix = RULES[entry["code"]]
        count = int(entry["count"])
        cost = penalty + (penalty // 2) * min(count - 1, 3)
        categories[category] = max(0, categories.get(category, 0) - cost)
        reported.append(
            {
                "code": entry["code"],
                "category": category,
                "count": count,
                "examples": entry["examples"],
                "fix": fix,
            }
        )
    reported.sort(key=lambda item: (-item["count"], item["code"]))
    return {"score": sum(categories.values()), "categories": categories, "findings": reported}


def collect_slides(source: Path) -> list[Path]:
    """The slides of a deck, in the order they are shown."""
    source = Path(source)
    if source.is_file():
        return [source]
    if not source.is_dir():
        raise ConversionError(f"{source}: no such file or folder")
    slides = sorted(source.glob("slide_*.html")) or sorted(source.glob("*.html"))
    if not slides:
        raise ConversionError(f"{source}: no slide HTML found")
    return slides


def review(
    source: Path,
    *,
    chromium: str | None = None,
    limit: int | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Lay every slide out, inspect it, and score the deck."""
    slides = collect_slides(Path(source))
    if limit:
        slides = slides[:limit]
    binary = find_chromium(chromium)
    gate = threshold()
    points = weights()

    measured: list[dict[str, Any]] = []
    workdir = Path(tempfile.mkdtemp(prefix="navin-ppt-qa-"))
    try:
        for slide in slides:
            payload = measure(
                slide,
                _dom.script(_MEASURE_BODY, SLIDE_WIDTH_PX, SLIDE_HEIGHT_PX),
                chromium=binary,
                workdir=workdir,
                timeout=timeout,
                viewport=(SLIDE_WIDTH_PX, SLIDE_HEIGHT_PX),
                prefix="qa",
                frame=(SLIDE_WIDTH_PX, SLIDE_HEIGHT_PX),
            )
            payload["source"] = slide
            measured.append(payload)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    _flag_repeated_layouts(measured)
    _flag_stagecraft(measured, slides)

    folder = Path(source) if Path(source).is_dir() else Path(source).parent
    shipped = _template_phrases(folder)

    reported: list[dict[str, Any]] = []
    for index, payload in enumerate(measured, start=1):
        findings = _dedupe(payload.get("findings") or [])
        findings.extend(_theme_findings(payload["source"]))
        findings.extend(_template_findings(payload["source"], shipped))
        scored = score_slide(findings, points)
        scored["slide"] = index
        scored["file"] = payload["source"].name
        reported.append(scored)

    scores = [slide["score"] for slide in reported] or [0]
    worst = min(scores)
    blocked = _blocked(reported)
    rework = sorted({slide["slide"] for slide in reported if slide["score"] < gate} | set(blocked))
    return {
        "deck": Path(source).name,
        "slides": reported,
        "score": round(sum(scores) / len(scores)),
        "worst_slide": worst,
        "threshold": gate,
        # One weak slide is the one the room remembers, so it is what gates -
        # and filler never passes, whatever the rest of the slide scores.
        "pass": worst >= gate and not blocked,
        "blocked": blocked,
        "rework": rework,
    }


def _blocked(reported: list[dict[str, Any]]) -> list[int]:
    """Slides carrying a fault no score can buy back."""
    return [
        slide["slide"]
        for slide in reported
        if any(finding["code"] in BLOCKING for finding in slide["findings"])
    ]


def _flag_repeated_layouts(measured: list[dict[str, Any]]) -> None:
    """Three slides in a row built the same way, per the declared rule."""
    run = 1
    for index in range(1, len(measured)):
        previous = str(measured[index - 1].get("signature") or "")
        current = str(measured[index].get("signature") or "")
        if current and current == previous:
            run += 1
        else:
            run = 1
        if run >= 3:
            measured[index].setdefault("findings", []).append(
                {"code": "repeat-layout", "detail": f"same composition as slides {index - 1} and {index}"}
            )


_STAGE_SKIP = {"cover", "hero", "full-bleed-hero", "agenda", "closing"}
_DATA_HERO = {"big-numbers", "kpi", "dashboard", "data-story"}
_TONE_RE = re.compile(r'data-tone="([^"]+)"')
_LAYOUT_ATTR_RE = re.compile(r'data-layout="([^"]+)"')


def _flag_stagecraft(measured: list[dict[str, Any]], paths: list[Path]) -> None:
    """Deck-level holes: no curtain, no data hero, no photo grid, flat rhythm."""
    layouts: list[str] = []
    tones: list[str] = []
    has_photo = False
    for path in paths:
        try:
            html = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            layouts.append("")
            tones.append("")
            continue
        layout_match = _LAYOUT_ATTR_RE.search(html)
        layouts.append(layout_match.group(1) if layout_match else "")
        tone_match = _TONE_RE.search(html)
        tones.append(tone_match.group(1) if tone_match else "")
        if "<img" in html.lower():
            has_photo = True
    content = [layout for layout in layouts if layout and layout not in _STAGE_SKIP]
    target = measured[-1] if measured else None
    if target is None:
        return
    if len(content) >= 8:
        if "section-break" not in layouts:
            target.setdefault("findings", []).append(
                {"code": "missing-section-break", "detail": "no chapter curtain"}
            )
        if not set(layouts) & _DATA_HERO:
            target.setdefault("findings", []).append(
                {"code": "missing-data-hero", "detail": "no giant number slide"}
            )
        if "gallery" not in layouts and has_photo:
            target.setdefault("findings", []).append(
                {"code": "missing-gallery", "detail": "photos exist but no grid"}
            )
    run = 1
    for index in range(1, len(tones)):
        previous = "dark" if "dark" in tones[index - 1] else ("light" if tones[index - 1] else "")
        current = "dark" if "dark" in tones[index] else ("light" if tones[index] else "")
        if current and current == previous:
            run += 1
        else:
            run = 1
        if run >= 3:
            measured[index].setdefault("findings", []).append(
                {"code": "flat-rhythm", "detail": f"same {current} family as the two before"}
            )


def _theme_findings(slide: Path) -> list[dict[str, Any]]:
    """Whether the slide can change theme, or has its colours nailed down.

    A deliverable slide comes out of ``ppt_design materialize``, so it links a
    theme or carries the tokens inline. One that does neither is a slide whose
    palette cannot be changed without a rewrite, which is worth saying once
    rather than once per stray colour.
    """
    try:
        html = slide.read_text(encoding="utf-8", errors="ignore")
    except OSError:  # pragma: no cover
        return []
    themed = bool(_THEME_LINK_RE.search(html)) or bool(_TOKEN_RE.search(html))
    if not themed:
        return [{"code": "theme-unlinked", "count": 1, "examples": []}]
    allowed = {value.lower() for value in _HEX_RE.findall(_declared_tokens(html))}
    stray = {
        found.lower()
        for found in _HEX_RE.findall(_authored_css(html))
        if found.lower() not in allowed and found.lower() not in _NEUTRAL_HEX
    }
    if stray:
        return [
            {"code": "theme-colors", "count": len(stray), "examples": sorted(stray)[:3]}
        ]
    return []


_TAG_RE = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)
_SENTENCE_RE = re.compile(r"[.!?\n]+")


def _phrases(html: str) -> set[str]:
    """The prose a viewer reads on the slide, as comparable chunks."""
    import html as html_mod

    text = html_mod.unescape(_TAG_RE.sub("\n", html))
    found: set[str] = set()
    for chunk in _SENTENCE_RE.split(text):
        normalized = " ".join(chunk.split()).lower()
        if len(normalized) >= _VERBATIM_MIN_CHARS:
            found.add(normalized)
    return found


def _bundled_template(slides_dir: Path) -> Path | None:
    """The template this deck was copied from, when it still says so.

    ``materialize`` copies the whole template folder, ``metadata.json``
    included, so the deck carries the name of the template it came from.
    """
    try:
        declared = json.loads((slides_dir / "metadata.json").read_text(encoding="utf-8"))
        name = str(declared.get("presentation_name") or "").strip()
    except Exception:
        return None
    if not name:
        return None
    try:
        if __package__:
            from . import ppt_design
        else:  # pragma: no cover - standalone workspace copy
            import ppt_design

        root = ppt_design.engine_dir().parent
    except Exception:  # pragma: no cover - engine not materialized beside the tools
        return None
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir() or candidate.name.startswith("_"):
            continue
        if candidate.resolve() == slides_dir.resolve():
            # QA run on the pristine template itself, not on a deck made from it.
            return None
        try:
            meta = json.loads((candidate / "metadata.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(meta.get("presentation_name") or "").strip() == name:
            return candidate
    return None


def _template_phrases(slides_dir: Path) -> set[str]:
    """Every sentence the bundled template shipped with."""
    template = _bundled_template(slides_dir)
    if template is None:
        return set()
    shipped: set[str] = set()
    for slide in sorted(template.glob("slide_*.html")):
        try:
            shipped |= _phrases(slide.read_text(encoding="utf-8", errors="ignore"))
        except OSError:  # pragma: no cover
            continue
    return shipped


def _template_findings(slide: Path, shipped: set[str]) -> list[dict[str, Any]]:
    """Text left exactly as the template wrote it.

    Every template metadata declares ``text_policy: replace_all_visible_text``
    and nothing checked it, so a deck could be shipped with two slides adapted
    and thirteen still describing the template's imaginary company.
    """
    if not shipped:
        return []
    try:
        html = slide.read_text(encoding="utf-8", errors="ignore")
    except OSError:  # pragma: no cover
        return []
    left = sorted(_phrases(html) & shipped)
    if not left:
        return []
    return [
        {
            "code": "template-copy",
            "count": len(left),
            "examples": [chunk[:80] for chunk in left[:3]],
        }
    ]


def _declared_tokens(html: str) -> str:
    """The token block, where naming a colour is the whole point."""
    return "\n".join(
        block
        for block in re.findall(r"<style\b[^>]*>(.*?)</style>", html, re.S | re.I)
        if _TOKEN_RE.search(block)
    )


def _authored_css(html: str) -> str:
    """Everything else: the rules and inline styles that survive a theme change."""
    styles = [
        block
        for block in re.findall(r"<style\b[^>]*>(.*?)</style>", html, re.S | re.I)
        if not _TOKEN_RE.search(block)
    ]
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    if body:
        styles.extend(re.findall(r'style="([^"]*)"', body.group(1), re.I))
    return "\n".join(styles)


def format_report(report: dict[str, Any]) -> str:
    """The same result, for someone reading a terminal."""
    lines = [
        f"{report['deck']}: {report['score']}/100 "
        f"(worst slide {report['worst_slide']}, threshold {report['threshold']}) "
        f"{'PASS' if report['pass'] else 'REWORK'}"
    ]
    for slide in report["slides"]:
        if not slide["findings"]:
            continue
        lines.append(f"\n  slide {slide['slide']} ({slide['file']}) - {slide['score']}/100")
        for finding in slide["findings"]:
            times = f" x{finding['count']}" if finding["count"] > 1 else ""
            lines.append(f"    [{finding['category']}] {finding['code']}{times}")
            for example in finding["examples"]:
                lines.append(f"        {example}")
            lines.append(f"      -> {finding['fix']}")
    if report.get("blocked"):
        lines.append(
            f"\n  unshippable - filler or template copy still on slides: "
            f"{', '.join(str(n) for n in report['blocked'])}"
        )
    if report["rework"]:
        lines.append(
            f"\n  slides to rework: {', '.join(str(n) for n in report['rework'])}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Visual QA for a slide deck")
    parser.add_argument("source", help="A slides folder, or one slide HTML")
    parser.add_argument("--json", action="store_true", help="Machine-readable report")
    parser.add_argument("--limit", type=int, help="Only the first N slides")
    parser.add_argument("--chromium", help="Path to a Chromium binary")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)

    try:
        report = review(
            Path(args.source),
            chromium=args.chromium,
            limit=args.limit,
            timeout=args.timeout,
        )
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
