# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Resource graph builders for Google Cloud, Azure, OpenStack, vSphere and Proxmox."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from navin.infra.engine import InfraEngine
from navin.infra.graph import (
    azure_graph,
    build_azure,
    build_gcp,
    build_openstack,
    build_proxmox,
    build_vsphere,
    gcp_graph,
    openstack_graph,
    proxmox_graph,
    vsphere_graph,
)
from tests import test_infra_engine as base

FakeRunner, fail = base.FakeRunner, base.fail
isolated = base.isolated  # the autouse fixture: no real HOME, kubeconfig or cloud files

GCP_NET = "https://www.googleapis.com/compute/v1/projects/p1/global/networks/vpc-main"
GCP_SUB = "https://www.googleapis.com/compute/v1/projects/p1/regions/europe-west1/subnetworks/sub-eu"
GCP_DATA = {
    "networks": [{"name": "vpc-main", "subnetMode": "CUSTOM"}],
    "subnets": [{"name": "sub-eu", "network": GCP_NET, "ipCidrRange": "10.0.0.0/24", "region": "x/europe-west1"}],
    "instances": [{"name": "web-1", "status": "RUNNING", "networkInterfaces": [{"network": GCP_NET, "subnetwork": GCP_SUB}]}],
    "firewalls": [{"name": "allow-ssh", "network": GCP_NET}],
    "routers": [{"name": "nat-router", "network": GCP_NET, "nats": [{"name": "nat-1"}]}],
    "backend_services": [{"name": "web-backend"}],
    "forwarding_rules": [{"name": "web-lb", "backendService": "x/backendServices/web-backend", "network": GCP_NET}],
    "clusters": [{"name": "gke-prod", "network": "vpc-main", "subnetwork": "sub-eu", "nodePools": [{"name": "pool-a"}]}],
    "sql_instances": [{"name": "db-main", "settings": {"ipConfiguration": {"privateNetwork": "projects/p1/global/networks/vpc-main"}}}],
    "buckets": [{"name": "assets"}, {}],
}


def test_gcp_graph_network_impact_reaches_instances_clusters_and_databases():
    graph = gcp_graph(GCP_DATA, "p1")
    impact = set(graph.impact("gcp:network:vpc-main"))
    assert {"gcp:subnet:sub-eu", "gcp:instance:web-1", "gcp:gke:gke-prod", "gcp:sql:db-main", "gcp:firewall:allow-ssh", "gcp:router:nat-router"} <= impact
    assert "gcp:forwarding-rule:web-lb" in graph.impact("gcp:backend-service:web-backend")
    assert graph.nodes["gcp:gke:gke-prod"]["node_pools"] == ["pool-a"]
    assert "gcp:bucket:assets" in graph.impact("gcp:project:p1")


def test_build_gcp_reads_project_and_reports_failing_lists():
    runner = FakeRunner({
        ("gcloud", "config", "get-value", "project"): "p1\n",
        ("gcloud", "compute", "networks", "list"): json.dumps(GCP_DATA["networks"]),
        ("gcloud", "compute", "instances", "list"): json.dumps(GCP_DATA["instances"]),
        ("gcloud", "sql", "instances", "list"): fail("ERROR: Cloud SQL Admin API has not been used"),
    })
    graph = build_gcp(runner)
    assert "gcp:instance:web-1" in graph.impact("gcp:network:vpc-main")
    assert any(error.startswith("gcloud sql instances list") and "Cloud SQL" in error for error in graph.errors)
    assert all("--project" in call and "p1" in call for call in runner.calls[1:])


SUB = "/subscriptions/s1/resourceGroups/RG1/providers/Microsoft.Network/virtualNetworks/vnet1/subnets/app"
NIC = "/subscriptions/s1/resourceGroups/RG1/providers/Microsoft.Network/networkInterfaces/vm1-nic"
VM = "/subscriptions/s1/resourceGroups/RG1/providers/Microsoft.Compute/virtualMachines/vm1"
AZURE_DATA = {
    "groups": [{"id": "/subscriptions/s1/resourceGroups/RG1", "name": "RG1"}],
    "vnets": [{"id": "/subscriptions/s1/resourceGroups/RG1/providers/Microsoft.Network/virtualNetworks/vnet1", "name": "vnet1", "resourceGroup": "RG1", "subnets": [{"id": SUB, "name": "app"}]}],
    "nics": [{"id": NIC, "name": "vm1-nic", "resourceGroup": "RG1", "ipConfigurations": [{"subnet": {"id": SUB}}], "virtualMachine": {"id": VM}}],
    "vms": [{"id": VM, "name": "vm1", "resourceGroup": "RG1"}],
    "aks": [{"id": "/subscriptions/s1/resourceGroups/RG1/providers/Microsoft.ContainerService/managedClusters/aks1", "name": "aks1", "agentPoolProfiles": [{"vnetSubnetId": SUB}]}],
    "nsgs": [{"id": "/subscriptions/s1/resourceGroups/RG1/providers/Microsoft.Network/networkSecurityGroups/nsg1", "name": "nsg1", "subnets": [{"id": SUB}]}],
    "postgres": [{"id": "/subscriptions/s1/resourceGroups/RG1/providers/Microsoft.DBforPostgreSQL/flexibleServers/pg1", "name": "pg1", "network": {"delegatedSubnetResourceId": SUB}}],
    "storage": [{"name": "no-id"}],
}


def test_azure_graph_vnet_impact_reaches_vms_aks_and_databases():
    graph = azure_graph(AZURE_DATA, "s1")
    vnet = "azure:/subscriptions/s1/resourcegroups/rg1/providers/microsoft.network/virtualnetworks/vnet1"
    impact = set(graph.impact(vnet))
    names = {graph.nodes[node]["name"] for node in impact}
    assert {"app", "vm1-nic", "vm1", "aks1", "pg1"} <= names
    nsg_impact = {graph.nodes[node]["name"] for node in graph.impact("azure:/subscriptions/s1/resourcegroups/rg1/providers/microsoft.network/networksecuritygroups/nsg1")}
    assert {"app", "vm1"} <= nsg_impact
    assert "vm1" in {graph.nodes[node]["name"] for node in graph.impact("azure:/subscriptions/s1/resourcegroups/rg1")}


def test_build_azure_uses_account_subscription_and_reports_failures():
    runner = FakeRunner({
        ("az", "account", "show"): json.dumps({"id": "s1", "name": "Prod"}),
        ("az", "network", "vnet", "list"): json.dumps(AZURE_DATA["vnets"]),
        ("az", "aks", "list"): fail("ERROR: AuthorizationFailed"),
    })
    graph = build_azure(runner)
    assert "azure:subscription:s1" in graph.nodes and any(node["name"] == "app" for node in graph.nodes.values())
    assert any(error.startswith("az aks list") and "AuthorizationFailed" in error for error in graph.errors)
    assert build_azure(FakeRunner({("az", "account", "show"): fail("Please run 'az login'")})).errors


OS_DATA = {
    "networks": [{"ID": "n1", "Name": "private"}, {"ID": "n2", "Name": "public"}],
    "subnets": [{"ID": "s1", "Name": "private-sub", "Network": "n1", "Subnet": "10.1.0.0/24"}],
    "routers": [{"ID": "r1", "Name": "edge"}],
    "ports": [{"device_id": "r1", "device_owner": "network:router_interface", "network_id": "n1"}],
    "servers": [{"ID": "vm-a", "Name": "web", "Networks": {"private": ["10.1.0.5"]}},
                {"ID": "vm-b", "Name": "db", "Networks": "private=10.1.0.6; public=1.2.3.4"}],
    "volumes": [{"ID": "v1", "Name": "data", "Attached to": [{"server_id": "vm-b", "device": "/dev/vdb"}]},
                {"ID": "v2", "Name": "logs", "Attached to": "Attached to web on /dev/vdc"}],
    "security_groups": [{"ID": "g1", "Name": "default"}],
}


def test_openstack_graph_network_impact_reaches_servers():
    graph = openstack_graph(OS_DATA, "mycloud")
    impact = set(graph.impact("openstack:network:n1"))
    assert {"openstack:server:vm-a", "openstack:server:vm-b", "openstack:subnet:s1", "openstack:router:r1"} <= impact
    assert "openstack:server:vm-b" in graph.impact("openstack:volume:v1")
    assert "openstack:server:vm-a" in graph.impact("openstack:volume:v2")


def test_build_openstack_reports_failing_list():
    runner = FakeRunner({
        ("openstack", "network", "list"): json.dumps(OS_DATA["networks"]),
        ("openstack", "server", "list"): json.dumps(OS_DATA["servers"]),
        ("openstack", "volume", "list"): fail("Missing value auth-url required for auth plugin password"),
    })
    graph = build_openstack(runner)
    assert "openstack:server:vm-a" in graph.impact("openstack:network:n1")
    assert any(error.startswith("openstack volume list") for error in graph.errors)


VS_DATA = {
    "clusters": ["/DC1/host/Cluster1"],
    "hosts": ["/DC1/host/Cluster1/esx1"],
    "datastores": ["/DC1/datastore/ds1"],
    "networks": ["/DC1/network/VM Network"],
    "refs": {"host-12": "esx1", "datastore-5": "ds1", "network-7": "VM Network"},
    "vms": [{"Name": "app-vm", "Runtime": {"Host": {"Value": "host-12"}, "PowerState": "poweredOn"},
             "Datastore": [{"Value": "datastore-5"}], "Network": [{"Value": "network-7"}]},
            {"summary": {"config": {"name": "orphan"}}, "runtime": {"host": {"value": "host-99"}}}],
}


def test_vsphere_graph_host_and_cluster_impact_reaches_vms():
    graph = vsphere_graph(VS_DATA, "vc1")
    assert "vsphere:vm:app-vm" in graph.impact("vsphere:host:esx1")
    assert "vsphere:vm:app-vm" in graph.impact("vsphere:cluster:Cluster1")
    assert "vsphere:vm:app-vm" in graph.impact("vsphere:datastore:ds1")
    assert "vsphere:vm:app-vm" in graph.impact("vsphere:network:VM Network")
    assert "vsphere:vm:orphan" in graph.impact("vsphere:host:host-99")


def test_build_vsphere_needs_govc_url_and_reports_failures(monkeypatch):
    monkeypatch.delenv("GOVC_URL", raising=False)
    assert "GOVC_URL" in build_vsphere(FakeRunner()).errors[0]
    monkeypatch.setenv("GOVC_URL", "https://user@vc1.example.com/sdk")
    runner = FakeRunner({
        ("govc", "find", "/", "-type", "h"): "/DC1/host/Cluster1/esx1\n",
        ("govc", "find", "/", "-type", "c"): "/DC1/host/Cluster1\n",
        ("govc", "find", "/", "-type", "s"): fail("permission denied"),
        ("govc", "ls", "-i", "/DC1/host/Cluster1/esx1"): "HostSystem:host-12\n",
        ("govc", "vm.info"): json.dumps({"virtualMachines": [VS_DATA["vms"][0]]}),
    })
    graph = build_vsphere(runner)
    assert "vsphere:vm:app-vm" in graph.impact("vsphere:host:esx1")
    assert "vsphere:vcenter:vc1.example.com" in graph.nodes
    assert any("-type s" in error and "permission denied" in error for error in graph.errors)


PVE = [
    {"type": "node", "node": "pve1", "status": "online"},
    {"type": "qemu", "node": "pve1", "vmid": 100, "name": "web"},
    {"type": "lxc", "node": "pve1", "vmid": 200, "name": "dns"},
    {"type": "storage", "node": "pve1", "storage": "local-lvm"},
    {"type": "pool", "pool": "prod"},
    {"type": "qemu"},
]


def test_proxmox_graph_node_impact_reaches_vms_and_containers():
    graph = proxmox_graph(PVE, "lab")
    impact = set(graph.impact("proxmox:node:pve1"))
    assert {"proxmox:qemu:100", "proxmox:lxc:200", "proxmox:storage:pve1:local-lvm"} <= impact
    assert "proxmox:qemu:100" in graph.impact("proxmox:cluster:lab")


def test_build_proxmox_reports_failure_and_reads_cluster_name():
    runner = FakeRunner({
        ("pvesh", "get", "/cluster/resources"): json.dumps(PVE),
        ("pvesh", "get", "/cluster/status"): json.dumps([{"type": "cluster", "name": "lab"}]),
    })
    assert "proxmox:qemu:100" in build_proxmox(runner).impact("proxmox:cluster:lab")
    broken = build_proxmox(FakeRunner({("pvesh", "get", "/cluster/resources"): fail("ipcc_send_rec failed")}))
    assert any("ipcc_send_rec" in error for error in broken.errors)


def test_engine_reports_missing_gcloud(isolated):
    out = InfraEngine(isolated, runner=FakeRunner()).graph("build", sources=["gcp"])
    assert any("gcp is not installed here" in error for error in out["errors"])


def test_api_accepts_proxmox_and_rejects_unknown_source(isolated):
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

    accepted = get("/api/sessions/websocket%3Aabc/infra/graph?build=proxmox")
    assert accepted.status_code == 200
    assert any("proxmox" in error for error in json.loads(accepted.body)["errors"])
    assert get("/api/sessions/websocket%3Aabc/infra/graph?build=foo").status_code == 400
