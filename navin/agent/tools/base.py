"""Base class for agent tools."""
from __future__ import annotations

import difflib
import json
import typing
from abc import ABC, abstractmethod
from collections.abc import Callable
from copy import deepcopy
from typing import Any, TypeVar

if typing.TYPE_CHECKING:
    from pydantic import BaseModel

    from navin.agent.tools.context import ToolContext
    from navin.runtime_context import RuntimeContextProvider

_ToolT = TypeVar("_ToolT", bound="Tool")

# Matches :meth:`Tool._cast_value` / :meth:`Schema.validate_json_schema_value` behavior
_JSON_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class Schema(ABC):
    """Abstract base for JSON Schema fragments describing tool parameters.

    Concrete types live in :mod:`navin.agent.tools.schema`; all implement
    :meth:`to_json_schema` and :meth:`validate_value`. Class methods
    :meth:`validate_json_schema_value` and :meth:`fragment` are the shared validation and normalization entry points.
    """

    @staticmethod
    def resolve_json_schema_type(t: Any) -> str | None:
        """Resolve the non-null type name from JSON Schema ``type`` (e.g. ``['string','null']`` -> ``'string'``)."""
        if isinstance(t, list):
            return next((x for x in t if x != "null"), None)
        return t  # type: ignore[return-value]

    @staticmethod
    def subpath(path: str, key: str) -> str:
        return f"{path}.{key}" if path else key

    @staticmethod
    def validate_json_schema_value(val: Any, schema: dict[str, Any], path: str = "") -> list[str]:
        """Validate ``val`` against a JSON Schema fragment; returns error messages (empty means valid).

        Used by :class:`Tool` and each concrete Schema's :meth:`validate_value`.
        """
        raw_type = schema.get("type")
        nullable = (isinstance(raw_type, list) and "null" in raw_type) or schema.get("nullable", False)
        t = Schema.resolve_json_schema_type(raw_type)
        label = path or "parameter"

        if nullable and val is None:
            return []
        if t == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
            return [f"{label} should be integer"]
        if t == "number" and (
            not isinstance(val, _JSON_TYPE_MAP["number"]) or isinstance(val, bool)
        ):
            return [f"{label} should be number"]
        if t in _JSON_TYPE_MAP and t not in ("integer", "number") and not isinstance(val, _JSON_TYPE_MAP[t]):
            return [f"{label} should be {t}"]

        errors: list[str] = []
        if "enum" in schema and val not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if t in ("integer", "number"):
            if "minimum" in schema and val < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and val > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")
        if t == "string":
            if "minLength" in schema and len(val) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(val) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")
        if t == "object":
            props = schema.get("properties", {})
            for k in schema.get("required", []):
                if k not in val:
                    errors.append(f"missing required {Schema.subpath(path, k)}")
            additional = schema.get("additionalProperties", True)
            for k, v in val.items():
                if k in props:
                    errors.extend(Schema.validate_json_schema_value(v, props[k], Schema.subpath(path, k)))
                elif additional is False:
                    errors.append(f"unexpected parameter {Schema.subpath(path, k)}")
                elif isinstance(additional, dict):
                    errors.extend(
                        Schema.validate_json_schema_value(v, additional, Schema.subpath(path, k))
                    )
        if t == "array":
            if "minItems" in schema and len(val) < schema["minItems"]:
                errors.append(f"{label} must have at least {schema['minItems']} items")
            if "maxItems" in schema and len(val) > schema["maxItems"]:
                errors.append(f"{label} must be at most {schema['maxItems']} items")
            if "items" in schema:
                prefix = f"{path}[{{}}]" if path else "[{}]"
                for i, item in enumerate(val):
                    errors.extend(
                        Schema.validate_json_schema_value(item, schema["items"], prefix.format(i))
                    )
        return errors

    @staticmethod
    def fragment(value: Any) -> dict[str, Any]:
        """Normalize a Schema instance or an existing JSON Schema dict to a fragment dict."""
        # Try to_json_schema first: Schema instances must be distinguished from dicts that are already JSON Schema
        to_js = getattr(value, "to_json_schema", None)
        if callable(to_js):
            return to_js()
        if isinstance(value, dict):
            return value
        raise TypeError(f"Expected schema object or dict, got {type(value).__name__}")

    @abstractmethod
    def to_json_schema(self) -> dict[str, Any]:
        """Return a fragment dict compatible with :meth:`validate_json_schema_value`."""
        ...

    def validate_value(self, value: Any, path: str = "") -> list[str]:
        """Validate a single value; returns error messages (empty means pass). Subclasses may override for extra rules."""
        return Schema.validate_json_schema_value(value, self.to_json_schema(), path)


class ToolResult(str):
    """String-compatible tool output with structured status."""

    is_error: bool
    recovery_hint: str | None

    def __new__(cls, content: str, *, is_error: bool = False, recovery_hint: str | None = None) -> ToolResult:
        obj = str.__new__(cls, content)
        obj.is_error = is_error
        obj.recovery_hint = recovery_hint
        return obj

    @classmethod
    def error(cls, content: str, *, recovery_hint: str | None = None) -> ToolResult:
        return cls(content, is_error=True, recovery_hint=recovery_hint)


class Tool(ABC):
    """Agent capability: read files, run commands, etc."""

    _TYPE_MAP = _JSON_TYPE_MAP
    _BOOL_TRUE = frozenset(("true", "1", "yes"))
    _BOOL_FALSE = frozenset(("false", "0", "no"))

    @staticmethod
    def _resolve_type(t: Any) -> str | None:
        """Pick first non-null type from JSON Schema unions like ``['string','null']``."""
        return Schema.resolve_json_schema_type(t)

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the tool does."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""
        ...

    def unknown_action(self, action: Any, *, parameter: str = "action") -> ToolResult:
        """Reject an unrecognized action while naming the ones that work.

        The valid values are read back from this tool's own declared schema, so
        the message cannot drift from the enum. A bare "unknown action" costs the
        agent a turn: it has no way to tell a typo from a capability the tool
        simply does not have.
        """
        allowed: list[str] = []
        try:
            spec = self.parameters.get("properties", {}).get(parameter, {})
            allowed = [str(v) for v in spec.get("enum", []) if v is not None]
        except (AttributeError, TypeError):
            allowed = []
        message = f"Error: unknown {parameter}: {action}."
        if allowed:
            close = difflib.get_close_matches(str(action), allowed, n=1, cutoff=0.6)
            if close:
                message += f" Did you mean {parameter}={close[0]}?"
            message += f" Valid values: {', '.join(allowed)}."
        return ToolResult.error(message)

    @property
    def read_only(self) -> bool:
        """Whether this tool is side-effect free and safe to parallelize."""
        return False

    @property
    def concurrency_safe(self) -> bool:
        """Whether this tool can run alongside other concurrency-safe tools."""
        return self.read_only and not self.exclusive

    @property
    def exclusive(self) -> bool:
        """Whether this tool should run alone even if concurrency is enabled."""
        return False

    def call_concurrency_safe(self, arguments: Any) -> bool:
        """Whether this particular call can run alongside others.

        Defaults to the tool-wide answer. A tool whose safety depends on what it
        was asked to do - one action queries, another writes files - overrides
        this so its read paths still parallelize while its write paths do not.
        """
        return self.concurrency_safe

    def call_read_only(self, arguments: Any) -> bool:
        """Whether this particular call is side-effect free.

        Defaults to the tool-wide :attr:`read_only`. Tools that multiplex read
        and write actions behind one name (git, board) override this so Ask and
        Plan turns keep their query actions instead of losing the whole tool.
        """
        return self.read_only

    # --- Plugin metadata ---

    config_key: str = ""
    _plugin_discoverable: bool = True
    _scopes: set[str] = {"core"}

    @classmethod
    def config_cls(cls) -> type[BaseModel] | None:
        return None

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return True

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls()

    def runtime_context_provider(self) -> RuntimeContextProvider | None:
        """Return optional per-turn prompt context owned by this tool."""
        return None

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Run the tool; return content, or ``ToolResult.error(...)`` for failures."""
        ...

    @staticmethod
    def error(content: str) -> ToolResult:
        return ToolResult.error(content)

    def _cast_object(self, obj: Any, schema: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(obj, dict):
            return obj
        props = schema.get("properties", {})
        additional = schema.get("additionalProperties")
        casted: dict[str, Any] = {}
        for k, v in obj.items():
            if k in props:
                casted[k] = self._cast_value(v, props[k])
            elif isinstance(additional, dict):
                casted[k] = self._cast_value(v, additional)
            else:
                casted[k] = v
        return casted

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Apply safe schema-driven casts before validation."""
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            return params
        return self._cast_object(params, schema)

    def _cast_value(self, val: Any, schema: dict[str, Any]) -> Any:
        t = self._resolve_type(schema.get("type"))

        if t == "boolean" and isinstance(val, bool):
            return val
        if t == "integer" and isinstance(val, int) and not isinstance(val, bool):
            return self._clamp_numeric(val, schema)
        if t == "number" and isinstance(val, (int, float)) and not isinstance(val, bool):
            return self._clamp_numeric(val, schema)
        if t in self._TYPE_MAP and t not in ("boolean", "integer", "number", "array", "object"):
            expected = self._TYPE_MAP[t]
            if isinstance(val, expected):
                return val

        if isinstance(val, str) and t in ("integer", "number"):
            try:
                parsed: int | float = int(val) if t == "integer" else float(val)
            except ValueError:
                return val
            return self._clamp_numeric(parsed, schema)

        if t == "string":
            if val is None:
                return val
            # A structured value for a string param (e.g. a findings array
            # passed natively for findings_json) must become valid JSON text,
            # not a Python repr with single quotes.
            if isinstance(val, (dict, list)):
                try:
                    return json.dumps(val, ensure_ascii=False)
                except (TypeError, ValueError):
                    return str(val)
            return str(val)

        if t == "boolean" and isinstance(val, str):
            low = val.lower()
            if low in self._BOOL_TRUE:
                return True
            if low in self._BOOL_FALSE:
                return False
            return val

        if t == "array" and isinstance(val, list):
            items = schema.get("items")
            return [self._cast_value(x, items) for x in val] if items else val

        if t == "object" and isinstance(val, dict):
            return self._cast_object(val, schema)

        return val

    @staticmethod
    def _clamp_numeric(val: int | float, schema: dict[str, Any]) -> int | float:
        """Clamp out-of-range numbers to schema bounds instead of hard-failing.

        Models often pass generous timeouts (emulator boot, long builds). Rejecting
        the whole tool call for ``wait_timeout_ms must be <= 120000`` burns turns;
        clamping keeps the run moving with a safe ceiling.
        """
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and val < minimum:
            return type(val)(minimum) if not isinstance(val, bool) else minimum
        if isinstance(maximum, (int, float)) and val > maximum:
            return type(val)(maximum) if not isinstance(val, bool) else maximum
        return val

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """Validate against JSON schema; empty list means valid."""
        if not isinstance(params, dict):
            return [f"parameters must be an object, got {type(params).__name__}"]
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")
        return Schema.validate_json_schema_value(params, {**schema, "type": "object"}, "")

    def to_schema(self) -> dict[str, Any]:
        """OpenAI function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool_parameters(schema: dict[str, Any]) -> Callable[[type[_ToolT]], type[_ToolT]]:
    """Class decorator: attach JSON Schema and inject a concrete ``parameters`` property.

    Use on ``Tool`` subclasses instead of writing ``@property def parameters``. The
    schema is stored on the class and returned as a fresh copy on each access.

    Example::

        @tool_parameters({
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        })
        class ReadFileTool(Tool):
            ...
    """

    def decorator(cls: type[_ToolT]) -> type[_ToolT]:
        frozen = deepcopy(schema)

        @property
        def parameters(self: Any) -> dict[str, Any]:
            return deepcopy(frozen)

        cls.parameters = parameters  # type: ignore[assignment]

        abstract = getattr(cls, "__abstractmethods__", None)
        if abstract is not None and "parameters" in abstract:
            cls.__abstractmethods__ = frozenset(abstract - {"parameters"})  # type: ignore[misc]

        return cls

    return decorator
