"""Turn a video attachment into still frames a vision model can actually read.

No chat provider accepts a raw mp4 on the OpenAI-compatible content format we
use everywhere, so a video reaching :func:`navin.agent.context._build_user_content`
was silently dropped. Sampling evenly spaced frames keeps the turn multimodal
with the image blocks every provider already understands.

Frames are sampled across the whole duration rather than from the first
seconds: the interesting moment in a screen recording or a bug reproduction is
almost never at t=0.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv"}
)

VIDEO_MIME_PREFIX = "video/"

# Enough coverage to follow a short screen recording without blowing the image
# budget: every frame is a full image payload for the model.
DEFAULT_MAX_FRAMES = 6
MAX_FRAMES_CEILING = 16

# Long edge of an extracted frame. Matches the WebUI normalization cap so a
# frame costs the model roughly what an attached screenshot costs.
FRAME_MAX_EDGE = 1280

# Extraction happens inline in the turn, so these are deliberately tight:
# input seeking makes a single frame a sub-second operation even on long clips.
_PROBE_TIMEOUT_S = 15
_EXTRACT_TIMEOUT_S = 30

# `Duration: 00:01:23.45` in ffmpeg/ffprobe stderr.
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


@dataclass(slots=True)
class VideoFrames:
    """Result of sampling one video.

    ``paths`` is empty when extraction was not possible; ``reason`` then says
    why, in a form safe to show the model so it does not invent content.
    """

    source: str
    paths: list[str] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    duration: float | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.paths)


def is_video_path(path: str | Path) -> bool:
    """True when *path* looks like a video by extension."""
    text = str(path).split("?", 1)[0].split("#", 1)[0]
    return Path(text).suffix.lower() in VIDEO_EXTENSIONS


def parse_duration(stderr: str) -> float | None:
    """Seconds from an ffmpeg/ffprobe ``Duration:`` banner, else ``None``."""
    match = _DURATION_RE.search(stderr or "")
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    try:
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None


def frame_timestamps(duration: float | None, count: int) -> list[float]:
    """Evenly spaced sample points inside *duration*.

    Sampling at the exact bounds is avoided: the first and last frames of a
    recording are often a black or half-rendered frame.
    """
    count = max(1, min(int(count), MAX_FRAMES_CEILING))
    if duration is None or duration <= 0:
        return [0.0]
    if count == 1:
        return [round(duration / 2, 3)]
    step = duration / (count + 1)
    return [round(step * (index + 1), 3) for index in range(count)]


def frames_cache_root() -> Path:
    """Where extracted frames live.

    Deliberately outside the workspace: sampling a video the user referenced
    from their repository must not drop a hidden directory into it.
    """
    return Path.home() / ".navin" / "cache" / "video-frames"


def _frame_dir(video: Path, base_dir: Path | None, count: int) -> Path:
    """Stable per-video output directory so re-sending a video is cheap.

    The sample count is part of the key: changing it moves every timestamp, so
    reusing the previous run's stills would mislabel them.
    """
    try:
        stat = video.stat()
        fingerprint = f"{video.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|{count}"
    except OSError:
        fingerprint = f"{video}|{count}"
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
    root = base_dir if base_dir is not None else frames_cache_root()
    return root / f"{safe_stem(video.stem)}-{digest}"


def safe_stem(stem: str, *, limit: int = 40) -> str:
    """Filesystem-safe, length-bounded version of a file stem."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")
    return (cleaned or "video")[:limit]


def _find_ffmpeg() -> str | None:
    try:
        from navin.montage.detect import find_ffmpeg
    except Exception:  # pragma: no cover - montage package always ships
        return None
    try:
        return find_ffmpeg()
    except Exception:
        return None


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    from navin.utils.proc import no_window_kwargs

    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )


def probe_duration(video: Path, ffmpeg: str) -> float | None:
    """Duration in seconds, read from ffmpeg's own banner (no ffprobe needed)."""
    try:
        proc = _run([ffmpeg, "-hide_banner", "-i", str(video)], _PROBE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("ffmpeg probe failed for {}: {}", video, exc)
        return None
    # `-i` without an output always exits non-zero; the banner is what we want.
    return parse_duration(proc.stderr or "")


def extract_video_frames(
    path: str | Path,
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    base_dir: Path | None = None,
) -> VideoFrames:
    """Sample *max_frames* stills from *path* as JPEG files on disk.

    Never raises: a video that cannot be decoded degrades to a reason string
    the caller shows the model instead of pretending the video was seen.
    """
    video = Path(path)
    result = VideoFrames(source=str(video))
    if not video.is_file():
        result.reason = "file not found"
        return result

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        result.reason = "ffmpeg is not installed, video frames unavailable"
        return result

    result.duration = probe_duration(video, ffmpeg)
    stamps = frame_timestamps(result.duration, max_frames)
    out_dir = _frame_dir(video, base_dir, len(stamps))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.reason = f"cannot write frames: {exc}"
        return result

    timed_out = False
    for index, stamp in enumerate(stamps):
        try:
            frame = _write_frame(ffmpeg, video, stamp, out_dir, index)
        except subprocess.TimeoutExpired:
            # One slow frame means the rest will be slow too; do not hold the
            # turn hostage for the whole sample set.
            logger.warning("frame extraction timed out for {}", video)
            timed_out = True
            break
        if frame is not None:
            result.paths.append(str(frame))
            result.timestamps.append(stamp)

    if not result.paths:
        result.reason = (
            "frame extraction timed out"
            if timed_out
            else "no frame could be decoded from this video"
        )
    return result


def _write_frame(
    ffmpeg: str, video: Path, stamp: float, out_dir: Path, index: int
) -> Path | None:
    """One still at *stamp*, reusing an earlier extraction when present.

    JPEG is tried first for size, then PNG: minimal ffmpeg builds (the one
    Playwright ships, for instance) have no mjpeg encoder at all.
    """
    for suffix in (".jpg", ".png"):
        target = out_dir / f"frame-{index:02d}{suffix}"
        if target.is_file() and target.stat().st_size > 0:
            return target

    scale = f"scale='min({FRAME_MAX_EDGE},iw)':-2"
    for suffix in (".jpg", ".png"):
        target = out_dir / f"frame-{index:02d}{suffix}"
        args = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            # Seek before -i: input seeking is orders of magnitude faster and
            # accurate enough for sampling.
            "-ss",
            f"{stamp}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            scale,
            str(target),
        ]
        if suffix == ".jpg":
            args[-1:-1] = ["-q:v", "4"]
        try:
            proc = _run(args, _EXTRACT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("frame extraction failed at {}s for {}: {}", stamp, video, exc)
            return None
        if proc.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            return target
        target.unlink(missing_ok=True)
    return None


def describe_frames(frames: VideoFrames) -> str:
    """One-line note telling the model what it is looking at, and what it is not.

    Without this the model sees loose images and can claim it "watched" the
    video. The soundtrack is handled by :mod:`navin.utils.audio_transcripts`,
    which appends its own note (transcript or reason) right after this one.
    """
    name = Path(frames.source).name
    if not frames.ok:
        return f"[video: {name} - not analyzable: {frames.reason}]"
    stamps = ", ".join(f"{value:g}s" for value in frames.timestamps)
    duration = f" of {frames.duration:g}s" if frames.duration else ""
    return (
        f"[video: {name}{duration} - {len(frames.paths)} sampled frames at {stamps}. "
        "These are stills, not the full video: motion between frames is not available; "
        "the soundtrack is reported in a separate note.]"
    )


def expand_video_attachments(
    text: str,
    media: list[str],
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    base_dir: Path | None = None,
) -> tuple[str, list[str]]:
    """Replace video paths in *media* with extracted frame image paths.

    Returns the annotated text and the rewritten media list. Non-video entries
    pass through untouched and keep their original order.
    """
    if not media:
        return text, media

    rewritten: list[str] = []
    notes: list[str] = []
    for item in media:
        if not isinstance(item, str) or not is_video_path(item):
            rewritten.append(item)
            continue
        frames = extract_video_frames(item, max_frames=max_frames, base_dir=base_dir)
        notes.append(describe_frames(frames))
        rewritten.extend(frames.paths)

    if notes:
        suffix = "\n".join(notes)
        text = f"{text}\n\n{suffix}" if text else suffix
    return text, rewritten
