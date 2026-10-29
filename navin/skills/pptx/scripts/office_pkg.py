#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""OOXML package helpers shared by the docx, pptx and xlsx skills.

An Office file (.docx, .pptx, .xlsx) is a zip archive of XML parts plus a
[Content_Types].xml manifest and _rels/*.rels relationship files. This tool
unpacks such a file into a folder with pretty-printed XML (easy to read and
edit), packs a folder back into a valid archive (minified XML, manifest
stored first) and validates an archive structurally.

Commands:
    unpack   <file.docx|pptx|xlsx> <out_dir>  [--no-pretty]
    pack     <in_dir> <file.docx|pptx|xlsx>    [--no-minify]
    validate <file.docx|pptx|xlsx>             [--json] [--no-roundtrip]

Exit codes: 0 ok, 1 validation failed, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import shutil
import sys
import zipfile
from pathlib import Path

from lxml import etree

CONTENT_TYPES = "[Content_Types].xml"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_SUFFIXES = (".xml", ".rels")

KNOWN_KINDS = {
    ".docx": "word",
    ".dotx": "word",
    ".docm": "word",
    ".pptx": "powerpoint",
    ".potx": "powerpoint",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".xltx": "excel",
}


# --------------------------------------------------------------------------- #
# XML helpers
# --------------------------------------------------------------------------- #


def _is_xml_part(name: str) -> bool:
    return name.lower().endswith(XML_SUFFIXES)


def pretty_xml(data: bytes) -> bytes:
    """Return an indented copy of an XML document (whitespace-only text nodes removed)."""
    parser = etree.XMLParser(remove_blank_text=True)
    root = etree.fromstring(data, parser)
    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8", standalone=True)


def minify_xml(data: bytes) -> bytes:
    """Return a compact copy of an XML document.

    Only whitespace-only text nodes between elements are removed. Text content
    (including deliberate spaces inside w:t, a:t or t elements) is untouched.
    """
    parser = etree.XMLParser(remove_blank_text=True)
    root = etree.fromstring(data, parser)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


# --------------------------------------------------------------------------- #
# unpack / pack
# --------------------------------------------------------------------------- #


def unpack(archive: Path, out_dir: Path, pretty: bool = True) -> dict:
    """Extract an Office archive into out_dir.

    Returns {"parts": [names], "raw_parts": [names]}: raw_parts lists the XML parts
    that could not be pretty-printed because they are malformed. They are written
    byte for byte so the caller can repair them, and are reported rather than hidden.
    """
    if not archive.is_file():
        raise FileNotFoundError(archive)
    out_dir.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    raw_parts: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = out_dir / info.filename
            if not str(target.resolve()).startswith(str(out_dir.resolve())):
                raise ValueError(f"refusing to write outside out_dir: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(info)
            if pretty and _is_xml_part(info.filename):
                try:
                    data = pretty_xml(data)
                except etree.XMLSyntaxError as exc:
                    raw_parts.append(f"{info.filename}: {exc}")
            target.write_bytes(data)
            names.append(info.filename)
    return {"parts": names, "raw_parts": raw_parts}


def pack(in_dir: Path, archive: Path, minify: bool = True) -> int:
    """Zip a folder back into an Office archive. Returns the number of parts written."""
    if not in_dir.is_dir():
        raise NotADirectoryError(in_dir)
    manifest = in_dir / CONTENT_TYPES
    if not manifest.is_file():
        raise FileNotFoundError(f"{CONTENT_TYPES} missing in {in_dir}")
    files = sorted(p for p in in_dir.rglob("*") if p.is_file())
    # The manifest must be the first entry; some consumers rely on it.
    ordered = [manifest] + [p for p in files if p != manifest]
    archive.parent.mkdir(parents=True, exist_ok=True)
    tmp = archive.with_suffix(archive.suffix + ".tmp")
    count = 0
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in ordered:
            name = path.relative_to(in_dir).as_posix()
            data = path.read_bytes()
            if minify and _is_xml_part(name):
                try:
                    data = minify_xml(data)
                except etree.XMLSyntaxError as exc:
                    tmp.unlink(missing_ok=True)
                    raise ValueError(f"{name} is not well-formed XML: {exc}") from exc
            zf.writestr(name, data)
            count += 1
    shutil.move(str(tmp), str(archive))
    return count


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #


def _resolve_target(source_part: str, target: str) -> str:
    """Resolve a relationship Target relative to the part that owns the .rels file."""
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(source_part)
    return posixpath.normpath(posixpath.join(base, target)).lstrip("/")


def _source_part_of_rels(rels_name: str) -> str:
    """'word/_rels/document.xml.rels' -> 'word/document.xml'; '_rels/.rels' -> ''."""
    directory, filename = posixpath.split(rels_name)
    parent = posixpath.dirname(directory)  # strip the trailing '_rels'
    part = filename[: -len(".rels")]
    return posixpath.join(parent, part) if part else parent


def _roundtrip(archive: Path, kind: str) -> str | None:
    """Open the archive with the matching python library. Returns an error string or None.

    This is a probe, not a guard: the question asked is "does the library open this
    file at all", and a corrupt package can surface as KeyError, AttributeError,
    IndexError, XMLSyntaxError or a library error. Whatever it raises is the
    validation finding, so it is caught whole and returned to the caller, which
    records it in the errors list (nothing is swallowed).
    """
    try:
        if kind == "word":
            from docx import Document

            Document(str(archive))
        elif kind == "powerpoint":
            from pptx import Presentation

            Presentation(str(archive))
        elif kind == "excel":
            from openpyxl import load_workbook

            load_workbook(str(archive)).close()
        else:
            return None
    except ImportError as exc:
        return f"library missing for round-trip: {exc}"
    except Exception as exc:
        return f"round-trip open failed with {type(exc).__name__}: {exc}"
    return None


def validate(archive: Path, roundtrip: bool = True) -> dict:
    """Structural validation. Returns a dict with ok, errors, warnings and stats."""
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, int] = {}
    if not archive.is_file():
        return {"ok": False, "errors": [f"file not found: {archive}"], "warnings": [], "stats": {}}
    if not zipfile.is_zipfile(archive):
        return {"ok": False, "errors": ["not a zip archive"], "warnings": [], "stats": {}}

    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad:
            errors.append(f"corrupt zip member: {bad}")
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        name_set = set(names)
        stats["parts"] = len(names)

        # 1. every XML part well-formed
        xml_parts = [n for n in names if _is_xml_part(n)]
        stats["xml_parts"] = len(xml_parts)
        trees: dict[str, etree._Element] = {}
        for name in xml_parts:
            try:
                trees[name] = etree.fromstring(zf.read(name))
            except etree.XMLSyntaxError as exc:
                errors.append(f"malformed XML in {name}: {exc}")

        # 2. content types manifest
        if CONTENT_TYPES not in name_set:
            errors.append(f"{CONTENT_TYPES} missing")
        else:
            if names[0] != CONTENT_TYPES:
                warnings.append(f"{CONTENT_TYPES} is not the first zip entry")
            ct = trees.get(CONTENT_TYPES)
            if ct is not None:
                defaults = {
                    (e.get("Extension") or "").lower()
                    for e in ct.iter(f"{{{CT_NS}}}Default")
                }
                overrides = {
                    (e.get("PartName") or "").lstrip("/") for e in ct.iter(f"{{{CT_NS}}}Override")
                }
                stats["overrides"] = len(overrides)
                for part in overrides:
                    if part not in name_set:
                        errors.append(f"[Content_Types].xml overrides missing part: /{part}")
                for name in names:
                    if name == CONTENT_TYPES or name in overrides:
                        continue
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if ext not in defaults:
                        errors.append(f"part has no content type: {name}")

        # 3. relationships
        rels_parts = [n for n in xml_parts if n.endswith(".rels")]
        stats["relationship_files"] = len(rels_parts)
        rel_count = 0
        for rels_name in rels_parts:
            tree = trees.get(rels_name)
            if tree is None:
                continue
            source = _source_part_of_rels(rels_name)
            seen_ids: set[str] = set()
            for rel in tree.iter(f"{{{REL_NS}}}Relationship"):
                rel_count += 1
                rid = rel.get("Id") or ""
                if rid in seen_ids:
                    errors.append(f"duplicate relationship Id {rid} in {rels_name}")
                seen_ids.add(rid)
                if (rel.get("TargetMode") or "Internal") == "External":
                    continue
                target = rel.get("Target") or ""
                resolved = _resolve_target(source, target)
                if resolved not in name_set:
                    errors.append(f"{rels_name}: {rid} -> {target} does not exist")
        stats["relationships"] = rel_count
        if "_rels/.rels" not in name_set:
            errors.append("_rels/.rels (package relationships) missing")

    # 4. round-trip with the matching library
    kind = KNOWN_KINDS.get(archive.suffix.lower())
    if roundtrip and kind and not errors:
        problem = _roundtrip(archive, kind)
        if problem:
            errors.append(f"round-trip open failed: {problem}")
    elif roundtrip and kind is None:
        warnings.append(f"unknown extension {archive.suffix}: round-trip skipped")

    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="office_pkg.py",
        description="Unpack, pack and validate OOXML packages (.docx, .pptx, .xlsx).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_unpack = sub.add_parser("unpack", help="extract an archive into a folder with pretty XML")
    p_unpack.add_argument("archive", type=Path)
    p_unpack.add_argument("out_dir", type=Path)
    p_unpack.add_argument("--no-pretty", action="store_true", help="keep XML bytes as stored")

    p_pack = sub.add_parser("pack", help="zip a folder back into an archive")
    p_pack.add_argument("in_dir", type=Path)
    p_pack.add_argument("archive", type=Path)
    p_pack.add_argument("--no-minify", action="store_true", help="do not compact the XML")

    p_val = sub.add_parser("validate", help="check zip, manifest, relationships, XML, round-trip")
    p_val.add_argument("archive", type=Path)
    p_val.add_argument("--json", action="store_true")
    p_val.add_argument("--no-roundtrip", action="store_true", help="skip the library open test")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "unpack":
            result = unpack(args.archive, args.out_dir, pretty=not args.no_pretty)
            print(json.dumps({"ok": not result["raw_parts"], "out_dir": str(args.out_dir), "parts": len(result["parts"]), "malformed_xml": result["raw_parts"]}))
            return 0 if not result["raw_parts"] else 1
        if args.command == "pack":
            count = pack(args.in_dir, args.archive, minify=not args.no_minify)
            result = validate(args.archive)
            print(json.dumps({"ok": result["ok"], "archive": str(args.archive), "parts": count, "errors": result["errors"]}))
            return 0 if result["ok"] else 1
        result = validate(args.archive, roundtrip=not args.no_roundtrip)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Validate: {args.archive}")
            for key, value in result["stats"].items():
                print(f"  {key}: {value}")
            for item in result["errors"]:
                print(f"[ERROR] {item}")
            for item in result["warnings"]:
                print(f"[WARNING] {item}")
            print("Result: " + ("PASS" if result["ok"] else "FAIL"))
        return 0 if result["ok"] else 1
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
