# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin app-template catalog (first-party, versioned in git)."""

from navin.templates.apps.catalog import (
    APP_TEMPLATES,
    UNIVERSAL_AGENTS,
    get_app_template,
    is_publishable,
    list_app_templates,
    template_slug_for_create_app,
)
from navin.templates.apps.schema import (
    APP_TEMPLATE_SCHEMA,
    AWS_TEMPLATE_PREFIX,
    INSTALL_JSON_SCHEMA,
    NAVIN_JSON_SCHEMA,
    OVERLAY_SCHEMA,
)

__all__ = [
    "APP_TEMPLATE_SCHEMA",
    "APP_TEMPLATES",
    "AWS_TEMPLATE_PREFIX",
    "INSTALL_JSON_SCHEMA",
    "NAVIN_JSON_SCHEMA",
    "OVERLAY_SCHEMA",
    "UNIVERSAL_AGENTS",
    "get_app_template",
    "is_publishable",
    "list_app_templates",
    "template_slug_for_create_app",
]
