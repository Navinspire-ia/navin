# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Infrastructure Resource Graph: what depends on what.

Nodes are accounts, VPCs, subnets, security groups, instances, clusters,
nodes, namespaces, workloads, pods, services, ingresses, volumes, databases,
load balancers, buckets, Terraform resources and containers. An edge
``a -> b`` means *a depends on b* (a pod runs on a node, a subnet lives in a
VPC, a service routes to pods). So :meth:`ResourceGraph.impact` of a node
lists everything that breaks if it goes away, and :meth:`deps` what it needs.

Builders read the real infrastructure through the runner (read-only calls)
and are pure functions of the JSON they receive, so they are testable.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from navin.infra.policy import infra_dir
from navin.infra.runner import Runner


class ResourceGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: set[tuple[str, str, str]] = set()
        self.sources: list[str] = []
        self.updated_at = ""
        self.errors: list[str] = []

    # -- building ---------------------------------------------------------------

    def add(self, node_id: str, kind: str, name: str, provider: str, **attrs: Any) -> str:
        node = self.nodes.setdefault(node_id, {"id": node_id, "kind": kind, "name": name, "provider": provider})
        node.update({key: value for key, value in attrs.items() if value not in (None, "", [], {})})
        return node_id

    def link(self, src: str, dst: str, rel: str) -> None:
        if src and dst and src != dst:
            self.edges.add((src, dst, rel))

    def merge(self, other: ResourceGraph, source: str) -> None:
        provider_prefix = f"{source}:"
        self.nodes = {key: value for key, value in self.nodes.items() if not key.startswith(provider_prefix)}
        self.edges = {edge for edge in self.edges if not edge[0].startswith(provider_prefix)}
        self.nodes.update(other.nodes)
        self.edges |= other.edges
        if source not in self.sources:
            self.sources.append(source)
        self.errors = [error for error in self.errors if not error.startswith(f"{source}:")] + [f"{source}: {error}" for error in other.errors]
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    # -- queries ------------------------------------------------------------------

    def _walk(self, start: str, forward: bool) -> list[str]:
        index: dict[str, list[str]] = {}
        for src, dst, _rel in self.edges:
            key, value = (src, dst) if forward else (dst, src)
            index.setdefault(key, []).append(value)
        seen, queue, order = {start}, deque([start]), []
        while queue:
            for neighbour in index.get(queue.popleft(), []):
                if neighbour not in seen:
                    seen.add(neighbour)
                    order.append(neighbour)
                    queue.append(neighbour)
        return order

    def impact(self, node_id: str) -> list[str]:
        """Everything that depends, directly or not, on ``node_id``."""
        return self._walk(node_id, forward=False)

    def deps(self, node_id: str) -> list[str]:
        """Everything ``node_id`` needs, directly or not."""
        return self._walk(node_id, forward=True)

    def path(self, source: str, target: str) -> list[str]:
        neighbours: dict[str, set[str]] = {}
        for src, dst, _rel in self.edges:
            neighbours.setdefault(src, set()).add(dst)
            neighbours.setdefault(dst, set()).add(src)
        previous: dict[str, str | None] = {source: None}
        queue = deque([source])
        while queue:
            current = queue.popleft()
            if current == target:
                chain = []
                while current is not None:
                    chain.append(current)
                    current = previous[current]
                return list(reversed(chain))
            for neighbour in sorted(neighbours.get(current, ())):
                if neighbour not in previous:
                    previous[neighbour] = current
                    queue.append(neighbour)
        return []

    def find(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        terms = [term for term in (query or "").lower().split() if term]
        out = []
        for node in self.nodes.values():
            haystack = f"{node['id']} {node['kind']} {node['name']}".lower()
            if all(term in haystack for term in terms):
                out.append(node)
            if len(out) >= limit:
                break
        return out

    def resolve(self, ref: str) -> str:
        """A node id from an id, a name or a fragment (exact id first)."""
        if ref in self.nodes:
            return ref
        matches = [node["id"] for node in self.nodes.values() if node["name"] == ref]
        if not matches:
            matches = [node["id"] for node in self.find(ref, limit=5)]
        return matches[0] if matches else ""

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for node in self.nodes.values():
            kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
        return {"nodes": len(self.nodes), "edges": len(self.edges), "kinds": dict(sorted(kinds.items())),
                "updated_at": self.updated_at, "sources": self.sources}

    def edges_of(self, node_id: str) -> list[dict[str, str]]:
        return [{"src": src, "dst": dst, "rel": rel} for src, dst, rel in sorted(self.edges) if node_id in (src, dst)]

    # -- persistence -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": list(self.nodes.values()), "edges": [list(edge) for edge in sorted(self.edges)],
                "sources": self.sources, "updated_at": self.updated_at, "errors": self.errors}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceGraph:
        graph = cls()
        for node in data.get("nodes", []):
            graph.nodes[node["id"]] = node
        graph.edges = {tuple(edge) for edge in data.get("edges", []) if len(edge) == 3}
        graph.sources = list(data.get("sources", []))
        graph.updated_at = data.get("updated_at", "")
        graph.errors = list(data.get("errors", []))
        return graph


def graph_path(workspace: str | Path) -> Path:
    return infra_dir(workspace) / "graph.json"


def load_graph(workspace: str | Path) -> ResourceGraph:
    path = graph_path(workspace)
    try:
        return ResourceGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return ResourceGraph()  # not built yet
    except (OSError, ValueError, KeyError) as exc:
        graph = ResourceGraph()
        graph.errors.append(f"the saved graph {path} is unreadable ({exc}): build it again")
        return graph


def save_graph(workspace: str | Path, graph: ResourceGraph) -> None:
    path = graph_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(graph.to_dict()), encoding="utf-8")
    temp.replace(path)


# -- Kubernetes ------------------------------------------------------------------------


def _labels_match(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return bool(selector) and all(labels.get(key) == value for key, value in selector.items())


def kubernetes_graph(items: list[dict[str, Any]], cluster: str) -> ResourceGraph:
    graph = ResourceGraph()
    cluster_id = graph.add(f"kubernetes:{cluster}", "cluster", cluster, "kubernetes")

    def node_id(kind: str, namespace: str, name: str) -> str:
        return f"kubernetes:{cluster}/{namespace or '_'}/{kind}/{name}"

    pods: list[tuple[str, dict[str, Any]]] = []
    services: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        kind = item.get("kind", "")
        meta = item.get("metadata") or {}
        name, namespace = meta.get("name", ""), meta.get("namespace", "")
        if not kind or not name:
            continue
        if kind == "Namespace":
            graph.link(graph.add(node_id("Namespace", "", name), "namespace", name, "kubernetes"), cluster_id, "in_cluster")
            continue
        if kind == "Node":
            graph.link(graph.add(node_id("Node", "", name), "node", name, "kubernetes"), cluster_id, "in_cluster")
            continue
        if kind == "PersistentVolume":
            graph.link(graph.add(node_id("PersistentVolume", "", name), "persistentvolume", name, "kubernetes"), cluster_id, "in_cluster")
            continue
        current = graph.add(node_id(kind, namespace, name), kind.lower(), name, "kubernetes", namespace=namespace)
        if namespace:
            graph.link(current, graph.add(node_id("Namespace", "", namespace), "namespace", namespace, "kubernetes"), "in_namespace")
        for owner in meta.get("ownerReferences") or []:
            graph.link(current, node_id(owner.get("kind", ""), namespace, owner.get("name", "")), "owned_by")
        spec = item.get("spec") or {}
        if kind == "Pod":
            pods.append((current, item))
            if spec.get("nodeName"):
                graph.link(current, node_id("Node", "", spec["nodeName"]), "runs_on")
            for volume in spec.get("volumes") or []:
                claim = (volume.get("persistentVolumeClaim") or {}).get("claimName")
                if claim:
                    graph.link(current, node_id("PersistentVolumeClaim", namespace, claim), "mounts")
                for source, ref_kind in (("secret", "Secret"), ("configMap", "ConfigMap")):
                    ref = (volume.get(source) or {}).get("secretName" if source == "secret" else "name")
                    if ref:
                        graph.link(current, node_id(ref_kind, namespace, ref), "mounts")
        elif kind == "Service":
            services.append((current, item))
        elif kind == "Ingress":
            for rule in spec.get("rules") or []:
                for path in ((rule.get("http") or {}).get("paths") or []):
                    backend = (path.get("backend") or {}).get("service") or {}
                    if backend.get("name"):
                        graph.link(current, node_id("Service", namespace, backend["name"]), "routes_to")
            default = ((spec.get("defaultBackend") or {}).get("service") or {}).get("name")
            if default:
                graph.link(current, node_id("Service", namespace, default), "routes_to")
        elif kind == "PersistentVolumeClaim" and spec.get("volumeName"):
            graph.link(current, node_id("PersistentVolume", "", spec["volumeName"]), "bound_to")
    for service_id, service in services:
        selector = (service.get("spec") or {}).get("selector") or {}
        namespace = (service.get("metadata") or {}).get("namespace", "")
        for pod_id, pod in pods:
            meta = pod.get("metadata") or {}
            if meta.get("namespace") == namespace and _labels_match(selector, meta.get("labels") or {}):
                graph.link(service_id, pod_id, "routes_to")
    for src, dst, _rel in list(graph.edges):
        if dst not in graph.nodes:
            parts = dst.split("/")
            graph.add(dst, parts[-2].lower() if len(parts) > 2 else "resource", parts[-1], "kubernetes")
    return graph


def build_kubernetes(runner: Runner, context: str = "", binary: str = "kubectl", cli: list[str] | None = None) -> ResourceGraph:
    """``cli`` is the kubectl to call (``["k3s", "kubectl"]`` on a K3s node); default ``[binary]``."""
    kubectl = list(cli or [binary])
    ctx = ["--context", context] if context else []
    name = context or runner([*kubectl, "config", "current-context"], timeout=15).stdout.strip() or "cluster"
    kinds = "nodes,namespaces,deployments,statefulsets,daemonsets,replicasets,pods,services,ingresses,persistentvolumeclaims,persistentvolumes"
    result = runner([*kubectl, "get", kinds, "-A", "-o", "json", "--request-timeout=60s", *ctx], timeout=180)
    if not result.ok:
        graph = ResourceGraph()
        graph.errors.append(_tail(result.output))
        return graph
    try:
        items = json.loads(result.stdout).get("items", [])
    except ValueError:
        graph = ResourceGraph()
        graph.errors.append("unreadable kubectl output")
        return graph
    return kubernetes_graph(items, name)


# -- AWS -----------------------------------------------------------------------------


def aws_graph(data: dict[str, Any], account: str, region: str) -> ResourceGraph:
    graph = ResourceGraph()
    root = graph.add(f"aws:account:{account}", "account", account, "aws", region=region)

    def nid(kind: str, ident: str) -> str:
        return f"aws:{kind}:{ident}" if ident else ""

    for vpc in data.get("vpcs", []):
        graph.link(graph.add(nid("vpc", vpc["VpcId"]), "vpc", _tag_name(vpc) or vpc["VpcId"], "aws", cidr=vpc.get("CidrBlock")), root, "in_account")
    for subnet in data.get("subnets", []):
        current = graph.add(nid("subnet", subnet["SubnetId"]), "subnet", _tag_name(subnet) or subnet["SubnetId"], "aws", az=subnet.get("AvailabilityZone"), cidr=subnet.get("CidrBlock"), public=subnet.get("MapPublicIpOnLaunch"))
        graph.link(current, nid("vpc", subnet.get("VpcId", "")), "in_vpc")
    for group in data.get("security_groups", []):
        current = graph.add(nid("security-group", group["GroupId"]), "security-group", group.get("GroupName", group["GroupId"]), "aws")
        graph.link(current, nid("vpc", group.get("VpcId", "")), "in_vpc")
    for reservation in data.get("reservations", []):
        for instance in reservation.get("Instances", []):
            current = graph.add(nid("instance", instance["InstanceId"]), "instance", _tag_name(instance) or instance["InstanceId"], "aws", type=instance.get("InstanceType"), state=(instance.get("State") or {}).get("Name"))
            graph.link(current, nid("subnet", instance.get("SubnetId", "")), "in_subnet")
            for group in instance.get("SecurityGroups", []):
                graph.link(current, nid("security-group", group.get("GroupId", "")), "uses")
    for cluster in data.get("eks_clusters", []):
        current = graph.add(nid("eks", cluster["name"]), "eks-cluster", cluster["name"], "aws", version=cluster.get("version"), endpoint_public=(cluster.get("resourcesVpcConfig") or {}).get("endpointPublicAccess"))
        config = cluster.get("resourcesVpcConfig") or {}
        graph.link(current, nid("vpc", config.get("vpcId", "")), "in_vpc")
        for subnet in config.get("subnetIds", []):
            graph.link(current, nid("subnet", subnet), "uses")
        for group in config.get("securityGroupIds", []) + ([config["clusterSecurityGroupId"]] if config.get("clusterSecurityGroupId") else []):
            graph.link(current, nid("security-group", group), "uses")
    for db in data.get("db_instances", []):
        current = graph.add(nid("rds", db["DBInstanceIdentifier"]), "database", db["DBInstanceIdentifier"], "aws", engine=db.get("Engine"), multi_az=db.get("MultiAZ"), encrypted=db.get("StorageEncrypted"))
        subnet_group = db.get("DBSubnetGroup") or {}
        graph.link(current, nid("vpc", subnet_group.get("VpcId", "")), "in_vpc")
        for subnet in subnet_group.get("Subnets", []):
            graph.link(current, nid("subnet", subnet.get("SubnetIdentifier", "")), "uses")
        for group in db.get("VpcSecurityGroups", []):
            graph.link(current, nid("security-group", group.get("VpcSecurityGroupId", "")), "uses")
    for balancer in data.get("load_balancers", []):
        current = graph.add(nid("load-balancer", balancer.get("LoadBalancerName", "")), "load-balancer", balancer.get("LoadBalancerName", ""), "aws", scheme=balancer.get("Scheme"), dns=balancer.get("DNSName"))
        graph.link(current, nid("vpc", balancer.get("VpcId", "")), "in_vpc")
        for zone in balancer.get("AvailabilityZones", []):
            graph.link(current, nid("subnet", zone.get("SubnetId", "")), "uses")
        for group in balancer.get("SecurityGroups", []):
            graph.link(current, nid("security-group", group), "uses")
    for bucket in data.get("buckets", []):
        graph.link(graph.add(nid("bucket", bucket["Name"]), "bucket", bucket["Name"], "aws"), root, "in_account")
    for src, dst, _rel in list(graph.edges):
        if dst not in graph.nodes:
            parts = dst.split(":", 2)
            graph.add(dst, parts[1] if len(parts) > 1 else "resource", parts[2] if len(parts) > 2 else dst, "aws", missing=True)
    return graph


def _tag_name(resource: dict[str, Any]) -> str:
    for tag in resource.get("Tags") or []:
        if tag.get("Key") == "Name":
            return str(tag.get("Value", ""))
    return ""


def build_aws(runner: Runner, profile: str = "", region: str = "") -> ResourceGraph:
    scope = (["--profile", profile] if profile else []) + (["--region", region] if region else []) + ["--output", "json"]

    def call(*args: str) -> dict[str, Any]:
        result = runner(["aws", *args, *scope], timeout=120)
        if not result.ok:
            raise RuntimeError(_tail(result.output))
        return json.loads(result.stdout or "{}")

    errors: list[str] = []
    try:
        identity = call("sts", "get-caller-identity")
    except (RuntimeError, ValueError) as exc:
        graph = ResourceGraph()
        graph.errors.append(f"sts get-caller-identity: {exc}")
        return graph
    data: dict[str, Any] = {}
    for key, args, field_name in (
        ("vpcs", ("ec2", "describe-vpcs"), "Vpcs"), ("subnets", ("ec2", "describe-subnets"), "Subnets"),
        ("security_groups", ("ec2", "describe-security-groups"), "SecurityGroups"),
        ("reservations", ("ec2", "describe-instances"), "Reservations"),
        ("db_instances", ("rds", "describe-db-instances"), "DBInstances"),
        ("load_balancers", ("elbv2", "describe-load-balancers"), "LoadBalancers"),
        ("buckets", ("s3api", "list-buckets"), "Buckets"),
    ):
        try:
            data[key] = call(*args).get(field_name, [])
        except (RuntimeError, ValueError) as exc:
            errors.append(f"{' '.join(args)}: {exc}")
    try:
        clusters = []
        for name in call("eks", "list-clusters").get("clusters", []):
            clusters.append(call("eks", "describe-cluster", "--name", name).get("cluster", {}))
        data["eks_clusters"] = clusters
    except (RuntimeError, ValueError) as exc:
        errors.append(f"eks: {exc}")
    graph = aws_graph(data, identity.get("Account", "account"), region)
    graph.errors.extend(errors)
    return graph


# -- Terraform / Docker -----------------------------------------------------------------------


def terraform_graph(state: dict[str, Any], workdir: str) -> ResourceGraph:
    graph = ResourceGraph()
    root = graph.add(f"terraform:{workdir}", "terraform-module", workdir, "terraform")

    def walk(module: dict[str, Any]) -> None:
        for resource in module.get("resources", []):
            address = resource.get("address", "")
            current = graph.add(f"terraform:{workdir}:{address}", resource.get("type", "resource"), address, "terraform", mode=resource.get("mode"))
            graph.link(current, root, "in_module")
            for dependency in resource.get("depends_on", []) or []:
                graph.link(current, f"terraform:{workdir}:{dependency}", "depends_on")
        for child in module.get("child_modules", []) or []:
            walk(child)

    walk(((state.get("values") or {}).get("root_module")) or {})
    for src, dst, _rel in list(graph.edges):
        if dst not in graph.nodes:
            graph.add(dst, "resource", dst.rsplit(":", 1)[-1], "terraform")
    return graph


def build_terraform(runner: Runner, workdir: str, binary: str = "terraform") -> ResourceGraph:
    result = runner([binary, f"-chdir={workdir}", "show", "-json"], timeout=180)
    if not result.ok:
        graph = ResourceGraph()
        graph.errors.append(_tail(result.output))
        return graph
    try:
        return terraform_graph(json.loads(result.stdout or "{}"), workdir)
    except ValueError:
        graph = ResourceGraph()
        graph.errors.append("unreadable terraform show -json output")
        return graph


def docker_graph(containers: list[dict[str, Any]], host: str) -> ResourceGraph:
    graph = ResourceGraph()
    root = graph.add(f"docker:{host}", "docker-host", host, "docker")
    for container in containers:
        name = (container.get("Name") or "").lstrip("/") or container.get("Id", "")[:12]
        current = graph.add(f"docker:{host}:container:{name}", "container", name, "docker", image=(container.get("Config") or {}).get("Image"), state=(container.get("State") or {}).get("Status"))
        graph.link(current, root, "on_host")
        labels = (container.get("Config") or {}).get("Labels") or {}
        project = labels.get("com.docker.compose.project")
        if project:
            graph.link(current, graph.add(f"docker:{host}:compose:{project}", "compose-project", project, "docker"), "in_project")
        for network in ((container.get("NetworkSettings") or {}).get("Networks") or {}):
            graph.link(current, graph.add(f"docker:{host}:network:{network}", "network", network, "docker"), "attached_to")
        for mount in container.get("Mounts") or []:
            if mount.get("Type") == "volume" and mount.get("Name"):
                graph.link(current, graph.add(f"docker:{host}:volume:{mount['Name']}", "volume", mount["Name"], "docker"), "mounts")
    return graph


def build_docker(runner: Runner, binary: str = "docker") -> ResourceGraph:
    listing = runner([binary, "ps", "-aq"], timeout=60)
    if not listing.ok:
        graph = ResourceGraph()
        graph.errors.append(_tail(listing.output))
        return graph
    ids = listing.stdout.split()
    containers = []
    problems = []
    if ids:
        inspected = runner([binary, "inspect", *ids[:200]], timeout=120)
        try:
            containers = json.loads(inspected.stdout or "[]")
        except ValueError:
            problems.append(f"{binary} inspect: unreadable output ({_tail(inspected.output)})")
    context = runner([binary, "context", "show"], timeout=15).stdout.strip() or "local"
    graph = docker_graph(containers, context)
    graph.errors.extend(problems)
    return graph


def _tail(text: str) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1][:300] if lines else "no output"


# -- shared helpers for the other platforms ------------------------------------------


def _get(item: Any, *keys: str) -> Any:
    """First present key, accepting camelCase, CamelCase and lowercase spellings."""
    if not isinstance(item, dict):
        return None
    for key in keys:
        for variant in (key, key[:1].upper() + key[1:], key[:1].lower() + key[1:], key.lower()):
            if variant in item and item[variant] not in (None, ""):
                return item[variant]
    return None


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _last(url: Any) -> str:
    return str(url or "").rstrip("/").rsplit("/", 1)[-1]


def _fill_missing(graph: ResourceGraph, provider: str) -> None:
    for _src, dst, _rel in list(graph.edges):
        if dst not in graph.nodes:
            parts = dst.split(":")
            graph.add(dst, parts[1] if len(parts) > 2 else "resource", parts[-1], provider, missing=True)


def _call_json(runner: Runner, argv: list[str], errors: list[str], default: Any, timeout: float = 120) -> Any:
    label = " ".join(argv[:4])
    result = runner(argv, timeout=timeout)
    if not result.ok:
        errors.append(f"{label}: {_tail(result.output)}")
        return default
    try:
        return json.loads(result.stdout or "null") or default
    except ValueError:
        errors.append(f"{label}: unreadable JSON output")
        return default


# -- Google Cloud ----------------------------------------------------------------------


def gcp_graph(data: dict[str, Any], project: str) -> ResourceGraph:
    graph = ResourceGraph()
    root = graph.add(f"gcp:project:{project}", "project", project, "gcp")

    def nid(kind: str, ident: Any) -> str:
        return f"gcp:{kind}:{_last(ident)}" if _last(ident) else ""

    for network in _items(data.get("networks")):
        name = _get(network, "name") or ""
        if name:
            graph.link(graph.add(nid("network", name), "network", name, "gcp", mode=_get(network, "subnetMode")), root, "in_project")
    for subnet in _items(data.get("subnets")):
        name = _get(subnet, "name") or ""
        if name:
            current = graph.add(nid("subnet", name), "subnet", name, "gcp", cidr=_get(subnet, "ipCidrRange"), region=_last(_get(subnet, "region")))
            graph.link(current, nid("network", _get(subnet, "network")), "in_network")
    for instance in _items(data.get("instances")):
        name = _get(instance, "name") or ""
        if not name:
            continue
        current = graph.add(nid("instance", name), "instance", name, "gcp", type=_last(_get(instance, "machineType")), state=_get(instance, "status"), zone=_last(_get(instance, "zone")))
        for nic in _items(_get(instance, "networkInterfaces")):
            graph.link(current, nid("subnet", _get(nic, "subnetwork")), "in_subnet")
            graph.link(current, nid("network", _get(nic, "network")), "in_network")
    for rule in _items(data.get("firewalls")):
        name = _get(rule, "name") or ""
        if name:
            graph.link(graph.add(nid("firewall", name), "firewall", name, "gcp", direction=_get(rule, "direction")), nid("network", _get(rule, "network")), "in_network")
    for router in _items(data.get("routers")):
        name = _get(router, "name") or ""
        if name:
            nats = [_get(nat, "name") for nat in _items(_get(router, "nats")) if _get(nat, "name")]
            graph.link(graph.add(nid("router", name), "router", name, "gcp", nats=nats), nid("network", _get(router, "network")), "in_network")
    for service in _items(data.get("backend_services")):
        name = _get(service, "name") or ""
        if name:
            graph.link(graph.add(nid("backend-service", name), "backend-service", name, "gcp", scheme=_get(service, "loadBalancingScheme")), root, "in_project")
    for rule in _items(data.get("forwarding_rules")):
        name = _get(rule, "name") or ""
        if not name:
            continue
        current = graph.add(nid("forwarding-rule", name), "load-balancer", name, "gcp", ip=_get(rule, "IPAddress"), scheme=_get(rule, "loadBalancingScheme"))
        graph.link(current, nid("network", _get(rule, "network")), "in_network")
        graph.link(current, nid("subnet", _get(rule, "subnetwork")), "in_subnet")
        graph.link(current, nid("backend-service", _get(rule, "backendService")), "routes_to")
        target = _get(rule, "target")
        if target:
            graph.link(current, nid("target", target), "routes_to")
    for cluster in _items(data.get("clusters")):
        name = _get(cluster, "name") or ""
        if not name:
            continue
        pools = [_get(pool, "name") for pool in _items(_get(cluster, "nodePools")) if _get(pool, "name")]
        current = graph.add(nid("gke", name), "gke-cluster", name, "gcp", version=_get(cluster, "currentMasterVersion"), location=_get(cluster, "location"), node_pools=pools)
        graph.link(current, nid("network", _get(cluster, "network")), "in_network")
        graph.link(current, nid("subnet", _get(cluster, "subnetwork")), "in_subnet")
    for db in _items(data.get("sql_instances")):
        name = _get(db, "name") or ""
        if not name:
            continue
        current = graph.add(nid("sql", name), "database", name, "gcp", engine=_get(db, "databaseVersion"), state=_get(db, "state"))
        private = _get(_get(_get(db, "settings"), "ipConfiguration"), "privateNetwork")
        graph.link(current, nid("network", private) if private else root, "in_network" if private else "in_project")
    for bucket in _items(data.get("buckets")):
        name = _get(bucket, "name") or (_last(str(bucket).removeprefix("gs://")) if isinstance(bucket, str) else "")
        if name:
            graph.link(graph.add(nid("bucket", name), "bucket", name, "gcp", location=_get(bucket, "location")), root, "in_project")
    _fill_missing(graph, "gcp")
    return graph


def build_gcp(runner: Runner, project: str = "") -> ResourceGraph:
    errors: list[str] = []
    if not project:
        result = runner(["gcloud", "config", "get-value", "project"], timeout=30)
        project = result.stdout.strip() if result.ok else ""
        if not project:
            graph = ResourceGraph()
            graph.errors.append(f"gcloud config get-value project: {_tail(result.output) if not result.ok else 'no project set'}")
            return graph
    scope = ["--format=json", "--project", project]
    data: dict[str, Any] = {}
    for key, args in (
        ("networks", ("compute", "networks", "list")), ("subnets", ("compute", "networks", "subnets", "list")),
        ("instances", ("compute", "instances", "list")), ("firewalls", ("compute", "firewall-rules", "list")),
        ("routers", ("compute", "routers", "list")), ("forwarding_rules", ("compute", "forwarding-rules", "list")),
        ("backend_services", ("compute", "backend-services", "list")), ("clusters", ("container", "clusters", "list")),
        ("sql_instances", ("sql", "instances", "list")), ("buckets", ("storage", "buckets", "list")),
    ):
        data[key] = _call_json(runner, ["gcloud", *args, *scope], errors, [])
    graph = gcp_graph(data, project)
    graph.errors.extend(errors)
    return graph


# -- Azure ------------------------------------------------------------------------------


def azure_graph(data: dict[str, Any], subscription: str) -> ResourceGraph:
    graph = ResourceGraph()
    root = graph.add(f"azure:subscription:{subscription}", "subscription", subscription, "azure")

    def nid(resource_id: Any) -> str:
        text = str(resource_id or "").strip().lower()
        return f"azure:{text}" if text else ""

    def add(item: Any, kind: str, **attrs: Any) -> str:
        ident = _get(item, "id")
        if not ident:
            return ""
        current = graph.add(nid(ident), kind, _get(item, "name") or _last(ident), "azure", location=_get(item, "location"), **attrs)
        group = _get(item, "resourceGroup")
        if not group and "/resourcegroups/" in str(ident).lower():
            group = str(ident).lower().split("/resourcegroups/", 1)[1].split("/", 1)[0]
        if group and kind != "resource-group":
            graph.link(current, nid(f"/subscriptions/{subscription}/resourceGroups/{group}"), "in_group")
        return current

    for group in _items(data.get("groups")):
        current = add(group, "resource-group")
        graph.link(current, root, "in_subscription")
    for vnet in _items(data.get("vnets")):
        current = add(vnet, "vnet", cidr=_get(_get(vnet, "addressSpace"), "addressPrefixes"))
        for subnet in _items(_get(vnet, "subnets")):
            if _get(subnet, "id"):
                sub = graph.add(nid(_get(subnet, "id")), "subnet", _get(subnet, "name") or _last(_get(subnet, "id")), "azure", cidr=_get(subnet, "addressPrefix"))
                graph.link(sub, current, "in_vnet")
    for nic in _items(data.get("nics")):
        current = add(nic, "nic")
        for config in _items(_get(nic, "ipConfigurations")):
            graph.link(current, nid(_get(_get(config, "subnet"), "id")), "in_subnet")
        graph.link(nid(_get(_get(nic, "virtualMachine"), "id")), current, "uses")
    for vm in _items(data.get("vms")):
        current = add(vm, "vm", size=_get(_get(vm, "hardwareProfile"), "vmSize"))
        for nic in _items(_get(_get(vm, "networkProfile"), "networkInterfaces")):
            graph.link(current, nid(_get(nic, "id")), "uses")
    for cluster in _items(data.get("aks")):
        current = add(cluster, "aks-cluster", version=_get(cluster, "kubernetesVersion"))
        for pool in _items(_get(cluster, "agentPoolProfiles")):
            graph.link(current, nid(_get(pool, "vnetSubnetId")), "in_subnet")
    for balancer in _items(data.get("lbs")):
        current = add(balancer, "load-balancer")
        for frontend in _items(_get(balancer, "frontendIPConfigurations")):
            graph.link(current, nid(_get(_get(frontend, "subnet"), "id")), "in_subnet")
    for nsg in _items(data.get("nsgs")):
        current = add(nsg, "security-group")
        for subnet in _items(_get(nsg, "subnets")):
            graph.link(nid(_get(subnet, "id")), current, "uses")
        for nic in _items(_get(nsg, "networkInterfaces")):
            graph.link(nid(_get(nic, "id")), current, "uses")
    for account in _items(data.get("storage")):
        add(account, "storage-account", sku=_get(_get(account, "sku"), "name"))
    for server in _items(data.get("postgres")):
        current = add(server, "database", engine="postgres", version=_get(server, "version"))
        graph.link(current, nid(_get(_get(server, "network"), "delegatedSubnetResourceId")), "in_subnet")
    for server in _items(data.get("sql")):
        add(server, "database", engine="sqlserver", version=_get(server, "version"))
    for node_id in list(graph.nodes):
        if node_id != root and not any(src == node_id for src, _d, _r in graph.edges):
            graph.link(node_id, root, "in_subscription")
    _fill_missing(graph, "azure")
    return graph


def build_azure(runner: Runner) -> ResourceGraph:
    errors: list[str] = []
    account = _call_json(runner, ["az", "account", "show", "-o", "json"], errors, {}, timeout=30)
    subscription = _get(account, "id") if isinstance(account, dict) else ""
    if not subscription:
        graph = ResourceGraph()
        graph.errors.extend(errors or ["az account show: no subscription"])
        return graph
    data: dict[str, Any] = {}
    for key, args in (
        ("groups", ("group", "list")), ("vnets", ("network", "vnet", "list")), ("nics", ("network", "nic", "list")),
        ("vms", ("vm", "list")), ("aks", ("aks", "list")), ("lbs", ("network", "lb", "list")),
        ("nsgs", ("network", "nsg", "list")), ("storage", ("storage", "account", "list")),
        ("postgres", ("postgres", "flexible-server", "list")), ("sql", ("sql", "server", "list")),
    ):
        data[key] = _call_json(runner, ["az", *args, "-o", "json"], errors, [])
    graph = azure_graph(data, str(subscription))
    graph.errors.extend(errors)
    return graph


# -- OpenStack ---------------------------------------------------------------------------


def _server_networks(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(name) for name in value]
    names = []
    for part in str(value or "").split(";"):
        name = part.split("=", 1)[0].strip()
        if name and "=" in part:
            names.append(name)
    return names


def openstack_graph(data: dict[str, Any], cloud: str) -> ResourceGraph:
    graph = ResourceGraph()
    root = graph.add(f"openstack:cloud:{cloud}", "cloud", cloud, "openstack")
    network_by_name: dict[str, str] = {}

    def nid(kind: str, ident: Any) -> str:
        return f"openstack:{kind}:{ident}" if ident else ""

    for network in _items(data.get("networks")):
        ident = _get(network, "ID", "id")
        if ident:
            name = _get(network, "Name", "name") or ident
            network_by_name[str(name)] = graph.link(graph.add(nid("network", ident), "network", name, "openstack"), root, "in_cloud") or nid("network", ident)
    for subnet in _items(data.get("subnets")):
        ident = _get(subnet, "ID", "id")
        if ident:
            current = graph.add(nid("subnet", ident), "subnet", _get(subnet, "Name", "name") or ident, "openstack", cidr=_get(subnet, "Subnet", "cidr"))
            graph.link(current, nid("network", _get(subnet, "Network", "network_id")), "in_network")
    for router in _items(data.get("routers")):
        ident = _get(router, "ID", "id")
        if ident:
            current = graph.add(nid("router", ident), "router", _get(router, "Name", "name") or ident, "openstack", state=_get(router, "Status", "status"))
            graph.link(current, root, "in_cloud")
    for port in _items(data.get("ports")):
        device = _get(port, "device_id", "Device ID")
        owner = str(_get(port, "device_owner", "Device Owner") or "")
        network = _get(port, "network_id", "Network ID")
        if device and network and owner.startswith("network:router"):
            graph.link(nid("router", device), nid("network", network), "attached_to")
    for group in _items(data.get("security_groups")):
        ident = _get(group, "ID", "id")
        if ident:
            graph.link(graph.add(nid("security-group", ident), "security-group", _get(group, "Name", "name") or ident, "openstack"), root, "in_cloud")
    for server in _items(data.get("servers")):
        ident = _get(server, "ID", "id")
        if not ident:
            continue
        current = graph.add(nid("server", ident), "instance", _get(server, "Name", "name") or ident, "openstack", state=_get(server, "Status", "status"), flavor=_get(server, "Flavor Name", "Flavor"))
        for name in _server_networks(_get(server, "Networks", "addresses")):
            graph.link(current, network_by_name.get(name) or nid("network", name), "attached_to")
    for volume in _items(data.get("volumes")):
        ident = _get(volume, "ID", "id")
        if not ident:
            continue
        current = graph.add(nid("volume", ident), "volume", _get(volume, "Name", "Display Name", "name") or ident, "openstack", size=_get(volume, "Size", "size"), state=_get(volume, "Status", "status"))
        attached = _get(volume, "Attached to", "attachments")
        for attachment in attached if isinstance(attached, list) else []:
            server = _get(attachment, "server_id", "Server ID") if isinstance(attachment, dict) else None
            graph.link(nid("server", server), current, "mounts")
        if isinstance(attached, str):
            for server_id in nid_servers(attached, data):
                graph.link(nid("server", server_id), current, "mounts")
    _fill_missing(graph, "openstack")
    return graph


def nid_servers(text: str, data: dict[str, Any]) -> list[str]:
    """Server ids named in an ``Attached to`` string such as ``Attached to web-1 on /dev/vdb``."""
    found = []
    for server in _items(data.get("servers")):
        ident, name = _get(server, "ID", "id"), _get(server, "Name", "name")
        if ident and (str(ident) in text or (name and f" {name} " in f" {text} ")):
            found.append(str(ident))
    return found


def build_openstack(runner: Runner) -> ResourceGraph:
    errors: list[str] = []
    data: dict[str, Any] = {}
    for key, args in (
        ("networks", ("network", "list")), ("subnets", ("subnet", "list")), ("routers", ("router", "list")),
        ("ports", ("port", "list", "--long")), ("servers", ("server", "list", "--long")),
        ("volumes", ("volume", "list", "--long")), ("security_groups", ("security", "group", "list")),
    ):
        data[key] = _call_json(runner, ["openstack", *args, "-f", "json"], errors, [])
    graph = openstack_graph(data, os.environ.get("OS_CLOUD") or os.environ.get("OS_AUTH_URL") or "openstack")
    graph.errors.extend(errors)
    return graph


# -- vSphere -----------------------------------------------------------------------------


def vsphere_graph(data: dict[str, Any], vcenter: str) -> ResourceGraph:
    graph = ResourceGraph()
    root = graph.add(f"vsphere:vcenter:{vcenter}", "vcenter", vcenter, "vsphere")
    host_names: dict[str, str] = {str(key): str(value) for key, value in (data.get("host_names") or {}).items()}

    def nid(kind: str, name: Any) -> str:
        return f"vsphere:{kind}:{name}" if name else ""

    def datacenter(path: str) -> str:
        parts = [part for part in path.split("/") if part]
        if not parts:
            return root
        return graph.link(graph.add(nid("datacenter", parts[0]), "datacenter", parts[0], "vsphere"), root, "in_vcenter") or nid("datacenter", parts[0])

    for path in _items(data.get("clusters")):
        name = _last(path)
        if name:
            graph.link(graph.add(nid("cluster", name), "cluster", name, "vsphere", path=path), datacenter(str(path)), "in_datacenter")
    for path in _items(data.get("hosts")):
        name = _last(path)
        if not name:
            continue
        current = graph.add(nid("host", name), "host", name, "vsphere", path=path)
        parts = [part for part in str(path).split("/") if part]
        if len(parts) >= 4 and parts[1] == "host" and parts[2] != name:
            graph.link(current, nid("cluster", parts[2]), "in_cluster")
        else:
            graph.link(current, datacenter(str(path)), "in_datacenter")
    for kind, key in (("datastore", "datastores"), ("network", "networks")):
        for path in _items(data.get(key)):
            name = _last(path)
            if name:
                graph.link(graph.add(nid(kind, name), kind, name, "vsphere", path=path), datacenter(str(path)), "in_datacenter")
    names_by_ref = {str(k): str(v) for k, v in (data.get("refs") or {}).items()}

    def ref_name(ref: Any) -> str:
        value = _get(ref, "value") if isinstance(ref, dict) else ref
        return names_by_ref.get(str(value), host_names.get(str(value), str(value or "")))

    for vm in _items(data.get("vms")):
        name = _get(vm, "name") or _get(_get(_get(vm, "summary"), "config"), "name")
        if not name:
            continue
        runtime = _get(vm, "runtime")
        current = graph.add(nid("vm", name), "vm", name, "vsphere", state=_get(runtime, "powerState"))
        host = _get(runtime, "host")
        if host:
            graph.link(current, nid("host", ref_name(host)), "runs_on")
        for store in _items(_get(vm, "datastore")):
            graph.link(current, nid("datastore", ref_name(store)), "stored_on")
        for network in _items(_get(vm, "network")):
            graph.link(current, nid("network", ref_name(network)), "attached_to")
    _fill_missing(graph, "vsphere")
    return graph


def build_vsphere(runner: Runner) -> ResourceGraph:
    url = os.environ.get("GOVC_URL", "")
    if not url:
        graph = ResourceGraph()
        graph.errors.append("GOVC_URL is not set: export GOVC_URL (and GOVC_USERNAME/GOVC_PASSWORD) to read vSphere")
        return graph
    errors: list[str] = []
    data: dict[str, Any] = {}
    for key, kind in (("clusters", "c"), ("hosts", "h"), ("datastores", "s"), ("networks", "n")):
        result = runner(["govc", "find", "/", "-type", kind], timeout=120)
        if result.ok:
            data[key] = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        else:
            errors.append(f"govc find -type {kind}: {_tail(result.output)}")
    info = _call_json(runner, ["govc", "vm.info", "-json", "-r", "/*/vm/*"], errors, {}, timeout=180)
    data["vms"] = _items(_get(info, "virtualMachines")) if isinstance(info, dict) else []
    refs: dict[str, str] = {}
    for kind_key, kind in (("hosts", "HostSystem"), ("datastores", "Datastore"), ("networks", "Network")):
        for path in data.get(kind_key, []):
            ref = runner(["govc", "ls", "-i", path], timeout=30)
            moref = ref.stdout.strip().split(":", 1)[-1] if ref.ok and ref.stdout.strip().startswith(f"{kind}:") else ""
            if moref:
                refs[moref] = _last(path)
    data["refs"] = refs
    graph = vsphere_graph(data, url.split("@")[-1].split("/")[0] or url)
    graph.errors.extend(errors)
    return graph


# -- Proxmox -----------------------------------------------------------------------------


def proxmox_graph(resources: list[dict[str, Any]], cluster: str) -> ResourceGraph:
    graph = ResourceGraph()
    root = graph.add(f"proxmox:cluster:{cluster}", "cluster", cluster, "proxmox")
    for entry in _items(resources):
        kind = str(_get(entry, "type") or "")
        node = _get(entry, "node")
        node_id = f"proxmox:node:{node}" if node else ""
        if kind == "node" and node:
            graph.link(graph.add(node_id, "node", str(node), "proxmox", status=_get(entry, "status")), root, "in_cluster")
        elif kind in {"qemu", "lxc"}:
            vmid = _get(entry, "vmid") or _last(_get(entry, "id"))
            if not vmid:
                continue
            current = graph.add(f"proxmox:{kind}:{vmid}", "vm" if kind == "qemu" else "container", _get(entry, "name") or str(vmid), "proxmox", vmid=vmid, status=_get(entry, "status"), pool=_get(entry, "pool"))
            graph.link(current, node_id, "runs_on")
        elif kind == "storage":
            name = _get(entry, "storage") or _last(_get(entry, "id"))
            if name:
                graph.link(graph.add(f"proxmox:storage:{node}:{name}", "storage", str(name), "proxmox", status=_get(entry, "status")), node_id or root, "on_node" if node_id else "in_cluster")
        elif kind in {"sdn", "pool"}:
            name = _get(entry, "sdn", "pool") or _last(_get(entry, "id"))
            if name:
                graph.link(graph.add(f"proxmox:{kind}:{name}", kind, str(name), "proxmox"), node_id or root, "on_node" if node_id else "in_cluster")
    _fill_missing(graph, "proxmox")
    return graph


def build_proxmox(runner: Runner) -> ResourceGraph:
    errors: list[str] = []
    resources = _call_json(runner, ["pvesh", "get", "/cluster/resources", "--output-format", "json"], errors, [])
    status = _call_json(runner, ["pvesh", "get", "/cluster/status", "--output-format", "json"], errors, [])
    name = next((_get(item, "name") for item in _items(status) if _get(item, "type") == "cluster"), "") or "proxmox"
    graph = proxmox_graph(_items(resources), str(name))
    graph.errors.extend(errors)
    return graph
