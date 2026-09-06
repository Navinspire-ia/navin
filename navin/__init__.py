"""
navin - A lightweight AI agent framework
"""

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


def _read_pyproject_version() -> str | None:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _read_stamped_version() -> str | None:
    """Version written into the tree by ``set-version.sh``.

    Frozen builds often lose ``navin-ai`` dist-info. Reading a file that ships
    inside the package is the only way the AppImage / DMG / EXE can report
    the version they were built as, instead of the hard-coded 1.0.0 fallback
    that made every launch offer an update that was already installed.
    """
    try:
        from navin._version import __version__ as stamped
    except Exception:
        return None
    value = str(stamped or "").strip()
    return value or None


def _resolve_version() -> str:
    stamped = _read_stamped_version()
    if stamped:
        return stamped
    try:
        return _pkg_version("navin-ai")
    except PackageNotFoundError:
        # Source checkouts often import navin without installed dist-info.
        return _read_pyproject_version() or "0.0.0"


__version__ = _resolve_version()
__logo__ = ""

_LAZY_EXPORTS = {
    "Navin": ".navin",
    "RunStream": ".navin",
    "RunResult": ".navin",
    "SessionInfo": ".navin",
    "SessionSnapshot": ".navin",
    "STREAM_EVENT_REASONING_COMPLETED": ".navin",
    "STREAM_EVENT_REASONING_DELTA": ".navin",
    "STREAM_EVENT_RUN_COMPLETED": ".navin",
    "STREAM_EVENT_RUN_FAILED": ".navin",
    "STREAM_EVENT_RUN_STARTED": ".navin",
    "STREAM_EVENT_TEXT_COMPLETED": ".navin",
    "STREAM_EVENT_TEXT_DELTA": ".navin",
    "STREAM_EVENT_TOOL_COMPLETED": ".navin",
    "STREAM_EVENT_TOOL_FAILED": ".navin",
    "STREAM_EVENT_TOOL_STARTED": ".navin",
    "STREAM_EVENT_TYPES": ".navin",
    "StreamEvent": ".navin",
    "StreamEventType": ".navin",
}


def __getattr__(name: str):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    mod = import_module(module_path, __name__)
    val = getattr(mod, name)
    globals()[name] = val
    return val


__all__ = [
    "Navin",
    "RunResult",
    "RunStream",
    "SessionInfo",
    "SessionSnapshot",
    "STREAM_EVENT_REASONING_COMPLETED",
    "STREAM_EVENT_REASONING_DELTA",
    "STREAM_EVENT_RUN_COMPLETED",
    "STREAM_EVENT_RUN_FAILED",
    "STREAM_EVENT_RUN_STARTED",
    "STREAM_EVENT_TEXT_COMPLETED",
    "STREAM_EVENT_TEXT_DELTA",
    "STREAM_EVENT_TOOL_COMPLETED",
    "STREAM_EVENT_TOOL_FAILED",
    "STREAM_EVENT_TOOL_STARTED",
    "STREAM_EVENT_TYPES",
    "StreamEvent",
    "StreamEventType",
]
