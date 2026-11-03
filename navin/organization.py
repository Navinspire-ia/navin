# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Organization gateway: the organization's LiteLLM proxy as Navin's model source.

An organization (bank, administration, large company) runs its own LiteLLM
proxy in front of the providers it chose and gives its staff a file that
points Navin at it. Two places, first match wins:

1. the managed settings file deployed by IT (same lookup as
   ``navin.agent.managed_policy``), keys ``organization`` and ``gateway``;
2. the file the user imported (Settings > Organization, ``navin org import``),
   kept next to the user's ``config.json`` as ``organization.json``.

Format and behaviour: ``navin/skills/managed-settings/references/organization-gateway.md``.

The organization's settings are layered on the user's settings when they are
loaded and taken out again when they are saved (``navin.config.loader``):
``config.json`` only ever holds the user's own key and the user's own choices.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from navin.config.schema import Config

PROVIDER_NAME = "organization"
PRESET_PREFIX = "org:"
IMPORTED_FILE_NAME = "organization.json"
FORMAT_VERSION = 1
AUTH_MODES = ("user_key", "shared_key", "none")
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")
MAX_MODELS = 200

_ORG_ID = re.compile(r"^[a-z0-9-]{1,64}$")
_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}$")
# Same refusals as Navin Manager: a secret never travels in a header of the file.
_SECRET_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "api-key", "x-api-key"})


class OrganizationLockedError(PermissionError):
    """The organization file comes from IT: the user cannot replace or remove it."""


@dataclass(frozen=True)
class GatewayModel:
    id: str
    label: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    vision: bool = False
    reasoning_effort: str | None = None

    @property
    def preset_name(self) -> str:
        return PRESET_PREFIX + self.id


@dataclass(frozen=True)
class Gateway:
    base_url: str
    auth: str
    models: tuple[GatewayModel, ...]
    default_model: str
    api_key: str | None = field(default=None, repr=False)
    headers: dict[str, str] = field(default_factory=dict)
    ca_bundle: str | None = None
    routes: dict[str, str] = field(default_factory=dict)
    exclusive: bool = True

    @property
    def preset_names(self) -> frozenset[str]:
        return frozenset(model.preset_name for model in self.models)

    @property
    def default_preset(self) -> str:
        return PRESET_PREFIX + self.default_model


@dataclass(frozen=True)
class Organization:
    """The organization in force. ``source`` is ``managed`` (IT) or ``imported``."""

    source: str
    path: str
    id: str = ""
    name: str = ""
    support_contact: str = ""
    gateway: Gateway | None = None
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.gateway is not None and not self.error

    @property
    def locked(self) -> bool:
        """True when the user cannot replace or remove the file."""
        return self.source == "managed"

    @property
    def exclusive(self) -> bool:
        if self.gateway is not None and not self.error:
            return self.gateway.exclusive
        # An IT file that cannot be read may have restricted providers: fail
        # closed rather than send company data to a personal provider.
        return self.source == "managed"

    @property
    def display_name(self) -> str:
        return self.name or "Your organization"


# -- parsing ---------------------------------------------------------------------

def _text(value: Any, key: str, *, limit: int = 200, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"'{key}' is longer than {limit} characters")
    if any(ord(ch) < 32 for ch in text):
        raise ValueError(f"'{key}' contains control characters")
    return text


def _positive_int(value: Any, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"'{key}' must be a positive integer")
    return value


def _flag(value: Any, key: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"'{key}' must be true or false")
    return value


def _base_url(value: Any) -> str:
    url = _text(value, "gateway.baseUrl", limit=2048)
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("'gateway.baseUrl' must be an http or https address")
    if parts.username is not None or parts.password is not None:
        raise ValueError("'gateway.baseUrl' must not carry credentials: use gateway.auth")
    if parts.query or parts.fragment:
        raise ValueError("'gateway.baseUrl' must not carry a query or a fragment")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"'gateway.baseUrl' has an invalid port: {exc}") from None
    if port == 0:
        raise ValueError("'gateway.baseUrl' has an invalid port: 0")
    trimmed = url.rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if trimmed.endswith(suffix):
            raise ValueError("'gateway.baseUrl' is the proxy address, without /chat/completions")
    return trimmed


def _headers(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("'gateway.headers' must be an object of strings")
    out: dict[str, str] = {}
    for name, header in value.items():
        if not isinstance(name, str) or not _HEADER_NAME.match(name):
            raise ValueError(f"'gateway.headers' has an invalid header name: {name!r}")
        if name.lower() in _SECRET_HEADERS:
            raise ValueError(f"'gateway.headers' cannot set {name}: use gateway.auth and gateway.apiKey")
        if not isinstance(header, str) or any(ord(ch) < 32 for ch in header):
            raise ValueError(f"'gateway.headers.{name}' must be a single-line string")
        out[name] = header
    return out


def _ca_bundle(value: Any) -> str | None:
    if value is None:
        return None
    path = Path(_text(value, "gateway.caBundle", limit=4096)).expanduser()
    if not path.is_file():
        raise ValueError(f"'gateway.caBundle' file not found: {path}")
    return str(path)


def _models(value: Any) -> tuple[GatewayModel, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("'gateway.models' must list at least one model")
    if len(value) > MAX_MODELS:
        raise ValueError(f"'gateway.models' lists more than {MAX_MODELS} models")
    models: list[GatewayModel] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        where = f"gateway.models[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"'{where}' must be an object")
        model_id = _text(item.get("id"), f"{where}.id")
        if model_id in seen:
            raise ValueError(f"'{where}.id' {model_id!r} is listed twice")
        seen.add(model_id)
        effort = item.get("reasoningEffort")
        if effort is not None and effort not in REASONING_EFFORTS:
            raise ValueError(f"'{where}.reasoningEffort' must be one of {', '.join(REASONING_EFFORTS)}")
        models.append(GatewayModel(
            id=model_id,
            label=_text(item.get("label"), f"{where}.label", limit=120, required=False) or model_id,
            context_window=_positive_int(item.get("contextWindow"), f"{where}.contextWindow"),
            max_output_tokens=_positive_int(item.get("maxOutputTokens"), f"{where}.maxOutputTokens"),
            vision=_flag(item.get("vision"), f"{where}.vision", False),
            reasoning_effort=effort,
        ))
    return tuple(models)


def parse(data: Any, *, source: str, path: str) -> Organization:
    """Validate a decoded organization file; raises ValueError on a bad file."""
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    org = data.get("organization")
    if not isinstance(org, dict):
        raise ValueError("'organization' must be an object with an id and a name")
    org_id = _text(org.get("id"), "organization.id", limit=64)
    if not _ORG_ID.match(org_id):
        raise ValueError("'organization.id' takes lowercase letters, digits and '-' (1 to 64)")
    gateway = data.get("gateway")
    if not isinstance(gateway, dict):
        raise ValueError("'gateway' must be an object")
    kind = gateway.get("type", "litellm")
    if kind != "litellm":
        raise ValueError("'gateway.type' must be litellm")
    auth = gateway.get("auth", "user_key")
    if auth not in AUTH_MODES:
        raise ValueError(f"'gateway.auth' must be one of {', '.join(AUTH_MODES)}")
    api_key = gateway.get("apiKey")
    if auth == "shared_key":
        api_key = _text(api_key, "gateway.apiKey", limit=4096)
    elif api_key not in (None, ""):
        raise ValueError("'gateway.apiKey' is only allowed with auth shared_key")
    else:
        api_key = None
    models = _models(gateway.get("models"))
    ids = {model.id for model in models}
    default = _text(gateway.get("defaultModel"), "gateway.defaultModel")
    if default not in ids:
        raise ValueError(f"'gateway.defaultModel' {default!r} is not one of the listed models")
    routes_raw = gateway.get("routes") or {}
    if not isinstance(routes_raw, dict):
        raise ValueError("'gateway.routes' must map task roles to model ids")
    routes: dict[str, str] = {}
    for role, model_id in routes_raw.items():
        if not isinstance(role, str) or not _ROLE.match(role):
            raise ValueError(f"'gateway.routes' has an invalid role: {role!r}")
        if model_id not in ids:
            raise ValueError(f"'gateway.routes.{role}' {model_id!r} is not one of the listed models")
        routes[role] = model_id
    return Organization(
        source=source,
        path=path,
        id=org_id,
        name=_text(org.get("name"), "organization.name", limit=120),
        support_contact=_text(org.get("supportContact"), "organization.supportContact", required=False),
        gateway=Gateway(
            base_url=_base_url(gateway.get("baseUrl")),
            auth=auth,
            api_key=api_key,
            headers=_headers(gateway.get("headers")),
            ca_bundle=_ca_bundle(gateway.get("caBundle")),
            models=models,
            default_model=default,
            routes=routes,
            exclusive=_flag(gateway.get("exclusive"), "gateway.exclusive", True),
        ),
    )


# -- where the file is -----------------------------------------------------------

def imported_path(config_dir: Path) -> Path:
    return config_dir / IMPORTED_FILE_NAME


def _managed_file() -> Path | None:
    from navin.agent.managed_policy import find_file

    return find_file()


def _stat(path: Path | None) -> tuple[str, int, int] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        # Present but unreadable: a distinct signature, read() reports why.
        return (str(path), -1, hash(str(exc)))
    return (str(path), stat.st_mtime_ns, stat.st_size)


def signature(config_dir: Path | None) -> tuple[object, ...]:
    """Cheap fingerprint of the files that decide the organization (two stats)."""
    managed = _managed_file()
    imported = imported_path(config_dir) if config_dir is not None else None
    return (_stat(managed), _stat(imported))


def _read_managed(path: Path) -> Organization | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Organization(source="managed", path=str(path), error=f"unreadable: {exc}")
    if isinstance(data, dict) and "organization" not in data and "gateway" not in data:
        return None  # a policy-only managed file: no gateway
    try:
        return parse(data, source="managed", path=str(path))
    except ValueError as exc:
        return Organization(source="managed", path=str(path), error=str(exc))


def _read_imported(path: Path, config_dir: Path) -> Organization | None:
    from navin.config.secrets import decrypt_config_data

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        return Organization(source="imported", path=str(path), error=f"unreadable: {exc}")
    try:
        data = decrypt_config_data(json.loads(raw), config_dir / "config.json")
        return parse(data, source="imported", path=str(path))
    except ValueError as exc:
        return Organization(source="imported", path=str(path), error=str(exc))


_lock = threading.Lock()
_cache: dict[str, tuple[tuple[object, ...], Organization | None]] = {}


def load(config_dir: Path | None) -> Organization | None:
    """The organization in force for the settings in *config_dir*, cached by file stats.

    ``config_dir`` None (settings that were never loaded from a file) only
    looks at the managed file: an imported file belongs to one settings folder.
    """
    key = str(config_dir) if config_dir is not None else ""
    sig = signature(config_dir)
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    managed = _managed_file()
    org: Organization | None = _read_managed(managed) if managed is not None else None
    if org is None and config_dir is not None:
        org = _read_imported(imported_path(config_dir), config_dir)
    with _lock:
        _cache[key] = (sig, org)
    return org


def for_config(config: Config) -> Organization | None:
    """The organization the loader applied to *config* (managed file otherwise)."""
    applied = getattr(config, "_organization", None)
    if isinstance(applied, Organization):
        return applied
    loaded = getattr(config, "_loaded_path", None)
    return load(Path(loaded).parent if loaded else None)


def invalidate() -> None:
    with _lock:
        _cache.clear()


# -- import and removal ----------------------------------------------------------

def import_file(content: str, config_dir: Path) -> Organization:
    """Check and keep the file the user was given. Raises ValueError or OrganizationLockedError."""
    from navin.config.secrets import encrypt_config_data

    current = load(config_dir)
    if current is not None and current.locked:
        raise OrganizationLockedError(
            f"{current.display_name} settings are deployed by your IT team ({current.path}); "
            "they cannot be replaced from here."
        )
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise ValueError(f"not a JSON file: {exc}") from None
    target = imported_path(config_dir)
    org = parse(data, source="imported", path=str(target))
    # Only the gateway travels: the policy keys of an imported file are not
    # enforceable (the user owns the file), so they are not kept either.
    kept = {"version": FORMAT_VERSION, "organization": data["organization"], "gateway": data["gateway"]}
    encrypted = encrypt_config_data(kept, config_dir / "config.json")
    _write_private(target, json.dumps(encrypted, indent=2, ensure_ascii=False) + "\n")
    invalidate()
    return org


def remove_imported(config_dir: Path) -> bool:
    """Forget the imported file; False when there was none. Raises OrganizationLockedError."""
    current = load(config_dir)
    if current is not None and current.locked:
        raise OrganizationLockedError(
            f"{current.display_name} settings are deployed by your IT team ({current.path}); "
            "they cannot be removed from here."
        )
    target = imported_path(config_dir)
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    finally:
        invalidate()
    return True


def _write_private(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# -- layering on the user's settings ---------------------------------------------

def _section(data: dict[str, Any], *names: str, create: bool = False) -> dict[str, Any] | None:
    for name in names:
        value = data.get(name)
        if isinstance(value, dict):
            return value
    if create:
        data[names[0]] = {}
        return data[names[0]]
    return None


def _preset_payload(model: GatewayModel) -> dict[str, Any]:
    payload: dict[str, Any] = {"label": model.label, "model": model.id, "provider": PROVIDER_NAME}
    if model.context_window:
        payload["catalogContextWindowTokens"] = model.context_window
    if model.max_output_tokens:
        payload["catalogMaxOutputTokens"] = model.max_output_tokens
    if model.vision:
        payload["inputModalities"] = ["text", "image"]
    if model.reasoning_effort:
        payload["reasoningEffort"] = model.reasoning_effort
    return payload


# What a user may tune on an organization model (hide it, its thinking effort,
# its window...). Kept in config.json under the model's preset name; the rest
# of the preset always comes from the organization file.
TWEAK_FIELDS = ("enabled", "reasoning_effort", "temperature", "max_tokens", "context_window_tokens")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _take_tweaks(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    kept: dict[str, Any] = {}
    for name in TWEAK_FIELDS:
        for key in (_camel(name), name):
            if key in entry:
                kept[_camel(name)] = entry[key]
                break
    return kept


def prepare_config_data(data: dict[str, Any], org: Organization | None) -> None:
    """Before validation: the organization's models and gateway in, stale references out.

    Runs on the decoded ``config.json`` so a saved choice of an organization
    model (``org:<id>``) validates, and a choice the organization withdrew
    falls back instead of making the whole file unreadable.
    """
    usable = org is not None and org.usable and org.gateway is not None
    presets = _section(data, "modelPresets", "model_presets", create=usable)
    tweaks: dict[str, dict[str, Any]] = {}
    if presets is not None:
        for name in [name for name in presets if name.startswith(PRESET_PREFIX)]:
            tweaks[name] = _take_tweaks(presets.pop(name))
    names: frozenset[str] = frozenset()
    if usable and presets is not None and org is not None and org.gateway is not None:
        gateway = org.gateway
        names = gateway.preset_names
        for model in gateway.models:
            presets[model.preset_name] = {**_preset_payload(model), **tweaks.get(model.preset_name, {})}
        providers = data.get("providers")
        if not isinstance(providers, dict):
            providers = data["providers"] = {}
        own = providers.get(PROVIDER_NAME)
        own_key = (own.get("apiKey") or own.get("api_key")) if isinstance(own, dict) else None
        entry: dict[str, Any] = {"apiBase": gateway.base_url}
        if gateway.headers:
            entry["extraHeaders"] = dict(gateway.headers)
        if gateway.ca_bundle:
            entry["caBundle"] = gateway.ca_bundle
        if gateway.auth == "shared_key":
            entry["apiKey"] = gateway.api_key
        elif gateway.auth == "user_key" and own_key:
            entry["apiKey"] = own_key
        providers[PROVIDER_NAME] = entry

    def stale(value: Any) -> bool:
        return isinstance(value, str) and value.startswith(PRESET_PREFIX) and value not in names

    agents = data.get("agents")
    defaults = _section(agents, "defaults") if isinstance(agents, dict) else None
    if defaults is not None:
        for key in ("modelPreset", "model_preset"):
            if stale(defaults.get(key)):
                defaults.pop(key)
        for key in ("fallbackModels", "fallback_models"):
            chain = defaults.get(key)
            if isinstance(chain, list):
                defaults[key] = [item for item in chain if not stale(item)]
    routes = _section(data, "modelRoutes", "model_routes")
    if routes is not None:
        for role in [role for role, preset in routes.items() if stale(preset)]:
            del routes[role]


def _dump_fallbacks(chain: list[Any]) -> list[Any]:
    return [
        item if isinstance(item, str) else item.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        for item in chain
    ]


def _in_force(config: Config, entry: tuple[str, str, Any, Any]) -> bool:
    kind, role, _old, new = entry
    defaults = config.agents.defaults
    if kind == "preset":
        return defaults.model_preset == new
    if kind == "fallbacks":
        return list(defaults.fallback_models) == new
    return config.model_routes.get(role) == new


def apply_choices(config: Config, org: Organization | None) -> None:
    """After validation: the default model and task routes the organization sets.

    Every change is recorded on the config so ``strip_config_dump`` can put the
    user's own value back in ``config.json``. Running it again keeps the
    records of the changes still in force: the user's value stays known.
    """
    previous = [entry for entry in (config._organization_overrides or []) if _in_force(config, entry)]
    config._organization = org
    overrides: list[tuple[str, str, Any, Any]] = list(previous) if org is not None and org.usable else []
    if org is None or not org.usable or org.gateway is None:
        config._organization_overrides = overrides
        return
    gateway = org.gateway
    names = gateway.preset_names
    defaults = config.agents.defaults
    current = defaults.model_preset
    if (gateway.exclusive and current not in names) or (not gateway.exclusive and current in (None, "", "default")):
        overrides.append(("preset", "", current, gateway.default_preset))
        defaults.model_preset = gateway.default_preset
    if gateway.exclusive:
        kept = [item for item in defaults.fallback_models if isinstance(item, str) and item in names]
        if len(kept) != len(defaults.fallback_models):
            overrides.append(("fallbacks", "", _dump_fallbacks(list(defaults.fallback_models)), list(kept)))
            defaults.fallback_models = kept
        for role in [role for role, preset in config.model_routes.items() if preset not in names]:
            overrides.append(("route", role, config.model_routes[role], None))
            del config.model_routes[role]
    for role, model_id in gateway.routes.items():
        target = PRESET_PREFIX + model_id
        previous = config.model_routes.get(role)
        if previous == target or (not gateway.exclusive and previous is not None):
            continue
        overrides.append(("route", role, previous, target))
        config.model_routes[role] = target
    config._organization_overrides = overrides


def strip_config_dump(
    data: dict[str, Any],
    dumped: Config,
    org: Organization | None,
    *,
    overrides: list[tuple[str, str, Any, Any]] | None = None,
) -> None:
    """Before writing ``config.json``: drop what the organization provides.

    *data* is the camelCase dump of *dumped* about to be written. The
    organization's models leave it (only what the user tuned on them stays),
    so does the gateway address; the user's own key stays (encrypted with the
    other secrets), and every choice the organization made (*overrides*)
    goes back to the user's value when the user did not change it since.
    """
    from navin.config.schema import ModelPresetConfig

    presets = data.get("modelPresets")
    if isinstance(presets, dict):
        payloads: dict[str, dict[str, Any]] = {}
        if org is not None and org.usable and org.gateway is not None:
            payloads = {model.preset_name: _preset_payload(model) for model in org.gateway.models}
        for name in [name for name in presets if name.startswith(PRESET_PREFIX)]:
            del presets[name]
            preset = dumped.model_presets.get(name)
            if preset is None or name not in payloads:
                continue
            baseline = ModelPresetConfig.model_validate(payloads[name])
            tuned = {
                _camel(field_name): getattr(preset, field_name)
                for field_name in TWEAK_FIELDS
                if getattr(preset, field_name) != getattr(baseline, field_name)
            }
            if tuned:
                presets[name] = tuned
        if not presets:
            del data["modelPresets"]
    providers = data.get("providers")
    if isinstance(providers, dict) and isinstance(providers.get(PROVIDER_NAME), dict):
        key = providers[PROVIDER_NAME].get("apiKey")
        shared = org is not None and org.gateway is not None and org.gateway.auth == "shared_key"
        if key and not shared:
            providers[PROVIDER_NAME] = {"apiKey": key}
        else:
            del providers[PROVIDER_NAME]
        if not providers:
            del data["providers"]
    for kind, role, old, new in overrides or ():
        if kind == "route":
            routes = data.setdefault("modelRoutes", {})
            if routes.get(role) == new:
                if old is None:
                    routes.pop(role, None)
                else:
                    routes[role] = old
            if not routes:
                data.pop("modelRoutes", None)
            continue
        agents = data.setdefault("agents", {})
        defaults = agents.setdefault("defaults", {})
        key = "modelPreset" if kind == "preset" else "fallbackModels"
        current = defaults.get(key, [] if kind == "fallbacks" else None)
        if current == new:
            if old in (None, []):
                defaults.pop(key, None)
            else:
                defaults[key] = old
        if not defaults:
            agents.pop("defaults", None)
        if not agents:
            data.pop("agents", None)


# -- enforcement -----------------------------------------------------------------

def refusal(org: Organization | None, provider_name: str | None) -> str | None:
    """Why *provider_name* cannot be used under *org*, or None when it can."""
    if org is None:
        return None
    if org.error and org.locked:
        contact = f" Contact {org.support_contact}." if org.support_contact else ""
        return (
            f"The organization settings deployed by your IT team ({org.path}) are invalid: {org.error}. "
            f"Model calls are blocked until the file is fixed.{contact}"
        )
    if org.exclusive and provider_name != PROVIDER_NAME:
        return (
            f"{org.display_name} only allows its own model gateway. "
            "Pick one of its models (Settings > Organization)."
        )
    return None


def ensure_provider_allowed(config: Config, provider_name: str | None) -> None:
    reason = refusal(for_config(config), provider_name)
    if reason:
        raise ValueError(reason)


def ensure_gateway_key(config: Config, provider_name: str | None, api_key: str | None) -> None:
    """A gateway that takes personal keys, without the user's key: say where to enter it."""
    if provider_name != PROVIDER_NAME or api_key:
        return
    org = for_config(config)
    if org is not None and org.gateway is not None and org.gateway.auth == "user_key":
        raise ValueError(
            f"Enter the personal key {org.display_name} gave you in Settings > Organization "
            "(or run: navin org key)."
        )


# Tools that reach media providers of their own (not the organization gateway).
EXTERNAL_MEDIA_TOOLS = frozenset({"generate_image", "generate_video", "generate_music", "generate_speech"})


def tool_refusal(tool_name: str) -> str | None:
    """Refusal for a media tool when the organization allows its gateway only."""
    if tool_name not in EXTERNAL_MEDIA_TOOLS:
        return None
    from navin.config.loader import get_config_path

    org = load(get_config_path().parent)
    if org is None or not org.exclusive:
        return None
    return (
        f"{tool_name} uses a media provider outside {org.display_name}'s model gateway, "
        "which your organization does not allow."
    )
