# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin optional feature helpers for WebUI Settings."""
from __future__ import annotations

from typing import Any

from navin.optional_features import (
    OptionalFeatureError,
    disable_optional_feature,
    enable_optional_feature,
    optional_features_payload,
)
from navin.webui.http_utils import query_first

QueryParams = dict[str, list[str]]


def navin_features_payload() -> dict[str, Any]:
    return optional_features_payload()


def navin_features_action(
    action: str,
    query: QueryParams,
    *,
    allow_install: bool = True,
) -> dict[str, Any]:
    name = (query_first(query, "name") or "").strip()
    instance_id = (query_first(query, "instance_id") or "default").strip()
    if not name:
        raise OptionalFeatureError("missing feature name")
    if action == "enable":
        return enable_optional_feature(name, allow_install=allow_install, instance_id=instance_id)
    if action == "disable":
        if name == "websocket":
            raise OptionalFeatureError(
                "The WebUI websocket channel cannot be disabled from WebUI. "
                "Use `navin plugins disable websocket` from a terminal if you need to disable it.",
                status=400,
            )
        return disable_optional_feature(name, instance_id=instance_id)
    raise OptionalFeatureError(f"unknown feature action '{action}'", status=404)
