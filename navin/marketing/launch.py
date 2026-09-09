# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Product launch kit from brand + product + positioning."""

from __future__ import annotations

from typing import Any

from navin.marketing import ai
from navin.marketing.content import adapt_message, core_message
from navin.marketing.store import MarketingStore

_KIT = (
    ("positioning", "Product positioning"),
    ("landing", "Landing copy"),
    ("seo", "SEO metadata"),
    ("og", "OpenGraph image brief"),
    ("screenshots", "Product screenshot list"),
    ("demo_video", "Demo video brief"),
    ("launch_video", "Launch video brief"),
    ("linkedin", "LinkedIn announcement"),
    ("x", "X thread"),
    ("reddit", "Reddit version"),
    ("producthunt", "Product Hunt assets"),
    ("email", "Email announcement"),
    ("blog", "Blog launch article"),
    ("press", "Press kit"),
)


def build_launch_kit(store: MarketingStore) -> dict[str, Any]:
    product = store.load_product()
    positioning = store.load_positioning()
    brand = store.load_brand()
    name = str(product.get("name") or brand.get("product") or "product")

    def _without_name(text: Any) -> str:
        raw = str(text or "").strip()
        if raw.lower().startswith(name.lower()):
            raw = raw[len(name) :].lstrip(" -.,:;")
        return raw

    pitch = _without_name(product.get("one_liner") or positioning.get("value_prop"))
    settings = store.load_settings()
    written: dict[str, str] = {}
    if name != "product" and ai.enabled(settings):
        written = ai.write_launch_kit(
            [(key, label) for key, label in _KIT if key not in {"positioning", "screenshots"}],
            {**product, "name": name},
            brand,
            positioning,
            settings,
        )
    model = written.pop("__model__", "")
    items: list[dict[str, Any]] = []
    for key, label in _KIT:
        if key in written:
            body = written[key]
        elif key in {"linkedin", "x", "reddit", "producthunt", "email", "blog"}:
            body = adapt_message(key, product, positioning)
        elif key == "positioning":
            body = str(positioning.get("statement") or core_message(product, positioning))
        elif key == "landing":
            body = (
                f"{name}: {pitch or 'start here'}. "
                f"For {positioning.get('icp')}. CTA: start free."
            )
        elif key == "seo":
            body = (
                f"title: {name} - {pitch or positioning.get('pain')}. "
                f"description: {product.get('pain') or pitch}."
            )
        elif key == "screenshots":
            shots = product.get("screenshots") or []
            body = ", ".join(shots) if shots else "capture the core workflow in the sandbox"
        else:
            body = f"{label} for {name}: {pitch or positioning.get('pain')}"
        folder = store.root / "launch"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{key}.md"
        (folder / filename).write_text(f"# {label}\n\n{body}\n", encoding="utf-8")
        items.append(
            {
                "id": key,
                "label": label,
                "body": body,
                "status": "ready",
                "file": filename,
                "path": str(folder / filename),
                "source": "model" if key in written else "template",
            }
        )
    kit = store.save_launch(
        {
            "status": "ready",
            "product": name,
            "items": items,
            "folder": str(store.root / "launch"),
            "model": model,
            "written": len(written),
        }
    )
    store.append_journal(
        {"kind": "launch", "text": f"launch kit ready for {name} ({len(items)} files, {len(written)} by model)"}
    )
    return kit
