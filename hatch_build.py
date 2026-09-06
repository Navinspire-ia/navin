"""Hatch build hook that bundles the webui (Vite) into navin/web/dist.

Triggered automatically by `python -m build` (and any other hatch-driven build)
so published wheels and sdists ship a fresh webui without requiring developers
to remember `cd webui && npm run build` beforehand.

Behavior:

- Skips the webui bundle for editable installs (`pip install -e .`). Editable
  mode is for Python development; webui contributors use `cd webui && npm run
  dev` (Vite HMR) and do not need a packaged `dist/`.
- On Linux, macOS and Windows, an editable install still compiles
  ``navin-sandbox`` when cargo is available. A missing copy must not brick
  agent ``exec``; the helper is still required in every Tauri sidecar.
- No-op when `webui/package.json` is absent (e.g. installing from an sdist that
  already contains a prebuilt `navin/web/dist/`).
- Skips when `NAVIN_SKIP_WEBUI_BUILD=1` is set.
- Reuses `navin/web/dist/` only when it is already fresh, unless
  `NAVIN_FORCE_WEBUI_BUILD=1` is set.
- Uses `npm` (`install` then `run build`) over the single lockfile the release
  builds resolve, `webui/package-lock.json`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _load_webui_build_module() -> ModuleType:
    from navin.webui import build as webui_build

    return webui_build


class WebUIBuildHook(BuildHookInterface):
    PLUGIN_NAME = "webui-build"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: D401
        # Independent of the webui bundle: editable installs skip Vite but
        # still need the OS command jail on Linux and macOS.
        self._ensure_sandbox_binary(version)
        root = Path(self.root)
        webui_dir = root / "webui"
        package_json = webui_dir / "package.json"
        dist_dir = root / "navin" / "web" / "dist"
        index_html = dist_dir / "index.html"

        # `pip install -e .` builds an editable wheel; skip the (slow) webui
        # bundle since editable installs target Python development and webui
        # work uses `npm run dev` instead.
        if self.target_name == "wheel" and version == "editable":
            self.app.display_info(
                "[webui-build] skipped for editable install "
                "(use `cd webui && npm run build` to bundle webui manually)"
            )
            return

        if os.environ.get("NAVIN_SKIP_WEBUI_BUILD") == "1":
            self.app.display_info("[webui-build] skipped via NAVIN_SKIP_WEBUI_BUILD=1")
            return

        if not package_json.is_file():
            self.app.display_info(
                "[webui-build] no webui/ source tree, assuming prebuilt navin/web/dist/"
            )
            return

        webui_build = _load_webui_build_module()
        status = webui_build.inspect_webui_bundle(source_dir=webui_dir, dist_dir=dist_dir)
        force = os.environ.get("NAVIN_FORCE_WEBUI_BUILD") == "1"
        if not status.needs_build and not force:
            self.app.display_info(
                f"[webui-build] reusing existing build at {dist_dir} "
                "(already fresh; set NAVIN_FORCE_WEBUI_BUILD=1 to rebuild)"
            )
            return

        if status.needs_build and not force:
            self.app.display_info(
                f"[webui-build] {webui_build.describe_webui_bundle_status(status)}"
            )

        try:
            webui_build.build_webui_bundle(
                source_dir=webui_dir,
                dist_dir=dist_dir,
                output=self.app.display_info,
            )
        except webui_build.WebUIBuildError as exc:
            raise RuntimeError(
                "[webui-build] "
                f"{exc}. Install Node.js/`npm`, or set NAVIN_SKIP_WEBUI_BUILD=1 to bypass."
            ) from exc

        if not index_html.is_file():
            raise RuntimeError(
                f"[webui-build] build finished but {index_html} is missing; "
                "check webui/vite.config.ts outDir."
            )
        self.app.display_info(f"[webui-build] webui ready at {dist_dir}")

    def _ensure_sandbox_binary(self, version: str) -> None:
        """Compile navin-sandbox for an editable source install.

        Every desktop sidecar ships this helper (Linux, macOS, Windows).
        Wheels stay portable: the sidecar build compiles it separately.
        """
        import shutil
        import subprocess

        if os.environ.get("NAVIN_SKIP_SANDBOX_BUILD") == "1":
            self.app.display_info("[sandbox-build] skipped via NAVIN_SKIP_SANDBOX_BUILD=1")
            return
        if self.target_name == "wheel" and version != "editable":
            return
        root = Path(self.root)
        name = "navin-sandbox.exe" if sys.platform == "win32" else "navin-sandbox"
        dest = root / "navin" / "resources" / "bin" / name
        if dest.is_file() and os.access(dest, os.X_OK):
            self.app.display_info(f"[sandbox-build] reusing {dest}")
            return
        cargo = shutil.which("cargo")
        crate = root / "navin-sandbox" / "Cargo.toml"
        if cargo is None or not crate.is_file():
            self.app.display_warning(
                "[sandbox-build] cargo or navin-sandbox/ is missing; "
                "agent exec stays unconfined until `make native`"
            )
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.app.display_info("[sandbox-build] cargo build --release (navin-sandbox)")
        result = subprocess.run(
            [cargo, "build", "--release", "--manifest-path", str(crate)],
            cwd=str(crate.parent),
            check=False,
        )
        if result.returncode != 0:
            self.app.display_warning(
                "[sandbox-build] cargo failed; navin-sandbox is still missing "
                "(exec stays unconfined)"
            )
            return
        built = crate.parent / "target" / "release" / name
        if not built.is_file():
            self.app.display_warning(f"[sandbox-build] {built} was not produced")
            return
        shutil.copy2(built, dest)
        dest.chmod(dest.stat().st_mode | 0o755)
        self.app.display_info(f"[sandbox-build] staged {dest}")
