"""Composer modes: how free text is routed to the engine.

Mirrors the desktop composer (ask / plan / agent / review / security / debug)
plus a plain ``chat`` mode that sends text untouched.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ComposerMode:
    id: str
    label: str
    command: str  # slash command prefix, "" for plain chat
    description: str
    color: str  # Textual color name / variable used for the badge
    key: str  # shortcut shown in the picker


MODES: tuple[ComposerMode, ...] = (
    ComposerMode("chat", "Chat", "", "Plain conversation with tools available.", "$primary", "1"),
    ComposerMode("ask", "Ask", "/ask", "Read-only answers: no file changes.", "$secondary", "2"),
    ComposerMode("plan", "Plan", "/blueprint", "Design an implementation plan before coding.", "$accent", "3"),
    ComposerMode("agent", "Agent", "/forge", "Build and verify code end to end.", "$success", "4"),
    ComposerMode("review", "Review", "/inspect", "Review code quality and risks.", "$warning", "5"),
    ComposerMode("security", "Security", "/fortify", "Security audit and hardening.", "$error", "6"),
    ComposerMode("debug", "Debug", "/debug", "Reproduce, diagnose and fix a bug.", "$secondary-lighten-2", "7"),
)

MODE_BY_ID: dict[str, ComposerMode] = {m.id: m for m in MODES}

# Workflow slashes the composer injects (plus desktop aliases). Hidden in the
# transcript so Agent/Plan/… free text does not show the routing prefix.
_ROUTING_ALIASES: frozenset[str] = frozenset(
    {
        "/mobile",
        "/board",
        "/turbo",
        "/probe",
        "/unmask",
        "/lineage",
        "/xray",
        "/gatekeeper",
        "/perimeter",
        "/bastion",
        "/vault",
        "/recon",
        "/threatmap",
        "/dast",
        "/redteam",
        "/pentest",
        "/comply",
        "/montage",
    }
)
ROUTING_COMMANDS: frozenset[str] = frozenset(
    {m.command.lower() for m in MODES if m.command} | _ROUTING_ALIASES
)


def get_mode(mode_id: str | None) -> ComposerMode:
    return MODE_BY_ID.get((mode_id or "chat").lower(), MODE_BY_ID["chat"])


def next_mode(mode_id: str) -> ComposerMode:
    ids = [m.id for m in MODES]
    try:
        idx = ids.index(mode_id)
    except ValueError:
        idx = 0
    return MODES[(idx + 1) % len(MODES)]


def route_text(mode_id: str, text: str) -> str:
    """Apply the mode prefix to *text* unless it is already a slash command."""
    text = text.strip()
    if not text or text.startswith("/"):
        return text
    mode = get_mode(mode_id)
    if not mode.command:
        return text
    return f"{mode.command} {text}"


def inbound_for_submit(mode_id: str, text: str, *, turn_active: bool) -> tuple[str, bool]:
    """Engine payload and whether this is a follow-up on a running turn.

    A running turn must receive the raw prompt. Prefixing ``/forge`` again
    makes the engine drop the message (agent-turn handlers return None).
    """
    routed = route_text(mode_id, text)
    if not turn_active:
        return routed, False
    if routed.lower().split(None, 1)[0] in {"/stop"}:
        return routed, False
    head = routed.split(None, 1)[0].lower()
    if head in ROUTING_COMMANDS:
        return display_user_text(routed), True
    return routed, True


def display_user_text(text: str) -> str:
    """Transcript text: drop a leading mode-routing slash, keep the prompt.

    ``/forge salut`` becomes ``salut``. A bare ``/forge`` and ordinary
    commands such as ``/help`` stay unchanged.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return stripped
    head, _, rest = stripped.partition(" ")
    if head.lower() not in ROUTING_COMMANDS:
        return stripped
    remainder = rest.strip()
    return remainder if remainder else stripped
