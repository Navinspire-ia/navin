"""Montage package catalog: builtin (tiny) vs system vs lazy heavy installs.

Cross-platform (Windows / macOS / Linux). Heavy optional deps stay opt-in so
we never pull Remotion/HyperFrames into a cold start (OpenMontage #481).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PackageTier = Literal["builtin", "system", "lazy"]
PackageCategory = Literal["stock", "composition_html", "composition_react", "post"]


@dataclass(frozen=True, slots=True)
class InstallRecipe:
    """One OS package-manager recipe (mirrors skills_setup kinds)."""

    id: str
    kind: str  # brew | apt | dnf | pacman | winget | choco | npm-local
    package: str
    label: str = ""
    platforms: tuple[str, ...] = ()  # empty = any; win32 / darwin / linux


@dataclass(frozen=True, slots=True)
class MontagePackage:
    id: str
    label: str
    category: PackageCategory
    tier: PackageTier
    description: str
    # Approximate download / install weight for UX (MB). 0 = no download.
    size_mb: int = 0
    # True = never auto-install; user must click explicitly.
    optional: bool = False
    env_keys: tuple[str, ...] = ()
    signup_url: str = ""
    recipes: tuple[InstallRecipe, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["recipes"] = [asdict(r) for r in self.recipes]
        return data


# Tiny: shipped in Navin (HTTP clients only). User only needs free developer keys.
STOCK_PACKAGES: tuple[MontagePackage, ...] = (
    MontagePackage(
        id="stock-pexels",
        label="Pexels",
        category="stock",
        tier="builtin",
        description="Free stock photos & videos (developer API key).",
        size_mb=0,
        env_keys=("PEXELS_API_KEY",),
        signup_url="https://www.pexels.com/api/",
    ),
    MontagePackage(
        id="stock-unsplash",
        label="Unsplash",
        category="stock",
        tier="builtin",
        description="Free stock photos (Access Key).",
        size_mb=0,
        env_keys=("UNSPLASH_ACCESS_KEY",),
        signup_url="https://unsplash.com/oauth/applications",
    ),
    MontagePackage(
        id="stock-pixabay",
        label="Pixabay",
        category="stock",
        tier="builtin",
        description="Free stock photos & videos (API key).",
        size_mb=0,
        env_keys=("PIXABAY_API_KEY",),
        signup_url="https://pixabay.com/api/docs/",
    ),
)

# Medium: OS binary via package managers, or user-local under ~/.navin/montage/bin.
FFMPEG_PACKAGE = MontagePackage(
    id="ffmpeg",
    label="FFmpeg",
    category="post",
    tier="system",
    description="Encoding, crops, subtitle burn-in, audio mix (post-production).",
    size_mb=80,
    recipes=(
        InstallRecipe(
            id="winget",
            kind="winget",
            package="Gyan.FFmpeg",
            label="winget Gyan.FFmpeg",
            platforms=("win32",),
        ),
        InstallRecipe(
            id="choco",
            kind="choco",
            package="ffmpeg",
            label="choco ffmpeg",
            platforms=("win32",),
        ),
        InstallRecipe(
            id="brew",
            kind="brew",
            package="ffmpeg",
            label="brew install ffmpeg",
            platforms=("darwin",),
        ),
        InstallRecipe(
            id="apt",
            kind="apt",
            package="ffmpeg",
            label="apt install ffmpeg",
            platforms=("linux",),
        ),
        InstallRecipe(
            id="dnf",
            kind="dnf",
            package="ffmpeg",
            label="dnf install ffmpeg",
            platforms=("linux",),
        ),
        InstallRecipe(
            id="yum",
            kind="yum",
            package="ffmpeg",
            label="yum install ffmpeg",
            platforms=("linux",),
        ),
        InstallRecipe(
            id="pacman",
            kind="pacman",
            package="ffmpeg",
            label="pacman -S ffmpeg",
            platforms=("linux",),
        ),
        InstallRecipe(
            id="user-local",
            kind="user-local",
            package="ffmpeg",
            label="user-local ~/.navin/montage/bin",
            platforms=("win32", "darwin", "linux"),
        ),
    ),
)

# Not bundled: a browser engine would add 150-300 MB per platform to installers
# that already ship a webview. Fetched on demand instead, and skipped entirely
# when the machine already has Chrome, Edge or Chromium.
CHROMIUM_PACKAGE = MontagePackage(
    id="chromium",
    label="Chromium",
    category="post",
    tier="system",
    description=(
        "Headless browser for the browser tool and HTML rendering: navigation, "
        "clicks, screenshots the model can read, CDP."
    ),
    size_mb=150,
    recipes=(
        InstallRecipe(
            id="playwright",
            kind="user-local",
            package="chromium",
            label="playwright install chromium",
            platforms=("win32", "darwin", "linux"),
        ),
    ),
)

# Heavy / lazy: npm under ~/.navin/montage - never auto on gateway start.
HYPERFRAMES_PACKAGE = MontagePackage(
    id="hyperframes",
    label="HyperFrames",
    category="composition_html",
    tier="lazy",
    description=(
        "HTML/CSS/GSAP composition → MP4 (kinetic type, product promos, "
        "website-to-video). Installed on demand under ~/.navin/montage."
    ),
    size_mb=120,
    optional=False,
)

REMOTION_PACKAGE = MontagePackage(
    id="remotion",
    label="Remotion",
    category="composition_react",
    tier="lazy",
    description=(
        "React composition (spring scenes, text/stat cards, captions). "
        "Heavy optional install under ~/.navin/montage/remotion - not required "
        "for HyperFrames or ffmpeg packaging."
    ),
    size_mb=350,
    optional=True,
)

ALL_PACKAGES: tuple[MontagePackage, ...] = (
    *STOCK_PACKAGES,
    FFMPEG_PACKAGE,
    CHROMIUM_PACKAGE,
    HYPERFRAMES_PACKAGE,
    REMOTION_PACKAGE,
)

_BY_ID = {p.id: p for p in ALL_PACKAGES}


def get_package(package_id: str) -> MontagePackage | None:
    return _BY_ID.get((package_id or "").strip().lower())


def list_packages() -> list[dict[str, Any]]:
    return [p.as_dict() for p in ALL_PACKAGES]


def packages_by_category() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {
        "stock": [],
        "composition_html": [],
        "composition_react": [],
        "post": [],
    }
    for package in ALL_PACKAGES:
        out.setdefault(package.category, []).append(package.as_dict())
    return out
