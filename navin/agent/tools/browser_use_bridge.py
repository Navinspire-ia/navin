"""Bridge to the MIT ``browser-use`` package behind Navin's ``browser`` tool.

Lazy-imports only. When ``browser-use`` is not installed, callers get a clear
install hint and Playwright remains the default engine.
"""

from __future__ import annotations

from typing import Any

_INSTALL_HINT = (
    "Error: browser-use is not installed. Install it with: "
    "pip install 'navin-ai[browser]' && playwright install chromium "
    "(add --with-deps on Linux if system libraries are missing), then retry."
)


def browser_use_available() -> bool:
    try:
        import browser_use  # noqa: F401

        return True
    except ImportError:
        return False


def browser_use_version() -> str | None:
    try:
        from browser_use.utils import get_browser_use_version

        return str(get_browser_use_version())
    except Exception:
        try:
            import browser_use

            return getattr(browser_use, "__version__", None)
        except Exception:
            return None


async def create_browser_use_session(
    *,
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    executable_path: str | None = None,
) -> Any:
    """Start a ``browser_use.BrowserSession`` ready for Tools actions."""
    try:
        from browser_use import BrowserSession
        from browser_use.browser.profile import BrowserProfile
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc

    profile_kwargs: dict[str, Any] = {
        "headless": headless,
        "viewport": {"width": viewport_width, "height": viewport_height},
        "keep_alive": True,
    }
    if not executable_path:
        from navin.browser_runtime import ensure_chromium

        executable_path = await ensure_chromium()
    profile_kwargs["executable_path"] = executable_path
    profile = BrowserProfile(**profile_kwargs)
    session = BrowserSession(browser_profile=profile)
    await session.start()
    return session


def create_browser_use_tools() -> Any:
    try:
        from browser_use import Tools
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc
    return Tools()


def format_action_result(result: Any) -> str:
    """Render a browser-use ActionResult (or similar) for the agent."""
    if result is None:
        return "OK."
    error = getattr(result, "error", None)
    if error:
        return f"Error: {error}"
    parts: list[str] = []
    extracted = getattr(result, "extracted_content", None)
    if extracted:
        parts.append(str(extracted))
    memory = getattr(result, "long_term_memory", None)
    if memory and memory != extracted:
        parts.append(f"(memory) {memory}")
    if not parts:
        return str(result)
    return "\n".join(parts)


async def run_browser_use_action(
    session: Any,
    tools: Any,
    action_name: str,
    params: dict[str, Any] | None = None,
) -> str:
    """Execute one named browser-use registry action on an open session."""
    params = dict(params or {})
    # go_back is registered as "go_back" in browser-use; Navin uses "back".
    name = {"back": "go_back"}.get(action_name, action_name)
    if name not in tools.registry.registry.actions:
        available = ", ".join(sorted(tools.registry.registry.actions)[:50])
        raise ValueError(f"Unknown browser-use action {name!r}. Available (sample): {available}")
    result = await tools.registry.execute_action(
        action_name=name,
        params=params,
        browser_session=session,
    )
    return format_action_result(result)
