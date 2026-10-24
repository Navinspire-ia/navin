# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Word-by-word animated captions for montage output.

Social cuts are watched muted, so captions carry the message. Plain SRT lines
look like an afterthought; marketing captions show a few words at a time, pop
in, and highlight the word being spoken in the brand color. This module turns
narration segments (with word timings from the TTS provider, from alignment,
or estimated from syllables) into an ASS file that libass burns in:

- ``highlight`` mode (default) writes one event per spoken word, the current
  word colored and slightly scaled, so the highlight jumps exactly on the word;
- ``karaoke`` mode writes one event per group with ``\\kf`` sweeps;
- the style comes from the brand kit (fonts, colors), with readable fallbacks
  when the brand text color would vanish over footage;
- margins follow the platform safe area for 16:9, 9:16 and 1:1, so captions
  stay clear of the TikTok/Reels/Shorts UI.

Brand kit contract (shared with the other montage phases)::

    {"name", "logo_path", "colors": {"primary", "secondary", "accent",
     "background", "text"}, "fonts": {"heading", "body"}, "font_files"?}
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODE_HIGHLIGHT = "highlight"
MODE_KARAOKE = "karaoke"
CAPTION_MODES = frozenset({MODE_HIGHLIGHT, MODE_KARAOKE})

DEFAULT_FONT = "Arial"
DEFAULT_TEXT = "#FFFFFF"
DEFAULT_HIGHLIGHT = "#FFD400"
DEFAULT_OUTLINE = "#000000"

_VOWELS = "aeiouyàâäáãåæéèêëíìîïóòôöõøœúùûüýÿ"
_VOWEL_GROUP_RE = re.compile(f"[{_VOWELS}]+", re.IGNORECASE)
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_SENTENCE_END = (".", "!", "?", "…")
_CLAUSE_END = (",", ";", ":")


class CaptionError(ValueError):
    """Invalid caption request."""


@dataclass(frozen=True, slots=True)
class CaptionWord:
    """One spoken word on the timeline, in seconds."""

    text: str
    start: float
    end: float

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3)}


@dataclass(frozen=True, slots=True)
class CaptionSegment:
    """One narration segment (usually one scene) with absolute word timings."""

    text: str
    start: float
    end: float
    words: tuple[CaptionWord, ...] = ()


@dataclass(frozen=True, slots=True)
class CaptionStyle:
    """Resolved look of the captions, in ASS pixel units (PlayRes = canvas)."""

    font: str = DEFAULT_FONT
    font_size: int = 72
    text_color: str = DEFAULT_TEXT
    highlight_color: str = DEFAULT_HIGHLIGHT
    outline_color: str = DEFAULT_OUTLINE
    outline: float = 5.0
    shadow: float = 2.0
    bold: bool = True
    uppercase: bool = False
    margin_l: int = 80
    margin_r: int = 80
    margin_v: int = 120
    max_words: int = 3
    max_chars: int = 18
    highlight_scale: int = 112


@dataclass(slots=True)
class CaptionResult:
    """What :func:`build_captions` wrote, ready for the assemble step."""

    path: str
    fonts_dir: str | None
    style: CaptionStyle
    groups: int
    events: int
    segments: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "fonts_dir": self.fonts_dir,
            "groups": self.groups,
            "events": self.events,
            "font": self.style.font,
            "segments": self.segments,
        }


# --------------------------------------------------------------------------
# Word timing estimate (used when neither the provider nor alignment gave any)
# --------------------------------------------------------------------------


def split_words(text: str) -> list[str]:
    """Whitespace tokens of *text*, punctuation kept on the word it follows."""
    return [token for token in (text or "").split() if token.strip()]


def count_syllables(word: str) -> int:
    """Rough syllable count: vowel groups for Latin scripts, letters otherwise.

    Good enough to weight a proportional estimate; it is never shown to anyone.
    Digits read as roughly one syllable each, Arabic and other abjads as one
    syllable per two letters.
    """
    core = "".join(ch for ch in word if ch.isalnum())
    if not core:
        return 0
    digits = sum(ch.isdigit() for ch in core)
    letters = "".join(ch for ch in core if not ch.isdigit())
    count = digits
    if letters:
        if all(_is_latin(ch) for ch in letters):
            groups = len(_VOWEL_GROUP_RE.findall(letters))
            # A silent final "e" (French/English) rarely adds a syllable.
            if groups > 1 and letters.lower().endswith("e") and not letters.lower().endswith("ee"):
                groups -= 1
            count += max(1, groups)
        else:
            count += max(1, round(len(letters) / 2))
    return max(1, count)


def _is_latin(ch: str) -> bool:
    try:
        return "LATIN" in unicodedata.name(ch)
    except ValueError:
        return False


def _pause_after(word: str) -> float:
    """Pause weight in syllables that punctuation adds after *word*."""
    stripped = word.rstrip("\"'»)”’")
    if stripped.endswith(_SENTENCE_END):
        return 1.4
    if stripped.endswith(_CLAUSE_END):
        return 0.7
    return 0.0


def estimate_word_timings(
    text: str, start: float, end: float, *, lead_in: float = 0.0
) -> list[CaptionWord]:
    """Spread the words of *text* over ``[start, end]`` by syllable weight.

    Punctuation adds a silent gap after its word, which is where real speech
    pauses, so the highlight does not drift ahead at every comma.
    """
    words = split_words(text)
    if not words:
        return []
    begin = float(start) + max(0.0, lead_in)
    span = max(0.0, float(end) - begin)
    weights = [max(1, count_syllables(word)) for word in words]
    pauses = [_pause_after(word) for word in words[:-1]] + [0.0]
    total = sum(weights) + sum(pauses)
    if total <= 0 or span <= 0:
        return [CaptionWord(word, begin, begin) for word in words]
    unit = span / total
    out: list[CaptionWord] = []
    cursor = begin
    for word, weight, pause in zip(words, weights, pauses, strict=True):
        word_end = cursor + weight * unit
        out.append(CaptionWord(word, cursor, word_end))
        cursor = word_end + pause * unit
    # Pin the last word on the segment end so rounding never leaves a tail.
    last = out[-1]
    out[-1] = CaptionWord(last.text, last.start, float(end))
    return out


def _norm_token(word: str) -> str:
    folded = unicodedata.normalize("NFKD", word.lower())
    return "".join(ch for ch in folded if ch.isalnum())


def align_script_words(
    text: str,
    recognized: Sequence[Mapping[str, Any] | CaptionWord],
    start: float,
    end: float,
) -> list[CaptionWord]:
    """Forced alignment: put the *script* words on recognized word timings.

    The transcription of synthesized speech is close to the script but not
    identical (numbers, brand names, punctuation), so captions must keep the
    script text and only borrow the timings. Matching tokens take the
    recognized times; unmatched runs are spread by syllables over the gap
    between their matched neighbours. Times are relative to the audio and
    offset by *start*.
    """
    words = split_words(text)
    if not words:
        return []
    heard: list[CaptionWord] = []
    for item in recognized:
        if isinstance(item, CaptionWord):
            heard.append(item)
        elif isinstance(item, Mapping):
            label = str(item.get("text") or item.get("word") or "").strip()
            if label:
                heard.append(
                    CaptionWord(label, _float(item.get("start"), 0.0), _float(item.get("end"), 0.0))
                )
    if not heard:
        return estimate_word_timings(text, start, end)
    import difflib

    script_tokens = [_norm_token(word) for word in words]
    heard_tokens = [_norm_token(word.text) for word in heard]
    matcher = difflib.SequenceMatcher(a=script_tokens, b=heard_tokens, autojunk=False)
    timed: list[tuple[float, float] | None] = [None] * len(words)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            source = heard[block.b + offset]
            timed[block.a + offset] = (start + source.start, start + source.end)
    out: list[CaptionWord] = []
    index = 0
    while index < len(words):
        if timed[index] is not None:
            w_start, w_end = timed[index]  # type: ignore[misc]
            out.append(CaptionWord(words[index], w_start, w_end))
            index += 1
            continue
        run_end = index
        while run_end < len(words) and timed[run_end] is None:
            run_end += 1
        gap_start = out[-1].end if out else float(start)
        gap_end = timed[run_end][0] if run_end < len(words) else float(end)  # type: ignore[index]
        if gap_end <= gap_start:
            gap_end = gap_start + 0.08 * (run_end - index)
        out += estimate_word_timings(" ".join(words[index:run_end]), gap_start, gap_end)
        index = run_end
    return out


# --------------------------------------------------------------------------
# Segment coercion
# --------------------------------------------------------------------------


def coerce_segments(segments: Iterable[Any]) -> list[CaptionSegment]:
    """Accept :class:`CaptionSegment` objects or plain dicts.

    A dict needs ``text`` plus timing: ``start``/``end``, or ``start`` and
    ``duration``/``duration_s``. Without ``start`` it follows the previous
    segment, so the output of ``generate_speech(segments=...)`` can be passed
    as is for a back-to-back narration. ``words`` are absolute timeline
    seconds unless the dict sets ``words_relative`` (the speech tool does),
    in which case they are offset by the segment start. Missing words are
    estimated from syllables.
    """
    out: list[CaptionSegment] = []
    cursor = 0.0
    for raw in segments:
        if isinstance(raw, CaptionSegment):
            segment = raw
        elif isinstance(raw, Mapping):
            segment = _segment_from_mapping(raw, cursor)
        else:
            raise CaptionError("each caption segment must be an object with text and timing")
        if segment.end <= segment.start:
            raise CaptionError(f"caption segment ends before it starts: {segment.text[:40]!r}")
        if not segment.words and segment.text.strip():
            segment = CaptionSegment(
                segment.text,
                segment.start,
                segment.end,
                tuple(estimate_word_timings(segment.text, segment.start, segment.end)),
            )
        out.append(segment)
        cursor = segment.end
    return out


def _segment_from_mapping(raw: Mapping[str, Any], cursor: float) -> CaptionSegment:
    text = str(raw.get("text") or "").strip()
    start = _float(raw.get("start"), cursor)
    end_value = raw.get("end")
    if end_value is not None:
        end = _float(end_value, start)
    else:
        duration = _float(raw.get("duration_s", raw.get("duration")), 0.0)
        if duration <= 0:
            raise CaptionError(f"caption segment needs end or duration: {text[:40]!r}")
        end = start + duration
    offset = start if raw.get("words_relative") else 0.0
    words: list[CaptionWord] = []
    for item in raw.get("words") or ():
        if isinstance(item, CaptionWord):
            words.append(CaptionWord(item.text, item.start + offset, item.end + offset))
            continue
        if not isinstance(item, Mapping):
            continue
        word = str(item.get("text") or item.get("word") or "").strip()
        if not word:
            continue
        w_start = _float(item.get("start"), 0.0) + offset
        w_end = _float(item.get("end"), w_start - offset) + offset
        words.append(CaptionWord(word, w_start, max(w_start, w_end)))
    if not text:
        text = " ".join(word.text for word in words)
    return CaptionSegment(text, start, end, tuple(words))


def _float(value: Any, default: float) -> float:
    try:
        return float(value) if value is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


# --------------------------------------------------------------------------
# Style: brand kit + safe area
# --------------------------------------------------------------------------


def aspect_family(width: int, height: int) -> str:
    """``vertical`` (9:16 and taller), ``square`` (around 1:1) or ``wide``."""
    ratio = width / height if height else 1.0
    if ratio < 0.8:
        return "vertical"
    if ratio <= 1.25:
        return "square"
    return "wide"


#: Safe area per aspect family, as canvas fractions: side margin, bottom
#: margin (caption baseline zone), font size, words and chars per caption.
#: The vertical bottom margin keeps captions above the Reels/TikTok/Shorts
#: action bar and description, the side margins clear the right-hand buttons.
_SAFE_AREA: dict[str, dict[str, float]] = {
    "vertical": {"side": 0.09, "bottom": 0.24, "size": 0.040, "words": 3, "chars": 18},
    "square": {"side": 0.08, "bottom": 0.12, "size": 0.055, "words": 4, "chars": 22},
    "wide": {"side": 0.10, "bottom": 0.08, "size": 0.058, "words": 6, "chars": 34},
}


def caption_style(
    width: int,
    height: int,
    brand: Mapping[str, Any] | None = None,
    *,
    uppercase: bool = False,
    max_words: int | None = None,
    font_scale: float = 1.0,
) -> CaptionStyle:
    """Style for a ``width x height`` canvas, branded when *brand* is given."""
    if width <= 0 or height <= 0:
        raise CaptionError("caption canvas must have a positive size")
    area = _SAFE_AREA[aspect_family(width, height)]
    colors = _mapping((brand or {}).get("colors"))
    fonts = _mapping((brand or {}).get("fonts"))
    font = str(fonts.get("heading") or fonts.get("body") or DEFAULT_FONT).strip() or DEFAULT_FONT

    text_color = _hex_or(colors.get("text"), DEFAULT_TEXT)
    # Brand text colors are usually designed for a light page; over footage a
    # dark fill disappears, so captions keep a light fill in that case.
    if _luminance(text_color) < 0.6:
        text_color = DEFAULT_TEXT
    highlight = _hex_or(colors.get("accent") or colors.get("primary"), DEFAULT_HIGHLIGHT)
    if _contrast(highlight, text_color) < 1.25:
        highlight = _hex_or(colors.get("primary") or colors.get("secondary"), DEFAULT_HIGHLIGHT)
        if _contrast(highlight, text_color) < 1.25:
            highlight = DEFAULT_HIGHLIGHT
    background = _hex_or(colors.get("background"), DEFAULT_OUTLINE)
    outline = background if _luminance(background) < 0.25 else DEFAULT_OUTLINE

    short_side = min(width, height)
    size = max(16, round(height * area["size"] * max(0.5, min(2.0, font_scale))))
    if aspect_family(width, height) == "wide":
        size = max(16, round(short_side * area["size"] * max(0.5, min(2.0, font_scale))))
    return CaptionStyle(
        font=font,
        font_size=size,
        text_color=text_color,
        highlight_color=highlight,
        outline_color=outline,
        outline=round(max(2.0, size * 0.07), 1),
        shadow=round(max(1.0, size * 0.025), 1),
        uppercase=uppercase,
        margin_l=round(width * area["side"]),
        margin_r=round(width * area["side"]),
        margin_v=round(height * area["bottom"]),
        max_words=max(1, int(max_words or area["words"])),
        max_chars=int(area["chars"]),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _hex_or(value: Any, default: str) -> str:
    match = _HEX_RE.match(str(value or "").strip())
    return f"#{match.group(1).upper()}" if match else default


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _luminance(color: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ass_color(color: str, alpha: int = 0) -> str:
    """``#RRGGBB`` to ASS ``&HAABBGGRR``."""
    r, g, b = _rgb(_hex_or(color, DEFAULT_TEXT))
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def ass_time(seconds: float) -> str:
    """ASS timestamp ``H:MM:SS.cc``."""
    total_cs = max(0, round(float(seconds) * 100))
    hours, rest = divmod(total_cs, 360_000)
    minutes, rest = divmod(rest, 6000)
    secs, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_text(text: str) -> str:
    # ASS has no brace escape; swap them so text never opens an override block.
    return (
        text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")
    )


# --------------------------------------------------------------------------
# Grouping and event generation
# --------------------------------------------------------------------------


def group_words(words: Sequence[CaptionWord], style: CaptionStyle) -> list[list[CaptionWord]]:
    """Cut a segment's words into short caption groups.

    A group closes on the word budget, on the character budget, or after a
    sentence end, so a caption never straddles two sentences.
    """
    groups: list[list[CaptionWord]] = []
    current: list[CaptionWord] = []
    chars = 0
    for word in words:
        length = len(word.text)
        if current and (
            len(current) >= style.max_words or chars + 1 + length > style.max_chars
        ):
            groups.append(current)
            current, chars = [], 0
        current.append(word)
        chars += length + (1 if chars else 0)
        if word.text.rstrip("\"'»)”’").endswith(_SENTENCE_END):
            groups.append(current)
            current, chars = [], 0
    if current:
        groups.append(current)
    return groups


def _display(word: str, style: CaptionStyle) -> str:
    text = _ass_text(word)
    return text.upper() if style.uppercase else text


def _group_events(
    group: Sequence[CaptionWord],
    group_end: float,
    style: CaptionStyle,
    mode: str,
) -> list[tuple[float, float, str]]:
    """``(start, end, text)`` events for one caption group."""
    highlight = ass_color(style.highlight_color)
    start = group[0].start
    # Each group fades in. \fad is a line-level tag, so the per-word {\r}
    # resets below never cancel it.
    pop = "{\\fad(70,0)}"
    if mode == MODE_KARAOKE:
        parts: list[str] = []
        cursor = start
        for index, word in enumerate(group):
            nxt = group[index + 1].start if index + 1 < len(group) else group_end
            cs = max(1, round((max(nxt, word.end) - cursor) * 100))
            parts.append(f"{{\\kf{cs}}}{_display(word.text, style)}")
            cursor = max(nxt, word.end)
        return [(start, group_end, pop + " ".join(parts))]
    events: list[tuple[float, float, str]] = []
    scale = style.highlight_scale
    for index, word in enumerate(group):
        ev_start = start if index == 0 else word.start
        ev_end = group[index + 1].start if index + 1 < len(group) else group_end
        if ev_end <= ev_start:
            continue
        pieces = []
        for other_index, other in enumerate(group):
            shown = _display(other.text, style)
            if other_index == index:
                # Every event starts on its word, so this \t bounce lands on
                # the exact moment the word is spoken.
                pieces.append(
                    f"{{\\c{highlight}\\t(0,90,\\fscx{scale}\\fscy{scale})}}{shown}{{\\r}}"
                )
            else:
                pieces.append(shown)
        prefix = pop if index == 0 else ""
        if index == len(group) - 1:
            prefix += "{\\fad(0,90)}" if index else ""
        events.append((ev_start, ev_end, prefix + " ".join(pieces)))
    return events


def render_ass(
    segments: Sequence[CaptionSegment],
    style: CaptionStyle,
    width: int,
    height: int,
    *,
    mode: str = MODE_HIGHLIGHT,
    title: str = "Navin captions",
) -> tuple[str, int, int]:
    """ASS document for *segments*; returns ``(text, groups, events)``."""
    if mode not in CAPTION_MODES:
        raise CaptionError(f"unknown caption mode {mode!r}; use highlight or karaoke")
    header = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    karaoke = mode == MODE_KARAOKE
    primary = ass_color(style.highlight_color if karaoke else style.text_color)
    secondary = ass_color(style.text_color if karaoke else style.highlight_color)
    header.append(
        "Style: Caption,"
        f"{style.font},{style.font_size},{primary},{secondary},"
        f"{ass_color(style.outline_color)},{ass_color(style.outline_color, 0x80)},"
        f"{-1 if style.bold else 0},0,0,0,100,100,0,0,1,"
        f"{style.outline},{style.shadow},2,{style.margin_l},{style.margin_r},"
        f"{style.margin_v},1"
    )
    header += [
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines: list[str] = []
    group_count = 0
    for segment in segments:
        words = [w for w in segment.words if w.text.strip()]
        if not words:
            continue
        # Clamp to the segment so a provider timing that overshoots never
        # bleeds into the next scene.
        words = [
            CaptionWord(
                w.text,
                min(max(w.start, segment.start), segment.end),
                min(max(w.end, segment.start), segment.end),
            )
            for w in words
        ]
        groups = group_words(words, style)
        for position, group in enumerate(groups):
            if position + 1 < len(groups):
                group_end = groups[position + 1][0].start
            else:
                group_end = segment.end
            group_end = max(group_end, group[-1].end)
            if position == 0:
                group = [CaptionWord(group[0].text, segment.start, group[0].end), *group[1:]]
            group_count += 1
            for start, end, text in _group_events(group, group_end, style, mode):
                lines.append(
                    f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{text}"
                )
    return "\n".join(header + lines) + "\n", group_count, len(lines)


def stage_fonts(brand: Mapping[str, Any] | None, target_dir: Path) -> str | None:
    """Copy brand font files into *target_dir* for libass ``fontsdir``.

    A dedicated folder keeps the render deterministic: libass only sees the
    brand fonts plus system fonts, never whatever sits next to a font file.
    """
    files = (brand or {}).get("font_files") or []
    if isinstance(files, (str, Path)):
        files = [files]
    staged = 0
    for raw in files:
        source = Path(str(raw)).expanduser()
        if not source.is_file() or source.suffix.lower() not in {".ttf", ".otf", ".ttc", ".woff2", ".woff"}:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / source.name
        if not destination.exists():
            shutil.copy2(source, destination)
        staged += 1
    return str(target_dir) if staged else None


def build_captions(
    segments: Iterable[Any],
    output: str | Path,
    *,
    width: int = 1080,
    height: int = 1920,
    brand: Mapping[str, Any] | None = None,
    mode: str = MODE_HIGHLIGHT,
    uppercase: bool = False,
    max_words: int | None = None,
    font_scale: float = 1.0,
) -> CaptionResult:
    """Write a styled, word-animated ASS caption file for *segments*.

    Returns the file path, the brand ``fonts_dir`` to pass to libass (or
    ``None`` when no brand font files were given), and counts. The result
    plugs into ``assemble(..., captions=result)`` or ``AssembleSpec(subtitles=
    result.path, fonts_dir=result.fonts_dir)``.
    """
    coerced = coerce_segments(segments)
    if not coerced:
        raise CaptionError("captions need at least one narration segment")
    style = caption_style(
        width, height, brand, uppercase=uppercase, max_words=max_words, font_scale=font_scale
    )
    text, groups, events = render_ass(coerced, style, width, height, mode=mode)
    path = Path(output).expanduser()
    if path.suffix.lower() != ".ass":
        path = path.with_suffix(".ass")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    fonts_dir = stage_fonts(brand, path.with_name(f"{path.stem}_fonts"))
    return CaptionResult(
        path=str(path),
        fonts_dir=fonts_dir,
        style=style,
        groups=groups,
        events=events,
        segments=[
            {
                "text": segment.text,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "words": [word.to_dict() for word in segment.words],
            }
            for segment in coerced
        ],
    )


def parse_ass_events(text: str) -> list[tuple[float, float, str]]:
    """``(start, end, text)`` of every Dialogue line; used by tests and QA."""
    out: list[tuple[float, float, str]] = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        out.append((_parse_ass_time(fields[1]), _parse_ass_time(fields[2]), fields[9]))
    return out


def _parse_ass_time(value: str) -> float:
    hours, minutes, seconds = value.strip().split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
