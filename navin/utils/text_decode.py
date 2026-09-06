"""Read text files as the repository actually stores them.

Decoding every file as strict UTF-8 fails two ordinary cases: a Windows editor
that saves with a byte-order mark, and a legacy file still in latin-1 or
cp1252. Both are plain text, so reporting them as binary hides real code from
the model. Both also have to survive a round trip: reading a cp1252 file and
writing it back as UTF-8 silently rewrites every accented character in the
diff, which is a corruption the user never asked for.

`decode` therefore returns the text alongside everything needed to reproduce
the original byte layout, and `encode` puts it back.
"""

from __future__ import annotations

from dataclasses import dataclass

# A byte-order mark identifies the codec exactly, so it is checked before any
# guessing. Longest first: the UTF-32 marks start with the UTF-16 LE mark.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

# Encodings tried when there is no BOM and UTF-8 fails. cp1252 is a superset of
# latin-1 over the printable range, so trying it first recovers curly quotes and
# dashes that latin-1 would turn into control characters.
_FALLBACKS: tuple[str, ...] = ("cp1252", "latin-1")

_SNIFF_BYTES = 8192
_MIN_CONFIDENCE = 0.7


@dataclass(frozen=True)
class DecodedText:
    """Text plus the byte-level details needed to write it back unchanged."""

    text: str
    """Content with the BOM removed and newlines normalised to LF."""

    encoding: str
    """Codec to re-encode with."""

    bom: bytes
    """Byte-order mark to re-emit, or empty."""

    crlf: bool
    """Whether the original used CRLF line endings."""

    @property
    def is_utf8(self) -> bool:
        return self.encoding == "utf-8" and not self.bom


def _looks_binary(raw: bytes) -> bool:
    """A NUL byte outside a UTF-16/32 file is the usual binary give-away."""
    return b"\x00" in raw[:_SNIFF_BYTES]


def _wide_encoding(name: str) -> bool:
    """Whether a codec stores NUL bytes as a matter of course."""
    return name.replace("_", "-").startswith(("utf-16", "utf-32"))


def _decode_wide(raw: bytes) -> DecodedText | None:
    """Try to read NUL-laden bytes as a byte-order-mark-less UTF-16/32 file.

    Such a file is valid UTF-8 as far as the codec is concerned - every NUL is a
    legal codepoint - so accepting the UTF-8 reading would hand back text riddled
    with NULs instead of the actual content. chardet recognises the pattern
    reliably; anything it does not name a wide codec is treated as binary.
    """
    encoding = _sniff_encoding(raw)
    if encoding is None or not _wide_encoding(encoding):
        return None
    try:
        text, crlf = _normalise(raw.decode(encoding))
    except (UnicodeDecodeError, LookupError):
        return None
    return DecodedText(text=text, encoding=encoding, bom=b"", crlf=crlf)


def _normalise(text: str) -> tuple[str, bool]:
    crlf = "\r\n" in text
    return text.replace("\r\n", "\n"), crlf


def _sniff_encoding(raw: bytes) -> str | None:
    """Ask chardet, then fall back to the common single-byte codecs.

    chardet is already a dependency and is far better than guessing at
    distinguishing cp1252 from the ISO-8859 family or from Shift-JIS.
    """
    try:
        import chardet
    except ImportError:
        detected = None
    else:
        guess = chardet.detect(raw[:_SNIFF_BYTES])
        name = (guess.get("encoding") or "").lower()
        confidence = guess.get("confidence") or 0.0
        detected = name if name and confidence >= _MIN_CONFIDENCE else None

    candidates = [detected, *_FALLBACKS] if detected else list(_FALLBACKS)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            raw.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
        return candidate
    return None


def decode(raw: bytes) -> DecodedText | None:
    """Decode file bytes, or return None when the content is really binary."""
    if not raw:
        return DecodedText(text="", encoding="utf-8", bom=b"", crlf=False)

    for mark, encoding in _BOMS:
        if raw.startswith(mark):
            try:
                body = raw[len(mark) :].decode(encoding)
            except UnicodeDecodeError:
                return None
            text, crlf = _normalise(body)
            return DecodedText(text=text, encoding=encoding, bom=mark, crlf=crlf)

    # NUL rules the content out before anything else is tried, because decoding
    # cleanly as UTF-8 proves nothing: NUL is a legal codepoint, so a blob that
    # happens to be valid UTF-8 would be handed to the model as source and
    # searched by grep as text. The one honest exception is a wide encoding
    # written without a byte-order mark.
    if _looks_binary(raw):
        return _decode_wide(raw)

    try:
        text, crlf = _normalise(raw.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    else:
        return DecodedText(text=text, encoding="utf-8", bom=b"", crlf=crlf)

    encoding = _sniff_encoding(raw)
    if encoding is None:
        return None
    text, crlf = _normalise(raw.decode(encoding))
    return DecodedText(text=text, encoding=encoding, bom=b"", crlf=crlf)


def encode(text: str, source: DecodedText | None = None) -> bytes:
    """Re-encode edited text in the original file's byte layout.

    Passing the `DecodedText` the content was read from keeps a cp1252 file in
    cp1252 and keeps a BOM where the editor that owns the file expects one. When
    the new text contains characters the original codec cannot represent, UTF-8
    is used instead: losing a character is worse than changing an encoding.
    """
    if source is None:
        return text.encode("utf-8")
    body = text.replace("\n", "\r\n") if source.crlf else text
    try:
        return source.bom + body.encode(source.encoding)
    except UnicodeEncodeError:
        return body.encode("utf-8")
