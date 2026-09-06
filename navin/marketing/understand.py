"""Product understanding from brand memory, a bound workspace, or a live site."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from loguru import logger

from navin.marketing import ai
from navin.marketing.harvest import apply_harvest, harvest_live_site
from navin.marketing.store import MarketingStore

_SKIP_DIRS = {".git", ".navin", "node_modules", "__pycache__", ".venv", "dist", "build"}
_README_NAMES = ("README.md", "README.fr.md", "readme.md", "docs/README.md")
_CATEGORY_HINTS = (
    ("invoice", "invoice automation"),
    ("factur", "invoice automation"),
    ("crm", "crm"),
    ("seo", "seo platform"),
    ("trad", "trading desk"),
    ("tender", "tender desk"),
    ("market", "marketing studio"),
    ("saas", "b2b saas"),
)


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _first_sentence(text: str) -> str:
    body = re.sub(r"`[^`]+`", "", text)
    body = re.sub(r"\[(.*?)\]\([^)]+\)", r"\1", body)
    for line in body.splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or clean.startswith("```") or clean.startswith("|"):
            continue
        return clean[:240]
    return ""


def _read_text(path: Path, limit: int = 12_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _package_name(root: Path) -> str:
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            name = str(data.get("name") or "").strip()
            if name:
                return name
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        for line in _read_text(pyproject, 2000).splitlines():
            if line.strip().startswith("name"):
                match = re.search(r'name\s*=\s*["\']([^"\']+)', line)
                if match:
                    return match.group(1)
    return root.name


def _guess_category(name: str, one_liner: str) -> str:
    hay = f"{name} {one_liner}".lower()
    for needle, category in _CATEGORY_HINTS:
        if needle in hay:
            return category
    return "product"


def _model_brief(
    store: MarketingStore,
    product: dict[str, Any],
    brand: dict[str, Any],
    harvest: dict[str, Any] | None,
    *,
    wanted: bool,
) -> dict[str, Any]:
    """Category, pain and value read by a routed model; empty when off or unusable."""
    if not wanted or not str(product.get("name") or "").strip():
        return {}
    settings = store.load_settings()
    if not ai.enabled(settings):
        return {}
    try:
        brief = ai.write_product_brief(product, brand, harvest, settings)
    except Exception as exc:  # noqa: BLE001 - the keyword guess is the fallback
        logger.info("marketing understand: model brief skipped ({})", exc)
        return {}
    return brief or {}


def _guess_pain(category: str) -> str:
    if "invoice" in category:
        return "saisie manuelle des factures"
    if category == "crm":
        return "pipeline commercial disperse"
    if "seo" in category:
        return "visibilite organique trop lente a construire"
    return ""


def _guess_value(name: str, category: str) -> str:
    # No invented figures: a value claim comes from the user, the site or a measured result.
    if "invoice" in category:
        return f"{name} capture, verifie et exporte les factures sans saisie manuelle"
    return ""


def _is_projects_root(root: Path) -> bool:
    """True for ~/NavinProjects: a container of projects, never the product."""
    if root.name.lower() == "navinprojects":
        return True
    try:
        from navin.config.paths import get_default_workspace_path

        return root.resolve() == get_default_workspace_path().resolve()
    except OSError:
        return False


def _usable_product_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or name.lower().replace(" ", "") == "navinprojects":
        return ""
    return name


def scan_workspace(workspace: str | Path | None) -> dict[str, Any]:
    """Deterministic scan of a project root. Empty dict when unbound."""
    if not workspace:
        return {}
    root = Path(workspace).expanduser()
    try:
        root = root.resolve()
    except OSError:
        return {}
    if not root.is_dir() or _is_projects_root(root):
        return {}
    docs: list[str] = []
    one_liner = ""
    heading = ""
    for relative in _README_NAMES:
        path = root / relative
        if not path.is_file():
            continue
        text = _read_text(path)
        docs.append(relative)
        heading = heading or _first_heading(text)
        one_liner = one_liner or _first_sentence(text)
    stack: list[str] = []
    if (root / "package.json").is_file():
        stack.append("node")
    if (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file():
        stack.append("python")
    screenshots: list[str] = []
    for path in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and path.is_file():
            screenshots.append(path.relative_to(root).as_posix())
        if len(screenshots) >= 8:
            break
    name = heading or _package_name(root)
    category = _guess_category(name, one_liner)
    return {
        "name": name,
        "one_liner": one_liner,
        "category": category,
        "pain": _guess_pain(category),
        "value_prop": _guess_value(name, category),
        "stack": stack,
        "workspace": str(root),
        "docs": docs,
        "screenshots": screenshots,
    }


def _serve_workspace_shots(store: MarketingStore, scanned: dict[str, Any]) -> list[str]:
    """Copy project stills into the marketing assets folder so Product can preview them."""
    from navin.marketing.assets import file_url, ingest_local_file

    root = Path(str(scanned.get("workspace") or ""))
    shots = [str(item) for item in (scanned.get("screenshots") or []) if str(item).strip()]
    if not root.is_dir() or not shots:
        return []
    served: list[str] = []
    for rel in shots[:8]:
        src = (root / rel).resolve()
        try:
            src.relative_to(root.resolve())
        except ValueError:
            continue
        name = ingest_local_file(store, src, hint="shot")
        if name:
            served.append(file_url(name))
    return served


def peek_live_site(url: str, *, timeout_s: float = 8.0) -> dict[str, str]:
    """Best-effort title / description from a public homepage the user bound."""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {}
    request = Request(
        raw,
        headers={"User-Agent": "NavinMarketing/1.0 (+local desk understand)"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - user-bound product URL
            html = response.read(40_000).decode("utf-8", errors="replace")
    except OSError:
        return {"site": raw}
    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip()[:160]
    desc = ""
    meta = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.I,
    ) or re.search(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
        html,
        re.I,
    )
    if meta:
        desc = re.sub(r"\s+", " ", meta.group(1)).strip()[:240]
    return {"site": raw, "name": title, "one_liner": desc}


def understand_product(
    store: MarketingStore,
    *,
    workspace: str | Path | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge brand memory, workspace scan, live site or a named product."""
    brand = store.load_brand()
    current = store.load_product()
    incoming = extras if isinstance(extras, dict) else {}
    kind = str(incoming.get("source_kind") or current.get("source_kind") or "").strip().lower()
    site = str(incoming.get("site") or brand.get("site") or current.get("site") or "").strip()
    if kind in {"url", "product"}:
        workspace = None
    scanned = scan_workspace(workspace)
    peeked: dict[str, str] = {}
    harvested: dict[str, Any] = {}
    if site and kind in {"url", "product"}:
        current_harvest = store.load_harvest()
        reuse = (
            not incoming.get("force_harvest")
            and str(current_harvest.get("site") or "") == site
            and bool(current_harvest.get("images") or current_harvest.get("one_liner") or current_harvest.get("name"))
        )
        harvested = current_harvest if reuse else harvest_live_site(site)
        if harvested.get("name") or harvested.get("one_liner") or harvested.get("images"):
            if not reuse:
                apply_harvest(store, harvested)
            peeked = {
                "site": str(harvested.get("site") or site),
                "name": str(harvested.get("name") or ""),
                "one_liner": str(harvested.get("one_liner") or ""),
            }
        else:
            peeked = peek_live_site(site)
        site = peeked.get("site") or harvested.get("site") or site
    if workspace and not kind:
        kind = "workspace"
    elif site and not kind:
        kind = "url"
    elif incoming.get("name") and not kind:
        kind = "product"
    name = (
        _usable_product_name(incoming.get("name"))
        or _usable_product_name(scanned.get("name"))
        or _usable_product_name(peeked.get("name"))
        or _usable_product_name(brand.get("product"))
        or _usable_product_name(current.get("name"))
        or _usable_product_name(brand.get("company"))
    )
    one_liner = str(
        incoming.get("one_liner")
        or scanned.get("one_liner")
        or peeked.get("one_liner")
        or brand.get("description")
        or current.get("one_liner")
        or ""
    ).strip()
    explicit_category = str(incoming.get("category") or scanned.get("category") or "").strip()
    explicit_pain = str(incoming.get("pain") or scanned.get("pain") or "").strip()
    explicit_value = str(incoming.get("value_prop") or scanned.get("value_prop") or "").strip()
    guess = _guess_category(name, one_liner)
    current_category = str(current.get("category") or "").strip()
    # A category the user typed earlier stays; a keyword guess or an older model brief is redone.
    user_kept = bool(current_category) and current_category != guess and not str(current.get("brief_model") or "")
    brief = _model_brief(
        store,
        {"name": name, "one_liner": one_liner, "site": site, "stack": scanned.get("stack") or [], "docs": scanned.get("docs") or []},
        brand,
        harvested or store.load_harvest(),
        wanted=not explicit_category and not user_kept,
    )
    category = explicit_category or brief.get("category") or current_category or guess
    if explicit_category:
        brief_model = ""
    elif brief:
        brief_model = str(brief.get("model") or "")
    else:
        brief_model = str(current.get("brief_model") or "")
    product = store.save_product(
        {
            "name": name,
            "one_liner": one_liner,
            "category": category,
            "pain": explicit_pain or brief.get("pain") or str(current.get("pain") or "") or _guess_pain(category),
            "value_prop": explicit_value
            or brief.get("value_prop")
            or str(current.get("value_prop") or "")
            or _guess_value(name or "the product", category),
            "audience": str(incoming.get("audience") or brief.get("audience") or current.get("audience") or ""),
            "search_terms": list(incoming.get("search_terms") or brief.get("search_terms") or current.get("search_terms") or []),
            "brief_model": brief_model,
            "stack": incoming.get("stack") or scanned.get("stack") or current.get("stack") or [],
            "workspace": str(incoming.get("workspace") or scanned.get("workspace") or (current.get("workspace") if kind == "workspace" else "") or ""),
            "site": site,
            "source_kind": kind,
            "docs": incoming.get("docs") or scanned.get("docs") or current.get("docs") or [],
            "screenshots": incoming.get("screenshots")
            or _serve_workspace_shots(store, scanned)
            or current.get("screenshots")
            or [],
        }
    )
    brand_patch: dict[str, Any] = {}
    if name and not str(brand.get("product") or "").strip():
        brand_patch["product"] = name
        brand_patch["company"] = brand.get("company") or name
    if site:
        brand_patch["site"] = site
    if brand_patch:
        store.save_brand(brand_patch)
    store.append_journal({"kind": "understand", "text": f"product understood: {product.get('name') or 'unnamed'} ({kind or 'manual'})"})
    return product
