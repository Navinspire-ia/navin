# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Optional navin feature discovery and enablement."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

from loguru import logger
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from navin.channels._setup import (
    channel_field_value,
    channel_setup_spec,
    channel_value_present,
    stringify_channel_value,
)
from navin.channels.registry import DEFAULT_ENABLED_CHANNELS
from navin.config.loader import merge_missing_defaults
from navin.config.schema import Config
from navin.utils.proc import no_window_kwargs


class OptionalFeatureError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class InstallResult:
    ok: bool
    label: str
    pip_cmd: list[str]
    failed_cmd: list[str] | None = None
    output: str = ""


_INSTALL_TIMEOUT_SECONDS = 300
_LOG_OUTPUT_LIMIT = 4000
_HIDDEN_OPTIONAL_FEATURES = {"documents", "pdf"}
_BUNDLED_FEATURE_ALIASES = {"documents", "pdf"}
_BUNDLED_EXTRAS_FILE = "bundled_extras.txt"


def packaged_build() -> bool:
    """Whether this process is a PyInstaller build rather than a source install."""
    return bool(getattr(sys, "frozen", False))


def bundled_extras() -> frozenset[str]:
    """Extras compiled into this build, as recorded by the packaging script.

    A packaged build carries the modules but rarely their metadata: PyInstaller
    copies a ``dist-info`` only when a hook asks for it, so
    ``importlib.metadata`` reports slack_sdk, telegram or aiohttp as absent while
    ``import`` works perfectly. Asking the build what it installed is the only
    reliable answer; the alternative is mapping every distribution name to its
    module name and keeping that map correct forever.

    Always empty for a source install, where the metadata is authoritative and
    the file may well be lying around: the packaging scripts write it into the
    working tree, so a developer who builds an artifact would otherwise end up
    with a checkout claiming to ship whatever that build shipped.
    """
    raw = os.environ.get("NAVIN_BUNDLED_EXTRAS", "")
    if not raw:
        if not packaged_build():
            return frozenset()
        path = Path(__file__).with_name(_BUNDLED_EXTRAS_FILE)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return frozenset()
    return frozenset(
        canonicalize_name(line.strip())
        for line in raw.replace(",", "\n").splitlines()
        if line.strip() and not line.startswith("#")
    )


def load_pyproject(path: Path) -> dict[str, Any]:
    try:
        import tomllib

        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def optional_dependency_groups_from_metadata() -> dict[str, list[str] | None]:
    try:
        from importlib.metadata import metadata, requires
    except Exception:
        return {}

    try:
        extras = metadata("navin-ai").get_all("Provides-Extra") or []
        groups: dict[str, list[str] | None] = {name: [] for name in extras if name != "dev"}
        for raw in requires("navin-ai") or []:
            try:
                req = Requirement(raw)
            except Exception:
                continue
            if not req.marker:
                continue
            for extra, deps in groups.items():
                if deps is not None and req.marker.evaluate({"extra": extra}):
                    deps.append(raw)
        return groups
    except Exception:
        return {}


def optional_dependency_groups() -> dict[str, list[str] | None]:
    root = Path(__file__).resolve().parents[1]
    project = load_pyproject(root / "pyproject.toml").get("project", {})
    deps = project.get("optional-dependencies", {})
    if isinstance(deps, dict) and deps:
        return {
            name: list(values)
            for name, values in deps.items()
            if name != "dev" and name not in _HIDDEN_OPTIONAL_FEATURES and isinstance(values, list)
        }
    return {
        name: values
        for name, values in optional_dependency_groups_from_metadata().items()
        if name not in _HIDDEN_OPTIONAL_FEATURES
    }


def _install_requirements_for_extra(extra: str, deps: list[str]) -> list[str]:
    install_args: list[str] = []
    for raw in deps:
        try:
            req = Requirement(raw)
        except Exception:
            install_args.append(raw)
            continue
        if req.marker and not req.marker.evaluate({"extra": extra}):
            continue
        req.marker = None
        install_args.append(str(req))
    return install_args


def install_args_for_extra(
    extra: str,
    deps: list[str] | None,
) -> tuple[list[str], str]:
    if deps:
        install_args = _install_requirements_for_extra(extra, deps)
        if install_args:
            return install_args, f"{extra} support"
        return [], f"{extra} support"
    target = f"navin-ai[{extra}]"
    return [target], f'"{target}"'


def _requirement_installed(req: Requirement, extra: str, seen: set[tuple[str, str]]) -> bool:
    if req.marker and not req.marker.evaluate({"extra": extra}):
        return True
    key = (
        canonicalize_name(req.name),
        ",".join(sorted(canonicalize_name(value) for value in req.extras)),
    )
    if key in seen:
        return True
    seen.add(key)
    try:
        dist = distribution(req.name)
    except PackageNotFoundError:
        return False
    if req.specifier and not req.specifier.contains(dist.version, prereleases=True):
        return False

    for requested_extra in req.extras:
        if not _extra_dependencies_installed(dist, requested_extra, seen):
            return False
    return True


def _extra_dependencies_installed(
    dist: Any,
    requested_extra: str,
    seen: set[tuple[str, str]],
) -> bool:
    normalized = canonicalize_name(requested_extra)
    provided = {
        canonicalize_name(value)
        for value in (dist.metadata.get_all("Provides-Extra") or [])
    }
    if provided and normalized not in provided:
        return False

    matched = False
    for raw in dist.requires or []:
        try:
            req = Requirement(raw)
        except Exception:
            continue
        if req.marker and not req.marker.evaluate({"extra": requested_extra}):
            continue
        matched = True
        if not _requirement_installed(req, requested_extra, seen):
            return False
    return matched or bool(provided)


def requirement_installed(raw: str, extra: str = "") -> bool:
    return _requirement_installed(Requirement(raw), extra, set())


def extra_installed(extra: str, deps: list[str] | None) -> bool:
    if deps is None:
        return True
    if canonicalize_name(extra) in bundled_extras():
        return True
    return all(requirement_installed(dep, extra) for dep in deps)


def run_install_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_INSTALL_TIMEOUT_SECONDS,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        message = f"Timed out after {_INSTALL_TIMEOUT_SECONDS}s"
        stderr = "\n".join(part for part in ((stderr or "").rstrip(), message) if part)
        return subprocess.CompletedProcess(argv, 124, stdout=stdout or "", stderr=stderr)


def command_text(argv: list[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in argv])


def _log_completed_command(label: str, proc: subprocess.CompletedProcess[str]) -> None:
    logger.info("{} exited with code {}", label, proc.returncode)
    output = (proc.stderr or proc.stdout or "").strip()
    if output:
        logger.info("{} output:\n{}", label, output[:_LOG_OUTPUT_LIMIT])


def missing_pip(proc: subprocess.CompletedProcess[str]) -> bool:
    return "no module named pip" in f"{proc.stdout}\n{proc.stderr}".lower()


def packaged_install_refusal(extra: str) -> str:
    """Message for a feature a packaged build cannot add to itself.

    ``sys.executable`` is the Navin executable here, not an interpreter, so
    ``-m pip install`` reaches Navin's own argument parser and answers "No such
    option: -m". Saying what the situation is beats running that command.
    """
    return (
        f"'{extra}' is not part of this packaged build, and a packaged build has no "
        "Python interpreter to install into. Use a build that ships this feature, or "
        "install navin from source to add it yourself."
    )


def install_extra(
    extra: str,
    deps: list[str] | None,
    *,
    runner: Any = run_install_command,
) -> InstallResult:
    import importlib

    install_args, label = install_args_for_extra(extra, deps)
    if packaged_build():
        message = packaged_install_refusal(extra)
        logger.info("Refusing to install '{}' in a packaged build: {}", extra, message)
        return InstallResult(False, label, [], output=message)
    pip_cmd = [sys.executable, "-m", "pip", "install", *install_args]
    if not install_args:
        logger.info("Optional feature '{}' has no installable dependencies for this platform", extra)
        return InstallResult(True, label, pip_cmd)

    logger.info("Installing optional feature '{}': {}", extra, command_text(pip_cmd))
    proc = runner(pip_cmd)
    _log_completed_command(f"Optional feature '{extra}' install", proc)
    if proc.returncode == 0:
        importlib.invalidate_caches()
        return InstallResult(True, label, pip_cmd)

    failed_cmd = pip_cmd
    failed_proc = proc
    if missing_pip(proc):
        ensure_cmd = [sys.executable, "-m", "ensurepip", "--upgrade"]
        logger.info("pip missing while installing '{}'; running {}", extra, command_text(ensure_cmd))
        ensure_proc = runner(ensure_cmd)
        _log_completed_command(f"Optional feature '{extra}' ensurepip", ensure_proc)
        if ensure_proc.returncode == 0:
            logger.info("Retrying optional feature '{}': {}", extra, command_text(pip_cmd))
            proc = runner(pip_cmd)
            _log_completed_command(f"Optional feature '{extra}' install retry", proc)
            if proc.returncode == 0:
                importlib.invalidate_caches()
                return InstallResult(True, label, pip_cmd)
            failed_cmd = pip_cmd
            failed_proc = proc
        else:
            failed_cmd = ensure_cmd
            failed_proc = ensure_proc

    output = (failed_proc.stderr or failed_proc.stdout or "").strip()
    return InstallResult(False, label, pip_cmd, failed_cmd=failed_cmd, output=output)


def read_config_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_config_data(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def enable_channel_config(config_path: Path, channel_name: str, defaults: dict[str, Any]) -> None:
    data = read_config_data(config_path)
    channels = data.setdefault("channels", {})
    existing = channels.get(channel_name, {})
    if not isinstance(existing, dict):
        existing = {}
    merged = merge_missing_defaults(existing, defaults)
    merged["enabled"] = True
    channels[channel_name] = merged
    write_config_data(config_path, data)



def disable_channel_config(config_path: Path, channel_name: str) -> None:
    data = read_config_data(config_path)
    channels = data.setdefault("channels", {})
    existing = channels.get(channel_name, {})
    if not isinstance(existing, dict):
        existing = {}
    existing["enabled"] = False
    channels[channel_name] = existing
    write_config_data(config_path, data)



def channel_enabled(config: Config, name: str) -> bool:
    section = getattr(config.channels, name, None)
    default_enabled = name in DEFAULT_ENABLED_CHANNELS
    if section is None:
        return default_enabled
    if isinstance(section, dict):
        return bool(section.get("enabled", default_enabled))
    return bool(getattr(section, "enabled", default_enabled))


def _channel_config_snapshot(section: Any, name: str) -> tuple[dict[str, str], list[str]]:
    if hasattr(section, "model_dump"):
        section = section.model_dump(mode="json", by_alias=True)
    if not isinstance(section, dict):
        return {}, []

    spec = channel_setup_spec(name)
    if spec is None:
        return {}, []

    values: dict[str, str] = {}
    configured_fields: list[str] = []
    for field in spec.snapshot_fields:
        value = channel_field_value(section, field)
        if not channel_value_present(value):
            continue
        key = f"channels.{name}.{field}"
        configured_fields.append(key)
        if field in spec.secrets:
            continue
        values[key] = stringify_channel_value(value)
    return values, configured_fields


def _channel_has_required_setup(section: Any, name: str) -> bool:
    spec = channel_setup_spec(name)
    return bool(spec and spec.is_configured(section))


def _local_login_state_present(section: Any, name: str) -> bool:
    """Return whether a QR-login channel has reusable local account state."""
    from navin.config.paths import get_runtime_subdir

    if name == "whatsapp":
        configured_path = channel_field_value(section, "databasePath")
        database_path = (
            Path(str(configured_path)).expanduser()
            if configured_path
            else get_runtime_subdir("whatsapp-auth") / "neonize.db"
        )
        try:
            return database_path.is_file() and database_path.stat().st_size > 0
        except OSError:
            return False

    return False



def channel_configured(config: Config, name: str) -> bool:
    """Return whether a channel has enough saved setup to be enabled directly."""
    section = getattr(config.channels, name, None)
    if name == "whatsapp" and _local_login_state_present(section, name):
        return True
    if section is None:
        return False

    spec = channel_setup_spec(name)
    if not spec or not spec.required:
        return channel_enabled(config, name)
    return _channel_has_required_setup(section, name)


def optional_features_payload(
    *,
    config: Config | None = None,
    last_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from navin.channels.registry import discover_channel_names, discover_plugins
    from navin.config.loader import load_config

    config = config or load_config()
    extras = optional_dependency_groups()
    builtin_channels = set(discover_channel_names())
    plugin_channels = discover_plugins()
    features: list[dict[str, Any]] = []

    for name in sorted(builtin_channels | set(plugin_channels) | set(extras)):
        is_channel = name in builtin_channels or name in plugin_channels
        installed = extra_installed(name, extras[name]) if name in extras else True
        enabled = channel_enabled(config, name) if is_channel else installed
        configured = channel_configured(config, name) if is_channel else installed
        ready = bool(enabled and installed)
        status = "enabled" if ready else "missing_dependency" if not installed else "not_enabled"
        feature = {
            "name": name,
            "display_name": name.replace("_", " ").title(),
            "type": "channel" if is_channel else "feature",
            "enabled": enabled,
            "configured": configured,
            "installed": installed,
            "ready": ready,
            "status": status,
            # Offering an install a packaged build cannot perform only produces a
            # button that always fails.
            "install_supported": (name in extras or is_channel)
            and (installed or not packaged_build()),
            "requires_restart": _feature_requires_restart(name, is_channel=is_channel),
        }
        if is_channel:
            config_values, configured_fields = _channel_config_snapshot(
                getattr(config.channels, name, None),
                name,
            )
            if config_values:
                feature["config_values"] = config_values
            if configured_fields:
                feature["configured_fields"] = configured_fields
        features.append(feature)

    from navin.marketing.social_channels import social_features

    existing = {row["name"] for row in features}
    try:
        features.extend(row for row in social_features() if row["name"] not in existing)
    except Exception:
        logger.exception("Social channel catalog could not be loaded")

    payload = {
        "features": features,
        "enabled_count": sum(1 for feature in features if feature["enabled"]),
    }
    if last_action:
        payload["last_action"] = last_action
    return payload


def enable_optional_feature(
    name: str,
    *,
    config_path: Path | None = None,
    allow_install: bool = True,
    instance_id: str = "default",
    runner: Any = run_install_command,
) -> dict[str, Any]:
    from navin.channels.registry import (
        discover_channel_names,
        discover_plugins,
        load_channel_class,
    )
    from navin.config.loader import get_config_path

    from navin.marketing.social_channels import is_social_channel, set_social_channel_enabled

    if is_social_channel(name):
        set_social_channel_enabled(name, True)
        payload = optional_features_payload(
            last_action={"ok": True, "message": f"Enabled social channel '{name}'", "enabled": True}
        )
        payload["requires_restart"] = False
        return payload
    if name in _BUNDLED_FEATURE_ALIASES:
        payload = optional_features_payload(
            last_action={
                "ok": True,
                "message": f"Feature '{name}' is included with navin",
                "enabled": True,
            }
        )
        payload["requires_restart"] = False
        return payload
    config_path = config_path or get_config_path()
    extras = optional_dependency_groups()
    builtin_channels = set(discover_channel_names())
    plugin_channels = discover_plugins()
    known = builtin_channels | set(plugin_channels) | set(extras)
    if name not in known:
        available = ", ".join(sorted(known))
        raise OptionalFeatureError(f"Unknown feature: {name}. Available: {available}", status=404)

    if name in extras and not extra_installed(name, extras[name]):
        if not allow_install:
            raise OptionalFeatureError(
                "Installing optional features from a remote WebUI is disabled. "
                "Run this action from localhost or set tools.webuiAllowRemotePackageInstall to true.",
                status=403,
            )
        result = install_extra(
            name,
            extras[name],
            runner=runner,
        )
        if not result.ok:
            failed = command_text(result.failed_cmd or result.pip_cmd)
            if not failed:
                # Nothing was run: the build itself cannot take new packages.
                raise OptionalFeatureError(result.output or packaged_install_refusal(name), status=409)
            detail = f": {result.output}" if result.output else ""
            raise OptionalFeatureError(f"Failed: {failed}{detail}", status=500)

    if name in builtin_channels:
        try:
            channel_cls = load_channel_class(name)
        except Exception as exc:
            raise OptionalFeatureError(
                f"Channel '{name}' is not importable after enable: {exc}",
                status=500,
            ) from exc
        _ = instance_id
        enable_channel_config(config_path, name, channel_cls.default_config())
        message = f"Enabled channel '{name}'"
    elif name in plugin_channels:
        enable_channel_config(config_path, name, plugin_channels[name].default_config())
        message = f"Enabled channel '{name}'"
    else:
        message = f"Enabled feature '{name}'"

    payload = optional_features_payload(last_action={"ok": True, "message": message, "enabled": True})
    payload["requires_restart"] = _feature_requires_restart(
        name,
        is_channel=name in builtin_channels or name in plugin_channels,
    )
    return payload


def _feature_requires_restart(name: str, *, is_channel: bool) -> bool:
    """Return whether an installed feature needs the running engine rebuilt."""
    if is_channel:
        return True
    # These libraries are imported lazily or used by a newly spawned service.
    return name not in {"api", "browser", "documents", "pdf", "olostep"}


def disable_optional_feature(
    name: str,
    *,
    config_path: Path | None = None,
    instance_id: str = "default",
) -> dict[str, Any]:
    from navin.channels.registry import discover_channel_names, discover_plugins
    from navin.config.loader import get_config_path

    from navin.marketing.social_channels import is_social_channel, set_social_channel_enabled

    if is_social_channel(name):
        set_social_channel_enabled(name, False)
        payload = optional_features_payload(
            last_action={"ok": True, "message": f"Disabled social channel '{name}'", "enabled": False}
        )
        payload["requires_restart"] = False
        return payload
    config_path = config_path or get_config_path()
    extras = optional_dependency_groups()
    builtin_channels = set(discover_channel_names())
    plugin_channels = discover_plugins()
    known_channels = builtin_channels | set(plugin_channels)
    known = known_channels | set(extras)
    if name not in known:
        available = ", ".join(sorted(known))
        raise OptionalFeatureError(f"Unknown feature: {name}. Available: {available}", status=404)
    if name not in known_channels:
        raise OptionalFeatureError(f"Feature '{name}' cannot be disabled", status=400)
    _ = instance_id
    disable_channel_config(config_path, name)
    payload = optional_features_payload(
        last_action={"ok": True, "message": f"Disabled channel '{name}'", "enabled": False}
    )
    payload["requires_restart"] = True
    return payload
