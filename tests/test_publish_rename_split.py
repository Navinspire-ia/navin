# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""/publish + forge repo_create, the split-to-prs skill, and /rename-chat.

Every network call is mocked; nothing touches a real forge or model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from navin.bus.events import InboundMessage
from navin.command.router import CommandContext
from navin.webui import forge_api
from navin.webui.forge_api import FORGEJO, GITHUB, GITLAB, ForgeError

SKILLS = Path(__file__).resolve().parents[1] / "navin" / "skills"


class _Resp:
    def __init__(self, status: int, payload: Any = None):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self) -> Any:
        return self._payload


class _Recorder:
    def __init__(self, *responses: _Resp):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> _Resp:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


# -- forge_api.create_repo ---------------------------------------------------


def test_create_repo_github_user_private() -> None:
    rec = _Recorder(
        _Resp(
            201,
            {
                "full_name": "me/my-app",
                "html_url": "https://github.com/me/my-app",
                "clone_url": "https://github.com/me/my-app.git",
                "ssh_url": "git@github.com:me/my-app.git",
                "private": True,
                "default_branch": "main",
            },
        )
    )
    with patch.object(forge_api.httpx, "request", rec):
        out = forge_api.create_repo("github.com", "tok", "my-app", description="demo")
    call = rec.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://api.github.com/user/repos"
    assert call["json"] == {"name": "my-app", "private": True, "description": "demo", "auto_init": False}
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert call["follow_redirects"] is False
    assert out["web_url"] == "https://github.com/me/my-app"
    assert out["clone_url"].endswith("my-app.git") and out["ssh_url"].startswith("git@")
    assert out["private"] is True and out["forge"] == GITHUB


def test_create_repo_github_org_public() -> None:
    rec = _Recorder(_Resp(201, {"full_name": "acme/site", "html_url": "https://github.com/acme/site", "private": False}))
    with patch.object(forge_api.httpx, "request", rec):
        out = forge_api.create_repo("github.com", "tok", "site", private=False, org="acme")
    assert rec.calls[0]["url"] == "https://api.github.com/orgs/acme/repos"
    assert rec.calls[0]["json"]["private"] is False
    assert out["clone_url"] == "https://github.com/acme/site.git"


def test_create_repo_gitlab_with_namespace() -> None:
    rec = _Recorder(
        _Resp(200, {"id": 42, "full_path": "team"}),
        _Resp(
            201,
            {
                "path_with_namespace": "team/app",
                "web_url": "https://gitlab.com/team/app",
                "http_url_to_repo": "https://gitlab.com/team/app.git",
                "ssh_url_to_repo": "git@gitlab.com:team/app.git",
                "visibility": "private",
            },
        ),
    )
    with patch.object(forge_api.httpx, "request", rec):
        out = forge_api.create_repo("gitlab.com", "tok", "app", org="team")
    assert rec.calls[0]["url"] == "https://gitlab.com/api/v4/namespaces/team"
    post = rec.calls[1]
    assert post["url"] == "https://gitlab.com/api/v4/projects"
    assert post["json"]["namespace_id"] == 42 and post["json"]["visibility"] == "private"
    assert post["headers"]["PRIVATE-TOKEN"] == "tok"
    assert out["full_name"] == "team/app" and out["private"] is True and out["forge"] == GITLAB


def test_create_repo_forgejo_user() -> None:
    rec = _Recorder(
        _Resp(201, {"full_name": "me/x", "html_url": "https://codeberg.org/me/x", "clone_url": "https://codeberg.org/me/x.git", "private": True})
    )
    with patch.object(forge_api.httpx, "request", rec):
        out = forge_api.create_repo("codeberg.org", "tok", "x")
    assert rec.calls[0]["url"] == "https://codeberg.org/api/v1/user/repos"
    assert rec.calls[0]["headers"]["Authorization"] == "token tok"
    assert out["forge"] == FORGEJO and out["web_url"] == "https://codeberg.org/me/x"


@pytest.mark.parametrize(
    "host,status,payload",
    [
        ("github.com", 422, {"message": "Repository creation failed.", "errors": [{"message": "name already exists on this account"}]}),
        ("gitlab.com", 400, {"message": {"name": ["has already been taken"]}}),
        ("codeberg.org", 409, {"message": "The repository with the same name already exists."}),
    ],
)
def test_create_repo_name_taken(host: str, status: int, payload: Any) -> None:
    with patch.object(forge_api.httpx, "request", _Recorder(_Resp(status, payload))):
        with pytest.raises(ForgeError) as err:
            forge_api.create_repo(host, "tok", "taken")
    assert err.value.code == "forge_repo_exists"


def test_create_repo_rejects_bad_name_without_network() -> None:
    with patch.object(forge_api.httpx, "request", _Recorder()):
        with pytest.raises(ValueError):
            forge_api.create_repo("github.com", "tok", "bad name/../x")


# -- forge tool repo_create --------------------------------------------------


def _tool(root: Path):
    from navin.agent.tools.forge import ForgeTool

    tool = object.__new__(ForgeTool)
    tool._root_or_error = lambda: (root, "")
    return tool


def _approval(monkeypatch, allowed: bool) -> list[Any]:
    from navin.agent import approval

    asked: list[Any] = []

    async def fake(request):
        asked.append(request)
        return SimpleNamespace(allowed=allowed, reason="not allowed in an unattended run")

    monkeypatch.setattr(approval, "request_approval", fake)
    return asked


def _no_origin(monkeypatch, url: str = "") -> None:
    monkeypatch.setattr(forge_api, "_origin_url", lambda root: url)
    monkeypatch.setattr(forge_api, "resolve_token", lambda remote, config=None: ("tok", "settings"))


def test_repo_create_asks_approval_and_creates(tmp_path, monkeypatch) -> None:
    root = tmp_path / "My Cool App"
    root.mkdir()
    _no_origin(monkeypatch)
    asked = _approval(monkeypatch, True)
    with patch.object(forge_api, "create_repo", return_value={"web_url": "https://github.com/me/my-cool-app"}) as create:
        out = asyncio.run(_tool(root).execute(action="repo_create"))
    assert json.loads(out)["web_url"].endswith("my-cool-app")
    assert create.call_args.args[2] == "my-cool-app"
    assert create.call_args.kwargs["private"] is True
    assert asked and asked[0].allow_when_unattended is False and asked[0].scope == "forge:repo_create"
    assert not _tool(root).call_read_only({"action": "repo_create"})


def test_repo_create_refused_unattended(tmp_path, monkeypatch) -> None:
    _no_origin(monkeypatch)
    _approval(monkeypatch, False)
    with patch.object(forge_api, "create_repo") as create:
        out = asyncio.run(_tool(tmp_path).execute(action="repo_create", name="x"))
    assert "refused" in str(out) and not create.called


def test_repo_create_refuses_when_origin_exists(tmp_path, monkeypatch) -> None:
    _no_origin(monkeypatch, "https://user:secret@github.com/me/app.git")
    asked = _approval(monkeypatch, True)
    with patch.object(forge_api, "create_repo") as create:
        out = str(asyncio.run(_tool(tmp_path).execute(action="repo_create", name="x")))
    assert "already has an origin" in out and "secret" not in out
    assert not create.called and not asked


def test_repo_create_missing_token_points_to_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(forge_api, "_origin_url", lambda root: "")
    monkeypatch.setattr(forge_api, "resolve_token", lambda remote, config=None: ("", ""))
    from navin.agent import credentials

    async def no_card(host, reason):
        return None

    monkeypatch.setattr(credentials, "ensure_git_credentials", no_card)
    out = str(asyncio.run(_tool(tmp_path).execute(action="repo_create", name="x", host="gitlab.com")))
    assert "gitlab.com" in out and "Settings > Git & GitHub" in out


# -- /publish ----------------------------------------------------------------


def test_publish_parse() -> None:
    from navin.command.publish import parse_publish_args

    assert parse_publish_args("") == parse_publish_args("   ")
    plain = parse_publish_args("")
    assert plain.private and plain.host == "github.com" and not plain.name
    args = parse_publish_args("my-app --public --host https://gitlab.com/ --org team")
    assert (args.name, args.private, args.host, args.org) == ("my-app", False, "gitlab.com", "team")
    assert parse_publish_args("--host=codeberg.org").host == "codeberg.org"
    assert parse_publish_args("--host").error
    assert parse_publish_args("--force").error
    assert parse_publish_args("a b").error


def _ctx(raw: str, args: str, **kw: Any) -> CommandContext:
    msg = InboundMessage(channel=kw.pop("channel", "websocket"), sender_id="u", chat_id="c1", content=raw, metadata={})
    return CommandContext(msg=msg, session=kw.pop("session", None), key="websocket:c1", raw=raw, args=args, **kw)


def test_publish_command_starts_skill_turn() -> None:
    from navin.command.builtin import is_agent_turn_command
    from navin.command.publish import cmd_publish

    ctx = _ctx("/publish demo --host gitlab.com", "demo --host gitlab.com")
    assert asyncio.run(cmd_publish(ctx)) is None
    assert ctx.msg.metadata["preload_skills"] == ["new-repo"]
    assert ctx.msg.metadata["original_command"] == "/publish"
    assert "gitlab.com" in ctx.msg.content and "`demo`" in ctx.msg.content and "private" in ctx.msg.content
    assert is_agent_turn_command("/publish")

    bad = _ctx("/publish --nope", "--nope")
    reply = asyncio.run(cmd_publish(bad))
    assert reply is not None and "Usage: /publish" in reply.content


def test_commands_registered() -> None:
    from navin.command.builtin import register_builtin_commands
    from navin.command.router import CommandRouter

    router = CommandRouter()
    register_builtin_commands(router)
    assert "/publish" in router._exact and "/rename-chat" in router._exact


# -- skill routing + skill files --------------------------------------------


@pytest.mark.parametrize(
    "text,skill",
    [
        ("push this project to GitHub", "new-repo"),
        ("crée un repo pour ce projet", "new-repo"),
        ("mets ce projet sur gitlab", "new-repo"),
        ("/publish --public", "new-repo"),
        ("split this into PRs", "split-to-prs"),
        ("découpe en plusieurs PR", "split-to-prs"),
        ("break this branch up into smaller pull requests", "split-to-prs"),
    ],
)
def test_intent_routing(text: str, skill: str) -> None:
    from navin.agent.skill_routing import intent_skill_names

    assert skill in intent_skill_names(text)


@pytest.mark.parametrize("text", ["fix the login bug", "review PR 12", "what is a repo?", ""])
def test_intent_routing_negative(text: str) -> None:
    from navin.agent.skill_routing import intent_skill_names

    assert intent_skill_names(text) == ()


def test_loop_preloads_intent_skills() -> None:
    from navin.agent.loop import AgentLoop

    fake = SimpleNamespace(tools={})
    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="split this into PRs", metadata={})
    assert "split-to-prs" in (AgentLoop._preload_skills_for_message(fake, msg) or [])
    brief = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="split this into PRs", metadata={"preload_skills": []})
    assert "split-to-prs" not in (AgentLoop._preload_skills_for_message(fake, brief) or [])


@pytest.mark.parametrize("name", ["new-repo", "split-to-prs"])
def test_skill_frontmatter(name: str) -> None:
    from navin.agent.skills import SkillsLoader

    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n") and f"\nname: {name}\n" in text
    assert chr(0x2014) not in text and chr(0x2013) not in text
    loader = SkillsLoader(SKILLS.parent.parent)
    assert name in {entry["name"] for entry in loader.list_skills(filter_unavailable=False)}


def test_split_skill_rules() -> None:
    text = (SKILLS / "split-to-prs" / "SKILL.md").read_text(encoding="utf-8")
    for needle in ("git stash create", "refs/backup/pre-split-", "git apply --cached", "CODEOWNERS", "pr_create", "approves the plan"):
        assert needle in text
    assert "never `git add -A`" in text.replace("Never", "never")


# -- /rename-chat ------------------------------------------------------------


class _Sessions:
    def __init__(self, session):
        self.session = session
        self.titles: list[str] = []
        self.fail = False

    def get_or_create(self, key):
        return self.session

    def set_title(self, key, title):
        if self.fail:
            raise OSError("disk full")
        self.titles.append(title)
        self.session.metadata["title"] = title
        return title


class _Bus:
    def __init__(self):
        self.out: list[Any] = []

    async def publish_outbound(self, msg):
        self.out.append(msg)


class _Provider:
    def __init__(self, content: str = "", exc: Exception | None = None):
        self.content = content
        self.exc = exc
        self.prompts: list[Any] = []

    async def chat_with_retry(self, messages, **kwargs):
        self.prompts.append(messages)
        if self.exc:
            raise self.exc
        return SimpleNamespace(content=self.content, finish_reason="stop")


def _rename_ctx(provider, *, args: str = "", channel: str = "websocket", fail: bool = False):
    session = SimpleNamespace(
        metadata={},
        messages=[
            {"role": "user", "content": "help me deploy the api to kubernetes"},
            {"role": "assistant", "content": "Sure, here is a helm chart."},
        ],
    )
    sessions = _Sessions(session)
    sessions.fail = fail
    bus = _Bus()
    loop = SimpleNamespace(sessions=sessions, bus=bus, provider=provider, model="m")
    ctx = _ctx(f"/rename-chat {args}".strip(), args, loop=loop, session=session, channel=channel)
    return ctx, sessions, bus


def test_rename_chat_sets_title_and_emits_update(monkeypatch) -> None:
    from navin.command import rename_chat

    dropped: list[str] = []
    monkeypatch.setattr(rename_chat, "_drop_sidebar_override", dropped.append)
    provider = _Provider('"deploying the API to Kubernetes chat."')
    ctx, sessions, bus = _rename_ctx(provider, args="helm")
    reply = asyncio.run(rename_chat.cmd_rename_chat(ctx))
    assert sessions.titles == ["Deploying the API"]
    assert reply.content == "Renamed this chat to: Deploying the API"
    assert dropped == ["websocket:c1"]
    from navin.bus.outbound_events import SessionUpdatedEvent

    assert len(bus.out) == 1 and isinstance(bus.out[0].event, SessionUpdatedEvent)
    assert bus.out[0].event.scope == "title" and bus.out[0].chat_id == "c1"
    prompt = provider.prompts[0][1]["content"]
    assert "helm" in prompt and "kubernetes" in prompt


def test_rename_chat_cli_does_not_emit(monkeypatch) -> None:
    from navin.command import rename_chat

    monkeypatch.setattr(rename_chat, "_drop_sidebar_override", lambda key: None)
    ctx, sessions, bus = _rename_ctx(_Provider("Kubernetes API deployment plan"), channel="cli")
    asyncio.run(rename_chat.cmd_rename_chat(ctx))
    assert sessions.titles == ["Kubernetes API deployment"] and not bus.out


@pytest.mark.parametrize(
    "provider,fail",
    [(_Provider(exc=RuntimeError("down")), False), (_Provider("Chat"), False), (_Provider("Kubernetes deploy notes"), True)],
)
def test_rename_chat_failure_is_plain(monkeypatch, provider, fail) -> None:
    from navin.command import rename_chat

    monkeypatch.setattr(rename_chat, "_drop_sidebar_override", lambda key: None)
    ctx, sessions, bus = _rename_ctx(provider, fail=fail)
    reply = asyncio.run(rename_chat.cmd_rename_chat(ctx))
    assert reply.content.startswith("Could not rename this chat")
    assert "http" not in reply.content and not bus.out


def test_polish_title_limits() -> None:
    from navin.command.rename_chat import polish_title

    assert polish_title("one two three four five six seven") == "One two three"
    assert polish_title("Conversation about taxes in France") == "About taxes"
    assert polish_title("") == ""


def test_drop_sidebar_override(tmp_path, monkeypatch) -> None:
    from navin.command import rename_chat
    from navin.webui import sidebar_state

    monkeypatch.setattr(sidebar_state, "webui_sidebar_state_path", lambda: tmp_path / "s.json")
    sidebar_state.write_webui_sidebar_state({"title_overrides": {"websocket:c1": "Old", "websocket:c2": "Keep"}})
    rename_chat._drop_sidebar_override("websocket:c1")
    assert sidebar_state.read_webui_sidebar_state()["title_overrides"] == {"websocket:c2": "Keep"}


def test_every_title_source_is_capped_at_three_words() -> None:
    from navin.session.import_sessions import _title_from_text
    from navin.session.webui_turns import (
        limit_title_words,
        provisional_title_from_user_text,
    )
    from navin.tui.session_labels import session_display_title

    assert limit_title_words("a b c d e") == "a b c"
    assert limit_title_words("a b") == "a b"
    assert limit_title_words("") == ""
    assert provisional_title_from_user_text("/forge corrige le bug du sidebar a droite") == "corrige le bug"
    assert _title_from_text("please fix the login page redirect") == "please fix the"
    assert session_display_title({"title": "Une longue session sur les gels", "preview": ""}) == "Une longue session"
    assert session_display_title({"title": "", "preview": "un deux trois quatre cinq"}) == "un deux trois"
