"""Pack app templates and read them from the public S3 prefix."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from navin.templates.apps.catalog import get_app_template, list_app_templates
from navin.templates.apps.install import (
    _COPY_IGNORE,
    AppTemplateError,
    apply_package_patches,
    aws_object_key,
    local_cache_dir,
    write_plugin_overlay,
)
from navin.templates.apps.schema import AWS_TEMPLATE_PREFIX, AWS_TEMPLATES_BASE_URL

CATALOG_NAME = "catalog.json"


def templates_base_url() -> str:
    raw = (
        os.environ.get("NAVIN_TEMPLATES_BASE_URL", "").strip()
        or os.environ.get("UPDATE_BASE_URL", "").strip()
        or AWS_TEMPLATES_BASE_URL
    )
    return raw.rstrip("/")


def template_public_url(slug: str) -> str:
    return f"{templates_base_url()}/{aws_object_key(slug)}"


def catalog_public_url() -> str:
    return f"{templates_base_url()}/{AWS_TEMPLATE_PREFIX}/{CATALOG_NAME}"


def fetch_remote_catalog(timeout_s: float = 12.0) -> dict[str, Any] | None:
    url = catalog_public_url()
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    templates = payload.get("templates")
    if not isinstance(templates, list) or not templates:
        return None
    return payload


def download_template_tarball(slug: str, dest: Path, timeout_s: float = 60.0) -> bool:
    url = template_public_url(slug)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            dest.write_bytes(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        if dest.exists():
            dest.unlink(missing_ok=True)
        return False
    return dest.is_file() and dest.stat().st_size > 0


def extract_template_tarball(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        _safe_extract(tf, dest)


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    dest_res = dest.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for member in tf.getmembers():
            name = member.name.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise AppTemplateError(f"unsafe tar member: {member.name}", status=400)
        tf.extractall(tmp_path)
        children = [p for p in tmp_path.iterdir() if p.name not in {".", ".."}]
        root = children[0] if len(children) == 1 and children[0].is_dir() else tmp_path
        for src in root.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(root)
            target = (dest_res / rel).resolve()
            if dest_res not in target.parents and target != dest_res:
                raise AppTemplateError(f"unsafe extract path: {rel}", status=400)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


def pack_template(slug: str, out_dir: Path) -> dict[str, Any]:
    row = get_app_template(slug)
    if row is None:
        raise AppTemplateError(f"unknown template: {slug}", status=404)
    out_dir.mkdir(parents=True, exist_ok=True)
    tar_path = out_dir / f"{slug}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / slug
        root.mkdir(parents=True, exist_ok=True)
        cache = local_cache_dir(slug)
        copied_source = False
        if cache.is_dir():
            try:
                shutil.copytree(
                    cache,
                    root,
                    dirs_exist_ok=True,
                    ignore=_COPY_IGNORE,
                    symlinks=True,
                    ignore_dangling_symlinks=True,
                )
            except shutil.Error:
                pass
            copied_source = True
        apply_package_patches(root, slug)
        write_plugin_overlay(root, row)
        (root / "NAVIN_SOURCE.json").write_text(
            json.dumps(
                {
                    "slug": slug,
                    "source_github": row["source_github"],
                    "copied_source": copied_source,
                    "aws_key": aws_object_key(slug),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(root, arcname=slug)
    return {
        "slug": slug,
        "path": str(tar_path),
        "bytes": tar_path.stat().st_size,
        "copied_source": copied_source,
        "aws_key": aws_object_key(slug),
        "aws_url": template_public_url(slug),
    }


def catalog_export() -> dict[str, Any]:
    templates = []
    for row in list_app_templates():
        templates.append(
            {
                **row,
                "local_cache": False,
                "installed": False,
                "can_publish": True,
                "aws_key": aws_object_key(row["slug"]),
                "aws_url": template_public_url(row["slug"]),
            }
        )
    return {
        "schema": "navin-app-template-catalog.v1",
        "aws_prefix": AWS_TEMPLATE_PREFIX,
        "aws_base_url": templates_base_url(),
        "source": "aws",
        "publish_policy": "S3 templates/v1 - make aws-upload-temp overwrites.",
        "templates": templates,
    }


def pack_all(out_dir: Path, slug: str | None = None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [get_app_template(slug)] if slug else list_app_templates()
    packed: list[dict[str, Any]] = []
    for row in rows:
        if row is None:
            raise AppTemplateError(f"unknown template: {slug}", status=404)
        packed.append(pack_template(row["slug"], out_dir))
    catalog = catalog_export()
    catalog_path = out_dir / CATALOG_NAME
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "out_dir": str(out_dir),
        "catalog": str(catalog_path),
        "packed": packed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pack Navin app templates for S3.")
    parser.add_argument("command", choices=["pack"], help="pack tarballs + catalog.json")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--slug", default="", help="Pack one slug only")
    args = parser.parse_args(argv)
    result = pack_all(Path(args.out), slug=args.slug or None)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
