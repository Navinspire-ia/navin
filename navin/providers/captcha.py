"""CAPTCHA-solving provider registry (CapSolver, 2Captcha).

These providers return a solution *token* for a known challenge (reCAPTCHA v2/v3,
hCaptcha, Cloudflare Turnstile) given its sitekey and page URL. The token is then
injected into a real browser session to pass the challenge - the solver never
"breaks" anything, it forwards the challenge to a human/ML solving service the
user pays for with their own API key.

Design mirrors ``navin.providers.lip_sync``: an abstract provider, concrete REST
adapters, and a registry with config-driven construction. Everything is opt-in;
with no key configured the registry yields nothing and the scraper falls back to
its assisted "pause for the user" behaviour.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

# Normalised challenge kinds this module understands.
CHALLENGE_KINDS = (
    "recaptcha_v2",
    "recaptcha_v3",
    "hcaptcha",
    "turnstile",
)

_DEFAULT_TIMEOUT = 120.0
_POLL_INTERVAL = 3.0


class CaptchaSolverError(RuntimeError):
    """Raised when a challenge cannot be solved."""


@dataclass(frozen=True)
class CaptchaChallenge:
    """A challenge to solve. ``sitekey``+``url`` are the minimum for every kind."""

    kind: str
    sitekey: str
    url: str
    action: str | None = None  # reCAPTCHA v3 action
    min_score: float | None = None  # reCAPTCHA v3
    extra: dict[str, Any] = field(default_factory=dict)

    def normalized_kind(self) -> str:
        kind = (self.kind or "").strip().lower().replace("-", "_")
        if kind in ("recaptcha", "recaptchav2", "recaptcha_v2"):
            return "recaptcha_v2"
        if kind in ("recaptchav3", "recaptcha_v3"):
            return "recaptcha_v3"
        if kind in ("hcaptcha",):
            return "hcaptcha"
        if kind in ("turnstile", "cf_turnstile", "cloudflare_turnstile"):
            return "turnstile"
        raise CaptchaSolverError(f"unsupported challenge kind: {self.kind}")


@dataclass(frozen=True)
class CaptchaSolution:
    token: str
    provider: str
    kind: str
    raw: dict[str, Any]


class CaptchaSolver(ABC):
    name: str
    requires_credentials: bool = True

    @abstractmethod
    async def solve(
        self, challenge: CaptchaChallenge, *, timeout: float = _DEFAULT_TIMEOUT
    ) -> CaptchaSolution:
        """Return a solution token for *challenge* or raise CaptchaSolverError."""


def _redact(text: str, api_key: str) -> str:
    text = text[:800]
    return text.replace(api_key, "<redacted>") if api_key else text


class CapSolverProvider(CaptchaSolver):
    """CapSolver createTask / getTaskResult REST API."""

    name = "capsolver"
    _BASE = "https://api.capsolver.com"

    _TASK_TYPES = {
        "recaptcha_v2": "ReCaptchaV2TaskProxyLess",
        "recaptcha_v3": "ReCaptchaV3TaskProxyLess",
        "hcaptcha": "HCaptchaTaskProxyLess",
        "turnstile": "AntiTurnstileTaskProxyLess",
    }

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        poll_interval: float = _POLL_INTERVAL,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._poll_interval = poll_interval

    def _build_task(self, challenge: CaptchaChallenge, kind: str) -> dict[str, Any]:
        task: dict[str, Any] = {
            "type": self._TASK_TYPES[kind],
            "websiteURL": challenge.url,
            "websiteKey": challenge.sitekey,
        }
        if kind == "recaptcha_v3":
            task["pageAction"] = challenge.action or "verify"
            if challenge.min_score is not None:
                task["minScore"] = challenge.min_score
        if kind == "turnstile" and challenge.action:
            task["action"] = challenge.action
        task.update(challenge.extra)
        return task

    @staticmethod
    def _token(solution: dict[str, Any]) -> str | None:
        for key in ("gRecaptchaResponse", "token", "text"):
            value = solution.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    async def solve(
        self, challenge: CaptchaChallenge, *, timeout: float = _DEFAULT_TIMEOUT
    ) -> CaptchaSolution:
        if not self._api_key:
            raise CaptchaSolverError("capsolver requires an API key")
        kind = challenge.normalized_kind()
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            create = await client.post(
                f"{self._BASE}/createTask",
                json={"clientKey": self._api_key, "task": self._build_task(challenge, kind)},
            )
            data = create.json()
            if data.get("errorId"):
                raise CaptchaSolverError(
                    f"capsolver createTask failed: {data.get('errorDescription') or data}"
                )
            task_id = data.get("taskId")
            if not task_id:
                raise CaptchaSolverError("capsolver returned no taskId")
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                await asyncio.sleep(self._poll_interval)
                result = await client.post(
                    f"{self._BASE}/getTaskResult",
                    json={"clientKey": self._api_key, "taskId": task_id},
                )
                payload = result.json()
                if payload.get("errorId"):
                    raise CaptchaSolverError(
                        f"capsolver getTaskResult failed: {payload.get('errorDescription') or payload}"
                    )
                if payload.get("status") == "ready":
                    token = self._token(payload.get("solution") or {})
                    if not token:
                        raise CaptchaSolverError("capsolver returned no token")
                    return CaptchaSolution(
                        token=token, provider=self.name, kind=kind, raw=payload
                    )
                if asyncio.get_event_loop().time() >= deadline:
                    raise CaptchaSolverError("capsolver timed out waiting for a solution")
        except httpx.HTTPError as exc:
            raise CaptchaSolverError(f"capsolver transport error: {_redact(str(exc), self._api_key)}") from exc
        finally:
            if owns:
                await client.aclose()


class TwoCaptchaProvider(CaptchaSolver):
    """2Captcha in.php / res.php REST API."""

    name = "2captcha"
    _BASE = "https://2captcha.com"

    _METHODS = {
        "recaptcha_v2": "userrecaptcha",
        "recaptcha_v3": "userrecaptcha",
        "hcaptcha": "hcaptcha",
        "turnstile": "turnstile",
    }

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        poll_interval: float = _POLL_INTERVAL,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._poll_interval = poll_interval

    def _in_params(self, challenge: CaptchaChallenge, kind: str) -> dict[str, str]:
        params: dict[str, str] = {
            "key": self._api_key,
            "method": self._METHODS[kind],
            "pageurl": challenge.url,
            "json": "1",
        }
        if kind in ("recaptcha_v2", "recaptcha_v3"):
            params["googlekey"] = challenge.sitekey
            if kind == "recaptcha_v3":
                params["version"] = "v3"
                params["action"] = challenge.action or "verify"
                if challenge.min_score is not None:
                    params["min_score"] = str(challenge.min_score)
        elif kind == "hcaptcha":
            params["sitekey"] = challenge.sitekey
        elif kind == "turnstile":
            params["sitekey"] = challenge.sitekey
        for key, value in challenge.extra.items():
            params[str(key)] = str(value)
        return params

    async def solve(
        self, challenge: CaptchaChallenge, *, timeout: float = _DEFAULT_TIMEOUT
    ) -> CaptchaSolution:
        if not self._api_key:
            raise CaptchaSolverError("2captcha requires an API key")
        kind = challenge.normalized_kind()
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            submit = await client.get(
                f"{self._BASE}/in.php", params=self._in_params(challenge, kind)
            )
            created = submit.json()
            if str(created.get("status")) != "1":
                raise CaptchaSolverError(f"2captcha in.php failed: {created.get('request') or created}")
            captcha_id = created.get("request")
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                await asyncio.sleep(self._poll_interval)
                poll = await client.get(
                    f"{self._BASE}/res.php",
                    params={
                        "key": self._api_key,
                        "action": "get",
                        "id": captcha_id,
                        "json": "1",
                    },
                )
                payload = poll.json()
                status = str(payload.get("status"))
                request = payload.get("request")
                if status == "1" and isinstance(request, str) and request:
                    return CaptchaSolution(
                        token=request, provider=self.name, kind=kind, raw=payload
                    )
                if request not in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
                    raise CaptchaSolverError(f"2captcha res.php failed: {request or payload}")
                if asyncio.get_event_loop().time() >= deadline:
                    raise CaptchaSolverError("2captcha timed out waiting for a solution")
        except httpx.HTTPError as exc:
            raise CaptchaSolverError(f"2captcha transport error: {_redact(str(exc), self._api_key)}") from exc
        finally:
            if owns:
                await client.aclose()


_PROVIDERS: dict[str, type[CaptchaSolver]] = {
    "capsolver": CapSolverProvider,
    "2captcha": TwoCaptchaProvider,
    "twocaptcha": TwoCaptchaProvider,
}


def captcha_provider_names() -> tuple[str, ...]:
    return ("capsolver", "2captcha")


def _key_from_env(provider: str) -> str:
    return (
        os.environ.get(f"{provider.upper()}_API_KEY")
        or os.environ.get("CAPTCHA_API_KEY")
        or ""
    ).strip()


def create_captcha_solver(
    name: str,
    *,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
    poll_interval: float = _POLL_INTERVAL,
) -> CaptchaSolver:
    key = (name or "").strip().lower()
    provider = _PROVIDERS.get(key)
    if provider is None:
        raise CaptchaSolverError(f"unknown captcha provider: {name}")
    canonical = "2captcha" if provider is TwoCaptchaProvider else "capsolver"
    resolved_key = (api_key or "").strip() or _key_from_env(canonical)
    if not resolved_key:
        raise CaptchaSolverError(
            f"{canonical} needs an API key (set {canonical.upper()}_API_KEY or configure it)"
        )
    return provider(api_key=resolved_key, client=client, poll_interval=poll_interval)
