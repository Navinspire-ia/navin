# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A live share end to end: host bridge <-> relay <-> guest, encrypted."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from navin.share.crypto import ShareCipher
from navin.share.host import ShareManager
from navin.share.relay import Relay, process_request


class FakeChannel:
    def __init__(self) -> None:
        self._conn_identity: dict[Any, dict[str, Any]] = {}
        self._conn_default: dict[Any, str] = {}
        self.attached: list[tuple[Any, str]] = []
        self.dispatched: list[dict[str, Any]] = []
        self.cleaned: list[Any] = []

    def _attach(self, conn: Any, chat_id: str) -> None:
        self.attached.append((conn, chat_id))

    async def _presence_join(self, conn: Any, chat_id: str) -> None:
        pass

    async def _send_event(self, conn: Any, event: str, **fields: Any) -> None:
        await conn.send(json.dumps({"event": event, **fields}))

    async def _hydrate_after_subscribe(self, chat_id: str) -> None:
        pass

    async def _dispatch_envelope(self, conn: Any, client_id: str, envelope: dict[str, Any]) -> None:
        self.dispatched.append({"client_id": client_id, **envelope})

    async def _cleanup_connection(self, conn: Any) -> None:
        self.cleaned.append(conn)


class GuestClient:
    def __init__(self, ws: Any, cipher: ShareCipher) -> None:
        self.ws, self.cipher = ws, cipher

    async def send(self, payload: dict[str, Any]) -> None:
        await self.ws.send(json.dumps({"type": "data", "data": self.cipher.seal(payload)}))

    async def next(self, kind: str | None = None, timeout: float = 3) -> dict[str, Any]:
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if msg.get("type") != "data":
                continue
            payload = self.cipher.open(msg["data"])
            if kind is None or payload.get("type") == kind:
                return payload


def _run(scenario, tmp_path: Path):
    async def go():
        relay = Relay()
        async with serve(relay.handler, "127.0.0.1", 0, process_request=process_request) as server:
            port = server.sockets[0].getsockname()[1]
            import os

            os.environ["NAVIN_RELAY_URL"] = f"ws://127.0.0.1:{port}"
            channel = FakeChannel()
            manager = ShareManager(channel, store_path=tmp_path / "shares.json", sessions_workspace=tmp_path)
            try:
                await scenario(manager, channel, f"ws://127.0.0.1:{port}")
            finally:
                await manager.stop()
                os.environ.pop("NAVIN_RELAY_URL", None)
    asyncio.run(go())


async def _wait(predicate, timeout: float = 3) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def _join(manager, base, info, name="Sami"):
    share_id = info["share_id"]
    key = info["link"].split("#", 1)[1]
    ws = await connect(f"{base}/guest/{share_id}")
    assert json.loads(await ws.recv())["type"] == "welcome"
    guest = GuestClient(ws, ShareCipher(key, share_id))
    await guest.send({"type": "hello", "name": name})
    return guest


def test_a_guest_follows_live_reads_only_then_drives_once_allowed(tmp_path):
    async def scenario(manager, channel, base):
        info = manager.create("chat-1", ttl="1h")
        assert "#" in info["link"] and "/s/" in info["link"]
        await _wait(lambda: manager.describe(info["share_id"])["online"])
        guest = await _join(manager, base, info)
        snapshot = await guest.next("snapshot")
        assert snapshot["chat_id"] == "chat-1" and snapshot["rights"] == {"drive": False, "download": False}
        # The first paint waits for nothing but the last page of the chat; the
        # context (dates, project, git) arrives right behind on its own frame.
        assert snapshot["context"] is None
        assert (await guest.next("context", timeout=10))["context"] is not None
        await _wait(lambda: channel.attached)
        conn, chat = channel.attached[0]
        assert chat == "chat-1" and channel._conn_identity[conn]["role"] == "viewer"
        assert channel._conn_identity[conn]["display_name"] == "Sami"

        # Live events reach the guest, encrypted.
        await conn.send(json.dumps({"event": "delta", "chat_id": "chat-1", "text": "Je regarde"}))
        event = await guest.next("event")
        assert json.loads(event["frame"])["text"] == "Je regarde"

        # Read only: refused before reaching the channel.
        await guest.send({"type": "envelope", "envelope": {"type": "message", "content": "fais le commit"}})
        assert (await guest.next("refused"))["reason"] == "read_only"
        assert not channel.dispatched

        # The guest asks, the host grants.
        requests: list[Any] = []
        manager.listeners.append(lambda share_id, extra: requests.append(extra.get("request")))
        await guest.send({"type": "request_control"})
        await _wait(lambda: any(r and r["what"] == "drive" for r in requests))
        guest_id = manager.describe(info["share_id"])["guests"][0]["guest_id"]
        await manager.set_guest(info["share_id"], guest_id, drive=True)
        assert (await guest.next("rights"))["drive"] is True
        assert channel._conn_identity[conn]["role"] == "member"

        # Driving: messages go through, pinned to the shared chat.
        await guest.send({"type": "envelope", "envelope": {"type": "message", "chat_id": "other", "content": "lance les tests et commit"}})
        await _wait(lambda: channel.dispatched)
        assert channel.dispatched[0]["chat_id"] == "chat-1"
        assert channel.dispatched[0]["client_id"].startswith("guest:")

        # Approvals stay with the host; dangerous commands never pass.
        await guest.send({"type": "envelope", "envelope": {"type": "approval_decision", "request_id": "r1", "allowed": True}})
        assert (await guest.next("refused"))["reason"] == "not_allowed_for_guests"
        await guest.send({"type": "envelope", "envelope": {"type": "message", "content": "/permission full"}})
        assert (await guest.next("refused"))["reason"] == "command_not_allowed"
        assert len(channel.dispatched) == 1

        # Revoking disconnects the guest and frees its connection.
        await manager.revoke(info["share_id"])
        closed = json.loads(await asyncio.wait_for(guest.ws.recv(), 3))
        assert closed["type"] == "closed"
        assert channel.cleaned
    _run(scenario, tmp_path)


def test_readonly_guest_inspects_agents_only_in_shared_session(tmp_path):
    async def scenario(manager, channel, base):
        info = manager.create("chat-1", ttl="1h")
        await _wait(lambda: manager.describe(info["share_id"])["online"])
        guest = await _join(manager, base, info)
        await guest.next("snapshot")
        await guest.send({"type": "envelope", "envelope": {
            "type": "subagent_history", "request_id": "inspect", "chat_id": "other-chat",
            "task_id": "worker-1", "after": 20, "limit": 100, "workspace_scope": {"project_path": "/elsewhere"},
        }})
        await _wait(lambda: channel.dispatched)
        assert channel.dispatched[-1] == {
            "client_id": channel.dispatched[-1]["client_id"], "chat_id": "chat-1",
            "type": "subagent_history", "request_id": "inspect", "task_id": "worker-1", "after": 20, "limit": 100,
        }
        await guest.send({"type": "envelope", "envelope": {"type": "subagent_recover", "request_id": "resume", "task_id": "worker-1"}})
        assert (await guest.next("refused"))["reason"] == "read_only"
        assert len(channel.dispatched) == 1
    _run(scenario, tmp_path)


def test_large_agent_history_crosses_relay_without_truncation(tmp_path):
    async def scenario(manager, channel, base):
        info = manager.create("chat-1", ttl="1h")
        await _wait(lambda: manager.describe(info["share_id"])["online"])
        guest = await _join(manager, base, info)
        await guest.next("snapshot")
        conn, _ = channel.attached[0]
        output = "Full tool output: é\n" * 80000
        frame = json.dumps({"event": "subagent_query_result", "chat_id": "chat-1", "request_id": "history", "result": {"events": [{"kind": "tool_done", "payload": {"result": output}}]}})
        assert len(frame) > 1024 * 1024
        await conn.send(frame)
        first = await guest.next("event_part")
        parts = [first["frame_part"]]
        for index in range(1, first["total"]):
            part = await guest.next("event_part")
            assert part["id"] == first["id"] and part["index"] == index
            parts.append(part["frame_part"])
        assert "".join(parts) == frame
        assert json.loads("".join(parts))["result"]["events"][0]["payload"]["result"] == output
    _run(scenario, tmp_path)


def test_download_needs_the_host_and_brings_the_whole_session(tmp_path):
    async def scenario(manager, channel, base):
        from navin.session.manager import SessionManager

        sessions = SessionManager(tmp_path)
        chat = sessions.get_or_create("websocket:chat-2")
        chat.add_message("user", "bonjour")
        sessions.save(chat)
        info = manager.create("chat-2", ttl="1h")
        await _wait(lambda: manager.describe(info["share_id"])["online"])
        guest = await _join(manager, base, info)
        await guest.next("snapshot")
        requests: list[Any] = []
        manager.listeners.append(lambda share_id, extra: requests.append(extra.get("request")))
        await guest.send({"type": "request_download"})
        await _wait(lambda: any(r and r["what"] == "download" for r in requests))
        guest_id = manager.describe(info["share_id"])["guests"][0]["guest_id"]
        await manager.set_guest(info["share_id"], guest_id, download=True)
        await guest.next("rights")
        await guest.send({"type": "request_download"})
        await guest.next("bundle_start", timeout=10)
        chunks = []
        while True:
            chunk = await guest.next("bundle_chunk", timeout=10)
            chunks.append(base64.b64decode(chunk["data"]))
            if chunk["index"] + 1 == chunk["total"]:
                break
        bundle = tmp_path / "received.navin-session"
        bundle.write_bytes(b"".join(chunks))
        from navin.session.bundle import read_manifest

        assert read_manifest(bundle)["session_key"] == "websocket:chat-2"
    _run(scenario, tmp_path)


def test_a_cli_session_is_shared_under_its_own_key(tmp_path):
    """The CLI shares ``cli:<chat>``: the bundle and context come from that session."""
    async def go():
        from navin.session.bundle import read_manifest
        from navin.session.manager import SessionManager

        sessions = SessionManager(tmp_path)
        chat = sessions.get_or_create("cli:direct")
        chat.add_message("user", "depuis le terminal")
        sessions.save(chat)
        relay = Relay()
        async with serve(relay.handler, "127.0.0.1", 0, process_request=process_request) as server:
            import os

            port = server.sockets[0].getsockname()[1]
            os.environ["NAVIN_RELAY_URL"] = f"ws://127.0.0.1:{port}"
            manager = ShareManager(
                FakeChannel(), store_path=tmp_path / "shares.json", sessions_workspace=tmp_path,
                session_key_for=lambda chat_id: f"cli:{chat_id}",
            )
            try:
                info = manager.create("direct", ttl="1h", access="full")
                await _wait(lambda: manager.describe(info["share_id"])["online"])
                guest = await _join(manager, f"ws://127.0.0.1:{port}", info)
                snapshot = await guest.next("snapshot")
                assert snapshot["rights"] == {"drive": True, "download": True}
                # The snapshot no longer waits for git log/status: the context
                # follows on its own frame, built from the CLI session.
                context = await guest.next("context", timeout=10)
                assert context["context"] is not None
                await guest.send({"type": "request_download"})
                await guest.next("bundle_start", timeout=10)
                chunks = []
                while True:
                    chunk = await guest.next("bundle_chunk", timeout=10)
                    chunks.append(base64.b64decode(chunk["data"]))
                    if chunk["index"] + 1 == chunk["total"]:
                        break
                bundle = tmp_path / "cli.navin-session"
                bundle.write_bytes(b"".join(chunks))
                assert read_manifest(bundle)["session_key"] == "cli:direct"
            finally:
                await manager.stop()
                os.environ.pop("NAVIN_RELAY_URL", None)
    asyncio.run(go())


def test_shares_survive_a_restart_until_they_expire(tmp_path):
    async def scenario(manager, channel, base):
        info = manager.create("chat-3", ttl="1h")
        await manager.stop()
        again = ShareManager(FakeChannel(), store_path=tmp_path / "shares.json", sessions_workspace=tmp_path)
        again.start()
        try:
            assert [s["share_id"] for s in again.for_chat("chat-3")] == [info["share_id"]]
            assert again.describe(info["share_id"])["link"] == info["link"]
            mode = (tmp_path / "shares.json").stat().st_mode & 0o777
            assert mode == 0o600
        finally:
            await again.stop()
    _run(scenario, tmp_path)


def test_guests_waiting_on_the_relay_are_known_again_after_the_host_reconnects(tmp_path):
    async def scenario(manager, channel, base):
        info = manager.create("chat-1", ttl="1h")
        share_id = info["share_id"]
        await _wait(lambda: manager.describe(share_id)["online"])
        guest = await _join(manager, base, info)
        await guest.next("snapshot")
        # The host's Navin restarts: it forgets its guests, the relay keeps them.
        manager.guests.pop(share_id, None)
        await manager.sockets[share_id].close()
        while json.loads(await asyncio.wait_for(guest.ws.recv(), 3))["type"] != "host_online":
            pass
        # The guest page says hello again on host_online and gets a fresh snapshot.
        await guest.send({"type": "hello", "name": "Sami"})
        assert (await guest.next("snapshot"))["chat_id"] == "chat-1"
        assert [g["name"] for g in manager.describe(share_id)["guests"]] == ["Sami"]

    _run(scenario, tmp_path)


def test_a_guest_back_on_its_seat_gets_the_chat_again_and_the_live_events(tmp_path):
    """A phone asleep or changing network dials again and takes its seat back.
    The host ignored its hello (it already knew the guest), so the page's new
    socket never got a snapshot and dropped every event from then on: the
    guest no longer saw anything new."""
    pages = [[{"role": "user", "content": "bonjour"}]]

    def thread_for(chat_id: str, limit: int, before: str | None) -> dict[str, Any]:
        return {"messages": [dict(m) for m in pages[-1]], "page": {"has_more_before": False}}

    async def scenario(manager, channel, base):
        manager.thread_for = thread_for
        info = manager.create("chat-1", ttl="1h", access="write")
        share_id = info["share_id"]
        await _wait(lambda: manager.describe(share_id)["online"])
        key = info["link"].split("#", 1)[1]
        ws = await connect(f"{base}/guest/{share_id}")
        joined = json.loads(await ws.recv())
        assert joined["type"] == "welcome", joined
        seat = joined["guest"]
        first = GuestClient(ws, ShareCipher(key, share_id))
        await first.send({"type": "hello", "name": "Sami"})
        assert [m["content"] for m in (await first.next("snapshot"))["thread"]["messages"]] == ["bonjour"]
        await _wait(lambda: channel.attached)
        conn = channel.attached[0][0]
        guest_id = manager.describe(share_id)["guests"][0]["guest_id"]
        await manager.set_guest(share_id, guest_id, drive=True)
        await first.next("rights")

        # The connection drops; the agent goes on meanwhile.
        pages.append(pages[-1] + [{"role": "assistant", "content": "pendant la coupure"}])
        again = await connect(f"{base}/guest/{share_id}?seat={seat}")
        welcome = json.loads(await again.recv())
        assert welcome["type"] == "welcome", welcome
        assert welcome["guest"] == seat and welcome["resumed"] is True
        back = GuestClient(again, ShareCipher(key, share_id))
        await back.send({"type": "hello", "name": "Sami"})
        snapshot = await back.next("snapshot")
        assert [m["content"] for m in snapshot["thread"]["messages"]] == ["bonjour", "pendant la coupure"]
        # Same seat, same guest: name and rights kept, attached once.
        assert snapshot["rights"] == {"drive": True, "download": False}
        assert [g["name"] for g in manager.describe(share_id)["guests"]] == ["Sami"]
        assert len(channel.attached) == 1
        # And the live events reach the page's new socket.
        await conn.send(json.dumps({"event": "delta", "chat_id": "chat-1", "text": "en direct"}))
        assert json.loads((await back.next("event"))["frame"])["text"] == "en direct"

    _run(scenario, tmp_path)


def test_guests_get_the_threads_media_through_the_encrypted_pipe_only_for_signed_urls(tmp_path):
    async def scenario(manager, channel, base):
        asked: list[str] = []

        def fetch(url):
            asked.append(url)
            return (b"\x89PNG" + b"x" * 700_000, "image/png")

        manager.media_fetch = fetch
        info = manager.create("chat-1", ttl="1h")
        await _wait(lambda: manager.describe(info["share_id"])["online"])
        guest = await _join(manager, base, info)
        await guest.next("snapshot")

        await guest.send({"type": "media", "id": "m1", "url": "/api/media/c2ln/cGF0aA"})
        first = await guest.next("media_chunk")
        second = await guest.next("media_chunk")
        assert first["total"] == 2 and first["mime"] == "image/png" and second["index"] == 1
        assert base64.b64decode(first["data"]).startswith(b"\x89PNG")

        # Anything that is not a signed media URL never reaches the disk.
        await guest.send({"type": "media", "id": "m2", "url": "/etc/passwd"})
        assert (await guest.next("media_error"))["id"] == "m2"
        assert asked == ["/api/media/c2ln/cGF0aA"]

    _run(scenario, tmp_path)


def test_the_host_sets_the_links_access_up_front_and_can_change_it_for_everyone(tmp_path):
    async def scenario(manager, channel, base):
        info = manager.create("chat-1", ttl="1h", access="write")
        assert info["access"] == "write"
        await _wait(lambda: manager.describe(info["share_id"])["online"])
        guest = await _join(manager, base, info)
        snapshot = await guest.next("snapshot")
        assert snapshot["rights"] == {"drive": True, "download": False}
        await _wait(lambda: channel.attached)
        conn = channel.attached[0][0]
        assert channel._conn_identity[conn]["role"] == "member"

        # Full: everyone already in can now download too, without asking.
        assert await manager.set_access(info["share_id"], "full")
        assert (await guest.next("rights")) == {"type": "rights", "drive": True, "download": True}
        # Back to read only: the channel's ACL follows.
        assert await manager.set_access(info["share_id"], "read")
        assert (await guest.next("rights"))["drive"] is False
        assert channel._conn_identity[conn]["role"] == "viewer"
        assert not await manager.set_access(info["share_id"], "admin")

    _run(scenario, tmp_path)


def test_a_guest_only_ever_sees_its_shared_chat_and_cannot_smuggle_fields(tmp_path):
    async def scenario(manager, channel, base):
        info = manager.create("chat-1", ttl="1h", access="write")
        await _wait(lambda: manager.describe(info["share_id"])["online"])
        guest = await _join(manager, base, info)
        await guest.next("snapshot")
        await _wait(lambda: channel.attached)
        conn = channel.attached[0][0]

        # Global and other-chat events of the host never reach the guest.
        await conn.send(json.dumps({"event": "notification", "chat_id": "*", "title": "Bank code 1234"}))
        await conn.send(json.dumps({"event": "account_updated"}))
        await conn.send(json.dumps({"event": "delta", "chat_id": "private-chat", "text": "secret"}))
        await conn.send(json.dumps({"event": "delta", "chat_id": "chat-1", "text": "shared"}))
        event = await guest.next("event")
        assert json.loads(event["frame"])["text"] == "shared"

        # Only content (and turn id) survive: no file by path, no other folder, no tools.
        await guest.send({"type": "envelope", "envelope": {
            "type": "message", "content": "lis ce fichier", "turn_id": "t1",
            "file_mentions": [{"path": "/home/me/.ssh/id_rsa"}], "open_files": ["/etc/passwd"],
            "workspace_scope": {"project_path": "/", "access_mode": "full"},
            "media": ["/home/me/secret.png"], "cli_apps": [{"name": "aws"}], "model_preset": "most-expensive",
        }})
        await _wait(lambda: channel.dispatched)
        sent = channel.dispatched[0]
        assert {k for k in sent if k != "client_id"} == {"type", "content", "turn_id", "chat_id", "webui"}
        assert sent["chat_id"] == "chat-1"

        # A replayed frame (same ciphertext) is ignored.
        frame = guest.cipher.seal({"type": "envelope", "envelope": {"type": "message", "content": "encore"}})
        await guest.ws.send(json.dumps({"type": "data", "data": frame}))
        await guest.ws.send(json.dumps({"type": "data", "data": frame}))
        await asyncio.sleep(0.3)
        assert [d["content"] for d in channel.dispatched] == ["lis ce fichier", "encore"]

        # Oversized prompts are refused.
        await guest.send({"type": "envelope", "envelope": {"type": "message", "content": "x" * 20_001}})
        assert (await guest.next("refused"))["reason"] == "too_long"

    _run(scenario, tmp_path)


async def _next_whole(guest: GuestClient, kind: str, timeout: float = 10) -> dict[str, Any]:
    """The next ``kind`` payload, put back together when it came in parts."""
    parts: list[str] = []
    while True:
        payload = await guest.next(timeout=timeout)
        if payload.get("type") == "payload_part":
            assert payload["index"] == len(parts)
            parts.append(payload["part"])
            if len(parts) < payload["total"]:
                continue
            payload, parts = json.loads("".join(parts)), []
        if payload.get("type") == kind:
            return payload


def test_a_chat_with_a_huge_turn_opens_at_once_and_every_older_message_follows(tmp_path):
    """The user's shared chat: one agent turn of 10 MB. Its page was over the
    relay limit, the snapshot was never sent, and the guest stayed on
    "Loading the conversation", deaf to live events as well."""
    from navin.share.host import FIRST_PAGE_BYTES, MAX_RELAY_FRAME

    earlier = [
        {"role": "user", "content": "bonjour"},
        {"role": "assistant", "content": "salut"},
        {"role": "assistant", "content": "pret"},
    ]
    huge = [{"role": "user", "content": "lance l'audit"}]
    huge += [{"role": "tool", "kind": "trace", "content": f"tool {i} " + "é" * 3000} for i in range(298)]
    huge.append({"role": "assistant", "content": "Rapport complet " + "x" * 1_200_000})
    whole = earlier + huge
    asked: list[Any] = []

    def thread_for(chat_id: str, limit: int, before: str | None) -> dict[str, Any]:
        # Pages of whole turns, as the transcript store builds them.
        asked.append(before)
        if before is None:
            return {"messages": [dict(m) for m in huge], "has_pending_tool_calls": False, "page": {
                "before_cursor": "turn-1", "has_more_before": True, "user_message_offset": 1,
            }}
        assert before == "turn-1"
        return {"messages": [dict(m) for m in earlier], "has_pending_tool_calls": False, "page": {
            "before_cursor": None, "has_more_before": False, "user_message_offset": 0,
        }}

    async def scenario(manager, channel, base):
        manager.thread_for = thread_for
        frames: list[int] = []
        send_relay = manager._send_relay

        async def measured(share_id: str, payload: dict[str, Any]) -> None:
            frames.append(len(json.dumps(payload)))
            await send_relay(share_id, payload)

        manager._send_relay = measured
        info = manager.create("chat-1", ttl="1h")
        await _wait(lambda: manager.describe(info["share_id"])["online"])
        guest = await _join(manager, base, info)

        # The snapshot arrives, on the newest message: alone over one relay
        # frame, it crossed in parts instead of being dropped.
        snapshot = await _next_whole(guest, "snapshot")
        shown = snapshot["thread"]["messages"]
        assert shown == [huge[-1]]
        page = snapshot["thread"]["page"]
        assert page["has_more_before"] is True and page["user_message_offset"] == 2
        assert max(frames) <= MAX_RELAY_FRAME

        # Live events reach the guest, now that it has its snapshot.
        await _wait(lambda: channel.attached)
        await channel.attached[0][0].send(json.dumps({"event": "delta", "chat_id": "chat-1", "text": "suite"}))
        assert json.loads((await _next_whole(guest, "event"))["frame"])["text"] == "suite"

        # "Earlier messages" until the start: each one once, in order, with
        # the prompt offset the guest's prompt list counts on.
        cursor, pages = page["before_cursor"], 0
        while cursor:
            await guest.send({"type": "history", "before": cursor})
            history = await _next_whole(guest, "history")
            older = history["thread"]["messages"]
            assert older and _json_bytes(history["thread"]) <= 460 * 1024
            shown = older + shown
            page = history["thread"]["page"]
            assert page["user_message_offset"] == sum(1 for m in whole[: len(whole) - len(shown)] if m["role"] == "user")
            cursor = page["before_cursor"] if page["has_more_before"] else None
            pages += 1
        assert shown == whole
        assert 3 < pages <= 8 and max(frames) <= MAX_RELAY_FRAME
        # The huge turn was read once: its older messages waited on the host.
        assert asked == [None, "turn-1"]
        assert FIRST_PAGE_BYTES < MAX_RELAY_FRAME

    _run(scenario, tmp_path)


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def test_older_messages_kept_no_longer_fall_back_to_the_turns_before_them(tmp_path):
    """After a host restart the kept messages are gone: the guest goes on
    with the turns before them instead of a dead "Earlier messages"."""
    from navin.share.host import ShareManager

    def thread_for(chat_id: str, limit: int, before: str | None) -> dict[str, Any]:
        if before is None:
            messages = [{"role": "tool", "content": "y" * 20_000} for _ in range(20)]
            return {"messages": messages, "page": {"before_cursor": "turn-7", "has_more_before": True}}
        return {"messages": [{"role": "user", "content": f"before {before}"}], "page": {"has_more_before": False}}

    first = ShareManager(FakeChannel(), store_path=tmp_path / "a.json", sessions_workspace=tmp_path, thread_for=thread_for)
    page = first._transcript("chat-1", None, 40, 160 * 1024)
    assert len(page["messages"]) == 7 and page["page"]["before_cursor"].startswith("share~")
    kept = first._transcript("chat-1", page["page"]["before_cursor"])
    assert len(kept["messages"]) == 13 and kept["page"]["before_cursor"] == "turn-7"
    # Another chat cannot read them, and a restarted host no longer holds them.
    restarted = ShareManager(FakeChannel(), store_path=tmp_path / "b.json", sessions_workspace=tmp_path, thread_for=thread_for)
    for manager, chat in ((first, "chat-2"), (restarted, "chat-1")):
        older = manager._transcript(chat, page["page"]["before_cursor"])
        assert older["messages"] == [{"role": "user", "content": "before turn-7"}]
