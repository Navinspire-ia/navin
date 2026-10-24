# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Offline search in Navin's user documentation, for the Live voice.

When someone asks the Live "how do I...", "what is...", the voice model calls
the ``navin_docs`` tool, which lands here. The public docs ship inside the
package (``navin/resources/docs``, see hatch_build.py); a source checkout reads
``site/front/content/docs`` and ``docs/`` directly.

Plain keyword scoring over sections (one per heading): fast, no network, no
index file, good enough to put the right page in front of the model.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PUBLIC_DOCS_URL = "https://navin.live/en/docs"
MAX_SECTION_CHARS = 1400

_STOP = frozenset(
    """a an and are as at be by can comment de des do does du en est et for from how i il in is it
    je la le les mon my of on ou pour que qui quoi sur the to un une what when where which why with
    avec dans est-ce faire fait me moi navin peut puis-je ce cette ces son sa ses vous tu""".split()
)


@dataclass(frozen=True)
class Section:
    path: str
    title: str
    heading: str
    text: str
    terms: Counter


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9_.+-]*", _fold(text)) if w not in _STOP and len(w) > 1]


def docs_roots() -> list[Path]:
    package = Path(__file__).resolve().parent.parent
    repo = package.parent
    candidates = [package / "resources" / "docs", repo / "site" / "front" / "content" / "docs", repo / "docs"]
    return [path for path in candidates if path.is_dir()]


def _sections(root: Path) -> list[Section]:
    out: list[Section] = []
    for file in sorted(root.rglob("*.md")):
        rel = file.relative_to(root).as_posix()
        if rel.startswith(("audits/", "md/")):
            continue
        try:
            raw = file.read_text(encoding="utf-8")
        except OSError:
            continue
        title_match = re.search(r"^#\s+(.+)$", raw, flags=re.M)
        title = title_match.group(1).strip() if title_match else rel
        parts = re.split(r"^(#{1,3}\s+.+)$", raw, flags=re.M)
        heading = title
        buffer = parts[0]
        chunks: list[tuple[str, str]] = []
        for part in parts[1:]:
            if re.match(r"^#{1,3}\s+", part):
                if buffer.strip():
                    chunks.append((heading, buffer))
                heading = part.lstrip("#").strip()
                buffer = ""
            else:
                buffer += part
        if buffer.strip():
            chunks.append((heading, buffer))
        for head, body in chunks:
            text = re.sub(r"\n{3,}", "\n\n", body).strip()
            terms = Counter(_terms(f"{title} {head} {head} {text}"))
            out.append(Section(rel, title, head, text, terms))
    return out


@lru_cache(maxsize=1)
def _index() -> tuple[tuple[Section, ...], dict[str, float]]:
    sections: list[Section] = []
    seen: set[str] = set()
    for root in docs_roots():
        for section in _sections(root):
            key = f"{section.path}#{section.heading}"
            if key in seen:  # the same page in the package and the checkout
                continue
            seen.add(key)
            sections.append(section)
    df: Counter = Counter()
    for section in sections:
        df.update(set(section.terms))
    total = max(1, len(sections))
    idf = {term: math.log(1 + total / count) for term, count in df.items()}
    return tuple(sections), idf


def search_docs(query: str, limit: int = 4) -> list[dict[str, str]]:
    """The best matching doc sections for *query* (title, page URL, excerpt)."""
    terms = _terms(query)
    if not terms:
        return []
    sections, idf = _index()
    scored: list[tuple[float, Section]] = []
    for section in sections:
        score = 0.0
        for term in terms:
            tf = section.terms.get(term, 0)
            if tf:
                score += (1 + math.log(tf)) * idf.get(term, 0.0)
        heading = _fold(f"{section.title} {section.heading}")
        score += sum(1.5 for term in terms if term in heading)
        if score > 0:
            scored.append((score, section))
    scored.sort(key=lambda item: item[0], reverse=True)
    results: list[dict[str, str]] = []
    for _, section in scored[:limit]:
        page = section.path.removesuffix(".md")
        results.append({
            "title": section.title,
            "section": section.heading,
            "url": f"{PUBLIC_DOCS_URL}/{page}",
            "excerpt": section.text[:MAX_SECTION_CHARS],
        })
    return results
