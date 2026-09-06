"""CAPTCHA solver provider adapters (CapSolver, 2Captcha) over mocked HTTP."""

from __future__ import annotations

import httpx
import pytest

from navin.providers.captcha import (
    CaptchaChallenge,
    CaptchaSolverError,
    CapSolverProvider,
    TwoCaptchaProvider,
    captcha_provider_names,
    create_captcha_solver,
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_capsolver_creates_a_task_then_polls_for_the_token() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/createTask"):
            return httpx.Response(200, json={"errorId": 0, "taskId": "task-1"})
        # First result poll: processing, then ready.
        if calls.count("/getTaskResult") < 2:
            return httpx.Response(200, json={"errorId": 0, "status": "processing"})
        return httpx.Response(
            200,
            json={
                "errorId": 0,
                "status": "ready",
                "solution": {"gRecaptchaResponse": "TOKEN-123"},
            },
        )

    provider = CapSolverProvider(api_key="k", client=_client(handler), poll_interval=0.0)
    challenge = CaptchaChallenge(
        kind="recaptcha_v2", sitekey="site", url="https://shop.example/checkout"
    )
    solution = await provider.solve(challenge, timeout=30)
    assert solution.token == "TOKEN-123"
    assert solution.provider == "capsolver"
    assert solution.kind == "recaptcha_v2"
    assert "/createTask" in calls


@pytest.mark.asyncio
async def test_capsolver_surfaces_api_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errorId": 1, "errorDescription": "bad key"})

    provider = CapSolverProvider(api_key="k", client=_client(handler), poll_interval=0.0)
    with pytest.raises(CaptchaSolverError, match="bad key"):
        await provider.solve(
            CaptchaChallenge(kind="hcaptcha", sitekey="s", url="https://x.test"),
            timeout=5,
        )


@pytest.mark.asyncio
async def test_twocaptcha_submits_then_resolves() -> None:
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/in.php"):
            assert request.url.params.get("googlekey") == "site"
            return httpx.Response(200, json={"status": 1, "request": "cap-42"})
        polls["n"] += 1
        if polls["n"] < 2:
            return httpx.Response(200, json={"status": 0, "request": "CAPCHA_NOT_READY"})
        return httpx.Response(200, json={"status": 1, "request": "TWO-TOKEN"})

    provider = TwoCaptchaProvider(api_key="k", client=_client(handler), poll_interval=0.0)
    solution = await provider.solve(
        CaptchaChallenge(kind="recaptcha_v2", sitekey="site", url="https://x.test"),
        timeout=30,
    )
    assert solution.token == "TWO-TOKEN"
    assert solution.provider == "2captcha"


@pytest.mark.asyncio
async def test_unsupported_challenge_kind_is_rejected() -> None:
    provider = CapSolverProvider(api_key="k", client=_client(lambda r: httpx.Response(200, json={})), poll_interval=0.0)
    with pytest.raises(CaptchaSolverError, match="unsupported challenge kind"):
        await provider.solve(
            CaptchaChallenge(kind="funcaptcha", sitekey="s", url="https://x.test")
        )


def test_registry_lists_providers_and_requires_a_key() -> None:
    assert set(captcha_provider_names()) == {"capsolver", "2captcha"}
    with pytest.raises(CaptchaSolverError, match="unknown captcha provider"):
        create_captcha_solver("nope")
    with pytest.raises(CaptchaSolverError, match="needs an API key"):
        create_captcha_solver("capsolver", api_key="")


def test_registry_builds_with_explicit_key() -> None:
    solver = create_captcha_solver("2captcha", api_key="explicit")
    assert isinstance(solver, TwoCaptchaProvider)
