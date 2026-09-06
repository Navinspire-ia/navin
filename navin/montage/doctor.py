"""Environment checks for Montage (HyperFrames + media tools)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal  # Any used by optional config in run_doctor

from navin.montage.detect import ToolchainDetect, detect_toolchain
from navin.montage.ffmpeg_runner import run_process_sync

CheckStatus = Literal["ok", "warn", "missing"]

_MIN_FREE_GB = 2.0
_REQUIRED_ENCODERS = ("libx264", "aac")
_REQUIRED_FILTERS = ("scale", "subtitles", "xfade", "sidechaincompress")


def _ffmpeg_capability_check(
    ffmpeg: str, flag: str, required: tuple[str, ...], name: str
) -> DoctorCheck:
    result = run_process_sync([ffmpeg, "-hide_banner", flag], timeout_s=12.0)
    if not result.ok:
        return DoctorCheck(
            name=name,
            status="warn",
            detail=f"could not inspect FFmpeg {name}",
            fix=result.stderr or "Run ffmpeg manually and inspect this build.",
        )
    output = result.stdout + "\n" + result.raw_stderr
    missing = [item for item in required if item not in output]
    if missing:
        return DoctorCheck(
            name=name,
            status="missing",
            detail=f"missing: {', '.join(missing)}",
            fix="Install a full FFmpeg build with the required codecs and filters.",
        )
    return DoctorCheck(name=name, status="ok", detail=", ".join(required))


@dataclass(slots=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    detail: str
    fix: str = ""

    def render(self) -> str:
        mark = {"ok": "ok", "warn": "!!", "missing": "no"}[self.status]
        line = f"  [{mark}] {self.name}: {self.detail}"
        if self.fix and self.status != "ok":
            line += f"\n       Fix: {self.fix}"
        return line

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass(slots=True)
class DoctorReport:
    toolchain: ToolchainDetect
    checks: list[DoctorCheck] = field(default_factory=list)

    @property
    def ready_for_ai(self) -> bool:
        """Images/clips via providers + optional ffmpeg - no HyperFrames required."""
        return True

    @property
    def ready_for_composition(self) -> bool:
        required = {"node", "npm", "hyperframes", "chrome", "disk"}
        by_name = {c.name: c for c in self.checks}
        return all(
            by_name.get(name) is not None and by_name[name].status != "missing" for name in required
        )

    def render(self) -> str:
        lines = [self.toolchain.render(), "", "Environment:"]
        lines.extend(c.render() for c in self.checks)
        lines.append("")
        if self.ready_for_composition:
            lines.append(
                "Doctor: ready for HyperFrames composition renders "
                "(AI image/video also available when providers are configured)."
            )
        else:
            lines.append(
                "Doctor: AI image/video path OK without HyperFrames. "
                "Resolve [no] items, then montage(action=setup) before composition renders."
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "toolchain": self.toolchain.to_dict(),
            "ready_for_ai": self.ready_for_ai,
            "ready_for_composition": self.ready_for_composition,
            "checks": [c.to_dict() for c in self.checks],
        }


def run_doctor(
    toolchain: ToolchainDetect | None = None,
    *,
    config: Any | None = None,
) -> DoctorReport:
    tc = toolchain or detect_toolchain()
    checks: list[DoctorCheck] = []

    if tc.node:
        checks.append(
            DoctorCheck(
                name="node",
                status="ok",
                detail=tc.node_version or tc.node,
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="node",
                status="missing",
                detail="Node.js not found on PATH",
                fix="Install Node.js 22+ (https://nodejs.org), then re-run montage(action=doctor).",
            )
        )

    if tc.npm:
        checks.append(DoctorCheck(name="npm", status="ok", detail=tc.npm))
    else:
        checks.append(
            DoctorCheck(
                name="npm",
                status="missing",
                detail="npm not found",
                fix="Install Node.js (includes npm), then montage(action=setup).",
            )
        )

    if tc.npx:
        checks.append(DoctorCheck(name="npx", status="ok", detail=tc.npx))
    else:
        checks.append(
            DoctorCheck(
                name="npx",
                status="warn",
                detail="npx not found (setup still works via npm)",
                fix="Reinstall Node.js so npx is on PATH.",
            )
        )

    if tc.ffmpeg:
        checks.append(DoctorCheck(name="ffmpeg", status="ok", detail=tc.ffmpeg))
        checks.append(
            _ffmpeg_capability_check(tc.ffmpeg, "-encoders", _REQUIRED_ENCODERS, "ffmpeg-codecs")
        )
        checks.append(
            _ffmpeg_capability_check(tc.ffmpeg, "-filters", _REQUIRED_FILTERS, "ffmpeg-filters")
        )
    else:
        checks.append(
            DoctorCheck(
                name="ffmpeg",
                status="warn",
                detail="ffmpeg missing - concat/subtitle burn-in unavailable",
                fix="Install ffmpeg (apt/brew/winget Gyan.FFmpeg), or rely on HyperFrames encode only.",
            )
        )

    if tc.chrome:
        checks.append(DoctorCheck(name="chrome", status="ok", detail=tc.chrome))
    else:
        checks.append(
            DoctorCheck(
                name="chrome",
                status="missing",
                detail="No Chrome/Chromium/Edge or Playwright Chromium found",
                fix=(
                    "Install Google Chrome/Chromium, or run "
                    "`playwright install chromium` (Navin browser cache), "
                    "or set PUPPETEER_EXECUTABLE_PATH / NAVIN_CHROMIUM. "
                    "HyperFrames may otherwise download Chromium (~200-300 MB)."
                ),
            )
        )

    if tc.hyperframes:
        detail = tc.hyperframes
        if tc.hyperframes_version:
            detail = f"{tc.hyperframes} ({tc.hyperframes_version})"
        checks.append(DoctorCheck(name="hyperframes", status="ok", detail=detail))
    else:
        checks.append(
            DoctorCheck(
                name="hyperframes",
                status="missing",
                detail="HyperFrames CLI not installed under ~/.navin/montage",
                fix=(
                    "Lazy install (Windows/macOS/Linux): montage(action=setup, package=hyperframes) "
                    "or Studio → Montage → Install HyperFrames."
                ),
            )
        )

    try:
        from navin.montage.install import remotion_bin

        remotion = remotion_bin()
    except Exception:
        remotion = None
    if remotion:
        checks.append(DoctorCheck(name="remotion", status="ok", detail=str(remotion)))
    else:
        checks.append(
            DoctorCheck(
                name="remotion",
                status="warn",
                detail="Remotion optional (React compositions) - not installed",
                fix=(
                    "Heavy optional: montage(action=setup, package=remotion). "
                    "Not required for HyperFrames or ffmpeg packaging."
                ),
            )
        )

    # Stock (Pexels/Unsplash/Pixabay) is builtin - optional env keys, never a
    # Studio UI blocker. Keep a quiet ok row for doctor text reports only.
    try:
        from navin.montage.stock import stock_status

        stock = stock_status(config, probe=False)
        ready_stock = [
            name for name, row in (stock.get("providers") or {}).items() if row.get("configured")
        ]
        checks.append(
            DoctorCheck(
                name="stock",
                status="ok",
                detail=(
                    "builtin clients ready"
                    + (f" (keys: {', '.join(ready_stock)})" if ready_stock else "")
                ),
            )
        )
    except Exception:
        checks.append(
            DoctorCheck(
                name="stock",
                status="warn",
                detail="stock status unavailable",
            )
        )

    free = tc.free_disk_gb
    if free is None:
        checks.append(
            DoctorCheck(
                name="disk",
                status="warn",
                detail="could not measure free disk",
                fix="Ensure ~/.navin/montage has at least 2 GB free before long renders.",
            )
        )
    elif free < _MIN_FREE_GB:
        checks.append(
            DoctorCheck(
                name="disk",
                status="missing",
                detail=f"{free:.1f} GB free (need ≥ {_MIN_FREE_GB:.0f} GB scratch)",
                fix="Free disk space or set TMPDIR / HYPERFRAMES_EXTRACT_CACHE_DIR to a larger volume.",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="disk",
                status="ok",
                detail=f"{free:.1f} GB free",
            )
        )

    return DoctorReport(toolchain=tc, checks=checks)
