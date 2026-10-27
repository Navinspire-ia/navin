# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The infrastructure orchestrator: registry, discovery, policy, broker, graph, runs, tool, API."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from navin.infra import policy as policy_mod
from navin.infra import registry as registry_mod
from navin.infra.broker import Broker, redact
from navin.infra.discovery import (
    discover,
    discover_ansible,
    discover_help_tree,
    discover_kubernetes,
    discover_terraform,
    kube_operations,
    parse_api_resources,
    parse_help,
)
from navin.infra.engine import InfraEngine
from navin.infra.graph import aws_graph, kubernetes_graph
from navin.infra.orchestrator import PlanError, blockers, create_run, load_run, ready_steps, record
from navin.infra.parse import classify_command, worst
from navin.infra.policy import InfraPolicy, decide, is_production, update_policy, widens
from navin.infra.registry import Registry
from navin.infra.runner import RunResult
from navin.infra.verify import diagnose, verification_plan

PROD = "arn:aws:eks:eu-west-3:111:cluster/eks-prod-01"


class FakeRunner:
    """Answers argv prefixes with canned results and records every call."""

    def __init__(self, answers: dict[tuple[str, ...], RunResult | str] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, timeout=120, env=None, cwd=None, input_text=None):
        self.calls.append(list(argv))
        best = None
        for prefix, answer in self.answers.items():
            if tuple(argv[: len(prefix)]) == prefix and (best is None or len(prefix) > len(best[0])):
                best = (prefix, answer)
        if best is None:
            return RunResult(list(argv), 0, "", "", 1)
        answer = best[1]
        return answer if isinstance(answer, RunResult) else RunResult(list(argv), 0, answer, "", 1)


def fail(text: str, code: int = 1) -> RunResult:
    return RunResult([], code, "", text, 1)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No real kubeconfig, AWS or gcloud files, no catalog or cache in the real home."""
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\ncurrent-context: dev\ncontexts:\n- name: dev\n- name: " + PROD + "\n")
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "gcloud"))
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "TF_WORKSPACE", "GOOGLE_APPLICATION_CREDENTIALS", "CLOUDSDK_CORE_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    policy_mod._CONTEXT_CACHE.clear()
    registry_mod._WHICH_CACHE.clear()
    registry_mod._FILE_CACHE.clear()
    present = {"kubectl", "helm", "aws", "terraform", "docker", "ansible-doc", "ansible"}
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}" if name in present else None)
    monkeypatch.setattr(registry_mod, "default_catalog_root", lambda: tmp_path / "catalog")
    import navin.infra.engine as engine_mod

    monkeypatch.setattr(engine_mod, "_tools_cache_path", lambda: tmp_path / "tools.json")
    project = tmp_path / "project"
    project.mkdir()
    return project


def write_policy(project: Path, **fields) -> None:
    policy_mod.save_policy(project, update_policy(InfraPolicy(), fields))


def judge(command: str, policy: InfraPolicy, project: Path):
    return decide(worst(classify_command(command)), policy, policy_mod.current_contexts(project))


# -- registry: the nine fields and the four tiers -----------------------------------


def test_every_curated_record_carries_the_fields_an_operator_needs(isolated):
    registry = Registry(isolated)
    ops = registry.operations()
    assert len(ops) > 150
    providers = {op.provider for op in ops}
    for needed in ("aws", "gcp", "azure", "kubernetes", "openshift", "docker", "podman", "terraform", "helm", "ansible",
                   "argocd", "flux", "github", "gitlab", "jenkins", "openstack", "vsphere", "proxmox", "security",
                   "observability", "databases", "storage", "linux", "webservers", "windows", "testing", "finops"):
        assert needed in providers, needed
    for op in ops:
        assert op.command and op.service and op.provider and op.examples and op.effects, op.id
        if op.risk in {"change", "critical"} and "local" not in op.categories and op.provider not in {"testing", "linux"}:
            assert op.verify or op.rollback, f"{op.id} has no way to check or undo"


def test_describe_answers_service_command_params_permissions_risk_verify_rollback_compat(isolated):
    pytest.importorskip("botocore")
    engine = InfraEngine(isolated, runner=FakeRunner())
    record = engine.describe("aws rds create-db-instance --db-instance-identifier db1")
    assert record["service"] == "rds" and record["provider"] == "aws"
    assert record["version"].startswith("API ") and "rds" in record["endpoint"]
    assert "--engine" in record["required"] and "--allocated-storage" in record["optional"]
    assert record["permissions"] and record["examples"]
    assert record["risk"] == "critical" and "costly" in record["categories"] and record["effects"]
    assert any("describe-db-instances" in v for v in record["verify"])
    assert any("delete-db-instance" in r for r in record["rollback"])
    assert any("botocore" in c for c in record["compat"])
    assert record["decision"]["action"] == "ask"
    for tier in ("known", "available", "authorized", "tested"):
        assert tier in record


def test_known_available_authorized_and_tested_stay_separate(isolated):
    registry = Registry(isolated)
    contexts = policy_mod.current_contexts(isolated)
    helm = registry.find("helm upgrade --install app ./chart")
    oc = registry.find("oc get projects")
    assert helm and oc
    tiers = registry.tiers(helm, InfraPolicy(), contexts)
    assert tiers["known"] and tiers["available"] and tiers["authorized"] and not tiers["tested"]
    assert not registry.tiers(oc, InfraPolicy(), contexts)["available"]  # oc is not installed
    assert not registry.tiers(helm, InfraPolicy(level="observe"), contexts)["authorized"]
    registry.mark_tested(helm.id, "helm upgrade --install app ./chart", True)
    assert registry.tiers(helm, InfraPolicy(), contexts)["tested"]
    registry.record_access(helm.id, False, "kubectl auth can-i")
    assert not registry.tiers(helm, InfraPolicy(), contexts)["authorized"]
    found = registry.search("helm", tier="tested", policy=InfraPolicy(), contexts=contexts)
    assert [item["id"] for item in found["items"]] == [helm.id]


# -- discovery ----------------------------------------------------------------------------

def _table(rows: list[list[str]]) -> str:
    """Columns padded the way kubectl's tabwriter pads them."""
    widths = [max(len(row[i]) for row in rows) + 3 for i in range(len(rows[0]))]
    return "\n".join("".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows) + "\n"


VERBS = "[create delete deletecollection get list patch update watch]"
API_RESOURCES = _table([
    ["NAME", "SHORTNAMES", "APIVERSION", "NAMESPACED", "KIND", "VERBS", "CATEGORIES"],
    ["pods", "po", "v1", "true", "Pod", VERBS, "all"],
    ["deployments", "deploy", "apps/v1", "true", "Deployment", VERBS, "all"],
    ["nodes", "no", "v1", "false", "Node", VERBS, ""],
    ["certificates", "cert,certs", "cert-manager.io/v1", "true", "Certificate", VERBS, "cert-manager"],
])


def test_kubernetes_discovery_reads_api_resources_and_marks_operator_crds():
    rows = parse_api_resources(API_RESOURCES)
    assert [row["name"] for row in rows] == ["pods", "deployments", "nodes", "certificates"]
    assert rows[1]["group"] == "apps" and rows[2]["namespaced"] is False
    assert "patch" in rows[3]["verbs"] and rows[3]["shortnames"] == ["cert", "certs"]
    ops = kube_operations(rows, "kubectl", "kubernetes", "v1.31.2", {"cert-manager.io"})
    cert_apply = next(op for op in ops if op.id == "kubernetes:cert-manager.io/v1:certificates:apply")
    assert cert_apply.service == "crd:cert-manager.io"
    assert "custom resource (operator / CRD)" in cert_apply.compat
    assert cert_apply.endpoint.startswith("PATCH /apis/cert-manager.io/v1/namespaces/{ns}/certificates")
    assert "patch certificates.cert-manager.io" in cert_apply.permissions
    delete = next(op for op in ops if op.id == "kubernetes:apps/v1:deployments:delete")
    assert delete.risk == "critical" and delete.rollback and "NotFound" in delete.verify[0]
    scale = next(op for op in ops if op.id == "kubernetes:apps/v1:deployments:scale")
    assert any("rollout status" in check for check in scale.verify)


def test_kubernetes_discovery_explains_an_expired_login():
    runner = FakeRunner({
        ("kubectl", "config", "current-context"): "dev\n",
        ("kubectl", "api-resources"): fail("Unable to connect to the server: getting credentials: exec: executable aws failed: Error loading SSO Token: Token has expired and refresh failed"),
    })
    result = discover_kubernetes(runner)
    assert not result.ops
    assert "aws sso login" in result.errors[0]


def test_kubernetes_discovery_of_a_live_cluster_saves_the_crds(isolated):
    runner = FakeRunner({
        ("kubectl", "config", "current-context"): "dev\n",
        ("kubectl", "version"): json.dumps({"clientVersion": {"gitVersion": "v1.35.0"}, "serverVersion": {"gitVersion": "v1.31.2"}}),
        ("kubectl", "api-resources"): API_RESOURCES,
        ("kubectl", "get", "crd"): "cert-manager.io\n",
    })
    registry = Registry(isolated)
    [result] = discover("kubernetes", registry=registry, runner=runner)
    assert result.version == "client v1.35.0, server v1.31.2" and result.context == "dev"
    found = registry.search("certificates", provider="kubernetes")
    assert found["total"] >= 5
    assert registry.providers()[0]["provider"] == "kubernetes"


HELM_HELP = """The Kubernetes package manager

Usage:
  helm [command]

Available Commands:
  completion  generate autocompletion scripts for the specified shell
  install     install a chart
  rollback    roll back a release to a previous revision
  uninstall   uninstall a release

Flags:
      --kube-context string   name of the kubeconfig context to use
  -n, --namespace string      namespace scope for this request
"""

DOCKER_HELP = """Usage:  docker [OPTIONS] COMMAND

Common Commands:
  run         Create and run a new container from an image
  ps          List containers

Management Commands:
  compose*    Docker Compose
  network     Manage networks

Global Options:
      --context string     Name of the context to use
"""


def test_cli_help_trees_become_operations_with_their_risk():
    commands, flags = parse_help(HELM_HELP)
    assert [name for name, _ in commands] == ["install", "rollback", "uninstall"]
    assert "--kube-context" in flags and "--namespace" in flags
    docker, _flags = parse_help(DOCKER_HELP)
    assert [name for name, _ in docker] == ["run", "ps", "compose", "network"]
    runner = FakeRunner({("helm", "--help"): HELM_HELP, ("helm", "version"): "v3.21.3\n", ("helm", "install", "--help"): "Usage:\n  helm install [NAME] [CHART]\n\nFlags:\n      --atomic   roll back on failure\n"})
    result = discover_help_tree(runner, binary="helm", provider="helm", version_args=("version", "--short"))
    by_name = {op.name: op for op in result.ops}
    assert by_name["uninstall"].risk == "critical" and by_name["install"].risk == "change"
    assert "--atomic" in by_name["install"].optional
    assert result.version == "v3.21.3"


def test_terraform_provider_schemas_become_typed_resources(isolated):
    module = isolated / "infra"
    (module / ".terraform").mkdir(parents=True)
    schema = {"provider_schemas": {"registry.terraform.io/hashicorp/aws": {
        "resource_schemas": {"aws_db_instance": {"version": 2, "block": {"attributes": {
            "engine": {"required": True}, "instance_class": {"required": True}, "multi_az": {"optional": True}},
            "block_types": {"timeouts": {}}, "description": "RDS instance"}}},
        "data_source_schemas": {"aws_vpc": {"block": {"attributes": {"id": {"optional": True}}}}},
    }}}
    runner = FakeRunner({
        ("terraform", "-help"): "Usage: terraform [global options] <subcommand> [args]\n\nMain commands:\n  init          Prepare your working directory\n  plan          Show changes required by the current configuration\n  apply         Create or update infrastructure\n  destroy       Destroy previously-created infrastructure\n",
        ("terraform", "version", "-json"): json.dumps({"terraform_version": "1.15.8", "provider_selections": {"registry.terraform.io/hashicorp/aws": "6.2.0"}}),
        ("terraform", f"-chdir={module}", "providers", "schema", "-json"): json.dumps(schema),
    })
    result = discover_terraform(runner, root=str(isolated))
    db = next(op for op in result.ops if op.name == "resource aws_db_instance")
    assert db.required == ["engine", "instance_class"] and "multi_az" in db.optional and "timeouts {}" in db.optional
    assert "6.2.0" in db.version and db.verify and db.rollback
    assert next(op for op in result.ops if op.name == "destroy").risk == "critical"
    assert next(op for op in result.ops if op.name == "data aws_vpc").risk == "read"


def test_terraform_catalog_belongs_to_its_project(isolated, tmp_path):
    """Two projects with different modules: discovering one never replaces the other's."""
    import threading

    def row(op_id: str, provider: str, name: str, command: str) -> dict:
        return {"id": op_id, "provider": provider, "service": provider, "name": name, "command": command}

    first, second = Registry(isolated), Registry(tmp_path / "other")
    first.save("terraform", {"provider": "terraform", "ops": [row("tf:a", "terraform", "resource aws_db_instance", "terraform apply")]})
    second.save("terraform", {"provider": "terraform", "ops": [row("tf:b", "terraform", "resource google_sql", "terraform apply")]})
    first.save("aws", {"provider": "aws", "ops": [row("aws:s3:ListBuckets", "aws", "s3 list", "aws s3 ls")]})
    assert (isolated / ".navin" / "infra" / "catalog" / "terraform.json").is_file()
    assert not (tmp_path / "catalog" / "terraform.json").exists()  # never in the machine catalog

    def ids(registry: Registry) -> set[str]:
        return {op.id for op in registry.operations("terraform") if op.id.startswith("tf:")}

    assert ids(first) == {"tf:a"} and ids(second) == {"tf:b"}
    # The machine-wide providers stay shared by every project.
    assert {row["provider"] for row in second.providers()} == {"aws", "terraform"}
    assert any(op.id == "aws:s3:ListBuckets" for op in second.operations())

    # Concurrent saves of one provider (two engines) always leave a complete file.
    big = {"provider": "aws", "ops": [row(f"aws:x:{n}", "aws", "x", "aws x") for n in range(4000)]}
    errors = []

    def write():
        try:
            for _ in range(5):
                first.save("aws", big)
        except OSError as exc:  # the test reports any write failure below
            errors.append(exc)

    threads = [threading.Thread(target=write) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(json.loads((tmp_path / "catalog" / "aws.json").read_text())["ops"]) == 4000
    assert not list((tmp_path / "catalog").glob("*.tmp"))


def test_an_unreadable_terraform_version_is_reported(isolated):
    runner = FakeRunner({
        ("terraform", "-help"): "Usage: terraform\n\nMain commands:\n  plan          Show changes\n",
        ("terraform", "version", "-json"): "Terraform v1.15.8 (plain text from an old CLI)\n",
    })
    result = discover_terraform(runner, root=str(isolated))
    assert result.version == "Terraform v1.15.8 (plain text from an old CLI)"
    assert any("provider versions unknown" in error for error in result.errors)


def test_ansible_modules_are_discovered():
    runner = FakeRunner({("ansible", "--version"): "ansible [core 2.18.1]\n", ("ansible-doc", "-l", "-j"): json.dumps({"ansible.builtin.ping": "Try to connect", "ansible.builtin.shell": "Execute shell commands"})})
    result = discover_ansible(runner)
    by_name = {op.name: op for op in result.ops}
    assert by_name["ansible.builtin.ping"].risk == "read"
    assert "remote-exec" in by_name["ansible.builtin.shell"].categories


def test_aws_discovery_reads_botocore_models_and_cloud_control_types(isolated, monkeypatch):
    botocore_session = pytest.importorskip("botocore.session")
    monkeypatch.setattr(botocore_session.Session, "get_available_services", lambda self: ["eks", "s3"])
    types = {"TypeSummaries": [{"TypeName": "AWS::Logs::LogGroup", "Description": "log group"}]}
    runner = FakeRunner({("aws", "--version"): "aws-cli/2.33.5\n", ("aws", "cloudformation", "list-types"): json.dumps(types)})
    registry = Registry(isolated)
    [result] = discover("aws", registry=registry, runner=runner, deep=True)
    ids = {op.id for op in result.ops}
    assert "aws:eks:CreateCluster" in ids and "aws:s3:PutObject" in ids
    assert "aws:cloudcontrol:AWS::Logs::LogGroup:delete" in ids
    create = next(op for op in result.ops if op.id == "aws:eks:CreateCluster")
    assert create.permissions == ["eks:CreateCluster"] and "--name" in create.required
    assert next(op for op in result.ops if op.id == "aws:s3:PutObject").command == "aws s3api put-object"
    assert registry.search("create-cluster", provider="aws")["items"][0]["command"] == "aws eks create-cluster"


# -- policy -----------------------------------------------------------------------------------


def test_levels_observe_prepare_execute(isolated):
    apply = "kubectl apply -f app.yaml --context dev"
    plan = "terraform plan -out=tfplan"
    read = "kubectl get pods --context dev"
    assert judge(read, InfraPolicy(level="observe"), isolated).action == "allow"
    assert judge(plan, InfraPolicy(level="observe"), isolated).action == "ask"
    assert judge(apply, InfraPolicy(level="observe"), isolated).action == "deny"
    assert judge(plan, InfraPolicy(level="prepare"), isolated).action == "allow"
    assert judge(apply, InfraPolicy(level="prepare"), isolated).action == "ask"
    assert judge(apply, InfraPolicy(level="execute"), isolated).action == "allow"


@pytest.mark.parametrize("command,category", [
    ("kubectl delete deploy/web -n shop --context dev", ""),
    ("aws iam attach-role-policy --role-name app --policy-arn arn:aws:iam::aws:policy/AdministratorAccess", "iam"),
    ("aws ec2 authorize-security-group-ingress --group-id sg-1 --port 22 --cidr 0.0.0.0/0", "network"),
    ("aws ec2 run-instances --image-id ami-1 --count 20", "costly"),
    ("docker compose down -v", "data"),
    ("aws s3 rm s3://bucket --recursive", "data"),
    ("kubectl get secret db -o yaml --context dev", "secrets"),
    ("ssh admin@web-1 systemctl stop nginx", "availability"),
    ("psql postgres://db -c 'drop table orders'", "data"),
    ("gcloud projects add-iam-policy-binding p1 --member user:x --role roles/owner", "iam"),
])
def test_critical_operations_always_ask_even_at_execute(isolated, command, category):
    decision = judge(command, InfraPolicy(level="execute"), isolated)
    assert decision.action == "ask", command
    if category:
        assert category in decision.categories


def test_production_targets_and_scopes(isolated):
    decision = judge(f"kubectl apply -f app.yaml --context {PROD}", InfraPolicy(), isolated)
    assert decision.risk == "critical" and decision.production and "production" in decision.categories
    assert is_production(PROD, ["prod"]) and is_production("shop-production", ["production"])
    assert not is_production("product-catalog", ["prod"])
    scoped = InfraPolicy(scopes={**InfraPolicy().scopes, "kube_contexts": ["dev"], "namespaces": ["shop"]})
    assert judge("kubectl rollout restart deploy/web -n shop --context dev", scoped, isolated).action == "allow"
    outside = judge("kubectl rollout restart deploy/web -n billing --context dev", scoped, isolated)
    assert outside.action == "ask" and "out-of-scope" in outside.categories
    assert judge("terraform apply", InfraPolicy(), isolated).action == "ask"  # no saved plan
    assert judge("terraform apply tfplan", InfraPolicy(), isolated).action == "allow"


def test_scale_to_zero_is_critical_and_compose_dry_run_is_a_plan():
    """Audit against the real CLIs: danger levels of a stop and of a Compose dry run."""
    stopped = worst(classify_command("kubectl scale deploy/web -n shop --replicas=0"))
    assert stopped.risk == "critical" and "availability" in stopped.categories
    assert worst(classify_command("kubectl scale deploy/web -n shop --replicas 0")).risk == "critical"
    assert worst(classify_command("kubectl scale deploy/web -n shop --replicas=3")).risk == "change"
    # docker compose --dry-run (global flag, Compose v2.17+) only prints what it would do.
    assert worst(classify_command("docker compose --dry-run up -d")).risk == "plan"
    assert worst(classify_command("docker compose up -d --dry-run")).risk == "plan"
    assert worst(classify_command("docker compose down -v --dry-run")).risk == "plan"
    assert worst(classify_command("docker compose up -d")).risk == "change"


def test_curated_examples_use_the_flags_of_the_installed_clis():
    """kubectl 1.35 dropped autoscale --cpu-percent for --cpu=70%; az monitor metrics list takes --metrics; gcloud monitoring policies is GA."""
    from navin.infra.curated import curated_operations

    by_command = {op.command: op for op in curated_operations()}
    autoscale = by_command["kubectl autoscale deployment"].examples[0]
    assert "--cpu=70%" in autoscale and "--cpu-percent" not in autoscale
    metrics = by_command["az monitor metrics list"].examples[0]
    assert "--metrics " in metrics and "--metric " not in metrics
    policies = by_command["gcloud monitoring policies list"].examples[0]
    assert policies.startswith("gcloud monitoring policies list")


def test_widening_the_policy_is_detected_and_long_lived_keys_are_reported(isolated, tmp_path):
    narrow = InfraPolicy(level="prepare", scopes={**InfraPolicy().scopes, "kube_contexts": ["dev"]})
    wider = update_policy(narrow, {"level": "execute", "scopes": {"kube_contexts": []}})
    assert len(widens(narrow, wider)) == 2
    assert widens(wider, narrow) == []
    with pytest.raises(ValueError):
        update_policy(narrow, {"level": "admin"})
    (tmp_path / "aws-credentials").write_text("[legacy]\naws_access_key_id = AKIAABCDEFGHIJKLMNOP\naws_secret_access_key = x\n")
    policy_mod._CONTEXT_CACHE.clear()
    warnings = policy_mod.auth_warnings(policy_mod.current_contexts(isolated), InfraPolicy())
    assert any("IAM Identity Center" in warning and "legacy" in warning for warning in warnings)


def test_exec_obeys_the_same_policy(isolated):
    from navin.agent.approval import ApprovalDecision, bind_approval_gate, reset_approval_gate
    from navin.agent.tools.shell import ExecTool

    write_policy(isolated, level="observe")
    tool = object.__new__(ExecTool)
    refusal = asyncio.run(tool._infra_gate("kubectl scale deploy/web --replicas=0 --context dev", str(isolated), str(isolated), already_asked=False))
    assert refusal and "observe" in refusal
    assert asyncio.run(tool._infra_gate("kubectl get pods --context dev", str(isolated), str(isolated), already_asked=False)) is None
    assert asyncio.run(tool._infra_gate("ls -la && git status", str(isolated), str(isolated), already_asked=False)) is None

    write_policy(isolated, level="execute")
    asked = []

    class Gate:
        def __init__(self, allowed):
            self.allowed = allowed

        async def ask(self, request):
            asked.append(request)
            return ApprovalDecision(allowed=self.allowed, reason="no")

    async def through(gate, command):
        token = bind_approval_gate(gate)
        try:
            return await tool._infra_gate(command, str(isolated), str(isolated), already_asked=False)
        finally:
            reset_approval_gate(token)

    assert asyncio.run(through(Gate(False), "helm uninstall shop -n shop")) is not None
    assert asked[-1].scope == ""  # a critical operation is asked every time
    assert asyncio.run(through(Gate(True), "helm uninstall shop -n shop")) is None
    # Nobody to ask (cron, unattended run): critical work is refused.
    assert "not approved" in asyncio.run(tool._infra_gate("helm uninstall shop -n shop", str(isolated), str(isolated), already_asked=False))


def test_exec_fails_closed_when_the_policy_cannot_be_read(isolated, monkeypatch):
    import navin.infra.gate as gate
    from navin.agent.tools.shell import ExecTool

    def broken(*_args, **_kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(gate, "exec_decision", broken)
    tool = object.__new__(ExecTool)
    refusal = asyncio.run(tool._infra_gate("kubectl delete ns shop", str(isolated), str(isolated), already_asked=False))
    assert refusal.startswith("Error: the infrastructure policy could not be evaluated") and "Nothing ran" in refusal


def test_unreadable_configs_are_reported_not_ignored(isolated, tmp_path, monkeypatch):
    (tmp_path / "kubeconfig").write_text("current-context: staging\ncontexts: [unclosed\n")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing-key.json"))
    policy_mod._CONTEXT_CACHE.clear()
    contexts = policy_mod.current_contexts(isolated)
    assert contexts["kubernetes"]["current"] == "staging"  # still read by hand
    warnings = policy_mod.auth_warnings(contexts, InfraPolicy())
    assert any("is not valid YAML" in warning for warning in warnings)
    assert any("missing-key.json" in warning for warning in warnings)
    from navin.infra.graph import graph_path, load_graph

    graph_path(isolated).parent.mkdir(parents=True, exist_ok=True)
    graph_path(isolated).write_text("{not json")
    assert "unreadable" in load_graph(isolated).errors[0]
    write = policy_mod.infra_dir(isolated) / "policy.json"
    write.write_text("{broken")
    assert policy_mod.load_policy(isolated).level == "observe"  # a damaged policy never widens


# -- broker, verification, recovery, audit ------------------------------------------------------------

LIVE_DEPLOYMENT = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: shop
  uid: 1234
  resourceVersion: "99"
  annotations:
    kubectl.kubernetes.io/last-applied-configuration: "{}"
spec:
  replicas: 2
status:
  readyReplicas: 2
"""


def test_a_change_is_snapshotted_run_verified_audited_and_marked_tested(isolated):
    (isolated / "app.yaml").write_text("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n  namespace: shop\n")
    runner = FakeRunner({
        ("kubectl", "get", "-f", "app.yaml", "-o", "yaml"): LIVE_DEPLOYMENT,
        ("kubectl", "apply"): "deployment.apps/web configured\n",
        ("kubectl", "rollout", "status"): 'deployment "web" successfully rolled out\n',
    })
    broker = Broker(isolated, runner=runner, policy=InfraPolicy())
    prepared = broker.prepare("kubectl apply -f app.yaml --context dev")
    assert prepared.decision.action == "allow" and not prepared.error
    report = broker.execute(prepared)
    assert report.status == "done" and report.verified is True
    assert [call[:2] for call in runner.calls] == [["kubectl", "get"], ["kubectl", "apply"], ["kubectl", "get"], ["kubectl", "rollout"]]
    assert ["kubectl", "rollout", "status", "deployment/web", "--timeout=300s", "--context", "dev", "-n", "shop"] in runner.calls
    snapshot = broker.snapshot(report.snapshot)
    saved = Path(snapshot["path"]).read_text()
    assert "uid" not in saved and "resourceVersion" not in saved and "status" not in saved and "replicas: 2" in saved
    assert snapshot["restore"][:3] == ["kubectl", "apply", "-f"]
    [entry] = broker.audit()
    assert entry["ok"] and entry["verified"] is True and entry["snapshot"] == report.snapshot and entry["decision"] == "allow"
    assert broker.registry.tested()[report.op_id]["verified"] is True


def test_a_failed_change_is_diagnosed_and_gets_a_recovery_plan(isolated):
    runner = FakeRunner({
        ("helm", "history"): json.dumps([{"revision": 4, "status": "superseded"}, {"revision": 5, "status": "deployed"}]),
        ("helm", "upgrade"): fail("Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress"),
    })
    broker = Broker(isolated, runner=runner, policy=InfraPolicy())
    report = broker.execute(broker.prepare("helm upgrade shop ./chart -n shop --kube-context dev"))
    assert report.status == "failed"
    assert report.diagnosis[0]["cause"] == "Helm release locked"
    assert report.recovery[0] == "restore the snapshot: helm rollback shop 5 --wait --kube-context dev -n shop"
    assert broker.audit()[0]["error_kind"] == "Helm release locked"


def test_the_broker_refuses_shells_unknown_tools_and_asks_for_critical_work(isolated):
    broker = Broker(isolated, runner=FakeRunner(), policy=InfraPolicy())
    assert "without a shell" in broker.prepare("kubectl get pods | grep web").error
    assert "without a shell" in broker.prepare("kubectl get pods && kubectl delete pod x").error
    assert not broker.prepare("aws ec2 describe-instances --query 'Reservations[].Instances[?State.Name==`running`]'").error
    assert broker.prepare("rm -rf /tmp/x").error
    pending = broker.execute(broker.prepare("kubectl delete ns shop --context dev"))
    assert pending.status == "needs_approval" and broker.runner.calls == []
    observe = Broker(isolated, runner=FakeRunner(), policy=InfraPolicy(level="observe"))
    assert observe.execute(observe.prepare("kubectl scale deploy/web --replicas=3 --context dev")).status == "refused"


def test_outputs_are_redacted_and_failures_diagnosed():
    text = redact("key AKIAABCDEFGHIJKLMNOP\npassword: hunter2hunter2\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz123456\n")
    assert "AKIA" not in text and "hunter2" not in text and "abcdefghij" not in text
    secret = redact("apiVersion: v1\nkind: Secret\ndata:\n  password: c3VwZXJzZWNyZXQ=\n")
    assert "c3VwZXJzZWNyZXQ" not in secret
    assert diagnose("An error occurred (AccessDenied) when calling the CreateCluster operation")[0]["cause"] == "AWS permission missing"
    assert diagnose("Back-off pulling image: ImagePullBackOff")[0]["cause"] == "Image cannot be pulled"
    assert diagnose("Error: Error acquiring the state lock")[0]["cause"] == "Terraform state locked"


def test_verification_plans_per_platform(isolated):
    def plan(command):
        return [" ".join(check.argv) for check in verification_plan(worst(classify_command(command)), isolated)]

    assert plan("kubectl rollout restart deploy/web -n shop") == ["kubectl rollout status deploy/web --timeout=300s -n shop"]
    assert plan("kubectl delete deploy/web -n shop") == ["kubectl get deploy/web -n shop"]
    assert plan("helm upgrade --install shop ./chart -n shop") == ["helm status shop -n shop"]
    assert plan("terraform -chdir=infra apply tfplan") == ["terraform -chdir=infra plan -detailed-exitcode -input=false -lock=false"]
    assert plan("systemctl restart nginx") == ["systemctl is-active nginx"]
    assert plan("kubectl get pods") == []


# -- graph and runs --------------------------------------------------------------------------


def test_kubernetes_graph_gives_the_blast_radius_of_a_node():
    items = [
        {"kind": "Node", "metadata": {"name": "ip-10-0-1-5"}},
        {"kind": "Namespace", "metadata": {"name": "shop"}},
        {"kind": "Deployment", "metadata": {"name": "web", "namespace": "shop"}},
        {"kind": "ReplicaSet", "metadata": {"name": "web-7d", "namespace": "shop", "ownerReferences": [{"kind": "Deployment", "name": "web"}]}},
        {"kind": "Pod", "metadata": {"name": "web-7d-x", "namespace": "shop", "labels": {"app": "web"}, "ownerReferences": [{"kind": "ReplicaSet", "name": "web-7d"}]},
         "spec": {"nodeName": "ip-10-0-1-5", "volumes": [{"persistentVolumeClaim": {"claimName": "data"}}]}},
        {"kind": "Service", "metadata": {"name": "web", "namespace": "shop"}, "spec": {"selector": {"app": "web"}}},
        {"kind": "Ingress", "metadata": {"name": "web", "namespace": "shop"}, "spec": {"rules": [{"http": {"paths": [{"backend": {"service": {"name": "web"}}}]}}]}},
    ]
    graph = kubernetes_graph(items, "dev")
    node = "kubernetes:dev/_/Node/ip-10-0-1-5"
    impact = graph.impact(node)
    assert "kubernetes:dev/shop/Pod/web-7d-x" in impact
    assert "kubernetes:dev/shop/Service/web" in impact and "kubernetes:dev/shop/Ingress/web" in impact
    assert "kubernetes:dev/shop/PersistentVolumeClaim/data" in graph.deps("kubernetes:dev/shop/Pod/web-7d-x")
    assert graph.path("kubernetes:dev/shop/Ingress/web", node)[-1] == node


def test_aws_graph_links_vpc_subnets_instances_eks_and_rds():
    data = {
        "vpcs": [{"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16"}],
        "subnets": [{"SubnetId": "subnet-a", "VpcId": "vpc-1", "AvailabilityZone": "eu-west-3a"}],
        "reservations": [{"Instances": [{"InstanceId": "i-1", "SubnetId": "subnet-a", "SecurityGroups": [{"GroupId": "sg-1"}]}]}],
        "eks_clusters": [{"name": "eks-prod-01", "resourcesVpcConfig": {"vpcId": "vpc-1", "subnetIds": ["subnet-a"]}}],
        "db_instances": [{"DBInstanceIdentifier": "orders", "DBSubnetGroup": {"VpcId": "vpc-1", "Subnets": [{"SubnetIdentifier": "subnet-a"}]}}],
    }
    graph = aws_graph(data, "111", "eu-west-3")
    impact = set(graph.impact("aws:vpc:vpc-1"))
    assert {"aws:subnet:subnet-a", "aws:instance:i-1", "aws:eks:eks-prod-01", "aws:rds:orders"} <= impact
    assert graph.nodes["aws:security-group:sg-1"]["missing"] is True


def test_runs_are_ordered_validated_and_resumable(isolated):
    engine = InfraEngine(isolated, runner=FakeRunner({("kubectl", "apply"): "ok\n"}))
    run = engine.plan("Ship the shop", [
        {"id": "deploy", "title": "Deploy", "command": "kubectl apply -f k8s/ --context dev", "depends_on": ["Namespace"]},
        {"id": "ns", "title": "Namespace", "command": "kubectl create namespace shop --context dev"},
        {"id": "drop", "title": "Drop old", "command": "kubectl delete ns legacy --context dev", "depends_on": ["deploy"]},
    ])
    assert [step["id"] for step in run["steps"]] == ["ns", "deploy", "drop"]
    assert run["approvals_needed"] == ["drop"] and run["ready"] == ["ns"]
    saved = load_run(isolated, run["id"])
    assert blockers(saved, "deploy") == ["ns"]
    with pytest.raises(PlanError):
        engine.prepare_step(run["id"], "deploy")
    record(isolated, saved, "ns", status="done")
    assert ready_steps(load_run(isolated, run["id"])) == ["deploy"]
    _run, prepared = engine.prepare_step(run["id"], "deploy")
    assert engine.execute(prepared, run_id=run["id"], step_id="deploy")["status"] == "done"
    assert engine.runs(run["id"])["status"] == "running"
    with pytest.raises(PlanError, match="cycle"):
        create_run(isolated, "loop", [{"id": "a", "title": "a", "depends_on": ["b"]}, {"id": "b", "title": "b", "depends_on": ["a"]}], engine._judge)
    with pytest.raises(PlanError, match="unknown"):
        create_run(isolated, "x", [{"id": "a", "title": "a", "depends_on": ["zz"]}], engine._judge)


# -- agent tool, CLI, Desktop API -----------------------------------------------------------------


def _tool(isolated, runner):
    from navin.agent.tools.infra import InfraTool

    tool = object.__new__(InfraTool)
    tool._engine = lambda: InfraEngine(isolated, runner=runner)
    return tool


def test_the_agent_tool_checks_runs_and_refuses_unapproved_critical_work(isolated):
    runner = FakeRunner({("kubectl", "rollout", "restart"): "restarted\n", ("kubectl", "rollout", "status"): "successfully rolled out\n"})
    tool = _tool(isolated, runner)
    checked = json.loads(asyncio.run(tool.execute(action="check", command=f"kubectl delete deploy/web --context {PROD}")))
    assert checked["decision"]["action"] == "ask" and checked["decision"]["production"]
    done = json.loads(asyncio.run(tool.execute(action="run", command="kubectl rollout restart deploy/web -n shop --context dev")))
    assert done["status"] == "done" and done["verified"] is True
    refused = asyncio.run(tool.execute(action="run", command="kubectl delete ns shop --context dev"))
    assert refused.is_error and json.loads(refused)["status"] == "not_approved"
    assert not any(call[:2] == ["kubectl", "delete"] for call in runner.calls)
    widened = asyncio.run(tool.execute(action="policy", op="set", level="execute", scopes={"kube_contexts": []}))
    assert json.loads(widened)["policy"]["level"] == "execute"  # same level, no scope before: nothing widened
    write_policy(isolated, level="observe")
    raised = asyncio.run(tool.execute(action="policy", op="set", level="execute"))
    assert raised.is_error and policy_mod.load_policy(isolated).level == "observe"
    assert tool.call_read_only({"action": "check"}) and not tool.call_read_only({"action": "run"})
    status = json.loads(asyncio.run(tool.execute(action="status")))
    assert status["policy"]["level"] == "observe" and status["catalog"]["known"] > 150


def test_cli_check_and_catalog(isolated):
    from rich.console import Console
    from typer.testing import CliRunner

    from navin.cli.infra_cmd import create_infra_app

    app = create_infra_app(console=Console(width=200))
    result = CliRunner().invoke(app, ["check", "kubectl delete deploy/web --context dev", "-w", str(isolated), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["decision"]["action"] == "ask"
    result = CliRunner().invoke(app, ["catalog", "rollout undo", "-w", str(isolated), "--json"])
    assert any(item["command"] == "kubectl rollout undo" for item in json.loads(result.output)["items"])


def test_desktop_routes(isolated):
    from navin.webui.ws_http import GatewayHTTPHandler

    class Workspaces:
        def scope_for_session_key(self, key):
            return SimpleNamespace(project_path=str(isolated))

    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = lambda request: True
    handler.bus = None
    handler.workspaces = Workspaces()

    def get(path):
        request = SimpleNamespace(path=path, headers={})
        return asyncio.run(handler._dispatch_session_routes(request, path.split("?", 1)[0]))

    state = get("/api/sessions/websocket%3Aabc/infra?probe=0")
    assert state.status_code == 200
    body = json.loads(state.body)
    assert body["policy"]["level"] == "execute" and body["contexts"]["kubernetes"]["current"] == "dev"
    updated = json.loads(get('/api/sessions/websocket%3Aabc/infra?probe=0&fields={"level":"prepare"}').body)
    assert updated["policy"]["level"] == "prepare"
    catalog = json.loads(get("/api/sessions/websocket%3Aabc/infra/catalog?q=helm%20rollback&limit=5").body)
    assert catalog["items"] and catalog["items"][0]["command"].startswith("helm rollback")
    operation = json.loads(get(f"/api/sessions/websocket%3Aabc/infra/operation?id={catalog['items'][0]['id']}").body)
    assert operation["rollback"] and operation["decision"]["action"] in {"allow", "ask"}
    assert get("/api/sessions/websocket%3Aabc/infra/catalog?risk=huge").status_code == 400
    assert get("/api/sessions/websocket%3Aabc/infra/operation?id=nope:x").status_code == 404
    assert json.loads(get("/api/sessions/websocket%3Aabc/infra/audit").body) == {"items": []}
    assert json.loads(get("/api/sessions/websocket%3Aabc/infra/runs").body) == {"items": []}
    graph = json.loads(get("/api/sessions/websocket%3Aabc/infra/graph").body)
    assert graph["summary"]["nodes"] == 0
