# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Infrastructure Orchestrator: the engine behind the ``infra`` tool.

Plan -> Discover -> Analyze -> Validate -> Execute -> Verify -> Recover.

- :mod:`navin.infra.registry`: the Universal Command & API Registry, built by
  :mod:`navin.infra.discovery` from the real machine (botocore models, Cloud
  Control, gcloud trees, Kubernetes discovery with CRDs, CLI help trees,
  Terraform provider schemas, ansible-doc) plus a curated base.
- :mod:`navin.infra.parse`: what a command line does (risk and categories).
- :mod:`navin.infra.policy`: the Policy & Approval Engine (autonomy levels,
  production targets, scopes, temporary credentials).
- :mod:`navin.infra.broker`: the Execution Broker (no shell, redaction,
  snapshots, audit) and :mod:`navin.infra.verify` (checks, diagnosis, recovery).
- :mod:`navin.infra.graph`: the Infrastructure Resource Graph.
- :mod:`navin.infra.orchestrator`: multi-step runs with dependencies.
- :mod:`navin.infra.engine`: one facade for the tool, the CLI and the Desktop.
"""

from navin.infra.model import LEVELS, RISKS, TIERS, Operation

__all__ = ["LEVELS", "RISKS", "TIERS", "Operation"]
