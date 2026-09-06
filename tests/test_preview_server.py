"""Tests for workspace preview server discovery."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.preview_server import (
    discover_project_dev_servers,
    html_looks_like_navin,
    is_navin_editor_cwd,
    is_navin_product_tree,
    pick_server_for_url,
)


class PreviewServerDiscoveryTests(unittest.TestCase):
    def test_prefers_start_sh_and_frontend_vite_port(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "start.sh").write_text(
                "#!/bin/bash\necho http://localhost:5180\nnpm run dev\n",
                encoding="utf-8",
            )
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                json.dumps(
                    {
                        "name": "crm-frontend",
                        "scripts": {"dev": "vite"},
                    }
                ),
                encoding="utf-8",
            )
            (frontend / "vite.config.ts").write_text(
                "export default { server: { port: 5180, strictPort: true } }\n",
                encoding="utf-8",
            )

            servers = discover_project_dev_servers(root)
            self.assertTrue(servers)
            self.assertEqual(servers[0].label, "start.sh")
            self.assertEqual(servers[0].port, 5180)

            picked = pick_server_for_url(servers, "http://127.0.0.1:5180")
            assert picked is not None
            self.assertEqual(picked.port, 5180)

    def test_vite_default_when_no_port_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "package.json").write_text(
                json.dumps({"name": "app", "scripts": {"dev": "vite"}}),
                encoding="utf-8",
            )
            servers = discover_project_dev_servers(root)
            self.assertEqual(len(servers), 1)
            self.assertEqual(servers[0].port, 5173)
            self.assertIn("npm run dev", servers[0].command)

    def test_skips_navin_product_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "navin" / "agent").mkdir(parents=True)
            webui = root / "webui"
            webui.mkdir()
            (webui / "index.html").write_text(
                '<html data-navin-webui><span data-boot-copy>Loading Navin…</span></html>',
                encoding="utf-8",
            )
            (webui / "package.json").write_text(
                json.dumps({"name": "navin-webui", "scripts": {"dev": "vite"}}),
                encoding="utf-8",
            )
            self.assertTrue(is_navin_product_tree(root))
            self.assertTrue(is_navin_editor_cwd(webui))
            self.assertEqual(discover_project_dev_servers(root), [])

    def test_html_markers_detect_navin_shell(self) -> None:
        self.assertTrue(html_looks_like_navin('<html data-navin-webui>'))
        self.assertTrue(html_looks_like_navin("Loading Navin…"))
        self.assertFalse(html_looks_like_navin("<html><title>CRM</title></html>"))


if __name__ == "__main__":
    unittest.main()
