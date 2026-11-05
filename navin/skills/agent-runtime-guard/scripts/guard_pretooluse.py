#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""PreToolUse hook: allow, deny or flag one agent tool call against a policy.

Usage (as a Navin hook, the event JSON arrives on stdin):
    guard_pretooluse.py --policy <policy.json> [--log <events.jsonl>]

Usage (dry run on a saved event, for tests):
    guard_pretooluse.py --policy <policy.json> --event <event.json> [--json]

The policy format is described in references/policy-schema.md and a complete
sample lives in assets/policy.example.json. The decision is written on stdout
as Navin hook JSON:

    {"hookSpecificOutput": {"permissionDecision": "deny",
                            "permissionDecisionReason": "..."}}

A call that passes prints nothing (exit 0). A call that only trips a "warn"
rule prints a systemMessage so the user sees it, and still runs. Every
evaluation is appended to --log (JSONL, one line per call, arguments trimmed
and secrets redacted) so audit_tool_log.py can review the session later.

Exit codes: 0 decision written (allow, warn or deny), 2 bad policy or event.
A PreToolUse hook blocks through the JSON decision, never through exit 2, so
that a broken policy file never silently blocks every tool.
"""

from __future__ import annotations

import argparse
import fnmatch
import ipaddress
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SECRET_RE = re.compile(
    r"(?i)("
    r"sk-[a-z0-9_-]{16,}|"
    r"ghp_[a-z0-9]{30,}|github_pat_[a-z0-9_]{40,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"xox[abprs]-[a-z0-9-]{10,}|"
    r"eyJ[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r")"
)
URL_RE = re.compile(r"https?://[^\s'\"<>]+")
HOST_FLAG_RE = re.compile(r"(?:-H|--host|--url|-u)\s+([^\s]+)")
MAX_ARG_CHARS = 400


class PolicyError(ValueError):
    """The policy file cannot be used."""


def load_json(path: Path, what: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyError(f"{what} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PolicyError(f"{what} {path}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{what} {path}: expected a JSON object")
    return data


def redact(text: str) -> str:
    return SECRET_RE.sub("[REDACTED]", text)


def trim(value: Any) -> Any:
    """Shorten and redact a tool argument before it is logged."""
    if isinstance(value, str):
        value = redact(value)
        return value if len(value) <= MAX_ARG_CHARS else value[:MAX_ARG_CHARS] + f"... [{len(value)} chars]"
    if isinstance(value, dict):
        return {k: trim(v) for k, v in value.items()}
    if isinstance(value, list):
        return [trim(v) for v in value[:20]]
    return value


# --------------------------------------------------------------------------- matching helpers


def tool_matches(tool: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(tool, pattern) for pattern in patterns)


def command_words(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def command_segments(command: str) -> list[list[str]]:
    """Split `a && b | c; d` into its simple commands."""
    segments: list[list[str]] = []
    current: list[str] = []
    for word in command_words(command):
        if word in {"&&", "||", "|", ";", "&"}:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(word)
    if current:
        segments.append(current)
    return segments


def binary_of(segment: list[str]) -> str:
    for word in segment:
        if "=" in word and not word.startswith("-") and segment.index(word) == 0:
            continue  # VAR=value prefix
        if word in {"sudo", "env", "nohup", "time", "nice", "command", "exec"}:
            continue
        return os.path.basename(word)
    return ""


def hosts_in(text: str) -> set[str]:
    hosts: set[str] = set()
    for url in URL_RE.findall(text):
        host = urlparse(url).hostname
        if host:
            hosts.add(host.lower())
    for raw in HOST_FLAG_RE.findall(text):
        candidate = raw.split("@")[-1].split(":")[0].strip("'\"")
        if "." in candidate and " " not in candidate:
            hosts.add(candidate.lower())
    return hosts


def private_address(host: str) -> bool:
    """True for a loopback or private IP literal; a domain name is not an address."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP literal, so a domain name: the caller matches it against the patterns.
        return False
    return address.is_private or address.is_loopback


def host_allowed(host: str, allowed: list[str]) -> bool:
    if host == "localhost" or private_address(host):
        return True
    for pattern in allowed:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if host.endswith(suffix) or host == pattern[2:]:
                return True
        elif fnmatch.fnmatchcase(host, pattern):
            return True
    return False


def paths_in(tool: str, args: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in ("path", "paths", "destination", "file", "files", "target", "working_dir", "workdir"):
        value = args.get(key)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(str(v) for v in value if isinstance(v, str))
    edits = args.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict) and isinstance(edit.get("path"), str):
                found.append(edit["path"])
    if tool == "exec" and isinstance(args.get("command"), str):
        for word in command_words(args["command"]):
            if word.startswith(("/", "~", "./", "../")) or word.startswith("$HOME"):
                found.append(word)
    return found


def path_matches(path: str, patterns: list[str], cwd: str) -> str | None:
    expanded = os.path.expanduser(os.path.expandvars(path))
    absolute = expanded if os.path.isabs(expanded) else os.path.normpath(os.path.join(cwd, expanded))
    home = os.path.expanduser("~")
    candidates = {path, expanded, absolute, absolute.replace(home, "~", 1) if absolute.startswith(home) else absolute}
    for pattern in patterns:
        full = os.path.expanduser(pattern)
        for candidate in candidates:
            if fnmatch.fnmatchcase(candidate, full) or fnmatch.fnmatchcase(candidate, pattern):
                return pattern
            # A pattern without a wildcard also covers everything under it.
            if not any(ch in full for ch in "*?[") and (candidate == full or candidate.startswith(full.rstrip("/") + "/")):
                return pattern
    return None


# --------------------------------------------------------------------------- evaluation


def evaluate(policy: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Return {"action": allow|warn|deny, "rule": ..., "reason": ...}."""
    tool = str(event.get("tool_name") or "")
    args = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    cwd = str(event.get("cwd") or os.getcwd())
    text = json.dumps(args, ensure_ascii=False)

    tools = policy.get("tools", {})
    denied_tools = tools.get("deny", [])
    allowed_tools = tools.get("allow", [])
    if tool_matches(tool, denied_tools):
        return {"action": "deny", "rule": "tools.deny", "reason": f"tool {tool} is denied by policy"}
    if allowed_tools and not tool_matches(tool, allowed_tools):
        return {"action": "deny", "rule": "tools.allow", "reason": f"tool {tool} is not in the allow list"}

    mcp = policy.get("mcp", {})
    if tool.startswith("mcp_"):
        server = tool[4:].split("_", 1)[0]
        if server in mcp.get("deny_servers", []):
            return {"action": "deny", "rule": "mcp.deny_servers", "reason": f"MCP server {server} is denied"}
        allowed_servers = mcp.get("allow_servers", [])
        if allowed_servers and server not in allowed_servers:
            return {"action": "deny", "rule": "mcp.allow_servers", "reason": f"MCP server {server} is not approved"}
        if tool_matches(tool, mcp.get("deny_tools", [])):
            return {"action": "deny", "rule": "mcp.deny_tools", "reason": f"MCP tool {tool} is denied"}
        if mcp.get("max_arg_chars") and len(text) > int(mcp["max_arg_chars"]):
            return {"action": "deny", "rule": "mcp.max_arg_chars",
                    "reason": f"MCP call carries {len(text)} characters of arguments (limit {mcp['max_arg_chars']}): possible exfiltration"}

    commands = policy.get("commands", {})
    if tool == "exec" and isinstance(args.get("command"), str):
        command = args["command"]
        if commands.get("deny_remote_scripts", True) and re.search(r"(curl|wget)[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", command):
            return {"action": "deny", "rule": "commands.deny_remote_scripts", "reason": "piping a download into a shell is not allowed"}
        for pattern in commands.get("deny_patterns", []):
            if re.search(pattern, command):
                return {"action": "deny", "rule": "commands.deny_patterns", "reason": f"command matches denied pattern /{pattern}/"}
        allowed_bins = commands.get("allow_binaries", [])
        denied_bins = commands.get("deny_binaries", [])
        for segment in command_segments(command):
            binary = binary_of(segment)
            if not binary:
                continue
            if binary in denied_bins:
                return {"action": "deny", "rule": "commands.deny_binaries", "reason": f"binary {binary} is denied"}
            if allowed_bins and binary not in allowed_bins:
                return {"action": "deny", "rule": "commands.allow_binaries", "reason": f"binary {binary} is not in the allow list"}
        if commands.get("deny_sudo", True) and re.search(r"(^|[;&|]\s*)sudo\b", command):
            return {"action": "deny", "rule": "commands.deny_sudo", "reason": "sudo is not allowed for the agent"}

    paths = policy.get("paths", {})
    for path in paths_in(tool, args):
        hit = path_matches(path, paths.get("deny", []), cwd)
        if hit:
            return {"action": "deny", "rule": "paths.deny", "reason": f"path {path} is protected ({hit})"}
    write_tools = paths.get("write_tools", ["write_file", "edit_file", "apply_patch", "manage_files"])
    if tool in write_tools and paths.get("allow_write"):
        for path in paths_in(tool, args):
            if not path_matches(path, paths["allow_write"], cwd):
                return {"action": "deny", "rule": "paths.allow_write", "reason": f"writing {path} is outside the allowed folders"}

    network = policy.get("network", {})
    if network.get("allow_hosts") is not None:
        unknown = sorted(h for h in hosts_in(text) if not host_allowed(h, network["allow_hosts"]))
        if unknown:
            return {"action": "deny", "rule": "network.allow_hosts", "reason": "host not approved: " + ", ".join(unknown)}

    secrets = policy.get("secrets", {})
    if secrets.get("deny_in_arguments", True) and SECRET_RE.search(text):
        return {"action": "deny", "rule": "secrets.deny_in_arguments",
                "reason": "a credential appears in the tool arguments; use an environment variable or the vault"}

    for rule in policy.get("warn", []):
        if not isinstance(rule, dict):
            continue
        if rule.get("tools") and not tool_matches(tool, rule["tools"]):
            continue
        pattern = rule.get("pattern")
        if pattern and not re.search(pattern, text):
            continue
        return {"action": "warn", "rule": f"warn:{rule.get('name', 'rule')}", "reason": str(rule.get("reason") or "flagged by policy")}
    return {"action": "allow", "rule": "", "reason": ""}


def validate_policy(policy: dict[str, Any]) -> None:
    for section in ("tools", "commands", "paths", "network", "mcp", "secrets"):
        if section in policy and not isinstance(policy[section], dict):
            raise PolicyError(f"policy section {section} must be an object")
    for pattern in policy.get("commands", {}).get("deny_patterns", []):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise PolicyError(f"commands.deny_patterns /{pattern}/: {exc}") from exc
    for rule in policy.get("warn", []):
        if isinstance(rule, dict) and rule.get("pattern"):
            try:
                re.compile(str(rule["pattern"]))
            except re.error as exc:
                raise PolicyError(f"warn rule {rule.get('name')}: {exc}") from exc


def append_log(path: Path, event: dict[str, Any], decision: dict[str, Any]) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "session_id": event.get("session_id"),
        "tool_name": event.get("tool_name"),
        "tool_input": trim(event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}),
        "action": decision["action"],
        "rule": decision["rule"],
        "reason": decision["reason"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def hook_output(decision: dict[str, Any]) -> str:
    if decision["action"] == "deny":
        return json.dumps({"hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": f"[agent-runtime-guard] {decision['reason']} (rule {decision['rule']})",
        }})
    if decision["action"] == "warn":
        return json.dumps({"systemMessage": f"[agent-runtime-guard] {decision['reason']} (rule {decision['rule']})"})
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate one Navin tool call against a runtime policy.")
    parser.add_argument("--policy", required=True, type=Path, help="policy JSON (see assets/policy.example.json)")
    parser.add_argument("--event", type=Path, help="event JSON file instead of stdin (dry run)")
    parser.add_argument("--log", type=Path, help="JSONL file that receives one line per evaluated call")
    parser.add_argument("--json", action="store_true", help="print the full decision instead of the hook output")
    args = parser.parse_args(argv)

    try:
        policy = load_json(args.policy, "policy")
        validate_policy(policy)
        if args.event:
            event = load_json(args.event, "event")
        else:
            raw = sys.stdin.read()
            if not raw.strip():
                raise PolicyError("no event on stdin")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise PolicyError(f"event on stdin is not JSON: {exc.msg}") from exc
            if not isinstance(parsed, dict):
                raise PolicyError("event on stdin must be a JSON object")
            event = parsed
    except PolicyError as exc:
        print(f"agent-runtime-guard: {exc}", file=sys.stderr)
        return 2

    if event.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return 0
    decision = evaluate(policy, event)
    if args.log:
        try:
            append_log(args.log, event, decision)
        except OSError as exc:
            print(f"agent-runtime-guard: cannot write {args.log}: {exc}", file=sys.stderr)
    if args.json:
        print(json.dumps({"tool_name": event.get("tool_name"), **decision}, ensure_ascii=False, indent=2))
        return 0
    output = hook_output(decision)
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
