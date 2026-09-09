# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Visual QA for a Word document, run on the page before it becomes a DOCX.

The document is laid out in Chromium and inspected the way a reader would: does
anything spill past the margin, is a heading stranded alone at the foot of a
page, is a caption still next to its figure, is that grey-on-grey caption
actually readable, is the page half empty. Each page comes back with a score out
of 100 and, for every finding, the thing to change.

Checking the HTML rather than the rendered DOCX is deliberate. It is the stage
where a problem can still be fixed by editing the source, it needs no office
suite installed, and it costs about a second. What it cannot see is Word's own
reflow, which is why html2docx pins the pagination it can (keep-with-next,
rows that must not split) instead of leaving it to chance.

Usage::

    python3 -m navin.documents.word_qa document.html
"""

from __future__ import annotations

import argparse
import json
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

PAGE_WIDTH_PX = 794
DEFAULT_THRESHOLD = 85

# What each category is worth, straight from the document quality brief.
CATEGORY_POINTS = {
    "typography": 15,
    "readability": 15,
    "layout": 15,
    "hierarchy": 15,
    "spacing": 10,
    "tables": 10,
    "images": 10,
    "theme": 10,
}

# code -> (category, penalty, what to do about it)
RULES: dict[str, tuple[str, int, str]] = {
    "overflow-right": ("layout", 8, "Content runs past the side margin. Shorten it, or let the block wrap instead of forcing its width."),
    "overflow-bottom": ("layout", 8, "Content runs off the bottom of the page. Move the tail onto the next .page section."),
    "page-nearly-empty": ("layout", 5, "The page is mostly white. Merge it with its neighbour, or give it real content."),
    "orphan-heading": ("layout", 6, "A heading sits alone at the foot of the page. Move it to the next page, or add data-doc-keep=\"next\"."),
    "table-overflow": ("tables", 8, "The table is wider than the text column. Drop a column, shorten the headers, or turn the page with data-doc-section=\"landscape\"."),
    "table-headerless": ("tables", 4, "The table has no header row. Use <th> so Word repeats it when the table breaks."),
    "caption-detached": ("images", 5, "The caption drifted away from its figure. Put it in the same <figure>, right after the image."),
    "image-distorted": ("images", 6, "The image is stretched. Set one dimension and leave the other auto."),
    "image-pixelated": ("images", 4, "The image is displayed larger than its pixels allow. Use a bigger source."),
    "low-contrast": ("readability", 6, "Text against its background falls under 4.5:1. Use --nv-text or --nv-accent-2-ink instead of a light tint."),
    "tiny-text": ("readability", 5, "Text under 8px is unreadable in print. Nothing below 9px except footers."),
    "font-zoo": ("typography", 5, "More than three font families on one page. Stick to --nv-font-heading and --nv-font-body."),
    "size-zoo": ("typography", 4, "Too many different text sizes on one page. Reuse the sizes the template already defines."),
    "no-heading": ("hierarchy", 6, "A page of running text with no heading. Give the section a title."),
    "level-skipped": ("hierarchy", 4, "A heading level was skipped (h1 to h3). Word builds its outline and its TOC from these."),
    "heading-in-grid": ("hierarchy", 4, "The heading sits deep inside a multi-column row, which html2docx renders as a table: Word gives it no outline level and the TOC cannot see it. Move it out of the row, or make it a direct child of it."),
    "heading-styled-div": ("hierarchy", 5, "This looks like a title but is not an h1-h6, so it stays plain text in Word: no outline entry, no TOC line. Use a heading tag and keep the class."),
    "wall-of-text": ("hierarchy", 5, "A long stretch of unbroken paragraphs. Break it up with a table, KPI row, callout or figure."),
    "spacing-jitter": ("spacing", 5, "The vertical rhythm is irregular. Reuse the same gaps between blocks."),
    "theme-literal-color": ("theme", 10, "A literal colour outside the theme block. Replace it with a var(--nv-*) token, or the document cannot change theme."),
    "theme-missing": ("theme", 10, "No <style id=\"navin-theme\"> block. Run word_design.py apply --theme <name>."),
}

_MEASURE_BODY = r"""
  const out = { pages: [] };

  // The same page containers html2docx converts, so QA and conversion never
  // disagree about what a page is.
  const roots = pageRoots([".page", ".sheet", "body > section", "body > article", "body > main"]);

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

  // The colour actually behind the text: the nearest ancestor that paints
  // something opaque, white when nothing does.
  const backdrop = (el) => {
    let node = el;
    while (node && node !== document.documentElement) {
      const found = color(getComputedStyle(node).backgroundColor);
      if (found && found.alpha > 0.5) return found.hex;
      node = node.parentElement;
    }
    return "FFFFFF";
  };

  const label = (el) => {
    const tag = el.tagName.toLowerCase();
    const cls = (el.className || "").toString().trim().split(/\s+/)[0];
    const text = (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 48);
    return (cls ? tag + "." + cls : tag) + (text ? ' "' + text + '"' : "");
  };

  const headingLevel = (el) => {
    const match = /^H([1-6])$/.exec(el.tagName);
    return match ? parseInt(match[1], 10) : 0;
  };

  const isCaption = (el) => {
    if (el.tagName === "FIGCAPTION") return true;
    const cls = (el.className || "").toString().toLowerCase();
    return /caption|legend/.test(cls);
  };

  for (const root of roots) {
    const cs = getComputedStyle(root);
    const rect = root.getBoundingClientRect();
    const box = contentBox(root, cs, rect);
    const findings = [];
    const add = (code, detail) => findings.push({ code, detail });

    const paper = { x: rect.left, y: rect.top, w: rect.width, h: rect.height };
    const inMargin = (el) => {
      let node = el;
      while (node && node !== root) {
        const position = getComputedStyle(node).position;
        if (position === "absolute" || position === "fixed") return true;
        node = node.parentElement;
      }
      return false;
    };

    // Mirrors what html2docx does with a multi-column row: it becomes a table.
    // A heading that is a direct child keeps its level (the row is read as a
    // title bar); one nested deeper ends up inside a cell, invisible to the
    // Word outline.
    const swallowed = (heading) => {
      let node = heading;
      let parent = node.parentElement;
      while (parent && parent !== root.parentElement) {
        if (parent.getAttribute("data-doc-row") === "stack") return false;
        if (node !== heading && columnsOf(parent, getComputedStyle(parent)) > 1) return true;
        node = parent;
        parent = parent.parentElement;
      }
      return false;
    };

    const cover = root.hasAttribute("data-doc-cover") ||
      /cover|title-page/.test((root.className || "").toString().toLowerCase());

    const fonts = new Set();
    const sizes = new Set();
    let lowest = box.y;
    let paragraphRun = 0;
    let maxParagraphRun = 0;
    let sawHeading = false;
    let titleCandidate = null;
    let lastLevel = 0;
    let visuals = 0;
    const gaps = [];
    let previousBottom = null;

    const walk = (el) => {
      if (SKIP_TAGS.has(el.tagName)) return;
      const style = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const text = (el.textContent || "").trim();
      if (!visible(style, r)) {
        // visible() discards anything pushed off the canvas, which is right for
        // the converter and wrong here: text shoved past the right edge is the
        // defect, not a reason to stop looking.
        if (text && style.display !== "none" && style.visibility !== "hidden" &&
            r.width >= 1 && r.height >= 1 && r.left >= box.x + box.w) {
          add("overflow-right", label(el));
        }
        return;
      }

      if (text) lowest = Math.max(lowest, r.bottom);

      // Overflow is measured on leaves only: a container is allowed to be as
      // wide as the page while its padding keeps the text inside.
      const leaf = !Array.from(el.children).some(
        (kid) => !SKIP_TAGS.has(kid.tagName) && visible(getComputedStyle(kid), kid.getBoundingClientRect()),
      );
      if (leaf && text) {
        // Running headers and footers are placed in the margin on purpose, so
        // they are held to the paper edge rather than to the text column. The
        // test walks up: the positioned element is the footer, the overflowing
        // one is the span inside it.
        const limit = inMargin(el) ? paper : box;
        if (r.right > limit.x + limit.w + 2) add("overflow-right", label(el));
        if (r.bottom > limit.y + limit.h + 2) add("overflow-bottom", label(el));
        fonts.add(fontFamily(style.fontFamily));
        const size = Math.round(parseFloat(style.fontSize) || 0);
        sizes.add(size);
        // Kept in case the page turns out to have no heading tag at all: it is
        // far more useful to name the div that should have been one.
        const weight = parseInt(style.fontWeight, 10) || 400;
        if (!titleCandidate && isBlockish(el) && size >= 16 && weight >= 600 &&
            text.length >= 6 && text.length <= 90) {
          titleCandidate = label(el);
        }
        if (size > 0 && size < 8) add("tiny-text", label(el) + " at " + size + "px");
        const ink = color(style.color);
        if (ink && text.length > 3) {
          const ratio = contrast(ink.hex, backdrop(el));
          const large = size >= 18 || (size >= 14 && (parseInt(style.fontWeight, 10) || 400) >= 600);
          if (ratio < (large ? 3 : 4.5)) {
            add("low-contrast", label(el) + " at " + ratio.toFixed(1) + ":1");
          }
        }
      }

      const level = headingLevel(el);
      if (level) {
        sawHeading = true;
        if (swallowed(el)) add("heading-in-grid", label(el));
        if (lastLevel && level > lastLevel + 1) {
          add("level-skipped", "h" + lastLevel + " to h" + level);
        }
        lastLevel = level;
        maxParagraphRun = Math.max(maxParagraphRun, paragraphRun);
        paragraphRun = 0;
        // A heading needs room for what follows; less than three lines of it
        // left on the page and the reader turns over to an empty promise.
        if (r.bottom > box.y + box.h - 90) add("orphan-heading", label(el));
      } else if (el.tagName === "P" && text.length > 120) {
        paragraphRun += 1;
      }

      if (el.tagName === "TABLE") {
        visuals += 1;
        maxParagraphRun = Math.max(maxParagraphRun, paragraphRun);
        paragraphRun = 0;
        if (r.width > box.w + 2) add("table-overflow", label(el));
        if (!el.querySelector("th")) add("table-headerless", label(el));
      }

      if (el.tagName === "IMG") {
        visuals += 1;
        const natural = { w: el.naturalWidth || 0, h: el.naturalHeight || 0 };
        if (natural.w > 1 && natural.h > 1 && r.width > 1 && r.height > 1) {
          const fit = style.objectFit || "fill";
          if (fit === "fill") {
            const drift = Math.abs((r.width / r.height) / (natural.w / natural.h) - 1);
            if (drift > 0.05) add("image-distorted", label(el));
          }
          if (natural.w < r.width * 0.8) {
            add("image-pixelated", label(el) + " " + natural.w + "px shown at " + Math.round(r.width) + "px");
          }
        }
        const caption = el.closest("figure") ? el.closest("figure").querySelector("figcaption") : null;
        if (caption) {
          const gap = caption.getBoundingClientRect().top - r.bottom;
          if (gap > 60) add("caption-detached", label(caption));
        }
      }

      if (isCaption(el)) {
        const figure = el.closest("figure");
        const image = figure ? figure.querySelector("img") : el.previousElementSibling;
        if (!image || image.tagName !== "IMG") add("caption-detached", label(el));
      }

      if (isBlockish(el) && el.parentElement === root) {
        // Bucketed: a 14px gap and a 16px gap are the same intention, and
        // treating them as different would call every page irregular.
        if (previousBottom !== null) gaps.push(Math.round((r.top - previousBottom) / 6) * 6);
        previousBottom = r.bottom;
      }

      for (const kid of el.children) walk(kid);
    };

    for (const kid of root.children) walk(kid);
    maxParagraphRun = Math.max(maxParagraphRun, paragraphRun);

    const fill = box.h > 0 ? (lowest - box.y) / box.h : 1;
    const last = root === roots[roots.length - 1];
    if (!cover && !last && fill < 0.45) {
      add("page-nearly-empty", Math.round(fill * 100) + "% of the page used");
    }
    if (!cover && !sawHeading && (root.textContent || "").trim().length > 400) {
      if (titleCandidate) add("heading-styled-div", titleCandidate);
      else add("no-heading", "no h1-h6 on a page of running text");
    }
    if (maxParagraphRun >= 5 && visuals === 0) {
      add("wall-of-text", maxParagraphRun + " long paragraphs in a row, nothing visual");
    }
    if (fonts.size > 3) add("font-zoo", Array.from(fonts).join(", "));
    if (sizes.size > 8) add("size-zoo", sizes.size + " different sizes");
    const positive = gaps.filter((g) => g > 0);
    if (positive.length >= 8 && new Set(positive).size > positive.length * 0.8) {
      add("spacing-jitter", new Set(positive).size + " different gaps between " + (positive.length + 1) + " blocks");
    }

    out.pages.push({
      cover: cover,
      fill: round(fill),
      findings: findings,
    });
  }

  publish(out);
"""


def _dedupe(findings: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """One entry per code, with a count: twelve identical overflows read as one problem."""
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


def score_page(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn one page's findings into a score out of 100, per category.

    A repeated fault costs more than a single one but not linearly: three
    stretched images is one habit to fix, not three separate failures.
    """
    categories = dict(CATEGORY_POINTS)
    reported: list[dict[str, Any]] = []
    for entry in findings:
        category, penalty, fix = RULES[entry["code"]]
        count = int(entry["count"])
        cost = penalty + (penalty // 2) * min(count - 1, 3)
        categories[category] = max(0, categories[category] - cost)
        reported.append(
            {
                "code": entry["code"],
                "category": category,
                "count": count,
                "examples": entry["examples"],
                "fix": fix,
            }
        )
    total = sum(categories.values())
    reported.sort(key=lambda item: (-item["count"], item["code"]))
    return {"score": total, "categories": categories, "findings": reported}


def review(
    source: Path,
    *,
    chromium: str | None = None,
    threshold: int = DEFAULT_THRESHOLD,
    timeout: int = 60,
    workdir: Path | None = None,
) -> dict[str, Any]:
    """Lay the document out, inspect every page, and score it."""
    source = Path(source)
    if not source.is_file():
        raise ConversionError(f"{source}: no such file")
    binary = find_chromium(chromium)
    temporary = workdir is None
    directory = Path(workdir or tempfile.mkdtemp(prefix="navin-word-qa-"))
    try:
        payload = measure(
            source,
            _dom.script(_MEASURE_BODY, PAGE_WIDTH_PX, 0),
            chromium=binary,
            workdir=directory,
            timeout=timeout,
            viewport=(PAGE_WIDTH_PX, 1123),
            prefix="qa",
        )
    finally:
        if temporary:
            shutil.rmtree(directory, ignore_errors=True)

    theme_findings = _theme_findings(source)
    pages: list[dict[str, Any]] = []
    for index, page in enumerate(payload.get("pages") or [], start=1):
        grouped = _dedupe(page.get("findings") or [])
        # The theme block belongs to the document, but a page that cannot be
        # restyled is a page that fails, so it is scored on every one of them.
        grouped.extend(theme_findings)
        scored = score_page(grouped)
        scored["page"] = index
        scored["fill"] = page.get("fill")
        pages.append(scored)

    scores = [page["score"] for page in pages] or [0]
    worst = min(scores)
    return {
        "document": source.name,
        "pages": pages,
        "score": round(sum(scores) / len(scores)),
        "worst_page": worst,
        "threshold": threshold,
        # The weakest page is what a reader notices, so it is what gates.
        "pass": worst >= threshold,
        "rework": [page["page"] for page in pages if page["score"] < threshold],
    }


def _theme_findings(source: Path) -> list[dict[str, Any]]:
    """Theme consistency, borrowed from the design system's own audit."""
    try:
        if __package__:
            from . import word_design
        else:  # pragma: no cover - standalone workspace copy
            import word_design
    except ImportError:  # pragma: no cover - converters copied without the engine
        return []
    try:
        report = word_design.audit(source.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, FileNotFoundError):  # pragma: no cover
        return []
    if not report["themed"]:
        return [{"code": "theme-missing", "count": 1, "examples": []}]
    stray = report["stray_colors"]
    if stray:
        return [{"code": "theme-literal-color", "count": len(stray), "examples": stray[:3]}]
    return []


def format_report(report: dict[str, Any]) -> str:
    """The same result, for someone reading a terminal."""
    lines = [
        f"{report['document']}: {report['score']}/100 "
        f"(worst page {report['worst_page']}, threshold {report['threshold']}) "
        f"{'PASS' if report['pass'] else 'REWORK'}"
    ]
    for page in report["pages"]:
        if not page["findings"]:
            continue
        lines.append(f"\n  page {page['page']} - {page['score']}/100")
        for finding in page["findings"]:
            times = f" x{finding['count']}" if finding["count"] > 1 else ""
            lines.append(f"    [{finding['category']}] {finding['code']}{times}")
            for example in finding["examples"]:
                lines.append(f"        {example}")
            lines.append(f"      -> {finding['fix']}")
    if report["rework"]:
        lines.append(f"\n  pages to rework: {', '.join(str(n) for n in report['rework'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Visual QA for a Word document master")
    parser.add_argument("source", help="The filled document.html")
    parser.add_argument("--json", action="store_true", help="Machine-readable report")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--chromium", help="Path to a Chromium binary")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)

    try:
        report = review(
            Path(args.source),
            chromium=args.chromium,
            threshold=args.threshold,
            timeout=args.timeout,
        )
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Persist the verdict beside the document so html2docx can enforce it:
    # an exit code is easy to ignore, a qa.json on disk is not.
    try:
        qa_path = Path(args.source).expanduser().resolve().parent / "qa.json"
        qa_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - disk full / read-only folder
        print(f"warning: could not write qa.json: {exc}", file=sys.stderr)

    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
