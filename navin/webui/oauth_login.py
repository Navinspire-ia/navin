"""Non-blocking OAuth sign-in for the WebUI.

``oauth_cli_kit`` runs the whole browser round-trip inline: it prints the
authorize URL, tries to open a browser *from the server process*, then blocks
up to 120 s on a localhost callback. Behind the WebUI that produced exactly the
failure users reported - a request hanging for two minutes, then
``RuntimeError: Authorization code not found`` - because the printed URL went
nowhere and ``webbrowser.open`` does nothing inside a packaged Linux app.

This module runs the same flow on a worker thread, publishes the authorize URL
as soon as the kit prints it so the client can open it, and lets the caller
poll for the outcome or paste the callback URL by hand.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# The kit wraps its output in Rich markup, so the URL is never alone on a line.
_URL_RE = re.compile(r"https?://[^\s\[\]()'\"<>]+")

STATUS_PENDING = "pending"
STATUS_SIGNED_IN = "signed_in"
STATUS_ERROR = "error"

#: How long the flow waits for a manually pasted callback URL once the kit has
#: given up on its own localhost listener.
MANUAL_CODE_TIMEOUT = 600.0


def extract_authorize_url(message: str) -> str | None:
    """First http(s) URL printed by the kit, stripped of Rich markup."""
    match = _URL_RE.search(message or "")
    if not match:
        return None
    url = match.group(0).rstrip(".,")
    return url if "://" in url else None


class LoginRunner(Protocol):
    def __call__(
        self,
        print_fn: Callable[[str], None],
        prompt_fn: Callable[[str], str],
    ) -> Any: ...


@dataclass
class _Session:
    provider: str
    status: str = STATUS_PENDING
    authorize_url: str | None = None
    error: str | None = None
    messages: list[str] = field(default_factory=list)
    code: str | None = None
    code_ready: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    url_ready: threading.Event = field(default_factory=threading.Event)
    prompted: bool = False
    thread: threading.Thread | None = None


class OAuthLoginManager:
    """One in-flight sign-in per provider."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}

    def start(
        self,
        provider: str,
        runner: LoginRunner,
        *,
        wait_seconds: float = 6.0,
    ) -> dict[str, Any]:
        """Kick off (or rejoin) a sign-in and return its state.

        Returns as soon as the authorize URL is known, so the request never
        holds the connection for the kit's two-minute callback window.
        """
        with self._lock:
            current = self._sessions.get(provider)
            if current is not None and not current.finished.is_set():
                session = current
                fresh = False
            else:
                session = _Session(provider=provider)
                self._sessions[provider] = session
                fresh = True

        if fresh:
            session.thread = threading.Thread(
                target=self._run,
                args=(session, runner),
                name=f"oauth-login-{provider}",
                daemon=True,
            )
            session.thread.start()

        # Whichever comes first: a URL to open, or a flow that already failed.
        session.url_ready.wait(timeout=wait_seconds)
        return self.snapshot(provider)

    def _run(self, session: _Session, runner: LoginRunner) -> None:
        def print_fn(message: str) -> None:
            text = str(message)
            session.messages.append(text)
            if session.authorize_url is None:
                url = extract_authorize_url(text)
                if url:
                    session.authorize_url = url
                    session.url_ready.set()

        def prompt_fn(_prompt: str) -> str:
            # The kit only asks once its own listener has timed out. Give the
            # user a real window to paste the callback URL instead of the empty
            # string that used to turn this into an instant failure.
            session.prompted = True
            session.code_ready.wait(timeout=MANUAL_CODE_TIMEOUT)
            return session.code or ""

        try:
            token = runner(print_fn=print_fn, prompt_fn=prompt_fn)
            access = getattr(token, "access", None)
            if access:
                session.status = STATUS_SIGNED_IN
            else:
                session.status = STATUS_ERROR
                session.error = "OAuth login failed"
        except Exception as exc:  # the kit raises plain RuntimeError
            session.status = STATUS_ERROR
            session.error = str(exc) or exc.__class__.__name__
        finally:
            session.finished.set()
            session.url_ready.set()

    def submit_code(self, provider: str, raw: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(provider)
        if session is None or session.finished.is_set():
            raise LookupError("no sign-in is waiting for a code")
        session.code = raw.strip()
        session.code_ready.set()
        session.finished.wait(timeout=30.0)
        return self.snapshot(provider)

    def cancel(self, provider: str) -> None:
        with self._lock:
            session = self._sessions.pop(provider, None)
        if session is None:
            return
        # Unblock the prompt so the worker thread can unwind on its own.
        session.code = ""
        session.code_ready.set()

    def snapshot(self, provider: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(provider)
        if session is None:
            return {
                "provider": provider,
                "status": STATUS_ERROR,
                "authorize_url": None,
                "awaiting_code": False,
                "error": "no sign-in in progress",
            }
        return {
            "provider": provider,
            "status": session.status,
            "authorize_url": session.authorize_url,
            "awaiting_code": session.prompted and not session.finished.is_set(),
            "error": session.error,
        }


oauth_logins = OAuthLoginManager()
