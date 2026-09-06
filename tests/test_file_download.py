"""Workspace file download + rich preview kinds."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.security.workspace_access import default_workspace_scope
from navin.webui.file_preview import (
    WebUIFilePreviewError,
    content_disposition_attachment,
    file_download_payload,
    file_preview_availability_payload,
    file_preview_payload,
)


class FileDownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.scope = default_workspace_scope(self.root, True)
        self.addCleanup(self._tmp.cleanup)

    def test_download_csv(self) -> None:
        path = self.root / "books.csv"
        path.write_text("titre,prix\nA,1\n", encoding="utf-8")
        body, content_type, filename, meta = file_download_payload(
            "books.csv",
            scope=self.scope,
        )
        self.assertEqual(filename, "books.csv")
        self.assertIn(b"titre,prix", body)
        self.assertTrue(content_type.startswith("text/") or "csv" in content_type)
        self.assertEqual(meta["display_path"], "books.csv")

    def test_download_rejects_outside_workspace(self) -> None:
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            file_download_payload("/etc/passwd", scope=self.scope)
        self.assertIn(ctx.exception.status, {403, 404})

    def test_content_disposition_unicode(self) -> None:
        header = content_disposition_attachment("données.csv")
        self.assertIn("attachment;", header)
        self.assertIn("filename*=UTF-8''", header)

    def test_preview_csv_kind(self) -> None:
        path = self.root / "out.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        payload = file_preview_payload("out.csv", scope=self.scope, allow_any=True)
        self.assertEqual(payload["kind"], "csv")
        self.assertIn("a,b", payload["content"])

    def test_preview_image_kind(self) -> None:
        # Minimal PNG header bytes (not a valid image, but binary image path).
        png = self.root / "shot.png"
        png.write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\0" * 32,
        )
        payload = file_preview_payload("shot.png", scope=self.scope, allow_any=True)
        self.assertEqual(payload["kind"], "image")
        self.assertTrue(payload.get("data_url", "").startswith("data:image/png"))

    def test_availability_allows_zip(self) -> None:
        zpath = self.root / "bundle.zip"
        zpath.write_bytes(b"PK\x03\x04" + b"\0" * 20)
        avail = file_preview_availability_payload("bundle.zip", scope=self.scope)
        self.assertTrue(avail["available"])

    def test_wsl_unc_path_resolves_to_posix(self) -> None:
        """The Windows spelling of a WSL file reaches the POSIX file.

        A chip rendered on the Windows side may carry
        ``\\\\wsl.localhost\\Ubuntu\\home\\...``; the gateway runs inside the
        distribution, so the same bytes live at ``/home/...``.
        """
        path = self.root / "report.html"
        path.write_text("<html><body>ok</body></html>", encoding="utf-8")
        posix = path.as_posix()
        variants = [
            "\\\\wsl.localhost\\Ubuntu" + posix.replace("/", "\\"),
            "//wsl.localhost/Ubuntu" + posix,
            "wsl.localhost/Ubuntu" + posix,
            "\\\\wsl$\\Ubuntu" + posix.replace("/", "\\"),
        ]
        for raw in variants:
            with self.subTest(raw=raw):
                payload = file_preview_payload(raw, scope=self.scope, allow_any=True)
                self.assertEqual(payload["kind"], "html")
                self.assertIn("ok", payload["content"])
                body, _ctype, filename, _meta = file_download_payload(raw, scope=self.scope)
                self.assertEqual(filename, "report.html")
                self.assertIn(b"ok", body)

    def test_bare_name_finds_a_unique_nested_file(self) -> None:
        target = self.root / "src" / "auth.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def verify():\n    return True\n", encoding="utf-8")
        preview = file_preview_payload("auth.py", scope=self.scope, allow_any=True)
        self.assertEqual(preview["display_path"], "src/auth.py")
        self.assertIn("verify", preview["content"])
        body, _ctype, filename, _meta = file_download_payload("auth.py", scope=self.scope)
        self.assertEqual(filename, "auth.py")
        self.assertIn(b"verify", body)

    def test_extra_root_finds_file_outside_session_workspace(self) -> None:
        """Code can have a project open that is not the chat session folder."""
        other = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(other, ignore_errors=True))
        nested = other / "navin" / "documents"
        nested.mkdir(parents=True)
        target = nested / "ppt_qa.py"
        target.write_text("ok = True\n", encoding="utf-8")
        body, _ctype, filename, meta = file_download_payload(
            "ppt_qa.py",
            scope=self.scope,
            extra_roots=[other],
        )
        self.assertEqual(filename, "ppt_qa.py")
        self.assertIn(b"ok = True", body)
        self.assertTrue(str(meta["path"]).endswith("ppt_qa.py"))

    def test_unrestricted_scope_finds_install_document_basename(self) -> None:
        """A full-access session still opens Navin document helpers by name."""
        from navin.webui.ws_http import _extra_file_roots

        scope = default_workspace_scope(self.root, False)
        extra = _extra_file_roots({}, scope)
        target = next(
            (
                root / "navin" / "documents" / "ppt_qa.py"
                for root in extra
                if (root / "navin" / "documents" / "ppt_qa.py").is_file()
            ),
            None,
        )
        if target is None:
            self.skipTest("Navin documents are not on this install")
        body, _ctype, filename, _meta = file_download_payload(
            "ppt_qa.py",
            scope=scope,
            extra_roots=extra,
        )
        self.assertEqual(filename, "ppt_qa.py")
        self.assertGreater(len(body), 20)

    def test_bare_name_opens_the_newest_when_ambiguous(self) -> None:
        """Two same-named files: the one written last opens, not a dead 404.

        The chip sits next to the edit that produced the file, and the tab
        shows the full path, so the newest file is the right default.
        """
        import os

        first = self.root / "src" / "config.py"
        second = self.root / "tests" / "config.py"
        first.parent.mkdir(parents=True, exist_ok=True)
        second.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("A = 1\n", encoding="utf-8")
        second.write_text("B = 2\n", encoding="utf-8")
        os.utime(first, (1_700_000_000, 1_700_000_000))
        os.utime(second, (1_700_000_100, 1_700_000_100))
        payload = file_preview_payload("config.py", scope=self.scope, allow_any=True)
        self.assertEqual(payload["display_path"], "tests/config.py")
        self.assertEqual(payload["content"], "B = 2\n")

        # Rewriting the older copy makes it the newest, and the cached hit
        # must not pin the previous answer.
        os.utime(first, (1_700_000_200, 1_700_000_200))
        payload = file_preview_payload("config.py", scope=self.scope, allow_any=True)
        self.assertEqual(payload["display_path"], "src/config.py")

    def test_excel_workbook_previews_as_a_native_table(self) -> None:
        """An .xlsx opens as cells without LibreOffice; download still gives the file."""
        import datetime

        import openpyxl

        target = self.root / "data" / "produits.xlsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Catalogue"
        sheet.append(["id", "nom", "prix", "date", "actif"])
        sheet.append([1, "Oud Royal", 120.0, datetime.datetime(2026, 9, 3), True])
        sheet.append([2, "Jasmin", 85.5, None, False])
        sheet.append([None, None, None, None, None])
        second = workbook.create_sheet("Vide")
        second.sheet_state = "hidden"
        workbook.save(target)

        payload = file_preview_payload("produits.xlsx", scope=self.scope, allow_any=True)
        self.assertEqual(payload["kind"], "spreadsheet")
        self.assertEqual(payload["language"], "xlsx")
        self.assertFalse(payload["truncated"])
        sheets = payload["sheets"]
        self.assertEqual([s["name"] for s in sheets], ["Catalogue", "Vide"])
        self.assertEqual(
            sheets[0]["rows"],
            [
                ["id", "nom", "prix", "date", "actif"],
                ["1", "Oud Royal", "120", "2026-09-03", "TRUE"],
                ["2", "Jasmin", "85.5", "", "FALSE"],
            ],
        )
        self.assertEqual(sheets[1]["rows"], [])
        self.assertTrue(sheets[1]["hidden"])
        self.assertIn("render_available", payload)

        body, ctype, filename, _meta = file_download_payload("produits.xlsx", scope=self.scope)
        self.assertEqual(filename, "produits.xlsx")
        self.assertIn("spreadsheetml", ctype)
        self.assertEqual(body[:2], b"PK")

    def test_bare_name_found_right_after_a_failed_probe(self) -> None:
        """A chip probed before the agent wrote the file must open once it exists."""
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            file_preview_payload("schema.sql", scope=self.scope, allow_any=True)
        self.assertEqual(ctx.exception.status, 404)
        target = self.root / "supabase" / "schema.sql"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("create table t (id int);\n", encoding="utf-8")
        payload = file_preview_payload("schema.sql", scope=self.scope, allow_any=True)
        self.assertEqual(payload["display_path"], "supabase/schema.sql")
        self.assertEqual(payload["language"], "sql")
        body, _ctype, filename, _meta = file_download_payload("schema.sql", scope=self.scope)
        self.assertEqual(filename, "schema.sql")
        self.assertIn(b"create table", body)


if __name__ == "__main__":
    unittest.main()
