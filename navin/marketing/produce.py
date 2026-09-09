# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Propose and generate brand / post images, clips and voice from the desk."""

from __future__ import annotations

import asyncio
from typing import Any

from navin.marketing.assets import copy_generated
from navin.marketing.creative import brief_creatives
from navin.marketing.pack import PACKS, creative_prompt, keep_creative
from navin.marketing.store import MarketingStore


def _prompt(store: MarketingStore, kind: str, placement: str) -> str:
    return creative_prompt(store, kind, placement)


def _creative_score(row: dict[str, Any]) -> int:
    score = 0
    if row.get("preview"):
        score += 10
    if row.get("status") == "produced":
        score += 5
    if row.get("source") == "site":
        score += 3
    return score


def _dedupe_creatives(store: MarketingStore) -> None:
    rows = store.load_creatives()
    best: dict[tuple[Any, Any], dict[str, Any]] = {}
    extras: list[dict[str, Any]] = []
    for row in rows:
        key = (row.get("kind"), row.get("placement"))
        if not key[1]:
            extras.append(row)
            continue
        current = best.get(key)
        if current is None or _creative_score(row) > _creative_score(current):
            best[key] = row
    merged = list(best.values()) + extras
    if len(merged) != len(rows):
        store.save_creatives(merged)


def _ensure_briefs(
    store: MarketingStore,
    kinds: list[str],
    *,
    pack: str = "",
) -> list[dict[str, Any]]:
    wanted = {item for item in kinds if item in {"image", "video", "audio", "banner"}}
    if not wanted:
        wanted = {"image"}
    _dedupe_creatives(store)
    existing = store.load_creatives()
    have = {(row.get("kind"), row.get("placement")) for row in existing}
    from navin.marketing.content import product_facts

    bound = product_facts(store)["name"]
    chosen: tuple[tuple[str, str, str], ...] = ()
    pack_key = (pack or "").strip().lower()
    if pack_key in PACKS:
        chosen = PACKS[pack_key]
    else:
        if "image" in wanted or "banner" in wanted:
            chosen += PACKS["image"]
        if "video" in wanted:
            chosen += PACKS["video"]
        if "audio" in wanted:
            chosen += PACKS["audio"]
    pack = chosen
    created: list[dict[str, Any]] = []
    for kind, placement, aspect in pack:
        if (kind, placement) in have:
            current = next(
                (row for row in existing if row.get("kind") == kind and row.get("placement") == placement),
                None,
            )
            hay = str((current or {}).get("prompt") or "")
            stale = (
                "navinprojects" in hay.lower()
                or "pme et equipes produit qui veulent scaler" in hay.lower()
                or (bound != "the product" and bound.lower() not in hay.lower())
            )
            if current and stale:
                store.upsert_creative(
                    {
                        **current,
                        "prompt": _prompt(store, kind, placement),
                        "aspect": aspect or current.get("aspect"),
                    }
                )
            continue
        row = store.upsert_creative(
            {
                "kind": kind,
                "placement": placement,
                "aspect": aspect,
                "prompt": _prompt(store, kind, placement),
                "status": "brief",
            }
        )
        created.append(row)
    if not created and not existing:
        created = brief_creatives(store, kinds=list(wanted))
    if pack_key in {"social", "posts", "clips", "brand"}:
        kept = [row for row in store.load_creatives() if keep_creative(row)]
        if len(kept) != len(store.load_creatives()):
            store.save_creatives(kept)
    for row in store.load_creatives():
        hay = str(row.get("prompt") or "")
        if "navinprojects" not in hay.lower().replace(" ", ""):
            continue
        store.upsert_creative(
            {
                **row,
                "prompt": _prompt(store, str(row.get("kind") or "image"), str(row.get("placement") or "post")),
            }
        )
    return created


def _refs(store: MarketingStore) -> list[str]:
    from navin.marketing.assets import resolve_asset

    paths: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        clean = str(path or "").strip()
        if not clean or clean in seen:
            return
        seen.add(clean)
        paths.append(clean)

    harvest = store.load_harvest()
    for item in harvest.get("images") or []:
        if isinstance(item, dict):
            _add(str(item.get("path") or ""))
            asset = resolve_asset(store, str(item.get("name") or ""))
            if asset is not None:
                _add(str(asset))
        if len(paths) >= 4:
            return paths[:4]
    for row in store.load_creatives():
        if row.get("status") not in {"produced", "harvested", "ready"}:
            continue
        _add(str(row.get("path") or ""))
        asset = resolve_asset(store, str(row.get("asset") or ""))
        if asset is not None:
            _add(str(asset))
        if len(paths) >= 4:
            break
    return paths[:4]


def _try_generate_image(store: MarketingStore, row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from navin.config.loader import load_config
        from navin.providers.image_generation import (
            get_image_gen_provider,
            image_gen_provider_configs,
        )
        from navin.providers.media_credentials import media_credentials_ready
        from navin.utils.artifacts import store_generated_image_artifact
    except Exception:
        return None
    try:
        config = load_config()
        img_cfg = config.tools.image_generation
        if not media_credentials_ready(img_cfg.provider, image_gen_provider_configs(config).get(img_cfg.provider)):
            return {"skipped": "image provider is not configured"}
        cls = get_image_gen_provider(img_cfg.provider)
        if cls is None:
            return {"skipped": f"unsupported image provider {img_cfg.provider}"}
        provider = image_gen_provider_configs(config).get(img_cfg.provider)
        client = cls(
            api_key=getattr(provider, "api_key", None),
            api_base=getattr(provider, "api_base", None),
            extra_headers=getattr(provider, "extra_headers", None),
            extra_body=getattr(provider, "extra_body", None),
            proxy=getattr(provider, "proxy", None),
        )

        async def _run() -> Any:
            return await client.generate(
                prompt=str(row.get("prompt") or ""),
                model=img_cfg.model,
                reference_images=_refs(store),
                aspect_ratio=str(row.get("aspect") or img_cfg.default_aspect_ratio or "1:1"),
                image_size=img_cfg.default_image_size,
            )

        response = asyncio.run(_run())
        images = list(getattr(response, "images", None) or [])
        if not images:
            return {"skipped": "image provider returned no stills"}
        artifact = store_generated_image_artifact(
            images[0],
            prompt=str(row.get("prompt") or ""),
            model=img_cfg.model,
            save_dir="marketing",
            provider=img_cfg.provider,
        )
        copied = copy_generated(store, artifact["path"], hint=str(row.get("placement") or "image"))
        if not copied:
            return {"skipped": "could not store generated image"}
        return {
            "preview": copied["preview"],
            "asset": copied["name"],
            "path": copied["path"],
            "status": "produced",
            "provider": img_cfg.provider,
        }
    except Exception as exc:  # noqa: BLE001 - desk must stay usable without media keys
        return {"skipped": str(exc)[:180]}


def _try_generate_video(store: MarketingStore, row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from navin.config.loader import load_config
        from navin.providers.media_credentials import media_credentials_ready
        from navin.providers.video_generation import get_video_gen_provider
        from navin.utils.artifacts import store_generated_video_artifact
    except Exception:
        return None
    try:
        config = load_config()
        vid_cfg = config.tools.video_generation
        provider = getattr(config.providers, vid_cfg.provider, None)
        if not media_credentials_ready(vid_cfg.provider, provider):
            return {"skipped": "video provider is not configured"}
        cls = get_video_gen_provider(vid_cfg.provider)
        if cls is None:
            return {"skipped": f"unsupported video provider {vid_cfg.provider}"}
        client = cls(
            api_key=getattr(provider, "api_key", None),
            api_base=getattr(provider, "api_base", None),
            extra_headers=getattr(provider, "extra_headers", None),
            extra_body=getattr(provider, "extra_body", None),
            proxy=getattr(provider, "proxy", None),
        )
        refs = _refs(store)

        async def _run() -> Any:
            return await client.generate(
                prompt=str(row.get("prompt") or ""),
                model=vid_cfg.model,
                reference_image=refs[0] if refs else None,
                aspect_ratio=str(row.get("aspect") or vid_cfg.default_aspect_ratio or "9:16"),
                duration_seconds=min(int(getattr(vid_cfg, "default_duration_seconds", 8) or 8), 10),
            )

        response = asyncio.run(_run())
        raw = getattr(response, "video", None)
        mime = str(getattr(response, "mime", None) or "video/mp4")
        if raw is None:
            return {"skipped": "video provider returned no clip"}
        artifact = store_generated_video_artifact(
            raw if isinstance(raw, (bytes, bytearray)) else bytes(raw),
            mime=mime,
            prompt=str(row.get("prompt") or ""),
            model=vid_cfg.model,
            save_dir="marketing-video",
            provider=vid_cfg.provider,
        )
        copied = copy_generated(store, artifact["path"], hint=str(row.get("placement") or "clip"))
        if not copied:
            return {"skipped": "could not store generated clip"}
        return {
            "preview": copied["preview"],
            "asset": copied["name"],
            "path": copied["path"],
            "status": "produced",
            "provider": vid_cfg.provider,
        }
    except Exception as exc:  # noqa: BLE001
        return {"skipped": str(exc)[:180]}


def _try_generate_speech(store: MarketingStore, row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from navin.agent.tools.speech_generation import SpeechGenerationTool
        from navin.config.loader import load_config
        from navin.config.paths import get_data_dir
    except Exception:
        return None
    try:
        config = load_config()
        tool = SpeechGenerationTool(
            workspace=get_data_dir(),
            config=config.tools.speech_generation,
        )
        from navin.marketing.content import product_facts

        facts = product_facts(store)
        text = str(
            row.get("prompt")
            or facts["one_liner"]
            or f"{facts['name']} removes {facts['pain'] or 'manual work'}."
        )[:400]
        result = asyncio.run(tool.execute(text=text))
        text_result = str(result)
        path = ""
        for line in text_result.splitlines():
            if line.strip().endswith((".mp3", ".wav", ".m4a")):
                path = line.strip().split()[-1]
                break
        if not path:
            return {"skipped": "speech tool returned no file"}
        copied = copy_generated(store, path, hint="voice")
        if not copied:
            return {"skipped": "could not store voice over"}
        return {**copied, "status": "produced"}
    except Exception as exc:  # noqa: BLE001
        return {"skipped": str(exc)[:180]}


def produce_assets(
    store: MarketingStore,
    *,
    kinds: list[str] | None = None,
    generate: bool = True,
    max_images: int = 3,
    max_videos: int = 1,
    max_audio: int = 1,
    creative_id: str = "",
    pack: str = "",
) -> dict[str, Any]:
    picked = [str(item).lower() for item in (kinds or ["image"]) if str(item).strip()]
    target = str(creative_id or "").strip()
    pack_key = (pack or "").strip().lower()
    if target:
        rows = [row for row in store.load_creatives() if str(row.get("id") or "") == target]
        if not rows:
            return {"produced": 0, "skipped": [f"creative {target} not found"], "creatives": store.load_creatives()}
        picked = [str(rows[0].get("kind") or "image")]
    else:
        _ensure_briefs(store, picked, pack=pack_key)
        rows = store.load_creatives()
        if pack_key in PACKS:
            allowed = {(kind, placement) for kind, placement, _aspect in PACKS[pack_key]}
            rows = [row for row in rows if (row.get("kind"), row.get("placement")) in allowed]
    produced = 0
    skipped: list[str] = []
    images_left = max_images if any(item in {"image", "banner"} for item in picked) else 0
    videos_left = max_videos if "video" in picked else 0
    audio_left = max_audio if "audio" in picked else 0
    if pack_key == "brand":
        images_left = min(images_left, 2)
    if pack_key == "posts":
        images_left = min(max(images_left, 4), 4)
    if pack_key in {"social", "clips"}:
        images_left = 4 if pack_key == "social" else 0
        videos_left = 4 if pack_key == "clips" else min(max(videos_left, 4), 4)
    if not generate:
        store.append_journal({"kind": "produce", "text": "briefs ready - generation skipped"})
        return {"produced": 0, "skipped": ["generation skipped"], "creatives": store.load_creatives()}
    for row in rows:
        if row.get("preview") and row.get("status") in {"produced", "harvested", "ready"}:
            if target:
                skipped.append("this creative already has a preview")
            continue
        kind = str(row.get("kind") or "")
        result: dict[str, Any] | None = None
        if kind in {"image", "banner"} and images_left > 0:
            result = _try_generate_image(store, row)
            if result and result.get("preview"):
                images_left -= 1
        elif kind == "video" and videos_left > 0:
            result = _try_generate_video(store, row)
            if result and result.get("preview"):
                videos_left -= 1
        elif kind == "audio" and audio_left > 0:
            result = _try_generate_speech(store, row)
            if result and result.get("preview"):
                audio_left -= 1
        else:
            continue
        if not result:
            continue
        if result.get("skipped"):
            skipped.append(str(result["skipped"]))
            continue
        store.upsert_creative({**row, **result, "id": row.get("id")})
        produced += 1
    store.append_journal({"kind": "produce", "text": f"produced {produced} assets"})
    if store.load_content():
        from navin.marketing.social import build_social_calendar

        build_social_calendar(store)
    return {
        "produced": produced,
        "skipped": list(dict.fromkeys(skipped))[:6],
        "creatives": store.load_creatives(),
    }
