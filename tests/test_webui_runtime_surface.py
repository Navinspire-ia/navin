"""Desktop sidecar must report a native WebUI surface and serve dist via symlink."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from websockets.http11 import Response

from navin.webui.runtime_surface import desktop_sidecar_surface
from navin.webui.ws_http import GatewayHTTPHandler


class DesktopSidecarSurfaceTest(unittest.TestCase):
    def test_unset_is_browser(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"NAVIN_DESKTOP_PID": "", "NAVIN_DESKTOP_APP": ""},
        ):
            self.assertEqual(desktop_sidecar_surface(), "browser")

    def test_desktop_pid_is_native(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"NAVIN_DESKTOP_PID": "4242", "NAVIN_DESKTOP_APP": ""},
        ):
            self.assertEqual(desktop_sidecar_surface(), "native")

    def test_desktop_app_path_is_native(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "NAVIN_DESKTOP_PID": "",
                "NAVIN_DESKTOP_APP": "/opt/Navin/navin-desktop",
            },
        ):
            self.assertEqual(desktop_sidecar_surface(), "native")


class StaticDistSymlinkTest(unittest.TestCase):
    def test_symlink_dist_serves_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            real = tmp / "real-dist"
            real.mkdir()
            _ = (real / "index.html").write_text("<html>ok</html>", encoding="utf-8")
            link = tmp / "dist"
            link.symlink_to(real)
            handler = object.__new__(GatewayHTTPHandler)
            handler.static_dist_path = link
            handler._log = mock.Mock()  # pyright: ignore[reportPrivateUsage]
            response = handler._serve_static("/")  # pyright: ignore[reportPrivateUsage]
            self.assertIsNotNone(response)
            self.assertIsInstance(response, Response)
            assert response is not None
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"ok", response.body)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
