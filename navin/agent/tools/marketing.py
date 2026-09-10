# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool over the same Marketing desk store as Studio #/marketing."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    NumberSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)

_CONTENT_OPTIONS = ObjectSchema(
    description="Draft changes for content-options. Read its updated_at from snapshot as revision. TikTok choices must come from the user; consent is confirmed in Studio after edits.",
    required=["revision"], additional_properties=False,
    revision=NumberSchema(description="Exact updated_at of the current content row."),
    title=StringSchema("Post title."), body=StringSchema("Complete post text."),
    creative_id=StringSchema("Existing generated image/video creative ID from the desk."),
    media_type=StringSchema("Selected attachment type.", enum=["image", "video"]),
    media_path=StringSchema("Existing studio asset or Montage export path."),
    media_url=StringSchema("Public HTTPS media URL owned by the user."),
    duration_s=NumberSchema(description="Verified hosted-video duration, in seconds.", minimum=0),
    reddit_kind=StringSchema("Reddit submission type.", enum=["self", "link"]),
    reddit_subreddit=StringSchema("Subreddit selected by the user."),
    reddit_url=StringSchema("Target HTTPS URL of a Reddit link post."),
    reddit_flair_id=StringSchema("Optional subreddit flair ID selected by the user."),
    privacy_level=StringSchema("TikTok privacy explicitly chosen by the user from creator-info."),
    photo_images=ArraySchema(StringSchema("Public JPEG/WebP URL."), max_items=35),
    photo_cover_index=IntegerSchema(description="User-selected photo cover index.", minimum=0, maximum=34),
    **{key: BooleanSchema(description="User-provided choice; editing it requires a fresh TikTok confirmation in Studio.") for key in (
        "disable_comment", "disable_duet", "disable_stitch", "brand_content_toggle", "brand_organic_toggle", "is_aigc", "auto_add_music",
    )},
)


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Marketing desk action. status/snapshot/watch are reads. "
            "brand saves Brand Memory. understand scans the current project. "
            "pipeline runs understand+position+research+plan+content+creatives+launch. "
            "harvest fetches a live product URL and fills brand, SEO, social and ads. "
            "produce generates brand images, clips or voice when providers are configured. "
            "approve-content / schedule-content / publish move one post to its channel "
            "(LinkedIn, Facebook, Instagram, TikTok, Reddit from Settings > Channels, plus X, Telegram, email, webhook, blog) with a UTM link; "
            "publish-status follows accepted media processing, creator-info gets current TikTok choices, "
            "and content-capabilities validates the selected media. Never invent TikTok consent or privacy choices. "
            "measure pulls real counters (channel metrics + Plausible/Matomo). "
            "start/stop/schedule/tick drive the growth loop on a wall-clock "
            "calendar (not heartbeat). watch is the silent heartbeat.",
            enum=[
                "status",
                "snapshot",
                "brand",
                "settings",
                "secret",
                "connection",
                "understand",
                "product",
                "position",
                "research",
                "competitor",
                "plan",
                "campaign",
                "approve",
                "content",
                "approve-content",
                "schedule-content",
                "unschedule",
                "retire",
                "publish",
                "publish-status",
                "creator-info",
                "content-capabilities",
                "content-options",
                "measure",
                "creative",
                "harvest",
                "seo",
                "social",
                "ads",
                "produce",
                "generate",
                "vision",
                "metrics",
                "improve",
                "launch",
                "pipeline",
                "watch",
                "tick",
                "start",
                "stop",
                "schedule",
            ],
        ),
        workspace=StringSchema("Absolute project root for action=understand or pipeline."),
        site=StringSchema("Live product URL for action=harvest or understand source_kind=url."),
        goal=StringSchema("Campaign goal such as 1000 signups."),
        days=StringSchema("Plan horizon: 7, 30 or 90."),
        signups=StringSchema("Numeric signup target, or measured signups for action=metrics."),
        id=StringSchema("Campaign, creative or content id (approve, vision, approve-content, schedule-content, publish, retire)."),
        hook=StringSchema("Winning angle to double down on."),
        angle=StringSchema("Editorial angle a routed model must develop for action=content."),
        channel=StringSchema("Connector to test for action=connection: linkedin, x, facebook, instagram, tiktok, reddit, telegram, email, webhook, blog, analytics."),
        scheduled_at=StringSchema("When to post for action=schedule-content: ISO date, epoch seconds or +2h / +1d."),
        dry_run=StringSchema("true to render the post and its tracked link without sending (action=publish)."),
        content_options=_CONTENT_OPTIONS,
        secret_name=StringSchema("Key name for action=secret (linkedin_token, x_api_key, facebook_page_token, telegram_bot_token, webhook_secret, plausible_key, matomo_token...)."),
        secret_value=StringSchema("Key value for action=secret; empty clears it."),
        settings=StringSchema("JSON settings patch for action=settings: execution_mode, auto_publish, ai_assist, publish{channel{...}}, analytics{provider,site_id,base_url,goal}, channels, utm_campaign."),
        company=StringSchema("Brand company name."),
        tone=StringSchema("Brand tone."),
        audience=StringSchema("Brand audience / ICP."),
        name=StringSchema("Competitor name for action=competitor."),
        note=StringSchema("Competitor note."),
        verdict=StringSchema("Vision verdict: PASS, WARN or BLOCK."),
        score=StringSchema("Vision score 0-100."),
        notes=StringSchema("Vision notes."),
        traffic=StringSchema("Measured traffic for action=metrics."),
        leads=StringSchema("Measured leads for action=metrics."),
        channels=StringSchema("Comma channels for content or plan."),
        kinds=StringSchema("Comma creative kinds: image, video, audio, banner, or pack names brand/posts."),
        brief=StringSchema("Free-text product or campaign brief."),
        by_content=StringSchema("JSON object of content_id → views/clicks/conversions for action=metrics."),
        schedule=StringSchema(
            "JSON loop schedule for start/schedule: kind (daily, weekdays, "
            "weekend, weekly, monthly), hour, minute, weekday, day, tz."
        ),
        run_now=StringSchema("true to run one growth cycle immediately when starting the loop."),
        tz=StringSchema("IANA timezone for the Marketing loop schedule."),
        force=StringSchema("true to cycle now on action=tick, even if the next slot is later."),
        required=["action"],
    )
)
class MarketingTool(Tool):
    """Read and steer the Marketing Agent OS. Never invent live traffic."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "marketing"

    @property
    def description(self) -> str:
        return (
            "Navin Marketing Agent OS (Studio #/marketing). Same book as Studio, "
            "Tauri, navin marketing, and python -m navin.marketing.desk_cli: "
            "brand memory, product understanding, campaigns, content, creatives, "
            "analytics and the growth loop. Use status first. "
            "Drive the loop with start/stop/schedule/tick. "
            "Social accounts (Reddit, Instagram, Facebook, TikTok, LinkedIn) live in "
            "Settings > Channels: connect OAuth, turn the channel On, then publish. "
            "Never invent traffic, spend or published posts."
        )

    @property
    def read_only(self) -> bool:
        return False

    def call_read_only(self, arguments: Any) -> bool:
        action = str((arguments or {}).get("action") or "").strip().lower()
        return action in {"status", "snapshot", "watch", "content-capabilities"}

    async def execute(self, **kwargs: Any) -> Any:
        from navin.marketing.errors import MarketingError
        from navin.webui.marketing_desk_api import handle_marketing_action

        action = str(kwargs.get("action") or "").strip().lower()
        from navin.agent.tools.context import is_heartbeat_turn
        from navin.marketing.heartbeat import HEARTBEAT_MARKETING_ACTIONS

        if is_heartbeat_turn() and action not in HEARTBEAT_MARKETING_ACTIONS:
            return ToolResult.error(
                "Refused on heartbeat. Marketing silent checks may only run "
                "status/snapshot/watch. Understand, publish and loop ticks stay on the desk."
            )
        body: dict[str, Any] = {}
        if kwargs.get("workspace"):
            body["workspace"] = kwargs.get("workspace")
        if kwargs.get("site"):
            body["site"] = kwargs.get("site")
        if kwargs.get("goal") or kwargs.get("brief"):
            body["goal"] = kwargs.get("goal") or kwargs.get("brief")
        if kwargs.get("days") not in (None, ""):
            body["days"] = kwargs.get("days")
        elif action in {"plan", "campaign", "pipeline"}:
            body["days"] = 30
        if kwargs.get("signups") not in (None, ""):
            body["signups"] = kwargs.get("signups")
        elif action in {"plan", "campaign", "pipeline"}:
            body["signups"] = 1000
        if kwargs.get("id"):
            body["id"] = kwargs.get("id")
        if action == "content-options":
            options = kwargs.get("content_options")
            if not isinstance(options, dict):
                return ToolResult.error("content_options must be an object with the draft revision")
            if any(key in options for key in {"publish_consent", "music_usage_confirmed", "branded_content_policy_confirmed"}):
                return ToolResult.error("TikTok consent must be confirmed by the user in Studio after reviewing the post")
            errors = _CONTENT_OPTIONS.validate_value(options, "content_options")
            if errors:
                return ToolResult.error("; ".join(errors))
            body.update(options)
        if kwargs.get("hook"):
            body["hook"] = kwargs.get("hook")
        if kwargs.get("angle"):
            body["angle"] = kwargs.get("angle")
        if kwargs.get("channel"):
            body["channel"] = kwargs.get("channel")
        if kwargs.get("scheduled_at"):
            body["scheduled_at"] = kwargs.get("scheduled_at")
        if kwargs.get("dry_run") not in (None, ""):
            body["dry_run"] = str(kwargs.get("dry_run") or "").strip().lower() in {"1", "true", "yes"}
        if action == "secret":
            body["name"] = kwargs.get("secret_name") or kwargs.get("name") or ""
            body["value"] = kwargs.get("secret_value") or ""
        raw_settings = str(kwargs.get("settings") or "").strip()
        if raw_settings:
            try:
                parsed_settings = json.loads(raw_settings)
            except json.JSONDecodeError:
                return ToolResult.error("settings must be JSON")
            if isinstance(parsed_settings, dict):
                body["settings"] = parsed_settings
        brand = {
            key: kwargs[key]
            for key in ("company", "tone", "audience")
            if str(kwargs.get(key) or "").strip()
        }
        if brand:
            body["brand"] = brand
        if kwargs.get("name"):
            body["name"] = kwargs.get("name")
            body["note"] = kwargs.get("note") or ""
        if kwargs.get("verdict"):
            body["verdict"] = kwargs.get("verdict")
        if kwargs.get("score") not in (None, ""):
            body["score"] = kwargs.get("score")
        if kwargs.get("notes"):
            body["notes"] = kwargs.get("notes")
        for metric in ("traffic", "leads"):
            if kwargs.get(metric) not in (None, ""):
                body[metric] = kwargs.get(metric)
        if kwargs.get("channels"):
            body["channels"] = [
                part.strip()
                for part in str(kwargs.get("channels")).replace(";", ",").split(",")
                if part.strip()
            ]
        if kwargs.get("kinds"):
            body["kinds"] = [
                part.strip()
                for part in str(kwargs.get("kinds")).replace(";", ",").split(",")
                if part.strip()
            ]
        raw_hits = str(kwargs.get("by_content") or "").strip()
        if raw_hits:
            try:
                parsed_hits = json.loads(raw_hits)
            except json.JSONDecodeError:
                return ToolResult.error("by_content must be JSON")
            if isinstance(parsed_hits, dict):
                body["by_content"] = parsed_hits
        if action == "tick":
            body["force"] = str(kwargs.get("force") or "true").strip().lower() in {
                "1",
                "true",
                "yes",
            }
        if kwargs.get("run_now") not in (None, ""):
            body["run_now"] = str(kwargs.get("run_now") or "").strip().lower() in {
                "1",
                "true",
                "yes",
            }
        if str(kwargs.get("tz") or "").strip():
            body["tz"] = str(kwargs.get("tz")).strip()
        raw_schedule = str(kwargs.get("schedule") or "").strip()
        if raw_schedule:
            try:
                parsed = json.loads(raw_schedule)
            except json.JSONDecodeError:
                return ToolResult.error("schedule must be JSON")
            if isinstance(parsed, dict):
                body["schedule"] = parsed
        try:
            payload = await asyncio.to_thread(handle_marketing_action, action, body)
        except MarketingError as exc:
            return ToolResult.error(exc.message)
        if action in {"status", "snapshot"}:
            kpis = payload.get("kpis") or {}
            loop = payload.get("loop") or {}
            product = payload.get("product") or {}
            from navin.marketing.social_channels import social_ready_summary

            social = ", ".join(social_ready_summary())
            text = (
                f"Marketing desk. product={product.get('name') or 'none'} "
                f"armed={payload.get('armed')} campaigns={kpis.get('campaigns')} "
                f"content={kpis.get('content')} signups={kpis.get('signups')} "
                f"loop={loop.get('phase')} {loop.get('last_result') or ''}"
            )
            if social:
                text += f" social={social}"
            return ToolResult(text)
        if action in {"publish", "post"}:
            report = payload.get("publish") or {}
            sent = []
            manual = []
            pending = list(report.get("pending") or [])
            for row in report.get("sent") or []:
                content = row.get("content") if isinstance(row.get("content"), dict) else row
                receipt = content.get("receipt") if isinstance(content.get("receipt"), dict) else {}
                if row.get("pending") or content.get("status") == "publishing":
                    pending.append(row)
                elif row.get("manual") or row.get("via") == "manual" or receipt.get("mode") == "manual":
                    manual.append((row, content))
                else:
                    sent.append((row, content))
            failed = report.get("failed") or []
            preview = report.get("preview") or []
            processing = f"{len(pending)} publishing, " if pending else ""
            lines = [f"publish: {len(sent)} sent, {processing}{len(manual)} manual, {len(failed)} failed, {len(preview)} preview, {len(report.get('skipped') or [])} waiting"]
            for row, content in sent:
                lines.append(f"- sent {content.get('channel') or row.get('channel')} {content.get('id') or row.get('id')} {content.get('published_url') or row.get('url') or ''}".rstrip())
            for row, content in manual:
                channel = content.get("channel") or row.get("channel")
                content_id = content.get("id") or row.get("id")
                text = row.get("text") or content.get("publish_text") or content.get("body") or ""
                lines.append(
                    f"- manual {channel} {content_id}: marked published in the desk; "
                    f"copy and paste this text to publish:\n{text}"
                )
            for row in pending:
                content = row.get("content") if isinstance(row.get("content"), dict) else row
                receipt = content.get("receipt") or {}
                lines.append(f"- publishing {content.get('channel') or row.get('channel')} {content.get('id') or row.get('id')}: {receipt.get('phase') or 'processing'}; publication is not confirmed")
                if row.get("error") or receipt.get("error"):
                    lines.append(str(row.get("error") or receipt["error"]))
            for row in failed:
                content = row.get("content") if isinstance(row.get("content"), dict) else row
                lines.append(f"- failed {content.get('channel') or row.get('channel')} {content.get('id') or row.get('id')}: {row.get('error') or ''}")
            for row in preview:
                lines.append(f"- preview via {row.get('via')}:\n{row.get('text') or ''}")
            return ToolResult("\n".join(lines))
        if action == "publish-status":
            result = payload.get("publication_status") or {}
            row = result.get("content") or {}
            receipt = row.get("receipt") or {}
            state = "publishing; publication is not confirmed" if result.get("pending") else "publication confirmed" if result.get("confirmed") else str(row.get("status") or "publication not confirmed")
            text = f"{row.get('channel') or ''} {row.get('id') or ''}: {state}"
            if row.get("published_url") and result.get("confirmed"):
                text += f"\n{row['published_url']}"
            if result.get("error") or receipt.get("error"):
                text += f"\n{result.get('error') or receipt['error']}"
            return ToolResult(text)
        if action in {"creator-info", "content-capabilities"}:
            key = "creator_info" if action == "creator-info" else "content_capabilities"
            return ToolResult(json.dumps(payload.get(key) or {}, ensure_ascii=False))
        if action == "content-options":
            content = next((row for row in payload.get("content") or [] if row.get("id") == body.get("id")), {})
            return ToolResult(f"Updated draft {body.get('id')}. creative_id={content.get('creative_id') or body.get('creative_id') or 'none'}. Review its media and publication choices before approving or publishing.")
        if action in {"measure", "sync-metrics"}:
            report = payload.get("measure") or {}
            return ToolResult(
                f"measure: {report.get('engagement', 0)} posts with channel metrics, "
                f"{report.get('traffic', 0)} tracked links with visits"
                + (f" via {report.get('provider')}" if report.get("provider") else "")
                + (f"; error: {report.get('error')}" if report.get("error") else "")
            )
        if action in {"connection", "test-connection"}:
            result = payload.get("connection") or {}
            if result.get("ok"):
                return ToolResult(f"{result.get('channel') or result.get('provider') or 'connection'} ok: {result.get('account') or result.get('traffic', '')}")
            return ToolResult.error(str(result.get("error") or "connection failed"))
        if action == "secret":
            return ToolResult("secret saved" if body.get("value") else "secret cleared")
        return payload
