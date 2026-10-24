# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live voice for the terminal: the same call as the desktop Live mode.

The microphone is read locally and nothing leaves the machine until the user
speaks (energy voice detection with an adaptive noise floor). Each utterance
is transcribed with the configured speech-to-text engine and sent as a voice
turn, so the agent gets the same "expert developer on a call" brief as the
desktop. What the agent says (progress, questions, approvals, the answer) is
synthesized with the configured text-to-speech engine and played; speaking
over it cuts it off.

Audio needs no Python dependency: ``sounddevice`` is used when installed,
otherwise the platform recorder (``parecord`` on Linux and WSL, ``ffmpeg`` on
macOS) and player (``ffplay``, ``afplay``, ``paplay``).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import wave
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.utils.proc import no_window_kwargs

# The terminal Live is switched off for now (the desktop Live stays on): no
# /live command, no footer chip. Flip to True to bring it back.
CLI_LIVE_ENABLED = False


def cli_live_enabled() -> bool:
    return CLI_LIVE_ENABLED


SAMPLE_RATE = 16_000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2  # s16le mono

# Voice detection, in 30 ms frames.
_START_FRAMES = 3  # ~90 ms above the threshold opens an utterance
_END_FRAMES = 26  # ~780 ms of quiet closes it
_PREROLL_FRAMES = 10  # keep ~300 ms before the trigger (first syllable)
_MIN_SPEECH_FRAMES = 12  # shorter is a click or a breath
_MAX_UTTERANCE_S = 30
_MIN_THRESHOLD = 0.012
_CALIBRATION_FRAMES = 33  # ~1 s of room tone before anything can trigger
_FLOOR_WINDOW = 100  # ~3 s of recent levels
_FLOOR_PERCENTILE = 0.2
_FLOOR_FACTOR = 2.8
# The speaker is heard by the microphone: while the agent talks, only a clearly
# louder voice counts as the user cutting in.
_PLAYBACK_FACTOR = 3.2

MAX_SPOKEN_CHARS = 900


# -- what to say and how to read answers -------------------------------------


_FENCED = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_LIST = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+", re.M)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)([\s\S]*?\S)\1")


def speech_text(markdown: str, *, code_note: str = "", limit: int = MAX_SPOKEN_CHARS) -> str:
    """Plain spoken prose from a Markdown reply: no code, URLs or table rows."""
    text = markdown or ""
    had_code = bool(_FENCED.search(text))
    text = _FENCED.sub(" ", text)
    text = _TABLE_ROW.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _HEADING.sub("", text)
    text = _LIST.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = re.sub(r"[>#|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if had_code and code_note:
        text = f"{text} {code_note}".strip()
    if len(text) > limit:
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = cut[: end + 1] if end > limit // 2 else cut.rstrip() + "..."
    return text


def normalize_speech(text: str) -> str:
    folded = unicodedata.normalize("NFD", text or "")
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", folded)).strip()


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_FIRST_CHUNK_CHARS = 160
_CHUNK_CHARS = 320
_ECHO_WINDOW_S = 30.0
_ECHO_OVERLAP = 0.6


def speech_chunks(text: str) -> list[str]:
    """Sentences grouped into short clips: the first one is small so the voice
    starts fast, the next ones are synthesized while the previous one plays."""
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split((text or "").strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        limit = _FIRST_CHUNK_CHARS if not chunks else _CHUNK_CHARS
        if current and len(current) + 1 + len(sentence) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def is_own_echo(heard: str, spoken: list[str]) -> bool:
    """The microphone caught the agent's own voice: most of the heard words
    come from something it just said."""
    words = normalize_speech(heard).split()
    if len(words) < 2:
        return False
    for said in spoken:
        said_words = set(normalize_speech(said).split())
        if said_words and sum(word in said_words for word in words) / len(words) >= _ECHO_OVERLAP:
            return True
    return False


# -- plans, reviews and reports: offered as a summary, never read out -------

_FENCED_BLOCK = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_TOP_ITEM = re.compile(r"^ {0,3}(?:\d{1,3}[.)]|[-*+•])\s+\S", re.M)
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+(.+)$", re.M)
_DIGEST_KINDS = (
    ("security", re.compile(r"\b(?:s[ée]curit[ée]|security|vuln[ée]rabilit|vulnerab|owasp|cve-\d|injection|xss|csrf|secrets?\b)", re.I)),
    ("review", re.compile(r"\b(?:revue|review|relecture|code review|findings?|constats?)\b", re.I)),
    ("plan", re.compile(r"\b(?:plan|[ée]tapes?|steps?|roadmap|blueprint|phases?|milestones?|jalons?)\b", re.I)),
    ("recommendation", re.compile(r"\b(?:recommand|recommend|suggestions?|conseils?|next steps|prochaines [ée]tapes)", re.I)),
)
DIGEST_MIN_CHARS = 700
_OFFER_YES = re.compile(
    r"^(?:oui|ouais|ok|okay|d accord|vas y|go|yes|yeah|yep|sure|please|s il te plait|volontiers|carrement"
    r"|bien sur|resume(?: moi)?|explique(?: moi)?|summari[sz]e|tell me)(?: |$)"
)
_OFFER_NO = re.compile(r"^(?:non|nan|no|nope|pas besoin|pas maintenant|plus tard|c est bon|not now|no thanks)(?: |$)")


def live_digest(content: str | None) -> tuple[str, int] | None:
    """(kind, top-level items) for a plan, review, security report,
    recommendations or a long answer; None for a message read as it is.
    Same rules as the desktop (webui/src/lib/live-digest.ts)."""
    text = _FENCED_BLOCK.sub(" ", content or "")
    plain = re.sub(r"\s+", " ", text).strip()
    if not plain:
        return None
    count = len(_TOP_ITEM.findall(text))
    headings = " \n ".join(_HEADING_LINE.findall(text))
    signature = f"{headings} \n {plain[:280]}"
    kind = next((name for name, pattern in _DIGEST_KINDS if pattern.search(signature)), "long")
    structured = kind != "long" and count >= 3
    if not structured and len(plain) < DIGEST_MIN_CHARS and count < 5:
        return None
    return (kind, count) if structured else ("long", 0)


def offer_answer(text: str) -> str | None:
    """"yes" / "no" to a spoken offer, None for anything else."""
    words = normalize_speech(text)
    if _OFFER_NO.search(words):
        return "no"
    if _OFFER_YES.search(words):
        return "yes"
    return None


_STOP = re.compile(
    r"^(?:stop|arrete(?: toi)?|annule(?: la tache)?|cancel(?: the task)?|stop working)"
    r"(?: (?:please|s il te plait|s il vous plait))?$"
)


# Same filters as the desktop (webui/src/lib/live-voice.ts): Whisper-family
# models invent these on silence or breath, and a lone "hmm" is not a turn.
_STT_NOISE = re.compile(
    r"^(?:sous titres? (?:realises? )?par (?:la communaute )?(?:d )?amara ?org|thank you|thanks for watching"
    r"|merci d avoir regarde|you|bye)$"
)
_ACK_WORDS = frozenset({
    "yeah", "yes", "yep", "no", "nope", "ok", "okay", "oh", "ah", "uh", "um", "hmm", "hm", "mm", "mhm",
    "oui", "non", "ouais", "euh", "hum", "bah", "ben", "bon", "ca", "so", "and", "et", "voila",
    "merci", "thanks", "d", "accord", "right", "sure", "cool", "super", "great", "nice", "hein",
})


def transcript_is_meaningful(text: str, *, answer_expected: bool = False) -> bool:
    """Not an STT hallucination, not a stray syllable, not a bare "hmm"
    (unless a question is on the table: then "oui" is the answer)."""
    normalized = normalize_speech(text)
    if not normalized or _STT_NOISE.match(normalized):
        return False
    if len(re.sub(r"[^a-z0-9]", "", normalized)) < 2:
        return False
    words = normalized.split()
    if not answer_expected and len(words) <= 3 and all(word in _ACK_WORDS for word in words):
        return False
    return True


def is_spoken_stop(text: str) -> bool:
    return bool(_STOP.match(normalize_speech(text)))


# Same rules as the desktop (webui/src/lib/live-voice.ts): any negation or
# hesitation refuses, an unclear answer is asked again, never guessed.
_IDIOM_YES = re.compile(
    r"\b(?:pas de (?:souci|soucis|probleme|problemes)|sans (?:souci|probleme)"
    r"|no (?:problem|worries)|why not|pourquoi pas)\b"
)
_DENY = re.compile(
    r"\b(?:non|no|nope|pas|refuse|refuses|refuser|deny|denied|dont|don t|never|jamais"
    r"|attends|attend|wait|hold|surtout|negatif|negative)\b"
)
_ALLOW = re.compile(
    r"\b(?:oui|yes|yeah|yep|ouais|ok|okay|d accord|vas y|va y|go|go ahead|valide|valides"
    r"|je valide|approuve|j approuve|approve|approved|accepte|j accepte|accept|autorise"
    r"|allow|confirme|confirm|fais le|do it|lance|lance le|c est bon|bien sur|sure"
    r"|absolument|evidemment|parfait|carrement)\b"
)


def approval_from_speech(text: str) -> str | None:
    """``"allow"``, ``"deny"`` or None when the answer is neither."""
    normalized = _IDIOM_YES.sub("ok", normalize_speech(text))
    if not normalized:
        return None
    if _DENY.search(normalized):
        return "deny"
    if len(normalized.split()) > 8:
        return None
    return "allow" if _ALLOW.search(normalized) else None


_ORDINALS = (
    (re.compile(r"\b(?:premier|premiere|first|un|une|one|1)\b"), 0),
    (re.compile(r"\b(?:deuxieme|second|seconde|two|deux|2)\b"), 1),
    (re.compile(r"\b(?:troisieme|third|three|trois|3)\b"), 2),
    (re.compile(r"\b(?:quatrieme|fourth|four|quatre|4)\b"), 3),
)
_SKIP = re.compile(r"\b(?:comme tu veux|comme vous voulez|as you prefer|you choose|peu importe|whatever|skip|passe)\b")


def choice_from_speech(text: str, options: list[dict[str, Any]]) -> tuple[str, str]:
    """``("option", id)``, ``("skip", "")`` or ``("other", text)`` for a spoken pick."""
    normalized = normalize_speech(text)
    words = normalized.split()
    ids = [str(option.get("id") or "") for option in options]
    letter = re.fullmatch(r"(?:option |choix |la |le )?([a-d])", normalized)
    if letter:
        index = ord(letter.group(1)) - ord("a")
        if index < len(ids):
            return "option", ids[index]
    if len(words) <= 4:
        if re.search(r"\b(?:dernier|derniere|last)\b", normalized) and ids:
            return "option", ids[-1]
        for pattern, index in _ORDINALS:
            if pattern.search(normalized) and index < len(ids):
                return "option", ids[index]
    if len(words) <= 6 and _SKIP.search(normalized):
        return "skip", ""
    for option, option_id in zip(options, ids):
        label = normalize_speech(str(option.get("label") or ""))
        if label and (label == normalized or (len(label) > 3 and label in normalized)):
            return "option", option_id
    return "other", text.strip()


def speech_for_approval(action: str, detail: str, *, title: str, prompt: str) -> str:
    detail = (detail or "").strip()
    if len(detail) > 160:
        detail = detail[:160].rstrip() + "..."
    parts = [f"{title}: {action.strip().rstrip('.')}."]
    if detail:
        parts.append(detail.rstrip(".") + ".")
    parts.append(prompt)
    return " ".join(parts)


def speech_for_choice(question: str, options: list[dict[str, Any]], *, other: str) -> str:
    parts = [question.strip()]
    for index, option in enumerate(options):
        parts.append(f"{chr(65 + index)}: {str(option.get('label') or '').strip()}.")
    parts.append(other)
    return " ".join(part for part in parts if part)


# -- voice detection ----------------------------------------------------------


def frame_level(frame: bytes) -> float:
    """RMS of one s16le frame, 0..1."""
    import numpy as np

    samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
    if not samples.size:
        return 0.0
    # Without the DC offset some microphones (WSLg's RDP source) read as loud
    # in a silent room.
    samples -= samples.mean()
    return float(np.sqrt(np.mean(np.square(samples))))


def pcm_to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


@dataclass
class VoiceDetector:
    """Energy voice detection with an adaptive noise floor.

    ``feed`` returns the PCM of a finished utterance, else None. ``playing``
    raises the bar while the agent's own voice comes back through the mic.
    """

    floor: float = 0.004
    playing: bool = False
    speaking: bool = False
    level: float = 0.0
    _recent: deque[float] = field(default_factory=lambda: deque(maxlen=_FLOOR_WINDOW))
    _seen: int = 0
    _loud: int = 0
    _quiet: int = 0
    _voiced: int = 0
    _preroll: deque[bytes] = field(default_factory=lambda: deque(maxlen=_PREROLL_FRAMES))
    _frames: list[bytes] = field(default_factory=list)

    @property
    def threshold(self) -> float:
        base = max(_MIN_THRESHOLD, self.floor * _FLOOR_FACTOR)
        return base * (_PLAYBACK_FACTOR if self.playing else 1.0)

    def feed(self, frame: bytes) -> bytes | None:
        level = frame_level(frame)
        self.level = level
        # The floor is a low percentile of the last seconds: steady noise (a
        # fan, a noisy line) sets it, speech, which comes and goes, rises above
        # it. It keeps adapting while someone talks, so a noise that starts
        # mid-call cannot hold an utterance open for good.
        self._recent.append(level)
        self._seen += 1
        ordered = sorted(self._recent)
        self.floor = ordered[int(len(ordered) * _FLOOR_PERCENTILE)]
        if self._seen <= _CALIBRATION_FRAMES:
            # Learn the room first: nothing may open an utterance (and a paid
            # transcription) the moment the call starts.
            self._preroll.append(frame)
            return None
        loud = level >= self.threshold
        if not self.speaking:
            self._preroll.append(frame)
            self._loud = self._loud + 1 if loud else 0
            if self._loud >= _START_FRAMES:
                self.speaking = True
                self._frames = list(self._preroll)
                self._quiet = 0
                self._voiced = self._loud
            return None
        self._frames.append(frame)
        if level >= self.threshold * 0.7:
            self._quiet = 0
            self._voiced += 1
        else:
            self._quiet += 1
        too_long = len(self._frames) * FRAME_MS >= _MAX_UTTERANCE_S * 1000
        if self._quiet < _END_FRAMES and not too_long:
            return None
        frames, voiced = self._frames, self._voiced
        self.reset()
        if voiced < _MIN_SPEECH_FRAMES:
            return None
        return b"".join(frames)

    def reset(self) -> None:
        self.speaking = False
        self._frames = []
        self._loud = self._quiet = self._voiced = 0
        self._preroll.clear()


# -- microphone and speaker ---------------------------------------------------


class AudioUnavailableError(RuntimeError):
    """No way to record or play audio on this machine."""


def _ffmpeg() -> str | None:
    """ffmpeg from PATH, else the copy shipped inside Navin."""
    try:
        from navin.montage.detect import find_ffmpeg

        return find_ffmpeg()
    except Exception:  # noqa: BLE001 - optional
        return shutil.which("ffmpeg")


def _recorder_argv() -> list[str] | None:
    raw = ["--format=s16le", f"--rate={SAMPLE_RATE}", "--channels=1"]
    if sys.platform.startswith("linux") and shutil.which("parecord"):
        return ["parecord", "--raw", *raw, "--latency-msec=60"]
    if sys.platform.startswith("linux") and shutil.which("arecord"):
        return ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1"]
    ffmpeg = _ffmpeg()
    if ffmpeg and sys.platform == "darwin":
        source = ["-f", "avfoundation", "-i", ":0"]
    elif ffmpeg and sys.platform.startswith("linux"):
        source = ["-f", "pulse", "-i", "default"]
    else:
        return None
    return [ffmpeg, "-hide_banner", "-loglevel", "error", *source,
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]


class Microphone:
    """Raw 16 kHz mono frames from sounddevice or a platform recorder."""

    def __init__(self, on_frame: Callable[[bytes], None]) -> None:
        self._on_frame = on_frame
        self._process: subprocess.Popen[bytes] | None = None
        self._stream: Any = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()
        self.backend = ""

    def start(self) -> None:
        try:
            import sounddevice  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001 - optional; PortAudio may be missing too
            sounddevice = None
        if sounddevice is not None:
            try:
                self._stream = sounddevice.RawInputStream(
                    samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES,
                    callback=lambda data, *_: self._on_frame(bytes(data)),
                )
                self._stream.start()
                self.backend = "sounddevice"
                return
            except Exception as exc:  # noqa: BLE001 - fall back to a recorder
                logger.debug("sounddevice input failed: {}", exc)
                self._stream = None
        argv = _recorder_argv()
        if argv is None:
            raise AudioUnavailableError(
                "No microphone backend: install sounddevice (pip install sounddevice), "
                "or parecord / ffmpeg."
            )
        self._process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        self.backend = Path(argv[0]).name
        self._thread = threading.Thread(target=self._pump, name="live-voice-mic", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        stream = self._process.stdout if self._process is not None else None
        if stream is None:
            return
        while not self._stopped.is_set():
            frame = stream.read(FRAME_BYTES)
            if not frame or len(frame) < FRAME_BYTES:
                break
            self._on_frame(frame)

    def stop(self) -> None:
        self._stopped.set()
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None
        if self._process is not None:
            with contextlib.suppress(Exception):
                self._process.kill()
                self._process.wait(timeout=2)
            self._process = None


def _player_argv(path: str, mime: str) -> list[str] | None:
    if ffplay := shutil.which("ffplay"):
        return [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
    if sys.platform == "darwin" and shutil.which("afplay"):
        return ["afplay", path]
    if mime == "audio/wav" and shutil.which("paplay"):
        return ["paplay", path]
    if mime == "audio/wav" and shutil.which("aplay"):
        return ["aplay", "-q", path]
    if sys.platform == "win32":
        script = (
            "Add-Type -AssemblyName PresentationCore;"
            "$p=New-Object System.Windows.Media.MediaPlayer;"
            f"$p.Open([uri]'{path}');$p.Play();Start-Sleep -Milliseconds 400;"
            "while(-not $p.NaturalDuration.HasTimeSpan){Start-Sleep -Milliseconds 50};"
            "Start-Sleep -Milliseconds ([int]$p.NaturalDuration.TimeSpan.TotalMilliseconds)"
        )
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    return None


class Speaker:
    """Plays one clip at a time; ``stop`` cuts the current one (barge-in)."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None

    @staticmethod
    def available(mime: str = "audio/mpeg") -> bool:
        return _player_argv("x", mime) is not None

    async def play(self, audio: bytes, mime: str) -> None:
        suffix = ".wav" if mime == "audio/wav" else ".mp3"
        handle, path = tempfile.mkstemp(prefix="navin-voice-", suffix=suffix)
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(audio)
            argv = _player_argv(path, mime)
            if argv is None:
                raise AudioUnavailableError("No audio player: install ffmpeg (ffplay).")
            self._process = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, **no_window_kwargs(),
            )
            await self._process.wait()
        finally:
            self._process = None
            with contextlib.suppress(OSError):
                Path(path).unlink()

    def stop(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()


# -- the call -----------------------------------------------------------------


@dataclass(frozen=True)
class LiveReady:
    ready: bool
    reason: str | None
    stt: str = ""
    tts: str = ""


def live_ready(config: Any) -> LiveReady:
    """The server's own Live readiness contract (same as the desktop button)."""
    from navin.audio.tts import live_voice_status

    status = live_voice_status(config)
    return LiveReady(
        bool(status.get("ready")), status.get("reason"),
        str((status.get("stt") or {}).get("provider") or ""),
        str((status.get("tts") or {}).get("provider") or ""),
    )


class LiveVoiceSession:
    """Microphone in, transcripts out; text in, voice out.

    The TUI wires ``on_transcript`` (what the user said) and ``on_state``
    (presence repaint). ``speak`` queues text; a new utterance cuts the voice.
    """

    def __init__(
        self,
        config: Any,
        *,
        on_transcript: Callable[[str], Awaitable[None]],
        on_state: Callable[[], None] = lambda: None,
        on_error: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config
        self.on_transcript = on_transcript
        self.on_state = on_state
        self.on_error = on_error
        self.detector = VoiceDetector()
        self.speaker = Speaker()
        self.microphone: Microphone | None = None
        self.muted = False
        self.transcribing = False
        self.speaking = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._context: contextvars.Context = contextvars.copy_context()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        # What the voice said lately: a transcript made of it is the speakers'
        # echo, never a new message from the user.
        self._recent_speech: deque[tuple[str, float]] = deque(maxlen=12)
        self._speaker_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._epoch = 0
        self.active = False

    @property
    def hearing(self) -> bool:
        return self.detector.speaking

    @property
    def level(self) -> float:
        return self.detector.level

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        # The microphone thread hands work to the loop. Without the caller's
        # context (Textual's active app) the tasks and timers it starts are
        # orphaned and break the app's shutdown (LookupError: active_app).
        self._context = contextvars.copy_context()
        self.microphone = Microphone(self._frame)
        self.microphone.start()
        self.active = True
        self._speaker_task = asyncio.create_task(self._speak_loop(), name="live-voice-speaker")
        self.on_state()

    async def stop(self) -> None:
        self.active = False
        self._epoch += 1
        if self.microphone is not None:
            self.microphone.stop()
            self.microphone = None
        self.speaker.stop()
        for task in [self._speaker_task, *self._tasks]:
            if task is not None and not task.done():
                task.cancel()
        self._tasks.clear()
        self._speaker_task = None
        self.on_state()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.detector.reset()
        self.on_state()

    # Microphone thread -> event loop.
    def _frame(self, frame: bytes) -> None:
        if not self.active or self.muted:
            return
        was = self.detector.speaking
        utterance = self.detector.feed(frame)
        loop = self._loop
        if loop is None:
            return
        # No barge-in on a mere sound: the speakers' own echo would cut the
        # voice. What was heard is transcribed first; only real words from the
        # user (not the agent's echo) cut the voice (see _transcribe).
        if utterance is not None:
            loop.call_soon_threadsafe(self._spawn, self._transcribe(utterance), context=self._context)
        elif self.detector.speaking != was:
            loop.call_soon_threadsafe(self.on_state, context=self._context)

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def interrupt(self) -> None:
        """Stop what is being said and drop what was queued."""
        self._epoch += 1
        while not self._queue.empty():
            self._queue.get_nowait()
        self.speaker.stop()

    async def _transcribe(self, pcm: bytes) -> None:
        from navin.audio.transcription import resolve_transcription_config, transcribe_audio_file

        self.transcribing = True
        self.on_state()
        handle, path = tempfile.mkstemp(prefix="navin-live-", suffix=".wav")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(pcm_to_wav(pcm))
            text = await transcribe_audio_file(path, resolve_transcription_config(self.config))
        except Exception as exc:  # noqa: BLE001 - a failed utterance is a hiccup
            logger.debug("live transcription failed: {}", exc)
            text = ""
            if self.on_error is not None:
                await self.on_error(f"Transcription failed: {exc}")
        finally:
            self.transcribing = False
            with contextlib.suppress(OSError):
                Path(path).unlink()
            self.on_state()
        text = (text or "").strip()
        if not text or not self.active:
            return
        now = time.monotonic()
        recent = [said for said, at in self._recent_speech if now - at <= _ECHO_WINDOW_S]
        if is_own_echo(text, recent):
            logger.debug("live: dropped the agent's own echo: {}", text)
            return
        if self.speaking or not self._queue.empty():
            self.interrupt()  # the user talks over the voice: it stops
        await self.on_transcript(text)

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if text and self.active:
            self._queue.put_nowait(text)

    async def _speak_loop(self) -> None:
        from navin.audio.tts import resolve_tts_config, synthesize_speech_with_config

        while True:
            text = await self._queue.get()
            epoch = self._epoch
            cfg = resolve_tts_config(self.config)
            if cfg.response_format != "wav" and not Speaker.available("audio/mpeg"):
                # Linux without ffplay: paplay / aplay read WAV only.
                cfg = dataclasses.replace(cfg, response_format="wav")
            mime = "audio/wav" if getattr(cfg, "response_format", "") == "wav" else "audio/mpeg"
            chunks = speech_chunks(text) or [text]
            # Synthesize the next clip while the current one plays.
            pending = asyncio.ensure_future(synthesize_speech_with_config(chunks[0], cfg))
            for index, chunk in enumerate(chunks):
                try:
                    audio = await pending
                except Exception as exc:  # noqa: BLE001 - keep listening
                    logger.debug("live speech failed: {}", exc)
                    if self.on_error is not None:
                        await self.on_error(f"Speech failed: {exc}")
                    break
                if epoch != self._epoch or not self.active:
                    break  # interrupted while synthesizing
                if index + 1 < len(chunks):
                    pending = asyncio.ensure_future(synthesize_speech_with_config(chunks[index + 1], cfg))
                self._recent_speech.append((chunk, time.monotonic()))
                self.speaking = True
                self.detector.playing = True
                self.on_state()
                try:
                    await self.speaker.play(audio, mime)
                except AudioUnavailableError as exc:
                    if self.on_error is not None:
                        await self.on_error(str(exc))
                    break
                finally:
                    self.speaking = False
                    self.detector.playing = False
                    self.on_state()
                if epoch != self._epoch or not self.active:
                    break
            if not pending.done():
                pending.cancel()
            # A short tail so the room echo does not open an utterance.
            await asyncio.sleep(0.15)


def presence_mode(session: LiveVoiceSession | None, agent_busy: bool) -> str | None:
    """Same states as the desktop presence (LivePresence.tsx)."""
    if session is None or not session.active:
        return None
    if session.muted:
        return "muted"
    if session.speaking:
        return "speaking"
    if session.hearing:
        return "hearing"
    if session.transcribing or agent_busy:
        return "thinking"
    return "listening"


__all__ = [
    "AudioUnavailableError", "LiveReady", "LiveVoiceSession", "Microphone", "Speaker",
    "VoiceDetector", "approval_from_speech", "choice_from_speech", "is_spoken_stop",
    "transcript_is_meaningful",
    "live_ready", "pcm_to_wav", "presence_mode", "speech_for_approval",
    "speech_for_choice", "speech_text",
]
