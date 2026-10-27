# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One facade over the infrastructure engine for the tool, the CLI and the Desktop."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from loguru import logger

from navin.infra.broker import Broker, Prepared, redact
from navin.infra.discovery import _terraform_dirs, discover, discoverable_installed, kube_cli
from navin.infra.graph import (
    ResourceGraph,
    build_aws,
    build_azure,
    build_docker,
    build_gcp,
    build_kubernetes,
    build_openstack,
    build_proxmox,
    build_terraform,
    build_vsphere,
    load_graph,
    save_graph,
)
from navin.infra.model import LEVELS
from navin.infra.orchestrator import (
    PlanError,
    blockers,
    create_run,
    list_runs,
    load_run,
    record,
    step_of,
    summary,
)
from navin.infra.parse import classify_command
from navin.infra.policy import (
    InfraPolicy,
    auth_warnings,
    combine,
    current_contexts,
    decide,
    is_production,
    load_policy,
    save_policy,
    update_policy,
    widens,
)
from navin.infra.registry import Registry, which
from navin.infra.runner import Runner, default_runner

#: (binary, provider, version arguments) shown in the status, most used first.
TOOLS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("aws", "aws", ("--version",)), ("gcloud", "gcp", ("--version",)), ("az", "azure", ("--version",)),
    ("kubectl", "kubernetes", ("version", "--client")), ("oc", "openshift", ("version", "--client")),
    ("k3s", "kubernetes", ("--version",)), ("microk8s", "kubernetes", ("version",)), ("rke2", "kubernetes", ("--version",)),
    ("helm", "helm", ("version", "--short")), ("kustomize", "kubernetes", ("version",)), ("eksctl", "kubernetes", ("version",)),
    ("docker", "docker", ("--version",)), ("podman", "podman", ("--version",)), ("nerdctl", "containerd", ("--version",)),
    ("crictl", "containerd", ("--version",)), ("terraform", "terraform", ("version",)), ("tofu", "opentofu", ("version",)),
    ("pulumi", "pulumi", ("version",)), ("cdk", "cdk", ("--version",)), ("ansible", "ansible", ("--version",)),
    ("argocd", "argocd", ("version", "--client", "--short")), ("flux", "flux", ("--version",)), ("gh", "github", ("--version",)),
    ("glab", "gitlab", ("--version",)), ("tea", "forgejo", ("--version",)), ("jenkins-cli", "jenkins", ("version",)),
    ("openstack", "openstack", ("--version",)), ("govc", "vsphere", ("version",)), ("pvesh", "proxmox", ("help",)),
    ("velero", "velero", ("version", "--client-only")), ("trivy", "security", ("--version",)), ("vault", "security", ("version",)),
    ("cmctl", "security", ("version", "--client")), ("falcoctl", "security", ("version",)), ("kyverno", "security", ("version",)),
    ("opa", "security", ("version",)), ("promtool", "observability", ("--version",)), ("logcli", "observability", ("--version",)),
    ("otelcol", "observability", ("--version",)), ("psql", "databases", ("--version",)), ("mysql", "databases", ("--version",)),
    ("redis-cli", "databases", ("--version",)), ("mongosh", "databases", ("--version",)), ("mc", "storage", ("--version",)),
    ("ceph", "storage", ("--version",)), ("k6", "testing", ("version",)), ("hey", "testing", ("-h",)),
    ("infracost", "finops", ("--version",)), ("ssh", "linux", ("-V",)), ("pwsh", "windows", ("--version",)),
)


def _tools_cache_path() -> Path:
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("infra") / "tools.json"


_GRAPH_CLIS = {"gcp": "gcloud", "azure": "az", "openstack": "openstack", "vsphere": "govc", "proxmox": "pvesh"}
_GRAPH_BUILDERS = {"gcp": build_gcp, "azure": build_azure, "openstack": build_openstack, "vsphere": build_vsphere, "proxmox": build_proxmox}


class InfraEngine:
    def __init__(self, workspace: str | Path, *, runner: Runner = default_runner, catalog_root: str | Path | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve(strict=False)
        self.runner = runner
        self.registry = Registry(self.workspace, catalog_root)

    # -- policy -----------------------------------------------------------------------

    def policy(self) -> InfraPolicy:
        return load_policy(self.workspace)

    def contexts(self) -> dict[str, Any]:
        return current_contexts(self.workspace)

    def broker(self) -> Broker:
        return Broker(self.workspace, runner=self.runner, registry=self.registry)

    def preview_policy(self, fields: dict[str, Any]) -> tuple[InfraPolicy, list[str]]:
        """The policy ``fields`` would give, and what it widens (to ask the user)."""
        current = self.policy()
        updated = update_policy(current, fields)
        return updated, widens(current, updated)

    def set_policy(self, fields: dict[str, Any]) -> dict[str, Any]:
        updated, _widened = self.preview_policy(fields)
        save_policy(self.workspace, updated)
        return self.state()

    # -- status ---------------------------------------------------------------------------

    def tools(self, probe: bool = False) -> list[dict[str, Any]]:
        try:
            cache = json.loads(_tools_cache_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            cache = {}  # first probe on this machine
        except (OSError, ValueError) as exc:
            logger.warning("infra: tool version cache unreadable ({}): probing again", exc)
            cache = {}
        rows, to_probe = [], []
        for binary, provider, version_args in TOOLS:
            path = which(binary)
            stamp = f"{path}:{os.path.getmtime(path) if path and os.path.exists(path) else 0}"
            cached = cache.get(binary) or {}
            version = cached.get("version", "") if cached.get("stamp") == stamp else ""
            rows.append({"name": binary, "provider": provider, "available": bool(path), "version": version, "path": path})
            if path and probe and not version:
                to_probe.append((len(rows) - 1, binary, version_args, stamp))
        if to_probe:
            def probe_one(item: tuple[int, str, tuple[str, ...], str]) -> tuple[int, str, str]:
                index, binary, args, stamp = item
                result = self.runner([binary, *args], timeout=20)
                line = next((line.strip() for line in result.output.splitlines() if line.strip()), "")
                return index, line[:120], stamp

            with ThreadPoolExecutor(max_workers=8) as pool:
                for index, version, stamp in pool.map(probe_one, to_probe):
                    rows[index]["version"] = version
                    cache[rows[index]["name"]] = {"version": version, "stamp": stamp}
            try:
                _tools_cache_path().write_text(json.dumps(cache, indent=1), encoding="utf-8")
            except OSError as exc:
                # The versions are already in the answer; only the next probe gets slower.
                logger.warning("infra: could not save the tool version cache: {}", exc)
        return rows

    def state(self, probe: bool = False) -> dict[str, Any]:
        policy = self.policy()
        contexts = self.contexts()
        markers = policy.production_markers
        kube, aws, gcp, terraform = contexts["kubernetes"], contexts["aws"], contexts["gcp"], contexts["terraform"]
        aws_profile = policy.auth.get("aws_profile") or aws["profile"]
        graph = load_graph(self.workspace)
        counts = self.registry.counts(policy, contexts)
        return {
            "project": str(self.workspace),
            "policy": policy.to_dict(),
            "levels": list(LEVELS),
            "tools": self.tools(probe),
            "contexts": {
                "kubernetes": {"current": kube["current"], "contexts": kube["contexts"], "production": is_production(kube["current"], markers)},
                "aws": {"profile": aws_profile, "profiles": aws["profiles"], "production": is_production(aws_profile, markers), "sso_profiles": aws.get("sso_profiles", [])},
                "gcp": {"project": gcp["project"], "configuration": gcp["configuration"], "production": is_production(gcp["project"], markers), "impersonate": policy.auth.get("gcp_impersonate") or gcp.get("impersonate", "")},
                "terraform": {"workspace": terraform["workspace"], "production": is_production(terraform["workspace"], markers)},
            },
            "catalog": {**counts, "providers": self.registry.providers()},
            "warnings": auth_warnings(contexts, policy),
            "graph": {"nodes": len(graph.nodes), "edges": len(graph.edges), "updated_at": graph.updated_at},
            "audit_count": len(self.broker().audit(limit=100000)),
            # What a Discover button may ask for on this machine (the API refuses the rest).
            "discoverable": discoverable_installed(),
        }

    # -- registry -------------------------------------------------------------------------

    def discover(self, provider: str, *, deep: bool = False, context: str = "", workdir: str = "") -> dict[str, Any]:
        results = discover(provider or "all", registry=self.registry, runner=self.runner, deep=deep, context=context, workdir=workdir)
        return {"results": [result.summary() for result in results]}

    def catalog(self, query: str = "", *, provider: str = "", risk: str = "", tier: str = "", limit: int = 50) -> dict[str, Any]:
        return self.registry.search(query, provider=provider, risk=risk, tier=tier, limit=max(1, min(int(limit or 50), 500)),
                                    policy=self.policy(), contexts=self.contexts())

    def describe(self, ref: str) -> dict[str, Any] | None:
        return self.registry.describe(ref, self.policy(), self.contexts())

    def check(self, command: str) -> dict[str, Any]:
        policy, contexts = self.policy(), self.contexts()
        segments = classify_command(command)
        if not segments:
            return {"command": command, "infrastructure": False, "decision": {"action": "allow", "reasons": ["not an infrastructure command"]}}
        decisions = [decide(segment, policy, contexts) for segment in segments]
        combined = combine(decisions)
        match = self.registry.match_command(command)
        return {
            "command": redact(command), "infrastructure": True,
            "segments": [{"command": redact(segment.command), "tool": segment.binary, "provider": segment.provider, "service": segment.service,
                          "verb": segment.verb, "risk": decision.risk, "categories": decision.categories, "targets": decision.targets,
                          "decision": decision.action} for segment, decision in zip(segments, decisions)],
            "decision": combined.to_dict() if combined else None,
            "operation": match.id if match else "",
            "level": policy.level,
        }

    def access(self, operations: list[str]) -> list[dict[str, Any]]:
        out = []
        identity_cache: dict[str, str] = {}
        for ref in operations[:25]:
            op = self.registry.find(ref)
            command = op.command if op else ref
            segments = classify_command(command)
            segment = segments[0] if segments else None
            allowed, how, detail = None, "", ""
            if segment is not None and segment.binary in {"kubectl", "oc"} and segment.parsed is not None:
                allowed, how, detail = self._kube_can_i(segment)
            elif segment is not None and segment.provider == "aws" and op is not None and op.permissions:
                allowed, how, detail = self._aws_simulate(op.permissions[:5], identity_cache)
            else:
                how = "not checked: no provider-side permission test for this platform; the policy decision still applies"
            if op is not None and allowed is not None:
                self.registry.record_access(op.id, allowed, how, detail)
            out.append({"operation": op.id if op else ref, "command": command, "allowed": allowed, "how": how, "detail": detail[:600]})
        return out

    def _kube_can_i(self, segment: Any) -> tuple[bool | None, str, str]:
        p = segment.parsed
        verb = (segment.verb or "get").split(" ")[0]
        mapping = {"get": "get", "describe": "get", "logs": "get", "top": "get", "apply": "patch", "create": "create", "patch": "patch",
                   "replace": "update", "scale": "patch", "delete": "delete", "exec": "create", "rollout": "patch", "edit": "patch",
                   "label": "patch", "annotate": "patch", "set": "patch", "drain": "delete", "cordon": "patch", "debug": "create"}
        resource = segment.resource or ("pods" if verb in {"logs", "exec", "debug"} else "")
        if verb == "logs":
            resource = "pods/log"
        if verb == "exec":
            resource = "pods/exec"
        if not resource:
            return None, "not checked: the command names no resource (manifest file)", ""
        argv = [p.binary, "auth", "can-i", mapping.get(verb, "get"), resource]
        for name in ("--context", "-n", "--namespace"):
            if p.flag(name):
                argv += [name, p.flag(name)]
        result = self.runner(argv, timeout=30)
        answer = result.stdout.strip().lower()
        allowed = True if answer.startswith("yes") else (False if answer.startswith("no") else None)
        return allowed, " ".join(argv), result.output.strip()[-300:]

    def _aws_simulate(self, permissions: list[str], cache: dict[str, str]) -> tuple[bool | None, str, str]:
        if "arn" not in cache:
            identity = self.runner(["aws", "sts", "get-caller-identity", "--output", "json"], timeout=30)
            if not identity.ok:
                return None, "aws sts get-caller-identity failed", identity.output.strip()[-300:]
            arn = json.loads(identity.stdout or "{}").get("Arn", "")
            if ":assumed-role/" in arn:
                role = arn.split(":assumed-role/", 1)[1].split("/", 1)[0]
                found = self.runner(["aws", "iam", "get-role", "--role-name", role, "--query", "Role.Arn", "--output", "text"], timeout=30)
                arn = found.stdout.strip() if found.ok else arn.replace(":sts::", ":iam::").replace(":assumed-role/", ":role/").rsplit("/", 1)[0]
            cache["arn"] = arn
        argv = ["aws", "iam", "simulate-principal-policy", "--policy-source-arn", cache["arn"], "--action-names", *[perm.split(" ")[0] for perm in permissions], "--output", "json"]
        result = self.runner(argv, timeout=60)
        if not result.ok:
            return None, "aws iam simulate-principal-policy failed (it needs iam:SimulatePrincipalPolicy)", result.output.strip()[-300:]
        evaluations = json.loads(result.stdout or "{}").get("EvaluationResults", [])
        allowed = bool(evaluations) and all(entry.get("EvalDecision") == "allowed" for entry in evaluations)
        detail = ", ".join(f"{entry.get('EvalActionName')}={entry.get('EvalDecision')}" for entry in evaluations)
        return allowed, "aws iam simulate-principal-policy", detail

    # -- runs ------------------------------------------------------------------------------

    def _judge(self, command: str):
        segments = classify_command(command)
        if not segments:
            return None, "not an infrastructure command: run local work with exec, or describe it as a manual step (no command)"
        policy, contexts = self.policy(), self.contexts()
        return combine([decide(segment, policy, contexts) for segment in segments]), ""

    def plan(self, goal: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        return summary(create_run(self.workspace, goal, steps, self._judge))

    def prepare(self, command: str, cwd: str = "") -> Prepared:
        return self.broker().prepare(command, cwd=cwd)

    def prepare_step(self, run_id: str, step_id: str) -> tuple[dict[str, Any], Prepared]:
        run = load_run(self.workspace, run_id)
        step = step_of(run, step_id)
        waiting = blockers(run, step_id)
        if waiting:
            raise PlanError(f"step {step_id} waits for {', '.join(waiting)}")
        if step["status"] == "done":
            raise PlanError(f"step {step_id} is already done")
        if not step["command"]:
            raise PlanError(f"step {step_id} is a manual step: mark it with infra action=runs op=done")
        return run, self.prepare(step["command"])

    def execute(self, prepared: Prepared, *, approved: bool = False, timeout: float = 300, run_id: str = "", step_id: str = "") -> dict[str, Any]:
        report = self.broker().execute(prepared, approved=approved, timeout=timeout, run_id=run_id, step_id=step_id)
        if run_id and step_id and report.status != "needs_approval":
            run = load_run(self.workspace, run_id)
            status = {"done": "done", "refused": "refused", "error": "failed", "failed": "failed", "verify_failed": "failed"}[report.status]
            record(self.workspace, run, step_id, status=status, verified=report.verified, snapshot=report.snapshot,
                   exit_code=report.exit_code, recovery=report.recovery)
        elif run_id and step_id:
            run = load_run(self.workspace, run_id)
            record(self.workspace, run, step_id, status="needs_approval")
        return report.to_dict()

    def mark_step(self, run_id: str, step_id: str, status: str) -> dict[str, Any]:
        if status not in {"done", "skipped", "pending"}:
            raise PlanError("a step can be marked done, skipped or pending")
        run = load_run(self.workspace, run_id)
        return summary(record(self.workspace, run, step_id, status=status))

    def verify(self, command: str, cwd: str = "") -> dict[str, Any]:
        return self.broker().verify(command, cwd=cwd)

    def recovery(self, *, run_id: str = "", step_id: str = "", snapshot: str = "") -> dict[str, Any]:
        broker = self.broker()
        if run_id and step_id:
            step = step_of(load_run(self.workspace, run_id), step_id)
            snapshot = snapshot or step.get("snapshot", "")
            plan = list(step.get("recovery") or [])
        else:
            plan = []
        meta = broker.snapshot(snapshot) if snapshot else None
        if snapshot and meta is None:
            raise PlanError(f"snapshot {snapshot} not found")
        restore = " ".join(meta["restore"]) if meta and meta.get("restore") else ""
        if restore and f"restore the snapshot: {restore}" not in plan:
            plan.insert(0, f"restore the snapshot: {restore}")
        return {"snapshot": meta, "restore_command": restore, "plan": plan or ["no snapshot or recovery plan recorded for this step"]}

    def runs(self, run_id: str = "") -> dict[str, Any]:
        if run_id:
            return summary(load_run(self.workspace, run_id))
        return {"items": [summary(run) for run in list_runs(self.workspace)]}

    def audit(self, limit: int = 100) -> dict[str, Any]:
        return {"items": self.broker().audit(limit=max(1, min(int(limit or 100), 2000)))}

    # -- graph --------------------------------------------------------------------------------

    def graph(self, op: str = "summary", *, sources: list[str] | None = None, node: str = "", target: str = "",
              query: str = "", context: str = "", workdir: str = "") -> dict[str, Any]:
        graph = load_graph(self.workspace)
        if op == "build":
            policy, contexts = self.policy(), self.contexts()
            default = ["kubernetes", "aws", "terraform", "docker"] + [name for name, binary in _GRAPH_CLIS.items() if which(binary)]
            for source in sources or default:
                started = time.monotonic()
                built = self._build(source, policy, contexts, context, workdir)
                if not built.nodes:
                    built.errors.append(f"nothing read in {int((time.monotonic() - started) * 1000)} ms")
                graph.merge(built, source)
            save_graph(self.workspace, graph)
        out: dict[str, Any] = {"summary": graph.summary(), "errors": graph.errors[-12:], "nodes": [], "edges": [], "impact": []}
        if op in {"impact", "deps", "path"} or node:
            node_id = graph.resolve(node)
            if not node_id:
                out["errors"] = [*out["errors"], f"no node matches {node!r}: build the graph first (op=build) or use op=find"]
                return out
            out["node"] = graph.nodes[node_id]
            out["edges"] = graph.edges_of(node_id)
            if op == "deps":
                out["deps"] = graph.deps(node_id)
            elif op == "path":
                target_id = graph.resolve(target)
                out["path"] = graph.path(node_id, target_id) if target_id else []
            out["impact"] = graph.impact(node_id)
            out["nodes"] = [graph.nodes[nid] for nid in [node_id, *out["impact"][:200]] if nid in graph.nodes]
        elif op == "find" or query:
            out["nodes"] = graph.find(query)
        else:
            out["nodes"] = list(graph.nodes.values())[:300]
            out["edges"] = [{"src": src, "dst": dst, "rel": rel} for src, dst, rel in sorted(graph.edges)[:600]]
        return out

    def _build(self, source: str, policy: InfraPolicy, contexts: dict[str, Any], context: str, workdir: str) -> ResourceGraph:
        if source == "kubernetes" and kube_cli():
            return build_kubernetes(self.runner, context=context, cli=kube_cli())
        if source == "aws" and which("aws"):
            return build_aws(self.runner, profile=policy.auth.get("aws_profile") or "")
        if source == "terraform" and (which("terraform") or which("tofu")):
            binary = "terraform" if which("terraform") else "tofu"
            graph = ResourceGraph()
            for directory in _terraform_dirs(self.workspace, workdir):
                part = build_terraform(self.runner, str(directory), binary)
                graph.nodes.update(part.nodes)
                graph.edges |= part.edges
                graph.errors.extend(part.errors)
            if not graph.nodes and not graph.errors:
                graph.errors.append("no initialized Terraform module with a state in this project")
            return graph
        if source == "docker" and (which("docker") or which("podman")):
            return build_docker(self.runner, "docker" if which("docker") else "podman")
        if source in _GRAPH_CLIS and which(_GRAPH_CLIS[source]):
            return _GRAPH_BUILDERS[source](self.runner)
        graph = ResourceGraph()
        graph.errors.append(f"{source} is not installed here")
        return graph
