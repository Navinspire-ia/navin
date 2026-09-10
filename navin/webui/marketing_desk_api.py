# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP payloads for the Navin Marketing desk."""

from __future__ import annotations

from typing import Any

from navin.loop_schedule import LoopScheduleError
from navin.marketing.ads import propose_ads
from navin.marketing.content import generate_content
from navin.marketing.creative import apply_vision, brief_creatives
from navin.marketing.desk import (
    approve_and_fill,
    fill_from_site,
    run_pipeline,
    ship_social_pack,
    snapshot,
)
from navin.marketing.errors import MarketingError
from navin.marketing.growth import ingest_metrics, run_growth_cycle
from navin.marketing.harvest import apply_harvest, harvest_live_site
from navin.marketing.heartbeat import HEARTBEAT_MARKETING_ACTIONS
from navin.marketing.launch import build_launch_kit
from navin.marketing.loop import maybe_tick, start_loop, stop_loop, update_loop_schedule
from navin.marketing.measure import collect_metrics, test_analytics
from navin.marketing.plan import build_campaign
from navin.marketing.positioning import build_positioning
from navin.marketing.produce import produce_assets
from navin.marketing.publish import approve as approve_content
from navin.marketing.publish import (
    publish_content,
    publish_due,
    schedule,
    test_connection,
    unschedule,
)
from navin.marketing.research import build_research, ingest_competitor
from navin.marketing.seo import build_seo, ingest_ranking
from navin.marketing.social import build_social_calendar
from navin.marketing.store import MarketingStore
from navin.marketing.understand import understand_product


def _store() -> MarketingStore:
    return MarketingStore()


def _int_field(body: dict[str, Any], name: str, default: int) -> int:
    raw = body.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise MarketingError(f"{name} must be an integer", status=400) from exc


def _clean_patch(data: dict[str, Any]) -> dict[str, Any]:
    """Drop empty scalars so a merge does not wipe Brand Memory or settings."""
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[key] = value
    return cleaned


def _object_or_fields(
    body: dict[str, Any],
    key: str,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    nested = body.get(key)
    if isinstance(nested, dict):
        return _clean_patch(nested)
    return _clean_patch({name: body[name] for name in fields if name in body})


def handle_marketing_action(action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    store = _store()
    act = (action or "snapshot").strip().lower()
    from navin.agent.tools.context import is_heartbeat_turn

    if is_heartbeat_turn() and act not in HEARTBEAT_MARKETING_ACTIONS:
        raise MarketingError(
            "Refused on heartbeat. Marketing silent checks may only run "
            "status/snapshot/watch. Understand, publish and loop ticks stay on the desk."
        )
    if act in {"snapshot", "status"}:
        return snapshot(store)
    if act == "brand":
        incoming = _object_or_fields(
            body,
            "brand",
            ("company", "product", "site", "description", "tone", "colors", "fonts", "logo", "audience", "forbidden", "competitors", "liked_examples", "languages", "countries", "industries"),
        )
        if not incoming:
            raise MarketingError("brand fields are required")
        store.save_brand(incoming)
        store.append_journal({"kind": "brand", "text": "brand memory updated"})
        return snapshot(store)
    if act == "settings":
        incoming = _object_or_fields(
            body,
            "settings",
            ("execution_mode", "auto_publish", "ai_assist", "winner_multiple", "publish", "analytics", "channels", "utm_campaign", "media_base_url"),
        )
        # Nested connector forms legitimately send empty strings to clear a field.
        for key in ("publish", "analytics", "channels"):
            if isinstance(body.get(key), dict):
                incoming[key] = body[key]
        if "utm_campaign" in body and isinstance(body.get("utm_campaign"), str):
            incoming["utm_campaign"] = body["utm_campaign"]
        if "media_base_url" in body and isinstance(body.get("media_base_url"), str):
            incoming["media_base_url"] = body["media_base_url"]
        if not incoming:
            raise MarketingError("settings payload is required")
        store.save_settings(incoming)
        return snapshot(store)
    if act == "secret":
        name = str(body.get("name") or "").strip()
        if not name:
            raise MarketingError("secret name is required", status=400)
        store.save_secret(name, str(body.get("value") or ""))
        store.append_journal({"kind": "settings", "text": f"secret {name} {'set' if body.get('value') else 'cleared'}"})
        return snapshot(store)
    if act in {"oauth-configure", "oauth-connect", "oauth-disconnect", "oauth-select-account"}:
        from navin.agent.tools.context import current_request_context
        from navin.marketing.oauth import (
            configure_connection,
            disconnect,
            select_account,
            start_connection,
        )

        provider = str(body.get("provider") or "")
        if act == "oauth-configure":
            result = configure_connection(store, provider, body)
        elif act == "oauth-connect":
            context = current_request_context()
            result = start_connection(
                store,
                provider,
                session_key=context.session_key if context else "",
                return_to=str(body.get("return_to") or ""),
            )
        elif act == "oauth-disconnect":
            result = disconnect(store, provider)
        else:
            result = select_account(store, provider, str(body.get("account_id") or ""))
        snap = snapshot(store)
        snap["oauth"] = result
        return snap
    if act in {"connection", "test-connection"}:
        channel = str(body.get("channel") or "").strip().lower()
        if channel in {"analytics", "plausible", "matomo"}:
            result = test_analytics(store)
        else:
            result = test_connection(store, channel)
        snap = snapshot(store)
        snap["connection"] = result
        return snap
    if act in {"publish", "post"}:
        ids = body.get("ids") if isinstance(body.get("ids"), list) else [body.get("id")]
        ids = [str(item).strip() for item in ids if str(item or "").strip()]
        dry_run = bool(body.get("dry_run"))
        if ids:
            results = [
                publish_content(store, cid, force=bool(body.get("force")), origin="manual", dry_run=dry_run)
                for cid in ids
            ]
            report = {
                "sent": [row for row in results if row.get("ok") and not row.get("dry_run") and not row.get("pending")],
                "failed": [row for row in results if not row.get("ok") and not row.get("pending")],
                "preview": [row for row in results if row.get("dry_run")],
                "pending": [row for row in results if row.get("pending")],
            }
        else:
            report = publish_due(store, limit=_int_field(body, "limit", 0) or None, dry_run=dry_run)
        snap = snapshot(store)
        snap["publish"] = report
        return snap
    if act == "content-options":
        from navin.marketing.publication_options import update_publication_options

        update_publication_options(store, str(body.get("id") or ""), body)
        return snapshot(store)
    if act in {"creator-info", "content-capabilities", "publish-status"}:
        from navin.marketing.publish import content_capabilities, creator_info, publish_status

        if act == "creator-info":
            result = creator_info(store, str(body.get("channel") or "tiktok"))
            key = "creator_info"
        elif act == "content-capabilities":
            result = content_capabilities(store, store.get_content(str(body.get("id") or "")))
            key = "content_capabilities"
        else:
            result = publish_status(store, str(body.get("id") or ""))
            key = "publication_status"
        snap = snapshot(store)
        snap[key] = result
        return snap
    if act in {"approve-content", "content-approve"}:
        cid = str(body.get("id") or "").strip()
        if not cid:
            raise MarketingError("id is required", status=400)
        approve_content(store, cid)
        return snapshot(store)
    if act in {"schedule-content", "content-schedule"}:
        cid = str(body.get("id") or "").strip()
        if not cid:
            raise MarketingError("id is required", status=400)
        schedule(store, cid, body.get("scheduled_at") or body.get("when"))
        return snapshot(store)
    if act in {"unschedule", "content-ready"}:
        cid = str(body.get("id") or "").strip()
        if not cid:
            raise MarketingError("id is required", status=400)
        unschedule(store, cid)
        return snapshot(store)
    if act in {"retire", "content-retire"}:
        cid = str(body.get("id") or "").strip()
        if not cid:
            raise MarketingError("id is required", status=400)
        store.upsert_content({"id": cid, "status": "retired"})
        store.append_journal({"kind": "content", "text": f"retired {cid}"})
        return snapshot(store)
    if act in {"measure", "sync-metrics"}:
        report = collect_metrics(store)
        snap = snapshot(store)
        snap["measure"] = report
        return snap
    if act in {"understand", "product"}:
        extras = body.get("product") if isinstance(body.get("product"), dict) else {}
        extras = _clean_patch(
            {
                **extras,
                **{
                    key: body[key]
                    for key in ("name", "one_liner", "site", "source_kind", "category", "pain")
                    if key in body
                },
            }
        ) or None
        understand_product(
            store,
            workspace=str(body.get("workspace") or (extras or {}).get("workspace") or "") or None,
            extras=extras,
        )
        return snapshot(store)
    if act in {"position", "positioning"}:
        build_positioning(store)
        return snapshot(store)
    if act == "research":
        hits = body.get("hits") if isinstance(body.get("hits"), list) else None
        build_research(
            store,
            hits=hits,
            market=str(body.get("market") or ""),
            trends=body.get("trends") if isinstance(body.get("trends"), list) else None,
            web=body.get("web", True) is not False,
        )
        build_seo(store)
        return snapshot(store)
    if act == "harvest":
        site = str(body.get("site") or body.get("url") or store.load_product().get("site") or store.load_brand().get("site") or "").strip()
        if not site:
            raise MarketingError("site url is required", status=400)
        report = harvest_live_site(site)
        if not report.get("site"):
            raise MarketingError("could not fetch that site", status=400)
        apply_harvest(store, report)
        understand_product(
            store,
            extras={
                "source_kind": "url",
                "site": site,
                "name": report.get("name"),
                "one_liner": report.get("one_liner"),
            },
        )
        fill_from_site(store)
        return snapshot(store)
    if act == "seo":
        if str(body.get("keyword") or "").strip() and str(body.get("url") or "").strip():
            ingest_ranking(store, body)
        else:
            build_seo(store)
        return snapshot(store)
    if act in {"social", "calendar"}:
        generate_content(
            store,
            channels=body.get("channels") if isinstance(body.get("channels"), list) else None,
            campaign_id=str(body.get("campaign_id") or body.get("id") or ""),
        )
        build_social_calendar(store)
        return snapshot(store)
    if act in {"ads", "adset"}:
        propose_ads(store)
        return snapshot(store)
    if act in {"produce", "generate"}:
        raw_kinds = body.get("kinds")
        if isinstance(raw_kinds, list):
            kinds = raw_kinds
        elif isinstance(raw_kinds, str) and raw_kinds.strip():
            kinds = [part.strip() for part in raw_kinds.replace(";", ",").split(",") if part.strip()]
        else:
            kinds = None
        if not kinds and body.get("kind"):
            kinds = [str(body.get("kind"))]
        pack = str(body.get("pack") or "")
        if not pack and kinds and len(kinds) == 1 and str(kinds[0]).lower() in {"brand", "posts", "social"}:
            pack = str(kinds[0]).lower()
            kinds = ["image", "video"] if pack == "social" else ["image"]
        result = produce_assets(
            store,
            kinds=kinds,
            generate=body.get("generate", True) is not False,
            creative_id=str(body.get("id") or body.get("creative_id") or ""),
            pack=pack,
        )
        snap = snapshot(store)
        snap["produce"] = {key: result[key] for key in ("produced", "skipped") if key in result}
        return snap
    if act == "competitor":
        incoming = _object_or_fields(body, "competitor", ("name", "pricing", "angle", "note", "url", "source"))
        if not incoming.get("name"):
            raise MarketingError("competitor name is required")
        ingest_competitor(store, incoming)
        return snapshot(store)
    if act in {"plan", "campaign"}:
        build_campaign(
            store,
            goal=str(body.get("goal") or ""),
            days=_int_field(body, "days", 30),
            signups=_int_field(body, "signups", 1000),
            channels=body.get("channels") if isinstance(body.get("channels"), list) else None,
            label=str(body.get("label") or ""),
        )
        return snapshot(store)
    if act == "approve":
        cid = str(body.get("id") or body.get("campaign_id") or "").strip()
        if not cid:
            raise MarketingError("id is required")
        filled = approve_and_fill(store, cid)
        return filled["desk"]
    if act in {"ship", "social-pack"}:
        result = ship_social_pack(store, generate=body.get("generate", True) is not False)
        snap = result["desk"]
        snap["produce"] = result.get("produce") or {}
        snap["ship"] = {
            "channels": ["linkedin", "facebook", "instagram", "tiktok"],
            "content": len(result.get("content") or []),
            "produced": (result.get("produce") or {}).get("produced") or 0,
            "skipped": (result.get("produce") or {}).get("skipped") or [],
        }
        return snap
    if act == "content":
        generate_content(
            store,
            channels=body.get("channels") if isinstance(body.get("channels"), list) else None,
            campaign_id=str(body.get("campaign_id") or body.get("id") or ""),
            hook=str(body.get("hook") or ""),
            angle=str(body.get("angle") or ""),
            fresh=bool(body.get("fresh")),
        )
        return snapshot(store)
    if act == "creative":
        brief_creatives(
            store,
            kinds=body.get("kinds") if isinstance(body.get("kinds"), list) else None,
            campaign_id=str(body.get("campaign_id") or ""),
        )
        return snapshot(store)
    if act == "vision":
        cid = str(body.get("id") or "").strip()
        if not cid:
            raise MarketingError("id is required")
        score = body.get("score")
        if score not in (None, ""):
            try:
                score = float(score)
            except (TypeError, ValueError) as exc:
                raise MarketingError("score must be a number", status=400) from exc
        else:
            score = None
        apply_vision(
            store,
            cid,
            verdict=str(body.get("verdict") or ""),
            score=score,
            notes=str(body.get("notes") or ""),
        )
        return snapshot(store)
    if act in {"metrics", "analytics"}:
        ingest_metrics(store, body)
        return snapshot(store)
    if act in {"improve", "optimize"}:
        growth = run_growth_cycle(store)
        snap = snapshot(store)
        snap["growth"] = growth
        return snap
    if act == "launch":
        build_launch_kit(store)
        return snapshot(store)
    if act == "pipeline":
        extras = body.get("product") if isinstance(body.get("product"), dict) else {}
        extras = _clean_patch(
            {
                **extras,
                **{
                    key: body[key]
                    for key in ("name", "one_liner", "site", "source_kind", "category", "pain")
                    if key in body
                },
            }
        ) or None
        result = run_pipeline(
            store,
            workspace=str(body.get("workspace") or "") or None,
            goal=str(body.get("goal") or ""),
            days=_int_field(body, "days", 30),
            signups=_int_field(body, "signups", 1000),
            extras=extras,
        )
        return result["desk"]
    if act == "watch":
        from navin.marketing.watch import run_watch

        result = run_watch(store)
        snap = snapshot(store)
        snap["watch"] = result
        return snap
    if act == "start":
        try:
            started = start_loop(
                store,
                schedule=body.get("schedule") if isinstance(body.get("schedule"), dict) else None,
                run_now=bool(body.get("run_now")),
                tz=str(body.get("tz") or "") or None,
            )
        except LoopScheduleError as exc:
            raise MarketingError(str(exc), status=400) from exc
        snap = snapshot(store)
        snap["loop_tick"] = started
        return snap
    if act == "schedule":
        raw = body.get("schedule")
        if not isinstance(raw, dict):
            raise MarketingError("schedule is required", status=400)
        try:
            update_loop_schedule(store, schedule=raw, tz=str(body.get("tz") or "") or None)
        except LoopScheduleError as exc:
            raise MarketingError(str(exc), status=400) from exc
        return snapshot(store)
    if act == "stop":
        stop_loop(store)
        return snapshot(store)
    if act == "tick":
        ticked = maybe_tick(store, force=bool(body.get("force", True)))
        snap = snapshot(store)
        snap["loop_tick"] = ticked
        return snap
    raise MarketingError(f"unknown marketing action {act}")
