"""Marketing publish connectors, measured analytics, model layer and the loop: real calls, scripted wire."""

from __future__ import annotations

import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from navin.marketing import ai, measure, publish, research
from navin.marketing.content import REFILL_ANGLES, generate_content, render_post
from navin.marketing.errors import MarketingError
from navin.marketing.growth import improve_from_winner, scoreboard
from navin.marketing.publish import (
    oauth1_header,
    render_for_channel,
    signature_base_string,
    utm_link,
)
from navin.marketing.store import MarketingStore
from navin.marketing.understand import understand_product
from navin.webui.marketing_desk_api import handle_marketing_action


class Wire:
    """Scripted HTTP: replies by URL prefix (first match wins), every call recorded."""

    def __init__(self, replies: dict[str, tuple[int, dict[str, str], Any]] | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> tuple[int, dict[str, str], bytes]:
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "data": data})
        for prefix, (status, resp_headers, body) in self.replies.items():
            if url.startswith(prefix):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                return status, resp_headers, raw
        return 404, {}, b'{"message":"no route"}'


def _store(tmp: str) -> MarketingStore:
    store = MarketingStore(Path(tmp))
    understand_product(store, extras={"name": "InvoiceAI", "site": "https://invoiceai.example", "one_liner": "Invoices that chase themselves."})
    return store


def _post(store: MarketingStore, channel: str, status: str = "approved", **extra: Any) -> dict[str, Any]:
    return store.upsert_content(
        {
            "channel": channel,
            "title": "Chasing invoices",
            "hook": "Stop chasing",
            "body": extra.pop("body", "Invoices that chase themselves. Start free."),
            "status": status,
            "hashtags": ["#invoicing"],
            **extra,
        }
    )


class LinkAndTextTests(unittest.TestCase):
    def test_utm_link_keeps_query_and_tags_the_post(self) -> None:
        link = utm_link("invoiceai.example/pricing?ref=nav", source="linkedin", campaign="launch", content="cnt-1")
        parts = urlsplit(link)
        self.assertEqual(parts.scheme, "https")
        query = parse_qs(parts.query)
        self.assertEqual(query["ref"], ["nav"])
        self.assertEqual(query["utm_source"], ["linkedin"])
        self.assertEqual(query["utm_medium"], ["social"])
        self.assertEqual(query["utm_campaign"], ["launch"])
        self.assertEqual(query["utm_content"], ["cnt-1"])
        self.assertEqual(utm_link("", source="x", campaign="a", content="b"), "")

    def test_render_for_channel_appends_link_and_caps_x(self) -> None:
        link = "https://invoiceai.example/?utm_source=x&utm_content=cnt-1"
        text = render_for_channel({"channel": "linkedin", "body": "No link here"}, link)
        self.assertTrue(text.endswith(link))
        long_text = render_for_channel({"channel": "x", "body": "word " * 100}, link)
        self.assertLessEqual(publish.x_length(long_text), 280)
        self.assertGreater(publish.x_length(long_text), 240)
        self.assertIn(link, long_text)
        self.assertTrue(long_text.split("\n")[0].endswith("..."))
        # A body that already carries the link is not doubled.
        self.assertEqual(render_for_channel({"channel": "x", "body": f"Hello {link}"}, link).count(link), 1)

    def test_render_post_joins_hook_body_cta_and_tags(self) -> None:
        text = render_post({"hook": "Stop chasing", "body": "Stop chasing invoices today.", "cta": "Start free.", "hashtags": ["#invoicing"]})
        self.assertEqual(text.count("Stop chasing"), 1)
        self.assertTrue(text.endswith("#invoicing"))


class ConcurrentPublishTests(unittest.TestCase):
    def test_overlapping_requests_cannot_publish_the_same_content_twice(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_settings({"publish": {"blog": {"enabled": True}}})
            row = _post(store, "blog")
            posting, release = Event(), Event()
            calls: list[str] = []

            def poster(*args: Any, **kwargs: Any) -> dict[str, Any]:
                calls.append(kwargs["row"]["id"])
                posting.set()
                if not release.wait(5):
                    raise AssertionError("concurrent request did not finish")
                return {"id": "fictional-one-post", "url": "https://example.invalid/one"}

            with patch.dict(publish._POSTERS, {"blog": poster}), ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(publish.publish_content, store, row["id"])
                try:
                    self.assertTrue(posting.wait(5))
                    second = pool.submit(publish.publish_content, MarketingStore(Path(tmp)), row["id"])
                    with self.assertRaises(MarketingError) as error:
                        second.result(timeout=5)
                    self.assertEqual(error.exception.status, 409)
                finally:
                    release.set()
                self.assertTrue(first.result(timeout=5)["ok"])
            self.assertEqual(calls, [row["id"]])
            self.assertEqual(store.get_content(row["id"])["remote_id"], "fictional-one-post")
            with self.assertRaises(MarketingError):
                publish.publish_content(store, row["id"])


class OAuthTests(unittest.TestCase):
    def test_oauth1_signature_matches_the_documented_twitter_example(self) -> None:
        header = oauth1_header(
            "POST",
            "https://api.twitter.com/1.1/statuses/update.json?include_entities=true",
            consumer_key="xvz1evFS4wEEPTGEFPHBog",
            consumer_secret="kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
            token="370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
            token_secret="LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
            extra_params={"status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
            nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
            timestamp=1318622958,
        )
        self.assertTrue(header.startswith("OAuth "))
        self.assertIn('oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"', header)
        base = signature_base_string(
            "POST",
            "https://api.twitter.com/1.1/statuses/update.json?include_entities=true",
            {"oauth_consumer_key": "k", "status": "a b"},
        )
        self.assertTrue(base.startswith("POST&https%3A%2F%2Fapi.twitter.com%2F1.1%2Fstatuses%2Fupdate.json&"))
        self.assertIn("status%3Da%2520b", base)


class ChannelStateTests(unittest.TestCase):
    def test_missing_pieces_manual_channels_and_webhook_bridge(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            rows = {row["channel"]: row for row in publish.channel_state(store)}
            self.assertEqual(rows["linkedin"]["missing"], ["linkedin_token"])
            self.assertEqual(rows["facebook"]["missing"], ["facebook_page_token", "page_id"])
            self.assertEqual(rows["instagram"]["mode"], "api")
            self.assertEqual(rows["instagram"]["missing"], ["instagram_access_token", "instagram_user_id"])
            self.assertEqual(rows["youtube"]["mode"], "manual")
            self.assertFalse(rows["instagram"]["ready"])
            self.assertEqual(rows["blog"]["mode"], "file")
            self.assertTrue(rows["blog"]["configured"])
            self.assertFalse(rows["blog"]["ready"])
            self.assertEqual(sorted(rows["x"]["secrets"]), ["x_access_secret", "x_access_token", "x_api_key", "x_api_secret"])
            store.save_settings({"publish": {"linkedin": {"enabled": True, "author": "urn:li:person:abc"}, "webhook": {"enabled": True, "url": "https://hooks.example/n"}}})
            store.save_secret("linkedin_token", "tok")
            rows = {row["channel"]: row for row in publish.channel_state(store)}
            self.assertTrue(rows["linkedin"]["ready"])
            self.assertTrue(rows["linkedin"]["secrets"]["linkedin_token"])
            self.assertEqual(rows["instagram"]["bridge"], "webhook")
            self.assertTrue(rows["instagram"]["ready"])
            self.assertIn("tiktok", publish.sendable_channels(store))
            with self.assertRaises(MarketingError):
                store.save_secret("not_a_key", "x")

    def test_secret_file_is_private_and_never_in_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_secret("x_api_key", "super-secret-key")
            self.assertEqual(oct(store.secrets_path().stat().st_mode & 0o777), "0o600")
            from navin.marketing.desk import snapshot

            self.assertNotIn("super-secret-key", json.dumps(snapshot(store)))
            store.save_secret("x_api_key", "")
            self.assertEqual(store.load_secrets(), {})


class PublishTests(unittest.TestCase):
    def test_unconfigured_channel_is_refused_without_a_call(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            row = _post(store, "linkedin")
            wire = Wire()
            with self.assertRaises(MarketingError) as ctx:
                publish.publish_content(store, row["id"], http=wire)
            self.assertIn("linkedin_token", ctx.exception.message)
            self.assertEqual(wire.calls, [])
            self.assertEqual(store.get_content(row["id"])["status"], "approved")

    def test_linkedin_posts_api_with_article_card_then_ugc_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_settings({"publish": {"linkedin": {"enabled": True, "author": "urn:li:person:abc"}}, "utm_campaign": "launch"})
            store.save_secret("linkedin_token", "tok")
            row = _post(store, "linkedin")
            wire = Wire({"https://api.linkedin.com/rest/posts": (201, {"x-restli-id": "urn:li:share:777"}, {})})
            result = publish.publish_content(store, row["id"], http=wire)
            self.assertTrue(result["ok"])
            call = wire.calls[0]
            self.assertEqual(call["headers"]["Authorization"], "Bearer tok")
            payload = json.loads(call["data"])
            self.assertEqual(payload["author"], "urn:li:person:abc")
            self.assertIn("utm_content=" + row["id"], payload["content"]["article"]["source"])
            self.assertIn("utm_campaign=launch", payload["commentary"])
            saved = store.get_content(row["id"])
            self.assertEqual(saved["status"], "published")
            self.assertEqual(saved["published_url"], "https://www.linkedin.com/feed/update/urn:li:share:777")
            self.assertEqual(saved["remote_id"], "urn:li:share:777")
            self.assertEqual(saved["receipt"]["via"], "linkedin")
            self.assertTrue(any(item["kind"] == "publish" for item in store.load_journal(10)))
            # An app without the Posts API falls back to v2 ugcPosts with an ARTICLE share.
            row2 = _post(store, "linkedin")
            wire = Wire(
                {
                    "https://api.linkedin.com/rest/posts": (426, {}, {"message": "upgrade"}),
                    "https://api.linkedin.com/v2/ugcPosts": (201, {}, {"id": "urn:li:ugcPost:9"}),
                }
            )
            publish.publish_content(store, row2["id"], http=wire)
            legacy = json.loads(wire.calls[1]["data"])["specificContent"]["com.linkedin.ugc.ShareContent"]
            self.assertEqual(legacy["shareMediaCategory"], "ARTICLE")
            self.assertIn("utm_source=linkedin", legacy["media"][0]["originalUrl"])
            self.assertEqual(store.get_content(row2["id"])["remote_id"], "urn:li:ugcPost:9")

    def test_x_signs_with_oauth1_and_caps_to_280(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_settings({"publish": {"x": {"enabled": True}}})
            for name in ("x_api_key", "x_api_secret", "x_access_token", "x_access_secret"):
                store.save_secret(name, name + "-v")
            row = _post(store, "x", body="long " * 80)
            wire = Wire({"https://api.x.com/2/tweets": (201, {}, {"data": {"id": "1234"}})})
            result = publish.publish_content(store, row["id"], http=wire)
            self.assertTrue(result["ok"])
            call = wire.calls[0]
            self.assertTrue(call["headers"]["Authorization"].startswith("OAuth "))
            self.assertIn('oauth_consumer_key="x_api_key-v"', call["headers"]["Authorization"])
            text = json.loads(call["data"])["text"]
            self.assertLessEqual(publish.x_length(text), 280)
            self.assertIn("utm_source=x", text)
            self.assertEqual(store.get_content(row["id"])["published_url"], "https://x.com/i/web/status/1234")

    def test_facebook_form_with_link_and_provider_error_marks_failed(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_settings({"publish": {"facebook": {"enabled": True, "page_id": "99"}}})
            store.save_secret("facebook_page_token", "ptok")
            row = _post(store, "facebook")
            wire = Wire({f"https://graph.facebook.com/{publish.GRAPH_VERSION}/99/feed": (200, {}, {"id": "99_555"})})
            self.assertTrue(publish.publish_content(store, row["id"], http=wire)["ok"])
            form = parse_qs(wire.calls[0]["data"].decode())
            self.assertEqual(form["access_token"], ["ptok"])
            self.assertIn("utm_source=facebook", form["link"][0])
            self.assertEqual(store.get_content(row["id"])["published_url"], "https://www.facebook.com/99_555")
            row2 = _post(store, "facebook")
            bad = Wire({"https://graph.facebook.com": (400, {}, {"error": {"message": "Invalid OAuth access token"}})})
            result = publish.publish_content(store, row2["id"], http=bad)
            self.assertFalse(result["ok"])
            saved = store.get_content(row2["id"])
            self.assertEqual(saved["status"], "failed")
            self.assertIn("Invalid OAuth access token", saved["error"])
            # A failed post can go back to the queue.
            self.assertEqual(publish.unschedule(store, row2["id"])["status"], "ready")

    def test_telegram_sends_photo_when_a_still_exists_else_message(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_settings({"publish": {"telegram": {"enabled": True, "chat_id": "@invoiceai"}}})
            store.save_secret("telegram_bot_token", "bot:tok")
            still = Path(tmp) / "still.png"
            still.write_bytes(b"\x89PNG fake")
            creative = store.upsert_creative({"kind": "image", "placement": "telegram-post", "path": str(still), "status": "produced"})
            row = _post(store, "telegram", creative_id=creative["id"])
            wire = Wire({"https://api.telegram.org/bot": (200, {}, {"ok": True, "result": {"message_id": 42, "chat": {"username": "invoiceai"}}})})
            self.assertTrue(publish.publish_content(store, row["id"], http=wire)["ok"])
            self.assertIn("/sendPhoto", wire.calls[0]["url"])
            self.assertIn(b'name="photo"; filename="still.png"', wire.calls[0]["data"])
            self.assertEqual(store.get_content(row["id"])["published_url"], "https://t.me/invoiceai/42")
            row2 = _post(store, "telegram")
            publish.publish_content(store, row2["id"], http=wire)
            self.assertIn("/sendMessage", wire.calls[-1]["url"])

    def test_webhook_bridges_manual_channels_with_a_signature(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            row = _post(store, "youtube")
            # Without a bridge the post is marked published with copy-ready text.
            plain = publish.publish_content(store, row["id"], http=Wire())
            self.assertTrue(plain["manual"])
            self.assertEqual(store.get_content(row["id"])["receipt"]["mode"], "manual")
            self.assertIn("utm_source=youtube", store.get_content(row["id"])["publish_text"])
            store.save_settings({"publish": {"webhook": {"enabled": True, "url": "https://hooks.example/navin"}}})
            store.save_secret("webhook_secret", "shh")
            row2 = _post(store, "tiktok")
            wire = Wire({"https://hooks.example": (200, {}, {"url": "https://tiktok.com/@x/video/1"})})
            result = publish.publish_content(store, row2["id"], http=wire)
            self.assertEqual(result["via"], "webhook")
            call = wire.calls[0]
            self.assertTrue(call["headers"]["X-Navin-Signature"].startswith("sha256="))
            self.assertEqual(
                call["headers"]["X-Navin-Signature"],
                publish.webhook_signature("shh", call["headers"]["X-Navin-Timestamp"], call["data"]),
            )
            body = json.loads(call["data"])
            self.assertEqual(body["channel"], "tiktok")
            self.assertEqual(body["event"], "marketing.publish")
            saved = store.get_content(row2["id"])
            self.assertEqual(saved["published_url"], "https://tiktok.com/@x/video/1")
            self.assertEqual(saved["receipt"]["via"], "webhook")

    def test_blog_writes_markdown_with_front_matter(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            out = Path(tmp) / "site" / "posts"
            store.save_settings({"publish": {"blog": {"enabled": True, "dir": str(out), "base_url": "https://blog.invoiceai.example"}}})
            row = _post(store, "blog", body="Long form about chasing invoices.")
            self.assertTrue(publish.publish_content(store, row["id"], http=Wire())["ok"])
            files = list(out.glob("*.md"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            self.assertIn('title: "Chasing invoices"', text)
            self.assertIn("utm_source=blog", text)
            self.assertEqual(store.get_content(row["id"])["published_url"], "https://blog.invoiceai.example/chasing-invoices/")

    def test_dry_run_renders_without_posting(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_settings({"publish": {"linkedin": {"enabled": True, "author": "urn:li:person:abc"}}})
            store.save_secret("linkedin_token", "tok")
            row = _post(store, "linkedin")
            wire = Wire()
            result = publish.publish_content(store, row["id"], http=wire, dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertIn("utm_content=" + row["id"], result["text"])
            self.assertEqual(wire.calls, [])
            self.assertEqual(store.get_content(row["id"])["status"], "approved")

    def test_test_connection_records_account_or_error(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_secret("linkedin_token", "tok")
            wire = Wire({"https://api.linkedin.com/v2/userinfo": (200, {}, {"sub": "abc", "name": "Aymen"})})
            result = publish.test_connection(store, "linkedin", http=wire)
            self.assertTrue(result["ok"])
            linkedin = store.load_settings()["publish"]["linkedin"]
            self.assertEqual(linkedin["account"], "Aymen")
            self.assertEqual(linkedin["author"], "urn:li:person:abc")
            self.assertGreater(linkedin["tested_at"], 0)
            bad = publish.test_connection(store, "x", http=Wire())
            self.assertFalse(bad["ok"])
            self.assertIn("HTTP 404", store.load_settings()["publish"]["x"]["last_error"])


class QueueTests(unittest.TestCase):
    def test_parse_when_accepts_iso_epoch_and_relative(self) -> None:
        now = 1_800_000_000.0
        self.assertEqual(publish.parse_when("+2h", now=now), now + 7200)
        self.assertEqual(publish.parse_when("+1d", now=now), now + 86400)
        self.assertEqual(publish.parse_when(1_800_000_500, now=now), 1_800_000_500.0)
        self.assertEqual(publish.parse_when(1_800_000_500_000, now=now), 1_800_000_500.0)
        self.assertAlmostEqual(publish.parse_when("2026-09-02T10:00:00+00:00", now=now), 1788343200.0)
        with self.assertRaises(MarketingError):
            publish.parse_when("next tuesday", now=now)

    def test_publish_due_orders_scheduled_then_approved_within_budget(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_settings({"publish": {"linkedin": {"enabled": True, "author": "urn:li:person:abc"}, "per_cycle": 1}})
            store.save_secret("linkedin_token", "tok")
            now = time.time()
            approved_a = _post(store, "linkedin")
            approved_b = _post(store, "linkedin")
            ready = _post(store, "linkedin", status="ready")
            due = publish.schedule(store, _post(store, "linkedin", status="ready")["id"], now - 60, now=now)
            later = publish.schedule(store, _post(store, "linkedin", status="ready")["id"], "+2h", now=now)
            blocked = _post(store, "x")
            queue = publish.publish_queue(store, now=now)
            self.assertEqual(queue["due"], [due["id"]])
            self.assertEqual(queue["waiting"], [later["id"]])
            self.assertEqual(set(queue["approved"]), {approved_a["id"], approved_b["id"], blocked["id"]})
            self.assertEqual(queue["auto"], [])
            self.assertEqual(queue["blocked"], [blocked["id"]])
            wire = Wire({"https://api.linkedin.com/rest/posts": (201, {"x-restli-id": "urn:li:share:1"}, {})})
            result = publish.publish_due(store, now=now, http=wire)
            sent = [row["id"] for row in result["sent"]]
            # The due post always goes; the budget of one covers a single approved post.
            self.assertEqual(sent[0], due["id"])
            self.assertEqual(len(sent), 2)
            self.assertIn(blocked["id"], result["skipped"])
            self.assertEqual(store.get_content(later["id"])["status"], "scheduled")
            self.assertEqual(store.get_content(ready["id"])["status"], "ready")
            # Autonomous mode with auto_publish picks up ready posts too.
            store.save_settings({"execution_mode": "autonomous", "auto_publish": True, "publish": {"per_cycle": 5}})
            result = publish.publish_due(store, now=now, http=wire)
            self.assertIn(ready["id"], [row["id"] for row in result["sent"]])
            self.assertEqual(store.get_content(ready["id"])["status"], "published")

    def test_approve_then_schedule_then_back_to_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            row = _post(store, "linkedin", status="ready")
            self.assertEqual(publish.approve(store, row["id"])["status"], "approved")
            scheduled = publish.schedule(store, row["id"], "+1h")
            self.assertEqual(scheduled["status"], "scheduled")
            self.assertGreater(scheduled["scheduled_at"], time.time())
            self.assertEqual(publish.unschedule(store, row["id"])["status"], "ready")
            store.upsert_content({"id": row["id"], "status": "published"})
            with self.assertRaises(MarketingError):
                publish.approve(store, row["id"])


class MeasureTests(unittest.TestCase):
    def test_channel_metrics_parsers(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            for name in ("x_api_key", "x_api_secret", "x_access_token", "x_access_secret"):
                store.save_secret(name, "v")
            store.save_secret("facebook_page_token", "ptok")
            store.save_secret("linkedin_token", "tok")
            wire = Wire(
                {
                    "https://api.x.com/2/tweets/555": (200, {}, {"data": {"public_metrics": {"impression_count": 900, "like_count": 12, "reply_count": 3, "retweet_count": 4, "quote_count": 1}}}),
                    "https://graph.facebook.com": (
                        200,
                        {},
                        {
                            "likes": {"summary": {"total_count": 7}},
                            "comments": {"summary": {"total_count": 2}},
                            "shares": {"count": 1},
                            "insights": {"data": [{"name": "post_impressions", "values": [{"value": 300}]}, {"name": "post_clicks", "values": [{"value": 25}]}]},
                        },
                    ),
                    "https://api.linkedin.com/rest/socialActions": (200, {}, {"likesSummary": {"totalLikes": 40}, "commentsSummary": {"aggregatedTotalComments": 5}}),
                }
            )
            x = measure.x_metrics(store, "555", wire)
            self.assertEqual((x["views"], x["likes"], x["shares"]), (900.0, 12.0, 5.0))
            self.assertTrue(wire.calls[0]["headers"]["Authorization"].startswith("OAuth "))
            fb = measure.facebook_metrics(store, "99_555", wire)
            self.assertEqual((fb["views"], fb["clicks"], fb["likes"]), (300.0, 25.0, 7.0))
            li = measure.linkedin_metrics(store, "urn:li:share:1", wire)
            self.assertEqual((li["likes"], li["comments"]), (40.0, 5.0))

    def test_plausible_breakdown_maps_content_goal_and_totals(self) -> None:
        def wire(method: str, url: str, headers: dict[str, str], data: bytes | None) -> tuple[int, dict[str, str], bytes]:
            self.assertEqual(headers["Authorization"], "Bearer pk")
            if "/breakdown" in url and "event%3Agoal" in url:
                return 200, {}, json.dumps({"results": [{"utm_content": "cnt-1", "events": 7, "visitors": 6}]}).encode()
            if "/breakdown" in url:
                return 200, {}, json.dumps({"results": [{"utm_content": "cnt-1", "visitors": 80, "visits": 95}, {"utm_content": "", "visitors": 5}]}).encode()
            return 200, {}, json.dumps({"results": {"visitors": {"value": 1200}, "visits": {"value": 1500}}}).encode()

        per_content, totals = measure.plausible_breakdown({"site_id": "invoiceai.example", "goal": "Signup"}, "pk", wire)
        self.assertEqual(per_content["cnt-1"], {"visitors": 80.0, "visits": 95.0, "signups": 7.0})
        self.assertNotIn("", per_content)
        self.assertEqual(totals["traffic"], 1200.0)

    def test_matomo_breakdown_reads_summary_and_content_report(self) -> None:
        def wire(method: str, url: str, headers: dict[str, str], data: bytes | None) -> tuple[int, dict[str, str], bytes]:
            self.assertEqual(parse_qs((data or b"").decode())["token_auth"], ["mt"])
            method_name = parse_qs(urlsplit(url).query)["method"][0]
            if method_name == "VisitsSummary.get":
                return 200, {}, json.dumps({"nb_uniq_visitors": 900, "nb_visits": 1100}).encode()
            if method_name == "MarketingCampaignsReporting.getContent":
                return 200, {}, json.dumps([{"label": "cnt-9", "nb_visits": 40, "nb_uniq_visitors": 33, "nb_conversions": 2}]).encode()
            return 200, {}, json.dumps({"result": "error"}).encode()

        per_content, totals = measure.matomo_breakdown({"site_id": "3", "base_url": "https://stats.example/"}, "mt", wire)
        self.assertEqual(totals["traffic"], 900.0)
        self.assertEqual(per_content["cnt-9"]["visits"], 40.0)
        self.assertEqual(per_content["cnt-9"]["signups"], 2.0)
        with self.assertRaises(MarketingError):
            measure.matomo_breakdown({"site_id": "3", "base_url": ""}, "mt", wire)

    def test_collect_metrics_merges_channel_and_site_numbers_into_the_scoreboard(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_settings({"analytics": {"provider": "plausible", "site_id": "invoiceai.example"}})
            store.save_secret("plausible_key", "pk")
            for name in ("x_api_key", "x_api_secret", "x_access_token", "x_access_secret"):
                store.save_secret(name, "v")
            row = store.upsert_content({"channel": "x", "body": "posted", "status": "published", "remote_id": "555", "published_at": 1.0, "receipt": {"mode": "api"}})
            other = store.upsert_content({"channel": "x", "body": "quiet", "status": "published", "remote_id": "556", "published_at": 1.0, "receipt": {"mode": "api"}})

            def wire(method: str, url: str, headers: dict[str, str], data: bytes | None) -> tuple[int, dict[str, str], bytes]:
                if url.startswith("https://api.x.com/2/tweets/555"):
                    return 200, {}, json.dumps({"data": {"public_metrics": {"impression_count": 900, "like_count": 12, "reply_count": 3, "retweet_count": 4, "quote_count": 1}}}).encode()
                if url.startswith("https://api.x.com/2/tweets/556"):
                    return 200, {}, json.dumps({"data": {"public_metrics": {"impression_count": 400, "like_count": 1}}}).encode()
                if "/breakdown" in url:
                    return 200, {}, json.dumps({"results": [{"utm_content": row["id"], "visitors": 50, "visits": 61}]}).encode()
                return 200, {}, json.dumps({"results": {"visitors": {"value": 700}, "visits": {"value": 800}}}).encode()

            report = measure.collect_metrics(store, http=wire)
            self.assertEqual(report["engagement"], 2)
            self.assertEqual(report["traffic"], 1)
            self.assertEqual(report["provider"], "plausible")
            saved = store.load_analytics()
            self.assertEqual(saved["traffic"], 700)
            hit = saved["by_content"][row["id"]]
            self.assertEqual((hit["views"], hit["clicks"], hit["likes"], hit["shares"]), (900.0, 61.0, 12.0, 5.0))
            self.assertEqual(saved["by_content"][other["id"]]["views"], 400.0)
            self.assertGreater(store.load_settings()["analytics"]["measured_at"], 0)
            board = scoreboard(store)
            self.assertEqual(board[0]["id"], row["id"])
            self.assertAlmostEqual(board[0]["ctr"], round(61 / 900, 4))

    def test_collect_metrics_without_provider_or_posts_is_a_quiet_noop(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            report = measure.collect_metrics(store, http=Wire())
            self.assertEqual(report["measured"], 0)
            self.assertEqual(report["error"], "")
            store.save_settings({"analytics": {"provider": "plausible", "site_id": "s"}})
            result = measure.test_analytics(store, http=Wire())
            self.assertFalse(result["ok"])
            self.assertIn("plausible_key", result["error"])


class ModelLayerTests(unittest.TestCase):
    def test_clean_copy_and_post_normalisation(self) -> None:
        self.assertEqual(ai.clean_copy("A \u2014 B \u2013 C  \n"), "A - B - C")
        rows = ai._posts([{"hook": "H", "body": "B", "cta": "C", "hashtags": ["#Invoicing", "ai ops", ""]}, {"body": ""}, "junk"], 3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hashtags"], ["#Invoicing", "#aiops"])

    def test_without_a_routed_model_everything_falls_back_to_templates(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            brand = store.load_brand()
            product = store.load_product()
            self.assertIsNone(ai.write_positioning(product, brand, None, store.load_settings()))
            self.assertEqual(ai.write_channel_set(["linkedin"], product, brand, {}, settings=store.load_settings()), {})
            self.assertEqual(ai.write_launch_kit([("landing", "Landing")], product, brand, {}), {})
            routing = ai.routing(store.load_settings())
            self.assertEqual(
                {row["task"] for row in routing["tasks"]},
                {"understand", "positioning", "copy", "variants", "research", "launch"},
            )
            self.assertIsNone(ai.write_product_brief(product, brand, {}, store.load_settings()))
            self.assertEqual(routing["routed"], 0)
            store.save_settings({"ai_assist": False})
            self.assertFalse(ai.enabled(store.load_settings()))
            rows = generate_content(store, channels=["linkedin"])
            self.assertEqual(rows[0]["source"], "template")


class ResearchTests(unittest.TestCase):
    def test_queries_and_heuristic_extraction(self) -> None:
        queries = research.web_queries({"name": "InvoiceAI", "category": "invoice automation"}, {"countries": ["France"]}, {"pain": "chasing late payers"})
        self.assertEqual(queries[0], "InvoiceAI alternatives")
        self.assertIn("invoice automation pricing", queries)
        self.assertLessEqual(len(queries), research.QUERY_LIMIT)
        hits = [
            {"query": "InvoiceAI alternatives", "title": "Pennylane - Comptabilite pour PME", "url": "https://www.pennylane.com/fr/", "snippet": "Des 19 EUR/mois."},
            {"query": "q", "title": "Top 10 best invoicing tools", "url": "https://www.g2.com/categories/invoicing", "snippet": ""},
            {"query": "q", "title": "InvoiceAI vs Pennylane", "url": "https://blog.example/vs", "snippet": ""},
            {"query": "q", "title": "InvoiceAI - Invoice capture", "url": "https://invoiceai.example/", "snippet": ""},
            {"query": "q", "title": "Yooz | AP automation", "url": "https://www.getyooz.com/", "snippet": "Workflow"},
            {"query": "q", "title": "Yooz pricing", "url": "https://www.getyooz.com/pricing", "snippet": "Duplicate host"},
        ]
        rows = research.competitors_from_hits(hits, own_name="InvoiceAI", own_site="https://invoiceai.example")
        self.assertEqual([row["name"] for row in rows], ["Pennylane", "Yooz"])
        self.assertEqual(rows[0]["angle"], "alternative")
        self.assertEqual(rows[0]["pricing"], "19 EUR/mois")
        self.assertEqual(research.keywords_from_hits(hits, ["invoiceai"])[0], "invoiceai")

    def test_buyer_search_terms_lead_the_queries(self) -> None:
        queries = research.web_queries(
            {"name": "Navin", "category": "ai coding agent", "search_terms": ["local ai coding agent", "cursor alternative", "extra"]},
            {},
            {},
        )
        self.assertEqual(queries[:3], ["Navin alternatives", "local ai coding agent", "cursor alternative"])
        self.assertIn("ai coding agent trends " + time.strftime("%Y"), queries)
        self.assertLessEqual(len(queries), research.QUERY_LIMIT)

    def test_trends_keep_market_headlines_only(self) -> None:
        hits = [
            {"title": "State of AI agents report 2026 | Acme Research", "url": "https://acme.example/report"},
            {"title": "30+ Best AI tools (2026)", "url": "https://list.example/best"},
            {"title": "Как обойти блокировки Google в России в 2026", "url": "https://ru.example/x"},
            {"title": "Navin Harness market outlook", "url": "https://navin.live/blog"},
            {"title": "Developer tools market forecast: agents replace seats", "url": "https://analyst.example/forecast"},
            {"title": "AI", "url": "https://short.example/"},
        ]
        self.assertEqual(
            research.trends_from_hits(hits, own_name="Navin"),
            ["State of AI agents report 2026", "Developer tools market forecast: agents replace seats"],
        )

    def test_app_stores_and_directories_are_never_competitors(self) -> None:
        hits = [
            {"query": "Navin alternatives", "title": "Navin 1.0.7 APK download", "url": "https://apkpure.com/navin/app", "snippet": ""},
            {"query": "Navin alternatives", "title": "Navin | F6S", "url": "https://www.f6s.com/navin", "snippet": ""},
            {"query": "q", "title": "Aider - AI pair programming in your terminal", "url": "https://aider.chat/", "snippet": "Open source."},
        ]
        rows = research.competitors_from_hits(hits, own_name="Navin", own_site="https://navin.live")
        self.assertEqual([row["name"] for row in rows], ["Aider"])


class UnderstandBriefTests(unittest.TestCase):
    def test_model_brief_replaces_the_keyword_guess_but_not_a_typed_category(self) -> None:
        brief = {"category": "ai coding agent", "audience": "developers", "pain": "six seats", "value_prop": "Navin owns the agent", "search_terms": ["ai coding agent"], "model": "m"}
        with TemporaryDirectory() as tmp, patch("navin.marketing.understand.ai.enabled", return_value=True), patch(
            "navin.marketing.understand.ai.write_product_brief", return_value=brief
        ) as write:
            store = MarketingStore(Path(tmp))
            product = understand_product(store, extras={"name": "Navin", "one_liner": "Agent that runs your marketing"})
            self.assertEqual(product["category"], "ai coding agent")
            self.assertEqual(product["pain"], "six seats")
            self.assertEqual(product["search_terms"], ["ai coding agent"])
            self.assertEqual(product["brief_model"], "m")
            # A category typed by the user wins and the model is not asked.
            write.reset_mock()
            typed = understand_product(store, extras={"name": "Navin", "one_liner": "Agent that runs your marketing", "category": "agent platform"})
            self.assertEqual(typed["category"], "agent platform")
            write.assert_not_called()
            # The typed category survives a later understand without a category.
            again = understand_product(store, extras={"name": "Navin", "one_liner": "Agent that runs your marketing"})
            self.assertEqual(again["category"], "agent platform")
            write.assert_not_called()

    def test_without_a_model_the_keyword_guess_stays(self) -> None:
        with TemporaryDirectory() as tmp, patch("navin.marketing.understand.ai.enabled", return_value=False):
            store = MarketingStore(Path(tmp))
            product = understand_product(store, extras={"name": "Navin", "one_liner": "Agent that runs your marketing"})
            self.assertEqual(product["category"], "marketing studio")
            self.assertEqual(product["brief_model"], "")


class ContentTests(unittest.TestCase):
    def test_fresh_batch_keeps_published_history(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            first = generate_content(store, channels=["linkedin", "x"])
            store.upsert_content({"id": first[0]["id"], "status": "published", "published_at": 1.0})
            again = generate_content(store, channels=["linkedin", "x"])
            ids = {row["id"] for row in store.load_content()}
            self.assertIn(first[0]["id"], ids)
            self.assertEqual(store.get_content(first[0]["id"])["status"], "published")
            # The published linkedin post got a new sibling; the untouched x draft was rewritten in place.
            self.assertNotEqual(again[0]["id"], first[0]["id"])
            self.assertEqual(again[1]["id"], first[1]["id"])
            fresh = generate_content(store, channels=["x"], fresh=True, angle=REFILL_ANGLES[0])
            self.assertNotEqual(fresh[0]["id"], first[1]["id"])
            self.assertEqual(fresh[0]["angle"], REFILL_ANGLES[0])
            self.assertNotIn(REFILL_ANGLES[0], fresh[0]["body"])

    def test_improve_from_winner_stays_deterministic_without_model(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            winner = _post(store, "linkedin", status="winner")
            variant = improve_from_winner(store, {"id": winner["id"], "channel": "linkedin", "title": "Chasing invoices"})
            self.assertEqual(variant["parent_id"], winner["id"])
            self.assertEqual(variant["source"], "template")
            self.assertTrue(variant["body"].startswith("Double down"))


class ApiTests(unittest.TestCase):
    def test_dry_run_without_ids_previews_queue_without_writing_or_posting(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_settings({"publish": {"blog": {"enabled": True, "output_dir": "blog"}}})
            row = _post(store, "blog")
            before = store.get_content(row["id"])
            wire = Wire()
            with (
                patch("navin.webui.marketing_desk_api._store", return_value=store),
                patch("navin.marketing.publish.http_request", wire),
                patch.dict(publish._POSTERS, {"blog": unittest.mock.Mock(side_effect=AssertionError("preview posted"))}),
            ):
                result = handle_marketing_action("publish", {"dry_run": True})["publish"]
            self.assertEqual(result["sent"], [])
            self.assertEqual(result["failed"], [])
            self.assertEqual(len(result["preview"]), 1)
            self.assertEqual(result["preview"][0]["id"], row["id"])
            self.assertIn(row["body"], result["preview"][0]["text"])
            self.assertEqual(store.get_content(row["id"]), before)
            self.assertEqual(wire.calls, [])

    def test_settings_secret_connection_publish_schedule_and_measure(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                snap = handle_marketing_action(
                    "settings",
                    {"publish": {"linkedin": {"enabled": True, "author": "urn:li:person:abc"}}, "analytics": {"provider": "plausible", "site_id": "s"}, "utm_campaign": "q3"},
                )
                self.assertTrue(snap["settings"]["publish"]["linkedin"]["enabled"])
                self.assertEqual(snap["settings"]["analytics"]["provider"], "plausible")
                self.assertEqual(snap["settings"]["utm_campaign"], "q3")
                connectors = {row["channel"]: row for row in snap["connectors"]}
                self.assertFalse(connectors["linkedin"]["configured"])
                snap = handle_marketing_action("secret", {"name": "linkedin_token", "value": "AQV-very-secret-token"})
                connectors = {row["channel"]: row for row in snap["connectors"]}
                self.assertTrue(connectors["linkedin"]["ready"])
                self.assertNotIn("AQV-very-secret-token", json.dumps(snap))
                with self.assertRaises(MarketingError):
                    handle_marketing_action("secret", {"name": "not_a_key", "value": "x"})
                row = _post(store, "linkedin", status="ready")
                snap = handle_marketing_action("approve-content", {"id": row["id"]})
                self.assertIn(row["id"], snap["queue"]["approved"])
                snap = handle_marketing_action("schedule-content", {"id": row["id"], "scheduled_at": "+1h"})
                self.assertIn(row["id"], snap["queue"]["waiting"])
                self.assertEqual(snap["kpis"]["scheduled"], 1)
                wire = Wire({"https://api.linkedin.com/rest/posts": (201, {"x-restli-id": "urn:li:share:9"}, {})})
                with patch("navin.marketing.publish.http_request", wire):
                    snap = handle_marketing_action("publish", {"ids": [row["id"]], "dry_run": True})
                    self.assertEqual(len(snap["publish"]["preview"]), 1)
                    self.assertEqual(wire.calls, [])
                    snap = handle_marketing_action("publish", {"ids": [row["id"]]})
                self.assertEqual(wire.calls[0]["headers"]["Authorization"], "Bearer AQV-very-secret-token")
                self.assertEqual(len(snap["publish"]["sent"]), 1)
                self.assertEqual(snap["kpis"]["published"], 1)
                self.assertEqual(snap["content"][0]["published_url"], "https://www.linkedin.com/feed/update/urn:li:share:9")
                with patch("navin.marketing.measure.http_request", Wire()):
                    snap = handle_marketing_action("measure", {})
                self.assertIn("plausible_key", snap["measure"]["error"])
                with patch("navin.marketing.publish.http_request", Wire({"https://api.linkedin.com/v2/userinfo": (401, {}, {"message": "expired"})})):
                    snap = handle_marketing_action("connection", {"channel": "linkedin"})
                self.assertFalse(snap["connection"]["ok"])
                self.assertIn("expired", snap["connection"]["error"])

    def test_heartbeat_turn_cannot_publish(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            with patch("navin.webui.marketing_desk_api._store", return_value=store), patch("navin.agent.tools.context.is_heartbeat_turn", return_value=True):
                with self.assertRaises(MarketingError):
                    handle_marketing_action("publish", {"ids": ["cnt-1"]})


class LoopTests(unittest.TestCase):
    def test_cycle_publishes_measures_learns_and_reports(self) -> None:
        from navin.marketing.loop import maybe_tick, start_loop

        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save_settings({"publish": {"linkedin": {"enabled": True, "author": "urn:li:person:abc"}, "per_cycle": 2}})
            store.save_secret("linkedin_token", "tok")
            approved = _post(store, "linkedin")
            ready = _post(store, "linkedin", status="ready")
            wire = Wire({"https://api.linkedin.com/rest/posts": (201, {"x-restli-id": "urn:li:share:1"}, {})})
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, run_now=False)
            with patch("navin.marketing.publish.http_request", wire):
                result = maybe_tick(store, force=True, watch_fn=lambda _d, **_k: {"count": 0, "events": []}, improve_fn=lambda _d, **_k: {"winners": [], "variants": []})
            self.assertTrue(result["did_work"])
            self.assertEqual([row["id"] for row in result["publish"]["sent"]], [approved["id"]])
            self.assertIn("1 published", result["reason"])
            self.assertEqual(result["create"]["reason"], "approval mode")
            self.assertEqual(store.get_content(ready["id"])["status"], "ready")
            self.assertEqual(len(wire.calls), 1)
            self.assertEqual(store.load_loop()["phase"], "idle")


if __name__ == "__main__":
    unittest.main()
