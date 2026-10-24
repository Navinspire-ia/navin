# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""``/publish [name] [--public] [--host gitlab.com] [--org team]``.

Starts the new-repo skill-guided flow as a normal agent turn: inspect the
project first (an existing remote stops it), prepare a safe first commit,
create a private repository with ``forge action=repo_create`` (approval card),
add ``origin`` and push through the git tool's credential bridge.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from navin.bus.events import OutboundMessage
from navin.command.router import CommandContext

PUBLISH_USAGE = (
    "Usage: /publish [name] [--public] [--host gitlab.com] [--org team]\n\n"
    "Puts this project in a new repository (private by default) and pushes it.\n"
    "  /publish                       private repo on GitHub named after the folder\n"
    "  /publish my-app --public       public repo called my-app\n"
    "  /publish --host gitlab.com     on GitLab (or any Forgejo host)"
)


@dataclass(frozen=True)
class PublishArgs:
    name: str = ""
    private: bool = True
    host: str = "github.com"
    org: str = ""
    error: str = ""


def parse_publish_args(raw: str) -> PublishArgs:
    """Parse ``/publish`` arguments; ``error`` is set for anything unexpected."""
    try:
        tokens = shlex.split(raw or "")
    except ValueError as exc:
        return PublishArgs(error=f"cannot read the arguments ({exc})")
    name = ""
    private = True
    host = "github.com"
    org = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        flag, has_value, inline = token.partition("=")
        lowered = flag.lower()
        if lowered == "--public":
            private = False
        elif lowered == "--private":
            private = True
        elif lowered in ("--host", "--org"):
            if has_value:
                value = inline
            elif index + 1 < len(tokens):
                index += 1
                value = tokens[index]
            else:
                return PublishArgs(error=f"{lowered} needs a value")
            value = value.strip()
            if not value:
                return PublishArgs(error=f"{lowered} needs a value")
            if lowered == "--host":
                host = value.lower().removeprefix("https://").removeprefix("http://").strip("/")
            else:
                org = value.strip("/")
        elif token.startswith("-"):
            return PublishArgs(error=f"unknown option {token}")
        elif not name:
            name = token.strip()
        else:
            return PublishArgs(error=f"only one repository name, got {name!r} and {token!r}")
        index += 1
    return PublishArgs(name=name, private=private, host=host, org=org)


def publish_brief(args: PublishArgs) -> str:
    visibility = "private" if args.private else "public"
    name = (
        f"Use the name `{args.name}`."
        if args.name
        else "Name it after the project folder (lowercase, dashes)."
    )
    owner = f" under the organization/group `{args.org}`" if args.org else ""
    org_arg = f" org={args.org}" if args.org else ""
    return (
        f"[Publish] (/publish) Put this project in a new {visibility} repository on "
        f"{args.host}{owner} and push it. {name}\n\n"
        "Follow the new-repo skill step by step:\n"
        "1. Inspect first (git status, git remote). If a remote already exists, say which, "
        "offer to commit and push there, and stop: never a second repository, never change a remote.\n"
        "2. Prepare: list what would be committed, make .gitignore cover dependency dirs, "
        "build outputs and secrets, ask before including anything that looks like a secret; "
        "git init -b main and an initial commit only if needed.\n"
        f"3. forge action=repo_create host={args.host} private={str(args.private).lower()}{org_arg} "
        "(one close variant if the name is taken, then ask), git action=remote create=true "
        "name=origin remote=<clone_url>, git action=push set_upstream=true. If the host is not "
        "connected, request_credentials(kind=git_host) once.\n"
        "4. Report the repository page.\n"
        "Never delete a repository."
    )


async def cmd_publish(ctx: CommandContext) -> OutboundMessage | None:
    args = parse_publish_args(ctx.args)
    meta = dict(ctx.msg.metadata or {})
    if args.error:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"{args.error[:1].upper()}{args.error[1:]}.\n\n{PUBLISH_USAGE}",
            metadata={**meta, "render_as": "text"},
        )
    from navin.command.modules import PRELOAD_SKILLS_METADATA_KEY

    ctx.msg.metadata = {
        **meta,
        "original_command": "/publish",
        "original_content": ctx.raw,
        PRELOAD_SKILLS_METADATA_KEY: ["new-repo"],
    }
    ctx.msg.content = publish_brief(args)
    return None
