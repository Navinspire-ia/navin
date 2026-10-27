# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool ``infra``: the Navin Infrastructure Orchestrator.

Plan -> Discover -> Analyze -> Validate -> Execute -> Verify -> Recover, over
the Universal Command & API Registry, the Policy & Approval Engine, the
Execution Broker and the Resource Graph (:mod:`navin.infra`). Reads are free;
changes follow the project's autonomy level and critical operations always
ask the user.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.base import ToolResult
from navin.agent.tools.quality import _QualityTool

_READ_ACTIONS = frozenset({"status", "discover", "catalog", "describe", "check", "access", "graph", "audit", "runs", "verify", "plan"})
_WRITE_ACTIONS = frozenset({"run", "recover", "policy"})
_ACTIONS = tuple(sorted(_READ_ACTIONS | _WRITE_ACTIONS))
_MAX_CHARS = 60_000


def _dump(payload: Any) -> str:
    text = json.dumps(payload, indent=1, ensure_ascii=False, default=str)
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n... [{len(text) - _MAX_CHARS} characters cut: narrow the query or lower limit]"
    return text


class InfraTool(_QualityTool):
    """Discover, plan, run, verify and recover infrastructure operations under policy."""

    # Discovery and verified changes enforce their own timeouts per call.
    wall_timeout_s = 0

    @property
    def name(self) -> str:
        return "infra"

    @property
    def description(self) -> str:
        return (
            "Infrastructure orchestrator for cloud, Kubernetes, IaC, CI/CD, servers and networks "
            "(AWS, GCP, Azure, OpenStack, vSphere, Proxmox, Kubernetes/EKS/GKE/AKS/OpenShift/K3s, Docker/Podman, "
            "Terraform/OpenTofu/Pulumi/CloudFormation, Helm, Ansible, Argo CD, Flux, GitHub/GitLab, Linux). "
            "Use it instead of exec for infrastructure CLIs. status: tools, contexts (production flagged), autonomy "
            "level, credential warnings, catalog counts. discover provider=<aws|gcp|azure|kubernetes|openshift|docker|"
            "podman|terraform|opentofu|helm|ansible|argocd|flux|github|gitlab|openstack|vsphere|proxmox|pulumi|all> "
            "[deep] builds the Universal Command & API Registry from this machine (botocore models, Cloud Control, "
            "gcloud tree, cluster api-resources with CRDs, CLI help trees, Terraform provider schemas, ansible-doc). "
            "catalog query/provider/risk/tier searches it; tiers: known, available (CLI here), authorized (policy), "
            "tested (ran and verified here). describe operation=<id or command>: service, provider, version, exact "
            "command and API endpoint, required/optional parameters, permissions, examples, risks, verification, "
            "rollback, compatibility, policy decision. check command=: risk and allow/ask/deny without running. "
            "access operations=[...]: provider permission test (kubectl auth can-i, aws iam simulate). graph "
            "op=build|summary|impact|deps|path|find: resource graph and blast radius. plan goal= steps=[{id,title,"
            "command,depends_on,owner}]: validated, ordered multi-step run. run command= (one program, no shell) or "
            "run_id+step_id: policy gate, snapshot, execution, automatic verification, diagnosis and recovery plan, "
            "audit. verify command=, recover run_id+step_id|snapshot [restore], audit, runs, policy op=show|set "
            "(level observe|prepare|execute, scopes, production_markers, auth aws_profile/gcp_impersonate; widening "
            "asks the user). Critical operations (production, deletions, IAM, sensitive network, data, costly) "
            "always ask the user. Skill infra-orchestrator."
        )

    def call_read_only(self, arguments: Any) -> bool:
        if not isinstance(arguments, dict):
            return False
        action = arguments.get("action")
        if action == "policy":
            return (arguments.get("op") or "show") == "show"
        if action == "recover":
            return not arguments.get("restore")
        return action in _READ_ACTIONS

    def call_concurrency_safe(self, arguments: Any) -> bool:
        return isinstance(arguments, dict) and arguments.get("action") in {"status", "catalog", "describe", "check", "audit", "runs"}

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "provider": {"type": "string", "description": "discover / catalog: provider id, or all"},
                "deep": {"type": "boolean", "description": "discover: also Cloud Control types (AWS), Google API Discovery, every az group"},
                "context": {"type": "string", "description": "discover / graph: kube context or AWS profile to read"},
                "workdir": {"type": "string", "description": "discover / graph: Terraform module folder (relative to the project)"},
                "query": {"type": "string", "description": "catalog / graph find: search words"},
                "risk": {"type": "string", "enum": ["read", "plan", "change", "critical"]},
                "tier": {"type": "string", "enum": ["known", "available", "authorized", "tested"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "operation": {"type": "string", "description": "describe: an operation id from catalog, or a command line"},
                "operations": {"type": "array", "items": {"type": "string"}, "description": "access: operation ids or commands"},
                "command": {"type": "string", "description": "check / run / verify: one infrastructure command (no pipes or &&)"},
                "cwd": {"type": "string", "description": "run / verify: working folder relative to the project"},
                "timeout": {"type": "integer", "minimum": 5, "maximum": 3600, "description": "run: seconds (default 300)"},
                "goal": {"type": "string", "description": "plan: the mission in one sentence"},
                "steps": {
                    "type": "array",
                    "description": "plan: ordered steps; depends_on names step ids or titles",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"}, "title": {"type": "string"}, "command": {"type": "string"},
                            "depends_on": {"type": "array", "items": {"type": "string"}}, "owner": {"type": "string"},
                            "phase": {"type": "string", "enum": ["plan", "discover", "analyze", "validate", "execute", "verify", "recover"]},
                        },
                        "required": ["title"],
                    },
                },
                "run_id": {"type": "string"},
                "step_id": {"type": "string"},
                "mark": {"type": "string", "enum": ["done", "skipped", "pending"], "description": "runs: mark a manual step"},
                "snapshot": {"type": "string", "description": "recover: snapshot id from a run report"},
                "restore": {"type": "boolean", "description": "recover: apply the snapshot (goes through the policy)"},
                "op": {"type": "string", "description": "graph: build|summary|impact|deps|path|find; policy: show|set"},
                "sources": {"type": "array", "items": {"type": "string", "enum": ["kubernetes", "aws", "terraform", "docker", "gcp", "azure", "openstack", "vsphere", "proxmox"]}},
                "node": {"type": "string", "description": "graph: node id or name"},
                "target": {"type": "string", "description": "graph path: second node"},
                "probe": {"type": "boolean", "description": "status: read tool versions (slower once, cached)"},
                "level": {"type": "string", "enum": ["observe", "prepare", "execute"]},
                "scopes": {"type": "object", "description": "policy set: {aws_profiles, kube_contexts, namespaces, gcp_projects, azure_subscriptions}"},
                "production_markers": {"type": "array", "items": {"type": "string"}},
                "auth": {"type": "object", "description": "policy set: {aws_profile, gcp_impersonate}"},
            },
            "required": ["action"],
        }

    def _engine(self):
        from navin.infra.engine import InfraEngine

        return InfraEngine(self._project_root())

    async def execute(self, action: str = "", **kwargs: Any) -> Any:
        act = (action or "").strip().lower()
        if act not in _ACTIONS:
            return ToolResult.error(f"Error: action must be one of {', '.join(_ACTIONS)}")
        try:
            engine = await asyncio.to_thread(self._engine)
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")
        from navin.infra.orchestrator import PlanError

        try:
            handler = getattr(self, f"_do_{act}")
            return await handler(engine, kwargs)
        except (PlanError, ValueError) as exc:
            return ToolResult.error(f"Error: {exc}")

    # -- reads -------------------------------------------------------------------------

    async def _do_status(self, engine: Any, kw: dict[str, Any]) -> str:
        state = await asyncio.to_thread(engine.state, bool(kw.get("probe")))
        state["tools"] = [tool for tool in state["tools"] if tool["available"]] + [
            {"missing": [tool["name"] for tool in state["tools"] if not tool["available"]]}
        ]
        if not state["catalog"]["providers"]:
            state["next"] = "No provider discovered yet: infra action=discover provider=all"
        return _dump(state)

    async def _do_discover(self, engine: Any, kw: dict[str, Any]) -> str:
        provider = str(kw.get("provider") or "all").strip().lower()
        result = await asyncio.to_thread(engine.discover, provider, deep=bool(kw.get("deep")),
                                         context=str(kw.get("context") or ""), workdir=str(kw.get("workdir") or ""))
        result["next"] = "Search with infra action=catalog query=... provider=...; read a record with infra action=describe"
        return _dump(result)

    async def _do_catalog(self, engine: Any, kw: dict[str, Any]) -> str:
        found = await asyncio.to_thread(engine.catalog, str(kw.get("query") or ""), provider=str(kw.get("provider") or ""),
                                        risk=str(kw.get("risk") or ""), tier=str(kw.get("tier") or ""), limit=int(kw.get("limit") or 40))
        return _dump(found)

    async def _do_describe(self, engine: Any, kw: dict[str, Any]) -> Any:
        ref = str(kw.get("operation") or kw.get("command") or "").strip()
        if not ref:
            return ToolResult.error("Error: describe needs operation= (an id from catalog, or a command line)")
        record = await asyncio.to_thread(engine.describe, ref)
        if record is None:
            return ToolResult.error(f"Error: no operation matches {ref!r}; search with infra action=catalog, or discover the provider first")
        return _dump(record)

    async def _do_check(self, engine: Any, kw: dict[str, Any]) -> Any:
        command = str(kw.get("command") or "").strip()
        if not command:
            return ToolResult.error("Error: check needs command=")
        return _dump(await asyncio.to_thread(engine.check, command))

    async def _do_access(self, engine: Any, kw: dict[str, Any]) -> Any:
        operations = [str(item) for item in (kw.get("operations") or ([kw["command"]] if kw.get("command") else []))]
        if not operations:
            return ToolResult.error("Error: access needs operations=[...] (ids or commands)")
        return _dump({"results": await asyncio.to_thread(engine.access, operations)})

    async def _do_graph(self, engine: Any, kw: dict[str, Any]) -> str:
        op = str(kw.get("op") or ("impact" if kw.get("node") else "summary"))
        if op not in {"build", "summary", "impact", "deps", "path", "find"}:
            raise ValueError("graph op must be build, summary, impact, deps, path or find")
        result = await asyncio.to_thread(engine.graph, op, sources=kw.get("sources") or None, node=str(kw.get("node") or ""),
                                         target=str(kw.get("target") or ""), query=str(kw.get("query") or ""),
                                         context=str(kw.get("context") or ""), workdir=str(kw.get("workdir") or ""))
        if op in {"summary", "build"}:
            result["nodes"] = result["nodes"][:60]
            result["edges"] = result["edges"][:120]
        return _dump(result)

    async def _do_audit(self, engine: Any, kw: dict[str, Any]) -> str:
        return _dump(await asyncio.to_thread(engine.audit, int(kw.get("limit") or 30)))

    async def _do_runs(self, engine: Any, kw: dict[str, Any]) -> Any:
        run_id = str(kw.get("run_id") or "")
        if kw.get("mark"):
            if not run_id or not kw.get("step_id"):
                return ToolResult.error("Error: mark needs run_id and step_id")
            return _dump(await asyncio.to_thread(engine.mark_step, run_id, str(kw["step_id"]), str(kw["mark"])))
        return _dump(await asyncio.to_thread(engine.runs, run_id))

    async def _do_verify(self, engine: Any, kw: dict[str, Any]) -> Any:
        command = str(kw.get("command") or "").strip()
        if not command:
            return ToolResult.error("Error: verify needs the command whose result to check")
        return _dump(await asyncio.to_thread(engine.verify, command, str(kw.get("cwd") or "")))

    async def _do_plan(self, engine: Any, kw: dict[str, Any]) -> str:
        run = await asyncio.to_thread(engine.plan, str(kw.get("goal") or ""), list(kw.get("steps") or []))
        run["next"] = (
            "Run the ready steps in order with infra action=run run_id=<id> step_id=<id>; steps owned by a "
            "subagent go to spawn with that exact call. Approvals listed are asked when the step runs."
        )
        return _dump(run)

    # -- changes ---------------------------------------------------------------------------

    async def _approve(self, prepared: Any, why: str = "") -> tuple[bool, str]:
        from navin.agent.approval import ApprovalRequest, request_approval
        from navin.infra.gate import approval_scope

        decision = prepared.decision
        targets = ", ".join(f"{key} {value}" for key, value in decision.targets.items()) or "the current context"
        verdict = await request_approval(ApprovalRequest(
            tool="infra",
            action=f"Run a {decision.risk} infrastructure operation",
            reason=f"{'; '.join(decision.reasons) or decision.risk} (on {targets}).{(' ' + why) if why else ''}",
            detail=f"$ {prepared.command}\nin {prepared.cwd}",
            consequence=(
                "Navin captures the current state first when it can, verifies the result and logs it; "
                "deletions and data changes may not be reversible."
            ),
            scope=approval_scope(prepared.command, decision),
            allow_when_unattended=False,
        ))
        return verdict.allowed, verdict.reason

    async def _run_prepared(self, engine: Any, prepared: Any, kw: dict[str, Any], run_id: str = "", step_id: str = "") -> Any:
        timeout = float(kw.get("timeout") or 300)
        approved = False
        if not prepared.error and prepared.decision.action == "ask":
            approved, reason = await self._approve(prepared)
            if not approved:
                report = await asyncio.to_thread(engine.execute, prepared, approved=False, run_id=run_id, step_id=step_id)
                report["status"] = "not_approved"
                report["error"] = reason or "the user did not approve this operation"
                return ToolResult.error(_dump(report))
        report = await asyncio.to_thread(engine.execute, prepared, approved=approved, timeout=timeout, run_id=run_id, step_id=step_id)
        if report.get("status") in {"done"}:
            return _dump(report)
        return ToolResult.error(_dump(report))

    async def _do_run(self, engine: Any, kw: dict[str, Any]) -> Any:
        run_id, step_id = str(kw.get("run_id") or ""), str(kw.get("step_id") or "")
        if run_id or step_id:
            if not (run_id and step_id):
                return ToolResult.error("Error: a plan step needs both run_id and step_id")
            _run, prepared = await asyncio.to_thread(engine.prepare_step, run_id, step_id)
            return await self._run_prepared(engine, prepared, kw, run_id, step_id)
        command = str(kw.get("command") or "").strip()
        if not command:
            return ToolResult.error("Error: run needs command= or run_id + step_id")
        prepared = await asyncio.to_thread(engine.prepare, command, str(kw.get("cwd") or ""))
        return await self._run_prepared(engine, prepared, kw)

    async def _do_recover(self, engine: Any, kw: dict[str, Any]) -> Any:
        plan = await asyncio.to_thread(engine.recovery, run_id=str(kw.get("run_id") or ""), step_id=str(kw.get("step_id") or ""),
                                       snapshot=str(kw.get("snapshot") or ""))
        if not kw.get("restore"):
            plan["next"] = "Apply it with restore=true (it goes through the policy), or run one of the plan commands with infra action=run"
            return _dump(plan)
        if not plan["restore_command"]:
            return ToolResult.error(_dump({**plan, "error": "this snapshot has no automatic restore: follow the plan"}))
        prepared = await asyncio.to_thread(engine.prepare, plan["restore_command"])
        return await self._run_prepared(engine, prepared, kw)

    async def _do_policy(self, engine: Any, kw: dict[str, Any]) -> Any:
        op = str(kw.get("op") or "show")
        if op == "show":
            state = await asyncio.to_thread(engine.state)
            return _dump({"policy": state["policy"], "levels": state["levels"], "warnings": state["warnings"]})
        if op != "set":
            raise ValueError("policy op must be show or set")
        fields = {key: kw[key] for key in ("level", "scopes", "production_markers", "auth") if key in kw and kw[key] is not None}
        if not fields:
            return ToolResult.error("Error: policy set needs level, scopes, production_markers or auth")
        updated, widened = await asyncio.to_thread(engine.preview_policy, fields)
        if widened:
            from navin.agent.approval import ApprovalRequest, request_approval

            verdict = await request_approval(ApprovalRequest(
                tool="infra",
                action="Give Navin more autonomy on your infrastructure",
                reason="The agent asks to widen its infrastructure policy: " + "; ".join(widened) + ".",
                detail=json.dumps(updated.to_dict(), indent=1),
                consequence="Critical operations keep asking you whatever the level.",
                scope="infra:policy",
                allow_when_unattended=False,
            ))
            if not verdict.allowed:
                return ToolResult.error(f"Error: the policy was not changed ({verdict.reason or 'not approved'})")
        state = await asyncio.to_thread(engine.set_policy, fields)
        return _dump({"policy": state["policy"], "warnings": state["warnings"]})
