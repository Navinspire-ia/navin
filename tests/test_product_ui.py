"""Tests for product UI quality gates."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.quality.product_ui import lint_product_ui, scan_file_for_product_issues


class ProductUiGateTests(unittest.TestCase):
    def test_flags_em_dash_and_stub_button(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            rel = "src/Dashboard.tsx"
            path = root / rel
            path.parent.mkdir(parents=True)
            path.write_text(
                "export function Dash() {\n"
                "  return (\n"
                "    <button onClick={() => {}}>"
                "Chargement \u2014 veuillez patienter</button>\n"
                "  );\n"
                "}\n",
                encoding="utf-8",
            )
            diags = scan_file_for_product_issues(root, rel)
            codes = {d.code for d in diags}
            self.assertIn("no-em-dash", codes)
            self.assertIn("ui-stub-placeholder-click", codes)

    def test_flags_missing_official_design_system(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            rel = "package.json"
            (root / rel).write_text(
                json.dumps(
                    {
                        "name": "app",
                        "dependencies": {"react": "19.0.0", "framer-motion": "12.0.0"},
                        "scripts": {"dev": "vite"},
                    }
                ),
                encoding="utf-8",
            )
            result = lint_product_ui(root, [rel])
            codes = {d.code for d in result.diagnostics}
            self.assertIn("missing-official-ds", codes)

    def test_allows_mui_as_official_design_system(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            rel = "package.json"
            (root / rel).write_text(
                json.dumps(
                    {
                        "name": "app",
                        "dependencies": {
                            "react": "19.0.0",
                            "framer-motion": "12.0.0",
                            "@mui/material": "6.0.0",
                            "three": "0.170.0",
                            "@react-three/fiber": "9.0.0",
                            "@react-three/drei": "10.0.0",
                        },
                        "scripts": {"dev": "vite"},
                    }
                ),
                encoding="utf-8",
            )
            result = lint_product_ui(root, [rel])
            codes = {d.code for d in result.diagnostics}
            self.assertNotIn("missing-official-ds", codes)
            self.assertNotIn("missing-framer-motion", codes)
            self.assertNotIn("missing-three-stack", codes)

    def test_flags_missing_framer_motion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            rel = "package.json"
            (root / rel).write_text(
                json.dumps(
                    {
                        "name": "crm",
                        "dependencies": {"react": "19.0.0"},
                        "scripts": {"dev": "vite"},
                    }
                ),
                encoding="utf-8",
            )
            result = lint_product_ui(root, [rel])
            codes = {d.code for d in result.diagnostics}
            self.assertIn("missing-framer-motion", codes)

    def test_flags_missing_three_stack(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            rel = "package.json"
            (root / rel).write_text(
                json.dumps(
                    {
                        "name": "app",
                        "dependencies": {
                            "react": "19.0.0",
                            "framer-motion": "12.0.0",
                            "@mui/material": "6.0.0",
                        },
                        "scripts": {"dev": "vite"},
                    }
                ),
                encoding="utf-8",
            )
            result = lint_product_ui(root, [rel])
            codes = {d.code for d in result.diagnostics}
            self.assertIn("missing-three-stack", codes)

    def test_fluent_and_carbon_need_motion_and_three_too(self) -> None:
        extras = {
            "framer-motion": "12.0.0",
            "three": "0.170.0",
            "@react-three/fiber": "9.0.0",
            "@react-three/drei": "10.0.0",
        }
        for ds_name, ds_pkg in (
            ("@fluentui/react", "9.0.0"),
            ("@carbon/react", "1.0.0"),
        ):
            with self.subTest(ds=ds_name):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    rel = "package.json"
                    (root / rel).write_text(
                        json.dumps(
                            {
                                "name": "app",
                                "dependencies": {
                                    "react": "19.0.0",
                                    ds_name: ds_pkg,
                                    **extras,
                                },
                                "scripts": {"dev": "vite"},
                            }
                        ),
                        encoding="utf-8",
                    )
                    codes = {d.code for d in lint_product_ui(root, [rel]).diagnostics}
                    self.assertNotIn("missing-official-ds", codes)
                    self.assertNotIn("missing-framer-motion", codes)
                    self.assertNotIn("missing-three-stack", codes)

                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    rel = "package.json"
                    (root / rel).write_text(
                        json.dumps(
                            {
                                "name": "app",
                                "dependencies": {"react": "19.0.0", ds_name: ds_pkg},
                                "scripts": {"dev": "vite"},
                            }
                        ),
                        encoding="utf-8",
                    )
                    codes = {d.code for d in lint_product_ui(root, [rel]).diagnostics}
                    self.assertIn("missing-framer-motion", codes)
                    self.assertIn("missing-three-stack", codes)

    def test_allows_hyphen_and_real_handler(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            rel = "src/Ok.tsx"
            path = root / rel
            path.parent.mkdir(parents=True)
            path.write_text(
                "export function Ok() {\n"
                "  return <button onClick={() => save()}>Save - now</button>;\n"
                "}\n",
                encoding="utf-8",
            )
            diags = scan_file_for_product_issues(root, rel)
            self.assertEqual(diags, [])


if __name__ == "__main__":
    unittest.main()
