# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Discovery of every platform with a CLI or an API: K3s / MicroK8s / RKE2 without
kubectl, CDK, Forgejo, Jenkins, Velero and the security CLIs."""

from __future__ import annotations

import json
import shutil

import httpx
import pytest

from navin.infra import discovery as disco
from navin.infra import registry as registry_mod
from navin.infra.discovery import (
    discover,
    discover_forgejo,
    discover_help_tree,
    discover_jenkins,
    discover_kubernetes,
    parse_help,
)
from navin.infra.engine import InfraEngine
from navin.infra.model import DISCOVERABLE
from navin.infra.registry import Registry
from tests import test_infra_engine as base

FakeRunner, fail = base.FakeRunner, base.fail
isolated = base.isolated  # autouse: no real HOME, kubeconfig or cloud files


def only(monkeypatch, *present: str) -> None:
    registry_mod._WHICH_CACHE.clear()
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}" if name in present else None)


def test_k3s_and_microk8s_clusters_are_discovered_without_kubectl(monkeypatch):
    only(monkeypatch, "k3s")
    runner = FakeRunner({
        ("k3s", "kubectl", "config", "current-context"): "default\n",
        ("k3s", "kubectl", "version"): json.dumps({"clientVersion": {"gitVersion": "v1.31.4+k3s1"}, "serverVersion": {"gitVersion": "v1.31.4+k3s1"}}),
        ("k3s", "kubectl", "api-resources"): base.API_RESOURCES,
        ("k3s", "kubectl", "get", "crd"): "cert-manager.io\n",
    })
    result = discover_kubernetes(runner)
    assert result.available and result.ops and "k3s" in result.source
    assert all(call[:2] == ["k3s", "kubectl"] for call in runner.calls)
    only(monkeypatch, "microk8s")
    assert disco.kube_cli() == ["microk8s", "kubectl"]
    only(monkeypatch)
    missing = discover_kubernetes(FakeRunner())
    assert not missing.available and "k3s, microk8s" in missing.errors[0]


CDK_HELP = """Usage: cdk -a <cdk-app> COMMAND

Commands:
  cdk list [STACKS..]             Lists all stacks in the app      [aliases: ls]
  cdk synth [STACKS..]            Synthesizes and prints the CloudFormation template
  cdk deploy [STACKS..]           Deploys the stack(s) named STACKS into your AWS account
  cdk destroy [STACKS..]          Destroy the stack(s) named STACKS
  cdk diff [STACKS..]             Compares the specified stack with the deployed stack

Options:
  -a, --app  REQUIRED WHEN RUNNING APP
"""

TEA_HELP = """NAME:
   tea - command line tool to interact with Gitea / Forgejo

COMMANDS:
   logins, login                  Log in to a Gitea server
   repos, repo                    Show repository details
   pulls, pull, pr                Manage and checkout pull requests
   actions, action                Manage repository actions
   help, h                        Shows a list of commands or help for one command
"""


def test_cdk_yargs_and_tea_urfave_help_pages_are_read(monkeypatch):
    only(monkeypatch, "cdk")
    runner = FakeRunner({("cdk", "--help"): CDK_HELP, ("cdk", "--version"): "2.170.0 (build 1)\n"})
    result = discover_help_tree(runner, binary="cdk", provider="cdk")
    by_name = {op.name: op for op in result.ops}
    assert set(by_name) >= {"list", "synth", "deploy", "destroy", "diff"}
    assert by_name["destroy"].risk == "critical" and by_name["diff"].risk == "plan" and by_name["list"].risk == "read"
    commands, _flags = parse_help(TEA_HELP)
    assert [name for name, _ in commands] == ["logins", "repos", "pulls", "actions"]


class Http:
    """A fake JSON web API keyed by URL prefix."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, url, *, auth=None, headers=None):
        self.calls.append((url, auth, headers))
        for prefix, answer in self.answers.items():
            if url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404))


def test_jenkins_jobs_become_operations(monkeypatch):
    monkeypatch.setenv("JENKINS_URL", "https://ci.example.com/")
    monkeypatch.setenv("JENKINS_USER", "navin")
    monkeypatch.setenv("JENKINS_TOKEN", "t0ken")
    http = Http({
        "https://ci.example.com/api/json?tree=jobs": {"jobs": [
            {"name": "deploy", "url": "https://ci.example.com/job/deploy/", "color": "blue"},
            {"name": "apps", "url": "https://ci.example.com/job/apps/", "jobs": [{"name": "shop", "url": "https://ci.example.com/job/apps/job/shop/"}]},
        ]},
        "https://ci.example.com/api/json?tree=mode": {"_class": "hudson.model.Hudson"},
    })
    monkeypatch.setattr(disco, "_http_json", http)
    result = discover_jenkins(FakeRunner())
    ids = {op.id: op for op in result.ops}
    build = ids["jenkins:job:/job/deploy:build"]
    assert build.endpoint == "POST /job/deploy/build" and build.permissions == ["Job/Build"] and build.verify and build.rollback
    assert ids["jenkins:job:/job/apps/job/shop:delete"].risk == "critical"
    assert ids["jenkins:job:/job/deploy:status"].risk == "read"
    assert http.calls[0][1] == ("navin", "t0ken")
    assert "t0ken" not in json.dumps([op.to_row() for op in result.ops])  # the token never lands in the catalog
    monkeypatch.setattr(disco, "_http_json", Http({"https://ci.example.com/": httpx.ConnectError("refused")}))
    assert "Jenkins API" in discover_jenkins(FakeRunner()).errors[0]
    monkeypatch.delenv("JENKINS_URL")
    assert "JENKINS_URL is not set" in discover_jenkins(FakeRunner()).errors[0]


def test_forgejo_actions_api_per_repository(monkeypatch):
    only(monkeypatch)
    monkeypatch.setenv("FORGEJO_URL", "https://forgejo.example.com")
    monkeypatch.setenv("FORGEJO_TOKEN", "abc")
    monkeypatch.setattr(disco, "_http_json", Http({
        "https://forgejo.example.com/api/v1/version": {"version": "11.0.1"},
        "https://forgejo.example.com/api/v1/user/repos": [{"full_name": "team/shop"}],
    }))
    result = discover_forgejo(FakeRunner())
    by_name = {op.name: op for op in result.ops}
    dispatch = by_name["dispatch workflow (team/shop)"]
    assert dispatch.endpoint == "POST /api/v1/repos/team/shop/actions/workflows/<workflow.yml>/dispatches"
    assert dispatch.risk == "change" and dispatch.verify
    assert by_name["set secret (team/shop)"].risk == "critical"
    assert result.version == "Forgejo 11.0.1" and result.available
    assert "abc" not in json.dumps([op.to_row() for op in result.ops])


def test_security_clis_are_merged_into_one_catalog(monkeypatch, isolated):
    only(monkeypatch, "trivy", "vault")
    runner = FakeRunner({
        ("trivy", "--help"): "Scanner for vulnerabilities\n\nScanning Commands:\n  image       Scan a container image\n  k8s         [EXPERIMENTAL] Scan kubernetes cluster\n",
        ("trivy", "--version"): "Version: 0.58.1\n",
        ("vault", "--help"): "Usage: vault <command> [args]\n\nCommon commands:\n    read        Read data and retrieves secrets\n    write       Write data, configuration, and secrets\n    delete      Delete secrets and configuration\n",
        ("vault", "version"): "Vault v1.18.2\n",
    })
    registry = Registry(isolated)
    [result] = discover("security", registry=registry, runner=runner)
    names = {op.command for op in result.ops}
    assert {"trivy image", "trivy k8s", "vault read", "vault write", "vault delete"} <= names
    assert "trivy" in result.version and "vault" in result.version and "not installed here" in result.errors[-1]
    assert next(op for op in result.ops if op.command == "vault delete").risk == "critical"


def test_every_discoverable_provider_has_a_discovery_and_the_state_lists_what_runs_here(monkeypatch, isolated):
    for name in ("JENKINS_URL", "FORGEJO_URL", "GOVC_URL"):
        monkeypatch.delenv(name, raising=False)
    only(monkeypatch)
    for provider in DISCOVERABLE:
        [result] = discover(provider, registry=Registry(isolated), runner=FakeRunner())
        assert not result.available or result.errors, provider  # nothing installed: said, never a crash
        assert not any("no discovery for" in error for error in result.errors), provider
    only(monkeypatch, "kubectl", "velero", "ssh", "psql")
    state = InfraEngine(isolated, runner=FakeRunner()).state()
    assert state["discoverable"] == ["kubernetes", "velero"]
    with pytest.raises(ValueError):
        discover("linux", registry=Registry(isolated), runner=FakeRunner())
