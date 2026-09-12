# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool registry for dynamic tool management."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import ContextAware, current_request_context

if TYPE_CHECKING:
    from navin.runtime_context import RuntimeContextProvider


def is_tool_error_result(name: str, result: Any) -> bool:
    return isinstance(result, ToolResult) and result.is_error


def tool_error_hint(result: ToolResult) -> str:
    """Setup blockers need user guidance instead of an alternate-method retry."""
    hint = result.recovery_hint
    if hint is None:
        hint = "Analyze the error above and try a different approach."
    suffix = f"\n\n[{hint}]" if hint else ""
    return "" if suffix and str(result).endswith(suffix) else suffix


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_runtime_context_providers(self) -> list[RuntimeContextProvider]:
        """Return tool-owned providers in stable tool-name order."""
        providers: list[RuntimeContextProvider] = []
        for name in sorted(self._tools):
            provider = self._tools[name].runtime_context_provider()
            if provider is not None:
                providers.append(provider)
        return providers

    @staticmethod
    def _lookup_key(name: str) -> str:
        """Normalize names for suggestions only; never for execution."""
        return "".join(ch.lower() for ch in name if ch.isalnum())

    def _suggest_name(self, name: str) -> str | None:
        key = self._lookup_key(str(name or ""))
        if not key:
            return None
        matches = [
            registered
            for registered in self._tools
            if self._lookup_key(registered) == key
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        """Extract a normalized tool name from either OpenAI or flat schemas."""
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions with stable ordering for cache-friendly prompts.

        Built-in tools are sorted first as a stable prefix, then MCP tools are
        sorted and appended.  The result is cached until the next
        register/unregister call.
        """
        if self._cached_definitions is not None:
            return self._cached_definitions

        definitions = [tool.to_schema() for tool in self._tools.values()]
        builtins: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []
        for schema in definitions:
            name = self._schema_name(schema)
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                builtins.append(schema)

        builtins.sort(key=self._schema_name)
        mcp_tools.sort(key=self._schema_name)
        self._cached_definitions = builtins + mcp_tools
        return self._cached_definitions

    def prepare_call(
        self,
        name: str,
        params: Any,
    ) -> tuple[Tool | None, Any, ToolResult | None]:
        """Resolve, cast, and validate one tool call."""
        tool = self._tools.get(name)
        if not tool:
            suggestion = self._suggest_name(str(name))
            hint = f" Did you mean '{suggestion}'? Tool names must match exactly." if suggestion else ""
            return None, params, (
                ToolResult.error(
                    f"Error: Tool '{name}' not found.{hint} Available: {', '.join(self.tool_names)}"
                )
            )

        # Compatibility for external tools that still implement the legacy
        # setter protocol. Built-ins read the authoritative ContextVar
        # directly and never copy routing state.
        if isinstance(tool, ContextAware) and (ctx := current_request_context()) is not None:
            tool.set_context(ctx)

        params = self._coerce_params(tool, params)
        if not isinstance(params, dict):
            schema = tool.parameters or {}
            required = schema.get("required")
            required_hint = (
                " Required: " + ", ".join(str(r) for r in required) + "."
                if isinstance(required, list) and required
                else ""
            )
            return tool, params, (
                ToolResult.error(
                    f"Error: Tool '{name}' parameters must be a JSON object, got "
                    f"{type(params).__name__}. Send arguments as one valid JSON "
                    "object with double-quoted keys and strings, matching the "
                    f"tool schema.{required_hint}",
                    recovery_hint=self._parameter_recovery_hint(tool),
                )
            )

        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, (
                ToolResult.error(
                    f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors),
                    recovery_hint=self._parameter_recovery_hint(tool),
                )
            )
        return tool, cast_params, None

    @staticmethod
    def _parameter_recovery_hint(tool: Tool) -> str:
        """Explain how to retry a rejected call without inventing its values."""
        hint = (
            f"No tool was executed. Reissue '{tool.name}' with one complete "
            "JSON object matching its schema."
        )
        schema = tool.parameters or {}
        properties = schema.get("properties", {})
        required = []
        for name in schema.get("required", []):
            field_type = properties.get(name, {}).get("type")
            required.append(f"{name} ({field_type})" if isinstance(field_type, str) else str(name))
        if required:
            hint += " Required fields: " + ", ".join(required) + "."
        return hint + " Supply the actual values; do not repeat the same invalid arguments."

    @classmethod
    def _coerce_argument_value(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if not stripped:
            return {}

        if not stripped.startswith(("{", "[")):
            return value

        try:
            parsed = json.loads(stripped)
        except Exception:
            return cls._repair_json_arguments(stripped, fallback=value)

        return parsed

    @classmethod
    def _repair_json_arguments(cls, stripped: str, *, fallback: Any) -> Any:
        """Best-effort recovery of near-JSON tool arguments.

        Models routinely emit large payloads (HTML, scripts, diffs) with
        literal newlines/tabs inside JSON strings, or a stray unescaped
        quote. Rejecting those turns every big write_file/exec into a
        retry loop, so repair what can be repaired without guessing:

        1. ``strict=False``: only tolerates raw control characters inside
           strings - content comes through byte-identical.
        2. ``json_repair``: quotes/commas/single-quote fixes; accepted only
           when the payload looks complete (ends with a closer), so a
           truncated ``content`` is never silently written to disk.
        """
        try:
            parsed = json.loads(stripped, strict=False)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        if stripped.endswith(("}", "]")):
            try:
                import json_repair

                parsed = json_repair.loads(stripped)
                if isinstance(parsed, dict) and parsed:
                    return parsed
            except Exception:
                pass

        return fallback

    @classmethod
    def _wrap_single_string_param(cls, tool: Tool, params: Any) -> Any:
        """Map a bare string onto the tool's only required string parameter.

        ``exec`` called with a raw command line (a common failure shape from
        smaller models) is unambiguous: there is exactly one required
        parameter and it is a string. Multi-field tools (write_file, ...)
        stay rejected - guessing which field the text belongs to is unsafe.
        """
        if not isinstance(params, str) or not params.strip():
            return params
        # A string that looks like a broken JSON object is a serialization
        # bug, not a bare value - executing it verbatim would be worse than
        # the error.
        if params.lstrip().startswith(("{", "[")):
            return params
        schema = tool.parameters or {}
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list):
            return params
        if len(required) != 1:
            return params
        name = required[0]
        prop = properties.get(name)
        if not isinstance(prop, dict) or prop.get("type") != "string":
            return params
        return {name: params}

    @classmethod
    def _coerce_params(cls, tool: Tool, params: Any) -> Any:
        params = cls._coerce_argument_value(params)
        params = cls._unwrap_arguments_payload(tool, params)
        return cls._wrap_single_string_param(tool, params)

    @classmethod
    def _unwrap_arguments_payload(cls, tool: Tool, params: Any) -> Any:
        if not isinstance(params, dict) or set(params) != {"arguments"}:
            return params
        properties = (tool.parameters or {}).get("properties", {})
        if isinstance(properties, dict) and "arguments" in properties:
            return params
        return cls._coerce_argument_value(params.get("arguments"))

    async def execute(self, name: str, params: Any) -> Any:
        """Execute a tool by name with given parameters."""
        hint = "\n\n[Analyze the error above and try a different approach.]"
        tool, params, error = self.prepare_call(name, params)
        if error:
            return ToolResult.error(
                str(error) + tool_error_hint(error), recovery_hint=error.recovery_hint,
            )

        try:
            assert tool is not None  # guarded by prepare_call()
            result = await tool.execute(**params)
            if is_tool_error_result(name, result):
                return ToolResult.error(str(result) + tool_error_hint(result), recovery_hint=result.recovery_hint)
            return result
        except Exception as e:
            return ToolResult.error(f"Error executing {name}: {str(e)}" + hint)

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
